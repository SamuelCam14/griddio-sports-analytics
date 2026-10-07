"""Celery schedule for real market ingestion and explicitly gated ML tasks."""
import asyncio
from datetime import datetime, timezone
from celery import Celery
from celery.schedules import crontab
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

celery_app = Celery(
    "sports_analytics",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,        # 5 minutos límite por tarea
    worker_prefetch_multiplier=1,
)

# Enrutamiento de tareas a sus respectivas colas
celery_app.conf.task_routes = {
    "app.workers.celery_app.ingest_odds_*": {"queue": "ingestion"},
    "app.workers.celery_app.evaluate_predictions_*": {"queue": "ml"},
    "app.workers.celery_app.audit_card_*": {"queue": "alerts"},
}

# ─── Programación de Tareas Recurrentes (Celery Beat Schedule) ───────────────
celery_app.conf.beat_schedule = {
    "ingest-nba-odds-frequent": {
            "task": "app.workers.celery_app.ingest_odds_task",
            "schedule": crontab(minute=0, hour="*/4"),  # Every 4 hours to satisfy 6h freshness
            "args": ("NBA",),
        },
        "ingest-nfl-odds-frequent": {
            "task": "app.workers.celery_app.ingest_odds_task",
            "schedule": crontab(minute=0, hour="*/8"),  # Every 8 hours to satisfy 12h freshness
            "args": ("NFL",),
        },
        "ingest-nba-schedule-weekly": {
            "task": "app.workers.celery_app.ingest_nba_schedule_task",
            "schedule": crontab(minute=0, hour=3, day_of_week='1'),  # Mondays at 3 AM
        },
        "ingest-nfl-schedule-weekly": {
            "task": "app.workers.celery_app.ingest_nfl_schedule_task",
            "schedule": crontab(minute=0, hour=4, day_of_week='2'),  # Tuesdays at 4 AM
        },
        "evaluate-nba-predictions-frequent": {
            "task": "app.workers.celery_app.evaluate_predictions_task",
            "schedule": crontab(minute=5, hour="*/4"),
            "args": ("NBA",),
        },
        "evaluate-nfl-predictions-frequent": {
            "task": "app.workers.celery_app.evaluate_predictions_task",
            "schedule": crontab(minute=5, hour="*/8"),
            "args": ("NFL",),
        },
}


# ─── Tareas Celery ───────────────────────────────────────────────────────────

from app.services.freshness_service import ProviderError

@celery_app.task(
    name="app.workers.celery_app.ingest_odds_task", 
    queue="ingestion",
    bind=True,
    autoretry_for=(ProviderError,), # Only retry transient provider errors
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True
)
def ingest_odds_task(self, sport: str = "NBA"):
    """Fetch and persist market data through the same path as the API sync. Retries on failures."""
    from app.core.database import AsyncSessionLocal
    from app.services.live_ingestion_service import sync_real_games

    async def _sync():
        async with AsyncSessionLocal() as session:
            return await sync_real_games(sport=sport, db=session)

    logger.info("Executing persistent odds ingestion", sport=sport, attempt=self.request.retries)
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_sync())
        if result.get("status") == "error":
            if result.get("error_type") == "commit_error":
                raise RuntimeError(f"Database sync failed: {result.get('detail')}")
            else:
                from app.services.freshness_service import ProviderError
                raise ProviderError(result.get("detail", "Unknown provider error"))
        return result
    finally:
        loop.close()


