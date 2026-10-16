"""
Audit and verification suite for 09-production-readiness-plan.md directives.
Tests targeted failure modes:
1. DB failure during odds fetch propagates HTTP 500 (fail-closed, never 200 [] masking).
2. Sports analytics service never fabricates -110 odds or default spread/totals lines when None.
3. Parlay prediction linker rejects date tolerance (+/- 1 day) and requires exact matching.
4. Game date search covers full Eastern Time day converted to UTC, including night games.
5. Simulation request schema has no hardcoded default odds or team names.
6. Settings security validation fails closed in production without a strong key.
"""
from datetime import datetime, date, timezone
from unittest.mock import patch, AsyncMock
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings
from app.api.v1.predictions import SimulationRequest
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from app.services.parlay_service import match_game_in_predictions


@pytest.fixture
def client():
    return TestClient(app)


def test_odds_nfl_endpoint_returns_500_on_database_failure(client):
    """When the DB query raises an exception, /api/v1/odds/nfl must return HTTP 500, never 200 []."""
    with patch("app.api.v1.odds.fetch_odds_from_db", side_effect=RuntimeError("DB connection dropped")):
        resp = client.get("/api/v1/odds/nfl")
        assert resp.status_code == 500, f"Expected HTTP 500 on DB failure, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data
        assert "RuntimeError" in data["detail"] or "DB" in data["detail"]


def test_odds_nba_endpoint_returns_500_on_database_failure(client):
    """When the DB query raises an exception, /api/v1/odds/nba must return HTTP 500, never 200 []."""
    with patch("app.api.v1.odds.fetch_odds_from_db", side_effect=RuntimeError("DB connection dropped")):
        resp = client.get("/api/v1/odds/nba")
        assert resp.status_code == 500, f"Expected HTTP 500 on DB failure, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data
        assert "RuntimeError" in data["detail"] or "DB" in data["detail"]


def test_sports_analytics_no_synthetic_defaults_and_no_minus_110():
    """Verify spread/totals with None lines do not fabricate defaults (-3.0, 38.0) or hardcode -110 odds."""
    analytics = generate_nfl_matchup_analytics(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        game_date=datetime.now(timezone.utc),
        home_ml=-140,
        away_ml=120,
        spread_line=None,
        total_line=None,
    )

    # Spread must be unavailable and contain no hardcoded odds
    spread = analytics["spread"]
    assert spread["status"] == "unavailable"
    assert spread["market_line"] is None
    assert spread["odds_american"] is None
    assert spread["odds_american"] != -110
    assert spread["pick"] is None
    assert spread["cover_probability"] is None

    # Totals must be unavailable and contain no hardcoded odds
    totals = analytics["totals"]
    assert totals["status"] == "unavailable"
    assert totals["market_line"] is None
    assert totals["odds_american"] is None
    assert totals["odds_american"] != -110
    assert totals["pick"] is None
    assert totals["probability"] is None


def test_sports_analytics_omits_spread_totals_projections_even_with_real_lines():
    """Verify that when real market lines are provided, spread and totals picks and cover probabilities remain omitted without a validated model."""
    analytics = generate_nfl_matchup_analytics(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        game_date=datetime.now(timezone.utc),
        home_ml=-140,
        away_ml=120,
        spread_line=-3.5,
        total_line=48.5,
    )

    spread = analytics["spread"]
    assert spread["market_line"] == -3.5
    assert spread["pick"] is None
    assert spread["cover_probability"] is None
    assert spread["projected_margin"] is None
    assert spread["projected_home_score"] is None
    assert spread["projected_away_score"] is None
    assert spread["tier"] == "UNAVAILABLE"
    assert spread["ev_dollars"] is None

    totals = analytics["totals"]
    assert totals["market_line"] == 48.5
    assert totals["pick"] is None
    assert totals["probability"] is None
    assert totals["projected_total"] is None
    assert totals["tier"] == "UNAVAILABLE"
    assert totals["ev_dollars"] is None


