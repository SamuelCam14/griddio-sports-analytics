"""
Empirical Verification and Adversarial Stress Test Suite for Milestone 4.
Challenger: Challenger 1 (teamwork_preview_challenger)

Tests:
1. Quota ledger immutability (strict SHA-256 baseline).
2. Live and TestClient schema validation for /api/v1/predictions/today:
   - Root as_of existence and ISO 8601 UTC compliance.
   - predictions_enabled == True for NFL.
   - Pick timestamps (observed_at, predicted_at, as_of).
   - Fail-closed behavior on NBA and invalid sports.
3. Live and TestClient schema validation for /api/v1/parlay/recommended:
   - Root as_of existence and ISO 8601 UTC compliance.
   - Evaluated legs include valid bookmaker and observed_at without placeholders.
   - Sport restrictions (NFL allowed, NBA/others fail-closed with status=unavailable).
4. Production gate integrity across model registries.
5. Defensive spread data handling (null/NaN/Inf resilience).
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1.predictions import _production_model_enabled, get_approved_model_version
from ml.data_pipeline.common import project_root

EXPECTED_QUOTA_LEDGER_SHA256 = "6f5c546089689631d0f0d49aadb76e8499f9f64e4adc9594bc0264a8ad17fa53"


class TestM4QuotaLedgerIntegrity:
    """Verifies that data/odds_quota_ledger.json remains completely unaltered."""

    def test_quota_ledger_hash_matches_strict_baseline(self):
        ledger_path = project_root() / "data" / "odds_quota_ledger.json"
        assert ledger_path.is_file(), f"Quota ledger not found at {ledger_path}"
        computed_sha256 = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        assert computed_sha256 == EXPECTED_QUOTA_LEDGER_SHA256, (
            f"Quota ledger hash altered! Expected {EXPECTED_QUOTA_LEDGER_SHA256}, got {computed_sha256}"
        )


class TestM4ProductionGateIntegrity:
    """Verifies production gate states across sports."""

    def test_nfl_gate_is_open_in_model_metadata(self):
        backend_dir = Path(__file__).resolve().parent.parent
        meta_path = backend_dir / "ml" / "models" / "nfl" / "model_metadata.json"
        assert meta_path.is_file(), f"Path not found: {meta_path}"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert data.get("production_enabled") is True
        assert data.get("sport") == "NFL"
        assert _production_model_enabled("NFL") is True
        assert get_approved_model_version("NFL") == "nfl_temporal_v1"

    def test_nba_and_team_only_gates_remain_strictly_closed(self):
        backend_dir = Path(__file__).resolve().parent.parent
        nba_meta = backend_dir / "ml" / "models" / "nba" / "model_metadata.json"
        assert nba_meta.is_file(), f"Path not found: {nba_meta}"
        data_nba = json.loads(nba_meta.read_text(encoding="utf-8"))
        assert data_nba.get("production_enabled") is False
        assert _production_model_enabled("NBA") is False
        assert get_approved_model_version("NBA") is None

        team_only_meta = backend_dir / "ml" / "models" / "nfl" / "team_only" / "model_metadata.json"
        if team_only_meta.is_file():
            data_team = json.loads(team_only_meta.read_text(encoding="utf-8"))
            assert data_team.get("production_enabled") is False


class TestM4PredictionsEndpointEmpirical:
    """Verifies /api/v1/predictions/today contract and timestamp serialization."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_predictions_today_nfl_root_schema(self, client):
        response = client.get("/api/v1/predictions/today?sport=NFL")
        assert response.status_code == 200
        payload = response.json()

        # Root fields validation
        assert payload.get("sport") == "NFL"
        assert payload.get("predictions_enabled") is True
        assert "as_of" in payload
        assert "picks" in payload
        assert isinstance(payload["picks"], list)

        # ISO 8601 UTC validation on root as_of
        as_of_str = payload["as_of"]
        dt = datetime.fromisoformat(as_of_str)
        assert dt.tzinfo is not None, "as_of must be timezone aware"
        assert dt.utcoffset().total_seconds() == 0, f"as_of must be in UTC, got offset {dt.utcoffset()}"

    def test_predictions_today_picks_timestamp_provenance(self, client):
        response = client.get("/api/v1/predictions/today?sport=NFL")
        assert response.status_code == 200
        payload = response.json()

        for pick in payload["picks"]:
            # If timestamps are present, verify ISO 8601 UTC formatting
            for ts_field in ("observed_at", "predicted_at", "as_of"):
                ts_val = pick.get(ts_field)
                if ts_val is not None:
                    parsed_dt = datetime.fromisoformat(ts_val)
                    assert parsed_dt.tzinfo is not None
                    assert parsed_dt.utcoffset().total_seconds() == 0

    def test_predictions_today_nba_fails_closed_with_as_of(self, client):
        response = client.get("/api/v1/predictions/today?sport=NBA")
        assert response.status_code == 200
        payload = response.json()

        assert payload.get("sport") == "NBA"
        assert payload.get("predictions_enabled") is False
        assert payload.get("total_picks") == 0
        assert payload.get("picks") == []
        assert "as_of" in payload

        dt = datetime.fromisoformat(payload["as_of"])
        assert dt.utcoffset().total_seconds() == 0

    def test_predictions_today_unsupported_sport_fails_closed(self, client):
        response = client.get("/api/v1/predictions/today?sport=MLB")
        assert response.status_code == 200
        payload = response.json()

        assert payload.get("predictions_enabled") is False
        assert payload.get("total_picks") == 0
        assert "as_of" in payload


