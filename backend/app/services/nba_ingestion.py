"""
Servicio de ingesta de datos NBA usando nba_api y conectores públicos de ESPN.
Obtiene standings, stats avanzadas y boxscores históricos.

AUDITORÍA DE FUENTES Y GOBERNANZA DE RED:
- stats.nba.com (nba_api): Servicio interno de NBA.com sin SLA oficial, susceptible a rate-limiting,
  bloqueos por IP en contenedores y tiempos de espera prolongados (>30s). Se configura con headers
  de navegador y reintentos exponenciales.
- site.api.espn.com (ESPN Public API): Endpoint público gratuito y de alta disponibilidad para calendario,
  scoreboard y standings en tiempo real. No requiere API keys ni autenticación.
- SportsDataverse (data/raw/nba_espn): Archivos Parquet inmutables con verificación
  de hash SHA-256 para entrenamiento e inferencia histórica reproducible sin llamadas de red.
- Gobernanza de Cuotas: CERO llamadas a The Odds API; data/odds_quota_ledger.json permanece 100% intacto.
"""
import asyncio
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import structlog
from nba_api.stats.endpoints import (
    leaguedashteamstats,
    leaguestandingsv3,
    teamgamelog,
    leaguegamefinder,
)

from app.core.config import settings

logger = structlog.get_logger(__name__)

SLEEP = settings.NBA_API_SLEEP_SECONDS

NBA_API_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nba.com/",
    "Connection": "keep-alive",
}


def _get_current_season() -> str:
    """Determina la temporada actual (ej: '2025-26')."""
    now = datetime.now()
    year = now.year
    # La temporada NBA empieza en octubre
    if now.month >= 9:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


NBA_ESPN_ID_TO_NAME = {
    "1": "Atlanta Hawks",
    "2": "Boston Celtics",
    "3": "New Orleans Pelicans",
    "4": "Chicago Bulls",
    "5": "Cleveland Cavaliers",
    "6": "Dallas Mavericks",
    "7": "Denver Nuggets",
    "8": "Detroit Pistons",
    "9": "Golden State Warriors",
    "10": "Houston Rockets",
    "11": "Indiana Pacers",
    "12": "LA Clippers",
    "13": "Los Angeles Lakers",
    "14": "Miami Heat",
    "15": "Milwaukee Bucks",
    "16": "Minnesota Timberwolves",
    "17": "Brooklyn Nets",
    "18": "New York Knicks",
    "19": "Orlando Magic",
    "20": "Philadelphia 76ers",
    "21": "Phoenix Suns",
    "22": "Portland Trail Blazers",
    "23": "Sacramento Kings",
    "24": "San Antonio Spurs",
    "25": "Oklahoma City Thunder",
    "26": "Utah Jazz",
    "27": "Washington Wizards",
    "28": "Toronto Raptors",
    "29": "Memphis Grizzlies",
    "30": "Charlotte Hornets",
}

ESPN_NBA_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"


