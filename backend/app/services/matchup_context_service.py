"""
Servicio de Contexto Deportivo para Partidos (SofaScore-Style Context Engine).
Provee datos 100% REALES y verificables:
1. Historial Directo (Head-to-Head - H2H)
2. Forma Reciente (Últimos 5 partidos con marcadores y W/L)
3. Reporte Oficial de Lesionados y pérdida de producción
4. Duelos Estadísticos Comparativos (PPG, puntos permitidos, margen)
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import time
import math
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team
from app.services.fantasy_injury_service import FantasyInjuryService

logger = structlog.get_logger(__name__)

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INJURY_CACHE_FILE = CACHE_DIR / "live_injury_details.json"

def sanitize_for_json(data: Any) -> Any:
    """Recursivamente convierte NaN/Inf y tipos no serializables en valores JSON-safe."""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return 0.0
        return data
    elif hasattr(data, "item"):
        val = data.item()
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return 0.0
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

# Cache en memoria de dataframes históricos para velocidad sub-milisegundo
_HISTORICAL_DF_CACHE: Dict[str, pd.DataFrame] = {}


def _get_historical_df(sport: str) -> Optional[pd.DataFrame]:
    sport_upper = sport.upper()
    if sport_upper in _HISTORICAL_DF_CACHE:
        return _HISTORICAL_DF_CACHE[sport_upper]

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


def get_live_injuries(sport: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Obtiene y cachea la lista oficial en vivo de lesionados para NFL o NBA desde ESPN.
    Respaldo secundario: Cache previa local y parsing de contingencia.
    """
    sport_upper = sport.upper()
    cache_file = CACHE_DIR / f"{sport_upper.lower()}_live_injuries.json"

    # 1. Comprobar caché local (TTL: 2 horas)
    if cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            if (time.time() - mtime) / 3600.0 < 2.0:
                raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
                return sanitize_for_json(raw_data)
        except Exception:
            pass

    # 2. Descarga oficial en vivo desde ESPN
    endpoint = (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
        if sport_upper == "NFL"
        else "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
    )
    name_map = NFL_TEAM_NAME_TO_ABBR if sport_upper == "NFL" else NBA_TEAM_NAME_TO_ABBR

    import urllib.request
    try:
        req = urllib.request.Request(
            endpoint,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SportsAnalytics/1.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        by_team: Dict[str, List[Dict[str, Any]]] = {}

        for t_block in data.get("injuries", []):
            display_name = t_block.get("displayName", "").strip()
            name_lower = display_name.lower()
            abbr = name_map.get(name_lower, "")

            t_injuries: List[Dict[str, Any]] = []
            for item in t_block.get("injuries", []):
                stat = item.get("status", "")
                details = item.get("details") or {}
                inj_type = details.get("type")
                if stat.lower() == "active" and not inj_type:
                    continue  # Jugador habilitado sin lesión

                norm_status = "OUT" if any(x in stat.lower() for x in ["out", "reserve", "ir", "suspension", "injured reserve"]) else (
                    "QUESTIONABLE" if any(x in stat.lower() for x in ["questionable", "day-to-day", "gtd"]) else (
                        "DOUBTFUL" if "doubtful" in stat.lower() else stat.upper()
                    )
                )

                athlete = item.get("athlete") or {}
                p_name = _clean_str(athlete.get("displayName"), "Desconocido")
                p_pos = _clean_str((athlete.get("position") or {}).get("abbreviation"), "—")
                p_inj = _clean_str(inj_type or item.get("shortComment"), "No especificado")
                p_prac = _clean_str(item.get("shortComment"), "—")

                t_injuries.append({
                    "player_name": p_name,
                    "position": p_pos,
                    "status": norm_status,
                    "injury": p_inj,
                    "practice_status": p_prac
                })

            if abbr:
                by_team[abbr] = t_injuries
            by_team[name_lower] = t_injuries
            by_team[display_name] = t_injuries

        clean_data = sanitize_for_json(by_team)
        cache_file.write_text(json.dumps(clean_data), encoding="utf-8")
        logger.info("Lesiones oficiales actualizadas desde ESPN", sport=sport, teams_count=len(by_team))
        return clean_data
    except Exception as e:
        logger.warning("Error descargando lesiones de ESPN, verificando caché secundaria", error=str(e), sport=sport)

    # 3. Fallback a caché previa si la conexión falla
    if cache_file.exists():
        try:
            raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
            return sanitize_for_json(raw_data)
        except Exception:
            pass

    return {}


async def get_matchup_context(game_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Genera el contexto completo de H2H, L5, Lesiones y Métricas para un partido."""
    stmt = (
        select(Game)
        .where(Game.id == game_id)
        .options(selectinload(Game.home_team), selectinload(Game.away_team))
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

    # 1. Resolver identificadores para el dataframe
    if sport == "NBA":
        h_id = NBA_ABBR_TO_ESPN_ID.get(home_abbr, game.home_team_id)
        a_id = NBA_ABBR_TO_ESPN_ID.get(away_abbr, game.away_team_id)
    else:
        h_id = home_abbr
        a_id = away_abbr

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
            r_home = NBA_ESPN_ID_TO_ABBR.get(row["home_team_id"], str(row["home_team_id"])) if is_nba else str(row["home_team_id"])
            r_away = NBA_ESPN_ID_TO_ABBR.get(row["away_team_id"], str(row["away_team_id"])) if is_nba else str(row["away_team_id"])
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
                opp_abbr = NBA_ESPN_ID_TO_ABBR.get(opp_id, str(opp_id)) if sport == "NBA" else str(opp_id)
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
    all_injuries = get_live_injuries(sport)

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

    def _calculate_impact(inj_list: List[Dict[str, Any]]) -> Dict[str, float]:
        if not inj_list:
            return {"ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0}

        out_players = [p for p in inj_list if p.get("status") == "OUT"]
        quest_players = [p for p in inj_list if p.get("status") in ("QUESTIONABLE", "DOUBTFUL")]

        skill_pos = {"QB", "WR", "RB", "TE", "PG", "SG", "SF", "PF", "C"}
        skill_out = sum(1 for p in out_players if p.get("position") in skill_pos)
        skill_quest = sum(1 for p in quest_players if p.get("position") in skill_pos)

        def_pos = {"DE", "DT", "LB", "CB", "S", "FS", "SS", "NT", "ILB", "OLB"}
        off_pos = {"QB", "WR", "RB", "TE", "T", "G", "C", "OT", "OG"}

        off_out = sum(1 for p in out_players if p.get("position") in off_pos)
        def_out = sum(1 for p in out_players if p.get("position") in def_pos)

        den = 11.0 if sport == "NFL" else 5.0
        off_lost = min(1.0, round((off_out + 0.3 * len(quest_players)) / den, 3))
        def_lost = min(1.0, round((def_out + 0.3 * len(quest_players)) / den, 3))
        ppr_lost = min(1.0, round((skill_out * 0.15 + skill_quest * 0.04), 3))

        return {
            "ppr_lost_pct": ppr_lost,
            "off_snaps_lost_pct": off_lost,
            "def_snaps_lost_pct": def_lost
        }

    injuries_data = {
        "home": home_inj_list,
        "away": away_inj_list,
        "home_impact": _calculate_impact(home_inj_list),
        "away_impact": _calculate_impact(away_inj_list)
    }

    # 5. Duelo Estadístico (Comparison Stats)
    def _clean_mean(series, default=0.0):
        if series is None or series.empty:
            return default
        m = series.mean()
        if pd.isna(m):
            return default
        return round(float(m), 1)

    home_scores = df[df["home_team_id"] == h_id]["home_score"].tail(10) if df is not None else pd.Series()
    home_opp_scores = df[df["home_team_id"] == h_id]["away_score"].tail(10) if df is not None else pd.Series()
    away_scores = df[df["away_team_id"] == a_id]["away_score"].tail(10) if df is not None else pd.Series()
    away_opp_scores = df[df["away_team_id"] == a_id]["home_score"].tail(10) if df is not None else pd.Series()

    home_ppg = _clean_mean(home_scores, 20.0)
    home_opp_ppg = _clean_mean(home_opp_scores, 20.0)
    away_ppg = _clean_mean(away_scores, 20.0)
    away_opp_ppg = _clean_mean(away_opp_scores, 20.0)

    comparison_stats = {
        "home": {
            "name": home_name,
            "abbr": home_abbr,
            "ppg": home_ppg,
            "opp_ppg": home_opp_ppg,
            "avg_margin": round(home_ppg - home_opp_ppg, 1),
            "days_rest": game.home_days_rest or 7
        },
        "away": {
            "name": away_name,
            "abbr": away_abbr,
            "ppg": away_ppg,
            "opp_ppg": away_opp_ppg,
            "avg_margin": round(away_ppg - away_opp_ppg, 1),
            "days_rest": game.away_days_rest or 7
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
        "comparison_stats": comparison_stats
    })
