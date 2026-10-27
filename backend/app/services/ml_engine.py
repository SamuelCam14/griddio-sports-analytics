"""
Motor de Inferencia de Machine Learning Legacy (ML Engine).

NOTA DE AUDITORÍA Y GOBERNANZA:
Este módulo es un componente experimental/legacy no referenciado por las rutas activas de FastAPI
ni por las tareas Celery en producción (el motor canónico de inferencia es `ml.inference.predict_matchups`).
Los artefactos referenciados aquí (catboost_winner.joblib, xgboost_winner.joblib, logistic_baseline.joblib,
feature_scaler.joblib) no forman parte de los modelos reproducibles checked-in y la metadata raíz
(backend/ml/models/model_metadata.json) documenta un experimento legacy previo sobre `nba_historical_rolling.csv`.
Ante la ausencia de dichos artefactos, MLEngine opera de forma fail-closed: `is_ready` permanece en False y
cualquier invocación a `predict_matchup` levanta `RuntimeError`.
"""
import os
import json
import joblib
from functools import lru_cache
from typing import Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
import structlog

from ml.features.feature_pipeline import CANONICAL_FEATURES, compute_derived_features

logger = structlog.get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "ml", "models")


class MLEngine:
    def __init__(self, models_dir: str = MODELS_DIR):
        self.models_dir = models_dir
        self.cat_model = None
        self.xgb_model = None
        self.lr_model = None
        self.scaler = None
        self.metadata = {}
        self._load_models()

    def _load_models(self) -> None:
        """Carga los modelos serializados y el scaler desde el disco."""
        cat_path = os.path.join(self.models_dir, "catboost_winner.joblib")
        xgb_path = os.path.join(self.models_dir, "xgboost_winner.joblib")
        lr_path = os.path.join(self.models_dir, "logistic_baseline.joblib")
        scaler_path = os.path.join(self.models_dir, "feature_scaler.joblib")
        meta_path = os.path.join(self.models_dir, "model_metadata.json")

        try:
            if os.path.exists(cat_path) and os.path.exists(scaler_path):
                self.cat_model = joblib.load(cat_path)
                self.scaler = joblib.load(scaler_path)
                logger.info("CatBoost and Scaler loaded successfully")
            
            if os.path.exists(xgb_path):
                self.xgb_model = joblib.load(xgb_path)
                logger.info("XGBoost loaded successfully")
                
            if os.path.exists(lr_path):
                self.lr_model = joblib.load(lr_path)
                logger.info("Logistic Baseline loaded successfully")

            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
                logger.info("Model metadata loaded", version=self.metadata.get("model_version"))

        except Exception as e:
            logger.error("Error loading ML models", error=str(e))

    @property
    def is_ready(self) -> bool:
        """Verifica si al menos el modelo principal y el scaler están cargados."""
        return self.cat_model is not None and self.scaler is not None

    def predict_matchup(
        self,
        home_stats: Dict[str, Any],
        visitor_stats: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calcula probabilidades de victoria para un partido entre dos equipos.
        
        Args:
            home_stats: Diccionario de estadísticas del equipo local.
            visitor_stats: Diccionario de estadísticas del equipo visitante.
            context: Información adicional (días de descanso, altitud, etc.)
        """
        if not self.is_ready:
            raise RuntimeError("ML Engine no está inicializado o faltan modelos entrenados.")

        # 1. Combinar estadísticas base
        merged_row: Dict[str, Any] = {
            "H_WPCT": home_stats.get("w_pct") or home_stats.get("wpct", 0.5),
            "V_WPCT": visitor_stats.get("w_pct") or visitor_stats.get("wpct", 0.5),
            "H_LOC_WPCT": home_stats.get("home_w_pct") or home_stats.get("loc_wpct", home_stats.get("wpct", 0.5)),
            "V_LOC_WPCT": visitor_stats.get("road_w_pct") or visitor_stats.get("loc_wpct", visitor_stats.get("wpct", 0.5)),
            "H_L10_WPCT": home_stats.get("l10_w_pct") or home_stats.get("l10", 0.5),
            "V_L10_WPCT": visitor_stats.get("l10_w_pct") or visitor_stats.get("l10", 0.5),
            "H_OFF_RTG": home_stats.get("off_rating") or home_stats.get("off_rtg", 112.0),
            "V_OFF_RTG": visitor_stats.get("off_rating") or visitor_stats.get("off_rtg", 112.0),
            "H_DEF_RTG": home_stats.get("def_rating") or home_stats.get("def_rtg", 112.0),
            "V_DEF_RTG": visitor_stats.get("def_rating") or visitor_stats.get("def_rtg", 112.0),
            "H_NET_RTG": home_stats.get("net_rating") or home_stats.get("net_rtg", 0.0),
            "V_NET_RTG": visitor_stats.get("net_rating") or visitor_stats.get("net_rtg", 0.0),
            "H_PACE": home_stats.get("pace", 99.0),
            "V_PACE": visitor_stats.get("pace", 99.0),
        }

        # Inyectar contexto si existe (rest days, b2b, etc.)
        if context:
            merged_row.update(context)

        # 2. Generar vector de features canónico
        feats_dict = compute_derived_features(merged_row)
        feat_df = pd.DataFrame([{col: feats_dict.get(col, 0.0) for col in CANONICAL_FEATURES}])

        # 3. Escalamiento
        scaled_vector = self.scaler.transform(feat_df)

        # 4. Inferencia en cada modelo disponible
        cat_p = float(self.cat_model.predict_proba(scaled_vector)[0, 1])

        if self.xgb_model:
            xgb_p = float(self.xgb_model.predict_proba(scaled_vector)[0, 1])
        else:
            xgb_p = cat_p

        if self.lr_model:
            lr_p = float(self.lr_model.predict_proba(scaled_vector)[0, 1])
        else:
            lr_p = cat_p

        # 5. Ensamble Ponderado
        ens_home_p = (0.45 * cat_p) + (0.35 * xgb_p) + (0.20 * lr_p)
        ens_home_p = max(0.05, min(0.95, ens_home_p))
        ens_away_p = 1.0 - ens_home_p

        # 6. Confluencia
        home_votes = sum([cat_p >= 0.50, xgb_p >= 0.50, lr_p >= 0.50])
        models_agree = 3 if (home_votes in (3, 0)) else 2

        if models_agree == 3 and abs(ens_home_p - 0.50) >= 0.12:
            tier = "PREMIUM"
        elif abs(ens_home_p - 0.50) >= 0.05:
            tier = "VALUE"
        else:
            tier = "SKIP"

        return {
            "home_win_prob": round(ens_home_p, 4),
            "away_win_prob": round(ens_away_p, 4),
            "confluence_tier": tier,
            "model_agreement": f"{models_agree}/3",
            "models_breakdown": {
                "catboost": round(cat_p, 4),
                "xgboost": round(xgb_p, 4),
                "logistic_regression": round(lr_p, 4),
            },
            "features_summary": {
                "diff_net_rtg": round(feats_dict.get("DIFF_NET_RTG", 0.0), 2),
                "diff_wpct": round(feats_dict.get("DIFF_WPCT", 0.0), 3),
                "is_b2b_disadvantage": feats_dict.get("B2B_DISADVANTAGE", 0.0) != 0,
                "altitude_advantage": feats_dict.get("ALTITUDE_ADVANTAGE", 0.0) == 1.0,
            }
        }

    def predict_totals(
        self,
        home_stats: Dict[str, Any],
        visitor_stats: Dict[str, Any],
        total_line: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calcula proyección de puntos totales y probabilidades calibradas de Over/Under.

        NOTA DE ALCANCE Y GOBERNANZA:
        Este método pertenece al componente experimental legacy MLEngine.
        El pipeline oficial y canónico de Totals NFL reside en `ml.inference.predict_matchups`.
        Falla cerrado si el motor no está inicializado (`RuntimeError`), o levanta
        `NotImplementedError` si no se ha inyectado un modelo de totales para este
        componente legacy. Si se provee un `totals_model` registrado, ejecuta inferencia
        completa y calibrada fail-closed.
        """
        if not self.is_ready:
            raise RuntimeError("ML Engine no está inicializado o faltan modelos base entrenados.")

        totals_model = getattr(self, "totals_model", None)
        if totals_model is None:
            raise NotImplementedError(
                "Inferencia de Totals no soportada en el componente legacy MLEngine sin un modelo de totales registrado. "
                "Para la inferencia oficial de NFL Totals, utilice ml.inference.predict_matchups."
            )

        merged_row: Dict[str, Any] = {
            "H_WPCT": home_stats.get("w_pct") or home_stats.get("wpct", 0.5),
            "V_WPCT": visitor_stats.get("w_pct") or visitor_stats.get("wpct", 0.5),
            "H_LOC_WPCT": home_stats.get("home_w_pct") or home_stats.get("loc_wpct", home_stats.get("wpct", 0.5)),
            "V_LOC_WPCT": visitor_stats.get("road_w_pct") or visitor_stats.get("loc_wpct", visitor_stats.get("wpct", 0.5)),
            "H_L10_WPCT": home_stats.get("l10_w_pct") or home_stats.get("l10", 0.5),
            "V_L10_WPCT": visitor_stats.get("l10_w_pct") or visitor_stats.get("l10", 0.5),
            "H_OFF_RTG": home_stats.get("off_rating") or home_stats.get("off_rtg", 112.0),
            "V_OFF_RTG": visitor_stats.get("off_rating") or visitor_stats.get("off_rtg", 112.0),
            "H_DEF_RTG": home_stats.get("def_rating") or home_stats.get("def_rtg", 112.0),
            "V_DEF_RTG": visitor_stats.get("def_rating") or visitor_stats.get("def_rtg", 112.0),
            "H_NET_RTG": home_stats.get("net_rating") or home_stats.get("net_rtg", 0.0),
            "V_NET_RTG": visitor_stats.get("net_rating") or visitor_stats.get("net_rtg", 0.0),
            "H_PACE": home_stats.get("pace", 99.0),
            "V_PACE": visitor_stats.get("pace", 99.0),
        }
        if context:
            merged_row.update(context)

        feats_dict = compute_derived_features(merged_row)
        feat_df = pd.DataFrame([{col: feats_dict.get(col, 0.0) for col in CANONICAL_FEATURES}])
        scaled_vector = self.scaler.transform(feat_df)

        try:
            raw_pred = totals_model.predict(scaled_vector)
            val = raw_pred[0] if hasattr(raw_pred, "__getitem__") else raw_pred
            if isinstance(val, (bool, np.bool_)):
                raise ValueError(f"projected_total del modelo no puede ser de tipo booleano: {val}")
            projected_total = float(val)
        except (TypeError, ValueError, IndexError) as err:
            raise ValueError(f"totals_model retornó un valor de proyección no convertible a float: {err}") from err

        if not np.isfinite(projected_total):
            raise ValueError(f"projected_total del modelo debe ser estrictamente finito, obtenido: {projected_total}")

        totals_sigma = getattr(self, "totals_sigma", 14.0)
        try:
            if isinstance(totals_sigma, (bool, np.bool_)):
                raise ValueError(f"totals_sigma no puede ser de tipo booleano: {totals_sigma}")
            parsed_sigma = float(totals_sigma)
            if not np.isfinite(parsed_sigma) or parsed_sigma <= 0:
                raise ValueError(f"totals_sigma debe ser finito > 0, obtenido: {totals_sigma}")
            totals_sigma = parsed_sigma
        except (TypeError, ValueError) as err:
            raise ValueError(f"totals_sigma inválido: {totals_sigma} ({err})") from err

        prob_over = None
        prob_under = None
        totals_pick = None

        if total_line is not None:
            if isinstance(total_line, (bool, np.bool_)):
                raise ValueError(f"total_line no puede ser de tipo booleano: {total_line}")
            try:
                line_val = float(total_line)
            except (TypeError, ValueError) as err:
                raise ValueError(f"total_line debe ser numérico: {total_line}") from err
            if not np.isfinite(line_val) or line_val <= 0:
                raise ValueError(f"total_line debe ser estrictamente finito y > 0: {line_val}")

            from scipy.stats import norm
            z = (projected_total - line_val) / totals_sigma
            p_over = round(float(norm.cdf(z)), 4)
            p_under = round(1.0 - p_over, 4)
            prob_over = p_over
            prob_under = p_under
            if p_over > 0.5:
                totals_pick = "OVER"
            elif p_under > 0.5:
                totals_pick = "UNDER"
            else:
                totals_pick = "PUSH"

        return {
            "projected_total": round(projected_total, 2),
            "prob_over": prob_over,
            "prob_under": prob_under,
            "totals_pick": totals_pick,
            "total_line": total_line,
        }


@lru_cache()
def get_ml_engine() -> MLEngine:
    """Singleton getter para el motor ML."""
    return MLEngine()
