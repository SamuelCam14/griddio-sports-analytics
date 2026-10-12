import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from ml.data_pipeline.common import project_root


@pytest.fixture
def client():
    return TestClient(app)


def test_get_endpoints_do_not_call_external_odds_api(client):
    """GET requests to dashboard endpoints must NEVER trigger external API calls."""
    # We patch requests and httpx to ensure no outbound network calls happen
    with patch("httpx.AsyncClient.get") as mock_http_get, \
         patch("requests.get") as mock_req_get:

        resp_today = client.get("/api/v1/predictions/today?sport=NFL")
        resp_exp = client.get("/api/v1/predictions/experimental/nfl")
        resp_parlay = client.get("/api/v1/parlay/recommended?sport=NFL")
        resp_odds = client.get("/api/v1/odds/nfl")

        # None of these calls should have touched the network
        assert not mock_http_get.called, "Outbound HTTP Async call was triggered on GET"
        assert not mock_req_get.called, "Outbound HTTP sync call was triggered on GET"

        # Check expected responses
        assert resp_today.status_code == 200
        assert resp_exp.status_code == 200
        assert resp_parlay.status_code == 200
        assert resp_odds.status_code == 200


def test_experimental_predictions_contract_and_provenance(client):
    """Experimental NFL predictions serve pure probabilities with dataset and artifact hashes."""
    resp = client.get("/api/v1/predictions/experimental/nfl")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "experimental_only"
    assert data["sport"] == "NFL"
    assert data["predictions_enabled"] is False
    assert data["production_eligible"] is False
    assert data["model_version"] == "nfl_team_only_temporal_v1"
    assert "dataset_sha256" in data
    assert "artifact_sha256" in data
    assert len(data["games"]) > 0

    first_game = data["games"][0]
    assert 0.0 <= first_game["home_win_probability"] <= 1.0
    assert 0.0 <= first_game["away_win_probability"] <= 1.0
    assert pytest.approx(first_game["home_win_probability"] + first_game["away_win_probability"], abs=1e-4) == 1.0
    assert "schedule_source_ref" in first_game


def test_production_gate_remains_closed_for_nfl(client):
    """Production predictions endpoint for NFL returns zero picks while gate is closed."""
    resp = client.get("/api/v1/predictions/today?sport=NFL")
    assert resp.status_code == 200
    data = resp.json()

    assert data["sport"] == "NFL"
    assert data["total_picks"] == 0
    assert data["picks"] == []


def test_parlay_recommended_returns_empty_or_unavailable_when_gate_closed(client):
    """Recommended parlay route returns empty list or unavailable status without mocks."""
    resp = client.get("/api/v1/parlay/recommended?sport=NFL")
    assert resp.status_code == 200
    data = resp.json()

    # Must be either status: unavailable with reason, or empty parlays
    if "status" in data:
        assert data["status"] == "unavailable"
    else:
        assert data.get("parlays", []) == []


def test_ledger_quota_unchanged_across_api_calls(client):
    """Verify that ledger remains strictly untouched (275 credits, exact SHA-256)."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    ledger_bytes = ledger_path.read_bytes()
    expected_hash = "13e43e0c675cf6c17946e3b3e03df639dbe2e96ab0786680c4a6d37c9ab028be"
    actual_hash = hashlib.sha256(ledger_bytes).hexdigest()

    assert actual_hash == expected_hash
    ledger_data = json.loads(ledger_bytes.decode("utf-8"))
    assert ledger_data["summary"]["requests_remaining"] == 275
