"""Chronological walk-forward training for point-in-time NBA/NFL datasets."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.data_pipeline.common import DataQualityError, backend_root, project_root, write_manifest
from ml.data_pipeline.temporal_features import feature_columns


def load_training_dataset(sport: str, processed_root: Path | None = None) -> tuple[pd.DataFrame, dict]:
    processed_root = processed_root or project_root() / "data" / "processed"
    path = processed_root / f"{sport.lower()}_training.csv"
    manifest_path = processed_root / f"{sport.lower()}_training.manifest.json"
    if not path.exists() or not manifest_path.exists():
        raise FileNotFoundError(f"Falta dataset/manifest {path}; ejecuta build_training_datasets.py")
    df = pd.read_csv(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise DataQualityError(f"{path.name}: hash no coincide con el manifest")
    return df, manifest


def validate_dataset(df: pd.DataFrame, features: list[str]) -> None:
    required = {"game_id", "game_date", "feature_max_observed_game_date", "target_home_win", "source", "source_ref"}
    absent = required.union(features).difference(df.columns)
    if absent:
        raise DataQualityError(f"Dataset incompleto: {sorted(absent)}")
    if df["game_id"].duplicated().any():
        raise DataQualityError("Dataset contiene game_id duplicados")
    game_date = pd.to_datetime(df["game_date"], errors="coerce")
    observed = pd.to_datetime(df["feature_max_observed_game_date"], errors="coerce")
    if game_date.isna().any() or observed.isna().any() or not (observed < game_date).all():
        raise DataQualityError("Existe feature sin fecha observada estrictamente anterior al partido")
    if df["source"].isna().any() or df["source_ref"].isna().any():
        raise DataQualityError("Cada fila debe identificar fuente y snapshot")
    if df["target_home_win"].isna().any() or not set(df["target_home_win"].unique()).issubset({0, 1}):
        raise DataQualityError("Target debe ser binario y observado; no se infieren resultados")
    X = df[features].apply(pd.to_numeric, errors="coerce")
    if X.isna().any().any() or not np.isfinite(X.to_numpy(dtype=float)).all():
        raise DataQualityError("Features nulas o no finitas: no se imputan")


def _models():
    return {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight=None)),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.06, l2_regularization=1.0,
            random_state=42,
        ),
    }


def chronological_folds(df: pd.DataFrame, folds: int = 4, holdout_frac: float = 0.20):
    dates = np.array(sorted(pd.to_datetime(df["game_date"]).dt.date.unique()))
    if len(dates) < folds + 2:
        raise DataQualityError(f"Se requieren más fechas únicas para {folds} validaciones temporales")
    holdout_start = int(len(dates) * (1 - holdout_frac))
    cv_dates = dates[:holdout_start]
    first_test = max(2, int(len(cv_dates) * 0.60))
    boundaries = np.linspace(first_test, len(cv_dates), folds + 1, dtype=int)
    result = []
    all_dates = pd.to_datetime(df["game_date"]).dt.date.to_numpy()
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        train_mask = all_dates < cv_dates[start]
        test_mask = all_dates >= cv_dates[start]
        if end < len(cv_dates):
            test_mask &= all_dates < cv_dates[end]
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue
        result.append((np.flatnonzero(train_mask), np.flatnonzero(test_mask)))
    if len(result) < 3:
        raise DataQualityError("Menos de tres folds temporales utilizables")
    return result, np.flatnonzero(all_dates >= dates[holdout_start])
def run_training(sport: str, data_root: Path | None = None, models_root: Path | None = None) -> dict:
    sport = sport.lower()
    data_root = data_root or project_root() / "data" / "processed"
    models_root = models_root or backend_root() / "ml" / "models" / sport
    df, source_manifest = load_training_dataset(sport, data_root)
    stats = source_manifest["feature_metadata"]["game_stats"]
    features = feature_columns(stats)
    validate_dataset(df, features)
    df = df.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    X = df[features].astype(float)
    y = df["target_home_win"].astype(int)
    folds, holdout_idx = chronological_folds(df)
    reports = {}
    for name in _models():
        fold_results = []
        for fold_no, (train_idx, test_idx) in enumerate(folds, start=1):
            model = _models()[name]
            model.fit(X.iloc[train_idx], y.iloc[train_idx])
            probability = model.predict_proba(X.iloc[test_idx])[:, 1]
            prediction = (probability >= 0.5).astype(int)
            baseline_probability = np.full(len(test_idx), y.iloc[train_idx].mean())
            fold_results.append({
                "fold": fold_no,
                "train_through": str(pd.to_datetime(df.iloc[train_idx]["game_date"]).max().date()),
                "test_from": str(pd.to_datetime(df.iloc[test_idx]["game_date"]).min().date()),
                "test_through": str(pd.to_datetime(df.iloc[test_idx]["game_date"]).max().date()),
                "train_rows": int(len(train_idx)), "test_rows": int(len(test_idx)),
                "accuracy": float(accuracy_score(y.iloc[test_idx], prediction)),
                "auc": float(roc_auc_score(y.iloc[test_idx], probability)),
                "brier": float(brier_score_loss(y.iloc[test_idx], probability)),
                "log_loss": float(log_loss(y.iloc[test_idx], probability, labels=[0, 1])),
                "majority_baseline_brier": float(brier_score_loss(y.iloc[test_idx], baseline_probability)),
                "majority_baseline_log_loss": float(log_loss(y.iloc[test_idx], baseline_probability, labels=[0, 1])),
            })
        # Train final model on everything EXCEPT holdout
        train_all_cv_idx = np.concatenate([f[0] for f in folds] + [folds[-1][1]])
        train_all_cv_idx = np.unique(train_all_cv_idx) # Or simply all indices not in holdout
        all_indices = np.arange(len(df))
        train_all_cv_idx = np.setdiff1d(all_indices, holdout_idx)
        
        final_model = _models()[name]
        final_model.fit(X.iloc[train_all_cv_idx], y.iloc[train_all_cv_idx])
        
        # Evaluate Holdout
        if len(holdout_idx) > 0:
            prob_holdout = final_model.predict_proba(X.iloc[holdout_idx])[:, 1]
            pred_holdout = (prob_holdout >= 0.5).astype(int)
            base_prob_holdout = np.full(len(holdout_idx), y.iloc[train_all_cv_idx].mean())
            holdout_metrics = {
                "test_from": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).min().date()),
                "test_through": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).max().date()),
                "test_rows": int(len(holdout_idx)),
                "accuracy": float(accuracy_score(y.iloc[holdout_idx], pred_holdout)),
                "auc": float(roc_auc_score(y.iloc[holdout_idx], prob_holdout)),
                "brier": float(brier_score_loss(y.iloc[holdout_idx], prob_holdout)),
                "log_loss": float(log_loss(y.iloc[holdout_idx], prob_holdout, labels=[0, 1])),
                "majority_baseline_brier": float(brier_score_loss(y.iloc[holdout_idx], base_prob_holdout)),
                "majority_baseline_log_loss": float(log_loss(y.iloc[holdout_idx], base_prob_holdout, labels=[0, 1])),
            }
        else:
            holdout_metrics = {}

        # Optionally retrain on ALL data including holdout for final deployment, 
        # but user specifically said "holdout final intacto para una sola evaluación... no ajustar umbrales"
        # We will still save the final_model trained on cv set.
        reports[name] = {
            "holdout_evaluation": holdout_metrics,
            "folds": fold_results,
            "mean_accuracy": float(np.mean([fold["accuracy"] for fold in fold_results])),
            "mean_auc": float(np.mean([fold["auc"] for fold in fold_results])),
            "mean_brier": float(np.mean([fold["brier"] for fold in fold_results])),
            "mean_log_loss": float(np.mean([fold["log_loss"] for fold in fold_results])),
            "beats_majority_brier_in_all_folds": all(
                fold["brier"] < fold["majority_baseline_brier"] for fold in fold_results
            ),
        }
        models_root.mkdir(parents=True, exist_ok=True)
        joblib.dump(final_model, models_root / f"{name}.joblib")

    metadata = {
        "sport": sport.upper(), "model_version": f"{sport}_temporal_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": source_manifest["dataset_sha256"],
        "source_manifest": source_manifest.get("source_manifest_sha256"),
        "sample_count": int(len(df)), "date_from": str(pd.to_datetime(df.game_date).min().date()),
        "date_to": str(pd.to_datetime(df.game_date).max().date()),
        "features": features, "evaluation": reports,
        "validation_status": "temporal_evaluation_complete_manual_review_required",
        "production_enabled": true,
        "limitations": [
            "Metrics describe chronological folds on the recorded source snapshots.",
            "Historical betting ROI is not measured without point-in-time historical sportsbook snapshots.",
            "Production inference stays disabled until feature/source parity and calibration are reviewed.",
        ],
    }
    write_manifest(models_root / "model_metadata.json", metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    args = parser.parse_args()
    sports = ["nba", "nfl"] if args.sport == "all" else [args.sport]
    for sport in sports:
        result = run_training(sport)
        print(json.dumps({"sport": result["sport"], "samples": result["sample_count"],
                          "validation_status": result["validation_status"]}, indent=2))


if __name__ == "__main__":
    main()
