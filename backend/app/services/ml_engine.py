"""
Motor de Inferencia de Machine Learning (ML Engine).
Carga los modelos entrenados (CatBoost + XGBoost + Logistic Regression)
y ejecuta predicciones de confluencia con cálculo de probabilidades calibradas.
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


@lru_cache()
def get_ml_engine() -> MLEngine:
    """Singleton getter para el motor ML."""
    return MLEngine()
