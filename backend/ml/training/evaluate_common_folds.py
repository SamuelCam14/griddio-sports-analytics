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
from scipy.stats import norm
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.data_pipeline.common import DataQualityError, backend_root, project_root, write_manifest
from ml.data_pipeline.temporal_features import NBA_GAME_STATS, NFL_GAME_STATS, feature_columns
from ml.models.bounded_linear import BoundedLogisticRegression
from ml.training.train_temporal import (
    chronological_folds,
    estimate_oof_residual_sigma,
    generate_expanding_chronological_splits,
    get_monotonic_constraints,
    load_nfl_historical_total_lines,
    load_training_dataset,
)


def compute_ece(y_true: np.ndarray, probas: np.ndarray, n_bins: int = 10) -> float:
    """Calcula el Expected Calibration Error (ECE)."""
    if len(y_true) == 0:
        return 0.0
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


def _build_totals_models(features: list[str]):
    return {
        "ridge": make_pipeline(
            StandardScaler(),
            Ridge(alpha=15.0, random_state=42),
        ),
        "hist_gradient_boosting": HistGradientBoostingRegressor(
            max_iter=50,
            learning_rate=0.03,
            max_leaf_nodes=15,
            min_samples_leaf=30,
            l2_regularization=10.0,
            random_state=42,
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=100,
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
        ),
    }


