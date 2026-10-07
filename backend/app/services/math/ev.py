"""
Módulo de Cálculo de Valor Esperado (Expected Value - EV) y Parlays.
Determina si una apuesta individual o combinada presenta ventaja matemática (+EV).
"""
from dataclasses import dataclass, field
from typing import List, Optional

from app.services.math.odds_converter import (
    american_to_decimal,
    decimal_to_american,
    american_to_implied_prob,
)
from app.services.math.kelly import (
    calculate_kelly_fraction,
    calculate_kelly_amount,
)


@dataclass
class BetEvent:
    """Evento individual analizado para EV."""
    prob_model: float
    odds_american: int
    odds_decimal: float = 0.0
    implied_prob: float = 0.0
    edge_pct: float = 0.0
    ev_dollars: float = 0.0
    ev_pct: str = ""
    net_payout: float = 0.0
    kelly_fraction: float = 0.0
    kelly_amount: float = 0.0
    has_edge: bool = False
    tier: str = "SKIP"
    team_name: Optional[str] = None
    sport: str = "NBA"
    game_id: Optional[int] = None
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Auto-calcular si no se especificaron métricas derivadas
        if self.odds_decimal == 0.0:
            self.odds_decimal = american_to_decimal(self.odds_american)
            self.implied_prob = american_to_implied_prob(self.odds_american)
            self.net_payout = self.odds_decimal - 1.0
            self.edge_pct = self.prob_model - self.implied_prob
            self.ev_dollars = round((self.prob_model * self.net_payout * 100.0) - ((1.0 - self.prob_model) * 100.0), 2)
            self.ev_pct = f"{self.ev_dollars:+.2f}%"
            self.has_edge = self.edge_pct >= 0.03 and self.ev_dollars > 0
            self.tier = "PREMIUM" if self.has_edge and self.edge_pct >= 0.08 else "VALUE" if self.has_edge else "SKIP"


@dataclass
class ParlayResult:
    """Resultado estructurado de una apuesta combinada (Parlay)."""
    events: List[BetEvent]
    combined_prob: float
    combined_decimal_odds: float
    combined_american_odds: int
    combined_ev: float
    kelly_fraction: float
    kelly_amount: float
    is_viable: bool
    reason: str


def calculate_ev(
    prob_model: float,
    odds_american: int,
    bankroll: float = 1000.0,
    min_edge_threshold: float = 0.03,
) -> BetEvent:
    """Calcula el Valor Esperado (EV) de una apuesta por cada $100 arriesgados."""
    odds_decimal = american_to_decimal(odds_american)
    implied_prob = american_to_implied_prob(odds_american)
    net_payout = odds_decimal - 1.0

    p_win = prob_model
    p_loss = 1.0 - p_win

    ev_dollars = (p_win * net_payout * 100.0) - (p_loss * 100.0)
    edge_pct = p_win - implied_prob

    kelly_frac = calculate_kelly_fraction(p_win, odds_decimal, fraction_multiplier=0.25, max_cap=0.05)
    kelly_amt = calculate_kelly_amount(bankroll, kelly_frac)

    has_edge = edge_pct >= min_edge_threshold and ev_dollars > 0

    if has_edge and edge_pct >= 0.08:
        tier = "PREMIUM"
    elif has_edge and edge_pct >= 0.03:
        tier = "VALUE"
    else:
        tier = "SKIP"

    warnings = []
    if prob_model > 0.85:
        warnings.append("⚠️ Probabilidad inusualmente alta (>85%). Verificar posible sobreajuste.")
    if abs(odds_american) >= 300:
        warnings.append("⚠️ Momio extremo. Varianza elevada.")

    return BetEvent(
        prob_model=round(prob_model, 4),
        odds_american=odds_american,
        odds_decimal=round(odds_decimal, 4),
        implied_prob=round(implied_prob, 4),
        edge_pct=round(edge_pct, 4),
        ev_dollars=round(ev_dollars, 2),
        ev_pct=f"{ev_dollars:+.2f}%",
        net_payout=round(net_payout, 4),
        kelly_fraction=round(kelly_frac, 4),
        kelly_amount=kelly_amt,
        has_edge=has_edge,
        tier=tier,
        warnings=warnings,
    )


def build_parlay(
    events: List[BetEvent],
    max_legs: int = 3,
    min_prob_per_leg: float = 0.65,
    bankroll: float = 1000.0,
) -> ParlayResult:
    """Construye un micro-parlay combinando eventos independientes con ventaja individual."""
    valid_legs = [e for e in events if e.prob_model >= min_prob_per_leg and e.has_edge]

    if len(valid_legs) < 2:
        return ParlayResult(
            events=[],
            combined_prob=0.0,
            combined_decimal_odds=0.0,
            combined_american_odds=0,
            combined_ev=0.0,
            kelly_fraction=0.0,
            kelly_amount=0.0,
            is_viable=False,
            reason=f"Solo se encontraron {len(valid_legs)} eventos con EV+ y prob ≥ {min_prob_per_leg:.0%}. Mínimo 2.",
        )

    # Ordenar por edge descendente y tomar hasta max_legs
    selected = sorted(valid_legs, key=lambda x: x.edge_pct, reverse=True)[:max_legs]

    comb_prob = 1.0
    comb_dec = 1.0
    for e in selected:
        comb_prob *= e.prob_model
        comb_dec *= e.odds_decimal

    net_payout = comb_dec - 1.0
    comb_ev = (comb_prob * net_payout * 100.0) - ((1.0 - comb_prob) * 100.0)

    kelly_frac = calculate_kelly_fraction(comb_prob, comb_dec, fraction_multiplier=0.20, max_cap=0.05)
    kelly_amt = calculate_kelly_amount(bankroll, kelly_frac)

    return ParlayResult(
        events=selected,
        combined_prob=round(comb_prob, 4),
        combined_decimal_odds=round(comb_dec, 4),
        combined_american_odds=decimal_to_american(comb_dec),
        combined_ev=round(comb_ev, 2),
        kelly_fraction=round(kelly_frac, 4),
        kelly_amount=kelly_amt,
        is_viable=comb_ev > 0,
        reason=f"Micro-parlay de {len(selected)} legs con EV +${comb_ev:.2f} por cada $100.",
    )
