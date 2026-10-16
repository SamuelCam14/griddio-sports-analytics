"""Seed canonical 30 NBA teams and 32 NFL teams into database."""
import asyncio
import structlog
from sqlalchemy import select
from nba_api.stats.static import teams as static_nba_teams

from app.core.database import AsyncSessionLocal
from app.models.game import Team

logger = structlog.get_logger(__name__)

NFL_TEAMS = [
    {"name": "Pittsburgh Steelers", "abbr": "PIT", "city": "Pittsburgh", "conf": "AFC", "div": "North", "market": 7.5},
    {"name": "Cleveland Browns", "abbr": "CLE", "city": "Cleveland", "conf": "AFC", "div": "North", "market": 6.5},
    {"name": "Baltimore Ravens", "abbr": "BAL", "city": "Baltimore", "conf": "AFC", "div": "North", "market": 7.0},
    {"name": "Cincinnati Bengals", "abbr": "CIN", "city": "Cincinnati", "conf": "AFC", "div": "North", "market": 6.5},
    {"name": "Buffalo Bills", "abbr": "BUF", "city": "Buffalo", "conf": "AFC", "div": "East", "market": 6.5},
    {"name": "Miami Dolphins", "abbr": "MIA", "city": "Miami", "conf": "AFC", "div": "East", "market": 8.0},
    {"name": "New York Jets", "abbr": "NYJ", "city": "New York", "conf": "AFC", "div": "East", "market": 9.5},
    {"name": "New England Patriots", "abbr": "NE", "city": "Foxborough", "conf": "AFC", "div": "East", "market": 8.5},
    {"name": "Houston Texans", "abbr": "HOU", "city": "Houston", "conf": "AFC", "div": "South", "market": 8.0},
    {"name": "Indianapolis Colts", "abbr": "IND", "city": "Indianapolis", "conf": "AFC", "div": "South", "market": 6.5},
    {"name": "Jacksonville Jaguars", "abbr": "JAX", "city": "Jacksonville", "conf": "AFC", "div": "South", "market": 5.5},
    {"name": "Tennessee Titans", "abbr": "TEN", "city": "Nashville", "conf": "AFC", "div": "South", "market": 6.0},
    {"name": "Kansas City Chiefs", "abbr": "KC", "city": "Kansas City", "conf": "AFC", "div": "West", "market": 7.5},
    {"name": "Los Angeles Chargers", "abbr": "LAC", "city": "Los Angeles", "conf": "AFC", "div": "West", "market": 8.5},
    {"name": "Denver Broncos", "abbr": "DEN", "city": "Denver", "conf": "AFC", "div": "West", "market": 7.0},
    {"name": "Las Vegas Raiders", "abbr": "LV", "city": "Las Vegas", "conf": "AFC", "div": "West", "market": 7.5},
    {"name": "Dallas Cowboys", "abbr": "DAL", "city": "Dallas", "conf": "NFC", "div": "East", "market": 10.0},
    {"name": "Philadelphia Eagles", "abbr": "PHI", "city": "Philadelphia", "conf": "NFC", "div": "East", "market": 8.5},
    {"name": "Washington Commanders", "abbr": "WAS", "city": "Washington", "conf": "NFC", "div": "East", "market": 7.5},
    {"name": "New York Giants", "abbr": "NYG", "city": "New York", "conf": "NFC", "div": "East", "market": 9.5},
    {"name": "Detroit Lions", "abbr": "DET", "city": "Detroit", "conf": "NFC", "div": "North", "market": 7.0},
    {"name": "Green Bay Packers", "abbr": "GB", "city": "Green Bay", "conf": "NFC", "div": "North", "market": 8.0},
    {"name": "Minnesota Vikings", "abbr": "MIN", "city": "Minneapolis", "conf": "NFC", "div": "North", "market": 7.0},
    {"name": "Chicago Bears", "abbr": "CHI", "city": "Chicago", "conf": "NFC", "div": "North", "market": 9.0},
    {"name": "Atlanta Falcons", "abbr": "ATL", "city": "Atlanta", "conf": "NFC", "div": "South", "market": 7.5},
    {"name": "Tampa Bay Buccaneers", "abbr": "TB", "city": "Tampa", "conf": "NFC", "div": "South", "market": 7.0},
    {"name": "New Orleans Saints", "abbr": "NO", "city": "New Orleans", "conf": "NFC", "div": "South", "market": 6.5},
    {"name": "Carolina Panthers", "abbr": "CAR", "city": "Charlotte", "conf": "NFC", "div": "South", "market": 6.0},
    {"name": "San Francisco 49ers", "abbr": "SF", "city": "San Francisco", "conf": "NFC", "div": "West", "market": 9.0},
    {"name": "Los Angeles Rams", "abbr": "LAR", "city": "Los Angeles", "conf": "NFC", "div": "West", "market": 8.5},
    {"name": "Seattle Seahawks", "abbr": "SEA", "city": "Seattle", "conf": "NFC", "div": "West", "market": 7.5},
    {"name": "Arizona Cardinals", "abbr": "ARI", "city": "Phoenix", "conf": "NFC", "div": "West", "market": 7.0},
]

NBA_CONFERENCES = {
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

NBA_MARKET_SIZES = {
    "NYK": 10.0, "BKN": 9.0, "LAL": 10.0, "LAC": 8.5, "CHI": 9.0, "GSW": 9.5, "BOS": 9.0,
    "PHI": 8.5, "DAL": 8.0, "HOU": 8.0, "MIA": 8.5, "TOR": 8.5, "WAS": 7.0, "ATL": 7.0,
    "PHX": 7.5, "DEN": 7.0, "MIN": 6.5, "CLE": 6.5, "DET": 6.5, "ORL": 6.5, "IND": 6.0,
    "CHA": 6.0, "SAS": 6.5, "POR": 6.5, "SAC": 6.0, "MIL": 6.5, "OKC": 6.0, "NOP": 5.5,
    "UTA": 5.5, "MEM": 5.5,
}


async def main():
    async with AsyncSessionLocal() as session:
        # Seed NFL teams
        for t in NFL_TEAMS:
            api_id = f"nfl_{t['abbr'].lower()}"
            res = await session.execute(select(Team).where(Team.api_id == api_id))
            team = res.scalar_one_or_none()
            if not team:
                team = Team(
                    api_id=api_id,
                    sport="NFL",
                    name=t["name"],
                    abbreviation=t["abbr"],
                    city=t["city"],
                    conference=t["conf"],
                    division=t["div"],
                    market_size_score=t["market"],
                )
                session.add(team)
            else:
                team.name = t["name"]
                team.abbreviation = t["abbr"]
                team.city = t["city"]
                team.conference = t["conf"]
                team.division = t["div"]
                team.market_size_score = t["market"]

        # Seed NBA teams
        for t in static_nba_teams.get_teams():
            api_id = str(t["id"])
            abbr = t["abbreviation"]
            conf, div = NBA_CONFERENCES.get(abbr, ("Unknown", "Unknown"))
            market = NBA_MARKET_SIZES.get(abbr, 6.0)
            res = await session.execute(select(Team).where(Team.api_id == api_id))
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
                    market_size_score=market,
                )
                session.add(team)
            else:
                team.name = t["full_name"]
                team.abbreviation = abbr
                team.city = t["city"]
                team.conference = conf
                team.division = div
                team.market_size_score = market

        await session.commit()
        print("Equipos NFL (32) y NBA (30) registrados exitosamente.")


if __name__ == "__main__":
    asyncio.run(main())
