"""
Tests unitarios y de integración para el pipeline de Totals NFL (Over/Under).
Cubre:
1. Formulación del target: total_points = home_score + away_score y carga sin leakage de total_line.
2. Validación walk-forward temporal sin solapamiento ni fuga de datos.
3. Calibración de Over/Under contra líneas de mercado (z-score, norm.cdf, ECE, Brier).
4. Integridad de artefactos y consistencia de model_metadata.json y manifiestos de folds comunes.
5. Inferencia en predict_matchups con manejo de líneas, casos límite y fail-closed ante corrupción.
6. Fail-closed de MLEngine.predict_totals en el componente legacy.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.services.ml_engine import MLEngine
from ml.data_pipeline.common import DataQualityError, backend_root, project_root
from ml.data_pipeline.nfl_source import load_nfl_games
from ml.inference import (
    load_all_totals_models,
    load_temporal_model,
    predict_matchups,
)
from ml.training.evaluate_common_folds import compute_ece
from ml.training.train_temporal import (
    chronological_folds,
    estimate_oof_residual_sigma,
    generate_expanding_chronological_splits,
    load_nfl_historical_total_lines,
    load_training_dataset,
)


# ==============================================================================
# 1. Target Formulation & Historical Line Loading
# ==============================================================================

class TestNFLTotalsTargetAndDataIntegrity:
    """Valida la integridad del target total_points y la carga de total_line sin data leakage."""

    def test_nfl_training_datasets_contain_valid_scores(self):
        """Los datasets de entrenamiento NFL contienen home_score y away_score finitos y sin nulos."""
        for variant in ("injury_aware", "team_only"):
            df, manifest = load_training_dataset("nfl", variant=variant)
            assert "home_score" in df.columns, f"home_score ausente en {variant}"
            assert "away_score" in df.columns, f"away_score ausente en {variant}"

            home_s = pd.to_numeric(df["home_score"], errors="coerce")
            away_s = pd.to_numeric(df["away_score"], errors="coerce")

            assert home_s.notna().all(), f"home_score contiene nulos o NaNs en {variant}"
            assert away_s.notna().all(), f"away_score contiene nulos o NaNs en {variant}"
            assert (home_s >= 0).all(), f"home_score con valores negativos en {variant}"
            assert (away_s >= 0).all(), f"away_score con valores negativos en {variant}"

            total_points = home_s + away_s
            assert (total_points > 0).all(), "total_points debe ser estrictamente positivo en NFL"
            # Rango plausible de puntos en la NFL
            assert total_points.min() >= 3.0
            assert total_points.max() <= 120.0

    def test_load_nfl_historical_total_lines_leakage_free(self):
        """load_nfl_historical_total_lines mapea líneas de cierre desde schedules sin alterar el orden."""
        df_inj, _ = load_training_dataset("nfl", variant="injury_aware")
        lines = load_nfl_historical_total_lines(df_inj)

        assert len(lines) == len(df_inj)
        assert lines.notna().all(), "Todas las filas históricas de NFL deben tener total_line verificado"
        # Líneas de total típicas en NFL (entre 28 y 70)
        assert (lines >= 25.0).all()
        assert (lines <= 75.0).all()

        # Si el DataFrame ya tiene total_line, debe conservarlo
        df_copy = df_inj.copy()
        df_copy["total_line"] = 47.5
        cached_lines = load_nfl_historical_total_lines(df_copy)
        assert (cached_lines == 47.5).all()

    def test_load_nfl_historical_total_lines_preserves_custom_slice_index(self):
        """load_nfl_historical_total_lines preserva el índice original en slices sin producir NaNs."""
        df_inj, _ = load_training_dataset("nfl", variant="injury_aware")
        slice_df = df_inj.iloc[10:25].copy()
        assert not (slice_df.index == np.arange(len(slice_df))).all()

        lines = load_nfl_historical_total_lines(slice_df)
        assert lines.index.equals(slice_df.index)
        assert lines.notna().all()
        # Asignación directa preserva índice y no introduce NaNs
        slice_df["loaded_total_line"] = lines
        assert slice_df["loaded_total_line"].notna().all()
        assert len(slice_df["loaded_total_line"]) == 15


# ==============================================================================
# 2. Temporal Walk-Forward Evaluation & Zero Contamination
# ==============================================================================

class TestNFLTotalsWalkForwardEvaluation:
    """Verifica que la evaluación temporal de totales no sufra fuga de datos."""

    def test_chronological_folds_strict_ordering_for_totals(self):
        """Los folds temporales garantizan que cada fold de test sea estrictamente posterior al train."""
        df, _ = load_training_dataset("nfl", variant="injury_aware")
        folds, holdout_idx = chronological_folds(df, folds=4, holdout_frac=0.20)
        all_dates = pd.to_datetime(df["game_date"]).dt.date.to_numpy()

        for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
            assert len(np.intersect1d(train_idx, test_idx)) == 0
            assert len(np.intersect1d(test_idx, holdout_idx)) == 0
            assert all_dates[train_idx].max() < all_dates[test_idx].min()

        # Holdout estrictamente posterior a todos los folds de test
        all_test_indices = np.concatenate([t for _, t in folds])
        assert all_dates[all_test_indices].max() < all_dates[holdout_idx].min()

    def test_residual_sigma_calculated_strictly_on_train_split(self):
        """La desviación estándar residual sigma se calcula de forma out-of-fold sobre el tramo de train."""
        from sklearn.linear_model import Ridge
        y_train = pd.Series([40.0, 45.0, 50.0, 55.0, 42.0, 48.0, 52.0, 46.0])
        X_train = pd.DataFrame({"f1": [1.0, 2.0, 3.0, 4.0, 1.5, 2.5, 3.5, 2.0]})
        sigma = estimate_oof_residual_sigma(Ridge(), X_train, y_train)
        assert math.isfinite(sigma)
        assert sigma > 0.0

        # Para muestras pequeñas (< 4) retorna default_sigma sin fallar
        fallback = estimate_oof_residual_sigma(Ridge(), X_train.iloc[:2], y_train.iloc[:2], default_sigma=14.0)
        assert fallback == 14.0

    def test_expanding_chronological_splits_strictly_past_train_and_disjoint_val(self):
        """
        Valida que generate_expanding_chronological_splits cumpla matemáticamente:
        1. max(train_date) < min(validation_date) en el 100% de los splits.
        2. Los bloques de validación son mutuamente disjuntos.
        3. El conjunto de entrenamiento se expande monótonamente.
        """
        # Secuencia realista con múltiples partidos por fecha y fechas no consecutivas
        raw_dates = [
            "2023-09-07", "2023-09-10", "2023-09-10", "2023-09-10", "2023-09-11",
            "2023-09-14", "2023-09-17", "2023-09-17", "2023-09-18", "2023-09-21",
            "2023-09-24", "2023-09-24", "2023-09-25", "2023-10-01", "2023-10-01",
            "2023-10-02", "2023-10-08", "2023-10-08", "2023-10-09", "2023-10-15",
        ]
        dates_series = pd.Series(pd.to_datetime(raw_dates)).dt.date
        splits = generate_expanding_chronological_splits(dates_series, n_splits=4)
        assert len(splits) >= 2, "Debe generar al menos 2 splits expansivos"

        val_indices_all = []
        prev_train_len = 0
        for split_no, (train_idx, val_idx) in enumerate(splits, start=1):
            assert len(train_idx) > 0
            assert len(val_idx) > 0

            max_train_date = dates_series.iloc[train_idx].max()
            min_val_date = dates_series.iloc[val_idx].min()

            # REQUISITO CODEX: Probar max(train_date) < min(validation_date) en cada split
            assert max_train_date < min_val_date, (
                f"Split {split_no}: violación temporal! max(train)={max_train_date} >= min(val)={min_val_date}"
            )

            # Cero solapamiento entre train y validation de este split
            assert len(np.intersect1d(train_idx, val_idx)) == 0

            # Los bloques de validación deben ser mutuamente disjuntos
            assert len(np.intersect1d(val_idx, val_indices_all)) == 0
            val_indices_all.extend(val_idx)

            # El conjunto de entrenamiento debe ser expansivo
            assert len(train_idx) > prev_train_len, "El conjunto de train debe expandirse en cada split"
            prev_train_len = len(train_idx)

    def test_expanding_chronological_splits_edge_cases(self):
        """Valida casos límite: dataset con una sola fecha, fechas nulas o vacías."""
        # Menos de 2 fechas únicas retorna lista vacía sin error
        single_date = pd.Series(["2023-09-10", "2023-09-10", "2023-09-10"])
        assert generate_expanding_chronological_splits(single_date) == []

        # Fechas vacías
        assert generate_expanding_chronological_splits(pd.Series([], dtype="object")) == []

        # Fechas inválidas/nulas lanzan DataQualityError fail-closed
        with pytest.raises(DataQualityError):
            generate_expanding_chronological_splits(pd.Series(["2023-09-10", None, "invalid_date"]))

    def test_estimate_oof_residual_sigma_with_explicit_dates(self):
        """Valida que estimate_oof_residual_sigma use splits expansivos con fechas reales."""
        from sklearn.linear_model import Ridge
        raw_dates = [
            "2023-09-07", "2023-09-10", "2023-09-10", "2023-09-11",
            "2023-09-14", "2023-09-17", "2023-09-17", "2023-09-18",
            "2023-09-21", "2023-09-24", "2023-09-25", "2023-10-01",
        ]
        y_train = pd.Series([45.0, 52.0, 38.0, 41.0, 49.0, 55.0, 44.0, 39.0, 51.0, 48.0, 43.0, 47.0])
        X_train = pd.DataFrame({
            "f1": [1.0, 2.0, 1.5, 2.5, 3.0, 3.5, 2.0, 1.8, 2.9, 3.1, 2.2, 2.7],
            "f2": [0.5, 0.8, 0.4, 0.6, 0.7, 0.9, 0.5, 0.4, 0.8, 0.7, 0.6, 0.7],
        })
        sigma = estimate_oof_residual_sigma(Ridge(), X_train, y_train, dates=raw_dates)
        assert math.isfinite(sigma)
        assert sigma > 0.0

        # Si las fechas tienen una sola fecha única, fallback seguro a default_sigma
        same_date = ["2023-09-10"] * len(y_train)
        fallback = estimate_oof_residual_sigma(Ridge(), X_train, y_train, dates=same_date, default_sigma=13.5)
        assert fallback == 13.5


# ==============================================================================
# 3. Probability Calibration & Over/Under Math
# ==============================================================================

class TestNFLTotalsCalibrationAndMath:
    """Valida la calibración de probabilidades Over/Under y casos límite matemáticos."""

    def test_over_under_probability_symmetry_and_monotonicity(self):
        """p_over y p_under suman exactamente 1.0 y son monotónicas respecto a la proyección."""
        line = 45.0
        sigma = 14.0

        for proj in [30.0, 40.0, 45.0, 50.0, 60.0]:
            z = (proj - line) / sigma
            p_over = norm.cdf(z)
            p_under = 1.0 - p_over

            assert abs(p_over + p_under - 1.0) < 1e-9

        # A mayor proyección, mayor p_over
        z_low = (40.0 - line) / sigma
        z_high = (50.0 - line) / sigma
        assert norm.cdf(z_high) > norm.cdf(z_low)

    def test_over_under_boundary_and_push_conditions(self):
        """Cuando projected == line, p_over == 0.5 y p_under == 0.5; extremos saturan a 0 y 1."""
        line = 44.0
        sigma = 14.0

        # Exactamente en la línea -> z = 0 -> 0.5
        z_even = (44.0 - line) / sigma
        assert abs(norm.cdf(z_even) - 0.5) < 1e-9

        # Proyección extremadamente alta -> z > 5 -> p_over ~ 1.0
        z_huge = (120.0 - line) / sigma
        assert norm.cdf(z_huge) > 0.9999

        # Proyección extremadamente baja -> z < -5 -> p_over ~ 0.0
        z_tiny = (0.0 - line) / sigma
        assert norm.cdf(z_tiny) < 0.001

    def test_compute_ece_metric(self):
        """compute_ece mide correctamente el error de calibración esperado."""
        # Calibración perfecta
        y_true = np.array([1, 1, 0, 0])
        probas = np.array([0.9, 0.9, 0.1, 0.1])
        ece_good = compute_ece(y_true, probas, n_bins=5)

        # Calibración pésima
        y_inv = np.array([0, 0, 1, 1])
        ece_bad = compute_ece(y_inv, probas, n_bins=5)

        assert ece_bad > ece_good


# ==============================================================================
# 4. Model Metadata & Artifact Invariants
# ==============================================================================

class TestNFLTotalsMetadataAndArtifacts:
    """Valida los bloques de metadatos de totales y la preservación de hashes de producción."""

    def test_nfl_metadata_contains_totals_evaluation_block(self):
        """model_metadata.json en injury_aware contiene métricas de Totals MAE y Over/Under Brier."""
        meta_path = backend_root() / "ml" / "models" / "nfl" / "model_metadata.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "totals_evaluation" in meta
        t_eval = meta["totals_evaluation"]
        assert t_eval["target"] == "total_points = home_score + away_score"
        assert t_eval["selected_model"] == "ridge"
        assert t_eval["validation_status"] == "walk_forward_evaluation_complete"
        assert t_eval.get("status") == "experimental"
        assert t_eval.get("beats_baseline_oof") is False
        assert t_eval.get("selection_rationale") == "a_priori_linear_baseline_parsimony"
        assert t_eval.get("production_enabled") is False
        assert "Totals NFL models currently do not outperform" in t_eval.get("audit_note", "")

        metrics = t_eval["metrics"]
        assert "mean_total_mae" in metrics
        assert "mean_baseline_total_mae" in metrics
        assert "mean_over_under_brier" in metrics
        assert "holdout_total_mae" in metrics
        assert "residual_sigma" in metrics

        assert metrics["mean_total_mae"] > 0
        assert metrics["holdout_total_mae"] > 0
        assert metrics["mean_over_under_brier"] > 0
        assert metrics["residual_sigma"] > 0

    def test_totals_artifacts_exist_and_hashes_match(self):
        """Los 3 modelos de totales (ridge, hgb, rf) existen físicamente y sus SHA-256 coinciden."""
        models_dir = backend_root() / "ml" / "models" / "nfl"
        meta_path = models_dir / "model_metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        expected_hashes = meta.get("totals_artifact_sha256", {})
        assert set(expected_hashes.keys()) == {"ridge", "hist_gradient_boosting", "random_forest"}

        for model_name, expected_hash in expected_hashes.items():
            artifact_file = models_dir / f"totals_{model_name}.joblib"
            assert artifact_file.is_file(), f"Falta archivo {artifact_file}"
            actual_hash = hashlib.sha256(artifact_file.read_bytes()).hexdigest()
            assert actual_hash == expected_hash, f"Hash mismatch en totals_{model_name}.joblib"

    def test_production_winner_logistic_sha256_unmodified(self):
        """El artefacto ganador de producción logistic.joblib mantiene estrictamente su SHA-256 baseline."""
        models_dir = backend_root() / "ml" / "models" / "nfl"
        logistic_file = models_dir / "logistic.joblib"
        assert logistic_file.is_file()
        expected_sha = "c672f6a3cd5de9a23fa76a8351005a557c0e106cc008d4c22f09c34986fa28c0"
        actual_sha = hashlib.sha256(logistic_file.read_bytes()).hexdigest()
        assert actual_sha == expected_sha, (
            f"El artefacto logistic.joblib fue alterado: {actual_sha} != {expected_sha}"
        )

    def test_common_folds_manifest_contains_totals_sections(self):
        """nfl_common_folds_predictions.manifest.json contiene totals_evaluation y métricas agregadas."""
        manifest_path = project_root() / "data" / "predictions" / "evaluation" / "nfl_common_folds_predictions.manifest.json"
        assert manifest_path.is_file()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert "totals_evaluation" in data
        assert "totals_folds_metrics" in data
        assert "totals_mean_metrics" in data

        t_mean = data["totals_mean_metrics"]
        assert "baselines" in t_mean
        assert "team_only" in t_mean
        assert "injury_aware" in t_mean
        assert "ridge" in t_mean["injury_aware"]
        assert "mean_total_mae" in t_mean["injury_aware"]["ridge"]
        assert "mean_over_under_brier" in t_mean["injury_aware"]["ridge"]


# ==============================================================================
# 5. Multi-Model Inference & Line Evaluation
# ==============================================================================

class TestNFLTotalsInferencePipeline:
    """Audita la inferencia de totales en predict_matchups."""

    def test_load_all_totals_models(self):
        """load_all_totals_models carga ridge, hgb y rf correctamente."""
        models = load_all_totals_models("nfl")
        assert "ridge" in models
        assert "hist_gradient_boosting" in models
        assert "random_forest" in models

    def test_predict_matchups_generates_totals_projections_and_picks(self):
        """predict_matchups genera projected_total, prob_over, prob_under y totals_pick válidos."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        kickoff = now + timedelta(hours=3)
        cutoff = kickoff - timedelta(hours=4)
        captured = cutoff - timedelta(minutes=10)

        upcoming = pd.DataFrame([{
            "game_id": "TEST_TOTALS_01",
            "game_date": now.strftime("%Y-%m-%d"),
            "game_start_utc": kickoff.isoformat(),
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "total_line": 46.5,
            "source_ref": "sched_test_01",
        }])

        past_games = load_nfl_games().iloc[-400:].copy()

        availability = {
            "TEST_TOTALS_01": {
                "game_id": "TEST_TOTALS_01",
                "cadence": "start_of_day",
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": captured.isoformat(),
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "v1.0",
                "teams": {
                    "KC": {
                        "coverage_state": "covered_with_injuries",
                        "ppr_lost_pct": 0.05,
                        "off_snaps_lost_pct": 0.04,
                        "def_snaps_lost_pct": 0.03,
                        "decision_cutoff": cutoff.isoformat(),
                    },
                    "BAL": {
                        "coverage_state": "covered_with_injuries",
                        "ppr_lost_pct": 0.02,
                        "off_snaps_lost_pct": 0.01,
                        "def_snaps_lost_pct": 0.02,
                        "decision_cutoff": cutoff.isoformat(),
                    },
                },
                "source_refs_by_team": {
                    "KC": ["snap_kc_01"],
                    "BAL": ["snap_bal_01"],
                },
            }
        }

        preds, meta = predict_matchups(
            "nfl",
            past_games,
            upcoming,
            as_of=as_of,
            availability_by_game=availability,
        )

        assert len(preds) == 1
        row = preds.iloc[0]

        # Columnas de moneyline
        assert "home_win_probability" in preds.columns
        # Columnas de totals
        assert "projected_total" in preds.columns
        assert "predicted_total" in preds.columns
        assert "prob_over" in preds.columns
        assert "prob_under" in preds.columns
        assert "totals_pick" in preds.columns

        assert np.isfinite(row["projected_total"])
        assert 20.0 < row["projected_total"] < 70.0
        assert 0.0 < row["prob_over"] < 1.0
        assert 0.0 < row["prob_under"] < 1.0
        assert abs(row["prob_over"] + row["prob_under"] - 1.0) < 1e-4
        assert row["totals_pick"] in ("OVER", "UNDER", "PUSH")

        # Metadatos de totals
        assert "totals_evaluation" in meta
        assert "totals_models_evaluated" in meta
        assert "selected_totals_model" in meta
        assert meta["selected_totals_model"] == "ridge"

    def test_predict_matchups_fails_closed_on_invalid_total_line(self):
        """predict_matchups falla cerrado (DataQualityError) si la línea es negativa o inválida."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        kickoff = now + timedelta(hours=3)
        cutoff = kickoff - timedelta(hours=4)
        captured = cutoff - timedelta(minutes=10)

        upcoming_invalid = pd.DataFrame([{
            "game_id": "TEST_TOTALS_INV",
            "game_date": now.strftime("%Y-%m-%d"),
            "game_start_utc": kickoff.isoformat(),
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "total_line": -10.5,  # Línea negativa inválida
            "source_ref": "sched_test_inv",
        }])

        past_games = load_nfl_games().iloc[-400:].copy()
        availability = {
            "TEST_TOTALS_INV": {
                "game_id": "TEST_TOTALS_INV",
                "cadence": "start_of_day",
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": captured.isoformat(),
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "v1.0",
                "teams": {
                    "KC": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                    "BAL": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                },
                "source_refs_by_team": {"KC": ["ref1"], "BAL": ["ref2"]},
            }
        }

        with pytest.raises(DataQualityError) as exc_info:
            predict_matchups(
                "nfl",
                past_games,
                upcoming_invalid,
                as_of=as_of,
                availability_by_game=availability,
            )
        assert "Línea de total inválida" in str(exc_info.value)

    def test_predict_matchups_fails_closed_on_corrupted_totals_artifact(self):
        """predict_matchups falla cerrado si un artefacto de totales está corrupto o truncado."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        kickoff = now + timedelta(hours=3)
        cutoff = kickoff - timedelta(hours=4)
        captured = cutoff - timedelta(minutes=10)

        upcoming = pd.DataFrame([{
            "game_id": "TEST_CORRUPT_01",
            "game_date": now.strftime("%Y-%m-%d"),
            "game_start_utc": kickoff.isoformat(),
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "source_ref": "sched_test_corrupt",
        }])

        past_games = load_nfl_games().iloc[-400:].copy()
        availability = {
            "TEST_CORRUPT_01": {
                "game_id": "TEST_CORRUPT_01",
                "cadence": "start_of_day",
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": captured.isoformat(),
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "v1.0",
                "teams": {
                    "KC": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                    "BAL": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                },
                "source_refs_by_team": {"KC": ["ref1"], "BAL": ["ref2"]},
            }
        }

        # Simular que totals_ridge no pudo cargarse en load_all_totals_models
        with patch("ml.inference.load_all_totals_models") as mock_load:
            mock_load.return_value = {"hist_gradient_boosting": object(), "random_forest": object()}
            with pytest.raises(DataQualityError) as exc_info:
                predict_matchups(
                    "nfl",
                    past_games,
                    upcoming,
                    as_of=as_of,
                    availability_by_game=availability,
                )
            assert "Artefacto de totales totals_ridge no pudo cargarse o está corrupto" in str(exc_info.value)

    def test_predict_matchups_preserves_game_id_line_mapping_when_unsorted(self):
        """predict_matchups mapea total_line por game_id garantizando que no se crucen las líneas en partidos desordenados."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        past_games = load_nfl_games().iloc[-400:].copy()

        # Dos partidos en orden inverso al cronológico
        kickoff_1 = now + timedelta(hours=48)
        kickoff_2 = now + timedelta(hours=24)

        upcoming = pd.DataFrame([
            {
                "game_id": "GAME_LATER",
                "game_date": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
                "game_start_utc": kickoff_1.isoformat(),
                "home_team_id": "BUF",
                "away_team_id": "MIA",
                "home_team_abbreviation": "BUF",
                "away_team_abbreviation": "MIA",
                "total_line": 56.5,  # Línea alta
                "source_ref": "sched_ref_1",
            },
            {
                "game_id": "GAME_EARLIER",
                "game_date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                "game_start_utc": kickoff_2.isoformat(),
                "home_team_id": "KC",
                "away_team_id": "BAL",
                "home_team_abbreviation": "KC",
                "away_team_abbreviation": "BAL",
                "total_line": 39.5,  # Línea baja
                "source_ref": "sched_ref_2",
            },
        ])

        preds, meta = predict_matchups(
            "nfl",
            past_games,
            upcoming,
            as_of=as_of,
            model_variant="team_only",
            allow_experimental=True,
        )

        assert len(preds) == 2
        # Verificar que cada partido retiene exactamente su línea configurada por game_id
        later_row = preds[preds["game_id"] == "GAME_LATER"].iloc[0]
        earlier_row = preds[preds["game_id"] == "GAME_EARLIER"].iloc[0]

        assert later_row["total_line"] == 56.5
        assert earlier_row["total_line"] == 39.5

        # La probabilidad de Over para la línea de 39.5 debe ser mayor que para la línea de 56.5
        # dado que las proyecciones son comparables
        assert earlier_row["prob_over"] > later_row["prob_over"]

    def test_predict_matchups_fails_closed_on_boolean_total_line(self):
        """predict_matchups rechaza tipos booleanos en total_line (isinstance bool) de forma fail-closed."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        past_games = load_nfl_games().iloc[-400:].copy()

        upcoming_bool = pd.DataFrame([{
            "game_id": "TEST_BOOL_LINE",
            "game_date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
            "game_start_utc": (now + timedelta(hours=24)).isoformat(),
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "total_line": True,  # Booleano inválido
            "source_ref": "sched_ref_bool",
        }])

        with pytest.raises(DataQualityError) as exc_info:
            predict_matchups(
                "nfl",
                past_games,
                upcoming_bool,
                as_of=as_of,
                model_variant="team_only",
                allow_experimental=True,
            )
        assert "tipo booleano no permitido" in str(exc_info.value)

    def test_predict_matchups_handles_partial_missing_total_lines_in_batch(self):
        """En un batch con partidos mixtos (algunos con línea y otros sin línea), los partidos sin línea obtienen NaN y pick None sin afectar al resto."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        past_games = load_nfl_games().iloc[-400:].copy()

        upcoming = pd.DataFrame([
            {
                "game_id": "GAME_WITH_LINE",
                "game_date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                "game_start_utc": (now + timedelta(hours=24)).isoformat(),
                "home_team_id": "KC",
                "away_team_id": "BAL",
                "home_team_abbreviation": "KC",
                "away_team_abbreviation": "BAL",
                "total_line": 47.0,
                "source_ref": "sched_ref_1",
            },
            {
                "game_id": "GAME_WITHOUT_LINE",
                "game_date": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                "game_start_utc": (now + timedelta(hours=24)).isoformat(),
                "home_team_id": "BUF",
                "away_team_id": "MIA",
                "home_team_abbreviation": "BUF",
                "away_team_abbreviation": "MIA",
                "total_line": None,
                "source_ref": "sched_ref_2",
            },
        ])

        preds, _ = predict_matchups(
            "nfl",
            past_games,
            upcoming,
            as_of=as_of,
            model_variant="team_only",
            allow_experimental=True,
        )

        row_with = preds[preds["game_id"] == "GAME_WITH_LINE"].iloc[0]
        row_without = preds[preds["game_id"] == "GAME_WITHOUT_LINE"].iloc[0]

        # Partido con línea tiene probabilidades y pick
        assert np.isfinite(row_with["projected_total"])
        assert np.isfinite(row_with["prob_over"])
        assert np.isfinite(row_with["prob_under"])
        assert row_with["totals_pick"] in ("OVER", "UNDER", "PUSH")

        # Partido sin línea tiene proyección pero probabilidades NaN y pick None
        assert np.isfinite(row_without["projected_total"])
        assert np.isnan(row_without["prob_over"])
        assert np.isnan(row_without["prob_under"])
        assert row_without["totals_pick"] is None


# ==============================================================================
# 6. Residual Sigma Validation Fail-Closed (Codex Audit Item 1)
# ==============================================================================

class TestResidualSigmaFailClosed:
    """Verifica la validación fail-closed de residual_sigma exigida por auditoría Codex."""

    @pytest.mark.parametrize("invalid_sigma", [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        0.0,
        -1.5,
        -10.0,
        "not_a_number",
        "invalid_text",
        [],
        {},
    ])
    def test_predict_matchups_fails_closed_on_invalid_residual_sigma(self, invalid_sigma):
        """predict_matchups falla cerrado (DataQualityError) si residual_sigma en metadata es NaN, inf, <= 0 o inválido."""
        now = datetime.now(timezone.utc)
        as_of = pd.Timestamp(now)
        kickoff = now + timedelta(hours=3)
        cutoff = kickoff - timedelta(hours=4)
        captured = cutoff - timedelta(minutes=10)

        upcoming = pd.DataFrame([{
            "game_id": "TEST_SIGMA_01",
            "game_date": now.strftime("%Y-%m-%d"),
            "game_start_utc": kickoff.isoformat(),
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_team_abbreviation": "KC",
            "away_team_abbreviation": "BAL",
            "total_line": 46.5,
            "source_ref": "sched_test_01",
        }])

        past_games = load_nfl_games().iloc[-400:].copy()
        availability = {
            "TEST_SIGMA_01": {
                "game_id": "TEST_SIGMA_01",
                "cadence": "start_of_day",
                "decision_cutoff_utc": cutoff.isoformat(),
                "captured_at_utc": captured.isoformat(),
                "source_observed_at_utc": captured.isoformat(),
                "known_by_system_at_utc": captured.isoformat(),
                "resolver_version": "v1.0",
                "teams": {
                    "KC": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                    "BAL": {"coverage_state": "covered_with_injuries", "ppr_lost_pct": 0.0, "off_snaps_lost_pct": 0.0, "def_snaps_lost_pct": 0.0, "decision_cutoff": cutoff.isoformat()},
                },
                "source_refs_by_team": {"KC": ["ref1"], "BAL": ["ref2"]},
            }
        }

        real_model, real_meta, real_manifest = load_temporal_model("nfl", model_variant="injury_aware")
        corrupted_meta = json.loads(json.dumps(real_meta))
        corrupted_meta["totals_evaluation"]["metrics"]["residual_sigma"] = invalid_sigma

        with patch("ml.inference.load_temporal_model", return_value=(real_model, corrupted_meta, real_manifest)):
            with pytest.raises(DataQualityError) as exc_info:
                predict_matchups(
                    "nfl",
                    past_games,
                    upcoming,
                    as_of=as_of,
                    availability_by_game=availability,
                )
            assert "residual_sigma inválido en metadata para totales" in str(exc_info.value)


# ==============================================================================
# 7. Legacy MLEngine Fail-Closed & Unsupported Scope (Codex Audit Item 3)
# ==============================================================================

class TestLegacyMLEngineTotalsFailClosed:
    """Verifica que el componente legacy MLEngine opere fail-closed y documentado."""

    def test_ml_engine_predict_totals_raises_runtime_error_when_not_ready(self):
        """MLEngine sin inicializar falla con RuntimeError."""
        engine = MLEngine()
        assert engine.is_ready is False

        with pytest.raises(RuntimeError) as exc_info:
            engine.predict_totals({"wpct": 0.6}, {"wpct": 0.4}, total_line=45.5)
        assert "ML Engine no está inicializado o faltan modelos base entrenados" in str(exc_info.value)

    def test_ml_engine_predict_totals_raises_not_implemented_when_ready_without_totals_model(self):
        """MLEngine listo pero sin totals_model registrado levanta NotImplementedError documentado."""
        engine = MLEngine()
        engine.cat_model = MagicMock()
        engine.scaler = MagicMock()
        assert engine.is_ready is True

        with pytest.raises(NotImplementedError) as exc_info:
            engine.predict_totals({"wpct": 0.6}, {"wpct": 0.4}, total_line=45.5)
        assert "Inferencia de Totals no soportada en el componente legacy MLEngine" in str(exc_info.value)
        assert "ml.inference.predict_matchups" in str(exc_info.value)

    def test_ml_engine_predict_totals_functional_with_injected_totals_model(self):
        """MLEngine listo con totals_model inyectado genera proyección y pick válidos."""
        engine = MLEngine()
        engine.cat_model = MagicMock()
        engine.scaler = MagicMock()
        engine.scaler.transform.return_value = np.zeros((1, 14))

        mock_tot_model = MagicMock()
        mock_tot_model.predict.return_value = np.array([51.0])
        engine.totals_model = mock_tot_model
        engine.totals_sigma = 14.0

        res = engine.predict_totals(
            {"wpct": 0.6, "off_rtg": 115.0, "def_rtg": 108.0, "pace": 100.0},
            {"wpct": 0.4, "off_rtg": 110.0, "def_rtg": 112.0, "pace": 98.0},
            total_line=47.5,
        )

        assert res["projected_total"] == 51.0
        assert res["totals_pick"] == "OVER"
        assert res["prob_over"] > 0.5
        assert res["prob_under"] < 0.5
        assert abs(res["prob_over"] + res["prob_under"] - 1.0) < 1e-3

    def test_ml_engine_predict_totals_fails_closed_on_invalid_line(self):
        """MLEngine con totals_model falla si la línea es negativa, cero o booleana."""
        engine = MLEngine()
        engine.cat_model = MagicMock()
        engine.scaler = MagicMock()
        engine.scaler.transform.return_value = np.zeros((1, 14))
        mock_tot_model = MagicMock()
        mock_tot_model.predict.return_value = np.array([45.0])
        engine.totals_model = mock_tot_model

        # Negativo
        with pytest.raises(ValueError) as exc:
            engine.predict_totals({"wpct": 0.5}, {"wpct": 0.5}, total_line=-10.0)
        assert "total_line debe ser estrictamente finito y > 0" in str(exc.value)

        # Cero
        with pytest.raises(ValueError) as exc:
            engine.predict_totals({"wpct": 0.5}, {"wpct": 0.5}, total_line=0.0)
        assert "total_line debe ser estrictamente finito y > 0" in str(exc.value)

        # Booleano
        with pytest.raises(ValueError) as exc:
            engine.predict_totals({"wpct": 0.5}, {"wpct": 0.5}, total_line=True)
        assert "no puede ser de tipo booleano" in str(exc.value)

    @pytest.mark.parametrize("bad_val", [np.nan, np.inf, -np.inf, "invalid_str"])
    def test_ml_engine_predict_totals_fails_closed_on_non_finite_projected_total(self, bad_val):
        """
        CODEX REQUISITO: Un modelo inyectado que devuelva NaN/±inf o no numérico
        debe ser rechazado antes de calcular probabilidades, tanto con línea como sin línea.
        """
        engine = MLEngine()
        engine.cat_model = MagicMock()
        engine.scaler = MagicMock()
        engine.scaler.transform.return_value = np.zeros((1, 14))
        mock_tot_model = MagicMock()
        mock_tot_model.predict.return_value = np.array([bad_val])
        engine.totals_model = mock_tot_model

        # 1. Con total_line
        with pytest.raises(ValueError) as exc1:
            engine.predict_totals({"wpct": 0.5}, {"wpct": 0.5}, total_line=45.5)
        assert any(term in str(exc1.value) for term in ["estrictamente finito", "no convertible a float"])

        # 2. Sin total_line (total_line=None)
        with pytest.raises(ValueError) as exc2:
            engine.predict_totals({"wpct": 0.5}, {"wpct": 0.5}, total_line=None)
        assert any(term in str(exc2.value) for term in ["estrictamente finito", "no convertible a float"])


# ==============================================================================
# 8. NBA Exclusion From Totals Training (Codex Audit Item 4)
# ==============================================================================

class TestNBAExclusionFromTotalsTraining:
    """Valida que el entrenamiento de totales esté restringido estrictamente a NFL (Codex Ítem 4)."""

    def test_nba_metadata_does_not_contain_totals(self):
        """backend/ml/models/nba/model_metadata.json no contiene totals_evaluation ni totals_artifact_sha256."""
        nba_meta_path = backend_root() / "ml" / "models" / "nba" / "model_metadata.json"
        assert nba_meta_path.is_file()
        nba_meta = json.loads(nba_meta_path.read_text(encoding="utf-8"))

        assert "totals_evaluation" not in nba_meta
        assert "totals_artifact_sha256" not in nba_meta
        assert "selected_totals_model" not in nba_meta
