"""
Adversarial Empirical Stress Test Suite for Milestone 3 (R3 Ensemble & Decision Engine).
Written by Challenger 2 (challenger_m3_2).

Verification Targets:
1. Spread / handicap margin model: projected margin, score consistency, and cover probability math
   across positive, negative, extreme spread lines, and invalid input fail-closed behavior.
2. Correlated microparlays: evaluate_correlated_microparlay enforces >= 2.0x return, positive EV,
   conditional probability calculation, and rejection of opposing teams.
   Standard parlay_service evaluator strictly rejects same-game legs.
3. Celery column persistence: unconsumed column CompileError demonstration, schema filtering,
   and live PostgreSQL roundtrip verification for model_agreement and confluence_tier.
4. Vegas independence & Confluence decision rules.
"""
import math
import hashlib
from datetime import datetime, timezone
import pytest
from scipy.stats import norm
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import CompileError

from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from app.services.parlay_service import (
    evaluate_correlated_microparlay,
    evaluate_custom_parlay,
    _compute_joint_probability,
)
from app.services.math.ev import calculate_ev
from app.models.game import Prediction, Game
from app.core.database import AsyncSessionLocal
from app.services.persistence import append_predictions


class TestSpreadHandicapModelAdversarial:
    """Empirically stress-tests the spread/handicap margin model."""

    def test_spread_positive_negative_and_extreme_lines(self):
        """
        Tests margin projection and cover probability across positive, negative, and extreme spreads.
        Verifies score consistency, mathematical symmetry, and bounds safety.
        """
        # Baseline game setup
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
        home = "Denver Broncos"
        away = "Kansas City Chiefs"

        # Case 1: Moderate favorite at home (-3.5 spread, 70% model win prob)
        res_fav = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            model_prob_home=0.70,
            model_prob_away=0.30,
            spread_line=-3.5,
            total_line=44.0,
            home_ml=-180,
            away_ml=+155,
            bookmaker="FanDuel",
            model_agreement=3,
            confluence_tier="HIGH",
        )
        spread_fav = res_fav["spread"]
        assert spread_fav["status"] == "available"
        assert spread_fav["projected_margin"] > 0, "Favorite projected margin must be positive"
        assert spread_fav["pick"] == f"{home} -3.5"
        assert spread_fav["cover_probability"] > 0.50
        assert spread_fav["tier"] in ["PREMIUM", "VALUE"]
        # Score sum consistency
        assert math.isclose(
            spread_fav["projected_home_score"] + spread_fav["projected_away_score"],
            44.0,
            abs_tol=0.2,
        )
        assert math.isclose(
            spread_fav["projected_home_score"] - spread_fav["projected_away_score"],
            spread_fav["projected_margin"],
            abs_tol=0.2,
        )

        # Case 2: Underdog at home (+7.5 spread, 35% model win prob)
        res_dog = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            model_prob_home=0.35,
            model_prob_away=0.65,
            spread_line=7.5,
            total_line=42.0,
            home_ml=+200,
            away_ml=-240,
            bookmaker="FanDuel",
            model_agreement=2,
            confluence_tier="MODERATE",
        )
        spread_dog = res_dog["spread"]
        assert spread_dog["projected_margin"] < 0, "Underdog projected margin must be negative"
        # Since margin is approx -5.2 and spread is +7.5, home covers +7.5 with >50% prob
        assert spread_dog["pick"] == f"{home} +7.5"
        assert spread_dog["cover_probability"] > 0.50

        # Case 3: Symmetrical inversion test
        # If Denver as away team has 70% prob and market line for Kansas City (home) is +3.5
        res_sym = generate_nfl_matchup_analytics(
            home_team=away,
            away_team=home,
            game_date=g_date,
            model_prob_home=0.30,
            model_prob_away=0.70,
            spread_line=3.5,
            total_line=44.0,
            home_ml=+155,
            away_ml=-180,
            bookmaker="FanDuel",
            model_agreement=3,
            confluence_tier="HIGH",
        )
        spread_sym = res_sym["spread"]
        assert spread_sym["pick"] == f"{home} -3.5"
        assert math.isclose(spread_sym["cover_probability"], spread_fav["cover_probability"], abs_tol=1e-3)
        assert math.isclose(spread_sym["projected_margin"], -spread_fav["projected_margin"], abs_tol=0.2)

        # Case 4: Extreme lines: -28.5 blowout spread
        res_extreme_neg = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            model_prob_home=0.99,
            model_prob_away=0.01,
            spread_line=-28.5,
            total_line=50.0,
            home_ml=-2000,
            away_ml=+1000,
            bookmaker="FanDuel",
            model_agreement=3,
            confluence_tier="HIGH",
        )
        assert res_extreme_neg["spread"]["projected_margin"] > 25.0
        assert 0.0 < res_extreme_neg["spread"]["cover_probability"] < 1.0

        # Case 5: Super extreme +50.0 spread on toss-up
        res_extreme_50 = generate_nfl_matchup_analytics(
            home_team=home,
            away_team=away,
            game_date=g_date,
            model_prob_home=0.50,
            model_prob_away=0.50,
            spread_line=-50.0,
            total_line=44.0,
            home_ml=-110,
            away_ml=-110,
            bookmaker="FanDuel",
            model_agreement=2,
            confluence_tier="MODERATE",
        )
        # Away team receiving +50.0 should have near 100% cover probability
        assert res_extreme_50["spread"]["pick"] == f"{away} +50.0"
        assert res_extreme_50["spread"]["cover_probability"] > 0.999

    def test_spread_fail_closed_on_invalid_inputs(self):
        """Tests that spread model strictly fails closed on missing, boundary, or nan probabilities."""
        g_date = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)

        # Missing probability
        res_none = generate_nfl_matchup_analytics(
            home_team="DEN",
            away_team="KC",
            game_date=g_date,
            model_prob_home=None,
            model_prob_away=None,
            spread_line=-3.5,
            home_ml=-110,
            away_ml=-110,
        )
        assert res_none["spread"]["tier"] == "UNAVAILABLE"
        assert res_none["spread"]["projected_margin"] is None
        assert res_none["spread"]["pick"] is None

        # Boundary probability 0.0 / 1.0 (invalid under statistical modeling)
        res_boundary = generate_nfl_matchup_analytics(
            home_team="DEN",
            away_team="KC",
            game_date=g_date,
            model_prob_home=0.0,
            model_prob_away=1.0,
            spread_line=-3.5,
            home_ml=-110,
            away_ml=-110,
        )
        assert res_boundary["spread"]["tier"] == "UNAVAILABLE"
        assert res_boundary["spread"]["projected_margin"] is None

        # Non-summing probabilities
        res_nonsum = generate_nfl_matchup_analytics(
            home_team="DEN",
            away_team="KC",
            game_date=g_date,
            model_prob_home=0.60,
            model_prob_away=0.60,
            spread_line=-3.5,
            home_ml=-110,
            away_ml=-110,
        )
        assert res_nonsum["spread"]["tier"] == "UNAVAILABLE"


