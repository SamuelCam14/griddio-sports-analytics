"""
Script para poblar la base de datos con tarjetas coleccionables (Sports Cards)
para el módulo de Sports Card Analytics & Arbitraje.
"""
import asyncio
from datetime import datetime, timezone
import structlog
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.models.card import SportsCard
from app.models.game import Team
from app.services.card_analytics_service import (
    calculate_fair_market_value,
    analyze_arbitrage_opportunity,
)

logger = structlog.get_logger(__name__)

INITIAL_CARDS = [
    {
        "player_name": "Victor Wembanyama",
        "sport": "NBA",
        "season": "2023-24",
        "team_abbr": "SAS",
        "college_team": "Metropolitans 92 (Francia)",
        "manufacturer": "Panini",
        "brand": "Prizm",
        "card_number": "#136",
        "card_type": "Rookie Card",
        "parallel_name": "Silver",
        "print_run": None,
        "has_autograph": False,
        "autograph_type": "None",
        "grade": "PSA 10",
        "current_market_price": 1450.0,
        "liquidity_score": 9.8,
        "image_url": "https://images.unsplash.com/photo-1546519638-68e109498ffc?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Victor Wembanyama",
        "sport": "NBA",
        "season": "2023-24",
        "team_abbr": "SAS",
        "college_team": "Metropolitans 92",
        "manufacturer": "Bowman",
        "brand": "Bowman U",
        "card_number": "#1",
        "card_type": "NIL",
        "parallel_name": "Refractor",
        "print_run": None,
        "has_autograph": True,
        "autograph_type": "Sticker",
        "grade": "Raw",
        "current_market_price": 420.0,
        "liquidity_score": 7.5,
        "image_url": "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Cooper Flagg",
        "sport": "NCAA",
        "season": "2024-25",
        "team_abbr": None,
        "college_team": "Duke Blue Devils",
        "manufacturer": "Bowman",
        "brand": "Bowman U Chrome",
        "card_number": "#CF-1",
        "card_type": "NIL",
        "parallel_name": "Refractor",
        "print_run": None,
        "has_autograph": False,
        "autograph_type": "None",
        "grade": "PSA 10",
        "current_market_price": 380.0,
        "liquidity_score": 8.5,
        "image_url": "https://images.unsplash.com/photo-1519766304817-4f37bda74a29?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Cooper Flagg",
        "sport": "NCAA",
        "season": "2024-25",
        "team_abbr": None,
        "college_team": "Duke Blue Devils",
        "manufacturer": "Bowman",
        "brand": "Bowman U Chrome",
        "card_number": "#AU-CF",
        "card_type": "NIL",
        "parallel_name": "Purple /99",
        "print_run": 99,
        "has_autograph": True,
        "autograph_type": "On-Card",
        "grade": "Raw",
        "current_market_price": 320.0,  # Subasta finalizando por debajo de FMV ($521) -> Arbitraje EV+
        "liquidity_score": 8.0,
        "image_url": "https://images.unsplash.com/photo-1546519638-68e109498ffc?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Chet Holmgren",
        "sport": "NBA",
        "season": "2022-23",
        "team_abbr": "OKC",
        "college_team": "Gonzaga Bulldogs",
        "manufacturer": "Panini",
        "brand": "Prizm",
        "card_number": "#266",
        "card_type": "Rookie Card",
        "parallel_name": "Silver",
        "print_run": None,
        "has_autograph": False,
        "autograph_type": "None",
        "grade": "PSA 10",
        "current_market_price": 210.0,
        "liquidity_score": 8.0,
        "image_url": "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Luka Doncic",
        "sport": "NBA",
        "season": "2018-19",
        "team_abbr": "DAL",
        "college_team": "Real Madrid (EuroLeague)",
        "manufacturer": "Panini",
        "brand": "Prizm",
        "card_number": "#280",
        "card_type": "Rookie Card",
        "parallel_name": "Base",
        "print_run": None,
        "has_autograph": False,
        "autograph_type": "None",
        "grade": "PSA 10",
        "current_market_price": 680.0,
        "liquidity_score": 9.5,
        "image_url": "https://images.unsplash.com/photo-1519766304817-4f37bda74a29?w=400&auto=format&fit=crop&q=80",
    },
    {
        "player_name": "Stephon Castle",
        "sport": "NBA",
        "season": "2024-25",
        "team_abbr": "SAS",
        "college_team": "UConn Huskies",
        "manufacturer": "Panini",
        "brand": "Hoops",
        "card_number": "#201",
        "card_type": "Rookie Card",
        "parallel_name": "Silver",
        "print_run": None,
        "has_autograph": False,
        "autograph_type": "None",
        "grade": "Raw",
        "current_market_price": 22.0,  # Listado subvalorado vs FMV ($51.30)
        "liquidity_score": 6.8,
        "image_url": "https://images.unsplash.com/photo-1546519638-68e109498ffc?w=400&auto=format&fit=crop&q=80",
    },
]


async def seed_sports_cards():
    await init_db()
    async with AsyncSessionLocal() as session:
        # Cargar equipos NBA para mapear team_id
        res = await session.execute(select(Team))
        teams = {t.abbreviation: t.id for t in res.scalars().all()}

        logger.info("Seeding initial sports cards catalog...")
        for item in INITIAL_CARDS:
            t_id = teams.get(item["team_abbr"]) if item.get("team_abbr") else None

            # Calcular valoración hedónica y arbitraje
            val = calculate_fair_market_value(
                player_name=item["player_name"],
                card_type=item["card_type"],
                grade=item["grade"],
                parallel_name=item["parallel_name"],
                autograph_type=item["autograph_type"],
                print_run=item["print_run"],
            )
            fmv = val["fair_market_value"]

            arb = analyze_arbitrage_opportunity(
                listing_price=item["current_market_price"],
                fair_market_value=fmv,
            )

            # Buscar o crear tarjeta
            stmt = select(SportsCard).where(
                (SportsCard.player_name == item["player_name"]) &
                (SportsCard.brand == item["brand"]) &
                (SportsCard.parallel_name == item["parallel_name"]) &
                (SportsCard.grade == item["grade"])
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if not existing:
                card = SportsCard(
                    player_name=item["player_name"],
                    sport=item["sport"],
                    season=item["season"],
                    team_id=t_id,
                    college_team=item["college_team"],
                    manufacturer=item["manufacturer"],
                    brand=item["brand"],
                    card_number=item["card_number"],
                    card_type=item["card_type"],
                    parallel_name=item["parallel_name"],
                    print_run=item["print_run"],
                    has_autograph=item["has_autograph"],
                    autograph_type=item["autograph_type"],
                    grade=item["grade"],
                    image_url=item["image_url"],
                    current_market_price=item["current_market_price"],
                    fair_market_value=fmv,
                    ev_opportunity=arb["is_arbitrage_opportunity"],
                    ev_percentage=arb["roi_percentage"],
                    liquidity_score=item["liquidity_score"],
                )
                session.add(card)
            else:
                existing.current_market_price = item["current_market_price"]
                existing.fair_market_value = fmv
                existing.ev_opportunity = arb["is_arbitrage_opportunity"]
                existing.ev_percentage = arb["roi_percentage"]

        await session.commit()
        logger.info("Sports cards catalog seeded successfully! 🃏")


if __name__ == "__main__":
    raise SystemExit("Disabled: card prices and valuations are sample data, not marketplace observations.")
