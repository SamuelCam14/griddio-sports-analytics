"""
Router: Odds — /api/v1/odds

Endpoints:
  GET /nba          → Momios actuales NBA con probabilidades implícitas sin vig
  GET /nfl          → Momios actuales NFL
  GET /remaining    → Requests restantes del plan gratuito
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.odds_service import fetch_odds_from_db, fetch_remaining_requests

router = APIRouter()


@router.get("/nba", summary="Momios actuales NBA")
async def get_nba_odds(db: AsyncSession = Depends(get_db)):
    try:
        data = await fetch_odds_from_db(sport="NBA", session=db)
    except Exception as e:
        data = []
    return {"sport": "NBA", "total_games": len(data), "games": data}


@router.get("/nfl", summary="Momios actuales NFL")
async def get_nfl_odds(db: AsyncSession = Depends(get_db)):
    try:
        data = await fetch_odds_from_db(sport="NFL", session=db)
    except Exception as e:
        data = []
    return {"sport": "NFL", "total_games": len(data), "games": data}


@router.get("/remaining", summary="Requests restantes del plan gratuito")
async def get_remaining_requests():
    """Consulta cuántos requests quedan en The Odds API (no consume quota)."""
    return await fetch_remaining_requests()


@router.post("/sync", summary="Sincronizar partidos reales desde The Odds API a la BD")
async def sync_live_data(
    sport: str = "NFL",
    db = Depends(get_db),
):
    """
    Sincroniza la cartelera completa de partidos reales y momios desde The Odds API.
    En Fase 01, la ingesta externa está restringida exclusivamente a NFL.
    """
    if sport.upper() != "NFL":
        raise HTTPException(
            status_code=400,
            detail=f"Deporte '{sport}' no permitido. Durante Fase 01, la sincronización de Odds API está restringida exclusivamente a NFL.",
        )
    from app.services.live_ingestion_service import sync_real_games
    result = await sync_real_games(sport=sport, db=db)
    return result

