"""
Módulo de Gestión de Riesgo: Criterio de Kelly (Kelly Criterion).
Calcula la fracción óptima del bankroll a arriesgar dada una ventaja matemática (Edge).
"""


def calculate_kelly_fraction(
    prob_win: float,
    odds_decimal: float,
    fraction_multiplier: float = 0.25,
    max_cap: float = 0.05,
) -> float:
    """
    Calcula la fracción recomendada de apuesta según el Criterio de Kelly Fraccional.
    
    Fórmula de Kelly:
      f* = (b * p - q) / b
      donde:
        b = cuota neta ganada = odds_decimal - 1.0
        p = probabilidad del modelo
        q = 1 - p (probabilidad de perder)
    
    Args:
        prob_win: Probabilidad calibrada estimada por el modelo (0 a 1).
        odds_decimal: Cuota decimal de la casa de apuestas (ej. 1.909).
        fraction_multiplier: Escalar conservador (ej. 0.25 = Cuarto de Kelly).
        max_cap: Límite estricto de seguridad para evitar quiebra (default: 5% del bankroll).
        
    Returns:
        Fracción entre 0.0 y max_cap.
    """
    b = odds_decimal - 1.0
    if b <= 0 or prob_win <= 0 or prob_win >= 1.0:
        return 0.0

    q = 1.0 - prob_win
    kelly_full = (b * prob_win - q) / b

    if kelly_full <= 0:
        return 0.0

    # Aplicar fracción conservadora y tope de seguridad
    kelly_scaled = kelly_full * fraction_multiplier
    return round(min(kelly_scaled, max_cap), 4)


def calculate_kelly_amount(
    bankroll: float,
    kelly_fraction: float,
) -> float:
    """Calcula el monto monetario a apostar dado un bankroll."""
    return round(bankroll * kelly_fraction, 2)
