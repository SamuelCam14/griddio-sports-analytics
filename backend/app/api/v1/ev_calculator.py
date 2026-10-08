"""
Router: EV Calculator — /api/v1/ev

Endpoints:
  POST /calculate      → EV de una apuesta individual
  POST /parlay         → Construye el mejor micro-parlay (2-3 legs)
  POST /montecarlo     → Simula 10,000 temporadas para validar estrategia
  GET  /quick/{prob}/{odds} → EV rápido sin body
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from app.services.ev_engine import (
    BetEvent,
    calculate_ev,
    build_parlay,
    simulate_bankroll_montecarlo,
)

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class EVRequest(BaseModel):
    prob_model: float = Field(..., ge=0.0, le=1.0, description="Probabilidad del modelo (0-1)")
    odds_american: int = Field(..., description="Momio americano (ej: -130, +120)")
    bankroll: float = Field(default=1000.0, gt=0, description="Bankroll actual en $")
    kelly_fraction: float = Field(default=0.25, gt=0, le=1.0, description="Fracción de Kelly (0.25 = 25%)")

    @field_validator("odds_american")
    @classmethod
    def validate_odds(cls, v):
        if -100 < v < 100 and v != 0:
            raise ValueError("El momio americano no puede estar entre -100 y +100 (exclusivo)")
        return v


class ParlayLeg(BaseModel):
    team_name: str
    prob_model: float = Field(..., ge=0.0, le=1.0)
    odds_american: int
    sport: str = "NBA"
    game_id: Optional[int] = None


class ParlayRequest(BaseModel):
    legs: list[ParlayLeg] = Field(..., min_length=2, max_length=10)
    max_legs: int = Field(default=3, ge=2, le=3)
    min_prob_per_leg: float = Field(default=0.65, ge=0.50, le=0.95)
    bankroll: float = Field(default=1000.0, gt=0)


class MonteCarloRequest(BaseModel):
    prob: float = Field(..., ge=0.01, le=0.99)
    odds_decimal: float = Field(..., gt=1.0)
    bankroll_initial: float = Field(default=1000.0, gt=0)
    n_bets: int = Field(default=100, ge=10, le=1000)
    kelly_fraction: float = Field(default=0.05, gt=0, le=0.25)
    n_simulations: int = Field(default=10_000, ge=1_000, le=100_000)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/calculate", summary="Calcular EV de una apuesta individual")
async def calculate_single_ev(req: EVRequest):
    """
    Calcula el Expected Value de una apuesta individual.

    - **prob_model**: Probabilidad que nuestro modelo asigna al evento (ej: 0.72)
    - **odds_american**: Momio americano del bookmaker (ej: -130 para favorito)
    - **bankroll**: Tu bankroll actual para calcular monto Kelly sugerido

    Retorna EV en $, edge %, nivel de confianza y monto Kelly recomendado.
    """
    result = calculate_ev(
        prob_model=req.prob_model,
        odds_american=req.odds_american,
        bankroll=req.bankroll,
        kelly_fraction=req.kelly_fraction,
    )
    return {
        "ev_dolares":       result.ev_dollars,
        "ev_porcentaje":    f"{result.ev_pct:.2%}",
        "prob_modelo":      result.prob_model,
        "prob_implicita":   result.prob_implied,
        "ventaja":          f"{result.edge_pct:.2%}",
        "pago_neto":        result.payout_net,
        "kelly_fraccion":   result.kelly_fraction,
        "kelly_monto":      result.kelly_amount,
        "tiene_ventaja":    result.has_edge,
        "tier":             result.tier,
        "advertencias":     result.warnings,
        "recomendacion":    "APOSTAR" if result.tier in ("PREMIUM", "VALUE") else "PASAR",
    }


@router.get("/quick/{prob}/{odds}", summary="Cálculo rápido de EV")
async def quick_ev(
    prob: float,
    odds: int,
    bankroll: float = 1000.0,
):
    """
    Versión rápida sin body. Útil para consultas desde frontend.
    Ejemplo: GET /api/v1/ev/quick/0.72/-130?bankroll=500
    """
    result = calculate_ev(prob_model=prob, odds_american=odds, bankroll=bankroll)
    return {
        "ev_dolares":    result.ev_dollars,
        "tier":          result.tier,
        "tiene_ventaja": result.has_edge,
        "kelly_monto":   result.kelly_amount,
    }


@router.post("/parlay", summary="Construir micro-parlay con ventaja matemática")
async def build_ev_parlay(req: ParlayRequest):
    """
    Construye el mejor micro-parlay (2-3 legs) usando solo eventos con EV+.

    La estrategia:
    1. Filtra legs con prob ≥ min_prob_per_leg
    2. Selecciona solo los que tienen EV+ individual
    3. Combina los N mejores por edge descendente
    4. Verifica EV combinado > 0

    **Regla de oro:** Solo parlays donde CADA leg tiene ventaja matemática individual.
    """
    events = [
        BetEvent(
            team_name=leg.team_name,
            prob_model=leg.prob_model,
            odds_american=leg.odds_american,
            sport=leg.sport,
            game_id=leg.game_id,
        )
        for leg in req.legs
    ]

    result = build_parlay(
        events=events,
        max_legs=req.max_legs,
        min_prob_per_leg=req.min_prob_per_leg,
        bankroll=req.bankroll,
    )

    return {
        "viable":           result.is_viable,
        "razon":            result.reason,
        "n_legs":           len(result.events),
        "eventos":          [
            {
                "equipo":   e.team_name,
                "prob":     e.prob_model,
                "momio":    e.odds_american,
            }
            for e in result.events
        ],
        "prob_combinada":   result.prob_combined,
        "momio_decimal":    result.odds_decimal,
        "momio_americano":  result.odds_american,
        "ev_dolares":       result.ev_dollars,
        "kelly_fraccion":   result.kelly_fraction,
        "recomendacion":    f"Apostar ${result.kelly_fraction * req.bankroll:.2f}" if result.is_viable else "No hay parlay viable hoy",
    }


@router.post("/montecarlo", summary="Simular temporada completa (10,000 escenarios)")
async def run_montecarlo(req: MonteCarloRequest):
    """
    Simula tu estrategia de apuestas en 10,000 temporadas ficticias.

    Útil para:
    - Validar una estrategia ANTES de apostar dinero real
    - Estimar la probabilidad de ruina
    - Comparar Kelly al 5% vs 10% vs 25%

    Ejemplo: prob=0.72, odds=1.77 (-130), bankroll=$500, 100 bets, kelly=5%
    """
    result = simulate_bankroll_montecarlo(
        prob=req.prob,
        odds_decimal=req.odds_decimal,
        bankroll_initial=req.bankroll_initial,
        n_bets=req.n_bets,
        kelly_fraction=req.kelly_fraction,
        n_simulations=req.n_simulations,
    )

    return {
        **result,
        "interpretacion": {
            "prob_ruina":      f"{result['prob_ruina']:.1%} de probabilidad de perder el 90% del bankroll",
            "roi_esperado":    f"{result['roi_esperado']:.1%} ROI promedio sobre {req.n_bets} apuestas",
            "rango_probable":  f"${result['percentil_25']:,.2f} – ${result['percentil_75']:,.2f} (50% de los casos)",
            "evaluacion":      (
                "ESTRATEGIA SOLIDA" if result["prob_ruina"] < 0.10 and result["roi_esperado"] > 0.05
                else "RIESGO MODERADO" if result["prob_ruina"] < 0.20
                else "ESTRATEGIA PELIGROSA"
            ),
        },
    }
