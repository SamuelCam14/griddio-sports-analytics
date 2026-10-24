"""
Servicio de Contexto Deportivo para Partidos (SofaScore-Style Context Engine).
Provee datos 100% REALES y verificables:
1. Historial Directo (Head-to-Head - H2H)
2. Forma Reciente (Últimos 5 partidos con marcadores y W/L)
3. Reporte Oficial de Lesionados y pérdida de producción
4. Duelos Estadísticos Comparativos (PPG, puntos permitidos, margen)
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import json
import time
import math
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team, ProviderGameCrosswalk

logger = structlog.get_logger(__name__)

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INJURY_CACHE_FILE = CACHE_DIR / "live_injury_details.json"

# Caché en memoria para cálculo de impacto de producción: (game_id, team_abbr, snapshot_sha) -> (timestamp, dict)
_IMPACT_CACHE: Dict[Tuple[int, str, Optional[str]], Tuple[float, Dict[str, Any]]] = {}
_IMPACT_CACHE_TTL_SECONDS = 900.0  # 15 minutos

def sanitize_for_json(data: Any) -> Any:
    """Recursivamente convierte NaN/Inf y tipos no serializables en valores JSON-safe."""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return None
        return data
    elif hasattr(data, "item"):
        val = data.item()
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None
        return val
    elif pd.isna(data):
        return None
    return data


def _clean_str(val: Any, default: str = "—") -> str:
    if val is None or pd.isna(val):
        return default
    s = str(val).strip()
    if s.lower() in ("nan", "none", "null", ""):
        return default
    return s


# Mapeo ESPN ID -> Abbreviation para la NBA
NBA_ESPN_ID_TO_ABBR = {
    1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE",
    6: "DAL", 7: "DEN", 8: "DET", 9: "GSW", 10: "HOU",
    11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL",
    16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI",
    21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC",
    26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA"
}
NBA_ABBR_TO_ESPN_ID = {v: k for k, v in NBA_ESPN_ID_TO_ABBR.items()}

# Mapeo de abreviaturas DB <-> nflverse/schedules
NFL_DB_TO_DF_ABBR = {"LAR": "LA", "WSH": "WAS"}
NFL_DF_TO_DB_ABBR = {"LA": "LAR", "WAS": "WSH"}

# Cache en memoria de dataframes históricos para velocidad sub-milisegundo
_HISTORICAL_DF_CACHE: Dict[str, pd.DataFrame] = {}


def _get_historical_df(sport: str) -> Optional[pd.DataFrame]:
    sport_upper = sport.upper()
    if sport_upper in _HISTORICAL_DF_CACHE:
        return _HISTORICAL_DF_CACHE[sport_upper]

    if sport_upper == "NFL":
        try:
            import glob
            files = sorted(glob.glob("data/raw/nflverse/*/schedules_*.parquet"))
            if files:
                dfs = [pd.read_parquet(f) for f in files]
                df = pd.concat(dfs, ignore_index=True)
                df = df.dropna(subset=["home_score", "away_score"]).copy()
                df["game_date"] = pd.to_datetime(df["gameday"]).dt.strftime("%Y-%m-%d")
                df["home_team_id"] = df["home_team"]
                df["away_team_id"] = df["away_team"]
                _HISTORICAL_DF_CACHE[sport_upper] = df
                return df
            else:
                logger.warning("No NFL schedules parquet files found")
        except Exception as e:
            logger.error("Error loading NFL schedules parquet files", error=str(e))

    path = Path("data/processed") / f"{sport_upper.lower()}_training.csv"
    if not path.exists():
        logger.warning("Historical dataset not found", path=str(path))
        return None

    try:
        df = pd.read_csv(path)
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")
        _HISTORICAL_DF_CACHE[sport_upper] = df
        return df
    except Exception as e:
        logger.error("Error loading historical dataframe", error=str(e), sport=sport)
        return None


# Mapeo Completo Nombre de Equipo -> Abreviatura para NFL y NBA
NFL_TEAM_NAME_TO_ABBR = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "los angeles chargers": "LAC",
    "los angeles rams": "LAR",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
}

NBA_TEAM_NAME_TO_ABBR = {
    "atlanta hawks": "ATL",
    "boston celtics": "BOS",
    "brooklyn nets": "BKN",
    "charlotte hornets": "CHA",
    "chicago bulls": "CHI",
    "cleveland cavaliers": "CLE",
    "dallas mavericks": "DAL",
    "denver nuggets": "DEN",
    "detroit pistons": "DET",
    "golden state warriors": "GSW",
    "houston rockets": "HOU",
    "indiana pacers": "IND",
    "la clippers": "LAC",
    "los angeles clippers": "LAC",
    "los angeles lakers": "LAL",
    "memphis grizzlies": "MEM",
    "miami heat": "MIA",
    "milwaukee bucks": "MIL",
    "minnesota timberwolves": "MIN",
    "new orleans pelicans": "NOP",
    "new york knicks": "NYK",
    "oklahoma city thunder": "OKC",
    "orlando magic": "ORL",
    "philadelphia 76ers": "PHI",
    "phoenix suns": "PHX",
    "portland trail blazers": "POR",
    "sacramento kings": "SAC",
    "san antonio spurs": "SAS",
    "toronto raptors": "TOR",
    "utah jazz": "UTA",
    "washington wizards": "WAS",
}


import hashlib

def _normalize_espn_injuries(data: Dict[str, Any], name_map: Dict[str, str], captured_at: str) -> Dict[str, Any]:
    """Normalize one immutable raw ESPN payload; never reuse a detached cache."""
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for t_block in data.get("injuries", []):
        display_name = t_block.get("displayName", "").strip()
        name_lower = display_name.lower()
        abbr = name_map.get(name_lower, "")
        team_injuries: List[Dict[str, Any]] = []
        for item in t_block.get("injuries", []):
            stat = item.get("status", "")
            details = item.get("details") or {}
            injury_type = details.get("type")
            if stat.lower() == "active" and not injury_type:
                continue
            norm_status = "OUT" if any(x in stat.lower() for x in ["out", "reserve", "ir", "suspension", "injured reserve"]) else (
                "QUESTIONABLE" if any(x in stat.lower() for x in ["questionable", "day-to-day", "gtd"]) else (
                    "DOUBTFUL" if "doubtful" in stat.lower() else stat.upper()
                )
            )
            athlete = item.get("athlete") or {}
            team_injuries.append({
                "player_name": _clean_str(athlete.get("displayName"), "Desconocido"),
                "position": _clean_str((athlete.get("position") or {}).get("abbreviation"), "—"),
                "status": norm_status,
                "injury": _clean_str(injury_type or item.get("shortComment"), "No especificado"),
                "practice_status": _clean_str(item.get("shortComment"), "—"),
                "espn_id": str(athlete.get("id")) if athlete.get("id") else None,
                "observed_at": item.get("dateModified") or item.get("lastModified") or item.get("date") or t_block.get("lastModified"),
                "fetched_at": captured_at,
                "source": "espn_live",
            })
        if abbr:
            by_team[abbr] = team_injuries
        by_team[name_lower] = team_injuries
        by_team[display_name] = team_injuries
    return sanitize_for_json(by_team)


def fetch_live_injuries_response(sport: str, allow_live_download: bool = True) -> Dict[str, Any]:
    """
    Obtiene la respuesta completa de ESPN para NFL o NBA, exponiendo:
    - raw_bytes: bytes íntegros recibidos
    - raw_payload: diccionario o estructura original sin alterar
    - received_at_utc: hora local UTC de recepción en nuestro sistema
    - captured_at_utc: hora de captura (preservada si proviene de caché)
    - is_from_cache: booleano indicando si proviene de caché local
    - payload_sha256: SHA-256 calculado sobre los bytes del payload recibido
    - teams: diccionario normalizado por equipo
    """
    sport_upper = sport.upper()
    cache_file = CACHE_DIR / f"{sport_upper.lower()}_live_injuries.json"
    raw_cache_file = CACHE_DIR / f"{sport_upper.lower()}_live_injuries_raw.json"
    name_map = NFL_TEAM_NAME_TO_ABBR if sport_upper == "NFL" else NBA_TEAM_NAME_TO_ABBR

    # 1. Comprobar caché local (TTL: 2 horas). El JSON normalizado y el payload
    # crudo son una sola captura: si sus mtimes divergen, no se puede atribuir
    # a ambos el mismo captured_at ni servir el normalizado como dato actual.
    if cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            age_seconds = time.time() - mtime
            raw_mtime = raw_cache_file.stat().st_mtime if raw_cache_file.exists() else None
            same_capture = raw_mtime is not None and abs(mtime - raw_mtime) <= 5.0
            if same_capture and (0 <= age_seconds < 7200 or not allow_live_download):
                raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
                original_captured_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                cached_raw_bytes = raw_cache_file.read_bytes()
                try:
                    cached_payload = json.loads(cached_raw_bytes.decode("utf-8"))
                except Exception:
                    cached_payload = raw_data
                cached_sha = hashlib.sha256(cached_raw_bytes).hexdigest()
                normalized_teams = _normalize_espn_injuries(cached_payload, name_map, original_captured_at)
                return {
                    "raw_bytes": cached_raw_bytes,
                    "raw_payload": cached_payload,
                    "received_at_utc": original_captured_at,
                    "captured_at_utc": original_captured_at,
                    "is_from_cache": True,
                    "cache_age_seconds": age_seconds,
                    "payload_sha256": cached_sha,
                    "teams": normalized_teams,
                }
        except Exception:
            pass

    # Si allow_live_download es False (ej. en request thread de endpoints GET),
    # no realizar descargas bloqueantes de red; consultar snapshot persistido de disponibilidad.
    if not allow_live_download:
        if sport_upper == "NFL":
            try:
                from app.services.nfl_availability_service import NFLAvailabilityService
                nfl_svc = NFLAvailabilityService()
                snap = nfl_svc.get_active_injury_snapshot()
                if snap and isinstance(snap, dict) and snap.get("teams"):
                    return {
                        "raw_bytes": snap.get("raw_payload"),
                        "raw_payload": snap.get("raw_payload"),
                        "received_at_utc": snap.get("known_by_system_at_utc") or snap.get("captured_at_utc"),
                        "captured_at_utc": snap.get("captured_at_utc"),
                        "is_from_cache": True,
                        "cache_age_seconds": None,
                        "payload_sha256": snap.get("payload_sha256"),
                        "teams": snap.get("teams", {}),
                    }
            except Exception:
                pass
        return {
            "raw_bytes": None,
            "raw_payload": None,
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
            "captured_at_utc": None,
            "is_from_cache": False,
            "payload_sha256": None,
            "teams": {},
        }

    # 2. Descarga oficial en vivo desde ESPN
    endpoint = (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
        if sport_upper == "NFL"
        else "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
    )

    import urllib.request
    try:
        req = urllib.request.Request(
            endpoint,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SportsAnalytics/1.0"}
        )
        received_at_utc = datetime.now(timezone.utc).isoformat()
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw_bytes = resp.read()

        payload_hash = hashlib.sha256(raw_bytes).hexdigest()
        data = json.loads(raw_bytes.decode("utf-8"))

        clean_data = _normalize_espn_injuries(data, name_map, received_at_utc)
        try:
            cache_file.write_text(json.dumps(clean_data), encoding="utf-8")
            raw_cache_file.write_bytes(raw_bytes)
        except Exception as pe:
            logger.warning("Error guardando archivos de caché de lesiones", error=str(pe))
        logger.info("Lesiones oficiales actualizadas desde ESPN", sport=sport, teams_count=len(clean_data))
        return {
            "raw_bytes": raw_bytes,
            "raw_payload": data,
            "received_at_utc": received_at_utc,
            "captured_at_utc": received_at_utc,
            "is_from_cache": False,
            "payload_sha256": payload_hash,
            "teams": clean_data,
        }
    except Exception as e:
        logger.warning("Error descargando lesiones de ESPN, verificando caché secundaria", error=str(e), sport=sport)

    # 3. Fallback solo si ambos archivos prueban que pertenecen a la misma
    # captura. Una caché normalizada sin payload original no tiene provenance.
    if cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            cache_age_seconds = time.time() - mtime
            raw_mtime = raw_cache_file.stat().st_mtime if raw_cache_file.exists() else None
            same_capture = raw_mtime is not None and abs(mtime - raw_mtime) <= 5.0
            if same_capture and 0 <= cache_age_seconds <= 7200:
                raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
                original_captured_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                cached_raw_bytes = raw_cache_file.read_bytes()
                try:
                    cached_payload = json.loads(cached_raw_bytes.decode("utf-8"))
                except Exception:
                    cached_payload = raw_data
                cached_sha = hashlib.sha256(cached_raw_bytes).hexdigest()
                normalized_teams = _normalize_espn_injuries(cached_payload, name_map, original_captured_at)
                return {
                    "raw_bytes": cached_raw_bytes,
                    "raw_payload": cached_payload,
                    "received_at_utc": original_captured_at,
                    "captured_at_utc": original_captured_at,
                    "is_from_cache": True,
                    "cache_age_seconds": cache_age_seconds,
                    "payload_sha256": cached_sha,
                    "teams": normalized_teams,
                }
        except Exception:
            pass

    return {
        "raw_bytes": None,
        "raw_payload": None,
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "captured_at_utc": None,
        "is_from_cache": False,
        "payload_sha256": None,
        "teams": {},
    }


def get_live_injuries(sport: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Obtiene y cachea la lista oficial en vivo de lesionados para NFL o NBA desde ESPN.
    Respaldo secundario: Cache previa local y parsing de contingencia.
    """
    resp = fetch_live_injuries_response(sport)
    return resp.get("teams", {})


