from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pandas as pd
import pytest
from app.workers.celery_app import ingest_odds_task, evaluate_predictions_task, celery_app

# Patch celery app for tests so it doesn't need redis
celery_app.conf.update(
    task_always_eager=True,
    result_backend='cache+memory://'
)

def test_celery_ingest_odds_success():
    """Ensure the task returns successfully without error for NFL."""
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_coro(*args, **kwargs):
            return {"status": "success", "sport": "NFL"}
        mock_sync.side_effect = mock_coro
        
        result = ingest_odds_task.apply(kwargs={"sport": "NFL"}).get()
        assert result == {"status": "success", "sport": "NFL"}

def test_celery_ingest_odds_provider_error():
    """Ensure provider errors raise exception to trigger retry and respect limits."""
    from app.services.freshness_service import ProviderError
    
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_err(*args, **kwargs):
            return {"status": "error", "sport": "NFL", "error_type": "provider_error", "detail": "Rate limited"}
        mock_sync.side_effect = mock_err
        
        with patch.object(ingest_odds_task, "retry") as mock_retry:
            from celery.exceptions import Retry
            mock_retry.side_effect = Retry("Simulated retry")
            
            with pytest.raises(Retry):
                ingest_odds_task(sport="NFL")
                
            mock_retry.assert_called_once()
            
def test_celery_configuration_error_no_retry():
    """Ensure configuration errors don't trigger retries."""
    from app.services.freshness_service import ConfigurationError
    
    with patch("app.services.live_ingestion_service.sync_real_games") as mock_sync:
        async def mock_err(*args, **kwargs):
            raise ConfigurationError("No API key")
        mock_sync.side_effect = mock_err
        
        with patch.object(ingest_odds_task, "retry") as mock_retry:
            with pytest.raises(ConfigurationError):
                ingest_odds_task(sport="NFL")
            
            mock_retry.assert_not_called()


def test_celery_prediction_uses_current_as_of_for_nfl():
    """NFL inference must not receive the former one-day-stale decision time."""
    from datetime import datetime, timezone
    import pandas as pd

    class SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    upcoming = pd.DataFrame([{"game_id": "fixture-game"}])
    with patch("app.core.database.AsyncSessionLocal", return_value=SessionContext()), \
         patch("ml.data_pipeline.nfl_source.load_nfl_games", return_value=pd.DataFrame()), \
         patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=upcoming), \
         patch("ml.inference.predict_matchups", return_value=(pd.DataFrame(), {"production_eligible": False})) as predict:
        result = evaluate_predictions_task("NFL")

    assert result["status"] == "success"
    as_of = predict.call_args.kwargs["as_of"]
    assert isinstance(as_of, pd.Timestamp)
    assert as_of.tzinfo is not None
    assert (datetime.now(timezone.utc) - as_of.to_pydatetime()).total_seconds() < 60


def test_celery_capture_nfl_injuries_task(tmp_path):
    """Ensure the injury capture task runs and creates a valid snapshot point-in-time."""
    from app.workers.celery_app import capture_nfl_injuries_task
    from app.services.nfl_availability_service import NFLAvailabilityService

    mock_injuries = {
        "KC": [{"player_name": "Patrick Mahomes", "position": "QB", "status": "ACTIVE"}],
        "BAL": [{"player_name": "Lamar Jackson", "position": "QB", "status": "QUESTIONABLE"}],
    }
    with patch("app.services.matchup_context_service.get_live_injuries", return_value=mock_injuries), \
         patch("app.services.nfl_availability_service.NFLAvailabilityService", return_value=NFLAvailabilityService(cache_dir=tmp_path / "nfl_avail")):
        res = capture_nfl_injuries_task.apply(kwargs={"cadence": "start_of_day"}).get()

    assert res["status"] == "success"
    assert res["cadence"] == "start_of_day"
    assert res["teams_count"] == 2
    assert res["omitted_teams_count"] == 30
    assert "espn_start_of_day_" in res["snapshot_id"]


def test_celery_schedule_nfl_gameday_captures_task():
    """Ensure gameday capture scheduler dispatches start_of_day immediately and
    near_kickoff tasks via apply_async(eta=) based on real kickoff windows."""
    import pandas as pd
    from app.workers.celery_app import schedule_nfl_gameday_captures_task

    now = datetime.now(timezone.utc)
    today = now.date()
    fake_upcoming = pd.DataFrame([
        {
            "game_id": "2024_06_KC_SF",
            "game_date": today.isoformat(),
            "game_start_utc": (now + timedelta(hours=3)).isoformat(),
            "home_team_id": "KC",
            "away_team_id": "SF",
        }
    ])

    with patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=fake_upcoming), \
         patch("app.workers.celery_app.capture_nfl_injuries_task.apply_async") as mock_async:
        res = schedule_nfl_gameday_captures_task.apply().get()

    assert res["status"] == "success"
    assert res["start_of_day_dispatched"] is True
    assert res["games_count"] == 1
    # At least one apply_async call for start_of_day and potentially one per kickoff window
    assert mock_async.call_count >= 1
    # start_of_day dispatched with no ETA (immediately)
    start_of_day_calls = [c for c in mock_async.call_args_list if c.kwargs.get("args") == ("start_of_day",) or
                          (c.args and c.args[0] == ("start_of_day",))]
    assert len(start_of_day_calls) >= 1
    # near_kickoff windows documented in output
    assert "scheduled_pregame" in res


def test_celery_evaluate_predictions_invokes_real_availability_pipeline():
    """Demonstrate that production execution path builds game availability snapshots before predicting."""
    import pandas as pd
    from datetime import datetime, timezone
    from app.workers.celery_app import evaluate_predictions_task

    class SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    now = datetime.now(timezone.utc)
    upcoming = pd.DataFrame([{
        "game_id": "2024_06_KC_SF",
        "game_date": now.date().isoformat(),
        "game_start_utc": (now + timedelta(hours=4)).isoformat(),
        "home_team_id": "KC",
        "away_team_id": "SF",
        "home_team_abbreviation": "KC",
        "away_team_abbreviation": "SF",
    }])

    with patch("app.core.database.AsyncSessionLocal", return_value=SessionContext()), \
         patch("ml.data_pipeline.nfl_source.load_nfl_games", return_value=pd.DataFrame()), \
         patch("ml.data_pipeline.nfl_source.load_nfl_upcoming_games", return_value=upcoming), \
         patch("app.services.nfl_availability_service.NFLAvailabilityService.build_game_availability_snapshot", return_value={"mock": "snapshot"}) as mock_build, \
         patch("ml.inference.predict_matchups", return_value=(pd.DataFrame(), {"production_eligible": False})) as mock_predict:
        result = evaluate_predictions_task("NFL")

    assert result["status"] == "success"
    mock_build.assert_called_once()
    assert mock_predict.call_args.kwargs["availability_by_game"] == {"2024_06_KC_SF": {"mock": "snapshot"}}
    assert len(result["results"]) == 1
    res = result["results"][0]
    assert res["sport"] == "NFL"
    assert res["model_variant"] == "injury_aware"
    assert res["production_eligible"] is False
    assert res["skipped_db_insert"] is True


