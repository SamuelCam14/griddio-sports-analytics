"""Chronological walk-forward training for point-in-time NBA/NFL datasets."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import glob
import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    roc_auc_score,
)
from sklearn.base import clone
from sklearn.model_selection import cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.data_pipeline.common import DataQualityError, backend_root, project_root, write_manifest
from ml.data_pipeline.temporal_features import feature_columns


def _dataset_stem(sport: str, variant: str) -> str:
    if variant not in {"injury_aware", "team_only"}:
        raise ValueError("variant must be injury_aware or team_only")
    if variant == "team_only" and sport.lower() != "nfl":
        raise ValueError("team_only variant is currently supported only for NFL")
    return f"{sport.lower()}_team_only_training" if variant == "team_only" else f"{sport.lower()}_training"


def load_training_dataset(
    sport: str, processed_root: Path | None = None, variant: str = "injury_aware",
) -> tuple[pd.DataFrame, dict]:
    processed_root = processed_root or project_root() / "data" / "processed"
    stem = _dataset_stem(sport, variant)
    path = processed_root / f"{stem}.csv"
    manifest_path = processed_root / f"{stem}.manifest.json"
    if not path.exists() or not manifest_path.exists():
        raise FileNotFoundError(f"Falta dataset/manifest {path}; ejecuta build_training_datasets.py")
    df = pd.read_csv(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise DataQualityError(f"{path.name}: hash no coincide con el manifest")
    return df, manifest


FORBIDDEN_FEATURE_COLUMNS = frozenset({
    "target_home_win", "home_win", "home_score", "away_score", "is_tie",
    "margin", "actual_margin", "total_points", "total_score", "did_play",
    "game_id", "source", "source_ref", "schedule_source_ref", "feature_source_refs",
    "season", "week", "game_date", "game_start_utc", "home_team_id", "away_team_id",
    "home_team_name", "away_team_name", "home_team_abbreviation", "away_team_abbreviation",
    "feature_cutoff_exclusive", "feature_max_observed_game_date",
})


def validate_dataset(df: pd.DataFrame, features: list[str]) -> None:
    forbidden = FORBIDDEN_FEATURE_COLUMNS.intersection(features)
    if forbidden:
        raise DataQualityError(
            f"Features contienen columnas prohibidas (data leakage/metadatos/targets): {sorted(forbidden)}"
        )
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


from ml.models.bounded_linear import BoundedLogisticRegression


def get_monotonic_constraints(features: list[str] | None) -> list[int] | None:
    """Retorna restricciones monótonas (-1, 0, 1) para HistGradientBoosting.
    
    Garantiza matemáticamente que las lesiones de un equipo (lost_pct) NUNCA
    incrementen su probabilidad de victoria.
    """
    if not features:
        return None
    monotonic_cst = []
    for f in features:
        if f.startswith("home_") and any(k in f for k in ["lost_pct", "points_against"]):
            monotonic_cst.append(-1)
        elif f.startswith("away_") and any(k in f for k in ["lost_pct", "points_against"]):
            monotonic_cst.append(1)
        elif f.startswith("home_") and any(k in f for k in ["win_rate", "avg_margin", "points_for", "off_epa", "off_success"]):
            monotonic_cst.append(1)
        elif f.startswith("away_") and any(k in f for k in ["win_rate", "avg_margin", "points_for", "off_epa", "off_success"]):
            monotonic_cst.append(-1)
        elif f.startswith("diff_") and any(k in f for k in ["win_rate", "avg_margin", "points_for"]):
            monotonic_cst.append(1)
        else:
            monotonic_cst.append(0)
    return monotonic_cst


def _models(features: list[str] | None = None):
    cst = get_monotonic_constraints(features)
    hgb_kwargs = {
        "max_iter": 50,
        "learning_rate": 0.03,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 30,
        "l2_regularization": 10.0,
        "random_state": 42,
    }
    if cst is not None:
        hgb_kwargs["monotonic_cst"] = cst

    return {
        "logistic": make_pipeline(StandardScaler(), BoundedLogisticRegression(C=0.3, feature_names=features)),
        "hist_gradient_boosting": HistGradientBoostingClassifier(**hgb_kwargs),
        "random_forest": RandomForestClassifier(
            n_estimators=100, max_depth=6, min_samples_leaf=5,
            random_state=42,
        ),
    }


def _totals_models(features: list[str] | None = None):
    """Modelos de regresión temporal para predicción de puntos totales."""
    return {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=15.0, random_state=42)),
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


def generate_expanding_chronological_splits(
    dates: pd.Series | np.ndarray | list,
    n_splits: int = 4,
    min_train_dates: int = 2,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    Genera particiones expansivas cronológicas (walk-forward) sobre una secuencia de fechas.
    Garantiza que en cada split se cumpla estrictamente:
        max(dates[train_idx]) < min(dates[val_idx])
    y que los bloques de validación sean mutuamente excluyentes entre sí.
    """
    if len(dates) == 0:
        return []

    parsed_series = pd.Series(pd.to_datetime(dates, errors="coerce"))
    if parsed_series.isna().any():
        raise DataQualityError("Las fechas contienen valores nulos o inválidos")

    all_dates = parsed_series.dt.date.to_numpy()
    unique_dates = np.array(sorted(np.unique(all_dates)))
    n_unique = len(unique_dates)

    if n_unique < 2:
        return []

    actual_min_train = max(1, min(min_train_dates, n_unique - 1))
    first_val_idx = max(actual_min_train, int(n_unique * 0.40))
    if first_val_idx >= n_unique:
        first_val_idx = n_unique - 1

    k = min(n_splits, n_unique - first_val_idx)
    k = max(1, k)

    boundaries = np.unique(np.linspace(first_val_idx, n_unique, k + 1, dtype=int))
    if len(boundaries) < 2:
        return []

    splits = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        val_start_date = unique_dates[start]
        val_end_date = unique_dates[end] if end < n_unique else None

        train_mask = all_dates < val_start_date
        if val_end_date is not None:
            val_mask = (all_dates >= val_start_date) & (all_dates < val_end_date)
        else:
            val_mask = all_dates >= val_start_date

        train_indices = np.flatnonzero(train_mask)
        val_indices = np.flatnonzero(val_mask)

        if len(train_indices) == 0 or len(val_indices) == 0:
            continue

        max_train_date = all_dates[train_indices].max()
        min_val_date = all_dates[val_indices].min()
        if not (max_train_date < min_val_date):
            raise DataQualityError(
                f"Violación de partición temporal expansiva: max(train)={max_train_date} >= min(val)={min_val_date}"
            )

        splits.append((train_indices, val_indices))

    return splits


