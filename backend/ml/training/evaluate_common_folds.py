"""Evaluación histórica rigurosa de modelos NFL y NBA bajo folds cronológicos comunes.

Compara baselines y los 3 modelos (Bounded Logistic, HistGradientBoosting, RandomForest)
más el ensamble multi-modelo sobre los mismos 4 folds de desarrollo cronológico estricto,
guardando predicciones out-of-fold partido a partido y métricas por fold con calibración ECE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.data_pipeline.common import backend_root, project_root, write_manifest
from ml.data_pipeline.temporal_features import NBA_GAME_STATS, NFL_GAME_STATS, feature_columns
from ml.models.bounded_linear import BoundedLogisticRegression
from ml.training.train_temporal import chronological_folds, get_monotonic_constraints, load_training_dataset


def compute_ece(y_true: np.ndarray, probas: np.ndarray, n_bins: int = 10) -> float:
    """Calcula el Expected Calibration Error (ECE)."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_indices = np.clip(np.digitize(probas, bins) - 1, 0, n_bins - 1)
    ece = 0.0
    total = len(y_true)
    for b in range(n_bins):
        mask = bin_indices == b
        if np.sum(mask) > 0:
            bin_acc = float(np.mean(y_true[mask]))
            bin_conf = float(np.mean(probas[mask]))
            ece += (np.sum(mask) / total) * abs(bin_acc - bin_conf)
    return float(ece)


def _build_models(features: list[str]):
    return {
        "logistic": make_pipeline(
            StandardScaler(),
            BoundedLogisticRegression(C=0.3, feature_names=features),
        ),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=50,
            learning_rate=0.03,
            max_leaf_nodes=15,
            min_samples_leaf=30,
            l2_regularization=10.0,
            random_state=42,
            monotonic_cst=get_monotonic_constraints(features),
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
        ),
    }