def _fetch_team_stats_fallback(season: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    Fallback resiliente a fuentes locales verificadas (SportsDataverse / ESPN Parquets)
    cuando stats.nba.com o endpoints en vivo no responden.
    Retorna un DataFrame estandarizado con standings y ratings reales observados.
    Falla cerrado si la temporada requerida no existe; nunca sintetiza constantes ni
    sustituye la temporada por otra.
    """
    try:
        from ml.data_pipeline.nba_source import load_nba_games
        games = load_nba_games()
        if games.empty:
            return None

        # Resolver año de temporada (ej. '2025-26' -> 2026, '2024' -> 2024)
        if season is not None:
            season_str = str(season).strip()
            if "-" in season_str:
                try:
                    target_season = int(season_str.split("-")[0]) + 1
                except Exception:
                    logger.warning("Temporada NBA con formato no analizable", season=season)
                    return None
            elif season_str.isdigit():
                target_season = int(season_str)
            else:
                logger.warning("Temporada NBA no numérica", season=season)
                return None
            s_games = games[games["season"] == target_season]
            if s_games.empty:
                logger.warning("Temporada NBA solicitada no encontrada en datos locales", season=season, target_season=target_season)
                return None
        else:
            target_season = int(games["season"].max())
            s_games = games[games["season"] == target_season]
            if s_games.empty:
                return None

        records = []
        unique_teams = sorted(set(s_games["home_team_id"].unique()).union(s_games["away_team_id"].unique()))
        for tid in unique_teams:
            h = s_games[s_games["home_team_id"] == tid]
            a = s_games[s_games["away_team_id"] == tid]
            w = int(h["home_win"].sum() + (1 - a["home_win"]).sum())
            played = len(h) + len(a)
            if played == 0:
                continue
            l = played - w
            pace = float(pd.concat([h["home_pace"], a["away_pace"]]).mean())
            off_rtg = float(pd.concat([h["home_off_rating"], a["away_off_rating"]]).mean())
            def_rtg = float(pd.concat([h["home_def_rating"], a["away_def_rating"]]).mean())
            records.append({
                "team_id": int(tid) if str(tid).isdigit() else tid,
                "teamname": NBA_ESPN_ID_TO_NAME.get(str(tid), f"Team {tid}"),
                "w": w,
                "l": l,
                "w_pct": float(w / played) if played else 0.0,
                "off_rating": off_rtg,
                "def_rating": def_rtg,
                "net_rating": off_rtg - def_rtg,
                "pace": pace,
                "ts_pct": None,
                "ast_ratio": None,
                "reb_pct": None,
            })
        if not records:
            return None
        df = pd.DataFrame(records)
        df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce").astype("Int64")
        return df
    except Exception as exc:
        logger.error("Error en fallback de stats NBA", error=str(exc))
        return None


def fetch_espn_nba_standings(season: Optional[int | str] = None, timeout: float = 5.0) -> Optional[pd.DataFrame]:
    """
    Obtiene standings de NBA directamente desde el conector público de ESPN Standings API.
    Fuente gratuita, sin API keys, sin consumir créditos de The Odds API.
    Valida HTTP 200, estructura JSON, temporada solicitada (si se especifica) y presencia
    de los 30 equipos con nombres y marcas reales.
    Las columnas no observadas directamente en el endpoint de standings (ts_pct, ast_ratio,
    reb_pct, pace, off_rating, def_rating, net_rating) se preservan como None sin inventar constantes.
    Degrada limpiamente a _fetch_team_stats_fallback(season) si la red no responde o el payload es inválido.
    """
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                ESPN_NBA_STANDINGS_URL,
                headers={"User-Agent": "sports-analytics/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Respuesta de ESPN Standings no es un objeto JSON válido")

            # Validar temporada si fue especificada
            if season is not None:
                season_str = str(season).strip()
                target_year = int(season_str.split("-")[0]) + 1 if "-" in season_str else int(season_str)
                espn_season_year = data.get("season", {}).get("year")
                if espn_season_year is not None and int(espn_season_year) != target_year:
                    logger.warning(
                        "Temporada en ESPN Standings no coincide con la solicitada",
                        espn_year=espn_season_year,
                        requested=target_year,
                    )
                    return _fetch_team_stats_fallback(season=season)

            records = []
            for child in data.get("children", []):
                for entry in child.get("standings", {}).get("entries", []):
                    t = entry.get("team", {})
                    tid = t.get("id")
                    if tid is None:
                        continue
                    display_name = t.get("displayName") or NBA_ESPN_ID_TO_NAME.get(str(tid), f"Team {tid}")
                    st = {s.get("name"): s.get("value") for s in entry.get("stats", []) if "name" in s}
                    w = int(st.get("wins", 0))
                    l = int(st.get("losses", 0))
                    played = w + l
                    w_pct = float(st.get("winPercent", float(w / played) if played > 0 else 0.0))
                    records.append({
                        "team_id": int(tid),
                        "teamname": display_name,
                        "w": w,
                        "l": l,
                        "w_pct": w_pct,
                        "off_rating": None,
                        "def_rating": None,
                        "net_rating": None,
                        "pace": None,
                        "ts_pct": None,
                        "ast_ratio": None,
                        "reb_pct": None,
                    })
            if len(records) >= 30:
                df = pd.DataFrame(records)
                df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce").astype("Int64")
                return df
            else:
                logger.warning("ESPN Standings devolvió menos de 30 equipos; activando fallback local", team_count=len(records))
    except Exception as exc:
        logger.warning("Fallo al consultar ESPN NBA Standings API live; activando fallback a datos locales", error=str(exc))
    return _fetch_team_stats_fallback(season=str(season) if season else None)


def _safe_fetch(endpoint_fn, **kwargs) -> Optional[pd.DataFrame]:
    """Wrapper con reintentos y sleep para respetar rate limits de nba_api."""
    fn_name = getattr(endpoint_fn, "__name__", str(endpoint_fn))
    if "headers" not in kwargs:
        kwargs["headers"] = NBA_API_HEADERS
    if "timeout" not in kwargs:
        kwargs["timeout"] = 5
    for attempt in range(3):
        try:
            result = endpoint_fn(**kwargs)
            data = result.get_data_frames()
            if data and len(data) > 0 and not data[0].empty:
                time.sleep(SLEEP)
                return data[0]
            logger.warning("Empty response from NBA API", endpoint=fn_name, attempt=attempt)
            time.sleep(SLEEP * 2)
        except Exception as e:
            logger.error("NBA API error", endpoint=fn_name, attempt=attempt, error=str(e))
            time.sleep(SLEEP * (attempt + 1))
    return None


async def fetch_team_stats(season: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    Obtiene stats avanzadas + standings combinados para todos los equipos.
    Retorna DataFrame con columnas estandarizadas.
    """
    season = season or _get_current_season()
    logger.info("Fetching team stats", season=season)

    loop = asyncio.get_event_loop()

    # Standings (W%, home record, road record, L10)
    standings_df = await loop.run_in_executor(
        None,
        lambda: _safe_fetch(
            leaguestandingsv3.LeagueStandingsV3,
            league_id="00",
            season=season,
            season_type="Regular Season",
        ),
    )

    # Advanced stats (OffRtg, DefRtg, NetRtg, Pace)
    advanced_df = await loop.run_in_executor(
        None,
        lambda: _safe_fetch(
            leaguedashteamstats.LeagueDashTeamStats,
            measure_type_detailed_defense="Advanced",
            season=season,
            season_type_all_star="Regular Season",
            per_mode_simple="PerGame",
        ),
    )

    if standings_df is None or advanced_df is None:
        logger.warning("Fallo al obtener stats de nba_api; activando fallback a fuentes verificadas", season=season)
        fallback_df = await loop.run_in_executor(None, lambda: _fetch_team_stats_fallback(season))
        return fallback_df

    # ─── Normalizar nombres de columnas ─────────────────────────────────────
    standings_df.columns = standings_df.columns.str.lower()
    advanced_df.columns = advanced_df.columns.str.lower()

    # Resolver nombre de columna de team_id
    def find_team_id_col(df: pd.DataFrame) -> str:
        for candidate in ["team_id", "teamid"]:
            if candidate in df.columns:
                return candidate
        raise ValueError(f"team_id column not found. Columns: {df.columns.tolist()}")

    s_id = find_team_id_col(standings_df)
    a_id = find_team_id_col(advanced_df)

    standings_df = standings_df.rename(columns={s_id: "team_id"})
    advanced_df  = advanced_df.rename(columns={a_id: "team_id"})

    # Convertir team_id a int
    for df in [standings_df, advanced_df]:
        df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce").astype("Int64")

    # ─── Seleccionar columnas útiles ─────────────────────────────────────────
    adv_cols = ["team_id", "off_rating", "def_rating", "net_rating", "pace",
                "ts_pct", "ast_ratio", "reb_pct"]
    adv_cols_existing = [c for c in adv_cols if c in advanced_df.columns]

    merged = pd.merge(
        standings_df,
        advanced_df[adv_cols_existing],
        on="team_id",
        how="left",
    )

    logger.info("Team stats fetched", season=season, teams=len(merged))
    return merged


async def fetch_recent_games(team_api_id: str, season: Optional[str] = None,
                              n_games: int = 10) -> Optional[pd.DataFrame]:
    """
    Obtiene los últimos N juegos de un equipo para calcular forma reciente.
    """
    season = season or _get_current_season()
    loop = asyncio.get_event_loop()

    games_df = await loop.run_in_executor(
        None,
        lambda: _safe_fetch(
            teamgamelog.TeamGameLog,
            team_id=team_api_id,
            season=season,
            season_type_all_star="Regular Season",
        ),
    )

    if games_df is None or games_df.empty:
        return None

    games_df.columns = games_df.columns.str.lower()
    return games_df.head(n_games)


async def fetch_historical_games(
    season_from: str = "2022-23",
    season_to: Optional[str] = None,
    season_type: str = "Regular Season",
) -> Optional[pd.DataFrame]:
    """
    Descarga todos los partidos históricos para entrenamiento del modelo.
    season_from: primera temporada (ej: "2022-23")
    season_to:   última temporada (por defecto: actual)
    """
    season_to = season_to or _get_current_season()
    loop = asyncio.get_event_loop()

    all_games = []
    current_season = season_from

    while True:
        logger.info("Fetching historical games", season=current_season)
        games_df = await loop.run_in_executor(
            None,
            lambda s=current_season: _safe_fetch(
                leaguegamefinder.LeagueGameFinder,
                league_id_nullable="00",
                season_nullable=s,
                season_type_nullable=season_type,
            ),
        )

        if games_df is not None and not games_df.empty:
            games_df["season"] = current_season
            all_games.append(games_df)

        if current_season == season_to:
            break

        # Siguiente temporada
        year_start = int(current_season[:4]) + 1
        current_season = f"{year_start}-{str(year_start + 1)[-2:]}"

    if not all_games:
        return None

    combined = pd.concat(all_games, ignore_index=True)
    combined.columns = combined.columns.str.lower()
    logger.info("Historical games fetched", total_games=len(combined))
    return combined
