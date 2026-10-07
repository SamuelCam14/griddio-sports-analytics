"""
Módulo de Matemáticas Cuantitativas y Gestión de Riesgo (Math Services).
Contiene funciones puras e independientes de infraestructura.
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
    calculate_ev,
)
from app.services.math.monte_carlo import (
    run_monte_carlo_simulation,
)

__all__ = [
    "american_to_decimal",
    "decimal_to_american",
    "american_to_implied_prob",
    "remove_vig",
    "calculate_kelly_fraction",
    "calculate_kelly_amount",
    "BetEvent",
    "calculate_ev",
    "run_monte_carlo_simulation",
]
