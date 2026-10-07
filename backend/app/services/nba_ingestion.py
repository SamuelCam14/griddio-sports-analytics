"""
Servicio de ingesta de datos NBA usando nba_api.
Obtiene standings, stats avanzadas y boxscores históricos.
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


def _get_current_season() -> str:
    """Determina la temporada actual (ej: '2025-26')."""
    now = datetime.now()
    year = now.year
    # La temporada NBA empieza en octubre
    if now.month >= 9:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


def _safe_fetch(endpoint_fn, **kwargs) -> Optional[pd.DataFrame]:
    """Wrapper con reintentos y sleep para respetar rate limits de nba_api."""
    for attempt in range(3):
        try:
            result = endpoint_fn(**kwargs)
            data = result.get_data_frames()
            if data and len(data) > 0 and not data[0].empty:
                time.sleep(SLEEP)
                return data[0]
            logger.warning("Empty response from NBA API", endpoint=endpoint_fn.__name__, attempt=attempt)
            time.sleep(SLEEP * 2)
        except Exception as e:
            logger.error("NBA API error", endpoint=endpoint_fn.__name__, attempt=attempt, error=str(e))
            time.sleep(SLEEP * (attempt + 2))
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
        logger.error("Failed to fetch NBA team stats", season=season)
        return None

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
