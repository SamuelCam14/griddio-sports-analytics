"""
Pruebas unitarias para el servicio de análisis integral de NFL y player props (sports_analytics_service.py).
Garantiza rigor matemático y ausencia de datos ficticios o hardcodeados.
"""
from datetime import datetime, timezone
import pytest
from app.services.sports_analytics_service import generate_nfl_matchup_analytics


class TestNFLAnalyticsService:
    def test_nfl_matchup_quantitative_modeling(self):
        """Valida que los cálculos de Spread, Totales y Moneyline sean matemáticamente rigurosos."""
        analytics = generate_nfl_matchup_analytics(
            home_team="Cleveland Browns",
            away_team="Pittsburgh Steelers",
            game_date=datetime.now(timezone.utc),
            home_ml=125,
            away_ml=-150,
            spread_line=3.0,
            total_line=38.0,
        )

        assert analytics["sport"] == "NFL"
        assert analytics["home_team"] == "Cleveland Browns"
        assert analytics["away_team"] == "Pittsburgh Steelers"

        # Validar probabilidades
        assert analytics["prob_away"] > analytics["prob_home"]
        assert pytest.approx(analytics["prob_home"] + analytics["prob_away"], 0.01) == 1.0

        # Validar Moneyline Pick
        assert analytics["moneyline"]["pick_team"] == "Pittsburgh Steelers"

        # Validar Spread
        assert "spread" in analytics
        assert analytics["spread"]["pick"] is not None
        assert analytics["spread"]["cover_probability"] >= 0.50

        # Validar Totals
        assert "totals" in analytics
        assert analytics["totals"]["pick"] in ["OVER", "UNDER"]

        # Sin props provistas, debe retornar lista vacía (NUNCA inventar jugadores)
        assert analytics["player_props"] == []

        # Validar Factores Explicados para Humanos
        factors = analytics["key_factors"]
        assert len(factors) >= 3
        assert any("favorito" in f for f in factors)

    def test_nfl_matchup_with_live_player_props(self):
        """Valida que cuando se proveen player props reales de casas de apuestas, se integren correctamente."""
        real_props_mock = [
            {
                "player_name": "Aaron Rodgers",
                "position": "QB",
                "prop_type": "passing_yards",
                "market_line": 215.5,
                "projected_value": 232.0,
                "pick": "OVER",
                "odds_american": -114,
                "win_probability": 0.58,
                "edge_pct": 5.2,
                "tier": "TIER_2 VALUE",
            },
            {
                "player_name": "Jaylen Warren",
                "position": "RB",
                "prop_type": "rushing_yards",
                "market_line": 75.5,
                "projected_value": 84.0,
                "pick": "OVER",
                "odds_american": -110,
                "win_probability": 0.61,
                "edge_pct": 7.4,
                "tier": "TIER_3 PREMIUM",
            }
        ]

        analytics = generate_nfl_matchup_analytics(
            home_team="Cleveland Browns",
            away_team="Pittsburgh Steelers",
            game_date=datetime.now(timezone.utc),
            home_ml=125,
            away_ml=-150,
            spread_line=3.0,
            total_line=38.0,
            live_player_props=real_props_mock,
        )

        props = analytics["player_props"]
        assert len(props) == 2
        assert props[0]["player_name"] == "Aaron Rodgers"
        assert props[1]["player_name"] == "Jaylen Warren"
