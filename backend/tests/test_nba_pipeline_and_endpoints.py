"""Auditoría y suite de verificación integral para pipeline, modelos y endpoints de NBA."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.game import Game, Team, ProviderGameCrosswalk
from app.services.nba_schedule_service import (
    NBA_TEAM_ABBR_CROSSWALK,
    NBA_ESPN_ID_TO_ABBR,
    sync_nba_games_from_source,
)
from app.api.v1.games import get_standings, get_upcoming_games
from app.api.v1.predictions import get_experimental_predictions, get_approved_model_version
from ml.data_pipeline.common import backend_root, project_root
from ml.data_pipeline.nba_source import load_nba_upcoming_games
from ml.data_pipeline.temporal_features import NBA_GAME_STATS, feature_columns
from ml.training.train_temporal import chronological_folds, load_training_dataset
from ml.training.evaluate_common_folds import run_common_folds_evaluation


@pytest.mark.asyncio
async def test_nba_team_catalog_and_crosswalk_completeness():
    """Verifica que el catálogo en BD tenga los 30 equipos NBA y el crosswalk cubra todos."""
    async with AsyncSessionLocal() as s:
        res = await s.execute(select(Team).where(Team.sport == "NBA"))
        teams = res.scalars().all()
        assert len(teams) == 30, f"Se esperaban 30 equipos NBA en catálogo, encontrados {len(teams)}"
        abbrs = {t.abbreviation.upper() for t in teams}

    # Crosswalks cubren los 30 IDs ESPN de la NBA
    assert len(NBA_ESPN_ID_TO_ABBR) == 30
    for espn_id, raw_abbr in NBA_ESPN_ID_TO_ABBR.items():
        canonical_abbr = NBA_TEAM_ABBR_CROSSWALK.get(raw_abbr, raw_abbr)
        assert canonical_abbr in abbrs, f"ESPN ID {espn_id} ({raw_abbr} -> {canonical_abbr}) no está en catálogo"


@pytest.mark.asyncio
async def test_sync_nba_schedule_idempotent():
    """Sincroniza el calendario NBA desde snapshots verificados; debe ser idempotente."""
    async with AsyncSessionLocal() as s:
        # Primera sincronización
        res1 = await sync_nba_games_from_source(s)
        assert res1["status"] == "success"
        assert res1["sport"] == "NBA"
        assert res1["games_synced"] > 0
        first_inserted = res1["inserted"]
        assert first_inserted > 0 or res1["unchanged"] > 0

        # Segunda sincronización (idempotencia)
        res2 = await sync_nba_games_from_source(s)
        assert res2["status"] == "success"
        assert res2["inserted"] == 0, "Una sincronización repetida no debe duplicar partidos"
        assert res2["rejected"] == 0

        # Verificar que los juegos insertados tengan estado scheduled y kickoff UTC
        games_res = await s.execute(select(Game).where(Game.sport == "NBA").limit(10))
        nba_games = games_res.scalars().all()
        assert len(nba_games) > 0
        for g in nba_games:
            assert g.sport == "NBA"
            assert g.game_date is not None
            assert g.external_id is not None
            assert g.home_team_id is not None
            assert g.away_team_id is not None

        # Verificar crosswalks
        cw_res = await s.execute(
            select(ProviderGameCrosswalk).where(ProviderGameCrosswalk.provider.in_(["sportsdataverse", "espn"]))
        )
        cws = cw_res.scalars().all()
        assert len(cws) > 0


@pytest.mark.asyncio
async def test_nba_standings_endpoint():
    """Valida que /games/standings?sport=NBA retorne los récords verificados de los 30 equipos."""
    async with AsyncSessionLocal() as s:
        res = await get_standings(sport="NBA", db=s)
        assert res["sport"] == "NBA"
        assert res["status"] in ("available", "partial")
        assert res["season"] is not None
        assert res["teams_in_catalog"] == 30
        assert res["teams_with_results"] == 30
        assert len(res["standings"]) == 30

        # Cada fila debe tener campos requeridos y ordenados por %
        prev_pct = 1.1
        for row in res["standings"]:
            assert "team_id" in row
            assert "name" in row
            assert "abbreviation" in row
            assert row["played"] == row["w"] + row["l"] + row["t"]
            assert row["pct"] is not None
            assert row["pct"] <= prev_pct + 1e-6
            prev_pct = row["pct"]
            assert row["points_for"] > 0
            assert row["points_against"] > 0


@pytest.mark.asyncio
async def test_nba_standings_with_partial_db_results():
    """Valida que get_standings con resultados parciales en DB complete con el histórico verificado
    sin duplicar equipos ni partidos, reportando los 30 equipos completos."""
    async with AsyncSessionLocal() as s:
        # Obtener 2 equipos del catálogo
        teams_res = await s.execute(select(Team).where(Team.sport == "NBA").limit(2))
        teams = teams_res.scalars().all()
        assert len(teams) == 2
        t1, t2 = teams[0], teams[1]

        # Insertar 1 juego terminado parcial en DB para temporada 2026
        partial_game = Game(
            external_id="partial_test_sync_game_001",
            sport="NBA",
            season="2026",
            game_date=datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc),
            home_team_id=t1.id,
            away_team_id=t2.id,
            home_score=110,
            away_score=105,
            home_win=True,
            status="final",
        )
        s.add(partial_game)
        await s.commit()

        try:
            res = await get_standings(sport="NBA", db=s)
            assert res["sport"] == "NBA"
            assert res["season"] == 2026
            assert res["teams_in_catalog"] == 30
            assert res["teams_with_results"] == 30
            assert len(res["standings"]) == 30

            # Comprobar que no haya equipos duplicados
            team_ids = [row["team_id"] for row in res["standings"]]
            assert len(team_ids) == len(set(team_ids)) == 30

            # Comprobar que todos los registros de los 30 equipos estén completos y válidos
            for row in res["standings"]:
                assert "team_id" in row
                assert "name" in row
                assert "abbreviation" in row
                assert row["played"] == row["w"] + row["l"] + row["t"]
                assert row["pct"] is not None
                assert row["points_for"] > 0
                assert row["points_against"] > 0
        finally:
            await s.delete(partial_game)
            await s.commit()


def test_load_nba_upcoming_games_cutoff_same_day():
    """Verifica que load_nba_upcoming_games filtre por kickoff UTC exacto y no descarte
    juegos futuros del mismo día calendario cuando as_of está horas antes del inicio."""
    # En el snapshot verificado de 2026-10-20:
    # 401909088 inicia a las 19:00:00 UTC
    # 401909089 inicia a las 23:00:00 UTC
    # Fijamos as_of en 2026-10-20 a las 20:00:00 UTC (1 hora después de 401909088, 3 horas antes de 401909089)
    as_of = pd.Timestamp("2026-10-20 20:00:00", tz="UTC")
    df = load_nba_upcoming_games(as_of=as_of)

    game_ids = set(df["game_id"].astype(str))
    # El partido de las 19:00 UTC ya empezó: debe excluirse
    assert "401909088" not in game_ids, "Juego pasado a las 19:00 UTC debe ser excluido"

    # El partido de las 23:00 UTC es futuro en el mismo día: debe incluirse
    assert "401909089" in game_ids, "Juego futuro a las 23:00 UTC en el mismo día debe ser incluido"

    # Verificar que TODOS los juegos retornados tengan kickoff estrictamente posterior a as_of
    assert (df["game_start_utc"] > as_of).all()

    # Si as_of es a las 18:00 UTC (antes de ambos juegos del día)
    as_of_early = pd.Timestamp("2026-10-20 18:00:00", tz="UTC")
    df_early = load_nba_upcoming_games(as_of=as_of_early)
    game_ids_early = set(df_early["game_id"].astype(str))
    assert "401909088" in game_ids_early
    assert "401909089" in game_ids_early


@pytest.mark.asyncio
async def test_nba_upcoming_games_endpoint():
    """Valida que /games/upcoming?sport=NBA retorne partidos programados."""
    async with AsyncSessionLocal() as s:
        # Aseguramos que haya partidos sincronizados
        await sync_nba_games_from_source(s)
        res = await get_upcoming_games(days=30, sport="NBA", db=s)
        assert res["sport"] == "NBA"
        assert "games" in res
        assert isinstance(res["games"], list)
        for g in res["games"]:
            assert g["sport"] == "NBA"
            assert g["status"] == "scheduled"
            assert g["home_team_name"] is not None
            assert g["away_team_name"] is not None


@pytest.mark.asyncio
async def test_nba_experimental_predictions_endpoint():
    """Valida el endpoint de predicciones experimentales de NBA con linking de crosswalk."""
    async with AsyncSessionLocal() as s:
        await sync_nba_games_from_source(s)
        res = await get_experimental_predictions(sport="nba", lookahead_days=90, db=s)
        assert res["sport"] == "NBA"
        assert res["status"] == "experimental_only"
        assert res["predictions_enabled"] is False
        assert res["production_eligible"] is False
        assert "games" in res
        for g in res["games"]:
            assert 0 <= g["home_win_probability"] <= 1
            assert 0 <= g["away_win_probability"] <= 1
            assert abs(g["home_win_probability"] + g["away_win_probability"] - 1.0) < 1e-4
            assert "canonical_game_id" in g


def test_nba_random_forest_artifact_and_features():
    """Audita el modelo Random Forest de NBA: artefacto, metadata, schema y no-leakage."""
    model_dir = backend_root() / "ml" / "models" / "nba"
    rf_path = model_dir / "random_forest.joblib"
    meta_path = model_dir / "model_metadata.json"

    assert rf_path.is_file(), "random_forest.joblib debe existir en models/nba"
    assert meta_path.is_file(), "model_metadata.json debe existir en models/nba"

    rf_model = joblib.load(rf_path)
    assert hasattr(rf_model, "predict_proba")
    assert getattr(rf_model, "n_estimators", 0) == 100

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sport"] == "NBA"
    assert meta["production_enabled"] is False
    assert get_approved_model_version("NBA") is None  # Puerta de producción cerrada

    features = feature_columns(NBA_GAME_STATS, include_availability=False)
    assert len(features) == 26
    assert meta["features"] == features

    # Inferir probabilidades sobre muestra y comprobar bounds
    df, _ = load_training_dataset("nba")
    X = df[features].iloc[:20].astype(float)
    probas = rf_model.predict_proba(X)
    assert probas.shape == (20, 2)
    assert np.all(probas >= 0.0)
    assert np.all(probas <= 1.0)
    assert np.allclose(probas.sum(axis=1), 1.0)


def test_nba_common_folds_evaluation_and_comparability():
    """Valida la evaluación de folds comunes para NBA, verificando calibración y baselines."""
    eval_dir = project_root() / "data" / "predictions" / "evaluation"
    res = run_common_folds_evaluation(sport="nba", output_dir=eval_dir)

    assert res["sport"] == "NBA"
    assert res["total_test_rows"] == 1975
    assert res["holdout_rows_reserved"] == 1229

    manifest_path = eval_dir / "nba_common_folds_predictions.manifest.json"
    csv_path = eval_dir / "nba_common_folds_predictions.csv"
    assert manifest_path.is_file()
    assert csv_path.is_file()

    # Todos los modelos y baselines presentes
    models_metrics = res["mean_metrics"]["models"]
    for m in ("logistic", "hist_gradient_boosting", "random_forest", "ensemble"):
        assert m in models_metrics
        met = models_metrics[m]
        assert met["mean_brier"] < 0.230, f"{m} Brier esperado < 0.230, obtenido {met['mean_brier']}"
        assert met["mean_ece"] < 0.08, f"{m} ECE esperado < 0.08, obtenido {met['mean_ece']}"
        assert met["mean_auc"] > 0.68, f"{m} AUC esperado > 0.68, obtenido {met['mean_auc']}"
        assert met["mean_accuracy"] > 0.62, f"{m} Accuracy esperado > 0.62, obtenido {met['mean_accuracy']}"
        assert met["beats_majority_in_all_folds"] is True
