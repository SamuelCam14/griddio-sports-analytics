"""
Router: Sports Card Analytics — /api/v1/cards

Endpoints:
  GET  /catalog       → Catálogo de tarjetas monitoreadas (NIL y Rookie Cards NBA)
  GET  /arbitrage     → Alertas de arbitraje de mercado (EV+ en reventa)
  GET  /{card_id}/trajectory → Proyección de serie temporal Prophet a 90 días (curva NIL)
  POST /evaluate      → Calculadora hedónica interactiva de Fair Market Value
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.card import SportsCard
from app.services.card_analytics_service import (
    calculate_fair_market_value,
    analyze_arbitrage_opportunity,
    project_depreciation_trajectory_prophet,
)

router = APIRouter()


class CardEvaluationRequest(BaseModel):
    player_name: str = Field(default="Victor Wembanyama", description="Nombre del jugador o prospecto")
    card_type: str = Field(default="Rookie Card", description="'Rookie Card', 'NIL', o 'Veteran'")
    grade: str = Field(default="PSA 10", description="'Raw', 'PSA 9', 'PSA 10', 'BGS 9.5'")
    parallel_name: str = Field(default="Silver", description="'Base', 'Silver', 'Gold /10', etc.")
    autograph_type: str = Field(default="None", description="'None', 'Sticker', 'On-Card'")
    listing_price: Optional[float] = Field(default=None, description="Precio observado en eBay o marketplace en $")
    market_size_score: float = Field(default=7.0, ge=1.0, le=10.0, description="Score de mercado de la franquicia")
    print_run: Optional[int] = Field(default=None, description="Numeración máxima si aplica (ej. 25)")


@router.get("/catalog", summary="Catálogo de cartas monitoreadas con métricas hedónicas")
async def get_cards_catalog(
    card_type: str = Query(default="all", description="all | NIL | Rookie Card"),
    sport: str = Query(default="NBA"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el listado de tarjetas monitoreadas con su precio actual,
    valor justo estimado (FMV) y ratio de oportunidad.
    """
    raise HTTPException(
        status_code=503,
        detail="Catálogo deshabilitado: no hay una fuente conectada que verifique listados y precios observados.",
    )
    stmt = select(SportsCard)
    if card_type != "all":
        stmt = stmt.where(SportsCard.card_type == card_type)
    stmt = stmt.order_by(SportsCard.ev_percentage.desc())

    res = await db.execute(stmt)
    cards = res.scalars().all()

    return {
        "status": "success",
        "total": len(cards),
        "cards": [
            {
                "id": c.id,
                "player_name": c.player_name,
                "sport": c.sport,
                "season": c.season,
                "manufacturer": c.manufacturer,
                "brand": c.brand,
                "card_type": c.card_type,
                "parallel_name": c.parallel_name,
                "print_run": c.print_run,
                "has_autograph": c.has_autograph,
                "grade": c.grade,
                "current_market_price": c.current_market_price,
                "fair_market_value": c.fair_market_value,
                "ev_opportunity": c.ev_opportunity,
                "ev_percentage": c.ev_percentage,
                "liquidity_score": c.liquidity_score,
                "image_url": c.image_url,
            }
            for c in cards
        ],
    }


@router.get("/arbitrage", summary="Alertas de arbitraje (Cartas subvaloradas EV+)")
async def get_arbitrage_opportunities(db: AsyncSession = Depends(get_db)):
    """
    Identifica anomalías de precio donde el precio de venta en marketplace
    es significativamente inferior al Fair Market Value neto de comisiones.
    """
    raise HTTPException(
        status_code=503,
        detail="Arbitraje deshabilitado: no existe feed verificado de transacciones/listados ni valoración validada.",
    )
    stmt = select(SportsCard).where(SportsCard.ev_opportunity == True).order_by(SportsCard.ev_percentage.desc())
    res = await db.execute(stmt)
    cards = res.scalars().all()

    return {
        "status": "success",
        "total_opportunities": len(cards),
        "arbitrage_cards": [
            {
                "id": c.id,
                "player_name": c.player_name,
                "brand": f"{c.manufacturer} {c.brand}",
                "card_type": c.card_type,
                "grade": c.grade,
                "parallel": c.parallel_name,
                "listing_price": c.current_market_price,
                "fair_market_value": c.fair_market_value,
                "expected_roi": f"+{c.ev_percentage:.1f}%",
                "profit_dollars": round(c.fair_market_value * 0.87 - c.current_market_price, 2),
                "image_url": c.image_url,
            }
            for c in cards
        ]
    }


@router.get("/{card_id}/trajectory", summary="Curva de depreciación proyectada a 90 días con Prophet")
async def get_card_trajectory(
    card_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Modela y proyecta la trayectoria de precios de la carta.
    Especialmente relevante para contratos NIL, visualizando el punto de inflexión
    cuando salen los productos oficiales con uniforme de la NBA.
    """
    raise HTTPException(
        status_code=503,
        detail="Pronóstico deshabilitado: faltan observaciones históricas reales de precio para esta carta.",
    )
    stmt = select(SportsCard).where(SportsCard.id == card_id)
    res = await db.execute(stmt)
    card = res.scalar_one_or_none()

    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta coleccionable no encontrada.")

    trajectory = project_depreciation_trajectory_prophet(
        initial_price=card.current_market_price,
        card_type=card.card_type,
        days_forecast=90,
    )

    return {
        "card_id": card.id,
        "player_name": card.player_name,
        "card_type": card.card_type,
        "current_price": card.current_market_price,
        "trajectory_points": trajectory,
        "model_used": "Prophet Time-Series (o Parametric NIL Decay con bandas 95% CI)",
    }


@router.post("/evaluate", summary="Calculadora hedónica de valoración en tiempo real")
async def evaluate_card_valuation(req: CardEvaluationRequest):
    """
    Calcula el Fair Market Value de cualquier carta a partir de sus atributos,
    y analiza si un precio de compra dado ofrece margen de arbitraje positivo.
    """
    raise HTTPException(
        status_code=503,
        detail="Valoración deshabilitada: no hay precios de venta observados ni modelo de mercado validado.",
    )
    valuation = calculate_fair_market_value(
        player_name=req.player_name,
        card_type=req.card_type,
        grade=req.grade,
        parallel_name=req.parallel_name,
        autograph_type=req.autograph_type,
        market_size_score=req.market_size_score,
        print_run=req.print_run,
    )

    arbitrage_analysis = None
    if req.listing_price is not None:
        arbitrage_analysis = analyze_arbitrage_opportunity(
            listing_price=req.listing_price,
            fair_market_value=valuation["fair_market_value"],
        )

    return {
        "player_name": req.player_name,
        "card_type": req.card_type,
        "grade": req.grade,
        "parallel_name": req.parallel_name,
        "valuation": valuation,
        "arbitrage_analysis": arbitrage_analysis,
    }
