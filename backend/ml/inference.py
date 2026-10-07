"""Fail-closed inference for the temporal NBA/NFL winner models."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

from ml.data_pipeline.common import DataQualityError, backend_root, project_root
from ml.data_pipeline.temporal_features import (
    NBA_GAME_STATS, NFL_GAME_STATS, build_matchup_features, feature_columns,
)

SPORT_STATS = {"nba": NBA_GAME_STATS, "nfl": NFL_GAME_STATS}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_temporal_model(
    sport: str, model_name: str = "logistic", *, allow_experimental: bool = False,
    models_root: Path | None = None, processed_root: Path | None = None,
):
    """Load only an artifact whose dataset, schema and approval state match.

    `allow_experimental` is for offline analysis and must never be enabled by API
    or scheduled production callers.
    """
    sport = sport.lower()
    if sport not in SPORT_STATS:
        raise ValueError(f"Deporte no soportado: {sport}")
    models_root = models_root or backend_root() / "ml" / "models" / sport
    processed_root = processed_root or project_root() / "data" / "processed"
    metadata_path = models_root / "model_metadata.json"
    dataset_path = processed_root / f"{sport}_training.csv"
    dataset_manifest_path = processed_root / f"{sport}_training.manifest.json"
    artifact_path = models_root / f"{model_name}.joblib"
    for required_path in (metadata_path, dataset_path, dataset_manifest_path, artifact_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"Falta archivo requerido de inferencia: {required_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
    if not metadata.get("production_enabled") and not allow_experimental:
        raise DataQualityError(f"{sport.upper()}: modelo experimental; producción está deshabilitada")
    dataset_hash = _digest(dataset_path)
    if dataset_hash != manifest.get("dataset_sha256") or dataset_hash != metadata.get("dataset_sha256"):
        raise DataQualityError("El dataset no coincide con los hashes del manifiesto/modelo")
    source_manifest_path = project_root() / manifest["source_manifest"]
    if not source_manifest_path.is_file() or _digest(source_manifest_path) != manifest.get("source_manifest_sha256"):
        raise DataQualityError("El manifiesto de snapshots cambió después de construir el dataset")
    if manifest.get("source_manifest_sha256") != metadata.get("source_manifest"):
        raise DataQualityError("El modelo no referencia el manifiesto de snapshots vigente")
    features = feature_columns(SPORT_STATS[sport])
    if metadata.get("features") != features:
        raise DataQualityError("El schema de features del artefacto no coincide con inferencia")
    model = joblib.load(artifact_path)
    if getattr(model, "n_features_in_", None) != len(features):
        raise DataQualityError("El artefacto no espera el número de features vigente")
    classes = list(getattr(model, "classes_", []))
    if classes != [0, 1]:
        raise DataQualityError(f"El artefacto no es un clasificador binario home_win: {classes}")
    metadata["_artifact_sha256"] = _digest(artifact_path)
    return model, metadata, manifest


def predict_matchups(
    sport: str,
    completed_games: pd.DataFrame,
    upcoming_games: pd.DataFrame,
    *,
    model_name: str = "logistic",
    window: int = 10,
    minimum_history: int = 1,
    as_of: pd.Timestamp | None = None,
    allow_experimental: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Create per-match probabilities with strict training/inference feature parity."""
    sport = sport.lower()
    model, metadata, manifest = load_temporal_model(
        sport, model_name, allow_experimental=allow_experimental
    )
    stats = SPORT_STATS[sport]
    features, provenance = build_matchup_features(
        completed_games, upcoming_games, stats, window=window,
        minimum_history=minimum_history, as_of=as_of,
    )
    
    # Inyectar features de lesiones en vivo
    features['home_ppr_lost_pct'] = 0.0
    features['home_off_snaps_lost_pct'] = 0.0
    features['home_def_snaps_lost_pct'] = 0.0
    features['away_ppr_lost_pct'] = 0.0
    features['away_off_snaps_lost_pct'] = 0.0
    features['away_def_snaps_lost_pct'] = 0.0
    
    if sport == "nfl" and not upcoming_games.empty:
        import structlog
        from app.services.fantasy_injury_service import FantasyInjuryService
        logger = structlog.get_logger(__name__)
        try:
            svc = FantasyInjuryService()
            # Asumimos misma season y week para los upcomings del lote actual
            # Extraer season de upcoming_games
            season = int(upcoming_games.iloc[0]['season'])
            # En NFL el week es extraíble del game_id 2024_01_ARI_BUF o upcoming puede no tener week directamente, 
            # pero podemos obtenerlo del game_id
            def extract_week(gid):
                try:
                    return int(str(gid).split('_')[1])
                except:
                    return 1
            
            for idx, row in features.iterrows():
                week = extract_week(row['game_id'])
                injury_data = svc.get_live_production_lost("nfl", season, week)
                
                # Obtener abbreviation, nflverse use abbreviation
                home_abbr = row.get('home_team_abbreviation') or row.get('home_team_name') or str(row['home_team_id'])
                away_abbr = row.get('away_team_abbreviation') or row.get('away_team_name') or str(row['away_team_id'])
                
                h_inj = injury_data.get(home_abbr, {})
                a_inj = injury_data.get(away_abbr, {})
                
                features.at[idx, 'home_ppr_lost_pct'] = h_inj.get('ppr_lost_pct', 0.0)
                features.at[idx, 'home_off_snaps_lost_pct'] = h_inj.get('off_snaps_lost_pct', 0.0)
                features.at[idx, 'home_def_snaps_lost_pct'] = h_inj.get('def_snaps_lost_pct', 0.0)
                
                features.at[idx, 'away_ppr_lost_pct'] = a_inj.get('ppr_lost_pct', 0.0)
                features.at[idx, 'away_off_snaps_lost_pct'] = a_inj.get('off_snaps_lost_pct', 0.0)
                features.at[idx, 'away_def_snaps_lost_pct'] = a_inj.get('def_snaps_lost_pct', 0.0)
                
        except Exception as e:
            logger.error("Error inyectando live injuries", error=str(e))
    if features[feature_columns(stats)].columns.tolist() != feature_columns(stats):
        raise DataQualityError("El orden de features de inferencia no coincide con el contrato del modelo")
    X = features[feature_columns(stats)].astype(float)
    matrix = X.to_numpy()
    if not np.isfinite(matrix).all():
        raise DataQualityError("Features de inferencia no finitas; no se imputan")
    probabilities = model.predict_proba(X)[:, 1]
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise DataQualityError("El modelo produjo probabilidades inválidas")
    features["home_win_probability"] = probabilities
    features["away_win_probability"] = 1.0 - probabilities
    features["model_version"] = metadata["model_version"]
    features["model_artifact"] = model_name
    features["model_dataset_sha256"] = metadata["dataset_sha256"]
    features["model_artifact_sha256"] = metadata["_artifact_sha256"]
    features["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    features["production_eligible"] = bool(metadata.get("production_enabled"))
    run_metadata = {
        "sport": sport.upper(), "model_version": metadata["model_version"],
        "artifact": model_name, "dataset_sha256": metadata["dataset_sha256"],
        "artifact_sha256": metadata["_artifact_sha256"],
        "dataset_manifest_sha256": metadata["source_manifest"],
        "as_of": pd.Timestamp(as_of or datetime.now(timezone.utc)).isoformat(),
        "feature_rule": provenance["feature_rule"],
        "window_games": window, "production_eligible": bool(metadata.get("production_enabled")),
        "source_refs": provenance["source_refs"],
        "schedule_source_refs": sorted(features["schedule_source_ref"].dropna().unique().tolist()),
        "skipped_games": provenance["skipped_games"],
    }
    return features, run_metadata
