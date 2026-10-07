from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from datetime import datetime

from app.models.game import Game, Prediction

async def upsert_games(session: AsyncSession, games_data: List[Dict[str, Any]]) -> None:
    """
    Idempotent upsert of games based on 'external_id'.
    Handles both PostgreSQL and SQLite dialects for testing.
    """
    if not games_data:
        return

    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    
    if dialect_name == "sqlite":
        stmt = sqlite_insert(Game).values(games_data)
    else:
        stmt = pg_insert(Game).values(games_data)
        
    update_dict = {
        c.name: c for c in stmt.excluded 
        if c.name not in ("id", "external_id", "created_at")
    }
    
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_=update_dict
    )
    
    try:
        await session.execute(upsert_stmt)
    except Exception as e:
        await session.rollback()
        raise e

async def append_predictions(session: AsyncSession, predictions_data: List[Dict[str, Any]]) -> None:
    """
    Append-only insertion of predictions. 
    Uses ON CONFLICT DO NOTHING to prevent duplicates of the exact same prediction run.
    Rolls back transaction internally on failure.
    """
    if not predictions_data:
        return

    # Pre-validate that no deterministic key component is missing or null
    for row in predictions_data:
        for key in ["game_id", "model_version", "artifact_sha256", "dataset_sha256", "inference_input_sha256"]:
            if row.get(key) is None:
                raise ValueError(f"Missing required deterministic key component: {key}")

    dialect_name = session.bind.dialect.name if session.bind else "postgresql"
    
    if dialect_name == "postgresql":
        stmt = pg_insert(Prediction).values(predictions_data)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["game_id", "model_version", "artifact_sha256", "inference_input_sha256"]
        )
    else:
        from sqlalchemy import insert as sqla_insert
        stmt = sqla_insert(Prediction).values(predictions_data)
    
    try:
        await session.execute(stmt)
    except Exception as e:
        await session.rollback()
        raise e