def estimate_oof_residual_sigma(
    estimator_template,
    X_train: pd.DataFrame | np.ndarray,
    y_train: pd.Series | np.ndarray,
    dates: pd.Series | np.ndarray | None = None,
    default_sigma: float = 14.0,
    n_splits: int = 4,
) -> float:
    """
    Estima la desviación estándar residual de forma estrictamente out-of-fold (OOF)
    dentro del tramo de entrenamiento mediante particiones temporales expansivas (walk-forward).
    Previene la fuga temporal y la subestimación de la varianza residual producida por residuos in-sample.
    Garantiza que para cada partición interna se cumpla max(train_date) < min(val_date).
    """
    n_samples = len(y_train)
    if n_samples < 4:
        return float(default_sigma)

    if dates is None:
        dates = pd.date_range("2020-01-01", periods=n_samples, freq="D")

    try:
        splits = generate_expanding_chronological_splits(dates, n_splits=n_splits)
    except Exception:
        return float(default_sigma)

    if not splits:
        return float(default_sigma)

    all_res = []
    for tr_idx, v_idx in splits:
        if len(tr_idx) == 0 or len(v_idx) == 0:
            continue
        try:
            model = clone(estimator_template)
        except Exception:
            import copy
            model = copy.deepcopy(estimator_template)

        X_tr = X_train.iloc[tr_idx] if hasattr(X_train, "iloc") else X_train[tr_idx]
        y_tr = y_train.iloc[tr_idx] if hasattr(y_train, "iloc") else y_train[tr_idx]
        X_v = X_train.iloc[v_idx] if hasattr(X_train, "iloc") else X_train[v_idx]
        y_v = y_train.iloc[v_idx] if hasattr(y_train, "iloc") else y_train[v_idx]

        try:
            model.fit(X_tr, y_tr)
            preds = model.predict(X_v)
            y_v_arr = np.asarray(y_v, dtype=float)
            preds_arr = np.asarray(preds, dtype=float)
            if np.all(np.isfinite(y_v_arr)) and np.all(np.isfinite(preds_arr)):
                res = y_v_arr - preds_arr
                all_res.extend(res.tolist())
        except Exception:
            continue

    if len(all_res) >= 2:
        res_arr = np.array(all_res, dtype=float)
        sigma = float(np.std(res_arr, ddof=1))
        if math.isfinite(sigma) and sigma > 0:
            return sigma

    return float(default_sigma)


def compute_ece(y_true: np.ndarray, probas: np.ndarray, n_bins: int = 10) -> float:
    """Calcula el Expected Calibration Error (ECE) de probabilidades."""
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


def load_nfl_historical_total_lines(df: pd.DataFrame, raw_root: Path | None = None) -> pd.Series:
    """Carga líneas oficiales de Totals desde schedules de nflverse sin data leakage."""
    if "total_line" in df.columns and df["total_line"].notna().any():
        return pd.Series(pd.to_numeric(df["total_line"], errors="coerce").to_numpy(), index=df.index)
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    sched_files = sorted(glob.glob(str(raw_root / "*" / "schedules_*.parquet")))
    if not sched_files:
        return pd.Series(np.nan, index=df.index)
    sched_df = pd.concat([pd.read_parquet(f) for f in sched_files], ignore_index=True)
    if "total_line" not in sched_df.columns:
        return pd.Series(np.nan, index=df.index)
    sched_dedup = sched_df.drop_duplicates(subset=["game_id"], keep="last")
    merged = pd.merge(df[["game_id"]], sched_dedup[["game_id", "total_line"]], on="game_id", how="left")
    return pd.Series(pd.to_numeric(merged["total_line"], errors="coerce").to_numpy(), index=df.index)


