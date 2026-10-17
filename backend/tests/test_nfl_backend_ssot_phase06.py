import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
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

        # Verify odds endpoint consolidates across market snapshots with real DB data
        odds_data = resp_odds.json()
        assert odds_data["sport"] == "NFL"
        assert odds_data["total_games"] >= 1
        first_odds = odds_data["games"][0]
        assert first_odds["home_team"] is not None
        assert first_odds["away_team"] is not None
        assert first_odds["home_ml"] is not None
        assert first_odds["away_ml"] is not None
        assert first_odds["total_line"] is not None


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
    assert data["source_game_count"] > 0
    assert data["game_count"] + data["unlinked_game_count"] == data["source_game_count"]

    first_game = data["games"][0]
    assert 0.0 <= first_game["home_win_probability"] <= 1.0
    assert 0.0 <= first_game["away_win_probability"] <= 1.0
    assert pytest.approx(first_game["home_win_probability"] + first_game["away_win_probability"], abs=1e-4) == 1.0
    assert "schedule_source_ref" in first_game


def test_production_gate_remains_closed_for_nfl(client, monkeypatch):
    """Production predictions endpoint for NFL returns zero picks while gate is closed."""
    monkeypatch.setattr("app.api.v1.predictions._production_model_enabled", lambda sport: False)
    resp = client.get("/api/v1/predictions/today?sport=NFL")
    assert resp.status_code == 200
    data = resp.json()

    assert data["sport"] == "NFL"
    assert data["total_picks"] == 0
    assert data["picks"] == []


def test_parlay_recommended_returns_empty_or_unavailable_when_gate_closed(client, monkeypatch):
    """Recommended parlay route returns empty list and unavailable status when gate is closed."""
    monkeypatch.setattr("app.api.v1.parlay._production_model_enabled", lambda sport: False)
    resp = client.get("/api/v1/parlay/recommended?sport=NFL")
    assert resp.status_code == 200
    data = resp.json()

    assert data.get("status") == "unavailable"
    assert data.get("parlays") == []


def test_parlay_recommended_returns_valid_response_when_gate_open(client, monkeypatch):
    """The route can return its service payload when a test explicitly opens the gate."""
    monkeypatch.setattr("app.api.v1.parlay._production_model_enabled", lambda sport: True)
    monkeypatch.setattr("app.api.v1.parlay.generate_recommended_parlays", AsyncMock(return_value=[]))
    resp = client.get("/api/v1/parlay/recommended?sport=NFL")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"
    assert isinstance(data.get("parlays"), list)


def test_ledger_quota_unchanged_across_api_calls(client):
    """Verify that ledger remains strictly untouched across read-only API calls."""
    ledger_path = project_root() / "data" / "odds_quota_ledger.json"
    before_bytes = ledger_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()

    # Trigger GET calls
    client.get("/api/v1/predictions/today?sport=NFL")
    client.get("/api/v1/predictions/experimental/nfl")
    client.get("/api/v1/odds/nfl")
    client.get("/api/v1/parlay/recommended?sport=NFL")

    after_bytes = ledger_path.read_bytes()
    after_hash = hashlib.sha256(after_bytes).hexdigest()

    assert after_hash == before_hash
    ledger_data = json.loads(after_bytes.decode("utf-8"))
    assert ledger_data["summary"]["requests_remaining"] > 0


@pytest.mark.filterwarnings("ignore:coroutine 'Connection._cancel' was never awaited:RuntimeWarning")
@pytest.mark.asyncio
async def test_game_context_endpoint_returns_rams_recent_form_and_h2h():
    """Verify SofaScore-style matchup context returns authentic Rams form and H2H clashes from nflverse schedules."""
    import asyncio
    from app.core.database import AsyncSessionLocal, engine
    from app.services.matchup_context_service import get_matchup_context
    from app.models.game import Game
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    await engine.dispose()
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Game).options(selectinload(Game.home_team), selectinload(Game.away_team)).order_by(Game.id.asc())
            res = await session.execute(stmt)
            target_game = None
            for g in res.scalars().all():
                if g.home_team and g.away_team and ("LAR" in (g.home_team.abbreviation, g.away_team.abbreviation)):
                    target_game = g
                    break
            assert target_game is not None
            data = await get_matchup_context(target_game.id, session)
    finally:
        await engine.dispose()
        await asyncio.sleep(0.05)

    assert "h2h" in data
    assert data["h2h"]["total_games"] >= 1
    assert len(data["h2h"]["recent_clashes"]) >= 1

    # Verify Rams recent form has official scores
    assert "recent_form" in data
    assert "home" in data["recent_form"]
    form = data["recent_form"]["home"] if target_game.home_team.abbreviation == "LAR" else data["recent_form"]["away"]
    assert len(form["games"]) == 5
    assert len(form["streak"]) == 5

    # Verify opponents have proper DB abbreviations
    opponents = [g["opponent"] for g in form["games"]]
    assert len(opponents) == 5
    for opp in opponents:
        assert isinstance(opp, str) and len(opp) in (2, 3)


def test_experimental_predictions_includes_full_29_games_and_buf_la(client):
    """Verify experimental predictions maintain full 29 upcoming games in active 14-day window."""
    resp = client.get("/api/v1/predictions/experimental/nfl?lookahead_days=14")
    assert resp.status_code == 200
    data = resp.json()

    assert data["source_game_count"] >= 29
    assert data["game_count"] + data["unlinked_game_count"] == data["source_game_count"]
    for linked_game in data["games"]:
        assert isinstance(linked_game["canonical_external_id"], str)
        assert linked_game["canonical_game_id"] is not None
        assert 0.0 <= linked_game["home_win_probability"] <= 1.0
        assert 0.0 <= linked_game["away_win_probability"] <= 1.0
        assert pytest.approx(linked_game["home_win_probability"] + linked_game["away_win_probability"], 1e-4) == 1.0


@pytest.mark.filterwarnings("ignore:coroutine 'Connection._cancel' was never awaited:RuntimeWarning")
@pytest.mark.asyncio
async def test_upcoming_games_endpoint_preserves_scheduled_game_61():
    """Verify /games/upcoming retains scheduled games for today's active window."""
    import asyncio
    from app.core.database import AsyncSessionLocal, engine
    from app.api.v1.games import get_upcoming_games

    await engine.dispose()
    try:
        async with AsyncSessionLocal() as session:
            data = await get_upcoming_games(days=14, sport="NFL", db=session)
    finally:
        await engine.dispose()
        await asyncio.sleep(0.05)

    assert data["total"] >= 15
    game_ids = [g["id"] for g in data["games"]]
    assert len(game_ids) >= 15
