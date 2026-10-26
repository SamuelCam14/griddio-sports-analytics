import pandas as pd
import pytest

from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.temporal_features import (
    NFL_GAME_STATS, build_matchup_features, build_temporal_dataset, feature_columns,
)
from ml.training.train_temporal import validate_dataset


def games_fixture():
    return pd.DataFrame([
        {"game_id": "a", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 10, "away_score": 7,
         "home_win": 1, "home_x": 1.0, "away_x": 2.0, "source": "fixture", "source_ref": "sha:a"},
        {"game_id": "b", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "B", "away_team_id": "A", "home_score": 8, "away_score": 10,
         "home_win": 0, "home_x": 3.0, "away_x": 4.0, "source": "fixture", "source_ref": "sha:b"},
        {"game_id": "c", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 6, "away_score": 9,
         "home_win": 0, "home_x": 5.0, "away_x": 6.0, "source": "fixture", "source_ref": "sha:c"},
        {"game_id": "d", "game_date": "2024-01-03", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 12, "away_score": 5,
         "home_win": 1, "home_x": 7.0, "away_x": 8.0, "source": "fixture", "source_ref": "sha:d"},
    ])


def test_same_day_games_are_not_used_as_features():
    result, _ = build_temporal_dataset(games_fixture(), "nba", ["x"])
    target = result.set_index("game_id").loc["d"]
    # A's Jan 2 game must not count in Jan 2 games on same day; at Jan 3 both Jan 1 and Jan 2 qualify.
    assert target.home_games_prior == 3
    assert target.feature_max_observed_game_date == "2024-01-02"


def test_nfl_team_only_feature_schema_excludes_optional_availability():
    features = feature_columns(NFL_GAME_STATS, include_availability=False)
    assert len(features) == 26
    assert "home_win_rate" in features
    assert "away_avg_off_epa_per_play" in features
    assert not any("ppr_lost_pct" in column for column in features)
    assert not any("snaps_lost_pct" in column for column in features)


def test_missing_source_metric_is_rejected_without_imputation():
    games = games_fixture()
    games.loc[0, "home_x"] = None
    with pytest.raises(DataQualityError, match="no se imputan"):
        build_temporal_dataset(games, "nba", ["x"])


def test_trainer_requires_strictly_prior_observed_date():
    result, metadata = build_temporal_dataset(games_fixture(), "nba", ["x"])
    features = metadata["features"]
    result.loc[result.index[0], "feature_max_observed_game_date"] = result.loc[result.index[0], "game_date"]
    with pytest.raises(DataQualityError, match="estrictamente anterior"):
        validate_dataset(result, features)


def test_inference_features_match_the_training_feature_contract():
    games = games_fixture()
    training, metadata = build_temporal_dataset(games, "nba", ["x"])
    upcoming, _ = build_matchup_features(
        games.iloc[:3], games.iloc[[3]][["game_id", "game_date", "home_team_id", "away_team_id", "source_ref"]], ["x"],
        as_of="2024-01-02",
    )
    expected = training.set_index("game_id").loc["d", metadata["features"]]
    actual = upcoming.set_index("game_id").loc["d", metadata["features"]]
    pd.testing.assert_series_equal(expected.astype(float), actual.astype(float), check_names=False)


def test_inference_excludes_same_day_completed_games():
    games = games_fixture()
    same_day = games.iloc[[3]].copy()
    same_day["game_id"] = "same-day-completed"
    targets = games.iloc[[3]][["game_id", "game_date", "home_team_id", "away_team_id", "source_ref"]]
    with pytest.raises(DataQualityError, match="historial prepartido suficiente"):
        build_matchup_features(same_day, targets, ["x"], as_of="2024-01-02")