async def get_matchup_context(game_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Genera el contexto completo de H2H, L5, Lesiones y Métricas para un partido."""
    stmt = (
        select(Game)
        .where(Game.id == game_id)
        .options(
            selectinload(Game.home_team),
            selectinload(Game.away_team),
            selectinload(Game.crosswalk_entries),
        )
    )
    result = await db.execute(stmt)
    game = result.scalar_one_or_none()
    if not game or not game.home_team or not game.away_team:
        return {}

    sport = game.sport.upper()
    home_abbr = game.home_team.abbreviation.upper()
    away_abbr = game.away_team.abbreviation.upper()
    home_name = game.home_team.name
    away_name = game.away_team.name

    df = _get_historical_df(sport)
    target_day = pd.Timestamp(game.game_date).date()
    if df is not None:
        dates = pd.to_datetime(df["game_date"], errors="coerce").dt.date
        # Context is point-in-time: same-day games are excluded because this
        # dataset does not carry a verified kickoff/observation timestamp per row.
        df = df.loc[dates < target_day].dropna(subset=["home_score", "away_score"]).copy()

    # 1. Resolver identificadores para el dataframe
    if sport == "NBA":
        h_id = NBA_ABBR_TO_ESPN_ID.get(home_abbr, game.home_team_id)
        a_id = NBA_ABBR_TO_ESPN_ID.get(away_abbr, game.away_team_id)
    else:
        h_id = NFL_DB_TO_DF_ABBR.get(home_abbr, home_abbr)
        a_id = NFL_DB_TO_DF_ABBR.get(away_abbr, away_abbr)

    # 2. Historial H2H
    h2h_data = {
        "home_wins": 0,
        "away_wins": 0,
        "draws": 0,
        "total_games": 0,
        "recent_clashes": []
    }

    if df is not None:
        h2h_filter = (
            ((df["home_team_id"] == h_id) & (df["away_team_id"] == a_id)) |
            ((df["home_team_id"] == a_id) & (df["away_team_id"] == h_id))
        )
        h2h_matches = df[h2h_filter].sort_values("game_date")
        h2h_data["total_games"] = len(h2h_matches)

        for _, row in h2h_matches.iterrows():
            is_home_current = row["home_team_id"] == h_id
            h_score = int(row["home_score"])
            a_score = int(row["away_score"])

            if (is_home_current and h_score > a_score) or (not is_home_current and a_score > h_score):
                h2h_data["home_wins"] += 1
            elif (is_home_current and a_score > h_score) or (not is_home_current and h_score > a_score):
                h2h_data["away_wins"] += 1
            else:
                h2h_data["draws"] += 1

        for _, row in h2h_matches.tail(5).iterrows():
            is_nba = sport == "NBA"
            r_home = NBA_ESPN_ID_TO_ABBR.get(row["home_team_id"], str(row["home_team_id"])) if is_nba else NFL_DF_TO_DB_ABBR.get(str(row["home_team_id"]), str(row["home_team_id"]))
            r_away = NBA_ESPN_ID_TO_ABBR.get(row["away_team_id"], str(row["away_team_id"])) if is_nba else NFL_DF_TO_DB_ABBR.get(str(row["away_team_id"]), str(row["away_team_id"]))
            winner = r_home if row["home_score"] > row["away_score"] else (r_away if row["away_score"] > row["home_score"] else "EMPATE")

            h2h_data["recent_clashes"].append({
                "date": str(row["game_date"]),
                "home_team": r_home,
                "away_team": r_away,
                "home_score": int(row["home_score"]),
                "away_score": int(row["away_score"]),
                "winner": winner
            })

    # 3. Forma Reciente (Últimos 5 partidos de cada equipo)
    recent_form = {
        "home": {"streak": [], "games": []},
        "away": {"streak": [], "games": []}
    }

    if df is not None:
        for key, team_identifier in [("home", h_id), ("away", a_id)]:
            team_filter = (df["home_team_id"] == team_identifier) | (df["away_team_id"] == team_identifier)
            team_matches = df[team_filter].sort_values("game_date").tail(5)

            for _, row in team_matches.iterrows():
                is_home = row["home_team_id"] == team_identifier
                opp_id = row["away_team_id"] if is_home else row["home_team_id"]
                opp_abbr = NBA_ESPN_ID_TO_ABBR.get(opp_id, str(opp_id)) if sport == "NBA" else NFL_DF_TO_DB_ABBR.get(str(opp_id), str(opp_id))
                t_score = int(row["home_score"]) if is_home else int(row["away_score"])
                opp_score = int(row["away_score"]) if is_home else int(row["home_score"])
                outcome = "W" if t_score > opp_score else ("L" if t_score < opp_score else "T")

                recent_form[key]["streak"].append(outcome)
                recent_form[key]["games"].append({
                    "date": str(row["game_date"]),
                    "opponent": opp_abbr,
                    "is_home": is_home,
                    "team_score": t_score,
                    "opponent_score": opp_score,
                    "result": outcome
                })

    # 4. Reporte de Lesionados y Pérdida de Producción (Fase 5 / ESPN Live)
    game_start = pd.Timestamp(game.game_date)
    if game_start.tzinfo is None:
        game_start = game_start.tz_localize("UTC")
    else:
        game_start = game_start.tz_convert("UTC")
    is_future_game = game_start.to_pydatetime() > datetime.now(timezone.utc)
    injury_snapshot = fetch_live_injuries_response(sport, allow_live_download=False) if is_future_game else {
        "raw_payload": None, "teams": {}, "captured_at_utc": None,
        "is_from_cache": False, "cache_age_seconds": None, "payload_sha256": None,
    }
    all_injuries = injury_snapshot.get("teams") or {}
    raw_injury_payload = injury_snapshot.get("raw_payload")
    injury_snapshot_available = (
        isinstance(raw_injury_payload, dict)
        and isinstance(raw_injury_payload.get("injuries"), list)
    )

    def _find_team_injuries(abbr: str, name: str) -> List[Dict[str, Any]]:
        if abbr and abbr.upper() in all_injuries:
            return all_injuries[abbr.upper()]
        if name and name.lower().strip() in all_injuries:
            return all_injuries[name.lower().strip()]
        if name in all_injuries:
            return all_injuries[name]
        return []

    home_inj_list = _find_team_injuries(home_abbr, home_name)
    away_inj_list = _find_team_injuries(away_abbr, away_name)
    if not is_future_game:
        # Current injury feeds cannot be used to describe a historical event.
        home_inj_list = []
        away_inj_list = []

    def _calculate_impact(inj_list: List[Dict[str, Any]], team_abbr: str) -> Dict[str, Any]:
        if not is_future_game:
            return {"coverage_state": "not_applicable_past_game", "ppr_lost_pct": None, "off_snaps_lost_pct": None, "def_snaps_lost_pct": None}
        if sport == "NFL":
            try:
                snap_sha = injury_snapshot.get("payload_sha256")
                cache_key = (game.id, team_abbr, snap_sha)
                if cache_key in _IMPACT_CACHE:
                    cached_time, cached_val = _IMPACT_CACHE[cache_key]
                    if (time.time() - cached_time) < _IMPACT_CACHE_TTL_SECONDS:
                        return cached_val

                from app.services.nfl_availability_service import CoverageState, NFLAvailabilityService
                nfl_svc = NFLAvailabilityService()
                start_iso = game_start.isoformat()
                start_dt = nfl_svc.parse_utc_timestamp(start_iso)
                cutoff_iso = (start_dt - timedelta(hours=2)).isoformat() if start_dt else None

                # 1. Identificadores externos y semana de juego
                odds_event_id = None
                eval_week = None
                for cw in (game.crosswalk_entries or []):
                    if cw.provider == "the_odds_api":
                        odds_event_id = cw.provider_event_id
                    elif cw.provider == "nflverse" and cw.provider_event_id:
                        parts = cw.provider_event_id.split("_")
                        if len(parts) >= 2 and parts[1].isdigit():
                            eval_week = int(parts[1])
                if not odds_event_id:
                    odds_event_id = getattr(game, "external_id", None)

                # 2. Player props cacheados / disponibles de Odds API
                live_props = None
                if odds_event_id:
                    live_props = nfl_svc.get_cached_player_props_for_event(odds_event_id)

                # 3. Mapeo de identidad de jugadores (ESPN / NFLverse / PFR)
                player_crosswalk = nfl_svc.get_player_crosswalk()

                # 4. Procesamiento estricto de lesionados con resolución binaria de mercado
                records = [
                    nfl_svc.process_player_injury(
                        p,
                        team=team_abbr,
                        game_start_utc=start_iso,
                        decision_cutoff=cutoff_iso,
                        player_crosswalk=player_crosswalk,
                        live_props=live_props,
                        expected_event_id=odds_event_id,
                        binary_market_resolution=True,
                    )
                    for p in inj_list
                ]
                team_report_present = any(
                    key in all_injuries
                    for key in (team_abbr.upper(), home_name if team_abbr == home_abbr else away_name,
                                (home_name if team_abbr == home_abbr else away_name).lower())
                )
                captured_at = injury_snapshot.get("captured_at_utc")
                clean_ref = None
                if team_report_present and not records and snap_sha and captured_at:
                    clean_ref = f"espn:{snap_sha}:{team_abbr}:{captured_at}"

                # 5. Carga de estadísticas previas reales de nflverse (PPR, snaps ofensivos y defensivos)
                prior_roster_stats = nfl_svc.load_team_roster_stats_from_nflverse(
                    team=team_abbr,
                    as_of=start_iso,
                    evaluated_week=eval_week,
                )

                # 6. Cálculo matemático de impacto de producción perdida
                summary = nfl_svc.compute_team_production_lost(
                    team=team_abbr,
                    records=records,
                    prior_roster_stats=prior_roster_stats,
                    game_id=str(game.id),
                    decision_cutoff=cutoff_iso,
                    is_source_available=injury_snapshot_available,
                    no_injuries_listed=injury_snapshot_available and not team_report_present,
                    clean_report_evidence_ref=clean_ref,
                    allow_unresolved_market=True,
                )
                res = {
                    "coverage_state": summary.coverage_state,
                    "ppr_lost_pct": summary.ppr_lost_pct,
                    "off_snaps_lost_pct": summary.off_snaps_lost_pct,
                    "def_snaps_lost_pct": summary.def_snaps_lost_pct,
                }
                _IMPACT_CACHE[cache_key] = (time.time(), res)
                return res
            except Exception as e:
                logger.error("Error calculando impacto NFL point-in-time", team=team_abbr, error=str(e))
                return {
                    "coverage_state": CoverageState.UNKNOWN.value,
                    "ppr_lost_pct": None,
                    "off_snaps_lost_pct": None,
                    "def_snaps_lost_pct": None,
                }

        return {"coverage_state": "impact_model_unavailable", "ppr_lost_pct": None, "off_snaps_lost_pct": None, "def_snaps_lost_pct": None}

    injuries_data = {
        "home": home_inj_list,
        "away": away_inj_list,
        "home_impact": _calculate_impact(home_inj_list, home_abbr),
        "away_impact": _calculate_impact(away_inj_list, away_abbr)
    }

    # 5. Duelo Estadístico (Comparison Stats)
    def _clean_mean(series):
        if series is None or series.empty:
            return None
        m = series.mean()
        if pd.isna(m):
            return None
        return round(float(m), 1)

    def team_recent_points(team_id):
        if df is None:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        games = df.loc[(df["home_team_id"] == team_id) | (df["away_team_id"] == team_id)].sort_values("game_date").tail(10)
        scored, allowed = [], []
        for _, row in games.iterrows():
            is_home = row["home_team_id"] == team_id
            scored.append(row["home_score"] if is_home else row["away_score"])
            allowed.append(row["away_score"] if is_home else row["home_score"])
        return pd.Series(scored, dtype=float), pd.Series(allowed, dtype=float)

    home_scores, home_opp_scores = team_recent_points(h_id)
    away_scores, away_opp_scores = team_recent_points(a_id)
    home_ppg, home_opp_ppg = _clean_mean(home_scores), _clean_mean(home_opp_scores)
    away_ppg, away_opp_ppg = _clean_mean(away_scores), _clean_mean(away_opp_scores)

    comparison_stats = {
        "home": {
            "name": home_name,
            "abbr": home_abbr,
            "ppg": home_ppg,
            "opp_ppg": home_opp_ppg,
            "avg_margin": round(home_ppg - home_opp_ppg, 1) if home_ppg is not None and home_opp_ppg is not None else None,
            "days_rest": game.home_days_rest
        },
        "away": {
            "name": away_name,
            "abbr": away_abbr,
            "ppg": away_ppg,
            "opp_ppg": away_opp_ppg,
            "avg_margin": round(away_ppg - away_opp_ppg, 1) if away_ppg is not None and away_opp_ppg is not None else None,
            "days_rest": game.away_days_rest
        }
    }

    return sanitize_for_json({
        "game_id": game.id,
        "sport": sport,
        "matchup_label": f"{away_abbr} @ {home_abbr}",
        "home_team": {"id": game.home_team.id, "name": home_name, "abbr": home_abbr},
        "away_team": {"id": game.away_team.id, "name": away_name, "abbr": away_abbr},
        "h2h": h2h_data,
        "recent_form": recent_form,
        "injuries": injuries_data,
        "injury_source": {
            "status": "not_applicable_past_game" if not is_future_game else ("available" if injury_snapshot_available else "unavailable"),
            "source": "ESPN" if injury_snapshot_available else None,
            "captured_at_utc": injury_snapshot.get("captured_at_utc"),
            "is_from_cache": injury_snapshot.get("is_from_cache", False),
            "cache_age_seconds": injury_snapshot.get("cache_age_seconds"),
            "payload_sha256": injury_snapshot.get("payload_sha256"),
        },
        "comparison_stats": comparison_stats
    })
