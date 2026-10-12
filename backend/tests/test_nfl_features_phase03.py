import hashlib
import json
from pathlib import Path
import pandas as pd
import pytest

from ml.data_pipeline.common import DataQualityError, project_root
from ml.data_pipeline.nfl_source import NFL_TEAM_ALIASES, load_nfl_games
from ml.data_pipeline.temporal_features import (
    NFL_GAME_STATS,
    build_matchup_features,
    build_temporal_dataset,
    feature_columns,
)


@pytest.fixture
def sample_nfl_games():
    """Synthetic multi-day NFL schedule with verified EPA and stats."""
    return pd.DataFrame([
        {
            "game_id": "2024_01_KC_BAL",
            "game_date": "2024-09-05",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "KC",
            "away_team_id": "BAL",
            "home_score": 27,
            "away_score": 20,
            "home_win": 1,
            "is_tie": False,
            "home_off_epa_per_play": 0.15,
            "away_off_epa_per_play": 0.05,
            "home_off_success_rate": 0.48,
            "away_off_success_rate": 0.42,
            "home_def_epa_allowed_per_play": 0.05,
            "away_def_epa_allowed_per_play": 0.15,
            "home_def_success_allowed": 0.42,
            "away_def_success_allowed": 0.48,
            "source": "fixture",
            "source_ref": "sha:2024_01",
        },
        {
            "game_id": "2024_01_CIN_LV",
            "game_date": "2024-09-05",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "CIN",
            "away_team_id": "LV",
            "home_score": 10,
            "away_score": 16,
            "home_win": 0,
            "is_tie": False,
            "home_off_epa_per_play": -0.05,
            "away_off_epa_per_play": 0.02,
            "home_off_success_rate": 0.38,
            "away_off_success_rate": 0.41,
            "home_def_epa_allowed_per_play": 0.02,
            "away_def_epa_allowed_per_play": -0.05,
            "home_def_success_allowed": 0.41,
            "away_def_success_allowed": 0.38,
            "source": "fixture",
            "source_ref": "sha:2024_02",
        },
        {
            "game_id": "2024_01_ATL_PHI",
            "game_date": "2024-09-06",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "ATL",
            "away_team_id": "PHI",
            "home_score": 18,
            "away_score": 20,
            "home_win": 0,
            "is_tie": False,
            "home_off_epa_per_play": 0.01,
            "away_off_epa_per_play": 0.04,
            "home_off_success_rate": 0.40,
            "away_off_success_rate": 0.44,
            "home_def_epa_allowed_per_play": 0.04,
            "away_def_epa_allowed_per_play": 0.01,
            "home_def_success_allowed": 0.44,
            "away_def_success_allowed": 0.40,
            "source": "fixture",
            "source_ref": "sha:2024_03",
        },
        {
            "game_id": "2024_02_KC_CIN",
            "game_date": "2024-09-15",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "KC",
            "away_team_id": "CIN",
            "home_score": 26,
            "away_score": 25,
            "home_win": 1,
            "is_tie": False,
            "home_off_epa_per_play": 0.10,
            "away_off_epa_per_play": 0.09,
            "home_off_success_rate": 0.45,
            "away_off_success_rate": 0.43,
            "home_def_epa_allowed_per_play": 0.09,
            "away_def_epa_allowed_per_play": 0.10,
            "home_def_success_allowed": 0.43,
            "away_def_success_allowed": 0.45,
            "source": "fixture",
            "source_ref": "sha:2024_04",
        },
        {
            "game_id": "2024_02_BAL_LV",
            "game_date": "2024-09-15",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "BAL",
            "away_team_id": "LV",
            "home_score": 23,
            "away_score": 26,
            "home_win": 0,
            "is_tie": False,
            "home_off_epa_per_play": 0.07,
            "away_off_epa_per_play": 0.11,
            "home_off_success_rate": 0.41,
            "away_off_success_rate": 0.47,
            "home_def_epa_allowed_per_play": 0.11,
            "away_def_epa_allowed_per_play": 0.07,
            "home_def_success_allowed": 0.47,
            "away_def_success_allowed": 0.41,
            "source": "fixture",
            "source_ref": "sha:2024_05",
        },
        {
            "game_id": "2024_03_KC_ATL",
            "game_date": "2024-09-22",
            "season": 2024,
            "season_type": "REG",
            "home_team_id": "ATL",
            "away_team_id": "KC",
            "home_score": 17,
            "away_score": 22,
            "home_win": 0,
            "is_tie": False,
            "home_off_epa_per_play": 0.02,
            "away_off_epa_per_play": 0.08,
            "home_off_success_rate": 0.38,
            "away_off_success_rate": 0.45,
            "home_def_epa_allowed_per_play": 0.08,
            "away_def_epa_allowed_per_play": 0.02,
            "home_def_success_allowed": 0.45,
            "away_def_success_allowed": 0.38,
            "source": "fixture",
            "source_ref": "sha:2024_06",
        },
    ])


