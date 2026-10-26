"""
Router: Parlay Builder — /api/v1/parlay

Endpoints:
  GET  /recommended   → Obtiene los mejores micro-parlays optimizados del día (2-3 legs, cuota ~2.0, EV+)
  POST /evaluate      → Evalúa una combinada personalizada en tiempo real con Criterio de Kelly
"""
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.parlay_service import (
    evaluate_custom_parlay,
    generate_recommended_parlays,
    resolve_and_evaluate_parlay,
)
from app.api.v1.predictions import _production_model_enabled

router = APIRouter()


class CustomLeg(BaseModel):
    game_id: Optional[str] = Field(default=None, description="Identificador de evento / partido")
    side: Optional[str] = Field(default=None, description="Lado de la selección: home, away, over, under")
    market: Optional[str] = Field(default="h2h", description="Mercado: h2h (moneyline), spreads, totals")
    team_name: Optional[str] = Field(default=None, description="Nombre descriptivo del equipo o selección")
    prob_model: Optional[float] = Field(default=None, description="Probabilidad manual (solo calculadora manual)")
    odds_american: Optional[int] = Field(default=None, description="Momio americano (opcional si se resuelve del backend)")
    source: Optional[str] = Field(default="real", description="'real' para selecciones del sistema, 'manual' para calculadora")


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
    sport_val = sport.default if hasattr(sport, "default") else sport
    target_sport = (sport_val if isinstance(sport_val, str) else "NFL").strip().upper()
    bankroll_val = bankroll.default if hasattr(bankroll, "default") else bankroll
    clean_bankroll = float(bankroll_val) if isinstance(bankroll_val, (int, float)) or (isinstance(bankroll_val, str) and bankroll_val.replace(".", "", 1).isdigit()) else 1000.0
    clean_bankroll = max(10.0, min(clean_bankroll, 100000.0))
    limit_val = limit.default if hasattr(limit, "default") else limit
    clean_limit = int(limit_val) if isinstance(limit_val, int) or (isinstance(limit_val, str) and str(limit_val).isdigit()) else 4
    clean_limit = max(1, min(clean_limit, 20))

    now_utc = datetime.now(timezone.utc)
    if target_sport != "NFL":
        return {
            "status": "unavailable",
            "sport": target_sport,
            "as_of": now_utc.isoformat(),
            "total": 0,
            "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
            "parlays": [],
            "reason": f"El Parlay Builder está restringido exclusivamente a NFL en esta fase. Deporte solicitado '{target_sport}' no admitido.",
        }

    if not _production_model_enabled("NFL"):
        return {
            "status": "unavailable",
            "sport": "NFL",
            "as_of": now_utc.isoformat(),
            "total": 0,
            "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
            "parlays": [],
            "reason": "No hay un modelo aprobado para producción para NFL.",
        }

    parlays = await generate_recommended_parlays(db=db, bankroll=clean_bankroll, max_combos=clean_limit, sport="NFL")
    return {
        "status": "success",
        "sport": "NFL",
        "as_of": now_utc.isoformat(),
        "total": len(parlays),
        "target_odds": "Cuota ~2.0 (+100) con probabilidad >45% y EV+",
        "parlays": parlays,
    }


@router.post("/evaluate", summary="Evaluar un parlay personalizado en tiempo real (Backend SSOT)")
async def evaluate_parlay(
    req: EvaluateParlayRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Backend SSOT: Recibe entre 2 y 6 patas (identificadores reales o entradas manuales),
    resuelve probabilidades y cuotas observadas en DB, valida procedencia y calcula:
    - Probabilidad conjunta independiente
    - Cuota decimal y americana combinada
    - Valor Esperado (Expected Value) — nulo si faltan datos de modelo (p. ej. totales)
    - Monto sugerido por Criterio de Kelly fraccional
    - Diagnóstico de viabilidad y rechazo de eventos correlacionados
    """
    legs_dicts = [leg.model_dump() for leg in req.legs]
    result = await resolve_and_evaluate_parlay(
        legs=legs_dicts,
        db=db,
        bankroll=req.bankroll,
        kelly_fraction=req.kelly_fraction,
    )
    return result
