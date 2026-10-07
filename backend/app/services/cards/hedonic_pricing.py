"""
Módulo de Valoración Hedónica de Sports Cards (Fair Market Value).
Calcula el valor intrínseco teórico en función de los multiplicadores paramétricos.
"""
from typing import Dict, Any, Optional

from app.services.cards.multipliers import (
    UNIFORM_MULTIPLIERS,
    GRADE_MULTIPLIERS,
    PARALLEL_MULTIPLIERS,
    AUTOGRAPH_MULTIPLIERS,
    PLAYER_BASE_PEDIGREE,
)


def calculate_fair_market_value(
    player_name: str,
    card_type: str = "Rookie Card",
    grade: str = "Raw",
    parallel_name: str = "Base",
    autograph_type: str = "None",
    market_size_score: float = 7.0,
    print_run: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Calcula el Valor Justo de Mercado (FMV) paramétrico basado en características.
    """
    base_price = PLAYER_BASE_PEDIGREE.get(player_name, PLAYER_BASE_PEDIGREE["Default Rookie"])

    # 1. Multiplicador de uniforme (Efecto Uniforme NIL)
    m_uniform = UNIFORM_MULTIPLIERS.get(card_type, 0.50 if "NIL" in card_type else 1.00)

    # 2. Multiplicador de grado
    m_grade = GRADE_MULTIPLIERS.get(grade, 1.00)

    # 3. Multiplicador de paralelo / escasez
    m_parallel = PARALLEL_MULTIPLIERS.get(parallel_name, 1.00)
    if print_run is not None:
        if print_run <= 1:
            m_parallel = max(m_parallel, 65.0)
        elif print_run <= 10:
            m_parallel = max(m_parallel, 18.0)
        elif print_run <= 25:
            m_parallel = max(m_parallel, 9.5)
        elif print_run <= 99:
            m_parallel = max(m_parallel, 5.0)

    # 4. Multiplicador de autógrafo
    m_auto = AUTOGRAPH_MULTIPLIERS.get(autograph_type, 1.00)

    # 5. Prima de tamaño de mercado de la franquicia
    m_market = 1.0 + (market_size_score - 5.5) * 0.04

    # Valor Justo Teórico
    fmv = base_price * m_uniform * m_grade * m_parallel * m_auto * m_market

    return {
        "fair_market_value": round(fmv, 2),
        "base_pedigree_price": base_price,
        "multipliers": {
            "uniform_effect": m_uniform,
            "grade": m_grade,
            "parallel_rarity": m_parallel,
            "autograph": m_auto,
            "franchise_market": round(m_market, 3),
        },
        "nil_discount_applied": card_type == "NIL",
    }
