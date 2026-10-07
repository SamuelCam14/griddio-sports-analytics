"""Read ESPN's public scoreboard and reconcile scores for known games."""
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, Team

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

    league_list = payload.get("sports", [{}])[0].get("leagues", [])
    team_entries = league_list[0].get("teams", []) if league_list else []
    existing_result = await db.execute(select(Team).where(Team.sport == sport))
    existing_abbrs = {team.abbreviation.upper() for team in existing_result.scalars().all()}
    inserted = 0
    for entry in team_entries:
        data = entry.get("team", {})
        abbreviation = data.get("abbreviation")
        espn_id = data.get("id")
        if not abbreviation or not espn_id or abbreviation.upper() in existing_abbrs:
            continue
        logos = data.get("logos") or []
        groups = data.get("groups")
        conference = groups.get("name") if isinstance(groups, dict) else None
        db.add(Team(
            api_id=f"espn_{sport}_{espn_id}",
            sport=sport,
            name=data.get("displayName") or data.get("name") or abbreviation,
            abbreviation=abbreviation.upper(),
            city=data.get("location"),
            conference=conference,
            logo_url=logos[0].get("href") if logos else None,
        ))
        existing_abbrs.add(abbreviation.upper())
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
