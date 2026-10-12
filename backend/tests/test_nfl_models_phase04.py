import json
import joblib
import numpy as np
import pandas as pd
import pytest

from ml.data_pipeline.common import backend_root, project_root
from ml.training.train_temporal import chronological_folds, load_training_dataset


def test_models_exist_and_load_from_artifacts():
    """Verify serialized joblib models exist for team_only and load successfully."""
    models_dir = backend_root() / "ml" / "models" / "nfl" / "team_only"
    assert (models_dir / "logistic.joblib").is_file()
    assert (models_dir / "hist_gradient_boosting.joblib").is_file()
    assert (models_dir / "model_metadata.json").is_file()

    logistic = joblib.load(models_dir / "logistic.joblib")
    hgb = joblib.load(models_dir / "hist_gradient_boosting.joblib")

    assert hasattr(logistic, "predict_proba")
    assert hasattr(hgb, "predict_proba")


def test_model_metadata_phase04_compliance():
    """Check metadata has required evaluations, ensemble decision, and production_enabled=False."""
    meta_path = backend_root() / "ml" / "models" / "nfl" / "team_only" / "model_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert meta["sport"] == "NFL"
    assert meta["model_variant"] == "team_only"
    assert meta["production_enabled"] is False
    assert "evaluation" in meta
    assert "logistic" in meta["evaluation"]
    assert "hist_gradient_boosting" in meta["evaluation"]
    assert "ensemble_blend" in meta["evaluation"]

    # Model selection audit
    sel = meta["model_selection"]
    assert sel["selected_model"] == "logistic"
    assert sel["ensemble_evaluated"] is True
    assert "retained" in sel["decision"]

    # Logistic beats majority baseline on holdout
    log_eval = meta["evaluation"]["logistic"]["holdout_evaluation"]
    assert log_eval["brier"] < log_eval["majority_baseline_brier"]
    assert log_eval["log_loss"] < log_eval["majority_baseline_log_loss"]
    assert log_eval["expected_calibration_error"] < 0.05  # well calibrated ECE < 5%


def test_chronological_splits_zero_leakage():
    """Chronological folds must have strict chronological separation and zero overlap."""
    df, meta = load_training_dataset("nfl", variant="team_only")
    folds, holdout_idx = chronological_folds(df, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df["game_date"]).dt.date.to_numpy()

    holdout_min_date = all_dates[holdout_idx].min()

    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        # 1. No index overlap between train and test
        assert len(np.intersect1d(train_idx, test_idx)) == 0
        # 2. No index overlap between test and holdout
        assert len(np.intersect1d(test_idx, holdout_idx)) == 0
        # 3. Train dates strictly earlier than test dates
        assert all_dates[train_idx].max() < all_dates[test_idx].min()
        # 4. Test dates strictly earlier than holdout dates
        assert all_dates[test_idx].max() < holdout_min_date


def test_model_inference_probability_bounds():
    """Models must produce probabilities bounded strictly in [0, 1]."""
    models_dir = backend_root() / "ml" / "models" / "nfl" / "team_only"
    logistic = joblib.load(models_dir / "logistic.joblib")

    df, meta = load_training_dataset("nfl", variant="team_only")
    features = meta["quality"]["features"]
    X = df[features].iloc[:20].astype(float)

    probas = logistic.predict_proba(X)
    assert probas.shape == (20, 2)
    assert np.all(probas >= 0.0)
    assert np.all(probas <= 1.0)
    assert np.allclose(probas.sum(axis=1), 1.0)
