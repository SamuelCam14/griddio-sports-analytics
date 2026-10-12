import asyncio

import pytest
from fastapi import HTTPException

from ml.data_pipeline.common import project_root
from app.api.v1.predictions import (
    _production_model_enabled, get_approved_model_version, get_experimental_predictions, get_game_prediction,
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


def test_nfl_gate_is_strictly_closed_by_default():
    """Verify that NFL production gate is closed in real model metadata without monkeypatching."""
    assert _production_model_enabled("NFL") is False
    assert _production_model_enabled("nfl") is False


def test_today_endpoint_nfl_serves_zero_picks_when_gate_closed():
    """Verify that /today returns zero picks for NFL with gate closed without monkeypatching."""
    response = asyncio.run(get_today_picks(sport="NFL", tier="all", period="all", db=None))
    assert response["predictions_enabled"] is False
    assert response["total_picks"] == 0
    assert response["picks"] == []
    assert "no hay un modelo aprobado" in response["reason"].lower()


def test_game_prediction_nfl_fails_closed_when_model_is_nfl():
    """Verify that get_game_prediction raises 503 for an NFL prediction when NFL gate is closed."""
    from unittest.mock import AsyncMock, MagicMock
    from app.models.game import Prediction

    mock_pred = MagicMock(spec=Prediction)
    mock_pred.game_id = 999
    mock_pred.model_version = "nfl_temporal_v1"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pred
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_game_prediction(game_id=999, db=mock_db))
    assert exc.value.status_code == 503
    assert "NFL" in exc.value.detail


def test_approved_model_version_rejects_unallowed_sports_and_path_traversal():
    """Verify that empty, non-string, unknown sports or path traversal attempts return None and False."""
    invalid_inputs = [
        "",
        "   ",
        "\t\n",
        None,
        123,
        [],
        {},
        "UNKNOWN",
        "SOCCER",
        "BASEBALL",
        "../../etc/passwd",
        "../nfl",
        "nfl/foo",
        "nba/..",
    ]
    for invalid_sport in invalid_inputs:
        assert get_approved_model_version(invalid_sport) is None
        assert _production_model_enabled(invalid_sport) is False


def test_approved_model_version_rejects_non_object_root_json(tmp_path, monkeypatch):
    """Verify that root JSON that is not a dictionary (e.g. list, string, number, null) returns None and False."""
    non_dict_payloads = [
        '[{"sport": "NFL", "production_enabled": true, "model_version": "v1"}]',
        '"just_a_string"',
        "12345",
        "true",
        "null",
    ]
    dummy_meta = tmp_path / "model_metadata.json"
    monkeypatch.setattr(
        "app.api.v1.predictions.Path.is_file",
        lambda self: True,
    )

    for payload in non_dict_payloads:
        dummy_meta.write_text(payload, encoding="utf-8")
        monkeypatch.setattr(
            "app.api.v1.predictions.Path.read_text",
            lambda self, encoding="utf-8": payload,
        )
        assert get_approved_model_version("NFL") is None
        assert _production_model_enabled("NFL") is False


def test_approved_model_version_rejects_invalid_json_and_read_errors(monkeypatch):
    """Verify that malformed JSON or read/encoding errors fail closed without leaking exceptions."""
    monkeypatch.setattr("app.api.v1.predictions.Path.is_file", lambda self: True)

    # Malformed JSON
    monkeypatch.setattr(
        "app.api.v1.predictions.Path.read_text",
        lambda self, encoding="utf-8": "{broken_json: definitely_not_valid",
    )
    assert get_approved_model_version("NFL") is None
    assert _production_model_enabled("NFL") is False

    # OSError / permission / read error
    def raise_oserror(self, encoding="utf-8"):
        raise OSError("Disk read error")

    monkeypatch.setattr("app.api.v1.predictions.Path.read_text", raise_oserror)
    assert get_approved_model_version("NFL") is None
    assert _production_model_enabled("NFL") is False

    # UnicodeDecodeError
    def raise_decode_error(self, encoding="utf-8"):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("app.api.v1.predictions.Path.read_text", raise_decode_error)
    assert get_approved_model_version("NFL") is None
    assert _production_model_enabled("NFL") is False