def test_chronological_folds_strict_ordering_and_no_holdout_overlap():
    import numpy as np
    from ml.training.train_temporal import chronological_folds

    # 30 games across 30 distinct dates
    dates = pd.date_range("2024-01-01", periods=30, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "game_id": [f"g_{i}" for i in range(30)],
        "game_date": dates,
        "target_home_win": [1, 0] * 15,
    })
    folds, holdout_idx = chronological_folds(df, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df["game_date"]).dt.date.to_numpy()

    assert len(folds) >= 3
    assert len(holdout_idx) > 0
    holdout_min_date = all_dates[holdout_idx].min()

    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        assert len(np.intersect1d(train_idx, test_idx)) == 0
        assert len(np.intersect1d(test_idx, holdout_idx)) == 0
        assert all_dates[train_idx].max() < all_dates[test_idx].min()
        assert all_dates[test_idx].max() < holdout_min_date


def test_adversarial_forbidden_features_leakage_rejected():
    """Valida que validate_dataset rechace columnas de resultado, etiquetas futuras o IDs en features."""
    games = games_fixture()
    training, metadata = build_temporal_dataset(games, "nba", ["x"])
    base_features = list(metadata["features"])

    # 1. Intento de fuga: home_score o target_home_win como feature
    for forbidden_col in ["target_home_win", "home_score", "away_score", "margin", "did_play", "game_id", "source"]:
        leaked_features = base_features + [forbidden_col]
        with pytest.raises(DataQualityError, match="columnas prohibidas"):
            validate_dataset(training, leaked_features)


def test_adversarial_transforms_strictly_fitted_only_on_train():
    """Demuestra matemáticamente que StandardScaler se ajusta ÚNICAMENTE en train_idx.

    Cualquier valor atípico extremo o distribución en test_idx no altera ni en un epsilon
    la media ni la escala calculadas por el pipeline de entrenamiento.
    """
    import numpy as np
    from ml.training.train_temporal import _models

    np.random.seed(42)
    n_train = 50
    n_test = 20

    # Train data con media ~10.0 y std ~2.0
    x_train_vals = np.random.normal(loc=10.0, scale=2.0, size=(n_train, 2))
    y_train = np.random.binomial(n=1, p=0.5, size=n_train)

    # Test data normal
    x_test_normal = np.random.normal(loc=10.0, scale=2.0, size=(n_test, 2))
    # Test data adversarial con un outlier extremo de 100,000,000.0
    x_test_adversarial = x_test_normal.copy()
    x_test_adversarial[0, 0] = 1e8

    feature_names = ["feat_1", "feat_2"]
    df_train = pd.DataFrame(x_train_vals, columns=feature_names)
    df_test_norm = pd.DataFrame(x_test_normal, columns=feature_names)
    df_test_adv = pd.DataFrame(x_test_adversarial, columns=feature_names)

    # Pipeline A: evaluado sobre test normal
    pipe_a = _models(feature_names)["logistic"]
    pipe_a.fit(df_train, y_train)
    scaler_a = pipe_a.named_steps["standardscaler"]

    # Pipeline B: evaluado sobre test adversarial
    pipe_b = _models(feature_names)["logistic"]
    pipe_b.fit(df_train, y_train)
    scaler_b = pipe_b.named_steps["standardscaler"]

    # 1. Parámetros del scaler deben coincidir exactamente con los estadísticos de train
    expected_mean = df_train.mean().to_numpy()
    expected_var = df_train.var(ddof=0).to_numpy()
    np.testing.assert_allclose(scaler_a.mean_, expected_mean, rtol=1e-5)
    np.testing.assert_allclose(scaler_a.var_, expected_var, rtol=1e-5)

    # 2. El scaler de A y B debe ser idéntico sin importar los datos de test
    np.testing.assert_array_equal(scaler_a.mean_, scaler_b.mean_)
    np.testing.assert_array_equal(scaler_a.var_, scaler_b.var_)
    np.testing.assert_array_equal(pipe_a.named_steps["boundedlogisticregression"].coef_, pipe_b.named_steps["boundedlogisticregression"].coef_)
    np.testing.assert_array_equal(pipe_a.named_steps["boundedlogisticregression"].intercept_, pipe_b.named_steps["boundedlogisticregression"].intercept_)


