"""
Script de inicialización y semillero para la NFL:
1. Inserta los 32 equipos oficiales de la NFL (AFC y NFC).
2. Inserta la cartelera de partidos de la semana actual (incluyendo Steelers vs Browns hoy).
3. Inserta momios (Moneyline, Spread, Totals) y genera predicciones cuantitativas con EV y Kelly.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import structlog
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.models.game import Team, Game, OddsSnapshot, Prediction

logger = structlog.get_logger(__name__)

NFL_TEAMS = [
    # AFC North
    {"name": "Pittsburgh Steelers", "abbr": "PIT", "city": "Pittsburgh", "conf": "AFC", "div": "North", "market": 7.5},
    {"name": "Cleveland Browns", "abbr": "CLE", "city": "Cleveland", "conf": "AFC", "div": "North", "market": 6.5},
    {"name": "Baltimore Ravens", "abbr": "BAL", "city": "Baltimore", "conf": "AFC", "div": "North", "market": 7.0},
    {"name": "Cincinnati Bengals", "abbr": "CIN", "city": "Cincinnati", "conf": "AFC", "div": "North", "market": 6.5},
    # AFC East
    {"name": "Buffalo Bills", "abbr": "BUF", "city": "Buffalo", "conf": "AFC", "div": "East", "market": 6.5},
    {"name": "Miami Dolphins", "abbr": "MIA", "city": "Miami", "conf": "AFC", "div": "East", "market": 8.0},
    {"name": "New York Jets", "abbr": "NYJ", "city": "New York", "conf": "AFC", "div": "East", "market": 9.5},
    {"name": "New England Patriots", "abbr": "NE", "city": "Foxborough", "conf": "AFC", "div": "East", "market": 8.5},
    # AFC South
    {"name": "Houston Texans", "abbr": "HOU", "city": "Houston", "conf": "AFC", "div": "South", "market": 8.0},
    {"name": "Indianapolis Colts", "abbr": "IND", "city": "Indianapolis", "conf": "AFC", "div": "South", "market": 6.5},
    {"name": "Jacksonville Jaguars", "abbr": "JAX", "city": "Jacksonville", "conf": "AFC", "div": "South", "market": 5.5},
    {"name": "Tennessee Titans", "abbr": "TEN", "city": "Nashville", "conf": "AFC", "div": "South", "market": 6.0},
    # AFC West
    {"name": "Kansas City Chiefs", "abbr": "KC", "city": "Kansas City", "conf": "AFC", "div": "West", "market": 7.5},
    {"name": "Los Angeles Chargers", "abbr": "LAC", "city": "Los Angeles", "conf": "AFC", "div": "West", "market": 8.5},
    {"name": "Denver Broncos", "abbr": "DEN", "city": "Denver", "conf": "AFC", "div": "West", "market": 7.0},
    {"name": "Las Vegas Raiders", "abbr": "LV", "city": "Las Vegas", "conf": "AFC", "div": "West", "market": 7.5},
    # NFC East
    {"name": "Dallas Cowboys", "abbr": "DAL", "city": "Dallas", "conf": "NFC", "div": "East", "market": 10.0},
    {"name": "Philadelphia Eagles", "abbr": "PHI", "city": "Philadelphia", "conf": "NFC", "div": "East", "market": 8.5},
    {"name": "Washington Commanders", "abbr": "WAS", "city": "Washington", "conf": "NFC", "div": "East", "market": 7.5},
    {"name": "New York Giants", "abbr": "NYG", "city": "New York", "conf": "NFC", "div": "East", "market": 9.5},
    # NFC North
    {"name": "Detroit Lions", "abbr": "DET", "city": "Detroit", "conf": "NFC", "div": "North", "market": 7.0},
    {"name": "Green Bay Packers", "abbr": "GB", "city": "Green Bay", "conf": "NFC", "div": "North", "market": 8.0},
    {"name": "Minnesota Vikings", "abbr": "MIN", "city": "Minneapolis", "conf": "NFC", "div": "North", "market": 7.0},
    {"name": "Chicago Bears", "abbr": "CHI", "city": "Chicago", "conf": "NFC", "div": "North", "market": 9.0},
    # NFC South
    {"name": "Atlanta Falcons", "abbr": "ATL", "city": "Atlanta", "conf": "NFC", "div": "South", "market": 7.5},
    {"name": "Tampa Bay Buccaneers", "abbr": "TB", "city": "Tampa", "conf": "NFC", "div": "South", "market": 7.0},
    {"name": "New Orleans Saints", "abbr": "NO", "city": "New Orleans", "conf": "NFC", "div": "South", "market": 6.5},
    {"name": "Carolina Panthers", "abbr": "CAR", "city": "Charlotte", "conf": "NFC", "div": "South", "market": 6.0},
    # NFC West
    {"name": "San Francisco 49ers", "abbr": "SF", "city": "San Francisco", "conf": "NFC", "div": "West", "market": 9.0},
    {"name": "Los Angeles Rams", "abbr": "LAR", "city": "Los Angeles", "conf": "NFC", "div": "West", "market": 8.5},
    {"name": "Seattle Seahawks", "abbr": "SEA", "city": "Seattle", "conf": "NFC", "div": "West", "market": 7.5},
    {"name": "Arizona Cardinals", "abbr": "ARI", "city": "Phoenix", "conf": "NFC", "div": "West", "market": 7.0},
]

# Cartelera de la Semana NFL
now_utc = datetime.now(timezone.utc)
GAMES_SLATE = [
    # 1. Thursday Night Football (HOY)
    {
        "home_abbr": "CLE", "away_abbr": "PIT",
        "date_offset_hours": 4,  # Hoy por la noche
        "home_ml": 125, "away_ml": -150,
        "spread": 3.0, "total": 37.5,
        "home_win_prob": 0.405, "away_win_prob": 0.595,
        "confluence_tier": "★★★",
        "best_bm": "DraftKings",
        "away_ev": 8.4, "home_ev": -6.2,
        "away_has_edge": True, "home_has_edge": False,
        "away_kelly": 0.042, "home_kelly": 0.0,
    },
    # 2. Sunday Slate - Kansas City vs Baltimore
    {
        "home_abbr": "KC", "away_abbr": "BAL",
        "date_offset_hours": 72,
        "home_ml": -140, "away_ml": 120,
        "spread": -2.5, "total": 47.5,
        "home_win_prob": 0.655, "away_win_prob": 0.345,
        "confluence_tier": "★★★",
        "best_bm": "FanDuel",
        "home_ev": 12.3, "away_ev": -11.0,
        "home_has_edge": True, "away_has_edge": False,
        "home_kelly": 0.048, "away_kelly": 0.0,
    },
    # 3. Sunday Slate - 49ers vs Cowboys
    {
        "home_abbr": "SF", "away_abbr": "DAL",
        "date_offset_hours": 75,
        "home_ml": -180, "away_ml": 155,
        "spread": -4.0, "total": 45.0,
        "home_win_prob": 0.680, "away_win_prob": 0.320,
        "confluence_tier": "★★★",
        "best_bm": "BetMGM",
        "home_ev": 5.4, "away_ev": -8.5,
        "home_has_edge": True, "away_has_edge": False,
        "home_kelly": 0.025, "away_kelly": 0.0,
    },
    # 4. Sunday Slate - Detroit Lions vs Green Bay Packers
    {
        "home_abbr": "DET", "away_abbr": "GB",
        "date_offset_hours": 70,
        "home_ml": -115, "away_ml": -105,
        "spread": -1.5, "total": 48.5,
        "home_win_prob": 0.582, "away_win_prob": 0.418,
        "confluence_tier": "★★",
        "best_bm": "Caesars",
        "home_ev": 8.8, "away_ev": -7.2,
        "home_has_edge": True, "away_has_edge": False,
        "home_kelly": 0.035, "away_kelly": 0.0,
    },
    # 5. Sunday Slate - Buffalo Bills vs Houston Texans
    {
        "home_abbr": "HOU", "away_abbr": "BUF",
        "date_offset_hours": 68,
        "home_ml": 110, "away_ml": -130,
        "spread": 1.5, "total": 47.0,
        "home_win_prob": 0.440, "away_win_prob": 0.560,
        "confluence_tier": "★★",
        "best_bm": "DraftKings",
        "home_ev": -3.5, "away_ev": 4.8,
        "home_has_edge": False, "away_has_edge": True,
        "home_kelly": 0.0, "away_kelly": 0.028,
    },
]


async def seed_nfl():
    print("🌱 Iniciando semillero de NFL (Equipos, Cartelera de Partidos y Predicciones)...")
    await init_db()

    async with AsyncSessionLocal() as session:
        # 1. Insertar Equipos
        team_map = {}
        for t in NFL_TEAMS:
            api_id = f"nfl_{t['abbr'].lower()}"
            stmt = select(Team).where(Team.api_id == api_id)
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()

            if not existing:
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
                await session.flush()
                team_map[t["abbr"]] = team.id
                print(f"  + Equipo agregado: {t['name']} ({t['abbr']})")
            else:
                team_map[t["abbr"]] = existing.id

        # 2. Insertar Juegos y Predicciones
        for g_data in GAMES_SLATE:
            h_id = team_map.get(g_data["home_abbr"])
            a_id = team_map.get(g_data["away_abbr"])
            if not h_id or not a_id:
                continue

            ext_id = f"nfl_2026_{g_data['home_abbr']}_{g_data['away_abbr']}"
            g_date = now_utc + timedelta(hours=g_data["date_offset_hours"])

            stmt = select(Game).where(Game.external_id == ext_id)
            res = await session.execute(stmt)
            game = res.scalar_one_or_none()

            if not game:
                game = Game(
                    external_id=ext_id,
                    sport="NFL",
                    season="2026",
                    season_type="Regular Season",
                    game_date=g_date,
                    home_team_id=h_id,
                    away_team_id=a_id,
                    status="scheduled",
                )
                session.add(game)
                await session.flush()
                print(f"  + Partido programado: {g_data['away_abbr']} @ {g_data['home_abbr']} ({g_date.strftime('%Y-%m-%d %H:%M UTC')})")

            # Odds Snapshot
            stmt = select(OddsSnapshot).where(OddsSnapshot.game_id == game.id)
            res = await session.execute(stmt)
            existing_odds = res.scalar_one_or_none()

            if not existing_odds:
                odds = OddsSnapshot(
                    game_id=game.id,
                    bookmaker=g_data["best_bm"],
                    market="h2h,spreads,totals",
                    captured_at=now_utc,
                    observed_at=now_utc,
                    home_ml=g_data["home_ml"],
                    away_ml=g_data["away_ml"],
                    home_spread=g_data["spread"],
                    away_spread=-g_data["spread"],
                    total_line=g_data["total"],
                )
                session.add(odds)

            # Prediction
            stmt = select(Prediction).where(Prediction.game_id == game.id)
            res = await session.execute(stmt)
            existing_pred = res.scalar_one_or_none()

            if not existing_pred:
                pred = Prediction(
                    game_id=game.id,
                    model_version="v2.0",
                    home_win_prob=g_data["home_win_prob"],
                    away_win_prob=g_data["away_win_prob"],
                    confluence_tier=g_data["confluence_tier"],
                    model_agreement=3 if g_data["confluence_tier"] == "★★★" else 2,
                    best_bookmaker=g_data["best_bm"],
                    best_home_ml=g_data["home_ml"],
                    best_away_ml=g_data["away_ml"],
                    home_ev=g_data["home_ev"],
                    away_ev=g_data["away_ev"],
                    home_kelly=g_data["home_kelly"],
                    away_kelly=g_data["away_kelly"],
                    home_has_edge=g_data["home_has_edge"],
                    away_has_edge=g_data["away_has_edge"],
                )
                session.add(pred)

        await session.commit()
        print("✅ Semillero de la NFL completado con éxito!")


if __name__ == "__main__":
    raise SystemExit("Disabled: this seed contains synthetic NFL schedules, odds, and predictions. Use the live API sync.")
