"""
Módulo de Servicios de Sports Card Analytics.
"""
from app.services.cards.hedonic_pricing import calculate_fair_market_value
from app.services.cards.arbitrage_detector import analyze_arbitrage_opportunity
from app.services.cards.prophet_forecaster import project_depreciation_trajectory_prophet
from app.services.cards.multipliers import (
    UNIFORM_MULTIPLIERS,
    GRADE_MULTIPLIERS,
    PARALLEL_MULTIPLIERS,
    AUTOGRAPH_MULTIPLIERS,
    PLAYER_BASE_PEDIGREE,
)

__all__ = [
    "calculate_fair_market_value",
    "analyze_arbitrage_opportunity",
    "project_depreciation_trajectory_prophet",
    "UNIFORM_MULTIPLIERS",
    "GRADE_MULTIPLIERS",
    "PARALLEL_MULTIPLIERS",
    "AUTOGRAPH_MULTIPLIERS",
    "PLAYER_BASE_PEDIGREE",
]