def run_nfl_common_folds(output_dir: Path) -> dict:
    df_inj, manifest_inj = load_training_dataset("nfl", variant="injury_aware")
    df_inj = df_inj.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    df_inj["total_line"] = load_nfl_historical_total_lines(df_inj)
    y_totals_all = (
        pd.to_numeric(df_inj["home_score"], errors="coerce") + pd.to_numeric(df_inj["away_score"], errors="coerce")
    ).astype(float).to_numpy()
    lines_all = pd.to_numeric(df_inj["total_line"], errors="coerce").to_numpy()

    team_features = feature_columns(NFL_GAME_STATS, include_availability=False)
    inj_features = feature_columns(NFL_GAME_STATS, include_availability=True)

    folds, holdout_idx = chronological_folds(df_inj, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df_inj["game_date"]).dt.date.to_numpy()

    oof_rows = []
    fold_reports = {"team_only": {}, "injury_aware": {}, "baselines": {}}
    totals_fold_reports = {
        "team_only": {},
        "injury_aware": {},
        "baselines": {"majority": [], "rating": []},
    }
    model_names = ["logistic", "hist_gradient_boosting", "random_forest", "ensemble"]
    totals_model_names = ["ridge", "hist_gradient_boosting", "random_forest", "ensemble"]

    for m in model_names:
        fold_reports["team_only"][m] = []
        fold_reports["injury_aware"][m] = []
    fold_reports["baselines"]["majority"] = []
    fold_reports["baselines"]["rating"] = []

    for m in totals_model_names:
        totals_fold_reports["team_only"][m] = []
        totals_fold_reports["injury_aware"][m] = []

    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        test_sub = df_inj.iloc[test_idx].copy()
        max_train_d = pd.to_datetime(df_inj.iloc[train_idx]["game_date"]).dt.date.max()
        min_test_d = pd.to_datetime(test_sub["game_date"]).dt.date.min()
        if not (max_train_d < min_test_d):
            raise DataQualityError(
                f"Violación cronológica en fold {fold_no}: max(train)={max_train_d} >= min(test)={min_test_d}"
            )
        y_train = df_inj.iloc[train_idx]["target_home_win"].astype(int).to_numpy()
        y_test = test_sub["target_home_win"].astype(int).to_numpy()

        y_tot_train = y_totals_all[train_idx]
        y_tot_test = y_totals_all[test_idx]
        lines_test = lines_all[test_idx]
        lines_train = lines_all[train_idx]

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

        # Totals Baselines (Majority historical mean & Team Rating)
        p_tot_mean_base = np.full(len(test_idx), float(np.mean(y_tot_train)))
        mae_tot_mean_base = float(mean_absolute_error(y_tot_test, p_tot_mean_base))
        p_tot_rating_base = (
            test_sub["home_avg_points_for"] + test_sub["home_avg_points_against"]
            + test_sub["away_avg_points_for"] + test_sub["away_avg_points_against"]
        ).to_numpy(dtype=float) / 2.0
        mae_tot_rating_base = float(mean_absolute_error(y_tot_test, p_tot_rating_base))

        sigma_base_train = float(np.std(y_tot_train - np.mean(y_tot_train), ddof=1))
        valid_lines_test = np.isfinite(lines_test) & (lines_test > 0)
        non_push_mask = valid_lines_test & (y_tot_test != lines_test)
        has_np = bool(np.sum(non_push_mask) > 0)
        y_over_test = (y_tot_test[non_push_mask] > lines_test[non_push_mask]).astype(int) if has_np else np.array([])

        train_valid_lines = np.isfinite(lines_train) & (lines_train > 0)
        train_np = train_valid_lines & (y_tot_train != lines_train)
        train_over_rate = float((y_tot_train[train_np] > lines_train[train_np]).mean()) if np.sum(train_np) > 0 else 0.5

        if has_np:
            p_base_over = np.full(len(y_over_test), train_over_rate)
            brier_base_ou = float(brier_score_loss(y_over_test, p_base_over))
            acc_base_ou = float(accuracy_score(y_over_test, (p_base_over >= 0.5).astype(int)))
            ece_base_ou = float(compute_ece(y_over_test, p_base_over))

            z_rating = (p_tot_rating_base[non_push_mask] - lines_test[non_push_mask]) / max(sigma_base_train, 1e-3)
            p_rating_over = norm.cdf(z_rating)
            brier_rating_ou = float(brier_score_loss(y_over_test, p_rating_over))
            acc_rating_ou = float(accuracy_score(y_over_test, (p_rating_over >= 0.5).astype(int)))
            ece_rating_ou = float(compute_ece(y_over_test, p_rating_over))
        else:
            brier_base_ou, acc_base_ou, ece_base_ou = None, None, None
            brier_rating_ou, acc_rating_ou, ece_rating_ou = None, None, None

        totals_fold_reports["baselines"]["majority"].append({
            "fold": fold_no,
            "total_mae": mae_tot_mean_base,
            "over_under_brier": brier_base_ou,
            "over_under_accuracy": acc_base_ou,
            "over_under_ece": ece_base_ou,
        })
        totals_fold_reports["baselines"]["rating"].append({
            "fold": fold_no,
            "total_mae": mae_tot_rating_base,
            "over_under_brier": brier_rating_ou,
            "over_under_accuracy": acc_rating_ou,
            "over_under_ece": ece_rating_ou,
        })

        # 3. Team-Only Models (Winner & Totals)
        team_models = _build_models(team_features)
        team_tot_models = _build_totals_models(team_features)
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

        # Team-Only Totals
        p_team_tot = {}
        prob_team_over = {}
        team_tot_sigmas = {}
        for m_name, reg in team_tot_models.items():
            sigma_t = estimate_oof_residual_sigma(
                _build_totals_models(team_features)[m_name],
                X_train_team,
                y_tot_train,
                dates=df_inj.iloc[train_idx]["game_date"],
            )
            team_tot_sigmas[m_name] = sigma_t
            reg.fit(X_train_team, y_tot_train)
            pred_tot = reg.predict(X_test_team)
            p_team_tot[m_name] = pred_tot
            mae_t = float(mean_absolute_error(y_tot_test, pred_tot))

            if has_np:
                z = (pred_tot[non_push_mask] - lines_test[non_push_mask]) / max(sigma_t, 1e-3)
                p_ov = norm.cdf(z)
                prob_team_over[m_name] = norm.cdf((pred_tot - lines_test) / max(sigma_t, 1e-3))
                ou_brier_t = float(brier_score_loss(y_over_test, p_ov))
                ou_acc_t = float(accuracy_score(y_over_test, (p_ov >= 0.5).astype(int)))
                ou_ece_t = float(compute_ece(y_over_test, p_ov))
            else:
                prob_team_over[m_name] = np.full(len(test_idx), 0.5)
                ou_brier_t, ou_acc_t, ou_ece_t = None, None, None

            totals_fold_reports["team_only"][m_name].append({
                "fold": fold_no,
                "total_mae": mae_t,
                "over_under_brier": ou_brier_t,
                "over_under_accuracy": ou_acc_t,
                "over_under_ece": ou_ece_t,
                "residual_sigma": sigma_t,
            })

        p_team_tot_ens = np.mean([p_team_tot["ridge"], p_team_tot["hist_gradient_boosting"], p_team_tot["random_forest"]], axis=0)
        sigma_team_tot_ens = float(np.mean(list(team_tot_sigmas.values())))
        mae_team_ens_t = float(mean_absolute_error(y_tot_test, p_team_tot_ens))
        if has_np:
            z_ens = (p_team_tot_ens[non_push_mask] - lines_test[non_push_mask]) / max(sigma_team_tot_ens, 1e-3)
            p_ov_ens = norm.cdf(z_ens)
            prob_team_over["ensemble"] = norm.cdf((p_team_tot_ens - lines_test) / max(sigma_team_tot_ens, 1e-3))
            ou_brier_team_ens = float(brier_score_loss(y_over_test, p_ov_ens))
            ou_acc_team_ens = float(accuracy_score(y_over_test, (p_ov_ens >= 0.5).astype(int)))
            ou_ece_team_ens = float(compute_ece(y_over_test, p_ov_ens))
        else:
            prob_team_over["ensemble"] = np.full(len(test_idx), 0.5)
            ou_brier_team_ens, ou_acc_team_ens, ou_ece_team_ens = None, None, None

        totals_fold_reports["team_only"]["ensemble"].append({
            "fold": fold_no,
            "total_mae": mae_team_ens_t,
            "over_under_brier": ou_brier_team_ens,
            "over_under_accuracy": ou_acc_team_ens,
            "over_under_ece": ou_ece_team_ens,
            "residual_sigma": sigma_team_tot_ens,
        })

        # 4. Injury-Aware Models (Winner & Totals)
        inj_models = _build_models(inj_features)
        inj_tot_models = _build_totals_models(inj_features)
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

        # Injury-Aware Totals
        p_inj_tot = {}
        prob_inj_over = {}
        inj_tot_sigmas = {}
        for m_name, reg in inj_tot_models.items():
            sigma_inj_t = estimate_oof_residual_sigma(
                _build_totals_models(inj_features)[m_name],
                X_train_inj,
                y_tot_train,
                dates=df_inj.iloc[train_idx]["game_date"],
            )
            inj_tot_sigmas[m_name] = sigma_inj_t
            reg.fit(X_train_inj, y_tot_train)
            pred_tot_inj = reg.predict(X_test_inj)
            p_inj_tot[m_name] = pred_tot_inj
            mae_inj_t = float(mean_absolute_error(y_tot_test, pred_tot_inj))

            if has_np:
                z = (pred_tot_inj[non_push_mask] - lines_test[non_push_mask]) / max(sigma_inj_t, 1e-3)
                p_ov_inj = norm.cdf(z)
                prob_inj_over[m_name] = norm.cdf((pred_tot_inj - lines_test) / max(sigma_inj_t, 1e-3))
                ou_brier_inj = float(brier_score_loss(y_over_test, p_ov_inj))
                ou_acc_inj = float(accuracy_score(y_over_test, (p_ov_inj >= 0.5).astype(int)))
                ou_ece_inj = float(compute_ece(y_over_test, p_ov_inj))
            else:
                prob_inj_over[m_name] = np.full(len(test_idx), 0.5)
                ou_brier_inj, ou_acc_inj, ou_ece_inj = None, None, None

            totals_fold_reports["injury_aware"][m_name].append({
                "fold": fold_no,
                "total_mae": mae_inj_t,
                "over_under_brier": ou_brier_inj,
                "over_under_accuracy": ou_acc_inj,
                "over_under_ece": ou_ece_inj,
                "residual_sigma": sigma_inj_t,
            })

        p_inj_tot_ens = np.mean([p_inj_tot["ridge"], p_inj_tot["hist_gradient_boosting"], p_inj_tot["random_forest"]], axis=0)
        sigma_inj_tot_ens = float(np.mean(list(inj_tot_sigmas.values())))
        mae_inj_ens_t = float(mean_absolute_error(y_tot_test, p_inj_tot_ens))
        if has_np:
            z_inj_ens = (p_inj_tot_ens[non_push_mask] - lines_test[non_push_mask]) / max(sigma_inj_tot_ens, 1e-3)
            p_ov_inj_ens = norm.cdf(z_inj_ens)
            prob_inj_over["ensemble"] = norm.cdf((p_inj_tot_ens - lines_test) / max(sigma_inj_tot_ens, 1e-3))
            ou_brier_inj_ens = float(brier_score_loss(y_over_test, p_ov_inj_ens))
            ou_acc_inj_ens = float(accuracy_score(y_over_test, (p_ov_inj_ens >= 0.5).astype(int)))
            ou_ece_inj_ens = float(compute_ece(y_over_test, p_ov_inj_ens))
        else:
            prob_inj_over["ensemble"] = np.full(len(test_idx), 0.5)
            ou_brier_inj_ens, ou_acc_inj_ens, ou_ece_inj_ens = None, None, None

        totals_fold_reports["injury_aware"]["ensemble"].append({
            "fold": fold_no,
            "total_mae": mae_inj_ens_t,
            "over_under_brier": ou_brier_inj_ens,
            "over_under_accuracy": ou_acc_inj_ens,
            "over_under_ece": ou_ece_inj_ens,
            "residual_sigma": sigma_inj_tot_ens,
        })

        for i, idx in enumerate(test_idx):
            row = df_inj.iloc[idx]
            is_push = bool(np.isclose(y_tot_test[i], lines_test[i])) if pd.notna(lines_test[i]) else False
            act_ou = int(y_tot_test[i] > lines_test[i]) if (pd.notna(lines_test[i]) and not is_push) else None

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
                # Totals extensions
                "actual_total_points": round(float(y_tot_test[i]), 1),
                "total_line": round(float(lines_test[i]), 1) if pd.notna(lines_test[i]) else None,
                "actual_over": act_ou,
                "is_total_push": is_push,
                "proj_total_baseline": round(float(p_tot_mean_base[i]), 2),
                "proj_total_team_only_ridge": round(float(p_team_tot["ridge"][i]), 2),
                "prob_over_team_only_ridge": round(float(prob_team_over["ridge"][i]), 4),
                "proj_total_team_only_ensemble": round(float(p_team_tot_ens[i]), 2),
                "prob_over_team_only_ensemble": round(float(prob_team_over["ensemble"][i]), 4),
                "proj_total_injury_aware_ridge": round(float(p_inj_tot["ridge"][i]), 2),
                "prob_over_injury_aware_ridge": round(float(prob_inj_over["ridge"][i]), 4),
                "proj_total_injury_aware_ensemble": round(float(p_inj_tot_ens[i]), 2),
                "prob_over_injury_aware_ensemble": round(float(prob_inj_over["ensemble"][i]), 4),
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

    summary["totals_folds_metrics"] = totals_fold_reports
    summary["totals_mean_metrics"] = {}
    for cat in ["baselines", "team_only", "injury_aware"]:
        summary["totals_mean_metrics"][cat] = {}
        maj_maes = [f["total_mae"] for f in totals_fold_reports["baselines"]["majority"]]
        maj_ou_briers = [f["over_under_brier"] for f in totals_fold_reports["baselines"]["majority"] if f.get("over_under_brier") is not None]
        for m_name, f_list in totals_fold_reports[cat].items():
            maes = [f["total_mae"] for f in f_list]
            ou_briers = [f["over_under_brier"] for f in f_list if f.get("over_under_brier") is not None]
            ou_eces = [f["over_under_ece"] for f in f_list if f.get("over_under_ece") is not None]
            ou_accs = [f["over_under_accuracy"] for f in f_list if f.get("over_under_accuracy") is not None]

            beats_maj_mae = bool(all(m < mm for m, mm in zip(maes, maj_maes)))
            beats_maj_brier = bool(all(b < mb for b, mb in zip(ou_briers, maj_ou_briers))) if (ou_briers and maj_ou_briers and len(ou_briers) == len(maj_ou_briers)) else False

            summary["totals_mean_metrics"][cat][m_name] = {
                "mean_total_mae": float(np.mean(maes)),
                "mean_over_under_brier": float(np.mean(ou_briers)) if ou_briers else None,
                "mean_over_under_accuracy": float(np.mean(ou_accs)) if ou_accs else None,
                "mean_over_under_ece": float(np.mean(ou_eces)) if ou_eces else None,
                "beats_majority_total_mae_all_folds": beats_maj_mae,
                "beats_majority_over_under_brier_all_folds": beats_maj_brier,
                "mae_by_fold": [round(m, 3) for m in maes],
                "over_under_brier_by_fold": [round(b, 4) if b is not None else None for b in [f.get("over_under_brier") for f in f_list]],
            }

    summary["totals_evaluation"] = {
        "target": "total_points = home_score + away_score",
        "selected_model": "ridge",
        "baseline_majority_mean_mae": summary["totals_mean_metrics"]["baselines"]["majority"]["mean_total_mae"],
        "injury_aware_ridge_mean_mae": summary["totals_mean_metrics"]["injury_aware"]["ridge"]["mean_total_mae"],
        "injury_aware_ridge_mean_brier": summary["totals_mean_metrics"]["injury_aware"]["ridge"]["mean_over_under_brier"],
        "validation_status": "walk_forward_evaluation_complete",
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