@celery_app.task(name="app.workers.celery_app.evaluate_predictions_task", queue="ml")
def evaluate_predictions_task(sport: str = "ALL"):
    """Run fail-closed inference to update daily predictions for upcoming games."""
    from app.core.database import AsyncSessionLocal
    import asyncio
    
    async def _evaluate():
        from sqlalchemy import select
        from app.models.game import Game, OddsSnapshot, Prediction
        from app.services.persistence import append_predictions
        from ml.inference import predict_matchups
        from app.services.math.ev import calculate_ev
        import pandas as pd
        from datetime import datetime, timezone
        
        sports_to_run = ["NBA", "NFL"] if sport == "ALL" else [sport.upper()]
        
        async with AsyncSessionLocal() as session:
            results = []
            for sp in sports_to_run:
                try:
                    if sp == "NBA":
                        from ml.data_pipeline.nba_source import load_nba_games, load_nba_upcoming_games
                        completed = load_nba_games()
                        upcoming = load_nba_upcoming_games(as_of=pd.Timestamp.now(tz="UTC"))
                    elif sp == "NFL":
                        from ml.data_pipeline.nfl_source import load_nfl_games, load_nfl_upcoming_games
                        completed = load_nfl_games()
                        upcoming = load_nfl_upcoming_games(as_of=pd.Timestamp.now(tz="UTC"))
                    else:
                        continue
                        
                    if upcoming.empty:
                        logger.info(f"No upcoming games to predict for {sp}")
                        continue
                        
                    preds_df, meta = predict_matchups(sp.lower(), completed, upcoming, allow_experimental=True)
                    
                    if not meta.get("production_eligible"):
                        logger.warning(f"Model for {sp} is not production eligible. Skipping DB insert.")
                        continue
                        
                    from app.workers.espn_map import ESPN_NBA_ID_TO_ABBR, ESPN_NFL_ABBR_TO_ABBR
                    from app.models.game import Team
                    
                    # Preload team mappings for this sport
                    team_stmt = select(Team).where(Team.sport == sp)
                    team_res = await session.execute(team_stmt)
                    db_teams = {t.abbreviation: t.id for t in team_res.scalars().all()}
                    
                    predictions_data = []
                    for _, row in preds_df.iterrows():
                        game_dt = pd.to_datetime(row["game_date"]).date()
                        
                        if sp == "NBA":
                            home_abbr = ESPN_NBA_ID_TO_ABBR.get(int(row["home_team_id"]))
                            away_abbr = ESPN_NBA_ID_TO_ABBR.get(int(row["away_team_id"]))
                        else:
                            home_abbr = ESPN_NFL_ABBR_TO_ABBR.get(str(row["home_team_id"]))
                            away_abbr = ESPN_NFL_ABBR_TO_ABBR.get(str(row["away_team_id"]))
                            
                        if not home_abbr or not away_abbr:
                            continue
                            
                        home_db_id = db_teams.get(home_abbr)
                        away_db_id = db_teams.get(away_abbr)
                        
                        if not home_db_id or not away_db_id:
                            continue
                            
                        # Find matching game in DB
                        game_stmt = select(Game).where(Game.home_team_id == home_db_id).where(Game.away_team_id == away_db_id)
                        game_res = await session.execute(game_stmt)
                        matched_game = None
                        for db_game in game_res.scalars().all():
                            db_date = db_game.game_date.date()
                            if abs((db_date - game_dt).days) <= 1:
                                matched_game = db_game
                                break
                                
                        if not matched_game:
                            continue
                            
                        internal_id = matched_game.id
                        
                        if not internal_id:
                            continue
                        
                        # Get latest odds
                        odds_stmt = select(OddsSnapshot).where(OddsSnapshot.game_id == internal_id).order_by(OddsSnapshot.captured_at.desc()).limit(1)
                        odds_res = await session.execute(odds_stmt)
                        odds_snap = odds_res.scalar_one_or_none()
                        
                        best_bm = None
                        best_home_ml = None
                        best_away_ml = None
                        home_ev = 0.0
                        away_ev = 0.0
                        home_kelly = 0.0
                        away_kelly = 0.0
                        home_edge = False
                        away_edge = False
                        
                        if odds_snap and odds_snap.home_ml and odds_snap.away_ml:
                            best_bm = odds_snap.bookmaker
                            best_home_ml = odds_snap.home_ml
                            best_away_ml = odds_snap.away_ml
                            
                            h_bet = calculate_ev(prob_model=row["home_win_probability"], odds_american=best_home_ml)
                            a_bet = calculate_ev(prob_model=row["away_win_probability"], odds_american=best_away_ml)
                            
                            home_ev = h_bet.ev_dollars
                            away_ev = a_bet.ev_dollars
                            home_kelly = h_bet.kelly_fraction
                            away_kelly = a_bet.kelly_fraction
                            home_edge = h_bet.ev_dollars > 0 and h_bet.edge_pct > 0.03
                            away_edge = a_bet.ev_dollars > 0 and a_bet.edge_pct > 0.03
                            
                        pred_dict = {
                            "game_id": internal_id,
                            "model_version": meta["model_version"],
                            "dataset_sha256": meta["dataset_sha256"],
                            "artifact_sha256": meta["artifact_sha256"],
                            "inference_input_sha256": str(hash("".join(meta.get("schedule_source_refs", [])))),
                            "as_of": pd.Timestamp(meta["as_of"]).to_pydatetime(),
                            "home_win_prob": row["home_win_probability"],
                            "away_win_prob": row["away_win_probability"],
                            "best_bookmaker": best_bm,
                            "best_home_ml": best_home_ml,
                            "best_away_ml": best_away_ml,
                            "home_ev": home_ev,
                            "away_ev": away_ev,
                            "home_kelly": home_kelly,
                            "away_kelly": away_kelly,
                            "home_has_edge": home_edge,
                            "away_has_edge": away_edge,
                        }
                        predictions_data.append(pred_dict)
                        
                    await append_predictions(session, predictions_data)
                    await session.commit()
                    results.append({"sport": sp, "predicted": len(predictions_data)})
                    
                except Exception as e:
                    logger.error(f"Error predicting {sp}", exc_info=True)
                    results.append({"sport": sp, "error": str(e)})
                    
            return {"status": "success", "results": results}
            
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_evaluate())
    finally:
        loop.close()


@celery_app.task(name="app.workers.celery_app.audit_cards_arbitrage_task", queue="alerts")
def audit_cards_arbitrage_task():
    """
    Tarea Celery: Audita catálogo de Sports Cards y detecta oportunidades de arbitraje neto en eBay.
    """
    logger.warning("Card arbitrage audit skipped: no verified marketplace feed")
    return {"status": "skipped", "reason": "no_verified_marketplace_feed"}

@celery_app.task(name="app.workers.celery_app.ingest_nba_schedule_task", queue="ingestion")
def ingest_nba_schedule_task():
    import asyncio
    from app.core.database import AsyncSessionLocal
    from app.services.nba_schedule_service import sync_nba_games_from_source
    
    async def _run():
        async with AsyncSessionLocal() as db:
            await sync_nba_games_from_source(db=db)
    asyncio.run(_run())

@celery_app.task(name="app.workers.celery_app.ingest_nfl_schedule_task", queue="ingestion")
def ingest_nfl_schedule_task():
    import asyncio
    from app.core.database import AsyncSessionLocal
    from app.services.nfl_schedule_service import sync_nfl_games_from_source
    
    async def _run():
        async with AsyncSessionLocal() as db:
            await sync_nfl_games_from_source(db=db)
    asyncio.run(_run())
