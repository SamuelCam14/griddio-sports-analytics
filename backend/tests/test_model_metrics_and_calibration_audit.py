"""Suite de pruebas y auditoría adversarial exhaustiva para TAREA-4:
Evaluación de métricas de rendimiento, calibración de probabilidades, baselines y
reproducibilidad de los modelos predictivos (NFL y NBA), comparabilidad de cohortes,
y comportamiento fail-closed de motores legacy e inferencia concurrente.
"""
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from app.services.ml_engine import MLEngine, get_ml_engine
from ml.data_pipeline.common import DataQualityError, backend_root, project_root
from ml.data_pipeline.nba_source import load_nba_games, load_nba_upcoming_games
from ml.data_pipeline.temporal_features import NBA_GAME_STATS, NFL_GAME_STATS, feature_columns
from ml.inference import _apply_ensemble, load_all_ensemble_models, load_temporal_model, predict_matchups
from ml.training.evaluate_common_folds import compute_ece, run_common_folds_evaluation
from ml.training.train_temporal import load_training_dataset


# ==============================================================================
# 1. Auditoría del Componente Legacy MLEngine y model_metadata.json Raíz
# ==============================================================================

class TestLegacyMLEngineAudit:
    """Audita el comportamiento fail-closed y la naturaleza legacy de MLEngine."""

    def test_legacy_ml_engine_fails_closed_when_artifacts_absent(self):
        """MLEngine debe inicializarse con is_ready=False y fallar cerrado con RuntimeError."""
        engine = MLEngine()
        assert engine.is_ready is False, "MLEngine no debe estar listo cuando faltan joblib files"
        assert engine.cat_model is None
        assert engine.scaler is None

        with pytest.raises(RuntimeError) as exc_info:
            engine.predict_matchup(
                home_stats={"w_pct": 0.60, "off_rating": 115.0},
                visitor_stats={"w_pct": 0.40, "off_rating": 110.0},
            )
        assert "ML Engine no está inicializado o faltan modelos entrenados" in str(exc_info.value)

    def test_root_model_metadata_documented_as_unverified_legacy(self):
        """Verifica que el metadata raíz describa el experimento legacy y no contamine producción."""
        meta_path = backend_root() / "ml" / "models" / "model_metadata.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert meta.get("model_version") == "validated_walk_forward_v1"
        assert meta.get("n_samples") == 2444
        # El dataset fuente declarado no existe físicamente en data/
        legacy_dataset_name = meta.get("dataset_source", "")
        assert "nba_historical_rolling.csv" in legacy_dataset_name
        assert not (project_root() / "data" / "processed" / "nba_historical_rolling.csv").exists()


# ==============================================================================
# 2. Auditoría de Métricas, Calibración y Comparabilidad en NFL
# ==============================================================================

