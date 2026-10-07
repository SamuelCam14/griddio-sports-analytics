"""
Motor Matemático y de Riesgo — Proxy de compatibilidad y fachada unificada.
Delega a los submódulos especializados en app.services.math.*
"""
from app.services.math.odds_converter import (
    american_to_decimal,
    decimal_to_american,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import (
    calculate_kelly_fraction,
    calculate_kelly_amount,
)
from app.services.math.ev import (
    BetEvent,
    ParlayResult,
    calculate_ev,
    build_parlay,
)
from app.services.math.monte_carlo import (
    run_monte_carlo_simulation,
)

# Alias para compatibilidad con código existente
simulate_bankroll_montecarlo = run_monte_carlo_simulation

__all__ = [
    "american_to_decimal",
    "decimal_to_american",
    "american_to_implied_prob",
    "remove_vig",
    "calculate_kelly_fraction",
    "calculate_kelly_amount",
    "BetEvent",
    "ParlayResult",
    "calculate_ev",
    "build_parlay",
    "run_monte_carlo_simulation",
    "simulate_bankroll_montecarlo",
]