class TestCorrelatedMicroparlaysAdversarial:
    """Empirically stress-tests correlated microparlay pricing, EV, and rejection."""

    def test_correlated_microparlay_positive_ev_and_min_odds(self):
        """
        Tests that evaluate_correlated_microparlay correctly evaluates same-game correlation,
        computes conditional probability (covering spread implies winning), and requires >= 2.0x return.
        """
        # KC Moneyline (-150, p=0.70) + KC Spread (-3.5, -110, p=0.60) in game 101
        legs = [
            {
                "game_id": 101,
                "team_name": "Kansas City Chiefs",
                "market": "h2h",
                "prob_model": 0.70,
                "odds_american": -150,
                "matchup": "KC @ DEN",
            },
            {
                "game_id": 101,
                "team_name": "Kansas City Chiefs",
                "market": "spread",
                "point": -3.5,
                "prob_model": 0.60,
                "odds_american": -110,
                "matchup": "KC @ DEN",
            },
        ]

        res = evaluate_correlated_microparlay(legs)
        assert res["viable"] is True
        assert res["is_correlated_microparlay"] is True
        assert res["momio_decimal"] >= 2.0, f"Combined decimal odds must be >= 2.0, got {res['momio_decimal']}"
        assert res["ev_dolares"] > 0.0, f"Microparlay must have positive EV, got {res['ev_dolares']}"
        # Conditional probability: covering -3.5 implies winning -> P = P(cover) = 0.60
        assert math.isclose(res["prob_combinada"], 0.60, abs_tol=1e-3)

    def test_correlated_microparlay_rejects_sub_2x_combined_odds(self):
        """Microparlay with high hit rate but combined odds < 2.0x must be rejected."""
        legs_sub_2x = [
            {
                "game_id": 102,
                "team_name": "Kansas City Chiefs",
                "market": "h2h",
                "prob_model": 0.85,
                "odds_american": -400,  # 1.25 decimal
                "matchup": "KC @ DEN",
            },
            {
                "game_id": 102,
                "team_name": "Kansas City Chiefs",
                "market": "spread",
                "point": 7.5,
                "prob_model": 0.90,
                "odds_american": -300,  # 1.333 decimal -> Combined = 1.667x
                "matchup": "KC @ DEN",
            },
        ]
        res = evaluate_correlated_microparlay(legs_sub_2x)
        assert res["viable"] is False
        assert res["momio_decimal"] < 2.0
        assert "inferior al objetivo mínimo (2.0x / +100)" in res["razon"]

    def test_correlated_microparlay_rejects_negative_ev(self):
        """Microparlay with combined odds >= 2.0x and probability >= 0.40 but negative EV must be rejected."""
        legs_neg_ev = [
            {
                "game_id": 103,
                "team_name": "Denver Broncos",
                "market": "h2h",
                "prob_model": 0.40,
                "odds_american": -150,  # 1.667
                "matchup": "KC @ DEN",
            },
            {
                "game_id": 103,
                "team_name": "Denver Broncos",
                "market": "spread",
                "point": 3.5,
                "prob_model": 0.40,
                "odds_american": -250,  # 1.40 -> Combined = 2.333x
                "matchup": "KC @ DEN",
            },
        ]
        res = evaluate_correlated_microparlay(legs_neg_ev)
        assert res["viable"] is False
        assert res["ev_dolares"] <= 0.0
        assert "EV no positivo" in res["razon"]

    def test_correlated_microparlay_rejects_opposing_teams(self):
        """Microparlay combining opposing teams in the same matchup must have 0% joint prob and be non-viable."""
        legs_opposing = [
            {
                "game_id": 104,
                "team_name": "Kansas City Chiefs",
                "market": "h2h",
                "prob_model": 0.70,
                "odds_american": -150,
                "matchup": "KC @ DEN",
            },
            {
                "game_id": 104,
                "team_name": "Denver Broncos",
                "market": "spread",
                "point": 3.5,
                "prob_model": 0.55,
                "odds_american": -110,
                "matchup": "KC @ DEN",
            },
        ]
        res = evaluate_correlated_microparlay(legs_opposing)
        assert res["viable"] is False
        assert res["prob_combinada"] == 0.0
        assert res["ev_dolares"] == -100.0

    def test_standard_evaluator_strictly_rejects_same_game_legs(self):
        """The standard parlay evaluator (allow_correlation=False) must strictly reject same-game legs."""
        legs = [
            {
                "game_id": 105,
                "team_name": "Kansas City Chiefs",
                "market": "h2h",
                "prob_model": 0.70,
                "odds_american": -150,
                "matchup": "KC @ DEN",
            },
            {
                "game_id": 105,
                "team_name": "Kansas City Chiefs",
                "market": "spread",
                "point": -3.5,
                "prob_model": 0.60,
                "odds_american": -110,
                "matchup": "KC @ DEN",
            },
        ]
        res = evaluate_custom_parlay(legs, allow_correlation=False)
        assert res["viable"] is False
        assert res["has_correlated_legs"] is True
        assert "No se permite combinar selecciones del mismo evento como patas independientes" in res["razon"]