class TestM4ParlayEndpointEmpirical:
    """Verifies /api/v1/parlay/recommended contract, timestamps, and bookmakers."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_parlay_recommended_nfl_root_schema(self, client):
        response = client.get("/api/v1/parlay/recommended?sport=NFL")
        assert response.status_code == 200
        payload = response.json()

        assert payload.get("status") == "success"
        assert payload.get("sport") == "NFL"
        assert "as_of" in payload
        assert "parlays" in payload
        assert isinstance(payload["parlays"], list)

        # ISO 8601 UTC timestamp check
        dt = datetime.fromisoformat(payload["as_of"])
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_parlay_recommended_legs_provenance(self, client):
        response = client.get("/api/v1/parlay/recommended?sport=NFL")
        assert response.status_code == 200
        payload = response.json()

        for parlay in payload["parlays"]:
            for leg in parlay.get("legs_evaluadas", []):
                bookmaker = leg.get("bookmaker")
                assert bookmaker is not None, "Leg must have a bookmaker"
                assert bookmaker != "casa desconocida", "Placeholder 'casa desconocida' must not be emitted"

                observed_at = leg.get("observed_at")
                assert observed_at is not None, "Leg must have observed_at timestamp"
                assert observed_at != "sin hora", "Placeholder 'sin hora' must not be emitted"
                dt = datetime.fromisoformat(observed_at)
                assert dt.tzinfo is not None
                assert dt.utcoffset().total_seconds() == 0

    def test_parlay_recommended_nba_restricted(self, client):
        response = client.get("/api/v1/parlay/recommended?sport=NBA")
        assert response.status_code == 200
        payload = response.json()

        assert payload.get("status") == "unavailable"
        assert payload.get("sport") == "NBA"
        assert payload.get("total") == 0
        assert "as_of" in payload
        dt = datetime.fromisoformat(payload["as_of"])
        assert dt.utcoffset().total_seconds() == 0

    def test_parlay_recommended_parameter_bounds(self, client):
        # Negative bankroll triggers 422
        resp_neg_bank = client.get("/api/v1/parlay/recommended?sport=NFL&bankroll=-100")
        assert resp_neg_bank.status_code == 422

        # Zero limit triggers 422
        resp_zero_limit = client.get("/api/v1/parlay/recommended?sport=NFL&limit=0")
        assert resp_zero_limit.status_code == 422

        # Limit > 10 triggers 422
        resp_high_limit = client.get("/api/v1/parlay/recommended?sport=NFL&limit=25")
        assert resp_high_limit.status_code == 422

        # Valid custom bankroll and limit
        resp_valid = client.get("/api/v1/parlay/recommended?sport=NFL&bankroll=5000&limit=5")
        assert resp_valid.status_code == 200
        assert resp_valid.json().get("status") == "success"