def chronological_folds(df: pd.DataFrame, folds: int = 4, holdout_frac: float = 0.20):
    if folds < 1:
        raise ValueError("folds must be >= 1")
    if not (0.0 < holdout_frac < 1.0):
        raise ValueError("holdout_frac must be in (0.0, 1.0)")
    if "game_id" in df.columns and df["game_id"].duplicated().any():
        raise DataQualityError("Dataset contiene game_id duplicados")

    parsed_dates = pd.to_datetime(df["game_date"], errors="coerce")
    if parsed_dates.isna().any():
        raise DataQualityError("game_date contiene valores nulos o inválidos")

    dates = np.array(sorted(parsed_dates.dt.date.unique()))
    if len(dates) < folds + 2:
        raise DataQualityError(f"Se requieren más fechas únicas para {folds} validaciones temporales")
    holdout_start = int(len(dates) * (1 - holdout_frac))
    cv_dates = dates[:holdout_start]
    first_test = max(2, int(len(cv_dates) * 0.60))
    boundaries = np.linspace(first_test, len(cv_dates), folds + 1, dtype=int)
    result = []
    all_dates = parsed_dates.dt.date.to_numpy()
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        train_mask = all_dates < cv_dates[start]
        end_date = cv_dates[end] if end < len(cv_dates) else dates[holdout_start]
        test_mask = (all_dates >= cv_dates[start]) & (all_dates < end_date)
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue
        result.append((np.flatnonzero(train_mask), np.flatnonzero(test_mask)))
    if len(result) < 3:
        raise DataQualityError("Menos de tres folds temporales utilizables")
    holdout_indices = np.flatnonzero(all_dates >= dates[holdout_start])

    # Assertions de separación temporal estricta y sin solapamiento
    test_indices_all = []
    for fold_no, (train_idx, test_idx) in enumerate(result, 1):
        if len(np.intersect1d(train_idx, test_idx)) > 0:
            raise DataQualityError(f"Fold {fold_no}: solapamiento entre train y test")
        if len(np.intersect1d(test_idx, holdout_indices)) > 0:
            raise DataQualityError(f"Fold {fold_no}: test se solapa con holdout/lockbox")
        if len(np.intersect1d(train_idx, holdout_indices)) > 0:
            raise DataQualityError(f"Fold {fold_no}: train se solapa con holdout/lockbox")
        if not (all_dates[train_idx].max() < all_dates[test_idx].min()):
            raise DataQualityError(f"Fold {fold_no}: observación de train posterior o igual a test")
        test_indices_all.extend(test_idx)

    # Validar que los folds de test no se solapen entre sí
    if len(test_indices_all) != len(np.unique(test_indices_all)):
        raise DataQualityError("Los folds de test no son mutuamente excluyentes entre sí")

    if not (all_dates[test_indices_all].max() < all_dates[holdout_indices].min()):
        raise DataQualityError("Test de folds de validación posterior o igual al holdout")

    return result, holdout_indices


def select_model_from_folds(
    reports: dict[str, dict], *, ensemble_name: str = "ensemble_blend",
) -> dict:
    """Select the ensemble only when it consistently beats logistic out of fold.

    The lockbox is deliberately excluded from selection. Logistic is the
    parsimonious default; an ensemble must beat it on Brier in every common
    fold, beat the majority baseline in every fold, and have lower mean log
    loss across those folds.
    """
    logistic_folds = reports.get("logistic", {}).get("folds", [])
    ensemble_folds = reports.get(ensemble_name, {}).get("folds", [])
    if not logistic_folds or len(logistic_folds) != len(ensemble_folds):
        return {
            "selected_model": "logistic",
            "decision": "single_model_retained",
            "ensemble_evaluated": bool(ensemble_folds),
            "selection_rule": "ensemble_must_dominate_logistic_and_majority_on_common_oof_folds",
            "rationale": "Common chronological folds unavailable or mismatched; retained logistic by parsimony.",
        }

    paired = []
    for logistic, ensemble in zip(logistic_folds, ensemble_folds):
        if logistic.get("fold") != ensemble.get("fold"):
            raise DataQualityError("Logistic y ensemble no comparten los mismos IDs de fold")
        paired.append({
            "fold": logistic["fold"],
            "logistic_brier": logistic["brier"],
            "ensemble_brier": ensemble["brier"],
            "majority_brier": ensemble["majority_baseline_brier"],
            "logistic_log_loss": logistic["log_loss"],
            "ensemble_log_loss": ensemble["log_loss"],
        })

    brier_dominates_logistic = all(x["ensemble_brier"] < x["logistic_brier"] for x in paired)
    beats_majority_all_folds = all(x["ensemble_brier"] < x["majority_brier"] for x in paired)
    mean_log_loss_better = np.mean([x["ensemble_log_loss"] for x in paired]) < np.mean(
        [x["logistic_log_loss"] for x in paired]
    )
    select_ensemble = brier_dominates_logistic and beats_majority_all_folds and mean_log_loss_better
    return {
        "selected_model": "ensemble" if select_ensemble else "logistic",
        "decision": "ensemble_selected" if select_ensemble else "single_model_retained",
        "ensemble_evaluated": True,
        "models": list(reports.keys()),
        "selection_rule": "ensemble_must_dominate_logistic_by_brier_in_every_oof_fold_and_majority_in_every_fold_and_improve_mean_log_loss",
        "checks": {
            "ensemble_brier_better_than_logistic_all_folds": brier_dominates_logistic,
            "ensemble_brier_better_than_majority_all_folds": beats_majority_all_folds,
            "ensemble_mean_log_loss_better_than_logistic": bool(mean_log_loss_better),
        },
        "fold_comparison": paired,
        "rationale": (
            "Ensemble passed all preregistered development-fold criteria."
            if select_ensemble else
            "Retained bounded logistic by parsimony; ensemble did not pass every development-fold criterion."
        ),
    }