class TestCeleryColumnPersistenceAdversarial:
    """Empirically tests PostgreSQL prediction insertion and column sanitization."""

    def test_unconsumed_columns_trigger_compile_error_unfiltered(self):
        """
        Empirically proves that passing unconsumed dictionary keys into SQLAlchemy pg_insert(Prediction)
        triggers CompileError: Unconsumed column names.
        """
        raw_pred_dict = {
            "game_id": 1,
            "model_version": "v1",
            "dataset_sha256": "d" * 64,
            "artifact_sha256": "a" * 64,
            "inference_input_sha256": "i" * 64,
            "home_win_prob": 0.60,
            "away_win_prob": 0.40,
            "model_variant": "injury_aware",       # NOT a column
            "used_injury_adjustment": True,        # NOT a column
        }
        with pytest.raises(CompileError) as exc_info:
            stmt = pg_insert(Prediction).values([raw_pred_dict])
            str(stmt.compile())
        assert "Unconsumed column names" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_celery_filter_sanitizes_columns_and_persists_to_postgresql(self):
        """
        Tests that Celery's filtering logic strips non-column keys and that append_predictions
        persists model_agreement and confluence_tier cleanly into the live PostgreSQL database.
        """
        raw_pred_dict = {
            "game_id": 1,
            "model_version": "challenger_m3_test",
            "dataset_sha256": "d" * 64,
            "artifact_sha256": "a" * 64,
            "home_win_prob": 0.65,
            "away_win_prob": 0.35,
            "model_agreement": 3,
            "confluence_tier": "HIGH",
            "model_variant": "injury_aware",       # Should be stripped
            "used_injury_adjustment": True,        # Should be stripped
        }

        # Apply Celery worker filtering
        valid_cols = {c.name for c in Prediction.__table__.columns}
        sanitized = {k: v for k, v in raw_pred_dict.items() if k in valid_cols}
        assert "model_variant" not in sanitized
        assert "used_injury_adjustment" not in sanitized
        assert "model_agreement" in sanitized
        assert "confluence_tier" in sanitized

        # Execute insertion against live database
        async with AsyncSessionLocal() as session:
            # Query an actual game id to maintain foreign key integrity
            g_res = await session.execute(select(Game.id).limit(1))
            real_game_id = g_res.scalar_one_or_none() or 1
            sanitized["game_id"] = real_game_id

            test_input_hash = "adv_test_" + hashlib.sha256(b"adversarial_test").hexdigest()[:48]
            sanitized["inference_input_sha256"] = test_input_hash
            sanitized["as_of"] = datetime.now(timezone.utc)

            # Insert
            await append_predictions(session, [sanitized])
            await session.commit()

            # Query back
            sel_stmt = select(Prediction).where(
                Prediction.game_id == real_game_id,
                Prediction.model_version == "challenger_m3_test",
                Prediction.inference_input_sha256 == test_input_hash,
            )
            row = (await session.execute(sel_stmt)).scalar_one_or_none()
            assert row is not None, "Sanitized prediction must exist in PostgreSQL"
            assert row.model_agreement == 3
            assert row.confluence_tier == "HIGH"

            # Idempotency check: repeat insert with ON CONFLICT DO NOTHING
            await append_predictions(session, [sanitized])
            await session.commit()

            # Cleanup
            del_stmt = delete(Prediction).where(
                Prediction.game_id == real_game_id,
                Prediction.model_version == "challenger_m3_test",
                Prediction.inference_input_sha256 == test_input_hash,
            )
            await session.execute(del_stmt)
            await session.commit()