class TestNFLMetricsAndCalibrationAudit:
    """Audita reproducibilidad, parsimonia y comparabilidad de cohortes en NFL."""

    def test_nfl_injury_aware_metadata_and_reproducible_metrics(self):
        """Verifica que las métricas de nfl_temporal_v1 en metadata coincidan exactamente con la evaluación."""
        meta_path = backend_root() / "ml" / "models" / "nfl" / "model_metadata.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert meta["sport"] == "NFL"
        assert meta["model_variant"] == "injury_aware"
        assert meta["sample_count"] == 672
        assert meta["production_enabled"] is True

        # Selección de modelo por parsimonia
        selection = meta["model_selection"]
        assert selection["selected_model"] == "logistic"
        assert selection["decision"] == "single_model_retained"
        assert selection["checks"]["ensemble_brier_better_than_majority_all_folds"] is False
        assert selection["checks"]["ensemble_brier_better_than_logistic_all_folds"] is False

        # Validación del manifiesto de folds comunes para NFL
        eval_manifest_path = project_root() / "data" / "predictions" / "evaluation" / "nfl_common_folds_predictions.manifest.json"
        assert eval_manifest_path.is_file()
        eval_manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))

        # Las métricas OOF en metadata coinciden con las del manifiesto generado
        meta_logistic_briers = [f["brier"] for f in meta["evaluation"]["logistic"]["folds"]]
        manifest_logistic_briers = [f["brier"] for f in eval_manifest["folds_metrics"]["injury_aware"]["logistic"]]
        assert np.allclose(meta_logistic_briers, manifest_logistic_briers, atol=1e-6)

        # En el fold 4, todos los modelos sufren degradación frente a la baseline mayoritaria (Brier > 0.2534)
        fold4_maj_brier = eval_manifest["folds_metrics"]["baselines"]["majority"][3]["brier"]
        assert fold4_maj_brier > 0.250
        for m_name in ("logistic", "hist_gradient_boosting", "random_forest", "ensemble"):
            fold4_m_brier = eval_manifest["folds_metrics"]["injury_aware"][m_name][3]["brier"]
            assert fold4_m_brier > fold4_maj_brier, f"{m_name} debió degradarse en fold 4 frente a baseline"

    def test_nfl_cohort_comparability_demonstration(self):
        """Demuestra que team_only (4412 filas) y injury_aware (672 filas) son cohortes dispares,
        pero sobre la cohorte común de 188 juegos sus Briers son estadísticamente comparables."""
        eval_manifest_path = project_root() / "data" / "predictions" / "evaluation" / "nfl_common_folds_predictions.manifest.json"
        eval_manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))

        team_only_metrics = eval_manifest["mean_metrics"]["team_only"]
        injury_aware_metrics = eval_manifest["mean_metrics"]["injury_aware"]

        # Sobre la cohorte común de 188 juegos, team_only logistic y injury_aware logistic tienen Brier cercano
        brier_to = team_only_metrics["logistic"]["mean_brier"]
        brier_ia = injury_aware_metrics["logistic"]["mean_brier"]
        assert abs(brier_to - brier_ia) < 0.005, f"Briers {brier_to} vs {brier_ia} difieren en más de 0.005"

    def test_nfl_ece_calculation_adversarial_properties(self):
        """Verifica las propiedades matemáticas de compute_ece."""
        # Predicción perfectamente calibrada
        y_true = np.array([1, 1, 0, 0] * 25)
        probas = np.array([0.5, 0.5, 0.5, 0.5] * 25)
        ece_perfect = compute_ece(y_true, probas, n_bins=5)
        assert ece_perfect == 0.0

        # Predicción severamente sobreconfiada (totalmente descalibrada)
        probas_bad = np.array([0.99] * 100)
        y_zero = np.array([0] * 100)
        ece_worst = compute_ece(y_zero, probas_bad, n_bins=10)
        assert ece_worst > 0.90

    def test_nfl_team_only_metadata_consistency_and_limitations(self):
        """Audita que la metadata de team_only declare formalmente producción deshabilitada y limitaciones."""
        meta_path = backend_root() / "ml" / "models" / "nfl" / "team_only" / "model_metadata.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert meta["sport"] == "NFL"
        assert meta["model_variant"] == "team_only"
        assert meta["sample_count"] == 4412
        assert meta["production_enabled"] is False
        assert meta["model_selection"]["selected_model"] == "logistic"
        assert meta["model_selection"]["decision"] == "single_model_retained"
        assert len(meta["features"]) == 26

        limitations = meta.get("limitations", [])
        assert any("Historical betting ROI is not measured" in lim for lim in limitations)
        assert any("Production inference stays disabled" in lim for lim in limitations)

    def test_compute_ece_edge_cases(self):
        """Audita casos borde en compute_ece (array vacío, predicciones idénticas a targets)."""
        # Array vacío
        assert compute_ece(np.array([]), np.array([])) == 0.0

        # Exactamente 1 muestra acertada con p=1.0
        assert compute_ece(np.array([1]), np.array([1.0]), n_bins=5) == 0.0

        # Exactamente 1 muestra fallada con p=1.0
        assert abs(compute_ece(np.array([0]), np.array([1.0]), n_bins=5) - 1.0) < 1e-6


