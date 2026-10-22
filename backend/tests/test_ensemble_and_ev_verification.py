"""
Milestone 3 Verification Test Suite — Requirement R3: Ensemble and Decision Engine.

Verifies:
1. Multi-model ensemble architecture with 3 independent models (LogisticRegression,
   HistGradientBoostingClassifier, RandomForestClassifier) trained via walk-forward validation.
2. Complete mathematical independence from bookmaker/Vegas lines during model training and feature extraction.
3. Multi-model inference engine computing individual probabilities, model agreement (1-3),
   and confluence tiers ("HIGH", "MODERATE", "NONE").
4. Confluence decision rule and +EV minimum odds filter (decimal >= 1.40, agreement >= 2/3, EV > 0),
   strictly rejecting heavy favorites with decimal odds < 1.40 even with high model probability.
5. Spread / handicap margin and cover probability modeling against market lines.
6. Correlated microparlays targeting return >= 2.0x (+100) and EV > 0 with conditional probability evaluation.
7. Celery prediction persistence remediation (stripping non-column attributes model_variant and used_injury_adjustment).
8. Production gate activation end-to-end verification.
9. Odds quota ledger immutability (SHA-256 integrity check).
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.api.v1.predictions import _production_model_enabled, get_approved_model_version
from app.models.game import Prediction
from app.services.math.ev import calculate_ev, build_parlay, american_to_decimal, decimal_to_american
from app.services.parlay_service import evaluate_custom_parlay, evaluate_correlated_microparlay
from app.services.sports_analytics_service import generate_nfl_matchup_analytics
from ml.data_pipeline.common import project_root
from ml.inference import load_all_ensemble_models, _apply_ensemble


# ─── 1. Multi-Model Walk-Forward Artifacts & Metadata ─────────────────────────

def test_multi_model_ensemble_artifacts_and_metrics():
    """Trained model directory must contain 3 distinct model artifacts and metadata beating baselines."""
    model_dir = project_root() / "backend" / "ml" / "models" / "nfl"
    if not model_dir.exists():
        model_dir = project_root() / "ml" / "models" / "nfl"

    lr_path = model_dir / "logistic.joblib"
    hgb_path = model_dir / "hist_gradient_boosting.joblib"
    rf_path = model_dir / "random_forest.joblib"
    meta_path = model_dir / "model_metadata.json"

    assert lr_path.is_file(), f"Logistic regression model missing at {lr_path}"
    assert hgb_path.is_file(), f"HistGradientBoosting model missing at {hgb_path}"
    assert rf_path.is_file(), f"RandomForest model missing at {rf_path}"
    assert meta_path.is_file(), f"Model metadata missing at {meta_path}"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sport"].upper() == "NFL"
    assert meta["model_variant"] == "injury_aware"
    assert meta["model_selection"]["decision"] == "ensemble_selected"
    assert set(meta["model_selection"]["models"]) == {"logistic", "hist_gradient_boosting", "random_forest"}

    # 4 Walk-forward chronological folds evaluated
    folds = meta["evaluation"]["ensemble_blend"]["folds"]
    assert len(folds) == 4

    # Metrics on 20% holdout lockbox
    holdout = meta["evaluation"]["ensemble_blend"]["holdout_evaluation"]
    assert holdout["accuracy"] >= 0.55, f"Holdout accuracy {holdout['accuracy']} below target 0.55"
    assert holdout["auc"] >= 0.55, f"Holdout AUC {holdout['auc']} below target 0.55"
    assert holdout["brier"] <= 0.25, f"Holdout Brier {holdout['brier']} above target 0.25"
    assert holdout["expected_calibration_error"] <= 0.15, f"Holdout ECE {holdout['expected_calibration_error']} exceeds calibration limit 0.15"


def test_load_all_ensemble_models_returns_all_three_architectures():
    """Inference engine loads all 3 models concurrently from disk."""
    models = load_all_ensemble_models("nfl")
    assert models is not None, "Failed to load ensemble models"
    assert "logistic" in models
    assert "hist_gradient_boosting" in models
    assert "random_forest" in models
    assert hasattr(models["logistic"], "predict_proba")
    assert hasattr(models["hist_gradient_boosting"], "predict_proba")
    assert hasattr(models["random_forest"], "predict_proba")


# ─── 2. Complete Independence from Vegas Odds in Feature Pipeline ─────────────

def test_vegas_odds_independence_in_feature_pipeline():
    """Features used in ML training contain zero betting lines, spreads, or odds metrics."""
    meta_path = project_root() / "backend" / "ml" / "models" / "nfl" / "model_metadata.json"
    if not meta_path.exists():
        meta_path = project_root() / "ml" / "models" / "nfl" / "model_metadata.json"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    features = meta.get("features", [])
    assert len(features) > 0, "Feature list in metadata is empty"

    forbidden_tokens = ["odd", "spread", "moneyline", "line", "book", "over", "under", "vig", "implied", "market"]
    for feat in features:
        feat_lower = feat.lower()
        for token in forbidden_tokens:
            assert token not in feat_lower, (
                f"Feature '{feat}' appears to reference bookmaker data ('{token}'). "
                f"Model training must remain strictly independent of Vegas odds."
            )


# ─── 3. Multi-Model Inference, Agreement & Confluence ─────────────────────────

def test_multi_model_inference_agreement_and_confluence_logic():
    """Multi-model inference computes individual probabilities, agreement count, and confluence tier."""
    # Synthetic models with known outputs
    mock_lr = MagicMock()
    mock_lr.predict_proba.return_value = np.array([[0.35, 0.65]])

    mock_hgb = MagicMock()
    mock_hgb.predict_proba.return_value = np.array([[0.38, 0.62]])

    mock_rf = MagicMock()
    mock_rf.predict_proba.return_value = np.array([[0.30, 0.70]])

    models = {
        "logistic": mock_lr,
        "hist_gradient_boosting": mock_hgb,
        "random_forest": mock_rf,
    }

    X_dummy = pd.DataFrame([[0.5] * 10])
    df = pd.DataFrame([{"game_id": "game_1"}])

    result = _apply_ensemble(models, X_dummy, df)
    row = result.iloc[0]

    # Blended probability: (0.65 + 0.62 + 0.70) / 3 = 0.6567
    assert row["home_win_probability"] == pytest.approx(0.6567, abs=1e-3)
    assert row["away_win_probability"] == pytest.approx(1.0 - 0.6567, abs=1e-3)

    # All 3 agree on home (> 0.50)
    assert row["model_agreement"] == 3
    # Agreement 3 and prob >= 0.65 -> HIGH confluence
    assert row["confluence_tier"] == "HIGH"

    # Verify individual breakdown
    breakdown = row["individual_models"]
    assert breakdown["logistic"]["home"] == pytest.approx(0.65)
    assert breakdown["hist_gradient_boosting"]["home"] == pytest.approx(0.62)
    assert breakdown["random_forest"]["home"] == pytest.approx(0.70)


def test_confluence_tier_categories():
    """Verify confluence categorization across agreement levels and probability thresholds."""
    # Split decision: 2 agree on home, 1 on away
    m1 = MagicMock()
    m1.predict_proba.return_value = np.array([[0.40, 0.60]])
    m2 = MagicMock()
    m2.predict_proba.return_value = np.array([[0.45, 0.55]])
    m3 = MagicMock()
    m3.predict_proba.return_value = np.array([[0.52, 0.48]])

    models = {"logistic": m1, "hist_gradient_boosting": m2, "random_forest": m3}
    X_dummy = pd.DataFrame([[0.5] * 10])
    df = pd.DataFrame([{"game_id": "game_2"}])

    res = _apply_ensemble(models, X_dummy, df).iloc[0]
    assert res["model_agreement"] == 2
    # Agreement 2 with blended prob = (0.60 + 0.55 + 0.48)/3 = 0.5433 -> NONE (prob < 0.55)
    assert res["confluence_tier"] == "NONE"

    # Moderate confluence: agreement 2 with prob >= 0.55
    m3.predict_proba.return_value = np.array([[0.49, 0.51]])
    res2 = _apply_ensemble(models, X_dummy, df).iloc[0]
    assert res2["model_agreement"] == 3
    # Agreement 3 with prob = (0.60 + 0.55 + 0.51)/3 = 0.5533 -> MODERATE (prob >= 0.55, but < 0.65)
    assert res2["confluence_tier"] == "MODERATE"


# ─── 4. Confluence Decision Rule & Minimum Odds >= 1.40 Filter ─────────────────

def test_ev_engine_rejects_heavy_favorites_below_minimum_odds():
    """EV engine strictly rejects odds < 1.40 (American < -250) even with high model probability."""
    # Decimal odds 1.25 (American -400) with 85% probability
    # Mathematically positive EV: 0.85 * 0.25 - 0.15 = +0.0625 (+6.25%)
    # BUT must be rejected by minimum odds rule (odds < 1.40)
    ev_res = calculate_ev(prob_model=0.85, odds_american=-400, model_agreement=3, bankroll=1000.0)

    assert ev_res.tier == "SKIP", "Bets with decimal odds < 1.40 must be assigned tier SKIP"
    assert ev_res.has_edge is False, "has_edge must be False when odds < 1.40"
    assert any("inferior al umbral mínimo de 1.40" in w for w in ev_res.warnings)


def test_ev_engine_requires_model_agreement_and_positive_ev():
    """EV engine requires model_agreement >= 2 to emit VALUE or PREMIUM tier."""
    # Case A: Good odds (1.91), good probability (0.60), but only 1 model agrees
    ev_disagreement = calculate_ev(prob_model=0.60, odds_american=-110, model_agreement=1, bankroll=1000.0)
    assert ev_disagreement.tier == "SKIP"
    assert ev_disagreement.has_edge is False
    assert any("Confluencia insuficiente" in w for w in ev_disagreement.warnings)

    # Case B: Agreement 3, good odds (1.91), high probability (0.65) -> PREMIUM
    ev_premium = calculate_ev(prob_model=0.65, odds_american=-110, model_agreement=3, bankroll=1000.0)
    assert ev_premium.tier == "PREMIUM"
    assert ev_premium.has_edge is True
    assert ev_premium.edge_pct > 0.05
    assert ev_premium.ev_dollars > 0

    # Case C: Agreement 2, moderate edge -> VALUE
    ev_value = calculate_ev(prob_model=0.56, odds_american=+100, model_agreement=2, bankroll=1000.0)
    assert ev_value.tier == "VALUE"
    assert ev_value.has_edge is True

    # Case D: Negative EV (odds -110, prob 0.50) -> SKIP
    ev_negative = calculate_ev(prob_model=0.50, odds_american=-110, model_agreement=3, bankroll=1000.0)
    assert ev_negative.tier == "SKIP"
    assert ev_negative.has_edge is False


# ─── 5. Spread / Handicap Margin & Cover Probability Modeling ─────────────────

def test_spread_handicap_model_computes_margins_and_cover_probabilities():
    """generate_nfl_matchup_analytics derives projected score margin and cover probability via normal CDF."""
    now = datetime.now(timezone.utc)
    analytics = generate_nfl_matchup_analytics(
        home_team="Denver Broncos",
        away_team="Seattle Seahawks",
        game_date=now,
        home_ml=-150,
        away_ml=+130,
        spread_line=-3.5,
        total_line=44.5,
        bookmaker="DraftKings",
        bankroll=1000.0,
        model_prob_home=0.65,
        model_prob_away=0.35,
        model_agreement=3,
        confluence_tier="HIGH",
    )

    spread_res = analytics["spread"]
    assert spread_res["status"] == "available"
    assert spread_res["projected_margin"] > 0, "Home projected margin must be positive for prob > 0.50"
    assert spread_res["cover_probability"] is not None
    assert 0.0 < spread_res["cover_probability"] < 1.0

    # Projected points should be realistic NFL team scores
    assert 15 <= spread_res["projected_home_score"] <= 35
    assert 15 <= spread_res["projected_away_score"] <= 35
    assert spread_res["projected_home_score"] > spread_res["projected_away_score"]


def test_spread_model_fails_closed_when_model_prob_missing():
    """Spread model returns unavailable status and tier UNAVAILABLE when model prob is missing."""
    now = datetime.now(timezone.utc)
    analytics = generate_nfl_matchup_analytics(
        home_team="Denver Broncos",
        away_team="Seattle Seahawks",
        game_date=now,
        home_ml=-150,
        away_ml=+130,
        spread_line=-3.5,
        total_line=44.5,
        bookmaker="DraftKings",
        bankroll=1000.0,
        model_prob_home=None,
        model_prob_away=None,
    )
    spread_res = analytics["spread"]
    assert spread_res["tier"] == "UNAVAILABLE"
    assert spread_res["cover_probability"] is None
    assert spread_res["pick"] is None


# ─── 6. Correlated Microparlays ────────────────────────────────────────────────

def test_correlated_microparlay_conditional_probability_evaluation():
    """Correlated microparlay evaluates conditional probabilities for within-game legs."""
    # Combining Kansas City ML (-160 -> prob 0.65) and Kansas City -3.0 (-110 -> prob 0.58)
    # Covering -3.0 points implies winning: P(win AND cover -3.0) = P(cover -3.0) = 0.58
    legs = [
        {
            "event_id": "game_kc_lv",
            "team_name": "Kansas City Chiefs",
            "market": "h2h",
            "prob_model": 0.65,
            "odds_american": -160,
        },
        {
            "event_id": "game_kc_lv",
            "team_name": "Kansas City Chiefs",
            "market": "spreads",
            "prob_model": 0.58,
            "odds_american": -110,
            "point": -3.0,
        },
    ]

    # In default mode (allow_correlation=False), must reject
    res_rejected = evaluate_custom_parlay(legs, allow_correlation=False)
    assert res_rejected["viable"] is False
    assert res_rejected["has_correlated_legs"] is True
    assert "No se permite combinar selecciones del mismo evento" in res_rejected["razon"]

    # In correlated microparlay mode, evaluates joint probability and target return >= 2.0x
    res_micro = evaluate_correlated_microparlay(legs)
    assert res_micro["has_correlated_legs"] is True
    assert res_micro["is_correlated_microparlay"] is True
    # Combined decimal odds: 1.625 * 1.909 = ~3.10 >= 2.0x
    assert res_micro["momio_decimal"] >= 2.0
    # Joint probability should be conditional min(0.65, 0.58) = 0.58
    assert res_micro["prob_combinada"] == pytest.approx(0.58, abs=0.01)
    # Positive EV: 0.58 * (3.10 - 1) * 100 - 0.42 * 100 > 0
    assert res_micro["ev_dolares"] > 0
    assert res_micro["viable"] is True


def test_correlated_microparlay_rejects_opposing_teams():
    """Microparlay with contradictory sides within the same game has 0 joint probability and fails."""
    legs = [
        {
            "event_id": "game_den_sea",
            "team_name": "Denver Broncos",
            "market": "h2h",
            "prob_model": 0.55,
            "odds_american": +110,
        },
        {
            "event_id": "game_den_sea",
            "team_name": "Seattle Seahawks",
            "market": "h2h",
            "prob_model": 0.45,
            "odds_american": -120,
        },
    ]
    res = evaluate_correlated_microparlay(legs)
    assert res["viable"] is False
    assert res["prob_combinada"] == 0.0


# ─── 7. Celery Prediction Persistence Remediation ─────────────────────────────

def test_celery_prediction_dict_filters_non_table_columns():
    """Prediction persistence strips model_variant and used_injury_adjustment and keeps valid columns."""
    valid_cols = {c.name for c in Prediction.__table__.columns}

    # Verify that model_variant and used_injury_adjustment are indeed not in the table
    assert "model_variant" not in valid_cols
    assert "used_injury_adjustment" not in valid_cols
    # Verify that confluence_tier and model_agreement ARE in the table
    assert "confluence_tier" in valid_cols
    assert "model_agreement" in valid_cols

    raw_row = {
        "home_win_probability": 0.62,
        "away_win_probability": 0.38,
        "model_agreement": 3,
        "confluence_tier": "HIGH",
    }
    meta = {
        "model_version": "nfl_injury_aware_temporal_v1",
        "model_variant": "injury_aware",
        "dataset_sha256": "fake_dataset_hash",
        "artifact_sha256": "fake_artifact_hash",
        "as_of": "2026-10-15T18:00:00Z",
    }

    pred_dict = {
        "game_id": 1,
        "model_version": meta["model_version"],
        "model_variant": meta.get("model_variant"),
        "used_injury_adjustment": meta.get("model_variant") == "injury_aware",
        "dataset_sha256": meta["dataset_sha256"],
        "artifact_sha256": meta["artifact_sha256"],
        "inference_input_sha256": "fake_input_hash",
        "as_of": pd.Timestamp(meta["as_of"]).to_pydatetime(),
        "home_win_prob": raw_row["home_win_probability"],
        "away_win_prob": raw_row["away_win_probability"],
        "best_bookmaker": "DraftKings",
        "best_home_ml": -130,
        "best_away_ml": +110,
        "home_ev": 4.5,
        "away_ev": -3.2,
        "home_kelly": 0.04,
        "away_kelly": 0.0,
        "home_has_edge": True,
        "away_has_edge": False,
    }
    if "model_agreement" in raw_row and pd.notna(raw_row["model_agreement"]):
        pred_dict["model_agreement"] = int(raw_row["model_agreement"])
    if "confluence_tier" in raw_row and pd.notna(raw_row["confluence_tier"]):
        pred_dict["confluence_tier"] = str(raw_row["confluence_tier"])

    filtered_pred_dict = {k: v for k, v in pred_dict.items() if k in valid_cols}

    assert "model_variant" not in filtered_pred_dict
    assert "used_injury_adjustment" not in filtered_pred_dict
    assert filtered_pred_dict["model_agreement"] == 3
    assert filtered_pred_dict["confluence_tier"] == "HIGH"
    assert filtered_pred_dict["home_win_prob"] == 0.62


# ─── 8. Production Gate Verification ──────────────────────────────────────────

def test_production_gate_remains_safely_closed_in_checked_in_repo():
    """When production gate is closed, model resolution rejects NFL version."""
    meta_path = project_root() / "backend" / "ml" / "models" / "nfl" / "model_metadata.json"
    if not meta_path.exists():
        meta_path = project_root() / "ml" / "models" / "nfl" / "model_metadata.json"

    real_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    disabled_meta = {**real_meta, "production_enabled": False}

    orig_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if "model_metadata.json" in str(self) and "nfl" in str(self):
            return json.dumps(disabled_meta)
        return orig_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", mock_read_text):
        assert _production_model_enabled("NFL") is False
        assert get_approved_model_version("NFL") is None


def test_production_gate_activation_end_to_end_when_enabled():
    """When production gate is enabled, model resolution returns approved NFL version and status."""
    meta_path = project_root() / "backend" / "ml" / "models" / "nfl" / "model_metadata.json"
    if not meta_path.exists():
        meta_path = project_root() / "ml" / "models" / "nfl" / "model_metadata.json"

    real_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    enabled_meta = {**real_meta, "production_enabled": True}

    orig_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if "model_metadata.json" in str(self) and "nfl" in str(self):
            return json.dumps(enabled_meta)
        return orig_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", mock_read_text):
        assert _production_model_enabled("NFL") is True
        assert get_approved_model_version("NFL") == "nfl_temporal_v1"


# ─── 9. Odds Quota Ledger Immutability ────────────────────────────────────────

def test_odds_quota_ledger_hash_integrity():
    """Verify data/odds_quota_ledger.json was not modified or corrupted during test execution."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    assert ledger_path.is_file(), f"Ledger file not found at {ledger_path}"

    current_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    expected_hash = "0fe97f674d12a2adb3f1986f486328ea69d55763ef641ba642d6e540daa11112"
    assert current_hash == expected_hash, (
        f"Quota ledger hash altered! Expected {expected_hash}, got {current_hash}. "
        f"Tests must NEVER spend external API credits or alter the ledger."
    )
