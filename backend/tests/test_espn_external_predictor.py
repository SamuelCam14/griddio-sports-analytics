from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.espn_service import fetch_espn_matchup_predictor

FUTURE_KICKOFF = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
FUTURE_KICKOFF_ISO = FUTURE_KICKOFF.strftime("%Y-%m-%dT%H:%M:%SZ")


def _game():
    return SimpleNamespace(
        id=1,
        sport="NFL",
        game_date=FUTURE_KICKOFF,
        home_team=SimpleNamespace(name="Denver Broncos", abbreviation="DEN"),
        away_team=SimpleNamespace(name="Seattle Seahawks", abbreviation="SEA"),
    )


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class _Client:
    def __init__(self, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append(url)
        if "scoreboard" in url:
            return _Response({"events": [{
                "id": "401872995",
                "date": FUTURE_KICKOFF_ISO,
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"id": "7", "abbreviation": "DEN"}},
                    {"homeAway": "away", "team": {"id": "26", "abbreviation": "SEA"}},
                ]}],
            }]})
        return _Response({"predictor": {
            "header": "Matchup Predictor",
            "homeTeam": {"id": "7", "gameProjection": "46.8"},
            "awayTeam": {"id": "26", "gameProjection": "52.9"},
        }})


@pytest.mark.asyncio
async def test_external_predictor_requires_exact_event_and_team_identity():
    with patch("app.services.espn_service.httpx.AsyncClient", _Client):
        result = await fetch_espn_matchup_predictor(_game())

    assert result["status"] == "available"
    assert result["home_win_probability"] == pytest.approx(0.468)
    assert result["away_win_probability"] == pytest.approx(0.529)
    assert result["probability_sum"] == pytest.approx(0.997)
    assert result["source"] == "ESPN Matchup Predictor"
    assert result["production_model"] is False
    assert result["event_id"] == "401872995"


@pytest.mark.asyncio
async def test_external_predictor_fails_closed_on_kickoff_mismatch():
    class WrongKickoffClient(_Client):
        async def get(self, url, **kwargs):
            response = await super().get(url, **kwargs)
            if "scoreboard" in url:
                response.body["events"][0]["date"] = (FUTURE_KICKOFF + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return response

    with patch("app.services.espn_service.httpx.AsyncClient", WrongKickoffClient):
        result = await fetch_espn_matchup_predictor(_game())

    assert result["status"] == "unavailable"
    assert result["reason"] == "event_identity_not_unique"
    assert result["matching_events"] == 0


@pytest.mark.asyncio
async def test_external_predictor_never_reads_in_game_or_postgame_projection():
    game = _game()
    game.game_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with patch("app.services.espn_service.httpx.AsyncClient", side_effect=AssertionError("network must not be queried")):
        result = await fetch_espn_matchup_predictor(game)

    assert result["status"] == "unavailable"
    assert result["reason"] == "event_started_or_finished"
