"""Fail-closed inference for the temporal NBA/NFL winner models."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
import joblib
import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

from ml.data_pipeline.common import DataQualityError, backend_root, project_root
from ml.models.bounded_linear import BoundedLogisticRegression  # noqa: F401
from ml.data_pipeline.temporal_features import (
    NBA_GAME_STATS, NFL_GAME_STATS, build_matchup_features, feature_columns,
)

SPORT_STATS = {"nba": NBA_GAME_STATS, "nfl": NFL_GAME_STATS}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_temporal_model(
    sport: str, model_name: str = "logistic", *, allow_experimental: bool = False,
    models_root: Path | None = None, processed_root: Path | None = None,
    model_variant: str = "injury_aware",
):
    """Load only an artifact whose dataset, schema and approval state match.

    `allow_experimental` is for offline analysis and must never be enabled by API
    or scheduled production callers.
    """
    sport = sport.lower()
    if sport not in SPORT_STATS:
        raise ValueError(f"Deporte no soportado: {sport}")
    if model_variant not in {"injury_aware", "team_only"}:
        raise ValueError(f"Variante de modelo no soportada: {model_variant}")
    if model_variant == "team_only" and sport != "nfl":
        raise ValueError("La variante team_only solo está disponible para NFL")
    models_root = models_root or backend_root() / "ml" / "models" / sport
    processed_root = processed_root or project_root() / "data" / "processed"
    if model_variant == "team_only":
        models_root = models_root / "team_only"
        dataset_stem = f"{sport}_team_only_training"
    else:
        dataset_stem = f"{sport}_training"
    metadata_path = models_root / "model_metadata.json"
    dataset_path = processed_root / f"{dataset_stem}.csv"
    dataset_manifest_path = processed_root / f"{dataset_stem}.manifest.json"
    artifact_path = models_root / f"{model_name}.joblib"
    for required_path in (metadata_path, dataset_path, dataset_manifest_path, artifact_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"Falta archivo requerido de inferencia: {required_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
    expected_variant = model_variant
    dataset_variant = manifest.get("feature_metadata", {}).get("variant", "injury_aware")
    metadata_variant = metadata.get("model_variant", "injury_aware")
    if dataset_variant != expected_variant or metadata_variant != expected_variant:
        raise DataQualityError(
            f"Variante solicitada {expected_variant} no coincide con dataset/modelo "
            f"({dataset_variant}/{metadata_variant})"
        )
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
    features = feature_columns(
        SPORT_STATS[sport], include_availability=(sport == "nfl" and model_variant != "team_only"),
    )
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


def load_all_ensemble_models(
    sport: str,
    *,
    models_root: Path | None = None,
    model_variant: str = "injury_aware",
) -> dict[str, Any]:
    """Load all distinct models in the ensemble if present."""
    sport = sport.lower()
    models_root = models_root or backend_root() / "ml" / "models" / sport
    if model_variant == "team_only":
        models_root = models_root / "team_only"
    loaded = {}
    for m_name in ("logistic", "hist_gradient_boosting", "random_forest"):
        p = models_root / f"{m_name}.joblib"
        if p.is_file():
            try:
                loaded[m_name] = joblib.load(p)
            except Exception as e:
                logger.warning(f"No se pudo cargar modelo {m_name}: {e}")
    return loaded


def _apply_ensemble(
    models: Mapping[str, Any],
    X: pd.DataFrame,
    df: pd.DataFrame,
    model_metadata: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Aplica inferencia concurrente sobre el diccionario de modelos y genera probabilidades combinadas,
    acuerdo de modelos (1-3) y nivel de confluencia ('HIGH', 'MODERATE', 'NONE').
    La probabilidad principal sigue model_selection.selected_model. Si ese campo
    indica ensemble, combina los modelos válidos con promedio simple, igual que
    el evaluador temporal. La confluencia se conserva como diagnóstico.
    """
    model_probs = {}
    for m_name, m_obj in models.items():
        if hasattr(m_obj, "predict_proba"):
            try:
                p = m_obj.predict_proba(X)[:, 1]
                if np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all():
                    model_probs[m_name] = p
            except Exception as exc:
                logger.warning(f"Fallo predict_proba para {m_name}: {exc}")

    if not model_probs:
        raise DataQualityError("Ningún modelo produjo probabilidades válidas")

    # Inference must use the model/aggregation selected from OOF development
    # folds. Ensemble evaluation and inference both use the same simple mean.
    selection = (model_metadata.get("model_selection") or {}) if isinstance(model_metadata, Mapping) else {}
    selected_model = selection.get("selected_model", "ensemble")
    if selected_model == "ensemble":
        probabilities = np.mean(list(model_probs.values()), axis=0)
    elif selected_model in model_probs:
        probabilities = model_probs[selected_model]
    else:
        raise DataQualityError(
            f"Modelo seleccionado {selected_model!r} no produjo probabilidades válidas"
        )

    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise DataQualityError("El ensamble produjo probabilidades inválidas")

    n_samples = len(probabilities)
    agreements = []
    confluence_tiers = []
    individual_models = []

    for i in range(n_samples):
        row_probs = {
            m: {
                "home": round(float(model_probs[m][i]), 4),
                "away": round(float(1.0 - model_probs[m][i]), 4),
            }
            for m in model_probs
        }
        individual_models.append(row_probs)
        fav_is_home = probabilities[i] >= 0.5
        if fav_is_home:
            agree = sum(1 for m in model_probs if model_probs[m][i] >= 0.5)
        else:
            agree = sum(1 for m in model_probs if model_probs[m][i] < 0.5)
        agreements.append(agree)
        n_mods = len(model_probs)
        if n_mods >= 3:
            if agree == 3 and probabilities[i] >= 0.65:
                confluence_tiers.append("HIGH")
            elif (agree == 3 and probabilities[i] >= 0.55) or (agree == 2 and probabilities[i] >= 0.55):
                confluence_tiers.append("MODERATE")
            else:
                confluence_tiers.append("NONE")
        elif n_mods == 2:
            confluence_tiers.append("HIGH" if (agree == 2 and probabilities[i] >= 0.60) else "NONE")
        else:
            confluence_tiers.append("HIGH" if (agree == 1 and probabilities[i] >= 0.60) else "NONE")

    result_df = df.copy()
    result_df["home_win_probability"] = probabilities
    result_df["away_win_probability"] = 1.0 - probabilities
    result_df["model_agreement"] = agreements
    result_df["confluence_tier"] = confluence_tiers
    result_df["individual_models"] = individual_models
    return result_df


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
    availability_by_game: Mapping[str, Mapping[str, Any]] | None = None,
    model_variant: str = "injury_aware",
    allow_baseline_fallback: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Create per-match probabilities with strict training/inference feature parity.

    NFL availability is an explicit, precomputed input. This function never fetches
    ESPN/Odds data or treats an omitted snapshot as a healthy team.
    """
    sport = sport.lower()
    if model_variant not in {"injury_aware", "team_only"}:
        raise ValueError(f"Variante de modelo no soportada: {model_variant}")
    uses_availability = sport == "nfl" and model_variant == "injury_aware"
    model, metadata, manifest = load_temporal_model(
        sport, model_name, allow_experimental=allow_experimental,
        model_variant=model_variant,
    )
    stats = SPORT_STATS[sport]
    decision_now = pd.Timestamp.now(tz="UTC")
    features, provenance = build_matchup_features(
        completed_games, upcoming_games, stats, window=window,
        minimum_history=minimum_history, as_of=as_of or decision_now,
    )
    
    # NFL has no neutral injury default: each target game needs verified coverage.
    injury_columns = [
        "home_ppr_lost_pct", "home_off_snaps_lost_pct", "home_def_snaps_lost_pct",
        "away_ppr_lost_pct", "away_off_snaps_lost_pct", "away_def_snaps_lost_pct",
    ]
    if uses_availability:
        features[injury_columns] = np.nan
    elif sport != "nfl":
        features[injury_columns] = 0.0
    
    availability_coverage = {
        "has_verified_snapshots": False,
        "mode": "required" if uses_availability else "not_used_by_model",
        "covered_teams": [],
        "source_refs": {},
        "decision_cutoffs_utc": {},
    }
    if uses_availability and not upcoming_games.empty:
        requested_as_of = pd.to_datetime(as_of or decision_now, errors="coerce", utc=True)
        if (
            pd.isna(requested_as_of)
            or requested_as_of < decision_now - timedelta(minutes=15)
            or requested_as_of > decision_now + timedelta(minutes=5)
        ):
            raise DataQualityError(
                "NFL live availability requiere un as_of de ejecución actual (ventana de 15 minutos)"
            )
        if not isinstance(availability_by_game, Mapping):
            if allow_baseline_fallback:
                return predict_matchups(
                    sport, completed_games, upcoming_games,
                    model_name=model_name, window=window, minimum_history=minimum_history,
                    as_of=as_of, allow_experimental=allow_experimental,
                    model_variant="team_only",
                )
            raise DataQualityError("NFL requiere snapshots de availability explícitos por game_id; inference no consulta fuentes externas")

        from app.services.nfl_availability_service import CoverageState

        for idx, row in features.iterrows():
            game_id = str(row.get("game_id") or "").strip()
            kickoff = pd.to_datetime(row.get("game_start_utc"), errors="coerce", utc=True)
            if pd.isna(kickoff):
                raise DataQualityError(f"NFL game {game_id} sin kickoff UTC verificable")
            if kickoff <= decision_now:
                raise DataQualityError(f"NFL game {game_id} ya inició o finalizó; no se acepta availability pregame")
            if not game_id or game_id not in availability_by_game:
                if allow_baseline_fallback:
                    return predict_matchups(
                        sport, completed_games, upcoming_games,
                        model_name=model_name, window=window, minimum_history=minimum_history,
                        as_of=as_of, allow_experimental=allow_experimental,
                        model_variant="team_only",
                    )
                raise DataQualityError(f"NFL availability snapshot ausente para game_id={game_id or 'unknown'}")
            snapshot = availability_by_game[game_id]
            if not isinstance(snapshot, Mapping) or str(snapshot.get("game_id")) != game_id:
                raise DataQualityError(f"NFL availability snapshot con identidad de partido inválida: {game_id}")

            def verified_utc(value: Any, field: str) -> pd.Timestamp:
                parsed = pd.to_datetime(value, errors="coerce", utc=True)
                if pd.isna(parsed):
                    raise DataQualityError(f"NFL availability {game_id} carece de {field} verificable")
                return parsed

            snapshot_cutoff = verified_utc(snapshot.get("decision_cutoff_utc"), "decision_cutoff_utc")
            captured_at = verified_utc(snapshot.get("captured_at_utc"), "captured_at_utc")

            # Cutoff debe ser prepartido (al menos 90 min antes del kickoff) y no en el futuro
            if snapshot_cutoff > kickoff - timedelta(minutes=90):
                raise DataQualityError(f"NFL availability cutoff no coincide con el juego {game_id}: posterior al horizonte prepartido de 90 minutos")
            if captured_at > decision_now + timedelta(minutes=5):
                raise DataQualityError(f"NFL game {game_id} aún está fuera de la ventana de decisión")

            source_obs_val = snapshot.get("source_observed_at_utc")
            source_observed_at = None
            if source_obs_val is not None and str(source_obs_val).strip() not in ("", "None", "null", "nan"):
                source_observed_at = verified_utc(source_obs_val, "source_observed_at_utc")

            if not isinstance(snapshot.get("resolver_version"), str) or not snapshot["resolver_version"].strip():
                raise DataQualityError(f"NFL availability snapshot sin resolver_version para {game_id}")

            cadence = snapshot.get("cadence")
            is_pregame_snap = cadence in ("near_kickoff", "pre_kickoff") or snapshot_cutoff >= kickoff - timedelta(minutes=95)
            if is_pregame_snap:
                # La predicción final puede consumir la captura oficial de 90 minutos antes del kickoff.
                # Se permite captured_at y source_observed_at dentro de la ventana oficial prepartido (hasta cutoff + 5m de tolerancia)
                # siempre que sea estrictamente antes del inicio del juego (kickoff).
                if captured_at > snapshot_cutoff + timedelta(minutes=5) or captured_at >= kickoff:
                    raise DataQualityError(f"NFL availability snapshot prepartido posterior a la ventana de decisión de {game_id}")
                if source_observed_at is not None and (source_observed_at > snapshot_cutoff + timedelta(minutes=5) or source_observed_at >= kickoff):
                    raise DataQualityError(f"NFL availability observación prepartido posterior a la ventana de decisión de {game_id}")
            else:
                # Snapshots no prepartido (start_of_day): deben ser estrictamente anteriores al cutoff
                if captured_at >= snapshot_cutoff or (source_observed_at is not None and source_observed_at >= snapshot_cutoff):
                    raise DataQualityError(f"NFL availability snapshot posterior o igual al cutoff de {game_id}")

            if source_observed_at is not None and captured_at < source_observed_at - timedelta(seconds=1):
                raise DataQualityError(f"NFL availability captura anterior a la observación fuente de {game_id}")

            known_by_system_val = snapshot.get("known_by_system_at_utc") or snapshot.get("captured_at_utc")
            if known_by_system_val:
                known_by_sys = verified_utc(known_by_system_val, "known_by_system_at_utc")
                if known_by_sys > requested_as_of + timedelta(minutes=5):
                    raise DataQualityError(f"NFL availability snapshot aún no conocido por el sistema al momento as_of en {game_id}")

            home_abbr = str(row.get("home_team_abbreviation") or row.get("home_team_name") or row.get("home_team_id", "")).upper()
            away_abbr = str(row.get("away_team_abbreviation") or row.get("away_team_name") or row.get("away_team_id", "")).upper()
            teams = snapshot.get("teams")
            refs_by_team = snapshot.get("source_refs_by_team")
            if not isinstance(teams, Mapping) or set(teams) != {home_abbr, away_abbr}:
                raise DataQualityError(f"NFL availability debe cubrir exactamente {home_abbr} y {away_abbr} para {game_id}")
            if not isinstance(refs_by_team, Mapping) or set(refs_by_team) != {home_abbr, away_abbr}:
                raise DataQualityError(f"NFL availability carece de referencias de fuente por equipo para {game_id}")

            for prefix, team in (("home", home_abbr), ("away", away_abbr)):
                injury = teams[team]
                if not isinstance(injury, Mapping):
                    raise DataQualityError(f"NFL availability inválida para {team}, game {game_id}")
                state = injury.get("coverage_state")
                if state == CoverageState.NO_INJURIES_LISTED.value:
                    logger.info("Equipo omitido en captura ESPN sin lesiones listadas; continuando con modelo base NFL", team=team, game_id=game_id)
                    return predict_matchups(
                        sport, completed_games, upcoming_games,
                        model_name=model_name, window=window, minimum_history=minimum_history,
                        as_of=as_of, allow_experimental=allow_experimental,
                        model_variant="team_only",
                    )
                if state not in (CoverageState.COVERED_HEALTHY.value, CoverageState.COVERED_WITH_INJURIES.value):
                    if allow_baseline_fallback:
                        return predict_matchups(
                            sport, completed_games, upcoming_games,
                            model_name=model_name, window=window, minimum_history=minimum_history,
                            as_of=as_of, allow_experimental=allow_experimental,
                            model_variant="team_only",
                        )
                    raise DataQualityError(f"NFL availability desconocida para {team}, game {game_id}: {state}")
                refs = refs_by_team.get(team)
                if not isinstance(refs, (list, tuple)) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
                    raise DataQualityError(f"NFL availability sin provenance verificable para {team}, game {game_id}")
                if state == CoverageState.COVERED_HEALTHY.value and not injury.get("clean_report_evidence_ref"):
                    raise DataQualityError(f"NFL equipo {team} sin lesionados carece de evidencia explícita de reporte limpio")
                if injury.get("decision_cutoff"):
                    team_cutoff = verified_utc(injury["decision_cutoff"], f"decision_cutoff de {team}")
                    if team_cutoff != snapshot_cutoff:
                        raise DataQualityError(f"NFL availability del equipo {team} tiene cutoff distinto en {game_id}")
                for metric in ("ppr_lost_pct", "off_snaps_lost_pct", "def_snaps_lost_pct"):
                    value = injury.get(metric)
                    if not isinstance(value, (int, float)) or not np.isfinite(value) or not 0.0 <= float(value) <= 1.0:
                        raise DataQualityError(f"NFL métrica {metric} inválida/desconocida para {team}, game {game_id}")
                    features.at[idx, f"{prefix}_{metric}"] = float(value)

            availability_coverage["has_verified_snapshots"] = True
            availability_coverage["covered_teams"] = sorted(
                set(availability_coverage["covered_teams"]) | {home_abbr, away_abbr}
            )
            availability_coverage["decision_cutoffs_utc"][game_id] = snapshot_cutoff.isoformat()
            availability_coverage["source_refs"][game_id] = {
                team: list(refs_by_team[team]) for team in (home_abbr, away_abbr)
            }
    model_features = feature_columns(stats, include_availability=(sport == "nfl" and model_variant != "team_only"))
    if features[model_features].columns.tolist() != model_features:
        raise DataQualityError("El orden de features de inferencia no coincide con el contrato del modelo")
    X = features[model_features].astype(float)
    matrix = X.to_numpy()
    if not np.isfinite(matrix).all():
        raise DataQualityError("Features de inferencia no finitas; no se imputan")
    # Multi-model concurrent inference
    if sport == "nfl":
        all_models = load_all_ensemble_models(sport, model_variant=model_variant)
        all_models[model_name] = model
        expected_hashes = metadata.get("artifact_sha256", {})
        if expected_hashes:
            model_dir = backend_root() / "ml" / "models" / sport
            if model_variant == "team_only":
                model_dir = model_dir / "team_only"
            for artifact_name, expected_hash in expected_hashes.items():
                artifact_file = model_dir / f"{artifact_name}.joblib"
                if artifact_name not in all_models or not artifact_file.is_file():
                    raise DataQualityError(f"Falta artefacto requerido para selección: {artifact_name}")
                if _digest(artifact_file) != expected_hash:
                    raise DataQualityError(f"Hash del artefacto {artifact_name} no coincide con metadata")
        ensemble_df = _apply_ensemble(all_models, X, features, model_metadata=metadata)
        models_evaluated = list(all_models.keys())
    else:
        ensemble_df = _apply_ensemble({model_name: model}, X, features, model_metadata=metadata)
        models_evaluated = [model_name]

    features["home_win_probability"] = ensemble_df["home_win_probability"]
    features["away_win_probability"] = ensemble_df["away_win_probability"]
    features["model_agreement"] = ensemble_df["model_agreement"]
    features["confluence_tier"] = ensemble_df["confluence_tier"]
    features["individual_models"] = ensemble_df["individual_models"]
    features["model_version"] = metadata["model_version"]
    features["model_variant"] = model_variant
    features["used_injury_adjustment"] = (model_variant == "injury_aware")
    features["model_artifact"] = model_name
    features["model_dataset_sha256"] = metadata["dataset_sha256"]
    features["model_artifact_sha256"] = metadata["_artifact_sha256"]
    features["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    features["production_eligible"] = bool(metadata.get("production_enabled"))
    run_metadata = {
        "sport": sport.upper(), "model_version": metadata["model_version"],
        "artifact": model_name, "dataset_sha256": metadata["dataset_sha256"],
        "model_variant": model_variant,
        "used_injury_adjustment": (model_variant == "injury_aware"),
        "artifact_sha256": metadata["_artifact_sha256"],
        "dataset_manifest_sha256": metadata["source_manifest"],
        "as_of": pd.Timestamp(as_of or datetime.now(timezone.utc)).isoformat(),
        "feature_rule": provenance["feature_rule"],
        "window_games": window, "production_eligible": bool(metadata.get("production_enabled")),
        "source_refs": provenance["source_refs"],
        "schedule_source_refs": sorted(features["schedule_source_ref"].dropna().unique().tolist()),
        "skipped_games": provenance["skipped_games"],
        "availability_coverage": availability_coverage,
        "models_evaluated": models_evaluated,
    }
    return features, run_metadata
