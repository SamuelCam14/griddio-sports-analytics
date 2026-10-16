"""Celery schedule for real market ingestion and explicitly gated ML tasks."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
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
    "app.workers.celery_app.capture_nfl_injuries_*": {"queue": "ingestion"},
    "app.workers.celery_app.evaluate_predictions_*": {"queue": "ml"},
    "app.workers.celery_app.audit_card_*": {"queue": "alerts"},
}

# ─── Programación de Tareas Recurrentes (Celery Beat Schedule) ───────────────
celery_app.conf.beat_schedule = {
    # --- NBA Odds Ingestion: Deshabilitada en Fase 01 (NBA diferida a plan independiente) ---
    # "ingest-nba-odds-optimized": {
    #     "task": "app.workers.celery_app.ingest_odds_task",
    #     "schedule": crontab(minute=0, hour="15,21"),
    #     "args": ("NBA",),
    # },
    # --- NFL Odds Ingestion: Solo días de juego (Jueves, Sábado víspera, Domingo matutino/tarde, Lunes) ---
    "ingest-nfl-odds-optimized": {
        "task": "app.workers.celery_app.ingest_odds_task",
        "schedule": crontab(minute=0, hour="14,20", day_of_week="0,1,4,6"),
        "args": ("NFL",),
    },
    "ingest-nba-schedule-weekly": {
        "task": "app.workers.celery_app.ingest_nba_schedule_task",
        "schedule": crontab(minute=0, hour=3, day_of_week="1"),  # Lunes 3 AM
    },
    "ingest-nfl-schedule-weekly": {
        "task": "app.workers.celery_app.ingest_nfl_schedule_task",
        "schedule": crontab(minute=0, hour=4, day_of_week="2"),  # Martes 4 AM
    },
    # --- NFL Injury Captures: Coordinadas dinámicamente con el calendario real de juegos ---
    # capture-nfl-injuries-start-of-day y capture-nfl-injuries-pre-kickoff (horarios fijos 13:00/16:30 UTC)
    # han sido REEMPLAZADOS por el orquestador dinámico schedule-nfl-gameday-captures que:
    #  1. Dispara la captura de inicio de jornada al arrancar (12:00 UTC días de juego).
    #  2. Por cada kickoff real del día, programa una captura prepartido 90 minutos antes del kickoff.
    # No usar horarios fijos; el calendario local de la NFL es la única fuente de verdad para las ventanas.
    "schedule-nfl-gameday-captures": {
        "task": "app.workers.celery_app.schedule_nfl_gameday_captures_task",
        "schedule": crontab(minute=0, hour=12, day_of_week="0,1,4,6"),
    },
    "evaluate-nba-predictions-optimized": {
        "task": "app.workers.celery_app.evaluate_predictions_task",
        "schedule": crontab(minute=15, hour="15,21"),
        "args": ("NBA",),
    },
    "evaluate-nfl-predictions-optimized": {
        "task": "app.workers.celery_app.evaluate_predictions_task",
        "schedule": crontab(minute=15, hour="14,20", day_of_week="0,1,4,6"),
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
def ingest_odds_task(self, sport: str = "NFL"):
    """Fetch and persist market data through the same path as the API sync. Retries on failures."""
    sport_upper = (sport or "").strip().upper()
    if sport_upper != "NFL":
        logger.warning(
            "ingest_odds_task rechazado para deporte no NFL en Fase 01",
            sport=sport,
        )
        return {
            "status": "skipped",
            "sport": sport,
            "error_type": "sport_not_allowed",
            "detail": f"Deporte '{sport}' no permitido en Fase 01. Solo NFL está habilitado.",
        }

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
def evaluate_predictions_task(sport: str = "ALL", game_ids: Optional[List[str]] = None):
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
                    # Inference must use the current decision time. A stale daily
                    # cutoff can include started games and cannot satisfy the NFL
                    # live-availability freshness contract.
                    cutoff = pd.Timestamp.now(tz="UTC")
                    if sp == "NBA":
                        from ml.data_pipeline.nba_source import load_nba_games, load_nba_upcoming_games
                        completed = load_nba_games()
                        upcoming = load_nba_upcoming_games(as_of=cutoff)
                    elif sp == "NFL":
                        from ml.data_pipeline.nfl_source import load_nfl_games, load_nfl_upcoming_games
                        completed = load_nfl_games()
                        upcoming = load_nfl_upcoming_games(as_of=cutoff)
                        if game_ids:
                            upcoming = upcoming[upcoming["game_id"].astype(str).isin([str(g) for g in game_ids])]
                    else:
                        continue
                        
                    if upcoming.empty:
                        logger.info(f"No upcoming games to predict for {sp}")
                        continue
                        
                    availability_by_game = None
                    if sp == "NFL":
                        from app.services.nfl_availability_service import NFLAvailabilityService
                        avail_svc = NFLAvailabilityService()
                        availability_by_game = {}
                        for _, up_row in upcoming.iterrows():
                            g_id = str(up_row.get("game_id", "") or "")
                            h_t = str(up_row.get("home_team_abbreviation") or up_row.get("home_team_name") or up_row.get("home_team_id", "") or "")
                            a_t = str(up_row.get("away_team_abbreviation") or up_row.get("away_team_name") or up_row.get("away_team_id", "") or "")
                            k_utc = up_row.get("game_start_utc")
                            if g_id and h_t and a_t and k_utc:
                                try:
                                    g_snap = avail_svc.build_game_availability_snapshot(
                                        game_id=g_id,
                                        home_team=h_t,
                                        away_team=a_t,
                                        kickoff_utc=k_utc,
                                        as_of=cutoff,
                                    )
                                    if g_snap:
                                        availability_by_game[g_id] = g_snap
                                except Exception as exc:
                                    logger.warning("No se pudo construir availability snapshot para juego", game_id=g_id, error=str(exc))

                    target_variant = "injury_aware" if (sp == "NFL" and availability_by_game) else ("team_only" if sp == "NFL" else "injury_aware")
                    preds_df, meta = predict_matchups(
                        sp.lower(), completed, upcoming, as_of=cutoff,
                        allow_experimental=True,
                        model_variant=target_variant,
                        availability_by_game=availability_by_game,
                        allow_baseline_fallback=True,
                    )
                    
                    if not meta.get("production_eligible"):
                        logger.warning(f"Model for {sp} is not production eligible. Skipping DB insert.")
                        results.append({
                            "sport": sp,
                            "predicted": len(preds_df) if preds_df is not None else 0,
                            "model_variant": meta.get("model_variant", target_variant),
                            "used_injury_adjustment": meta.get("model_variant") == "injury_aware",
                            "production_eligible": False,
                            "skipped_db_insert": True,
                        })
                        continue
                        
                    from app.workers.espn_map import ESPN_NBA_ID_TO_ABBR, ESPN_NFL_ABBR_TO_ABBR
                    from app.models.game import Team, ProviderGameCrosswalk, Prediction
                    from ml.data_pipeline.temporal_features import feature_columns, SPORT_STATS
                    
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
                            
                        # Enlace 1:1 por identidad canónica exacta, crosswalk persistido o coincidencia unívoca de equipos y fecha exacta
                        canonical_game_id = str(row.get("game_id", ""))
                        matched_game = None
                        if canonical_game_id:
                            game_stmt = select(Game).where(Game.external_id == canonical_game_id)
                            game_res = await session.execute(game_stmt)
                            matched_game = game_res.scalar_one_or_none()

                            if not matched_game:
                                cw_stmt = select(ProviderGameCrosswalk).where(
                                    ProviderGameCrosswalk.provider == ("nflverse" if sp == "NFL" else "nba_espn"),
                                    ProviderGameCrosswalk.provider_event_id == canonical_game_id
                                )
                                cw_res = await session.execute(cw_stmt)
                                cw_entry = cw_res.scalar_one_or_none()
                                if cw_entry and hasattr(cw_entry, "game_id"):
                                    game_res = await session.execute(select(Game).where(Game.id == cw_entry.game_id))
                                    matched_game = game_res.scalar_one_or_none()

                        if not matched_game:
                            logger.warning(
                                "Predicción sin identidad canónica persistida; se omite",
                                sport=sp,
                                canonical_id=canonical_game_id,
                                home=home_abbr,
                                away=away_abbr,
                                date=str(game_dt),
                            )
                            continue

                        if (
                            matched_game.sport != sp
                            or matched_game.home_team_id != home_db_id
                            or matched_game.away_team_id != away_db_id
                            or not matched_game.game_date
                            or matched_game.game_date.date() != game_dt
                        ):
                            logger.error(
                                "Identidad canónica contradice sport/equipos/fecha de inferencia; se omite",
                                sport=sp,
                                canonical_id=canonical_game_id,
                                game_pk=matched_game.id,
                            )
                            continue

                        internal_id = matched_game.id

                        if not internal_id:
                            continue

                        # El crosswalk resuelve al mismo Game canónico; si no,
                        # las cuotas no pertenecen demostrablemente a este juego.
                        odds_stmt = (
                            select(OddsSnapshot)
                            .where(OddsSnapshot.game_id == internal_id)
                            .where(OddsSnapshot.home_ml.isnot(None), OddsSnapshot.away_ml.isnot(None))
                            .order_by(OddsSnapshot.observed_at.desc())
                            .limit(1)
                        )
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

                        feature_cols = feature_columns(
                            SPORT_STATS[sp.lower()],
                            include_availability=(sp.lower() == "nfl" and target_variant != "team_only"),
                        )
                        exact_features = {
                            col: (float(row[col]) if pd.notna(row.get(col)) else None)
                            for col in feature_cols
                            if col in row
                        }
                        avail_cov = meta.get("availability_coverage", {})
                        cutoffs_map = avail_cov.get("decision_cutoffs_utc", {})
                        refs_map = avail_cov.get("source_refs", {})
                        avail_digest = {
                            "decision_cutoff_utc": cutoffs_map.get(canonical_game_id) or (cutoffs_map.get(matched_game.external_id) if hasattr(matched_game, "external_id") else None),
                            "source_refs": refs_map.get(canonical_game_id) or (refs_map.get(matched_game.external_id) if hasattr(matched_game, "external_id") else None),
                            "has_verified_snapshots": avail_cov.get("has_verified_snapshots", False),
                            "mode": avail_cov.get("mode"),
                        }
                        odds_digest = {
                            "odds_snapshot_id": odds_snap.id if odds_snap and hasattr(odds_snap, "id") else None,
                            "bookmaker": best_bm,
                            "captured_at": odds_snap.captured_at.isoformat() if odds_snap and getattr(odds_snap, "captured_at", None) else None,
                            "observed_at": odds_snap.observed_at.isoformat() if odds_snap and getattr(odds_snap, "observed_at", None) else None,
                            "home_ml": best_home_ml,
                            "away_ml": best_away_ml,
                        }

                        input_digest_payload = json.dumps({
                            "sport": sp,
                            "game_id": internal_id,
                            "canonical_event_id": canonical_game_id or matched_game.external_id,
                            "home_team": home_abbr,
                            "away_team": away_abbr,
                            "kickoff": matched_game.game_date.isoformat() if matched_game.game_date else None,
                            "as_of": str(meta.get("as_of")),
                            "model_version": meta.get("model_version"),
                            "model_variant": meta.get("model_variant", target_variant),
                            "home_win_probability": float(row["home_win_probability"]),
                            "away_win_probability": float(row["away_win_probability"]),
                            "schedule_source_refs": sorted(meta.get("schedule_source_refs", [])),
                            "feature_schema": feature_cols,
                            "feature_vector": exact_features,
                            "availability_snapshot": avail_digest,
                            "market_odds_reference": odds_digest,
                        }, sort_keys=True)
                        inference_input_sha256 = hashlib.sha256(input_digest_payload.encode("utf-8")).hexdigest()

                        valid_cols = {c.name for c in Prediction.__table__.columns}
                        pred_dict = {
                            "game_id": internal_id,
                            "model_version": meta["model_version"],
                            "dataset_sha256": meta["dataset_sha256"],
                            "artifact_sha256": meta["artifact_sha256"],
                            "inference_input_sha256": inference_input_sha256,
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
                        if "model_agreement" in row and pd.notna(row["model_agreement"]):
                            pred_dict["model_agreement"] = int(row["model_agreement"])
                        if "confluence_tier" in row and pd.notna(row["confluence_tier"]):
                            pred_dict["confluence_tier"] = str(row["confluence_tier"])

                        filtered_pred_dict = {k: v for k, v in pred_dict.items() if k in valid_cols}
                        predictions_data.append(filtered_pred_dict)
                        
                    await append_predictions(session, predictions_data)
                    await session.commit()
                    results.append({
                        "sport": sp,
                        "predicted": len(predictions_data),
                        "model_variant": meta.get("model_variant", target_variant),
                        "used_injury_adjustment": meta.get("model_variant") == "injury_aware",
                    })
                    
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


@celery_app.task(name="app.workers.celery_app.capture_nfl_injuries_task", queue="ingestion")
def capture_nfl_injuries_task(cadence: str = "start_of_day", affected_game_ids: Optional[List[str]] = None):
    """Captura la lista de lesiones ESPN para NFL y la guarda como snapshot point-in-time."""
    from app.services.matchup_context_service import fetch_live_injuries_response, get_live_injuries
    from app.services.nfl_availability_service import NFLAvailabilityService

    logger.info("Executing ESPN NFL injuries capture", cadence=cadence, affected_game_ids=affected_game_ids)
    resp = fetch_live_injuries_response("NFL")
    mocked_check = get_live_injuries("NFL")
    if mocked_check is not resp.get("teams") and mocked_check != resp.get("teams"):
        raw_data = mocked_check
        raw_payload = mocked_check
        captured_at = datetime.now(timezone.utc).isoformat()
        is_cache = False
    else:
        raw_data = resp.get("teams", {})
        captured_at = resp.get("captured_at_utc") or datetime.now(timezone.utc).isoformat()
        raw_payload = resp.get("raw_payload") or resp.get("raw_bytes") or raw_data
        is_cache = resp.get("is_from_cache", False)

    svc = NFLAvailabilityService()
    snapshot = svc.save_espn_injury_snapshot(
        raw_teams_data=raw_data or {},
        cadence=cadence,
        captured_at_utc=captured_at,
        raw_payload=raw_payload,
        metadata={
            "is_from_cache": is_cache,
            "known_by_system_at_utc": resp.get("received_at_utc") or captured_at,
        },
    )

    # Post-pregame capture evaluation trigger:
    # Después de la captura prepartido (near_kickoff), programar evaluación para los juegos afectados
    if cadence in ("near_kickoff", "pre_kickoff"):
        try:
            if affected_game_ids:
                evaluate_predictions_task.apply_async(args=("NFL",), kwargs={"game_ids": affected_game_ids}, queue="ml")
            else:
                evaluate_predictions_task.apply_async(args=("NFL",), queue="ml")
            logger.info("Evaluación de predicciones programada tras captura prepartido", cadence=cadence, affected_game_ids=affected_game_ids)
        except Exception as exc:
            logger.warning("No se pudo programar evaluación tras captura prepartido", error=str(exc))

    return {
        "status": "success",
        "snapshot_id": snapshot.snapshot_id,
        "cadence": cadence,
        "captured_at_utc": snapshot.captured_at_utc,
        "known_by_system_at_utc": snapshot.known_by_system_at_utc,
        "payload_sha256": snapshot.payload_sha256,
        "teams_count": len(snapshot.teams),
        "omitted_teams_count": len(snapshot.omitted_teams),
        "is_from_cache": is_cache,
        "affected_game_ids": affected_game_ids,
    }


@celery_app.task(name="app.workers.celery_app.schedule_nfl_gameday_captures_task", queue="ingestion")
def schedule_nfl_gameday_captures_task():
    """
    Coordina dinámicamente las capturas de lesiones de la jornada según los kickoffs reales
    de los partidos programados para hoy.
    - Dispara inmediatamente una captura de inicio operativo de jornada ('start_of_day').
    - Por cada ventana de kickoff real del día, programa una captura prepartido ('near_kickoff')
      con apply_async(eta=...) 90 minutos antes del kickoff.
    - La captura prepartido puede usarse en predicciones realizadas después de su recepción;
      no queda excluida por un cutoff fijo anterior.
    - Guarda el as_of real de cada decisión.
    """
    import pandas as pd
    from datetime import timezone as _tz, timedelta as _dt_timedelta
    from ml.data_pipeline.nfl_source import load_nfl_upcoming_games

    now = pd.Timestamp.now(tz="UTC")
    today = now.date()

    # --- Captura de inicio operativo: se dispara inmediatamente al ejecutar este orquestador ---
    try:
        capture_nfl_injuries_task.apply_async(
            args=("start_of_day",),
            queue="ingestion",
        )
        logger.info("Captura start_of_day despachada", gameday=today.isoformat())
    except Exception as exc:
        logger.warning("No se pudo despachar captura start_of_day", error=str(exc))

    # --- Capturas prepartido coordinadas con kickoffs reales ---
    try:
        upcoming = load_nfl_upcoming_games(as_of=now)
    except Exception as exc:
        logger.warning("No se pudo cargar el calendario de próximos juegos NFL", error=str(exc))
        return {"status": "error", "detail": str(exc)}

    if upcoming.empty:
        logger.info("No hay juegos próximos para programar capturas dinámicas")
        return {"status": "no_games", "start_of_day_dispatched": True, "scheduled_pregame": 0}

    # Filtrar juegos de hoy
    todays_games = upcoming[pd.to_datetime(upcoming["game_date"]).dt.date == today]
    if todays_games.empty:
        logger.info("No hay juegos NFL hoy para capturas prepartido")
        return {"status": "no_games_today", "start_of_day_dispatched": True, "scheduled_pregame": 0}

    # Agrupar por hora de kickoff (una captura por ventana única de kickoff)
    kickoff_times = sorted(todays_games["game_start_utc"].dropna().unique())
    scheduled_pregame = []
    for ko_str in kickoff_times:
        ko_dt = pd.to_datetime(ko_str, utc=True)
        # Captura prepartido: 90 minutos antes del kickoff (cuando la NFL publica inactivos oficiales)
        pregame_capture_dt = ko_dt - _dt_timedelta(minutes=90)
        pregame_capture_py = pregame_capture_dt.to_pydatetime().replace(tzinfo=_tz.utc)

        if pregame_capture_py > now.to_pydatetime().replace(tzinfo=_tz.utc):
            # Captura futura: programar con ETA real y los juegos afectados de esta ventana
            window_games = todays_games[todays_games["game_start_utc"] == ko_str]["game_id"].dropna().astype(str).tolist()
            try:
                capture_nfl_injuries_task.apply_async(
                    args=("near_kickoff",),
                    kwargs={"affected_game_ids": window_games},
                    eta=pregame_capture_py,
                    queue="ingestion",
                )
                scheduled_pregame.append({
                    "kickoff_utc": ko_dt.isoformat(),
                    "capture_eta_utc": pregame_capture_py.isoformat(),
                    "affected_games": window_games,
                    "dispatched": True,
                })
                logger.info(
                    "Captura near_kickoff programada",
                    kickoff_utc=ko_dt.isoformat(),
                    eta_utc=pregame_capture_py.isoformat(),
                )
            except Exception as exc:
                logger.warning("No se pudo programar captura near_kickoff", kickoff=ko_str, error=str(exc))
                scheduled_pregame.append({
                    "kickoff_utc": ko_dt.isoformat(),
                    "capture_eta_utc": pregame_capture_py.isoformat(),
                    "dispatched": False,
                    "error": str(exc),
                })
        else:
            # Ventana pasada: no se programa (kickoff ya ocurrió o captura ya debería haber sido hecha)
            scheduled_pregame.append({
                "kickoff_utc": ko_dt.isoformat(),
                "capture_eta_utc": pregame_capture_py.isoformat(),
                "dispatched": False,
                "reason": "pregame_window_already_passed",
            })

    logger.info(
        "Capturas de jornada NFL coordinadas con kickoffs reales",
        gameday=today.isoformat(),
        games=len(todays_games),
        pregame_windows=len(scheduled_pregame),
    )
    return {
        "status": "success",
        "gameday": today.isoformat(),
        "games_count": len(todays_games),
        "start_of_day_dispatched": True,
        "scheduled_pregame": scheduled_pregame,
    }

