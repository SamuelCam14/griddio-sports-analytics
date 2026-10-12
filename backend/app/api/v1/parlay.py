"""
Router: Parlay Builder — /api/v1/parlay

Endpoints:
  GET  /recommended   → Obtiene los mejores micro-parlays optimizados del día (2-3 legs, cuota ~2.0, EV+)
  POST /evaluate      → Evalúa una combinada personalizada en tiempo real con Criterio de Kelly
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.parlay_service import (
    evaluate_custom_parlay,
    generate_recommended_parlays,
)
from app.api.v1.predictions import _production_model_enabled

router = APIRouter()


class CustomLeg(BaseModel):
    team_name: str = Field(..., description="Nombre del equipo o evento seleccionado")
    prob_model: float = Field(..., ge=0.05, le=0.99, description="Probabilidad estimada por el modelo (0 a 1)")
    odds_american: int = Field(..., description="Momio americano de la casa de apuestas (ej. -130)")


class EvaluateParlayRequest(BaseModel):
    legs: List[CustomLeg] = Field(..., min_length=2, max_length=6, description="Lista de 2 a 6 eventos a combinar")
    bankroll: float = Field(default=1000.0, gt=0, description="Bankroll del usuario para calcular Kelly sugerido")
    kelly_fraction: float = Field(default=0.25, ge=0.05, le=0.5, description="Fracción del Criterio de Kelly a utilizar")


@router.get("/recommended", summary="Micro-parlays recomendados del día con ventaja matemática")
async def get_recommended_parlays(
    sport: str = Query(default="NFL", description="Filtrar por deporte (exclusivamente NFL admitido en esta fase)"),
    bankroll: float = Query(default=1000.0, gt=0, description="Banca actual para cálculo de apuestas"),
    limit: int = Query(default=4, ge=1, le=10, description="Cantidad máxima de parlays a retornar"),
    db: AsyncSession = Depends(get_db),
):
    """
    Escanea los partidos con momios del día, evalúa todas las permutaciones de 2 y 3 eventos
    y selecciona las combinaciones que cumplen la 'Regla de Oro':
    - Probabilidad individual alta (>65%)
    - Cuota combinada cercana a 2.0 (+100 americano)
    - EV combinado positivo (> $0 por cada $100 apostados)
    - Criterio de Kelly fraccional para gestión de riesgo
    Opera exclusivamente sobre NFL en esta fase de ejecución.
    """
    target_sport = (sport if isinstance(sport, str) else "NFL").upper()
    clean_bankroll = bankroll if isinstance(bankroll, (int, float)) else 1000.0
    clean_limit = limit if isinstance(limit, int) else 4

    if target_sport != "NFL":
        return {
            "status": "unavailable",
            "sport": target_sport,
            "total": 0,
            "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
            "parlays": [],
            "reason": f"El Parlay Builder está restringido exclusivamente a NFL en esta fase. Deporte solicitado '{target_sport}' no admitido.",
        }

    if not _production_model_enabled("NFL"):
        return {
            "status": "unavailable",
            "sport": "NFL",
            "total": 0,
            "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
            "parlays": [],
            "reason": "No hay un modelo aprobado para producción para NFL.",
        }

    parlays = await generate_recommended_parlays(db=db, bankroll=clean_bankroll, max_combos=clean_limit, sport="NFL")
    return {
        "status": "success",
        "total": len(parlays),
        "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
        "parlays": parlays,
    }


@router.post("/evaluate", summary="Evaluar un parlay personalizado en tiempo real")
async def evaluate_parlay(req: EvaluateParlayRequest):
    """
    Recibe entre 2 y 6 patas personalizadas y calcula:
    - Probabilidad conjunta independiente
    - Cuota decimal y americana combinada
    - Valor Esperado (Expected Value)
    - Monto sugerido por Criterio de Kelly fraccional
    - Diagnóstico de viabilidad matemática
    """
    legs_dicts = [leg.model_dump() for leg in req.legs]
    result = evaluate_custom_parlay(
        legs=legs_dicts,
        bankroll=req.bankroll,
        kelly_fraction=req.kelly_fraction,
    )
    return result
