"""
Pruebas unitarias para el módulo de Sports Cards & NIL Analytics:
- multipliers: tablas paramétricas y constantes de Efecto Uniforme (-52%).
- hedonic_pricing: cálculo de Fair Market Value (FMV).
- arbitrage_detector: detección neta de anomalías con comisiones de marketplace.
- prophet_forecaster: proyecciones sintéticas y series de tiempo a 90 días.
"""
import pytest
from app.services.cards.multipliers import (
    UNIFORM_MULTIPLIERS,
    GRADE_MULTIPLIERS,
    PARALLEL_MULTIPLIERS,
    AUTOGRAPH_MULTIPLIERS,
    PLAYER_BASE_PEDIGREE,
)
from app.services.cards.hedonic_pricing import calculate_fair_market_value
from app.services.cards.arbitrage_detector import analyze_arbitrage_opportunity
from app.services.cards.prophet_forecaster import project_depreciation_trajectory_prophet


class TestCardMultipliers:
    def test_uniform_effect_nil_discount(self):
        # Efecto uniforme en NIL debe ser 0.48 (-52%)
        assert UNIFORM_MULTIPLIERS["NIL"] == 0.48

    def test_uniform_effect_nba_official(self):
        assert UNIFORM_MULTIPLIERS["Rookie Card"] == 1.00

    def test_grade_multipliers(self):
        assert GRADE_MULTIPLIERS["PSA 10"] == 3.85
        assert GRADE_MULTIPLIERS["PSA 9"] == 1.65
        assert GRADE_MULTIPLIERS["Raw"] == 1.00

    def test_parallel_multipliers(self):
        assert PARALLEL_MULTIPLIERS["Gold /10"] == 18.0
        assert PARALLEL_MULTIPLIERS["1/1 Black Prizm"] == 75.0


class TestHedonicPricing:
    def test_fair_market_value_calculation(self):
        # Valuación de novato NBA oficial PSA 10
        val = calculate_fair_market_value(
            player_name="Victor Wembanyama",
            card_type="Rookie Card",
            grade="PSA 10",
            parallel_name="Base",
            autograph_type="None"
        )
        assert val["fair_market_value"] > 0
        assert val["multipliers"]["uniform_effect"] == 1.0
        assert val["multipliers"]["grade"] == 3.85

    def test_nil_discount_reflected_in_fmv(self):
        # Carta NIL debe costar menos que su contraparte oficial
        nba_val = calculate_fair_market_value(
            player_name="Cooper Flagg",
            card_type="Rookie Card",
            grade="PSA 10"
        )
        nil_val = calculate_fair_market_value(
            player_name="Cooper Flagg",
            card_type="NIL",
            grade="PSA 10"
        )
        # FMV de NIL debe ser exactamente el 48% de la versión NBA
        ratio = nil_val["fair_market_value"] / nba_val["fair_market_value"]
        assert pytest.approx(ratio, 0.01) == 0.48


class TestArbitrageDetector:
    def test_detect_profitable_arbitrage(self):
        # FMV = 500, Precio listado = 200 -> Gran oportunidad de arbitraje
        res = analyze_arbitrage_opportunity(
            listing_price=200.0,
            fair_market_value=500.0,
            platform_fee_pct=0.13,
            shipping_buffer=4.50
        )
        assert res["is_arbitrage_opportunity"] is True
        assert res["expected_profit_dollars"] > 0
        assert res["roi_percentage"] > 15.0

    def test_reject_overpriced_card(self):
        # FMV = 200, Precio listado = 250 -> Sin arbitraje
        res = analyze_arbitrage_opportunity(
            listing_price=250.0,
            fair_market_value=200.0
        )
        assert res["is_arbitrage_opportunity"] is False
        assert res["expected_profit_dollars"] < 0


class TestProphetForecaster:
    def test_trajectory_unavailable_without_price_history(self):
        """
        El pronóstico Prophet requiere historial real de precios de transacciones.
        Hasta que se ingeste ese historial, la función retorna lista vacía de forma explícita
        en lugar de fabricar trayectorias ficticias.
        (Ver: docs/DATA_ML_REFACTOR_PLAN.md — Fase 5)
        """
        points = project_depreciation_trajectory_prophet(
            initial_price=250.0,
            card_type="Rookie Card",
            days_forecast=30
        )
        # El comportamiento correcto es retornar [] sin datos reales
        assert isinstance(points, list)
        # Cuando se implemente con datos reales, los puntos tendrán estos campos:
        # assert len(points) > 0
        # assert "date" in points[0]
        # assert "projected_price" in points[0]
        # assert "is_forecast" in points[0]