def test_feature_columns_schema_parity():
    """Verifies exact feature counts for team_only (26) and injury_aware (32)."""
    team_cols = feature_columns(NFL_GAME_STATS, include_availability=False)
    injury_cols = feature_columns(NFL_GAME_STATS, include_availability=True)

    assert len(team_cols) == 26
    assert len(injury_cols) == 32
    # Verify exact difference is the 6 availability features
    diff = set(injury_cols) - set(team_cols)
    expected_diff = {
        "home_ppr_lost_pct", "home_off_snaps_lost_pct", "home_def_snaps_lost_pct",
        "away_ppr_lost_pct", "away_off_snaps_lost_pct", "away_def_snaps_lost_pct",
    }
    assert diff == expected_diff


def test_temporal_invariance_future_mutation(sample_nfl_games):
    """Mutating or adding games after cutoff must NOT alter prior feature values."""
    df1 = sample_nfl_games.copy()
    dataset1, _ = build_temporal_dataset(df1, "nfl", NFL_GAME_STATS, window=5, minimum_history=1)

    # Mutate game 5 (week 3) scores and stats
    df2 = sample_nfl_games.copy()
    df2.loc[df2["game_id"] == "2024_03_KC_ATL", "home_score"] = 99
    df2.loc[df2["game_id"] == "2024_03_KC_ATL", "home_off_epa_per_play"] = 0.99
    dataset2, _ = build_temporal_dataset(df2, "nfl", NFL_GAME_STATS, window=5, minimum_history=1)

    # Features for games on 2024-09-15 must be identical
    cols = feature_columns(NFL_GAME_STATS, include_availability=False)
    row1 = dataset1[dataset1["game_id"] == "2024_02_KC_CIN"][cols].reset_index(drop=True)
    row2 = dataset2[dataset2["game_id"] == "2024_02_KC_CIN"][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(row1, row2)


def test_no_same_day_information_leak(sample_nfl_games):
    """Games played on the same calendar date cannot use each other's outcome."""
    # Both 2024_02_KC_CIN and 2024_02_BAL_LV are on 2024-09-15
    dataset, _ = build_temporal_dataset(sample_nfl_games, "nfl", NFL_GAME_STATS, window=5, minimum_history=1)
    kc_game = dataset[dataset["game_id"] == "2024_02_KC_CIN"].iloc[0]
    bal_game = dataset[dataset["game_id"] == "2024_02_BAL_LV"].iloc[0]

    assert kc_game["feature_max_observed_game_date"] == "2024-09-05"
    assert bal_game["feature_max_observed_game_date"] == "2024-09-05"
    assert kc_game["home_games_prior"] == 1
    assert bal_game["home_games_prior"] == 1


def test_processed_canonical_datasets_on_disk():
    """Validates processed datasets on disk match manifests byte-for-byte with strict SHA-256."""
    root = project_root() / "data" / "processed"
    team_manifest_path = root / "nfl_team_only_training.manifest.json"
    team_csv_path = root / "nfl_team_only_training.csv"

    assert team_manifest_path.is_file(), "Missing team_only manifest"
    assert team_csv_path.is_file(), "Missing team_only CSV"

    team_manifest = json.loads(team_manifest_path.read_text(encoding="utf-8"))
    team_bytes = team_csv_path.read_bytes()
    assert team_manifest["dataset_sha256"] == hashlib.sha256(team_bytes).hexdigest()
    assert team_manifest["rows"] == 4398
    assert team_manifest["quality"]["features"] == feature_columns(NFL_GAME_STATS, include_availability=False)

    injury_manifest_path = root / "nfl_training.manifest.json"
    injury_csv_path = root / "nfl_training.csv"

    assert injury_manifest_path.is_file(), "Missing injury_aware manifest"
    assert injury_csv_path.is_file(), "Missing injury_aware CSV"

    injury_manifest = json.loads(injury_manifest_path.read_text(encoding="utf-8"))
    injury_bytes = injury_csv_path.read_bytes()
    assert injury_manifest["dataset_sha256"] == hashlib.sha256(injury_bytes).hexdigest()
    assert injury_manifest["rows"] == 184
    assert injury_manifest["quality"]["rows_without_verified_availability"] == 4214
    assert injury_manifest["quality"]["features"] == feature_columns(NFL_GAME_STATS, include_availability=True)


def test_dataset_contains_no_nans_in_feature_columns():
    """All 4,398 rows of team_only dataset must have 100% finite, non-null features."""
    root = project_root() / "data" / "processed"
    df = pd.read_csv(root / "nfl_team_only_training.csv")
    cols = feature_columns(NFL_GAME_STATS, include_availability=False)

    assert not df[cols].isna().any().any()
    assert (df[cols].map(lambda x: isinstance(x, (int, float)))).all().all()


def test_feature_cutoff_is_strictly_prior():
    """Every single row in the dataset must have max_observed_date < game_date."""
    root = project_root() / "data" / "processed"
    df = pd.read_csv(root / "nfl_team_only_training.csv")
    observed = pd.to_datetime(df["feature_max_observed_game_date"])
    target = pd.to_datetime(df["game_date"])
    assert (observed < target).all()


def test_training_and_inference_feature_parity(sample_nfl_games):
    """Features produced by build_matchup_features must match build_temporal_dataset for the same target."""
    stats = NFL_GAME_STATS
    training, meta = build_temporal_dataset(sample_nfl_games, "nfl", stats, window=5, minimum_history=1)
    features = meta["features"]

    past_games = sample_nfl_games.iloc[:5].copy()
    target_game = sample_nfl_games.iloc[[5]][["game_id", "game_date", "home_team_id", "away_team_id", "source_ref"]].copy()

    inference, _ = build_matchup_features(past_games, target_game, stats, window=5, minimum_history=1, as_of="2024-09-20")

    expected_vals = training[training["game_id"] == "2024_03_KC_ATL"][features].iloc[0].to_dict()
    actual_vals = inference[inference["game_id"] == "2024_03_KC_ATL"][features].iloc[0].to_dict()

    for feat in features:
        if "lost_pct" in feat:
            continue
        assert pytest.approx(expected_vals[feat], rel=1e-5) == actual_vals[feat]


def test_insufficient_history_is_cleanly_rejected():
    """When teams lack minimum_history, DataQualityError is raised."""
    single_game = pd.DataFrame([{
        "game_id": "g1", "game_date": "2024-09-01", "season": 2024,
        "home_team_id": "KC", "away_team_id": "BAL", "home_score": 20, "away_score": 17,
        "home_win": 1, "source": "f", "source_ref": "s",
        "home_off_epa_per_play": 0.1, "away_off_epa_per_play": -0.1,
        "home_off_success_rate": 0.45, "away_off_success_rate": 0.40,
        "home_def_epa_allowed_per_play": -0.1, "away_def_epa_allowed_per_play": 0.1,
        "home_def_success_allowed": 0.40, "away_def_success_allowed": 0.45,
    }])
    with pytest.raises(DataQualityError, match="no rows with the required pre-game history"):
        build_temporal_dataset(single_game, "nfl", NFL_GAME_STATS, minimum_history=1)

