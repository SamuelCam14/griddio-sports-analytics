"""
Script para inicializar la base de datos con equipos NBA y datos iniciales.
Uso:
    python scripts/seed_database.py
"""
import asyncio
from datetime import datetime, timezone
import structlog
from sqlalchemy import select
from nba_api.stats.static import teams as static_teams

from app.core.database import AsyncSessionLocal, init_db
from app.models.game import Team, Game, OddsSnapshot, Prediction
from app.services.odds_service import fetch_odds
from app.services.ev_engine import calculate_ev

logger = structlog.get_logger(__name__)

# Market size score para análisis de coleccionismo / Sports Cards (escala 1 a 10)
MARKET_SIZES = {
    "NYK": 10.0, "BKN": 9.0, "LAL": 10.0, "LAC": 8.5,
    "CHI": 9.0, "GSW": 9.5, "BOS": 9.0, "PHI": 8.5,
    "DAL": 8.0, "HOU": 8.0, "MIA": 8.5, "TOR": 8.5,
    "WAS": 7.0, "ATL": 7.0, "PHX": 7.5, "DEN": 7.0,
    "MIN": 6.5, "CLE": 6.5, "DET": 6.5, "ORL": 6.5,
    "IND": 6.0, "CHA": 6.0, "SAS": 6.5, "POR": 6.5,
    "SAC": 6.0, "MIL": 6.5, "OKC": 6.0, "NOP": 5.5,
    "UTA": 5.5, "MEM": 5.5,
}

CONFERENCES = {
    "BOS": ("East", "Atlantic"), "BKN": ("East", "Atlantic"), "NYK": ("East", "Atlantic"),
    "PHI": ("East", "Atlantic"), "TOR": ("East", "Atlantic"),
    "CHI": ("East", "Central"), "CLE": ("East", "Central"), "DET": ("East", "Central"),
    "IND": ("East", "Central"), "MIL": ("East", "Central"),
    "ATL": ("East", "Southeast"), "CHA": ("East", "Southeast"), "MIA": ("East", "Southeast"),
    "ORL": ("East", "Southeast"), "WAS": ("East", "Southeast"),
    "DEN": ("West", "Northwest"), "MIN": ("West", "Northwest"), "OKC": ("West", "Northwest"),
    "POR": ("West", "Northwest"), "UTA": ("West", "Northwest"),
    "GSW": ("West", "Pacific"), "LAC": ("West", "Pacific"), "LAL": ("West", "Pacific"),
    "PHX": ("West", "Pacific"), "SAC": ("West", "Pacific"),
    "DAL": ("West", "Southwest"), "HOU": ("West", "Southwest"), "MEM": ("West", "Southwest"),
    "NOP": ("West", "Southwest"), "SAS": ("West", "Southwest"),
}

async def seed_teams(session):
    """Inserta o actualiza los 30 equipos NBA."""
    all_teams = static_teams.get_teams()
    logger.info("Seeding NBA teams", count=len(all_teams))

    for t in all_teams:
        api_id = str(t["id"])
        abbr = t["abbreviation"]
        conf, div = CONFERENCES.get(abbr, ("Unknown", "Unknown"))
        market_score = MARKET_SIZES.get(abbr, 6.0)

        stmt = select(Team).where(Team.api_id == api_id)
        res = await session.execute(stmt)
        team = res.scalar_one_or_none()

        if not team:
            team = Team(
                api_id=api_id,
                sport="NBA",
                name=t["full_name"],
                abbreviation=abbr,
                city=t["city"],
                conference=conf,
                division=div,
                market_size_score=market_score,
                logo_url=f"/logos/{abbr.lower()}.svg",
            )
            session.add(team)
        else:
            team.name = t["full_name"]
            team.abbreviation = abbr
            team.city = t["city"]
            team.conference = conf
            team.division = div
            team.market_size_score = market_score
            team.logo_url = f"/logos/{abbr.lower()}.svg"

    await session.commit()
    logger.info("NBA teams seeded successfully")

async def sync_odds_and_predictions(session):
    """Obtiene momios en vivo, guarda partidos y genera predicciones EV+."""
    odds_list = await fetch_odds(sport="NBA")
    if not odds_list:
        logger.warning("No odds retrieved, skipping initial odds sync")
        return

    # Mapeo de nombres de equipos a objetos Team en DB
    stmt = select(Team).where(Team.sport == "NBA")
    res = await session.execute(stmt)
    teams_db = {t.name.lower(): t for t in res.scalars().all()}
    # También mapear por ciudad o última palabra
    for t in list(teams_db.values()):
        teams_db[t.abbreviation.lower()] = t
        teams_db[t.city.lower()] = t

    def find_team(name: str):
        if not name:
            return None
        low = name.lower()
        if low in teams_db:
            return teams_db[low]
        for k, v in teams_db.items():
            if k in low or low in k:
                return v
        return None

    logger.info("Processing games from odds feed", total=len(odds_list))
    saved_games = 0

    for item in odds_list:
        home_t = find_team(item["home_team"])
        away_t = find_team(item["away_team"])

        if not home_t or not away_t:
            continue

        commence_dt = datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00"))
        ext_id = item["external_id"]

        # Buscar o crear juego
        stmt = select(Game).where(Game.external_id == ext_id)
        res = await session.execute(stmt)
        game = res.scalar_one_or_none()

        if not game:
            game = Game(
                external_id=ext_id,
                sport="NBA",
                season="2025-26",
                season_type="Regular Season",
                game_date=commence_dt,
                home_team_id=home_t.id,
                away_team_id=away_t.id,
                status="scheduled",
            )
            session.add(game)
            await session.flush()

        # Guardar snapshot de momios
        if item.get("best_bookmaker"):
            captured_at = datetime.fromisoformat(item["captured_at"])
            snapshot = OddsSnapshot(
                game_id=game.id,
                bookmaker=item["best_bookmaker"],
                market="h2h",
                captured_at=captured_at,
                observed_at=captured_at,
                home_ml=item["home_ml"],
                away_ml=item["away_ml"],
                home_spread=item["home_spread"],
                home_spread_ml=item["home_spread_ml"],
                away_spread=item["away_spread"],
                total_line=item["total_line"],
                over_ml=item["over_ml"],
                under_ml=item["under_ml"],
                home_implied_prob=item["home_implied_prob"],
                away_implied_prob=item["away_implied_prob"],
            )
            session.add(snapshot)

        saved_games += 1

    await session.commit()
    logger.info("Odds synced (predictions disabled)", saved_games=saved_games)

async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_teams(session)
        await sync_odds_and_predictions(session)
    logger.info("Database seeding process complete! 🎉")

if __name__ == "__main__":
    asyncio.run(main())
