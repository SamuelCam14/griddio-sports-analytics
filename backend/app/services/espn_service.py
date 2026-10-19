"""Read ESPN's public scoreboard and reconcile scores for known games."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import math
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team
from ml.data_pipeline.common import project_root

logger = structlog.get_logger(__name__)
ESPN_SCOREBOARD_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
}
ESPN_TEAM_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams?limit=1000",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=1000",
}


async def sync_espn_team_catalog(sport: str, db: AsyncSession) -> int:
    """Upsert missing team identities from ESPN's official team feed."""
    sport = sport.upper()
    url = ESPN_TEAM_URLS.get(sport)
    if not url:
        return 0
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, headers={"User-Agent": "sports-analytics/1.0"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN team catalog unavailable", sport=sport, error=str(exc))
        return 0

    # Canonical abbreviation crosswalk — ESPN uses some non-canonical codes
    _ABBR_CROSSWALK = {"WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LAR"}

    league_list = payload.get("sports", [{}])[0].get("leagues", [])
    team_entries = league_list[0].get("teams", []) if league_list else []
    existing_result = await db.execute(select(Team).where(Team.sport == sport))
    existing_teams = existing_result.scalars().all()
    existing_abbrs = {team.abbreviation.upper() for team in existing_teams}
    existing_names = {team.name.strip().lower() for team in existing_teams}
    inserted = 0
    for entry in team_entries:
        data = entry.get("team", {})
        raw_abbr = data.get("abbreviation")
        espn_id = data.get("id")
        if not raw_abbr or not espn_id:
            continue
        # Canonicalize the abbreviation before any check
        abbreviation = _ABBR_CROSSWALK.get(raw_abbr.upper(), raw_abbr.upper())
        display_name = (data.get("displayName") or data.get("name") or abbreviation).strip()
        # Skip if already tracked by abbreviation OR by full team name
        if abbreviation in existing_abbrs or display_name.lower() in existing_names:
            continue
        logos = data.get("logos") or []
        groups = data.get("groups")
        conference = groups.get("name") if isinstance(groups, dict) else None
        db.add(Team(
            api_id=f"espn_{sport}_{espn_id}",
            sport=sport,
            name=display_name,
            abbreviation=abbreviation,
            city=data.get("location"),
            conference=conference,
            logo_url=logos[0].get("href") if logos else None,
        ))
        existing_abbrs.add(abbreviation)
        existing_names.add(display_name.lower())
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


async def sync_espn_scores(sport: str, db: AsyncSession) -> dict[str, Any]:
    """Update score/status only when ESPN has a matching event and teams."""
    sport = sport.upper()
    url = ESPN_SCOREBOARD_URLS.get(sport)
    if not url:
        return {"status": "unsupported_sport", "updated": 0}

    teams_added = await sync_espn_team_catalog(sport, db)

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, headers={"User-Agent": "sports-analytics/1.0"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN scoreboard unavailable", sport=sport, error=str(exc))
        return {"status": "unavailable", "updated": 0}

    teams_result = await db.execute(select(Team).where(Team.sport == sport))
    by_abbr = {team.abbreviation.upper(): team for team in teams_result.scalars().all()}
    games_result = await db.execute(select(Game).where(Game.sport == sport))
    games = games_result.scalars().all()
    games_by_key = {
        (g.game_date.astimezone(timezone.utc).date(), g.home_team_id, g.away_team_id): g
        for g in games
    }

    updated = 0
    for event in payload.get("events", []):
        try:
            event_date = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            competition = event["competitions"][0]
            competitors = competition["competitors"]
            home_data = next(c for c in competitors if c.get("homeAway") == "home")
            away_data = next(c for c in competitors if c.get("homeAway") == "away")
            home = by_abbr.get(home_data.get("team", {}).get("abbreviation", "").upper())
            away = by_abbr.get(away_data.get("team", {}).get("abbreviation", "").upper())
            if not home or not away:
                continue
            game = games_by_key.get((event_date.astimezone(timezone.utc).date(), home.id, away.id))
            if not game:
                continue

            status_type = competition.get("status", {}).get("type", {})
            state = status_type.get("state")
            if state in {"pre", "in", "post"}:
                game.status = {"pre": "scheduled", "in": "live", "post": "final"}[state]
            if status_type.get("completed"):
                game.status = "final"
            home_score = home_data.get("score")
            away_score = away_data.get("score")
            if home_score not in (None, "") and away_score not in (None, ""):
                game.home_score = int(float(home_score))
                game.away_score = int(float(away_score))
                if game.status == "final":
                    game.home_win = game.home_score > game.away_score
            updated += 1
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed ESPN scoreboard event", sport=sport, error=str(exc))

    await db.commit()
    return {
        "status": "success", "updated": updated, "teams_added": teams_added,
        "source": "ESPN scoreboard",
    }


CACHE_DIR = project_root() / "data" / "cache"
ESPN_PREDICTIONS_CACHE_FILE = CACHE_DIR / "espn_predictions.json"
_ESPN_PREDICTIONS_CACHE: dict[str, dict[str, Any]] = {}


def _load_espn_prediction_from_cache(game_id: Any) -> Optional[dict[str, Any]]:
    key = str(game_id)
    if key in _ESPN_PREDICTIONS_CACHE:
        return _ESPN_PREDICTIONS_CACHE[key]
    if ESPN_PREDICTIONS_CACHE_FILE.is_file():
        try:
            data = json.loads(ESPN_PREDICTIONS_CACHE_FILE.read_text(encoding="utf-8"))
            if key in data:
                _ESPN_PREDICTIONS_CACHE[key] = data[key]
                return data[key]
        except Exception:
            pass
    return None


def _save_espn_prediction_to_cache(game_id: Any, payload: dict[str, Any]) -> None:
    key = str(game_id)
    _ESPN_PREDICTIONS_CACHE[key] = payload
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        if ESPN_PREDICTIONS_CACHE_FILE.is_file():
            try:
                data = json.loads(ESPN_PREDICTIONS_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[key] = payload
        temp_file = ESPN_PREDICTIONS_CACHE_FILE.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_file.replace(ESPN_PREDICTIONS_CACHE_FILE)
    except Exception as exc:
        logger.warning("Error guardando predicción externa de ESPN en caché", error=str(exc))


async def get_cached_espn_prediction(game: Game) -> dict[str, Any]:
    """Lee exclusivamente la predicción externa de ESPN pre-ingerida en caché sin llamadas de red."""
    return await fetch_espn_matchup_predictor(game, allow_network=False)


async def fetch_espn_matchup_predictor(game: Game, allow_network: bool = True) -> dict[str, Any]:
    """Read ESPN's pregame Matchup Predictor for an exact event identity.

    This is a third-party forecast, never the Griddio production model. The
    event must match both team abbreviations and kickoff to the second.
    """
    if game.sport != "NFL" or not game.game_date or not game.home_team or not game.away_team:
        return {"status": "unavailable", "reason": "unsupported_or_incomplete_game_identity"}

    kickoff = game.game_date
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    retrieved_dt = datetime.now(timezone.utc)
    if kickoff.astimezone(timezone.utc) <= retrieved_dt:
        return {
            "status": "unavailable", "reason": "event_started_or_finished",
            "source": "ESPN Matchup Predictor", "retrieved_at_utc": retrieved_dt.isoformat(),
        }

    # 1. Si no se permite red (ej. en el request thread de endpoints GET), responder exclusivamente de caché
    if not allow_network:
        cached = _load_espn_prediction_from_cache(game.id)
        if cached is not None:
            return cached
        return {
            "status": "unavailable",
            "reason": "not_cached",
            "source": "ESPN Matchup Predictor",
            "game_id": game.id,
            "home_team": game.home_team.name if getattr(game, "home_team", None) else None,
            "away_team": game.away_team.name if getattr(game, "away_team", None) else None,
            "production_model": False,
            "retrieved_at_utc": retrieved_dt.isoformat(),
            "message": "Pronóstico externo no pre-ingerido en caché local. No se realizan llamadas de red en tiempo de consulta.",
        }

    local_game_date = kickoff.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    scoreboard_url = f"{ESPN_SCOREBOARD_URLS['NFL']}?dates={local_game_date}"
    retrieved_at = retrieved_dt.isoformat()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            scoreboard_response = await client.get(scoreboard_url, headers={"User-Agent": "sports-analytics/1.0"})
            scoreboard_response.raise_for_status()
            scoreboard = scoreboard_response.json()

            exact_events = []
            for event in scoreboard.get("events", []):
                event_kickoff = datetime.fromisoformat(str(event.get("date", "")).replace("Z", "+00:00"))
                if event_kickoff.tzinfo is None:
                    event_kickoff = event_kickoff.replace(tzinfo=timezone.utc)
                competitors = event.get("competitions", [{}])[0].get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if (
                    home and away
                    and str(home.get("team", {}).get("abbreviation", "")).upper() == game.home_team.abbreviation.upper()
                    and str(away.get("team", {}).get("abbreviation", "")).upper() == game.away_team.abbreviation.upper()
                    and event_kickoff.astimezone(timezone.utc).replace(microsecond=0)
                    == kickoff.astimezone(timezone.utc).replace(microsecond=0)
                ):
                    exact_events.append((event, home, away))

            if len(exact_events) != 1:
                return {
                    "status": "unavailable", "reason": "event_identity_not_unique",
                    "source": "ESPN Matchup Predictor", "retrieved_at_utc": retrieved_at,
                    "matching_events": len(exact_events),
                }

            event, scoreboard_home, scoreboard_away = exact_events[0]
            event_id = str(event.get("id") or "")
            if not event_id:
                return {"status": "unavailable", "reason": "provider_event_id_missing", "source": "ESPN Matchup Predictor"}

            summary_url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
            summary_response = await client.get(summary_url, headers={"User-Agent": "sports-analytics/1.0"})
            summary_response.raise_for_status()
            summary = summary_response.json()

        predictor = summary.get("predictor") or {}
        predictor_home = predictor.get("homeTeam") or {}
        predictor_away = predictor.get("awayTeam") or {}
        if (
            str(predictor_home.get("id", "")) != str(scoreboard_home.get("team", {}).get("id", ""))
            or str(predictor_away.get("id", "")) != str(scoreboard_away.get("team", {}).get("id", ""))
        ):
            return {"status": "unavailable", "reason": "provider_team_identity_mismatch", "source": "ESPN Matchup Predictor", "event_id": event_id}

        home_pct = float(predictor_home.get("gameProjection"))
        away_pct = float(predictor_away.get("gameProjection"))
        if not (math.isfinite(home_pct) and math.isfinite(away_pct) and 0.0 <= home_pct <= 100.0 and 0.0 <= away_pct <= 100.0):
            return {"status": "unavailable", "reason": "invalid_provider_probability", "source": "ESPN Matchup Predictor", "event_id": event_id}

        result = {
            "status": "available",
            "sport": "NFL",
            "game_id": game.id,
            "event_id": event_id,
            "home_team": game.home_team.name,
            "away_team": game.away_team.name,
            "home_win_probability": home_pct / 100.0,
            "away_win_probability": away_pct / 100.0,
            "probability_sum": (home_pct + away_pct) / 100.0,
            "source": "ESPN Matchup Predictor",
            "source_field": "predictor.homeTeam.gameProjection / predictor.awayTeam.gameProjection",
            "retrieved_at_utc": retrieved_at,
            "production_model": False,
            "reason": "Pronóstico externo de ESPN; no es el modelo Griddio ni una recomendación de apuesta.",
        }
        if isinstance(game, Game):
            _save_espn_prediction_to_cache(game.id, result)
        return result
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, StopIteration) as exc:
        logger.warning("ESPN matchup predictor unavailable", game_id=game.id, error_type=type(exc).__name__)
        return {
            "status": "unavailable", "reason": "provider_unavailable_or_invalid_response",
            "source": "ESPN Matchup Predictor", "retrieved_at_utc": retrieved_at,
        }