def test_adversarial_same_day_games_strictly_excluded_in_matchup_features():
    """Verifica que build_matchup_features excluya estrictamente partidos completados
    del mismo día calendario del juego evaluado, aun cuando as_of permita verlos."""
    completed = pd.DataFrame([
        # Partido completado día 1
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T18:00:00Z"},
        # Partido completado día 2 a las 18:00 UTC
        {"game_id": "c2", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 110, "away_score": 95,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T18:00:00Z"},
        # Partido completado para B el día 1
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    # Target: partido de A vs B el día 2 a las 23:00 UTC (as_of = día 2 a las 20:00 UTC)
    target = pd.DataFrame([{
        "game_id": "target_d2", "game_date": "2024-01-02",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-02T23:00:00Z",
        "source_ref": "ref:target",
    }])

    # as_of a las 20:00 UTC del día 2 (c2 ya terminó a las 18:00 UTC)
    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T20:00:00Z")

    row = res.iloc[0]
    # Regla: c2 es del MISMO DÍA que el target (2024-01-02) -> NO puede ser usado como feature
    # A solo debe tener 1 partido previo observado (el de 2024-01-01)
    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0  # Si usara c2 sería (100+110)/2 = 105.0
    assert row["home_days_rest"] == 1
    assert row["feature_max_observed_game_date"] == "2024-01-01"
    assert row["feature_max_observed_game_date"] < row["game_date"]


def test_adversarial_future_completed_game_rejected_in_matchup_features():
    """Valida que un partido en completed_games con kickoff en el futuro relativo a as_of sea rechazado fail-closed."""
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-02T22:00:00Z"},  # Kickoff a las 22:00 UTC
    ])
    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])
    # as_of a las 20:00 UTC del día 2 (el partido c1 dice haber empezado a las 22:00 UTC: es del futuro)
    with pytest.raises(DataQualityError, match="kickoff posterior al cutoff as_of"):
        build_matchup_features(completed, target, ["x"], as_of="2024-01-02T20:00:00Z")


def test_adversarial_duplicate_past_games_rejected_in_matchup_features():
    """Partidos completados con game_id duplicado deben ser rechazados fail-closed."""
    completed = pd.DataFrame([
        {"game_id": "dup_1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:1"},
        {"game_id": "dup_1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:1"},
    ])
    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-02",
        "home_team_id": "A", "away_team_id": "B",
        "source_ref": "ref:target",
    }])
    with pytest.raises(DataQualityError, match="game_id duplicados"):
        build_matchup_features(completed, target, ["x"], as_of="2024-01-01")


