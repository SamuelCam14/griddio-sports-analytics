"""
Challenger 1 Empirical Verification & Adversarial Stress Suite for Milestone 3.

Empirically tests and stress-tests:
1. Multi-Model Confluence:
   - Agreement >= 2/3 strictly enforced for PREMIUM or VALUE emission.
   - Agreement < 2/3 (e.g. 1/3) strictly demoted to SKIP even with high edge and positive EV.
   - Confluence tier "NONE" strictly demoted to SKIP.
2. Minimum Odds Enforcement:
   - Decimal odds < 1.40 (American < -250) strictly rejected from pick emission even with 99% probability and positive EV.
   - Exact boundary testing at decimal 1.40 (American -250 vs -251).
3. Complete Vegas Independence:
   - Programmatic audit of model features and training data ensuring 0 odds, line, spread, or bookmaker metrics.
   - Verification that pure model win probabilities are computed solely from football metrics.
4. Quota Ledger Immutability:
   - Cryptographic verification that data/odds_quota_ledger.json has SHA256:
     f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4
5. Microparlays & Spread Edge Cases:
   - Return >= 2.0x target strictly enforced.
   - Mutually exclusive outcomes produce 0 joint probability.
   - Fail-closed behavior on missing/invalid probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from app.models.game import Prediction
from app.services.math.ev import (
    BetEvent,
    american_to_decimal,
    american_to_implied_prob,
    build_parlay,
    calculate_ev,
)
from app.services.parlay_service import (
    evaluate_correlated_microparlay,
    evaluate_custom_parlay,
)
from app.services.sports_analytics_service import (
    generate_nfl_matchup_analytics,
)
from ml.data_pipeline.common import project_root
from ml.inference import (
    _apply_ensemble,
    load_all_ensemble_models,
)

EXPECTED_QUOTA_LEDGER_SHA256 = "f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4"


# ==============================================================================
# 1. Multi-Model Confluence Empirical Stress Tests
# ==============================================================================

class TestMultiModelConfluenceEmpirical:
    """Stress tests enforcement of model agreement >= 2/3 and demotion to SKIP."""

    @pytest.mark.parametrize("agreement,expected_tier,expected_edge", [
        (3, "PREMIUM", True),
        (2, "PREMIUM", True),
        (1, "SKIP", False),
        (0, "SKIP", False),
        (-1, "SKIP", False),
    ])
    def test_calculate_ev_strict_agreement_threshold(self, agreement, expected_tier, expected_edge):
        """calculate_ev must reject any pick where agreement < 2, regardless of edge."""
        # High edge scenario: prob=0.70 at -110 (1.91 dec, implied 0.524 -> edge=17.6%, EV=+$33.64)
        ev_res = calculate_ev(
            prob_model=0.70,
            odds_american=-110,
            model_agreement=agreement,
            confluence_tier="HIGH" if agreement >= 2 else "NONE",
        )
        assert ev_res.tier == expected_tier, f"Failed for agreement={agreement}: got tier={ev_res.tier}"
        assert ev_res.has_edge is expected_edge, f"Failed for agreement={agreement}: got has_edge={ev_res.has_edge}"
        if agreement < 2:
            assert any("Confluencia insuficiente" in w for w in ev_res.warnings)

    def test_calculate_ev_demotes_when_confluence_tier_is_none(self):
        """Even if agreement is 2, if confluence_tier is 'NONE' (low confidence), must demote to SKIP."""
        ev_res = calculate_ev(
            prob_model=0.56,
            odds_american=+100,
            model_agreement=2,
            confluence_tier="NONE",
        )
        assert ev_res.tier == "SKIP"
        assert ev_res.has_edge is False
        assert any("Confluencia insuficiente" in w for w in ev_res.warnings)

    def test_betevent_direct_instantiation_enforces_confluence(self):
        """BetEvent post-init auto-calculation enforces agreement >= 2 and confluence_tier != NONE."""
        # Agreement = 1 should result in SKIP despite prob 0.70 and odds +120
        event_disagree = BetEvent(
            prob_model=0.70,
            odds_american=120,
            model_agreement=1,
            confluence_tier="NONE",
        )
        assert event_disagree.has_edge is False
        assert event_disagree.tier == "SKIP"

        # Agreement = 2 with confluence MODERATE should result in PREMIUM
        event_agree = BetEvent(
            prob_model=0.70,
            odds_american=120,
            model_agreement=2,
            confluence_tier="MODERATE",
        )
        assert event_agree.has_edge is True
        assert event_agree.tier == "PREMIUM"

    def test_analytics_service_demotes_moneyline_when_agreement_is_one(self):
        """generate_nfl_matchup_analytics must assign tier SKIP to moneyline if agreement < 2."""
        now = datetime.now(timezone.utc)
        analytics = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Las Vegas Raiders",
            game_date=now,
            home_ml=-150,
            away_ml=+130,
            spread_line=-4.5,
            total_line=47.5,
            bookmaker="DraftKings",
            bankroll=1000.0,
            model_prob_home=0.75,  # Strong favorite
            model_prob_away=0.25,
            model_agreement=1,     # Disagreement in ensemble
            confluence_tier="NONE",
        )
        ml = analytics["moneyline"]
        assert ml["tier"] == "SKIP", f"Expected SKIP when model_agreement=1, got {ml['tier']}"
        assert ml["kelly_stake"] is None or ml["kelly_stake"] == 0.0

    def test_apply_ensemble_agreement_and_confluence_matrix(self):
        """Verify _apply_ensemble calculates exact model agreement and confluence tier for 3 models."""
        # Case A: 3 models agree on Home with prob >= 0.65 -> HIGH
        m1 = MagicMock(); m1.predict_proba.return_value = np.array([[0.30, 0.70]])
        m2 = MagicMock(); m2.predict_proba.return_value = np.array([[0.32, 0.68]])
        m3 = MagicMock(); m3.predict_proba.return_value = np.array([[0.35, 0.65]])
        models = {"logistic": m1, "hist_gradient_boosting": m2, "random_forest": m3}

        df = pd.DataFrame([{"game_id": "test_1"}])
        X = pd.DataFrame([[0.5] * 10])

        res_a = _apply_ensemble(models, X, df).iloc[0]
        assert res_a["model_agreement"] == 3
        assert res_a["confluence_tier"] == "HIGH"

        # Case B: 2 models agree on Home, 1 on Away, blended prob >= 0.55 -> MODERATE
        m3.predict_proba.return_value = np.array([[0.55, 0.45]])  # m3 favors Away
        res_b = _apply_ensemble(models, X, df).iloc[0]
        # Blended prob: (0.70 + 0.68 + 0.45) / 3 = 0.61 -> Home is favorite
        # Models 1 & 2 favor Home (>= 0.5), Model 3 favors Away (< 0.5)
        assert res_b["model_agreement"] == 2
        assert res_b["confluence_tier"] == "MODERATE"

        # Case C: 2 models agree on Home, 1 on Away, blended prob < 0.55 -> NONE
        m1.predict_proba.return_value = np.array([[0.48, 0.52]])
        m2.predict_proba.return_value = np.array([[0.47, 0.53]])
        m3.predict_proba.return_value = np.array([[0.60, 0.40]])
        # Blended prob: (0.52 + 0.53 + 0.40) / 3 = 0.4833 -> Away is favorite (prob_home < 0.5)
        # Favorite is Away! Models favoring Away: m3 only (1 model).
        res_c = _apply_ensemble(models, X, df).iloc[0]
        assert res_c["model_agreement"] == 1
        assert res_c["confluence_tier"] == "NONE"


# ==============================================================================
# 2. Minimum Odds (< 1.40 / < -250) Empirical Stress Tests
# ==============================================================================

class TestMinimumOddsEnforcementEmpirical:
    """Stress tests rejection of bets with decimal odds < 1.40 (American < -250)."""

    @pytest.mark.parametrize("odds_american,prob_model", [
        (-251, 0.80),   # Decimal 1.3984 (< 1.40)
        (-300, 0.85),   # Decimal 1.3333 (< 1.40)
        (-400, 0.90),   # Decimal 1.2500 (< 1.40)
        (-500, 0.92),   # Decimal 1.2000 (< 1.40)
        (-1000, 0.99),  # Decimal 1.1000 (< 1.40), 99% probability, massive positive EV
    ])
    def test_ev_engine_strictly_rejects_odds_below_1_40(self, odds_american, prob_model):
        """Decimal odds < 1.40 must ALWAYS be assigned tier SKIP with has_edge=False."""
        ev_res = calculate_ev(
            prob_model=prob_model,
            odds_american=odds_american,
            model_agreement=3,
            confluence_tier="HIGH",
        )
        assert ev_res.odds_decimal < 1.40, f"Test setup error: expected odds < 1.40, got {ev_res.odds_decimal}"
        assert ev_res.tier == "SKIP", f"Odds {odds_american} (dec {ev_res.odds_decimal}) was assigned {ev_res.tier} instead of SKIP"
        assert ev_res.has_edge is False
        assert any("inferior al umbral mínimo de 1.40" in w for w in ev_res.warnings)

    def test_exact_minimum_odds_boundary_transition(self):
        """Exact transition: -251 (1.398) fails, -250 (1.400) passes when EV > 0."""
        # -251 American = 1.3984 decimal -> MUST FAIL
        res_fail = calculate_ev(prob_model=0.80, odds_american=-251, model_agreement=3, confluence_tier="HIGH")
        assert res_fail.odds_decimal < 1.40
        assert res_fail.has_edge is False
        assert res_fail.tier == "SKIP"

        # -250 American = 1.4000 decimal -> MUST PASS (prob 0.80 > implied 0.714, EV > 0)
        res_pass = calculate_ev(prob_model=0.80, odds_american=-250, model_agreement=3, confluence_tier="HIGH")
        assert res_pass.odds_decimal >= 1.40
        assert res_pass.has_edge is True
        assert res_pass.tier in ("PREMIUM", "VALUE")

    def test_analytics_service_enforces_minimum_odds_on_moneyline(self):
        """generate_nfl_matchup_analytics rejects heavy favorite moneyline < 1.40."""
        now = datetime.now(timezone.utc)
        analytics = generate_nfl_matchup_analytics(
            home_team="San Francisco 49ers",
            away_team="Carolina Panthers",
            game_date=now,
            home_ml=-400,  # Decimal 1.25 (< 1.40)
            away_ml=+320,
            spread_line=-9.5,
            total_line=42.0,
            bookmaker="FanDuel",
            bankroll=1000.0,
            model_prob_home=0.88,
            model_prob_away=0.12,
            model_agreement=3,
            confluence_tier="HIGH",
        )
        ml = analytics["moneyline"]
        assert ml["tier"] == "SKIP", f"Expected SKIP for -400 moneyline, got {ml['tier']}"
        assert analytics["has_edge"] is False
        assert ml["has_kelly_edge"] is False


# ==============================================================================
# 3. Vegas Odds Independence Empirical Verification
# ==============================================================================

class TestVegasIndependenceEmpirical:
    """Verifies that model input features contain ZERO odds, betting, or Vegas metrics."""

    def test_model_metadata_features_contain_zero_odds_metrics(self):
        """Inspect model_metadata.json features list and assert 0 odds metrics."""
        meta_path = project_root() / "backend" / "ml" / "models" / "nfl" / "model_metadata.json"
        if not meta_path.exists():
            meta_path = project_root() / "ml" / "models" / "nfl" / "model_metadata.json"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        features = meta.get("features", [])
        assert len(features) == 32, f"Expected 32 features, got {len(features)}"

        forbidden_keywords = [
            "odd", "odds", "spread", "moneyline", "ml", "line", "lines",
            "over", "under", "total", "totals", "vig", "juice", "bookmaker",
            "sportsbook", "vegas", "implied", "consensus", "bet", "market",
        ]

        violations = []
        for feat in features:
            for kw in forbidden_keywords:
                # Check whole-word or delimited match
                parts = feat.lower().split("_")
                if kw in parts:
                    violations.append((feat, kw))

        assert not violations, f"Vegas independence violation! Found betting features: {violations}"

    def test_nfl_training_dataset_columns_contain_zero_odds_metrics(self):
        """Audit data/processed/nfl_training.csv to confirm zero betting/odds features."""
        dataset_path = project_root() / "data" / "processed" / "nfl_training.csv"
        assert dataset_path.is_file(), f"Training dataset missing at {dataset_path}"

        df = pd.read_csv(dataset_path, nrows=5)
        feature_cols = [
            c for c in df.columns
            if c not in {
                "game_id", "game_date", "feature_cutoff_exclusive",
                "feature_max_observed_game_date", "season", "sport",
                "home_team_id", "away_team_id", "source", "source_ref",
                "home_score", "away_score", "target_home_win",
            }
        ]

        forbidden = {"odd", "odds", "spread", "moneyline", "line", "vig", "book", "market"}
        for col in feature_cols:
            col_tokens = set(col.lower().split("_"))
            matched = col_tokens.intersection(forbidden)
            assert not matched, f"Feature column '{col}' contains forbidden betting tokens: {matched}"


# ==============================================================================
# 4. Quota Ledger Immutability Verification
# ==============================================================================

class TestQuotaLedgerImmutabilityEmpirical:
    """Verifies that data/odds_quota_ledger.json matches the exact SHA-256 hash."""

    def test_quota_ledger_hash_matches_strict_baseline(self):
        """data/odds_quota_ledger.json sha256 must match f935d68ba8f0e5656a6a34d60c7f7ba2cb541aba54028dbe0ed5cbeb52255af4."""
        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        assert ledger_path.is_file(), f"Ledger file missing: {ledger_path}"

        computed_sha256 = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        assert computed_sha256 == EXPECTED_QUOTA_LEDGER_SHA256, (
            f"Quota ledger hash altered! Expected {EXPECTED_QUOTA_LEDGER_SHA256}, got {computed_sha256}"
        )


# ==============================================================================
# 5. Microparlays & Spread Edge Cases Empirical Stress Tests
# ==============================================================================

class TestMicroparlaysAndSpreadAdversarial:
    """Adversarial testing of correlated microparlays and spread modeling."""

    def test_build_parlay_enforces_minimum_combined_odds_of_2_0x(self):
        """build_parlay must reject parlays where combined decimal odds < 2.0x (+100)."""
        # Two heavy favorites that individually have edge, but combined odds < 2.0x
        e1 = BetEvent(prob_model=0.75, odds_american=-200, odds_decimal=1.50, has_edge=True, model_agreement=3)
        e2 = BetEvent(prob_model=0.80, odds_american=-300, odds_decimal=1.30, has_edge=True, model_agreement=3)

        # Combined odds: 1.50 * 1.30 = 1.95 (< 2.0x)
        res = build_parlay([e1, e2], min_prob_per_leg=0.70)
        assert res.is_viable is False, f"Expected non-viable parlay with combined odds {res.combined_decimal_odds} < 2.0x"
        assert "cuota combinada: 1.95 < 2.0x" in res.reason

    def test_microparlay_same_game_contradictory_markets_have_zero_joint_prob(self):
        """Microparlay combining Home ML and Away +3.5 produces conditional conflict."""
        legs = [
            {"event_id": "G1", "team_name": "Kansas City Chiefs", "market": "h2h", "prob_model": 0.60, "odds_american": -140},
            {"event_id": "G1", "team_name": "Las Vegas Raiders", "market": "h2h", "prob_model": 0.40, "odds_american": +120},
        ]
        res = evaluate_correlated_microparlay(legs)
        assert res["viable"] is False
        assert res["prob_combinada"] == 0.0

    def test_spread_model_extreme_spread_handling(self):
        """Spread model handles extreme double-digit spreads without numeric breakdown."""
        now = datetime.now(timezone.utc)
        analytics = generate_nfl_matchup_analytics(
            home_team="Buffalo Bills",
            away_team="New York Jets",
            game_date=now,
            home_ml=-900,
            away_ml=+600,
            spread_line=-17.5,
            total_line=41.5,
            bookmaker="DraftKings",
            bankroll=1000.0,
            model_prob_home=0.85,
            model_prob_away=0.15,
            model_agreement=3,
            confluence_tier="HIGH",
        )
        spread = analytics["spread"]
        assert spread["status"] == "available"
        assert 0.0 < spread["cover_probability"] < 1.0
        assert math.isfinite(spread["projected_margin"])
        assert math.isfinite(spread["projected_home_score"])
        assert math.isfinite(spread["projected_away_score"])

    def test_spread_pick_suppressed_when_confluence_is_none(self):
        """Spread model must assign tier SKIP and suppress spread_pick when confluence is NONE."""
        now = datetime.now(timezone.utc)
        analytics = generate_nfl_matchup_analytics(
            home_team="Kansas City Chiefs",
            away_team="Denver Broncos",
            game_date=now,
            home_ml=-200,
            away_ml=+170,
            spread_line=-4.5,
            total_line=45.0,
            bookmaker="BetMGM",
            bankroll=1000.0,
            model_prob_home=0.68,
            model_prob_away=0.32,
            model_agreement=1,     # Disagreement!
            confluence_tier="NONE",
        )
        spread = analytics["spread"]
        assert spread["tier"] == "SKIP"
        assert spread["pick"] is None
        assert spread["has_kelly_edge"] is False

    def test_spread_pickem_line_zero(self):
        """Spread line of exactly 0.0 (pick'em) is evaluated cleanly."""
        now = datetime.now(timezone.utc)
        analytics = generate_nfl_matchup_analytics(
            home_team="Dallas Cowboys",
            away_team="Philadelphia Eagles",
            game_date=now,
            home_ml=-110,
            away_ml=-110,
            spread_line=0.0,
            total_line=48.0,
            bookmaker="DraftKings",
            bankroll=1000.0,
            model_prob_home=0.58,
            model_prob_away=0.42,
            model_agreement=3,
            confluence_tier="MODERATE",
        )
        spread = analytics["spread"]
        assert spread["status"] == "available"
        assert spread["projected_margin"] > 0
        assert spread["cover_probability"] > 0.50
        assert spread["pick"] == "Dallas Cowboys +0.0"

    def test_ensemble_apply_raises_on_empty_models(self):
        """_apply_ensemble raises DataQualityError if no valid models provide probabilities."""
        from ml.data_pipeline.common import DataQualityError
        df = pd.DataFrame([{"game_id": "G1"}])
        X = pd.DataFrame([[0.5] * 5])
        with pytest.raises(DataQualityError, match="Ningún modelo produjo probabilidades válidas"):
            _apply_ensemble({}, X, df)

    def test_ensemble_apply_raises_on_non_finite_predictions(self):
        """_apply_ensemble raises DataQualityError if model outputs NaN or inf."""
        from ml.data_pipeline.common import DataQualityError
        mock_broken = MagicMock()
        mock_broken.predict_proba.return_value = np.array([[np.nan, np.inf]])
        df = pd.DataFrame([{"game_id": "G1"}])
        X = pd.DataFrame([[0.5] * 5])
        with pytest.raises(DataQualityError, match="Ningún modelo produjo probabilidades válidas"):
            _apply_ensemble({"broken": mock_broken}, X, df)

    def test_kelly_zero_when_has_edge_is_false(self):
        """calculate_ev must ensure kelly_fraction == 0.0 and kelly_amount == 0.0 when has_edge is False."""
        res = calculate_ev(
            prob_model=0.45,
            odds_american=-110,
            model_agreement=3,
            confluence_tier="HIGH",
            bankroll=5000.0,
        )
        assert res.has_edge is False
        assert res.tier == "SKIP"
        assert res.kelly_fraction == 0.0
        assert res.kelly_amount == 0.0

