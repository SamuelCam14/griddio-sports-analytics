import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.live_ingestion_service import sync_real_games
from app.models.game import Game, Team

@pytest.mark.asyncio
async def test_sync_real_games_integrated_flow():
    # 1. Setup mock database session
    db = AsyncMock()
    
    # Mocking teams in DB
    home_team = Team(id=1, name="Los Angeles Lakers")
    away_team = Team(id=2, name="Golden State Warriors")
    mock_game = Game(id=100, external_id="game_1", sport="NBA", home_team_id=1, away_team_id=2)
    
    async def sophisticated_execute(stmt, *args, **kwargs):
        mock_res = MagicMock(rowcount=1)
        s = str(stmt).lower()
        if "team" in s:
            scalars_mock = MagicMock()
            scalars_mock.all.return_value = [home_team, away_team]
            mock_res.scalars.return_value = scalars_mock
        elif "game" in s:
            mock_res.scalar_one_or_none.return_value = mock_game
        else:
            mock_res.rowcount = 1
        return mock_res
        
    db.execute.side_effect = sophisticated_execute
    
    # Setup test odds data
    now = datetime.now(timezone.utc)
    game_start = now + timedelta(hours=2)
    fresh_time = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_time = (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")  # NBA threshold is 6h
    
    odds_data = {
        "external_id": "game_1",
        "home_team": "Los Angeles Lakers",
        "away_team": "Golden State Warriors",
        "commence_time": game_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "best_bookmaker": "draftkings",
        "spread_bookmaker": "fanduel",
        "total_bookmaker": "pinnacle",
        "home_ml": -150,
        "away_ml": 130,
        "home_spread": -3.5,
        "away_spread": 3.5,
        "total_line": 220.5,
        "over_ml": -110,
        "under_ml": -110,
        "ml_last_update": fresh_time,       # Fresh
        "spread_last_update": stale_time,   # Stale!
        "totals_last_update": fresh_time    # Fresh
    }
    
    with patch("app.services.live_ingestion_service.fetch_odds") as mock_fetch:
        mock_fetch.return_value = [odds_data]
        
        with patch("app.services.espn_service.sync_espn_scores") as mock_results:
            mock_results.return_value = {"updated": 0}
            
            result = await sync_real_games("NBA", db)
            
            # Assertions
            assert result["status"] == "success"
            assert result["games_synced"] == 1
            assert result["snapshots_saved"] == 2 # ML and Totals are fresh
            assert result["stale_discarded"] == 1 # Spread is stale
            assert result["unknown_teams"] == 0
            
            db.commit.assert_called_once()
            db.rollback.assert_not_called()

@pytest.mark.asyncio
async def test_sync_real_games_commit_failure():
    db = AsyncMock()
    
    async def sophisticated_execute(stmt, *args, **kwargs):
        mock_res = MagicMock(rowcount=1)
        s = str(stmt).lower()
        if "team" in s:
            scalars_mock = MagicMock()
            scalars_mock.all.return_value = [Team(id=1, name="LAL"), Team(id=2, name="GSW")]
            mock_res.scalars.return_value = scalars_mock
        elif "game" in s:
            mock_res.scalar_one_or_none.return_value = Game(id=100)
        else:
            mock_res.rowcount = 1
        return mock_res
        
    db.execute.side_effect = sophisticated_execute
    db.commit.side_effect = Exception("DB Connection Lost")
    
    odds_data = {
        "external_id": "game_1",
        "home_team": "LAL",
        "away_team": "GSW",
        "commence_time": (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "best_bookmaker": "draftkings",
        "home_ml": -150,
        "away_ml": 130,
        "ml_last_update": (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    
    with patch("app.services.live_ingestion_service.fetch_odds", return_value=[odds_data]):
        with patch("app.services.espn_service.sync_espn_scores", return_value={}):
            result = await sync_real_games("NBA", db)
            
            assert result["status"] == "error"
            assert result["error_type"] == "commit_error"
            db.rollback.assert_called_once()
