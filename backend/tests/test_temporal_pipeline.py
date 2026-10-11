import pandas as pd
import pytest

from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.temporal_features import build_matchup_features, build_temporal_dataset
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