# ==============================================================================
# 3. Auditoría de Inferencia Multi-Modelo y Calibración en NBA
# ==============================================================================

class TestNBAMultiModelInferenceAndCalibrationAudit:
    """Audita inferencia concurrente de 3 modelos, acuerdo, confluencia y calibración NBA."""

    def test_nba_predict_matchups_evaluates_all_three_models_concurrently(self):
        """predict_matchups('nba') debe evaluar concurrentemente logistic, hgb y random_forest."""
        as_of = pd.Timestamp("2026-10-20T20:00:00Z")
        past_games = load_nba_games()
        target_games = load_nba_upcoming_games(as_of=as_of).iloc[:3].copy()

        preds, meta = predict_matchups(
            "nba",
            past_games,
            target_games,
            as_of=as_of,
            allow_experimental=True,
        )

        assert meta["models_evaluated"] == ["logistic", "hist_gradient_boosting", "random_forest"]
        assert "individual_models" in preds.columns
        row_indiv = preds["individual_models"].iloc[0]
        assert "logistic" in row_indiv
        assert "hist_gradient_boosting" in row_indiv
        assert "random_forest" in row_indiv

        # Cada modelo individual debe tener home y away en [0, 1] sumando 1.0
        for m_name, probs in row_indiv.items():
            assert 0.0 <= probs["home"] <= 1.0
            assert 0.0 <= probs["away"] <= 1.0
            assert abs(probs["home"] + probs["away"] - 1.0) < 1e-3

        # model_agreement entre 1 y 3
        agreement = preds["model_agreement"].iloc[0]
        assert agreement in (1, 2, 3)

        # confluence_tier es HIGH, MODERATE o NONE
        tier = preds["confluence_tier"].iloc[0]
        assert tier in ("HIGH", "MODERATE", "NONE")

        # Probabilidad principal proviene del modelo seleccionado en metadata (logistic)
        p_main = preds["home_win_probability"].iloc[0]
        p_log = row_indiv["logistic"]["home"]
        assert abs(p_main - p_log) < 1e-4

    def test_nba_tampered_model_hash_fails_closed(self):
        """Si el hash de un modelo en metadata no coincide con el archivo en disco, predict_matchups falla cerrado."""
        as_of = pd.Timestamp("2026-10-20T20:00:00Z")
        past_games = load_nba_games()
        target_games = load_nba_upcoming_games(as_of=as_of).iloc[:3].copy()

        meta_path = backend_root() / "ml" / "models" / "nba" / "model_metadata.json"
        real_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tampered_meta = copy.deepcopy(real_meta)
        tampered_meta["artifact_sha256"]["random_forest"] = "0000000000000000000000000000000000000000000000000000000000000000"

        # Mock load_temporal_model para inyectar metadata alterada
        with pytest.MonkeyPatch.context() as mp:
            from ml.inference import load_temporal_model as orig_load
            def mock_load(*args, **kwargs):
                m, meta, man = orig_load(*args, **kwargs)
                return m, tampered_meta, man

            mp.setattr("ml.inference.load_temporal_model", mock_load)

            with pytest.raises(DataQualityError) as exc_info:
                predict_matchups(
                    "nba",
                    past_games,
                    target_games,
                    as_of=as_of,
                    allow_experimental=True,
                )
            assert "Hash del artefacto random_forest no coincide con metadata" in str(exc_info.value)

    def test_nba_common_folds_metrics_consistency(self):
        """Verifica que las métricas de evaluación de folds comunes de NBA superen a la baseline en todos los folds."""
        manifest_path = project_root() / "data" / "predictions" / "evaluation" / "nba_common_folds_predictions.manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        models_metrics = manifest["mean_metrics"]["models"]
        for m_name in ("logistic", "hist_gradient_boosting", "random_forest", "ensemble"):
            m = models_metrics[m_name]
            assert m["beats_majority_in_all_folds"] is True, f"{m_name} debió superar baseline en todos los folds"
            assert m["mean_brier"] < 0.230
            assert m["mean_ece"] < 0.08
            assert m["mean_auc"] > 0.68
            assert m["mean_accuracy"] > 0.62

    def test_nba_corrupted_artifact_fails_closed(self):
        """Si un artefacto requerido no puede cargarse (corrupto/truncado), predict_matchups falla cerrado."""
        as_of = pd.Timestamp("2026-10-20T20:00:00Z")
        past_games = load_nba_games()
        target_games = load_nba_upcoming_games(as_of=as_of).iloc[:2].copy()

        # Simular que random_forest no pudo cargarse en load_all_ensemble_models
        with pytest.MonkeyPatch.context() as mp:
            from ml.inference import load_all_ensemble_models as orig_load_all
            def mock_load_all(sport, **kwargs):
                loaded = orig_load_all(sport, **kwargs)
                loaded.pop("random_forest", None)  # Omitido por fallo en joblib.load
                return loaded

            mp.setattr("ml.inference.load_all_ensemble_models", mock_load_all)

            with pytest.raises(DataQualityError) as exc_info:
                predict_matchups(
                    "nba",
                    past_games,
                    target_games,
                    as_of=as_of,
                    allow_experimental=True,
                )
            assert "Artefacto random_forest no pudo cargarse o está corrupto" in str(exc_info.value)

    def test_nba_predict_proba_failure_fails_closed(self):
        """Si un modelo en el ensamble produce NaNs o falla en predict_proba, predict_matchups falla cerrado."""
        as_of = pd.Timestamp("2026-10-20T20:00:00Z")
        past_games = load_nba_games()
        target_games = load_nba_upcoming_games(as_of=as_of).iloc[:2].copy()

        # Inyectar un modelo roto que devuelve NaNs en predict_proba
        with pytest.MonkeyPatch.context() as mp:
            from ml.inference import load_all_ensemble_models as orig_load_all
            def mock_load_all(sport, **kwargs):
                loaded = orig_load_all(sport, **kwargs)
                class BrokenModel:
                    def predict_proba(self, X):
                        return np.full((len(X), 2), np.nan)
                if "random_forest" in loaded:
                    loaded["random_forest"] = BrokenModel()
                return loaded

            mp.setattr("ml.inference.load_all_ensemble_models", mock_load_all)

            with pytest.raises(DataQualityError) as exc_info:
                predict_matchups(
                    "nba",
                    past_games,
                    target_games,
                    as_of=as_of,
                    allow_experimental=True,
                )
            assert "El modelo requerido random_forest no produjo probabilidades válidas en inferencia" in str(exc_info.value)