def run_nfl_common_folds(output_dir: Path) -> dict:
    df_inj, manifest_inj = load_training_dataset("nfl", variant="injury_aware")
    df_inj = df_inj.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    team_features = feature_columns(NFL_GAME_STATS, include_availability=False)
    inj_features = feature_columns(NFL_GAME_STATS, include_availability=True)

    folds, holdout_idx = chronological_folds(df_inj, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df_inj["game_date"]).dt.date.to_numpy()

    oof_rows = []
    fold_reports = {"team_only": {}, "injury_aware": {}, "baselines": {}}
    model_names = ["logistic", "hist_gradient_boosting", "random_forest", "ensemble"]

    for m in model_names:
        fold_reports["team_only"][m] = []
        fold_reports["injury_aware"][m] = []
    fold_reports["baselines"]["majority"] = []
    fold_reports["baselines"]["rating"] = []

    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        test_sub = df_inj.iloc[test_idx].copy()
        y_train = df_inj.iloc[train_idx]["target_home_win"].astype(int).to_numpy()
        y_test = test_sub["target_home_win"].astype(int).to_numpy()

        # 1. Baseline Majority
        p_majority = np.full(len(test_idx), float(np.mean(y_train)))
        fold_reports["baselines"]["majority"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "test_from": str(test_sub["game_date"].min()),
            "test_through": str(test_sub["game_date"].max()),
            "brier": float(brier_score_loss(y_test, p_majority)),
            "log_loss": float(log_loss(y_test, p_majority, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_majority >= 0.5).astype(int))),
            "auc": 0.5,
            "ece": compute_ece(y_test, p_majority),
        })

        # 2. Baseline Rating
        margin_diff = test_sub["diff_avg_margin"].to_numpy(dtype=float)
        p_rating = expit((margin_diff + 2.0) / 7.0)
        p_rating = np.clip(p_rating, 0.01, 0.99)
        fold_reports["baselines"]["rating"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "test_from": str(test_sub["game_date"].min()),
            "test_through": str(test_sub["game_date"].max()),
            "brier": float(brier_score_loss(y_test, p_rating)),
            "log_loss": float(log_loss(y_test, p_rating, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_rating >= 0.5).astype(int))),
            "auc": float(roc_auc_score(y_test, p_rating)),
            "ece": compute_ece(y_test, p_rating),
        })

        # 3. Team-Only Models
        team_models = _build_models(team_features)
        X_train_team = df_inj.iloc[train_idx][team_features].astype(float)
        X_test_team = test_sub[team_features].astype(float)

        p_team = {}
        for m_name, clf in team_models.items():
            clf.fit(X_train_team, y_train)
            p = clf.predict_proba(X_test_team)[:, 1]
            p_team[m_name] = p
            fold_reports["team_only"][m_name].append({
                "fold": fold_no,
                "train_rows": len(train_idx),
                "test_rows": len(test_idx),
                "brier": float(brier_score_loss(y_test, p)),
                "log_loss": float(log_loss(y_test, p, labels=[0, 1])),
                "accuracy": float(accuracy_score(y_test, (p >= 0.5).astype(int))),
                "auc": float(roc_auc_score(y_test, p)),
                "ece": compute_ece(y_test, p),
            })
        p_team_ens = np.mean([p_team["logistic"], p_team["hist_gradient_boosting"], p_team["random_forest"]], axis=0)
        fold_reports["team_only"]["ensemble"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "brier": float(brier_score_loss(y_test, p_team_ens)),
            "log_loss": float(log_loss(y_test, p_team_ens, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_team_ens >= 0.5).astype(int))),
            "auc": float(roc_auc_score(y_test, p_team_ens)),
            "ece": compute_ece(y_test, p_team_ens),
        })

        # 4. Injury-Aware Models
        inj_models = _build_models(inj_features)
        X_train_inj = df_inj.iloc[train_idx][inj_features].astype(float)
        X_test_inj = test_sub[inj_features].astype(float)

        p_inj = {}
        for m_name, clf in inj_models.items():
            clf.fit(X_train_inj, y_train)
            p = clf.predict_proba(X_test_inj)[:, 1]
            p_inj[m_name] = p
            fold_reports["injury_aware"][m_name].append({
                "fold": fold_no,
                "train_rows": len(train_idx),
                "test_rows": len(test_idx),
                "brier": float(brier_score_loss(y_test, p)),
                "log_loss": float(log_loss(y_test, p, labels=[0, 1])),
                "accuracy": float(accuracy_score(y_test, (p >= 0.5).astype(int))),
                "auc": float(roc_auc_score(y_test, p)),
                "ece": compute_ece(y_test, p),
            })
        p_inj_ens = np.mean([p_inj["logistic"], p_inj["hist_gradient_boosting"], p_inj["random_forest"]], axis=0)
        fold_reports["injury_aware"]["ensemble"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "brier": float(brier_score_loss(y_test, p_inj_ens)),
            "log_loss": float(log_loss(y_test, p_inj_ens, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_inj_ens >= 0.5).astype(int))),
            "auc": float(roc_auc_score(y_test, p_inj_ens)),
            "ece": compute_ece(y_test, p_inj_ens),
        })

        for i, idx in enumerate(test_idx):
            row = df_inj.iloc[idx]
            oof_rows.append({
                "game_id": row["game_id"],
                "game_date": str(row["game_date"]),
                "home_team_id": row.get("home_team_id", ""),
                "away_team_id": row.get("away_team_id", ""),
                "fold": fold_no,
                "target_home_win": int(y_test[i]),
                "prob_majority_baseline": round(float(p_majority[i]), 4),
                "prob_rating_baseline": round(float(p_rating[i]), 4),
                "prob_team_only_logistic": round(float(p_team["logistic"][i]), 4),
                "prob_team_only_hgb": round(float(p_team["hist_gradient_boosting"][i]), 4),
                "prob_team_only_rf": round(float(p_team["random_forest"][i]), 4),
                "prob_team_only_ensemble": round(float(p_team_ens[i]), 4),
                "prob_injury_aware_logistic": round(float(p_inj["logistic"][i]), 4),
                "prob_injury_aware_hgb": round(float(p_inj["hist_gradient_boosting"][i]), 4),
                "prob_injury_aware_rf": round(float(p_inj["random_forest"][i]), 4),
                "prob_injury_aware_ensemble": round(float(p_inj_ens[i]), 4),
            })

    pred_df = pd.DataFrame(oof_rows)
    pred_csv_path = output_dir / "nfl_common_folds_predictions.csv"
    pred_df.to_csv(pred_csv_path, index=False)
    pred_csv_sha256 = hashlib.sha256(pred_csv_path.read_bytes()).hexdigest()

    summary = {
        "evaluation_name": "nfl_common_chronological_folds_evaluation",
        "sport": "NFL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_test_rows": len(pred_df),
        "total_development_rows": int(len(df_inj) - len(holdout_idx)),
        "holdout_rows_reserved": int(len(holdout_idx)),
        "holdout_dates": {
            "from": str(df_inj.iloc[holdout_idx]["game_date"].min()),
            "to": str(df_inj.iloc[holdout_idx]["game_date"].max()),
            "status": "locked_untouched_not_used_for_model_selection",
        },
        "output_predictions_csv": pred_csv_path.name,
        "output_predictions_sha256": pred_csv_sha256,
        "folds_metrics": fold_reports,
        "mean_metrics": {},
    }

    for cat in ["baselines", "team_only", "injury_aware"]:
        summary["mean_metrics"][cat] = {}
        for m_name, f_list in fold_reports[cat].items():
            briers = [f["brier"] for f in f_list]
            maj_briers = [f["brier"] for f in fold_reports["baselines"]["majority"]]
            summary["mean_metrics"][cat][m_name] = {
                "mean_brier": float(np.mean(briers)),
                "mean_log_loss": float(np.mean([f["log_loss"] for f in f_list])),
                "mean_accuracy": float(np.mean([f["accuracy"] for f in f_list])),
                "mean_auc": float(np.mean([f["auc"] for f in f_list])),
                "mean_ece": float(np.mean([f["ece"] for f in f_list])),
                "beats_majority_in_all_folds": bool(all(b < mb for b, mb in zip(briers, maj_briers))),
                "brier_by_fold": [round(b, 4) for b in briers],
            }

    manifest_path = output_dir / "nfl_common_folds_predictions.manifest.json"
    write_manifest(manifest_path, summary)
    return summary


