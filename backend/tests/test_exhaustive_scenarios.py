import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from app.models.game import Base, Game, Team, OddsSnapshot
from app.services.live_ingestion_service import sync_real_games
from app.workers.celery_app import ingest_nba_schedule_task, ingest_nfl_schedule_task
from unittest.mock import patch

async def setup_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, async_session

@pytest.mark.asyncio
async def test_exhaustive_idempotency_different_captured_at():
    """Test two inserts with same observed_at but different captured_at to verify idempotency."""
    engine, async_session = await setup_test_db()
    async with async_session() as test_db:
        t1 = Team(id=1, name="Los Angeles Lakers", api_id="lal", abbreviation="LAL", sport="NBA")
        t2 = Team(id=2, name="Golden State Warriors", api_id="gsw", abbreviation="GSW", sport="NBA")
        test_db.add_all([t1, t2])
        await test_db.commit()

        now = datetime.now(timezone.utc)
        game_start = now + timedelta(hours=2)
        observed_time = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        odds_data = {
            "external_id": "game_1",
            "home_team": "Los Angeles Lakers",
            "away_team": "Golden State Warriors",
            "commence_time": game_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "best_bookmaker": "draftkings",
            "home_ml": -150,
            "away_ml": 130,
            "ml_last_update": observed_time,
        }
        
        with patch("app.services.live_ingestion_service.fetch_odds", return_value=[odds_data]):
            with patch("app.services.espn_service.sync_espn_scores", return_value={"updated": 0}):
                # First run
                res1 = await sync_real_games("NBA", test_db)
                assert res1["status"] == "success"
                assert res1["snapshots_saved"] == 1
                
                # Second run (simulating retry with new captured_at but same odds)
                res2 = await sync_real_games("NBA", test_db)
                assert res2["status"] == "success"
                assert res2["snapshots_saved"] == 0 # Should do nothing because of ON CONFLICT DO NOTHING
                
                # Verify DB only has 1 snapshot
                from sqlalchemy import select
                result = await test_db.execute(select(OddsSnapshot))
                snapshots = result.scalars().all()
                assert len(snapshots) == 1
    await engine.dispose()

@pytest.mark.asyncio
async def test_exhaustive_multiple_markets_different_updates():
    """Test saving multiple markets with different observed_at times."""
    engine, async_session = await setup_test_db()
    async with async_session() as test_db:
        t1 = Team(id=1, name="Los Angeles Lakers", api_id="lal", abbreviation="LAL", sport="NBA")
        t2 = Team(id=2, name="Golden State Warriors", api_id="gsw", abbreviation="GSW", sport="NBA")
        test_db.add_all([t1, t2])
        await test_db.commit()

        now = datetime.now(timezone.utc)
        game_start = now + timedelta(hours=2)
        fresh_time = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_time = (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        odds_data = {
            "external_id": "game_1",
            "home_team": "Los Angeles Lakers",
            "away_team": "Golden State Warriors",
            "commence_time": game_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "best_bookmaker": "draftkings",
            "spread_bookmaker": "fanduel",
            "home_ml": -150,
            "away_ml": 130,
            "home_spread": -3.5,
            "away_spread": 3.5,
            "ml_last_update": fresh_time,       # Fresh
            "spread_last_update": stale_time,   # Stale -> should be discarded
        }
        
        with patch("app.services.live_ingestion_service.fetch_odds", return_value=[odds_data]):
            with patch("app.services.espn_service.sync_espn_scores", return_value={"updated": 0}):
                res = await sync_real_games("NBA", test_db)
                assert res["status"] == "success"
                assert res["snapshots_saved"] == 1
                assert res["stale_discarded"] == 1
    await engine.dispose()

def test_schedule_tasks_import_correctly():
    """Verify that schedule tasks execute without ModuleNotFoundError."""
    # Since we defined them locally and mocked them, we just ensure they can be called.
    with patch("app.services.nba_schedule_service.sync_nba_games_from_source") as mock_nba:
        # Mock database connection to avoid hitting real database logic
        with patch("app.core.database.AsyncSessionLocal"):
            ingest_nba_schedule_task()
            mock_nba.assert_called_once()
        
    with patch("app.services.nfl_schedule_service.sync_nfl_games_from_source") as mock_nfl:
        with patch("app.core.database.AsyncSessionLocal"):
            ingest_nfl_schedule_task()
            mock_nfl.assert_called_once()