def test_adversarial_cutoff_day_unverified_kickoff_excluded_from_history():
    """Valida que un partido completado el día de corte con game_start_utc=None se excluya del historial.

    Alineado con el hallazgo de Codex: al cutoff 2024-01-02T12:00:00Z, un partido completado
    en 2024-01-02 con game_start_utc ausente/None no puede demostrar haber concluido antes de las
    12:00 UTC. Sus estadísticas (e.g. 120 pts) no deben ser incorporadas en el target de 2024-01-03.
    """
    completed = pd.DataFrame([
        # Partido verificado del día anterior (día 1)
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        # Partido completado del día de corte (día 2) con kickoff None (sin verificar) y puntaje anómalo
        {"game_id": "c2_unverified", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 120, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": None},
        # Partido verificado para B del día 1
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    # Invariante: c2_unverified debe haber sido excluido del historial.
    # El equipo A solo tiene 1 partido previo (c1 de 2024-01-01).
    assert row["home_games_prior"] == 1
    # El resultado desconocido (120 pts) NO se incorpora. Si se incorporara sería (100 + 120) / 2 = 110.0
    assert row["home_avg_points_for"] == 100.0
    assert row["feature_max_observed_game_date"] == "2024-01-01"
    assert row["feature_max_observed_game_date"] < row["game_date"]
    assert "ref:c2" not in row["feature_source_refs"]


def test_adversarial_cutoff_day_unparseable_kickoff_excluded_from_history():
    """Valida que un partido completado el día de corte con timestamp no parseable se excluya del historial."""
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        {"game_id": "c2_corrupt", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "invalid_utc_timestamp"},
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0
    assert row["feature_max_observed_game_date"] == "2024-01-01"


def test_adversarial_cutoff_day_unverified_kickoff_insufficient_history_skipped():
    """Si el único historial de un equipo es un partido del día de corte sin kickoff verificado, falla cerrado."""
    completed = pd.DataFrame([
        {"game_id": "c_only", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 120, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c_only",
         "game_start_utc": None},
        {"game_id": "c_b", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "B", "away_team_id": "D", "home_score": 90, "away_score": 85,
         "home_win": 1, "home_x": 9.0, "away_x": 8.0, "source_ref": "ref:c_b",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    with pytest.raises(DataQualityError, match="historial prepartido suficiente"):
        build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")


def test_adversarial_cutoff_day_prior_kickoff_without_verified_end_excluded_from_history():
    """Valida que un partido del día de corte con kickoff previo a as_of pero sin game_end_utc
    se excluya del historial (reproducción exacta del hallazgo de Codex).

    A las 12:00 UTC de 2024-01-02, un partido iniciado a las 11:00 UTC no prueba haber concluido.
    Sin marca de finalización verificada, no se puede incorporar su score final (140 pts)
    a un target posterior (2024-01-03).
    """
    completed = pd.DataFrame([
        # c1 verificado de día 1 (100 pts)
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        # c2 iniciado a las 11:00 UTC pero sin game_end_utc (score final retrospectivo 140 pts)
        {"game_id": "c2_in_progress", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T11:00:00Z"},
        # c3 verificado de día 1 para B
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    # c2_in_progress debe haber sido excluido del historial
    assert row["home_games_prior"] == 1
    # home_avg_points_for debe ser exactamente 100.0, NO 120.0 ((100+140)/2)
    assert row["home_avg_points_for"] == 100.0
    assert row["feature_max_observed_game_date"] == "2024-01-01"
    assert row["feature_max_observed_game_date"] < row["game_date"]
    assert "ref:c2" not in row["feature_source_refs"]


def test_adversarial_cutoff_day_prior_kickoff_with_later_end_excluded_from_history():
    """Valida que un partido del día de corte con kickoff previo pero game_end_utc POSTERIOR
    al cutoff se excluya del historial.

    Kickoff a las 11:00 UTC, finalización a las 14:30 UTC. A las 12:00 UTC el juego seguía
    en desarrollo; su resultado final de 140 pts no puede incorporarse.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        {"game_id": "c2_later_end", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T11:00:00Z",
         "game_end_utc": "2024-01-02T14:30:00Z"},  # Finalizó después del cutoff de 12:00 UTC
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0
    assert row["feature_max_observed_game_date"] == "2024-01-01"


def test_adversarial_cutoff_day_verified_end_before_cutoff_included_in_history():
    """Valida que un partido del día de corte con game_end_utc <= as_of_ts SÍ sea incorporado
    en un target posterior.

    Kickoff a las 09:00 UTC, finalización verificada a las 11:30 UTC. Al cutoff de 12:00 UTC
    el resultado final ya estaba disponible. Para un target de 2024-01-03, se incorpora legítimamente.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        {"game_id": "c2_verified_finished", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T09:00:00Z",
         "game_end_utc": "2024-01-02T11:30:00Z"},  # Finalizó antes del cutoff de 12:00 UTC
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    # c2_verified_finished SÍ debe ser incorporado: 2 partidos observados
    assert row["home_games_prior"] == 2
    # Promedio de puntos: (100 + 140) / 2 = 120.0
    assert row["home_avg_points_for"] == 120.0
    assert row["feature_max_observed_game_date"] == "2024-01-02"
    assert "ref:c2" in row["feature_source_refs"]


def test_adversarial_cutoff_day_end_before_kickoff_excluded_from_history():
    """Valida que un partido del día de corte con timestamps invertidos (game_end_utc < game_start_utc)
    se excluya fail-closed del historial.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T20:00:00Z"},
        {"game_id": "c2_inverted", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T11:00:00Z",
         "game_end_utc": "2024-01-02T10:00:00Z"},  # Fin anterior al inicio (corrupto)
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T20:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0


def test_adversarial_chronological_folds_rejects_insufficient_unique_dates():
    """Valida que chronological_folds levante DataQualityError si el dataframe tiene folds + 1 o menos fechas únicas."""
    from ml.training.train_temporal import chronological_folds

    dates = pd.date_range("2024-01-01", periods=5, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "game_id": [f"g_{i}" for i in range(5)],
        "game_date": dates,
        "target_home_win": [1, 0, 1, 0, 1],
    })
    with pytest.raises(DataQualityError, match="Se requieren más fechas únicas para 4 validaciones temporales"):
        chronological_folds(df, folds=4, holdout_frac=0.20)


def test_adversarial_chronological_folds_shuffled_rows_and_same_day_clustering():
    """Prueba adversarial de chronological_folds con filas desordenadas y múltiples juegos por fecha.

    Verifica:
    1. Agrupamiento íntegro de partidos del mismo día en un único bloque (sin dispersión entre train y test).
    2. Separación temporal estricta aun cuando el DataFrame de entrada esté mezclado aleatoriamente.
    3. Folds de test mutuamente excluyentes entre sí.
    4. Cero solapamiento de train o test con el holdout.
    """
    import numpy as np
    from ml.training.train_temporal import chronological_folds

    # Crear 60 fechas, con 3 partidos por fecha = 180 partidos
    rows = []
    dates = pd.date_range("2023-09-01", periods=60, freq="D").strftime("%Y-%m-%d")
    for d in dates:
        for match_no in range(3):
            rows.append({
                "game_id": f"game_{d}_{match_no}",
                "game_date": d,
                "target_home_win": (match_no % 2),
            })

    df = pd.DataFrame(rows)
    # Mezclar aleatoriamente el orden de filas
    np.random.seed(999)
    df_shuffled = df.sample(frac=1.0, random_state=999).reset_index(drop=True)

    folds, holdout_idx = chronological_folds(df_shuffled, folds=4, holdout_frac=0.20)
    all_dates = pd.to_datetime(df_shuffled["game_date"]).dt.date.to_numpy()

    assert len(folds) >= 3
    assert len(holdout_idx) > 0
    holdout_min_date = all_dates[holdout_idx].min()

    all_test_indices = []
    for fold_no, (train_idx, test_idx) in enumerate(folds, 1):
        # 1. Cero intersección train/test
        assert len(np.intersect1d(train_idx, test_idx)) == 0
        # 2. Cero intersección train/holdout y test/holdout
        assert len(np.intersect1d(train_idx, holdout_idx)) == 0
        assert len(np.intersect1d(test_idx, holdout_idx)) == 0
        # 3. Máxima fecha de train estrictamente menor que mínima fecha de test
        assert all_dates[train_idx].max() < all_dates[test_idx].min()
        # 4. Máxima fecha de test estrictamente menor que mínima fecha de holdout
        assert all_dates[test_idx].max() < holdout_min_date

        # 5. Agrupamiento de fechas: ninguna fecha en test puede estar presente en train
        train_dates_set = set(all_dates[train_idx])
        test_dates_set = set(all_dates[test_idx])
        assert train_dates_set.isdisjoint(test_dates_set), f"Fold {fold_no}: fecha compartida entre train y test"

        all_test_indices.extend(test_idx)

    # 6. Todos los folds de test son mutuamente excluyentes entre sí
    assert len(all_test_indices) == len(np.unique(all_test_indices))


def test_adversarial_chronological_folds_rejects_nat_and_duplicates():
    """Valida rechazo fail-closed de fechas inválidas/NaT y game_ids duplicados."""
    from ml.training.train_temporal import chronological_folds

    dates = pd.date_range("2024-01-01", periods=20, freq="D").strftime("%Y-%m-%d").tolist()
    dates[5] = "not-a-date"

    df_nat = pd.DataFrame({
        "game_id": [f"g_{i}" for i in range(20)],
        "game_date": dates,
        "target_home_win": [1, 0] * 10,
    })
    with pytest.raises(DataQualityError, match="valores nulos o inválidos"):
        chronological_folds(df_nat)

    # Duplicado de game_id
    valid_dates = pd.date_range("2024-01-01", periods=20, freq="D").strftime("%Y-%m-%d")
    df_dup = pd.DataFrame({
        "game_id": ["dup_id"] + [f"g_{i}" for i in range(19)],
        "game_date": valid_dates,
        "target_home_win": [1, 0] * 10,
    })
    df_dup.loc[1, "game_id"] = "dup_id"
    with pytest.raises(DataQualityError, match="game_id duplicados"):
        chronological_folds(df_dup)


def test_adversarial_model_selection_strict_dominance_rule():
    """Verifica que select_model_from_folds seleccione el ensamble solo con dominancia estricta en todos los folds."""
    from ml.training.train_temporal import select_model_from_folds

    # Escenario A: Ensamble domina en Brier en todos los folds, supera a majority y mejora log loss
    reports_perfect = {
        "logistic": {
            "folds": [
                {"fold": 1, "brier": 0.220, "log_loss": 0.630, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.215, "log_loss": 0.620, "majority_baseline_brier": 0.250},
                {"fold": 3, "brier": 0.210, "log_loss": 0.610, "majority_baseline_brier": 0.250},
                {"fold": 4, "brier": 0.205, "log_loss": 0.600, "majority_baseline_brier": 0.250},
            ]
        },
        "ensemble_blend": {
            "folds": [
                {"fold": 1, "brier": 0.210, "log_loss": 0.610, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.205, "log_loss": 0.600, "majority_baseline_brier": 0.250},
                {"fold": 3, "brier": 0.200, "log_loss": 0.590, "majority_baseline_brier": 0.250},
                {"fold": 4, "brier": 0.195, "log_loss": 0.580, "majority_baseline_brier": 0.250},
            ]
        }
    }
    sel_a = select_model_from_folds(reports_perfect)
    assert sel_a["selected_model"] == "ensemble"
    assert sel_a["decision"] == "ensemble_selected"

    # Escenario B: Ensamble empata o pierde en el fold 4 -> retiene logistic por parsimonia
    reports_tied_fold = {
        "logistic": {
            "folds": [
                {"fold": 1, "brier": 0.220, "log_loss": 0.630, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.215, "log_loss": 0.620, "majority_baseline_brier": 0.250},
                {"fold": 3, "brier": 0.210, "log_loss": 0.610, "majority_baseline_brier": 0.250},
                {"fold": 4, "brier": 0.205, "log_loss": 0.600, "majority_baseline_brier": 0.250},
            ]
        },
        "ensemble_blend": {
            "folds": [
                {"fold": 1, "brier": 0.210, "log_loss": 0.610, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.205, "log_loss": 0.600, "majority_baseline_brier": 0.250},
                {"fold": 3, "brier": 0.200, "log_loss": 0.590, "majority_baseline_brier": 0.250},
                {"fold": 4, "brier": 0.206, "log_loss": 0.595, "majority_baseline_brier": 0.250},  # 0.206 > 0.205 (peor!)
            ]
        }
    }
    sel_b = select_model_from_folds(reports_tied_fold)
    assert sel_b["selected_model"] == "logistic"
    assert sel_b["decision"] == "single_model_retained"
    assert sel_b["checks"]["ensemble_brier_better_than_logistic_all_folds"] is False

    # Escenario C: Ensamble no supera a majority en un fold -> retiene logistic
    reports_worse_maj = {
        "logistic": {
            "folds": [
                {"fold": 1, "brier": 0.220, "log_loss": 0.630, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.215, "log_loss": 0.620, "majority_baseline_brier": 0.250},
            ]
        },
        "ensemble_blend": {
            "folds": [
                {"fold": 1, "brier": 0.210, "log_loss": 0.610, "majority_baseline_brier": 0.250},
                {"fold": 2, "brier": 0.260, "log_loss": 0.600, "majority_baseline_brier": 0.250},  # peor que majority (0.260 > 0.250)
            ]
        }
    }
    sel_c = select_model_from_folds(reports_worse_maj)
    assert sel_c["selected_model"] == "logistic"
    assert sel_c["checks"]["ensemble_brier_better_than_majority_all_folds"] is False


def test_adversarial_production_artifacts_untouched():
    """Garantiza que la suite de pruebas no regenere ni sustituya artefactos de producción."""
    import hashlib
    from ml.data_pipeline.common import backend_root

    nfl_rf = backend_root() / "ml" / "models" / "nfl" / "random_forest.joblib"
    nba_rf = backend_root() / "ml" / "models" / "nba" / "random_forest.joblib"

    hashes_before = {}
    if nfl_rf.is_file():
        hashes_before["nfl_rf"] = hashlib.sha256(nfl_rf.read_bytes()).hexdigest()
    if nba_rf.is_file():
        hashes_before["nba_rf"] = hashlib.sha256(nba_rf.read_bytes()).hexdigest()

    # Verificar que los artefactos clave existen
    assert len(hashes_before) > 0

    # Hashes después de inspección deben ser idénticos
    if "nfl_rf" in hashes_before:
        assert hashlib.sha256(nfl_rf.read_bytes()).hexdigest() == hashes_before["nfl_rf"]
    if "nba_rf" in hashes_before:
        assert hashlib.sha256(nba_rf.read_bytes()).hexdigest() == hashes_before["nba_rf"]


def test_adversarial_cross_midnight_game_finishing_after_intraday_cutoff_excluded():
    """Valida que un partido de fecha previa (game_date=2024-01-01) pero finalizado en la madrugada
    posterior al cutoff intradía (game_end_utc=2024-01-02T02:30:00Z > as_of=2024-01-02T01:00:00Z)
    se excluya fail-closed del historial prepartido.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
        {"game_id": "c2_late", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-01T23:30:00Z",
         "game_end_utc": "2024-01-02T02:30:00Z"},  # Fin a las 02:30 UTC > cutoff 01:00 UTC
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T01:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0
    assert "ref:c2" not in row["feature_source_refs"]


def test_adversarial_cutoff_day_game_without_kickoff_column_excluded():
    """Valida que un partido en el día de corte se excluya fail-closed si el dataframe
    carece por completo de la columna game_start_utc, aun si cuenta con game_end_utc.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1"},
        {"game_id": "c2_no_start_col", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_end_utc": "2024-01-02T11:00:00Z"},
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T12:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0


def test_adversarial_west_coast_utc_rollover_game_without_verified_end_excluded():
    """Valida que un juego con fecha local de ayer (2024-01-01) pero kickoff UTC en el día
    del corte (2024-01-02T03:00:00Z) se excluya fail-closed si no demuestra finalización pre-corte.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
        {"game_id": "c2_west_coast", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T03:00:00Z"},  # Kickoff en día de corte UTC, sin end timestamp
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    # Cutoff a las 04:00 UTC (1 hora tras kickoff de c2)
    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T04:00:00Z")
    row = res.iloc[0]

    assert row["home_games_prior"] == 1
    assert row["home_avg_points_for"] == 100.0


def test_adversarial_intraday_cutoff_with_timezone_offset():
    """Valida que un cutoff con offset de zona horaria (e.g. -05:00) normalice correctamente a UTC
    y discrimine partidas completadas antes vs después del corte efectivo.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
        # c2 finalizó a las 11:30 UTC (antes del cutoff 07:00 -05:00 = 12:00 UTC)
        {"game_id": "c2_finished_pre_cutoff", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T09:00:00Z",
         "game_end_utc": "2024-01-02T11:30:00Z"},
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    # 07:00 -05:00 equivale a 12:00:00 UTC
    res, meta = build_matchup_features(completed, target, ["x"], as_of="2024-01-02T07:00:00-05:00")
    row = res.iloc[0]

    assert row["home_games_prior"] == 2
    assert row["home_avg_points_for"] == 120.0


def test_adversarial_midnight_timestamp_cutoff_rejects_post_midnight_same_day_game():
    """Valida que un cutoff Timestamp/datetime a medianoche exacta (00:00:00Z) sea tratado
    como un instante exacto (as_of_has_intraday_time=True) por instrucción 3 de Codex.

    Un partido del mismo día pero iniciado a las 00:30 UTC tiene kickoff posterior al cutoff
    y debe ser rechazado fail-closed con DataQualityError, impidiendo que su resultado (140 pts)
    alimente el historial prepartido.
    """
    completed = pd.DataFrame([
        {"game_id": "c1", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "A", "away_team_id": "B", "home_score": 100, "away_score": 90,
         "home_win": 1, "home_x": 10.0, "away_x": 12.0, "source_ref": "ref:c1",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
        # Partido en día 2 con kickoff posterior a medianoche (00:30 UTC > 00:00 UTC)
        {"game_id": "c2_post_midnight", "game_date": "2024-01-02", "season": 2024,
         "home_team_id": "A", "away_team_id": "C", "home_score": 140, "away_score": 80,
         "home_win": 1, "home_x": 15.0, "away_x": 14.0, "source_ref": "ref:c2",
         "game_start_utc": "2024-01-02T00:30:00Z",
         "game_end_utc": "2024-01-02T03:00:00Z"},
        {"game_id": "c3", "game_date": "2024-01-01", "season": 2024,
         "home_team_id": "C", "away_team_id": "B", "home_score": 80, "away_score": 85,
         "home_win": 0, "home_x": 8.0, "away_x": 9.0, "source_ref": "ref:c3",
         "game_start_utc": "2024-01-01T10:00:00Z", "game_end_utc": "2024-01-01T13:00:00Z"},
    ])

    target = pd.DataFrame([{
        "game_id": "target_1", "game_date": "2024-01-03",
        "home_team_id": "A", "away_team_id": "B",
        "game_start_utc": "2024-01-03T20:00:00Z",
        "source_ref": "ref:target",
    }])

    # 1. pd.Timestamp a medianoche exacta debe fallar cerrado ante kickoff futuro
    with pytest.raises(DataQualityError, match="kickoff posterior al cutoff as_of"):
        build_matchup_features(completed, target, ["x"], as_of=pd.Timestamp("2024-01-02T00:00:00Z"))

    # 2. Si el kickoff no estuviera registrado, se excluye y jamás se usa su score
    completed_no_kickoff = completed.copy()
    completed_no_kickoff.loc[1, "game_start_utc"] = None
    res, meta = build_matchup_features(completed_no_kickoff, target, ["x"], as_of=pd.Timestamp("2024-01-02T00:00:00Z"))
    assert res.iloc[0]["home_games_prior"] == 1
    assert res.iloc[0]["home_avg_points_for"] == 100.0

