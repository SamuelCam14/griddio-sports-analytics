import pytest
import asyncio
from unittest.mock import AsyncMock
from sqlalchemy.dialects import postgresql
from app.services.persistence import upsert_games, append_predictions

@pytest.mark.asyncio
async def test_upsert_games_idempotent_query():
    session = AsyncMock()
    session.bind.dialect.name = "postgresql"
    games = [
        {"external_id": "ext1", "sport": "NBA", "season": "2026", "game_date": "2026-10-10", "home_team_id": 1, "away_team_id": 2, "source": "test_src"}
    ]
    await upsert_games(session, games)
    
    session.execute.assert_called_once()
    stmt = session.execute.call_args[0][0]
    
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql_str = str(compiled).lower()
    
    assert "on conflict (external_id) do update" in sql_str
    assert "source = excluded.source" in sql_str
    assert "set external_id" not in sql_str

@pytest.mark.asyncio
async def test_append_predictions_maintains_history():
    session = AsyncMock()
    session.bind.dialect.name = "postgresql"
    preds = [
        {"game_id": 1, "model_version": "nfl_temporal_v1", "artifact_sha256": "art_hash", "dataset_sha256": "data_hash", "inference_input_sha256": "inf_hash", "home_win_prob": 0.6, "away_win_prob": 0.4}
    ]
    await append_predictions(session, preds)
    
    session.execute.assert_called_once()
    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql_str = str(compiled).lower()
    
    assert "on conflict (game_id, model_version, artifact_sha256, inference_input_sha256) do nothing" in sql_str
    assert "update" not in sql_str

@pytest.mark.asyncio
async def test_append_predictions_pre_validation_error():
    session = AsyncMock()
    preds_missing_inference = [
        {"game_id": 1, "model_version": "v1", "artifact_sha256": "art", "dataset_sha256": "data_hash"}
    ]
    with pytest.raises(ValueError, match="Missing required deterministic key component: inference_input_sha256"):
        await append_predictions(session, preds_missing_inference)

@pytest.mark.asyncio
async def test_upsert_games_rollback_on_error():
    session = AsyncMock()
    session.bind.dialect.name = "postgresql"
    session.execute.side_effect = Exception("DB Error")
    games = [{"external_id": "ext1", "sport": "NBA", "season": "2026", "game_date": "2026-10-10", "home_team_id": 1, "away_team_id": 2}]
    
    with pytest.raises(Exception, match="DB Error"):
        await upsert_games(session, games)
        
    # Verify rollback was actually called on the session
    session.rollback.assert_called_once()

@pytest.mark.asyncio
async def test_append_predictions_rollback_on_error():
    session = AsyncMock()
    session.execute.side_effect = Exception("DB Error")
    preds = [{"game_id": 1, "model_version": "v1", "artifact_sha256": "art", "dataset_sha256": "data", "inference_input_sha256": "inf"}]
    
    with pytest.raises(Exception, match="DB Error"):
        await append_predictions(session, preds)
        
    # Verify rollback was actually called
    session.rollback.assert_called_once()
