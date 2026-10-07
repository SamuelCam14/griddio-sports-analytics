"""
Pruebas unitarias para módulos matemáticos atómicos (SRP):
- odds_converter: conversiones y eliminación de margen (vig).
- kelly: criterio de Kelly fraccional y cap de seguridad.
- ev: valor esperado y micro-parlays.
- monte_carlo: simulación estocástica y cálculo de probabilidad de ruina.
"""
import pytest
from app.services.math.odds_converter import (
    american_to_decimal,
    decimal_to_american,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import calculate_kelly_fraction, calculate_kelly_amount
from app.services.math.ev import calculate_ev, build_parlay, BetEvent
from app.services.math.monte_carlo import run_monte_carlo_simulation


class TestOddsConverter:
    def test_american_to_decimal_underdog(self):
        assert pytest.approx(american_to_decimal(150), 0.001) == 2.50

    def test_american_to_decimal_favorite(self):
        assert pytest.approx(american_to_decimal(-200), 0.001) == 1.50

    def test_decimal_to_american_underdog(self):
        assert decimal_to_american(2.50) == 150

    def test_decimal_to_american_favorite(self):
        assert decimal_to_american(1.50) == -200

    def test_implied_probability(self):
        # +100 americano -> 50%
        assert pytest.approx(american_to_implied_prob(100), 0.001) == 0.50
        # -150 americano -> 150 / 250 = 60%
        assert pytest.approx(american_to_implied_prob(-150), 0.001) == 0.60

    def test_remove_vig(self):
        # Dos eventos equilibrados con margen de 4.76% (implied 52.38% cada uno)
        p_home_raw = 0.5238
        p_away_raw = 0.5238
        fair_h, fair_a, vig = remove_vig(p_home_raw, p_away_raw)
        assert pytest.approx(fair_h, 0.001) == 0.50
        assert pytest.approx(fair_a, 0.001) == 0.50
        assert pytest.approx(fair_h + fair_a, 0.001) == 1.0


class TestKelly:
    def test_kelly_fraction_positive_edge(self):
        # b = 2.0 (+100), p = 0.60 -> Full Kelly = (1 * 0.6 - 0.4) / 1 = 0.20
        # Quarter Kelly = 0.05
        k_frac = calculate_kelly_fraction(0.60, 2.0, fraction_multiplier=0.25, max_cap=0.05)
        assert pytest.approx(k_frac, 0.001) == 0.05

    def test_kelly_fraction_negative_edge(self):
        # b = 2.0, p = 0.45 -> Kelly negativo debe devolver 0.0
        k_frac = calculate_kelly_fraction(0.45, 2.0)
        assert k_frac == 0.0

    def test_kelly_safety_cap(self):
        # Con edge gigantesco (p = 0.99), el cap de seguridad (0.05) debe limitar la fracción
        k_frac = calculate_kelly_fraction(0.99, 2.0, fraction_multiplier=1.0, max_cap=0.05)
        assert k_frac == 0.05
        stake = calculate_kelly_amount(1000.0, k_frac)
        assert stake == 50.0  # 5% de 1000


class TestExpectedValue:
    def test_calculate_ev_positive(self):
        # p = 0.70, momio = -120 (decimal 1.833) -> Gran ventaja
        res = calculate_ev(prob_model=0.70, odds_american=-120, bankroll=1000.0)
        assert res.has_edge is True
        assert res.ev_dollars > 0
        assert res.tier in ["PREMIUM", "VALUE"]

    def test_calculate_ev_negative(self):
        # p = 0.40, momio = -150 -> Sin ventaja
        res = calculate_ev(prob_model=0.40, odds_american=-150, bankroll=1000.0)
        assert res.has_edge is False
        assert res.ev_dollars < 0
        assert res.tier == "SKIP"

    def test_build_parlay(self):
        leg1 = calculate_ev(prob_model=0.72, odds_american=-130)
        leg1.team_name = "Boston Celtics"
        leg2 = calculate_ev(prob_model=0.70, odds_american=-125)
        leg2.team_name = "Denver Nuggets"

        parlay = build_parlay([leg1, leg2], max_legs=2, min_prob_per_leg=0.65, bankroll=1000.0)

        # Prob conjunta ≈ 0.72 * 0.70 = 0.504
        assert pytest.approx(parlay.combined_prob, 0.02) == 0.504
        assert parlay.is_viable is True
        assert parlay.combined_ev > 0
        assert parlay.kelly_amount > 0


class TestMonteCarlo:
    def test_simulation_execution(self):
        res = run_monte_carlo_simulation(
            prob_win=0.70,
            odds_decimal=1.75,
            bankroll_initial=1000.0,
            n_bets=30,
            n_simulations=50
        )
        assert "mediana_final" in res
        assert "prob_ruina" in res
        assert res["mediana_final"] > 1000.0  # El capital mediano debe crecer con +EV
        assert res["prob_ruina"] <= 0.05