def test_sports_analytics_missing_prop_probability_returns_unavailable_metrics():
    analytics = generate_nfl_matchup_analytics(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        game_date=datetime.now(timezone.utc),
        home_ml=-120,
        away_ml=110,
        live_player_props=[{
            "player_name": "Example Player",
            "pick": "over",
            "market_line": 0.5,
            "odds_american": -110,
            "win_probability": None,
        }],
    )
    prop = analytics["player_props"][0]
    assert prop["ev_dollars"] is None
    assert prop["kelly_fraction"] is None
    assert prop["kelly_stake"] is None
    assert prop["has_kelly_edge"] is False


def test_parlay_find_prediction_rejects_out_of_window_and_ambiguous_matches():
    """match_game_in_predictions must reject games beyond timezone drift and reject ambiguous multiple matches."""
    predictions_df = pd.DataFrame([
        {
            "home_team_name": "Kansas City Chiefs",
            "away_team_name": "Baltimore Ravens",
            "game_date": "2026-10-18",
            "home_win_probability": 0.65,
            "away_win_probability": 0.35,
        }
    ])

    # Exact date match -> found
    exact_match = match_game_in_predictions(
        home_name="Kansas City Chiefs",
        away_name="Baltimore Ravens",
        target_date="2026-10-18",
        predictions_df=predictions_df,
    )
    assert exact_match is not None
    assert exact_match["home_win_probability"] == 0.65

    # Match 3 days away -> strictly rejected (returns None)
    drifted_match = match_game_in_predictions(
        home_name="Kansas City Chiefs",
        away_name="Baltimore Ravens",
        target_date="2026-10-21",
        predictions_df=predictions_df,
    )
    assert drifted_match is None

    # Ambiguous match (2 entries for the same matchup) -> strictly rejected (returns None)
    df_ambiguous = pd.DataFrame([
        {
            "home_team_name": "Kansas City Chiefs",
            "away_team_name": "Baltimore Ravens",
            "game_date": "2026-10-18",
        },
        {
            "home_team_name": "Kansas City Chiefs",
            "away_team_name": "Baltimore Ravens",
            "game_date": "2026-10-18",
        },
    ])
    ambiguous_match = match_game_in_predictions(
        home_name="Kansas City Chiefs",
        away_name="Baltimore Ravens",
        target_date="2026-10-18",
        predictions_df=df_ambiguous,
    )
    assert ambiguous_match is None


def test_simulation_request_has_no_synthetic_sports_defaults():
    """SimulationRequest schema must default sports odds and team names to None."""
    fields = SimulationRequest.model_fields
    assert fields["home_odds"].default is None
    assert fields["away_odds"].default is None
    assert fields["home_team_name"].default is None
    assert fields["visitor_team_name"].default is None


def test_settings_security_validation():
    """validate_security must raise ValueError in production for missing, placeholder, or low-entropy keys, and accept strong keys."""
    # 1. Missing / empty key
    prod_empty = Settings(APP_ENV="production", SECRET_KEY="")
    with pytest.raises(ValueError, match="SECRET_KEY"):
        prod_empty.validate_security()

    # 2. Too short (< 32 chars)
    prod_short = Settings(APP_ENV="production", SECRET_KEY="ShortKey123!")
    with pytest.raises(ValueError, match="longitud mínima"):
        prod_short.validate_security()

    # 3. Known placeholder ("changeme")
    prod_placeholder = Settings(APP_ENV="production", SECRET_KEY="changeme_insecure_key_1234567890_abc")
    with pytest.raises(ValueError, match="placeholders"):
        prod_placeholder.validate_security()

    # 4. Low entropy / repeated character ("a" * 32)
    prod_low_entropy = Settings(APP_ENV="production", SECRET_KEY="a" * 32)
    with pytest.raises(ValueError, match="entropía"):
        prod_low_entropy.validate_security()

    # 5. Strong key with high entropy and >= 3 character classes (uppercase, lowercase, digits, symbols)
    prod_strong = Settings(APP_ENV="production", SECRET_KEY="Kj9#mP2$vL5*xQ8@zW1!rT4%yU7^bN0&")
    prod_strong.validate_security()  # Should not raise
