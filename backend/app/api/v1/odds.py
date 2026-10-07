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
from app.services.odds_service import fetch_odds, fetch_remaining_requests

router = APIRouter()


@router.get("/nba", summary="Momios actuales NBA")
async def get_nba_odds():
    """
    Obtiene los momios actuales de todos los partidos NBA del día.

    - Hace line shopping entre DraftKings, FanDuel, BetMGM y Caesars
    - Elimina el vig para mostrar probabilidades implícitas reales
    - Requiere ODDS_API_KEY configurado en .env

    Plan gratuito: 500 requests/mes. Se cachea cada 15 minutos.
    """
    data = await fetch_odds(sport="NBA")
    if not data:
        raise HTTPException(
            status_code=503,
            detail="No se pudieron obtener momios. Verifica que ODDS_API_KEY está configurado."
        )
    return {"sport": "NBA", "total_games": len(data), "games": data}


@router.get("/nfl", summary="Momios actuales NFL")
async def get_nfl_odds():
    data = await fetch_odds(sport="NFL")
    if not data:
        raise HTTPException(status_code=503, detail="No se pudieron obtener momios NFL.")
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
    Actualiza la base de datos PostgreSQL con datos 100% verificados.
    """
    from app.services.live_ingestion_service import sync_real_games
    result = await sync_real_games(sport=sport, db=db)
    return result