# ==============================================================================
# 4. Auditoría de Reportes Formales de Calidad de Datos
# ==============================================================================

class TestQualityReportsAudit:
    """Audita los reportes de calidad de datos formalizados."""

    def test_quality_report_json_audits_all_three_training_datasets(self):
        """data/quality_report.json debe contener las entradas auditadas de los 3 datasets sin clobbering."""
        report_path = project_root() / "data" / "quality_report.json"
        assert report_path.is_file()
        report = json.loads(report_path.read_text(encoding="utf-8"))

        datasets = report.get("datasets", {})
        assert "nba" in datasets
        assert "nfl" in datasets
        assert "nfl_team_only" in datasets

        # NBA: 6137 filas
        assert datasets["nba"]["total_rows"] == 6137
        assert datasets["nba"]["hash_valid"] is True
        assert datasets["nba"]["duplicates"] == 0

        # NFL injury_aware: 672 filas
        assert datasets["nfl"]["total_rows"] == 672
        assert datasets["nfl"]["hash_valid"] is True
        assert datasets["nfl"]["duplicates"] == 0

        # NFL team_only: 4412 filas
        assert datasets["nfl_team_only"]["total_rows"] == 4412
        assert datasets["nfl_team_only"]["hash_valid"] is True
        assert datasets["nfl_team_only"]["duplicates"] == 0