def run_training(
    sport: str, data_root: Path | None = None, models_root: Path | None = None,
    variant: str = "injury_aware",
) -> dict:
    sport = sport.lower()
    dataset_stem = _dataset_stem(sport, variant)
    data_root = data_root or project_root() / "data" / "processed"
    models_root = models_root or backend_root() / "ml" / "models" / sport
    if variant == "team_only":
        models_root = models_root / "team_only"
    df, source_manifest = load_training_dataset(sport, data_root, variant)
    stats = source_manifest["feature_metadata"]["game_stats"]
    include_availability = variant != "team_only" and sport.lower() != "nba"
    features = feature_columns(stats, include_availability=include_availability)
    validate_dataset(df, features)
    df = df.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    X = df[features].astype(float)
    y = df["target_home_win"].astype(int)
    folds, holdout_idx = chronological_folds(df)
    reports = {}
    oof_predictions = {name: [] for name in _models(features)}
    holdout_predictions = {}
    fitted_models = {}

    all_indices = np.arange(len(df))
    train_all_cv_idx = np.setdiff1d(all_indices, holdout_idx)

    for name in _models(features):
        fold_results = []
        for fold_no, (train_idx, test_idx) in enumerate(folds, start=1):
            model = _models(features)[name]
            model.fit(X.iloc[train_idx], y.iloc[train_idx])
            probability = model.predict_proba(X.iloc[test_idx])[:, 1]
            oof_predictions[name].append((test_idx, probability))
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
        final_model = _models(features)[name]
        final_model.fit(X.iloc[train_all_cv_idx], y.iloc[train_all_cv_idx])
        fitted_models[name] = final_model

        # Evaluate Holdout
        if len(holdout_idx) > 0:
            prob_holdout = final_model.predict_proba(X.iloc[holdout_idx])[:, 1]
            holdout_predictions[name] = prob_holdout
            pred_holdout = (prob_holdout >= 0.5).astype(int)
            base_prob_holdout = np.full(len(holdout_idx), y.iloc[train_all_cv_idx].mean())
            
            # Calibration computation
            cal_bins = np.linspace(0.0, 1.0, 11)
            bin_idx = np.clip(np.digitize(prob_holdout, cal_bins) - 1, 0, 9)
            ece = 0.0
            y_hold = y.iloc[holdout_idx].to_numpy()
            for b in range(10):
                mask = bin_idx == b
                if np.sum(mask) > 0:
                    bin_acc = float(np.mean(y_hold[mask]))
                    bin_conf = float(np.mean(prob_holdout[mask]))
                    ece += (np.sum(mask) / len(holdout_idx)) * abs(bin_acc - bin_conf)

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
                "expected_calibration_error": float(ece),
            }
        else:
            holdout_metrics = {}

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

    # Evaluate Multi-Model Ensemble Blend
    model_names = list(_models(features).keys())
    if len(model_names) >= 2:
        ensemble_folds = []
        for fold_idx in range(len(folds)):
            test_idx = oof_predictions[model_names[0]][fold_idx][0]
            probs_fold = [oof_predictions[m][fold_idx][1] for m in model_names if m in oof_predictions]
            prob_ens = np.mean(probs_fold, axis=0)
            pred_ens = (prob_ens >= 0.5).astype(int)
            base_prob = np.full(len(test_idx), y.iloc[folds[fold_idx][0]].mean())
            ensemble_folds.append({
                "fold": fold_idx + 1,
                "accuracy": float(accuracy_score(y.iloc[test_idx], pred_ens)),
                "auc": float(roc_auc_score(y.iloc[test_idx], prob_ens)),
                "brier": float(brier_score_loss(y.iloc[test_idx], prob_ens)),
                "log_loss": float(log_loss(y.iloc[test_idx], prob_ens, labels=[0, 1])),
                "majority_baseline_brier": float(brier_score_loss(y.iloc[test_idx], base_prob)),
            })
        
        if len(holdout_idx) > 0:
            probs_hold = [holdout_predictions[m] for m in model_names if m in holdout_predictions]
            prob_ens_holdout = np.mean(probs_hold, axis=0)
            pred_ens_holdout = (prob_ens_holdout >= 0.5).astype(int)
            base_prob_hold = np.full(len(holdout_idx), y.iloc[train_all_cv_idx].mean())

            cal_bins = np.linspace(0.0, 1.0, 11)
            bin_idx = np.clip(np.digitize(prob_ens_holdout, cal_bins) - 1, 0, 9)
            ece = 0.0
            y_hold = y.iloc[holdout_idx].to_numpy()
            for b in range(10):
                mask = bin_idx == b
                if np.sum(mask) > 0:
                    ece += (np.sum(mask) / len(holdout_idx)) * abs(float(np.mean(y_hold[mask])) - float(np.mean(prob_ens_holdout[mask])))

            ens_holdout_metrics = {
                "test_from": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).min().date()),
                "test_through": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).max().date()),
                "test_rows": int(len(holdout_idx)),
                "accuracy": float(accuracy_score(y.iloc[holdout_idx], pred_ens_holdout)),
                "auc": float(roc_auc_score(y.iloc[holdout_idx], prob_ens_holdout)),
                "brier": float(brier_score_loss(y.iloc[holdout_idx], prob_ens_holdout)),
                "log_loss": float(log_loss(y.iloc[holdout_idx], prob_ens_holdout, labels=[0, 1])),
                "majority_baseline_brier": float(brier_score_loss(y.iloc[holdout_idx], base_prob_hold)),
                "expected_calibration_error": float(ece),
            }
        else:
            ens_holdout_metrics = {}

        reports["ensemble_blend"] = {
            "holdout_evaluation": ens_holdout_metrics,
            "folds": ensemble_folds,
            "mean_accuracy": float(np.mean([fold["accuracy"] for fold in ensemble_folds])),
            "mean_auc": float(np.mean([fold["auc"] for fold in ensemble_folds])),
            "mean_brier": float(np.mean([fold["brier"] for fold in ensemble_folds])),
            "mean_log_loss": float(np.mean([fold["log_loss"] for fold in ensemble_folds])),
            "beats_majority_brier_in_all_folds": all(
                fold["brier"] < fold["majority_baseline_brier"] for fold in ensemble_folds
            ),
        }

    # Evaluate Totals (Over/Under) Walk-Forward Pipeline
    has_scores = ("home_score" in df.columns) and ("away_score" in df.columns)
    totals_reports = {}
    totals_artifact_hashes = {}
    selected_totals_model = "ridge"

    if sport.lower() == "nfl" and has_scores:
        y_totals = (
            pd.to_numeric(df["home_score"], errors="coerce") + pd.to_numeric(df["away_score"], errors="coerce")
        ).astype(float)
        if y_totals.isna().any():
            raise DataQualityError("Valores nulos o inválidos en home_score/away_score para total_points")
        total_lines = load_nfl_historical_total_lines(df) if sport.lower() == "nfl" else pd.Series(np.nan, index=df.index)

        totals_model_names = list(_totals_models(features).keys())
        totals_oof_predictions = {name: [] for name in totals_model_names}
        totals_holdout_predictions = {}
        totals_fitted_models = {}

        for t_name in totals_model_names:
            t_fold_results = []
            for fold_no, (train_idx, test_idx) in enumerate(folds, start=1):
                sigma_train = estimate_oof_residual_sigma(
                    _totals_models(features)[t_name],
                    X.iloc[train_idx],
                    y_totals.iloc[train_idx],
                    dates=df.iloc[train_idx]["game_date"],
                )
                t_model = _totals_models(features)[t_name]
                t_model.fit(X.iloc[train_idx], y_totals.iloc[train_idx])

                test_pred = t_model.predict(X.iloc[test_idx])
                totals_oof_predictions[t_name].append((test_idx, test_pred, sigma_train))

                mae_val = float(mean_absolute_error(y_totals.iloc[test_idx], test_pred))
                base_mean = float(y_totals.iloc[train_idx].mean())
                base_mae = float(mean_absolute_error(y_totals.iloc[test_idx], np.full(len(test_idx), base_mean)))

                lines_test = total_lines.iloc[test_idx]
                has_lines = lines_test.notna() & (lines_test > 0)
                if has_lines.any():
                    non_push = has_lines & (y_totals.iloc[test_idx] != lines_test)
                    if non_push.any():
                        y_over_act = (y_totals.iloc[test_idx][non_push] > lines_test[non_push]).astype(int).to_numpy()
                        z_score = (test_pred[non_push] - lines_test[non_push]) / max(sigma_train, 1e-3)
                        p_over = norm.cdf(z_score)
                        ou_brier = float(brier_score_loss(y_over_act, p_over))
                        ou_acc = float(accuracy_score(y_over_act, (p_over >= 0.5).astype(int)))
                        ou_ece = float(compute_ece(y_over_act, p_over))

                        lines_train = total_lines.iloc[train_idx]
                        train_valid_lines = lines_train.notna() & (lines_train > 0)
                        train_np = train_valid_lines & (y_totals.iloc[train_idx] != lines_train)
                        if train_np.any():
                            base_over_rate = float((y_totals.iloc[train_idx][train_np] > lines_train[train_np]).mean())
                        else:
                            base_over_rate = 0.5
                        base_ou_brier = float(brier_score_loss(y_over_act, np.full(len(y_over_act), base_over_rate)))
                    else:
                        ou_brier, ou_acc, ou_ece, base_ou_brier = None, None, None, None
                else:
                    ou_brier, ou_acc, ou_ece, base_ou_brier = None, None, None, None

                t_fold_results.append({
                    "fold": fold_no,
                    "train_through": str(pd.to_datetime(df.iloc[train_idx]["game_date"]).max().date()),
                    "test_from": str(pd.to_datetime(df.iloc[test_idx]["game_date"]).min().date()),
                    "test_through": str(pd.to_datetime(df.iloc[test_idx]["game_date"]).max().date()),
                    "train_rows": int(len(train_idx)), "test_rows": int(len(test_idx)),
                    "total_mae": mae_val,
                    "majority_baseline_total_mae": base_mae,
                    "over_under_brier": ou_brier,
                    "majority_baseline_over_under_brier": base_ou_brier,
                    "over_under_accuracy": ou_acc,
                    "over_under_ece": ou_ece,
                    "residual_sigma": sigma_train,
                })

            t_final_cv = _totals_models(features)[t_name]
            holdout_sigma = estimate_oof_residual_sigma(
                _totals_models(features)[t_name],
                X.iloc[train_all_cv_idx],
                y_totals.iloc[train_all_cv_idx],
                dates=df.iloc[train_all_cv_idx]["game_date"],
            )
            t_final_cv.fit(X.iloc[train_all_cv_idx], y_totals.iloc[train_all_cv_idx])

            if len(holdout_idx) > 0:
                t_hold_pred = t_final_cv.predict(X.iloc[holdout_idx])
                totals_holdout_predictions[t_name] = t_hold_pred
                t_hold_mae = float(mean_absolute_error(y_totals.iloc[holdout_idx], t_hold_pred))
                t_base_mean = float(y_totals.iloc[train_all_cv_idx].mean())
                t_base_mae = float(mean_absolute_error(y_totals.iloc[holdout_idx], np.full(len(holdout_idx), t_base_mean)))

                lines_hold = total_lines.iloc[holdout_idx]
                has_lines_h = lines_hold.notna() & (lines_hold > 0)
                if has_lines_h.any():
                    non_push_h = has_lines_h & (y_totals.iloc[holdout_idx] != lines_hold)
                    if non_push_h.any():
                        y_over_act_h = (y_totals.iloc[holdout_idx][non_push_h] > lines_hold[non_push_h]).astype(int).to_numpy()
                        z_score_h = (t_hold_pred[non_push_h] - lines_hold[non_push_h]) / max(holdout_sigma, 1e-3)
                        p_over_h = norm.cdf(z_score_h)
                        ou_brier_h = float(brier_score_loss(y_over_act_h, p_over_h))
                        ou_acc_h = float(accuracy_score(y_over_act_h, (p_over_h >= 0.5).astype(int)))
                        ou_ece_h = float(compute_ece(y_over_act_h, p_over_h))

                        lines_tr_all = total_lines.iloc[train_all_cv_idx]
                        valid_tr_all = lines_tr_all.notna() & (lines_tr_all > 0)
                        np_tr_all = valid_tr_all & (y_totals.iloc[train_all_cv_idx] != lines_tr_all)
                        if np_tr_all.any():
                            base_rate_h = float((y_totals.iloc[train_all_cv_idx][np_tr_all] > lines_tr_all[np_tr_all]).mean())
                        else:
                            base_rate_h = 0.5
                        base_brier_h = float(brier_score_loss(y_over_act_h, np.full(len(y_over_act_h), base_rate_h)))
                    else:
                        ou_brier_h, ou_acc_h, ou_ece_h, base_brier_h = None, None, None, None
                else:
                    ou_brier_h, ou_acc_h, ou_ece_h, base_brier_h = None, None, None, None

                t_holdout_metrics = {
                    "test_from": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).min().date()),
                    "test_through": str(pd.to_datetime(df.iloc[holdout_idx]["game_date"]).max().date()),
                    "test_rows": int(len(holdout_idx)),
                    "total_mae": t_hold_mae,
                    "baseline_total_mae": t_base_mae,
                    "over_under_brier": ou_brier_h,
                    "baseline_over_under_brier": base_brier_h,
                    "over_under_accuracy": ou_acc_h,
                    "over_under_ece": ou_ece_h,
                    "residual_sigma": holdout_sigma,
                }
            else:
                t_holdout_metrics = {}

            valid_ou_briers = [f["over_under_brier"] for f in t_fold_results if f.get("over_under_brier") is not None]
            valid_base_briers = [f["majority_baseline_over_under_brier"] for f in t_fold_results if f.get("majority_baseline_over_under_brier") is not None]

            totals_reports[t_name] = {
                "holdout_evaluation": t_holdout_metrics,
                "folds": t_fold_results,
                "mean_total_mae": float(np.mean([f["total_mae"] for f in t_fold_results])),
                "mean_baseline_total_mae": float(np.mean([f["majority_baseline_total_mae"] for f in t_fold_results])),
                "mean_over_under_brier": float(np.mean(valid_ou_briers)) if valid_ou_briers else None,
                "mean_baseline_over_under_brier": float(np.mean(valid_base_briers)) if valid_base_briers else None,
            }

        # Multi-model totals ensemble blend
        if len(totals_model_names) >= 2:
            t_ens_folds = []
            for fold_idx in range(len(folds)):
                test_idx = totals_oof_predictions[totals_model_names[0]][fold_idx][0]
                t_preds_fold = [totals_oof_predictions[m][fold_idx][1] for m in totals_model_names]
                t_pred_ens = np.mean(t_preds_fold, axis=0)
                t_sigmas = [totals_oof_predictions[m][fold_idx][2] for m in totals_model_names]
                t_sigma_ens = float(np.mean(t_sigmas))

                mae_ens_val = float(mean_absolute_error(y_totals.iloc[test_idx], t_pred_ens))
                base_total_mean = float(y_totals.iloc[folds[fold_idx][0]].mean())
                base_mae_ens = float(mean_absolute_error(y_totals.iloc[test_idx], np.full(len(test_idx), base_total_mean)))

                lines_test = total_lines.iloc[test_idx]
                has_lines = lines_test.notna() & (lines_test > 0)
                if has_lines.any():
                    non_push = has_lines & (y_totals.iloc[test_idx] != lines_test)
                    if non_push.any():
                        y_over_act = (y_totals.iloc[test_idx][non_push] > lines_test[non_push]).astype(int).to_numpy()
                        z_score = (t_pred_ens[non_push] - lines_test[non_push]) / max(t_sigma_ens, 1e-3)
                        p_over = norm.cdf(z_score)
                        ou_brier = float(brier_score_loss(y_over_act, p_over))
                        ou_acc = float(accuracy_score(y_over_act, (p_over >= 0.5).astype(int)))
                        ou_ece = float(compute_ece(y_over_act, p_over))

                        lines_train = total_lines.iloc[folds[fold_idx][0]]
                        train_valid_lines = lines_train.notna() & (lines_train > 0)
                        train_np = train_valid_lines & (y_totals.iloc[folds[fold_idx][0]] != lines_train)
                        if train_np.any():
                            base_over_rate = float((y_totals.iloc[folds[fold_idx][0]][train_np] > lines_train[train_np]).mean())
                        else:
                            base_over_rate = 0.5
                        base_ou_brier = float(brier_score_loss(y_over_act, np.full(len(y_over_act), base_over_rate)))
                    else:
                        ou_brier, ou_acc, ou_ece, base_ou_brier = None, None, None, None
                else:
                    ou_brier, ou_acc, ou_ece, base_ou_brier = None, None, None, None

                t_ens_folds.append({
                    "fold": fold_idx + 1,
                    "total_mae": mae_ens_val,
                    "majority_baseline_total_mae": base_mae_ens,
                    "over_under_brier": ou_brier,
                    "majority_baseline_over_under_brier": base_ou_brier,
                    "over_under_accuracy": ou_acc,
                    "over_under_ece": ou_ece,
                    "residual_sigma": t_sigma_ens,
                })

            if len(holdout_idx) > 0:
                t_preds_hold = [totals_holdout_predictions[m] for m in totals_model_names]
                t_pred_ens_h = np.mean(t_preds_hold, axis=0)
                t_mae_ens_h = float(mean_absolute_error(y_totals.iloc[holdout_idx], t_pred_ens_h))
                t_base_mean = float(y_totals.iloc[train_all_cv_idx].mean())
                t_base_mae = float(mean_absolute_error(y_totals.iloc[holdout_idx], np.full(len(holdout_idx), t_base_mean)))
                ens_sigma_h = float(np.mean([totals_reports[m]["holdout_evaluation"].get("residual_sigma", 14.0) for m in totals_model_names]))

                lines_hold = total_lines.iloc[holdout_idx]
                has_lines_h = lines_hold.notna() & (lines_hold > 0)
                if has_lines_h.any():
                    non_push_h = has_lines_h & (y_totals.iloc[holdout_idx] != lines_hold)
                    if non_push_h.any():
                        y_over_act_h = (y_totals.iloc[holdout_idx][non_push_h] > lines_hold[non_push_h]).astype(int).to_numpy()
                        z_score_h = (t_pred_ens_h[non_push_h] - lines_hold[non_push_h]) / max(ens_sigma_h, 1e-3)
                        p_over_h = norm.cdf(z_score_h)
                        ou_brier_h = float(brier_score_loss(y_over_act_h, p_over_h))
                        ou_acc_h = float(accuracy_score(y_over_act_h, (p_over_h >= 0.5).astype(int)))
                        ou_ece_h = float(compute_ece(y_over_act_h, p_over_h))

                        lines_tr_all = total_lines.iloc[train_all_cv_idx]
                        valid_tr_all = lines_tr_all.notna() & (lines_tr_all > 0)
                        np_tr_all = valid_tr_all & (y_totals.iloc[train_all_cv_idx] != lines_tr_all)
                        if np_tr_all.any():
                            base_rate_h = float((y_totals.iloc[train_all_cv_idx][np_tr_all] > lines_tr_all[np_tr_all]).mean())
                        else:
                            base_rate_h = 0.5
                        base_brier_h = float(brier_score_loss(y_over_act_h, np.full(len(y_over_act_h), base_rate_h)))
                    else:
                        ou_brier_h, ou_acc_h, ou_ece_h, base_brier_h = None, None, None, None
                else:
                    ou_brier_h, ou_acc_h, ou_ece_h, base_brier_h = None, None, None, None

                ens_t_holdout_metrics = {
                    "total_mae": t_mae_ens_h,
                    "baseline_total_mae": t_base_mae,
                    "over_under_brier": ou_brier_h,
                    "baseline_over_under_brier": base_brier_h,
                    "over_under_accuracy": ou_acc_h,
                    "over_under_ece": ou_ece_h,
                    "residual_sigma": ens_sigma_h,
                }
            else:
                ens_t_holdout_metrics = {}

            valid_ou_ens = [f["over_under_brier"] for f in t_ens_folds if f.get("over_under_brier") is not None]
            valid_base_ens = [f["majority_baseline_over_under_brier"] for f in t_ens_folds if f.get("majority_baseline_over_under_brier") is not None]

            totals_reports["ensemble_blend"] = {
                "holdout_evaluation": ens_t_holdout_metrics,
                "folds": t_ens_folds,
                "mean_total_mae": float(np.mean([f["total_mae"] for f in t_ens_folds])),
                "mean_baseline_total_mae": float(np.mean([f["majority_baseline_total_mae"] for f in t_ens_folds])),
                "mean_over_under_brier": float(np.mean(valid_ou_ens)) if valid_ou_ens else None,
                "mean_baseline_over_under_brier": float(np.mean(valid_base_ens)) if valid_base_ens else None,
            }

        # Fit final totals models on all_indices and persist artifacts
        for t_name in totals_model_names:
            final_t_model = _totals_models(features)[t_name]
            final_t_model.fit(X.iloc[all_indices], y_totals.iloc[all_indices])
            totals_fitted_models[t_name] = final_t_model
            models_root.mkdir(parents=True, exist_ok=True)
            t_art_path = models_root / f"totals_{t_name}.joblib"
            joblib.dump(final_t_model, t_art_path)
            totals_artifact_hashes[t_name] = hashlib.sha256(t_art_path.read_bytes()).hexdigest()

        # Attach totals metrics to the winner model reports for fold-level traceability
        for name in reports:
            mapped_t = selected_totals_model if selected_totals_model in totals_reports else (totals_model_names[0] if totals_model_names else None)
            if mapped_t and mapped_t in totals_reports:
                for idx_f, f_res in enumerate(reports[name].get("folds", [])):
                    if idx_f < len(totals_reports[mapped_t]["folds"]):
                        t_f = totals_reports[mapped_t]["folds"][idx_f]
                        f_res["total_mae"] = t_f["total_mae"]
                        f_res["over_under_brier"] = t_f["over_under_brier"]
                if "holdout_evaluation" in reports[name] and reports[name]["holdout_evaluation"]:
                    reports[name]["holdout_evaluation"]["total_mae"] = totals_reports[mapped_t]["holdout_evaluation"].get("total_mae")
                    reports[name]["holdout_evaluation"]["over_under_brier"] = totals_reports[mapped_t]["holdout_evaluation"].get("over_under_brier")
                reports[name]["mean_total_mae"] = totals_reports[mapped_t]["mean_total_mae"]
                reports[name]["mean_over_under_brier"] = totals_reports[mapped_t]["mean_over_under_brier"]

    # Select from common development folds only; never use lockbox metrics here.
    model_selection_info = select_model_from_folds(reports)
    # The historical holdout has already been inspected and is not confirmatory.
    # Refit artifacts on all available historical data; the new prospective
    # cohort, not this consumed holdout, is the independent evaluation.
    final_fit_idx = all_indices
    for name in model_names:
        final_model = _models(features)[name]
        final_model.fit(X.iloc[final_fit_idx], y.iloc[final_fit_idx])
        fitted_models[name] = final_model
        models_root.mkdir(parents=True, exist_ok=True)
        joblib.dump(final_model, models_root / f"{name}.joblib")

    artifact_hashes = {
        name: hashlib.sha256((models_root / f"{name}.joblib").read_bytes()).hexdigest()
        for name in model_names
    }

    metadata = {
        "sport": sport.upper(),
        "model_version": f"{sport}_team_only_temporal_v1" if variant == "team_only" else f"{sport}_temporal_v1",
        "model_variant": variant,
        "dataset": dataset_stem,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": source_manifest["dataset_sha256"],
        "source_manifest": source_manifest.get("source_manifest_sha256"),
        "sample_count": int(len(df)), "date_from": str(pd.to_datetime(df.game_date).min().date()),
        "date_to": str(pd.to_datetime(df.game_date).max().date()),
        "final_artifact_fit": {
            "rows": int(len(final_fit_idx)),
            "date_from": str(pd.to_datetime(df.iloc[final_fit_idx].game_date).min().date()),
            "date_through": str(pd.to_datetime(df.iloc[final_fit_idx].game_date).max().date()),
            "includes_previously_inspected_holdout": True,
            "holdout_metrics_are_confirmatory": False,
        },
        "evaluation_scope": (
            "OOF folds drive model selection. Historical holdout was previously inspected; "
            "reported holdout metrics are diagnostic for the pre-refit candidate only. "
            "Final artifacts are fit on all available historical rows and require prospective confirmation."
        ),
        "features": features, "evaluation": reports,
        "model_selection": model_selection_info,
        "artifact_sha256": artifact_hashes,
        "selected_artifact_sha256": artifact_hashes[model_selection_info["selected_model"]]
        if model_selection_info["selected_model"] != "ensemble" else None,
        "validation_status": "temporal_evaluation_complete_verified" if variant != "team_only" else "temporal_evaluation_complete_manual_review_required",
        "production_enabled": True if (sport.upper() == "NFL" and variant != "team_only") else False,
        "limitations": [
            "Metrics describe chronological folds on the recorded source snapshots.",
            "Historical betting ROI is not measured without point-in-time historical sportsbook snapshots.",
            (
                "Production inference stays disabled until feature/source parity and calibration are reviewed."
                if variant == "team_only"
                else "Production availability follows the configured project gate; it is not evidence of predictive validation."
            ),
        ],
    }
    if sport.lower() == "nfl" and has_scores and totals_reports:
        mapped_t = selected_totals_model if selected_totals_model in totals_reports else (totals_model_names[0] if totals_model_names else "ridge")
        metadata["totals_evaluation"] = {
            "target": "total_points = home_score + away_score",
            "selected_model": mapped_t,
            "selection_rationale": "a_priori_linear_baseline_parsimony",
            "beats_baseline_oof": False,
            "status": "experimental",
            "production_enabled": False,
            "models": totals_reports,
            "metrics": {
                "mean_total_mae": totals_reports[mapped_t]["mean_total_mae"],
                "mean_baseline_total_mae": totals_reports[mapped_t]["mean_baseline_total_mae"],
                "mean_over_under_brier": totals_reports[mapped_t]["mean_over_under_brier"],
                "mean_baseline_over_under_brier": totals_reports[mapped_t]["mean_baseline_over_under_brier"],
                "holdout_total_mae": totals_reports[mapped_t]["holdout_evaluation"].get("total_mae"),
                "holdout_baseline_total_mae": totals_reports[mapped_t]["holdout_evaluation"].get("baseline_total_mae"),
                "holdout_over_under_brier": totals_reports[mapped_t]["holdout_evaluation"].get("over_under_brier"),
                "holdout_over_under_ece": totals_reports[mapped_t]["holdout_evaluation"].get("over_under_ece"),
                "residual_sigma": totals_reports[mapped_t]["holdout_evaluation"].get("residual_sigma"),
            },
            "validation_status": "walk_forward_evaluation_complete",
            "audit_note": (
                "Totals NFL models currently do not outperform historical/market majority baseline out-of-fold. "
                "Ridge selected strictly as an a priori linear baseline (not justified via holdout/lockbox). "
                "Totals remains in experimental status with production disabled until prospective skill is demonstrated."
            ),
        }
        metadata["totals_artifact_sha256"] = totals_artifact_hashes
        metadata["selected_totals_artifact_sha256"] = totals_artifact_hashes.get(mapped_t)
    write_manifest(models_root / "model_metadata.json", metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    parser.add_argument("--variant", choices=["injury_aware", "team_only"], default="injury_aware")
    args = parser.parse_args()
    sports = ["nba", "nfl"] if args.sport == "all" else [args.sport]
    for sport in sports:
        variant = args.variant if sport == "nfl" else "injury_aware"
        result = run_training(sport, variant=variant)
        print(json.dumps({"sport": result["sport"], "samples": result["sample_count"],
                          "validation_status": result["validation_status"]}, indent=2))


if __name__ == "__main__":
    main()
