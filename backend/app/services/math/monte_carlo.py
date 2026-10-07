"""
Módulo de Simulación de Monte Carlo para Gestión de Banca.
Simula miles de trayectorias estocásticas para evaluar el riesgo de ruina y el ROI esperado.
"""
from typing import Dict, Any, List
import numpy as np


def run_monte_carlo_simulation(
    prob_win: float,
    odds_decimal: float,
    bankroll_initial: float = 1000.0,
    n_bets: int = 100,
    kelly_fraction: float = 0.25,
    n_simulations: int = 1000,
    ruin_threshold: float = 0.10,
) -> Dict[str, Any]:
    """
    Simula trayectorias de apuestas usando Criterio de Kelly.
    
    Args:
        prob_win: Probabilidad real de ganar cada apuesta independiente.
        odds_decimal: Cuota decimal de pago.
        bankroll_initial: Capital inicial ($).
        n_bets: Número de apuestas secuenciales por simulación.
        kelly_fraction: Multiplicador del Criterio de Kelly (default: 0.25).
        n_simulations: Número de réplicas de Monte Carlo.
        ruin_threshold: Umbral de quiebra (default: caer por debajo del 10% inicial).
    """
    b = odds_decimal - 1.0
    q = 1.0 - prob_win

    # Kelly continuo
    kelly_full = (b * prob_win - q) / b if b > 0 else 0.0
    kelly_applied = max(0.005, min(kelly_full * kelly_fraction, 0.05))

    final_bankrolls: List[float] = []
    ruin_count = 0
    ruin_level = bankroll_initial * ruin_threshold

    for _ in range(n_simulations):
        br = bankroll_initial
        # Generar resultados binomiales vectorizados para n_bets
        outcomes = np.random.random(n_bets) < prob_win

        for won in outcomes:
            bet_size = br * kelly_applied
            if won:
                br += bet_size * b
            else:
                br -= bet_size

            if br <= ruin_level:
                ruin_count += 1
                break

        final_bankrolls.append(max(0.0, br))

    arr = np.array(final_bankrolls)
    prob_ruin = ruin_count / n_simulations
    median_final = float(np.median(arr))
    p10 = float(np.percentile(arr, 10))
    p90 = float(np.percentile(arr, 90))
    expected_roi = ((median_final - bankroll_initial) / bankroll_initial) * 100.0

    return {
        "bankroll_inicial": bankroll_initial,
        "n_simulaciones": n_simulations,
        "n_apuestas": n_bets,
        "kelly_fraccion_usada": round(kelly_applied, 4),
        "mediana_final": round(median_final, 2),
        "percentil_10": round(p10, 2),
        "percentil_90": round(p90, 2),
        "prob_ruina": round(prob_ruin, 4),
        "roi_esperado": round(expected_roi, 2),
        "interpretacion": {
            "prob_ruina": f"{prob_ruin:.1%}",
            "roi_esperado": f"{expected_roi:+.1f}%",
            "rango_probable": f"${p10:,.0f} — ${p90:,.0f}",
            "evaluacion": (
                "Excelente perfil de riesgo (<2% ruina)" if prob_ruin < 0.02
                else "Riesgo moderado" if prob_ruin < 0.08
                else "Alto riesgo de quiebra — reducir fracción de Kelly"
            ),
        },
    }
