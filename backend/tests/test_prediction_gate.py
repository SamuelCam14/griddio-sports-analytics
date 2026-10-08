import asyncio

import pytest
from fastapi import HTTPException

from ml.data_pipeline.common import project_root
from app.api.v1.predictions import (
    _production_model_enabled, get_experimental_predictions, get_game_prediction,
    get_model_info, get_today_picks,
)


def test_temporal_training_artifacts_are_not_served_as_production_models():
    assert _production_model_enabled("unknown_sport") is False
    assert _production_model_enabled("") is False


def test_today_endpoint_does_not_return_legacy_database_picks(monkeypatch):
    monkeypatch.setattr("app.api.v1.predictions._production_model_enabled", lambda sport: False)
    response = asyncio.run(get_today_picks(sport="NBA", tier="all", period="today", db=None))
    assert response["predictions_enabled"] is False
    assert response["picks"] == []


def test_game_prediction_endpoint_fails_closed_without_approval(monkeypatch):
    monkeypatch.setattr("app.api.v1.predictions._production_model_enabled", lambda sport: False)
    with pytest.raises(HTTPException) as error:
        asyncio.run(get_game_prediction(game_id=123, db=None))
    assert error.value.status_code == 503


def test_model_info_reports_reproducible_metrics_without_legacy_roi():
    response = asyncio.run(get_model_info())
    assert set(response["models"]) == {"NBA", "NFL"}
    assert "premium_simulated_roi" not in response


def test_experimental_endpoint_serves_only_manifest_verified_probabilities(mock_experimental_dir):
    now = datetime.now(timezone.utc)
    game_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    csv_content = f"game_id,game_date,home_win_probability,away_win_probability\n1,{game_date},0.6,0.4\n"
    csv_filename = "nba_predictions_test.csv"
    manifest_filename = "nba_predictions_test.manifest.json"
    (mock_experimental_dir / csv_filename).write_text(csv_content)
    manifest_content = {
        "as_of": now.isoformat(),
        "schedule_retrieved_at": now.isoformat(),
        "output_file": csv_filename,
        "output_sha256": hashlib.sha256(csv_content.encode("utf-8")).hexdigest(),
        "model_version": "nba_temporal_v1",
        "dataset_sha256": "dummy_dataset",
        "artifact_sha256": "dummy_artifact"
    }
    (mock_experimental_dir / manifest_filename).write_text(json.dumps(manifest_content))

    response = asyncio.run(get_experimental_predictions("nba", lookahead_days=90))
    assert response["status"] == "experimental_only"
    assert response["predictions_enabled"] is False
    assert response["production_eligible"] is False
    for game in response["games"]:
        assert 0 <= game["home_win_probability"] <= 1
        assert abs(game["home_win_probability"] + game["away_win_probability"] - 1) < 1e-5
        assert "home_ev" not in game and "tier" not in game

import json
import hashlib
from datetime import datetime, timedelta, timezone

@pytest.fixture
def mock_experimental_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "predictions" / "experimental"
    data_dir.mkdir(parents=True, exist_ok=True)
    def mock_project_root():
        return tmp_path
    monkeypatch.setattr("app.api.v1.predictions.project_root", mock_project_root)
    return data_dir

def create_manifest_and_csv(dir_path, sport, suffix, as_of_delta, schedule_delta, bad_hash=False, bad_csv=False):
    now = datetime.now(timezone.utc)
    as_of = (now + timedelta(days=as_of_delta)).isoformat()
    schedule_retrieved = (now + timedelta(days=schedule_delta)).isoformat()
    csv_filename = f"{sport}_predictions_{suffix}.csv"
    manifest_filename = f"{sport}_predictions_{suffix}.manifest.json"
    csv_content = "game_id,game_date,home_win_prob,away_win_prob\n1,2026-10-10,0.6,0.4\n"
    if bad_csv:
        csv_content = "game_id,game_date\n1\n"
    csv_path = dir_path / csv_filename
    csv_path.write_text(csv_content)
    actual_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
    manifest_content = {
        "as_of": as_of,
        "schedule_retrieved_at": schedule_retrieved,
        "output_file": csv_filename,
        "output_sha256": "bad_hash" if bad_hash else actual_hash,
        "model_version": "v1"
    }
    manifest_path = dir_path / manifest_filename
    manifest_path.write_text(json.dumps(manifest_content))

def test_experimental_endpoint_unknown_sport():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("soccer", lookahead_days=14))
    assert exc.value.status_code == 404

def test_experimental_endpoint_missing_manifest(mock_experimental_dir):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 404

def test_experimental_endpoint_expired_as_of(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=-2, schedule_delta=0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_expired_schedule(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=-8)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_bad_hash(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0, bad_hash=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_truncated_csv(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0, bad_csv=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_unknown_model(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0)
    # Rewrite the manifest to have an unknown model version
    manifest_path = mock_experimental_dir / "nfl_predictions_1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["model_version"] = "unknown_model_v99"
    manifest_path.write_text(json.dumps(manifest))
    
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_simulate_matchup_fails_closed():
    from app.api.v1.predictions import simulate_matchup, SimulationRequest
    with pytest.raises(HTTPException) as exc:
        asyncio.run(simulate_matchup(SimulationRequest()))
    assert exc.value.status_code == 503


def test_experimental_endpoint_mismatched_model_sport(mock_experimental_dir):
    # Pass an NBA model for NFL
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0)
    manifest_path = mock_experimental_dir / "nfl_predictions_1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["model_version"] = "nba_temporal_v1"
    manifest_path.write_text(json.dumps(manifest))
    
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_bad_probability(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0)
    csv_path = mock_experimental_dir / "nfl_predictions_1.csv"
    # Bad probability sum
    csv_path.write_text("game_id,game_date,home_win_probability,away_win_probability\n1,2026-10-10,0.6,0.6\n")
    manifest_path = mock_experimental_dir / "nfl_predictions_1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest["model_version"] = "nfl_temporal_v1"
    manifest_path.write_text(json.dumps(manifest))
    
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503

def test_experimental_endpoint_null_id(mock_experimental_dir):
    create_manifest_and_csv(mock_experimental_dir, "nfl", "1", as_of_delta=0, schedule_delta=0)
    csv_path = mock_experimental_dir / "nfl_predictions_1.csv"
    # missing game_id
    csv_path.write_text("game_id,game_date,home_win_probability,away_win_probability\n,2026-10-10,0.6,0.4\n")
    manifest_path = mock_experimental_dir / "nfl_predictions_1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest["model_version"] = "nfl_temporal_v1"
    manifest_path.write_text(json.dumps(manifest))
    
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_experimental_predictions("nfl", lookahead_days=14))
    assert exc.value.status_code == 503
