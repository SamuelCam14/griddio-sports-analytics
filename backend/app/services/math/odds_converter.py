"""
Módulo de Conversión de Momios y Probabilidades (Odds & Probability Math).
Funciones puras para transformar entre momios americanos, decimales,
probabilidades implícitas y eliminación de vig (margen de la casa de apuestas).
"""
import math
from typing import Tuple


def american_to_decimal(odds: int) -> float:
    """
    Convierte momio americano a cuota decimal.
    Ejemplos:
      +150 -> 2.50
      -150 -> 1.667
    """
    if odds is None or odds == 0 or not math.isfinite(odds) or (-100 < odds < 100):
        return 1.0
    if odds > 0:
        return 1.0 + (odds / 100.0)
    elif odds < 0:
        return 1.0 + (100.0 / abs(odds))
    return 1.0


def decimal_to_american(decimal_odds: float) -> int:
    """
    Convierte cuota decimal a momio americano entero.
    Ejemplos:
      2.50  -> +150
      1.667 -> -150
    """
    if decimal_odds is None or not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
        return 0
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    elif decimal_odds > 1.0:
        return int(round(-100 / (decimal_odds - 1.0)))
    return 0


def american_to_implied_prob(odds: int) -> float:
    """
    Calcula la probabilidad implícita con vig incluido a partir del momio americano.
    Fórmula:
      Si odds > 0: 100 / (odds + 100)
      Si odds < 0: |odds| / (|odds| + 100)
    """
    if odds is None or odds == 0 or not math.isfinite(odds) or (-100 < odds < 100):
        return 0.5
    if odds > 0:
        return 100.0 / (odds + 100.0)
    elif odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 0.5


def remove_vig(p_home_raw: float, p_away_raw: float) -> Tuple[float, float, float]:
    """
    Elimina el sobreprecio (vig / juice) de las probabilidades implícitas de dos selecciones.
    Retorna:
      (prob_home_justa, prob_away_justa, vig_porcentaje)
    """
    if (
        p_home_raw is None
        or p_away_raw is None
        or not math.isfinite(p_home_raw)
        or not math.isfinite(p_away_raw)
        or not (0.0 < p_home_raw < 1.0)
        or not (0.0 < p_away_raw < 1.0)
    ):
        return 0.5, 0.5, 0.0
    total_implied = p_home_raw + p_away_raw
    if total_implied <= 0 or not math.isfinite(total_implied):
        return 0.5, 0.5, 0.0
    vig = total_implied - 1.0
    p_home_fair = p_home_raw / total_implied
    p_away_fair = p_away_raw / total_implied
    return p_home_fair, p_away_fair, max(0.0, vig)