def run_nba_common_folds(output_dir: Path) -> dict:
    df, manifest = load_training_dataset("nba", variant="injury_aware")
    df = df.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    features = feature_columns(NBA_GAME_STATS, include_availability=False)

    folds, holdout_idx = chronological_folds(df, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df["game_date"]).dt.date.to_numpy()

    oof_rows = []
    fold_reports = {"baselines": {}, "models": {}}
    model_names = ["logistic", "hist_gradient_boosting", "random_forest", "ensemble"]

    for m in model_names:
        fold_reports["models"][m] = []
    fold_reports["baselines"]["majority"] = []
    fold_reports["baselines"]["rating"] = []

    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        test_sub = df.iloc[test_idx].copy()
        y_train = df.iloc[train_idx]["target_home_win"].astype(int).to_numpy()
        y_test = test_sub["target_home_win"].astype(int).to_numpy()

        # 1. Baseline Majority
        p_majority = np.full(len(test_idx), float(np.mean(y_train)))
        fold_reports["baselines"]["majority"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "test_from": str(test_sub["game_date"].min()),
            "test_through": str(test_sub["game_date"].max()),
            "brier": float(brier_score_loss(y_test, p_majority)),
            "log_loss": float(log_loss(y_test, p_majority, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_majority >= 0.5).astype(int))),
            "auc": 0.5,
            "ece": compute_ece(y_test, p_majority),
        })

        # 2. Baseline Rating point-in-time (NBA home margin ~2.8 pts, scale 10.0 pts)
        margin_diff = test_sub["diff_avg_margin"].to_numpy(dtype=float)
        p_rating = expit((margin_diff + 2.8) / 10.0)
        p_rating = np.clip(p_rating, 0.01, 0.99)
        fold_reports["baselines"]["rating"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "test_from": str(test_sub["game_date"].min()),
            "test_through": str(test_sub["game_date"].max()),
            "brier": float(brier_score_loss(y_test, p_rating)),
            "log_loss": float(log_loss(y_test, p_rating, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_rating >= 0.5).astype(int))),
            "auc": float(roc_auc_score(y_test, p_rating)),
            "ece": compute_ece(y_test, p_rating),
        })

        # 3. Models
        models = _build_models(features)
        X_train = df.iloc[train_idx][features].astype(float)
        X_test = test_sub[features].astype(float)

        p_models = {}
        for m_name, clf in models.items():
            clf.fit(X_train, y_train)
            p = clf.predict_proba(X_test)[:, 1]
            p_models[m_name] = p
            fold_reports["models"][m_name].append({
                "fold": fold_no,
                "train_rows": len(train_idx),
                "test_rows": len(test_idx),
                "brier": float(brier_score_loss(y_test, p)),
                "log_loss": float(log_loss(y_test, p, labels=[0, 1])),
                "accuracy": float(accuracy_score(y_test, (p >= 0.5).astype(int))),
                "auc": float(roc_auc_score(y_test, p)),
                "ece": compute_ece(y_test, p),
            })

        p_ens = np.mean([p_models["logistic"], p_models["hist_gradient_boosting"], p_models["random_forest"]], axis=0)
        fold_reports["models"]["ensemble"].append({
            "fold": fold_no,
            "train_rows": len(train_idx),
            "test_rows": len(test_idx),
            "brier": float(brier_score_loss(y_test, p_ens)),
            "log_loss": float(log_loss(y_test, p_ens, labels=[0, 1])),
            "accuracy": float(accuracy_score(y_test, (p_ens >= 0.5).astype(int))),
            "auc": float(roc_auc_score(y_test, p_ens)),
            "ece": compute_ece(y_test, p_ens),
        })

        for i, idx in enumerate(test_idx):
            row = df.iloc[idx]
            oof_rows.append({
                "game_id": row["game_id"],
                "game_date": str(row["game_date"]),
                "home_team_id": row.get("home_team_id", ""),
                "away_team_id": row.get("away_team_id", ""),
                "fold": fold_no,
                "target_home_win": int(y_test[i]),
                "prob_majority_baseline": round(float(p_majority[i]), 4),
                "prob_rating_baseline": round(float(p_rating[i]), 4),
                "prob_logistic": round(float(p_models["logistic"][i]), 4),
                "prob_hist_gradient_boosting": round(float(p_models["hist_gradient_boosting"][i]), 4),
                "prob_random_forest": round(float(p_models["random_forest"][i]), 4),
                "prob_ensemble": round(float(p_ens[i]), 4),
            })

    pred_df = pd.DataFrame(oof_rows)
    pred_csv_path = output_dir / "nba_common_folds_predictions.csv"
    pred_df.to_csv(pred_csv_path, index=False)
    pred_csv_sha256 = hashlib.sha256(pred_csv_path.read_bytes()).hexdigest()

    summary = {
        "evaluation_name": "nba_common_chronological_folds_evaluation",
        "sport": "NBA",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_test_rows": len(pred_df),
        "total_development_rows": int(len(df) - len(holdout_idx)),
        "holdout_rows_reserved": int(len(holdout_idx)),
        "holdout_dates": {
            "from": str(df.iloc[holdout_idx]["game_date"].min()),
            "to": str(df.iloc[holdout_idx]["game_date"].max()),
            "status": "locked_untouched_not_used_for_model_selection",
        },
        "output_predictions_csv": pred_csv_path.name,
        "output_predictions_sha256": pred_csv_sha256,
        "folds_metrics": fold_reports,
        "mean_metrics": {},
    }

    for cat in ["baselines", "models"]:
        summary["mean_metrics"][cat] = {}
        for m_name, f_list in fold_reports[cat].items():
            briers = [f["brier"] for f in f_list]
            maj_briers = [f["brier"] for f in fold_reports["baselines"]["majority"]]
            summary["mean_metrics"][cat][m_name] = {
                "mean_brier": float(np.mean(briers)),
                "mean_log_loss": float(np.mean([f["log_loss"] for f in f_list])),
                "mean_accuracy": float(np.mean([f["accuracy"] for f in f_list])),
                "mean_auc": float(np.mean([f["auc"] for f in f_list])),
                "mean_ece": float(np.mean([f["ece"] for f in f_list])),
                "beats_majority_in_all_folds": bool(all(b < mb for b, mb in zip(briers, maj_briers))),
                "brier_by_fold": [round(b, 4) for b in briers],
            }

    manifest_path = output_dir / "nba_common_folds_predictions.manifest.json"
    write_manifest(manifest_path, summary)
    return summary