def test_approved_model_version_rejects_filesystem_oserror_on_is_file_and_resolve(monkeypatch):
    """Verify that OSError in Path.is_file() or Path.resolve() fails closed without leaking exceptions."""
    # 1. OSError in Path.is_file()
    def raise_is_file_error(self):
        raise OSError("Permission denied or disk I/O failure on is_file")

    monkeypatch.setattr("app.api.v1.predictions.Path.is_file", raise_is_file_error)
    assert get_approved_model_version("NFL") is None
    assert _production_model_enabled("NFL") is False

    # 2. OSError in Path.resolve()
    def raise_resolve_error(self):
        raise OSError("Path resolution failed")

    monkeypatch.setattr("app.api.v1.predictions.Path.resolve", raise_resolve_error)
    assert get_approved_model_version("NFL") is None
    assert _production_model_enabled("NFL") is False


def test_approved_model_version_rejects_mismatched_sport_in_metadata(monkeypatch):
    """Verify that if sport in metadata does not match requested sport, it fails closed."""
    monkeypatch.setattr("app.api.v1.predictions.Path.is_file", lambda self: True)

    mismatched_payloads = [
        json.dumps({"sport": "NBA", "production_enabled": True, "model_version": "nfl_v1"}),
        json.dumps({"sport": "UNKNOWN", "production_enabled": True, "model_version": "nfl_v1"}),
        json.dumps({"production_enabled": True, "model_version": "nfl_v1"}),  # missing sport
        json.dumps({"sport": None, "production_enabled": True, "model_version": "nfl_v1"}),
        json.dumps({"sport": 123, "production_enabled": True, "model_version": "nfl_v1"}),
    ]

    for payload in mismatched_payloads:
        monkeypatch.setattr(
            "app.api.v1.predictions.Path.read_text",
            lambda self, encoding="utf-8", p=payload: p,
        )
        assert get_approved_model_version("NFL") is None
        assert _production_model_enabled("NFL") is False


def test_approved_model_version_rejects_empty_or_whitespace_version(monkeypatch):
    """Verify that empty, whitespace-only, non-string, or missing model_version fails closed."""
    monkeypatch.setattr("app.api.v1.predictions.Path.is_file", lambda self: True)

    invalid_version_payloads = [
        json.dumps({"sport": "NFL", "production_enabled": True, "model_version": ""}),
        json.dumps({"sport": "NFL", "production_enabled": True, "model_version": "   "}),
        json.dumps({"sport": "NFL", "production_enabled": True, "model_version": "\t\n  "}),
        json.dumps({"sport": "NFL", "production_enabled": True, "model_version": None}),
        json.dumps({"sport": "NFL", "production_enabled": True, "model_version": 123}),
        json.dumps({"sport": "NFL", "production_enabled": True}),  # missing model_version
    ]

    for payload in invalid_version_payloads:
        monkeypatch.setattr(
            "app.api.v1.predictions.Path.read_text",
            lambda self, encoding="utf-8", p=payload: p,
        )
        assert get_approved_model_version("NFL") is None
        assert _production_model_enabled("NFL") is False


def test_approved_model_version_accepts_valid_metadata_when_production_enabled(monkeypatch):
    """Verify that valid metadata with production_enabled=True returns the clean stripped version."""
    monkeypatch.setattr("app.api.v1.predictions.Path.is_file", lambda self: True)

    valid_payload = json.dumps({
        "sport": "NFL",
        "production_enabled": True,
        "model_version": "  nfl_temporal_v1  ",
    })
    monkeypatch.setattr(
        "app.api.v1.predictions.Path.read_text",
        lambda self, encoding="utf-8": valid_payload,
    )
    assert get_approved_model_version("NFL") == "nfl_temporal_v1"
    assert get_approved_model_version("nfl") == "nfl_temporal_v1"
    assert _production_model_enabled("NFL") is True
    assert _production_model_enabled("nfl") is True