def run_common_folds_evaluation(sport: str = "nfl", output_dir: Path | None = None) -> dict:
    output_dir = output_dir or project_root() / "data" / "predictions" / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    sport_lower = sport.lower()
    if sport_lower == "nfl":
        return run_nfl_common_folds(output_dir)
    elif sport_lower == "nba":
        return run_nba_common_folds(output_dir)
    elif sport_lower == "all":
        nfl_res = run_nfl_common_folds(output_dir)
        nba_res = run_nba_common_folds(output_dir)
        return {"nfl": nfl_res, "nba": nba_res}
    else:
        raise ValueError(f"Deporte no soportado para evaluación de folds comunes: {sport}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["nfl", "nba", "all"], default="all")
    args = parser.parse_args()
    res = run_common_folds_evaluation(sport=args.sport)
    sports = [args.sport] if args.sport != "all" else ["nfl", "nba"]
    for s in sports:
        data = res if args.sport != "all" else res[s]
        print(f"\n==================== {s.upper()} EVALUACIÓN DE FOLDS COMUNES ====================")
        print("CSV:", data["output_predictions_csv"], "SHA-256:", data["output_predictions_sha256"])
        print("Total Filas Test (Folds 1-4):", data["total_test_rows"], "| Holdout Reservado:", data["holdout_rows_reserved"])
        categories = ["baselines", "team_only", "injury_aware"] if s == "nfl" else ["baselines", "models"]
        for cat in categories:
            print(f"\n--- {cat.upper()} ---")
            for m, met in data["mean_metrics"][cat].items():
                print(f"  {m:25s} | Brier: {met['mean_brier']:.4f} | ECE: {met['mean_ece']:.4f} | AUC: {met['mean_auc']:.4f} | Acc: {met['mean_accuracy']:.4f} | Beats Maj: {met['beats_majority_in_all_folds']}")
