"""
Router: Predictions — /api/v1/predictions

Endpoints:
  GET /game/{game_id}     → Predicción para un partido específico (con EV)
  GET /today              → Todos los picks del día clasificados por tier
  GET /top                → Mejores picks EV+ disponibles
"""
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.game import Game, Prediction
from app.services.ev_engine import calculate_ev
from ml.data_pipeline.common import project_root

router = APIRouter()


def _production_model_enabled(sport: str) -> bool:
    """Require explicit per-sport approval before any stored prediction is served."""
    backend_root = Path(__file__).resolve().parents[3]
    metadata = backend_root / "ml" / "models" / sport.lower() / "model_metadata.json"
    if not metadata.is_file():
        return False
    try:
        return json.loads(metadata.read_text(encoding="utf-8")).get("production_enabled") is True
    except (OSError, json.JSONDecodeError):
        return False


@router.get("/today", summary="Todos los picks y cartelera deportiva")
async def get_today_picks(
    sport: str = Query(default="NBA", description="NBA | NFL"),
    tier: str = Query(default="all", description="all | PREMIUM | VALUE | SKIP"),
    period: str = Query(default="auto", description="auto | today | week | all"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna los partidos y predicciones organizados por tier de confianza y enriquecidos con:
    - Probabilidades de victoria Moneyline
    - Análisis de Spread / Hándicap (márgenes proyectados)
    - Totales (Over / Under de puntos)
    - Player Props (Yardas aéreas, terrestres y de pase)
    - Factores clave del análisis cuantitativo
    """
    from datetime import date, datetime, timezone, timedelta
    from sqlalchemy.orm import aliased
    from app.models.game import Team, OddsSnapshot
    from app.services.sports_analytics_service import generate_nfl_matchup_analytics

    if not _production_model_enabled(sport):
        return {
            "sport": sport.upper(), "period": period, "total_picks": 0,
            "premium": 0, "value": 0, "skip": 0, "picks": [],
            "predictions_enabled": False,
            "reason": "No hay un modelo aprobado para producción con features en paridad temporal.",
        }

    now_utc = datetime.now(timezone.utc)
    us_tz = timezone(timedelta(hours=-4))  # US Eastern Time (zona horaria oficial de partidos NFL/NBA)
    now_us = now_utc.astimezone(us_tz)
    today_us_start = datetime.combine(now_us.date(), datetime.min.time()).replace(tzinfo=us_tz)
    today_us_end   = datetime.combine(now_us.date(), datetime.max.time()).replace(tzinfo=us_tz)

    # Determinar rango temporal según período
    sport_upper = sport.upper()
    effective_period = period
    if effective_period == "auto":
        effective_period = "week" if sport_upper == "NFL" else "today"

    if effective_period == "today":
        start_date = today_us_start
        end_date   = today_us_end
    elif effective_period == "week":
        start_date = today_us_start - timedelta(days=1)
        end_date   = today_us_start + timedelta(days=7)
    else:  # all
        start_date = today_us_start - timedelta(days=2)
        end_date   = today_us_start + timedelta(days=45)

    HomeTeam = aliased(Team)
    AwayTeam = aliased(Team)

    stmt = (
        select(Prediction, Game, HomeTeam, AwayTeam)
        .join(Game, Prediction.game_id == Game.id)
        .join(HomeTeam, Game.home_team_id == HomeTeam.id)
        .join(AwayTeam, Game.away_team_id == AwayTeam.id)
        .where(Game.sport == sport_upper)
        # Solo aceptaremos artefactos entrenados y validados con partición
        # temporal walk-forward. Los registros seed/heurísticos heredados no
        # son predicciones válidas y no deben presentarse como picks.
        .where(Prediction.model_version == f"{sport_upper.lower()}_temporal_v1")
        .where(Game.game_date >= start_date)
        .where(Game.game_date <= end_date)
        .order_by(Game.game_date.asc(), Prediction.home_ev.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    picks = []
    for pred, game, home, away in rows:
        # Consultar momios de spread y totals
        odds_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.game_id == game.id)
            .order_by(OddsSnapshot.captured_at.desc())
            .limit(1)
        )
        odds_res = await db.execute(odds_stmt)
        odds_snap = odds_res.scalar_one_or_none()

        spread_line = odds_snap.home_spread if odds_snap else None
        total_line = odds_snap.total_line if odds_snap else None
        bookmaker = pred.best_bookmaker or (odds_snap.bookmaker if odds_snap else None)

        # Determinar lado con ventaja
        if pred.home_has_edge and (not pred.away_has_edge or pred.home_ev >= pred.away_ev):
            side = "home"
            ev   = pred.home_ev
            ml   = pred.best_home_ml
            prob = pred.home_win_prob
            kelly= pred.home_kelly
        elif pred.away_has_edge:
            side = "away"
            ev   = pred.away_ev
            ml   = pred.best_away_ml
            prob = pred.away_win_prob
            kelly= pred.away_kelly
        else:
            side = "home" if pred.home_win_prob >= pred.away_win_prob else "away"
            ev   = max(pred.home_ev or 0.0, pred.away_ev or 0.0)
            ml   = pred.best_home_ml if side == "home" else pred.best_away_ml
            prob = max(pred.home_win_prob, pred.away_win_prob)
            kelly= 0.0

        # Detectar si el partido es hoy según el calendario oficial del evento
        is_today = (game.game_date.astimezone(us_tz).date() == now_us.date())

        # Ingesta en tiempo real de Player Props oficiales si el partido es hoy
        real_props = []
        if is_today and game.external_id:
            try:
                from app.services.live_ingestion_service import fetch_real_player_props
                real_props = await fetch_real_player_props(game.external_id, sport=sport_upper)
            except Exception as e:
                pass

        # Generar análisis cuantitativo enriquecido con Fuente Única de Verdad (SSOT)
        analytics = generate_nfl_matchup_analytics(
            home_team=home.name,
            away_team=away.name,
            game_date=game.game_date,
            home_ml=pred.best_home_ml or -110,
            away_ml=pred.best_away_ml or -110,
            spread_line=spread_line,
            total_line=total_line,
            bookmaker=bookmaker,
            live_player_props=real_props,
            model_prob_home=pred.home_win_prob,
            model_prob_away=pred.away_win_prob,
        )

        # Evaluar la oportunidad máxima entre todos los mercados disponibles (Moneyline, Spread, Totales, Props)
        ml_is_premium = "PREMIUM" in (pred.confluence_tier or "").upper()
        ml_is_value = "VALUE" in (pred.confluence_tier or "").upper()
        props_has_premium = any("PREMIUM" in p.get("tier", "").upper() for p in real_props)
        props_has_value = any("VALUE" in p.get("tier", "").upper() for p in real_props)
        spread_is_premium = "PREMIUM" in (analytics["spread"].get("tier") or "").upper()
        spread_is_value = "VALUE" in (analytics["spread"].get("tier") or "").upper()

        has_premium = ml_is_premium or props_has_premium or spread_is_premium
        has_value = ml_is_value or props_has_value or spread_is_value

        normalized_tier = "PREMIUM" if has_premium else ("VALUE" if has_value else "SKIP")
        tier_label = normalized_tier

        if tier != "all" and normalized_tier != tier.upper():
            continue

        picks.append({
            "game_id":          game.id,
            "sport":            game.sport,
            "game_date":        game.game_date.isoformat(),
            "is_today":         is_today,
            "matchup_label":    f"{away.abbreviation} @ {home.abbreviation}",
            "home_team": {
                "id":           home.id,
                "name":         home.name,
                "abbreviation": home.abbreviation,
                "city":         home.city,
                "conference":   home.conference,
                "division":     home.division,
            },
            "away_team": {
                "id":           away.id,
                "name":         away.name,
                "abbreviation": away.abbreviation,
                "city":         away.city,
                "conference":   away.conference,
                "division":     away.division,
            },
            "tier":             normalized_tier,
            "tier_label":       tier_label,
            "tier_emoji":       tier_label,
            "model_agreement":  pred.model_agreement,
            "pick_side":        side,
            "pick_team_name":   home.name if side == "home" else away.name,
            "prob_modelo":      round(prob, 4) if prob else None,
            "momio":            ml,
            "ev_dolares":       round(ev, 2) if ev else 0.0,
            "kelly_fraccion":   round(kelly, 4) if kelly else 0.0,
            "kelly_monto":      round(1000.0 * kelly, 2) if kelly else 0.0,
            "home_win_prob":    round(pred.home_win_prob, 4),
            "away_win_prob":    round(pred.away_win_prob, 4),
            "tiene_ventaja":    pred.home_has_edge or pred.away_has_edge,
            "bookmaker":        bookmaker,
            # Análisis integral de mercados (incluye best_bet = recomendación #1 EV+Kelly)
            "best_bet":         analytics.get("best_bet"),
            "moneyline":        analytics.get("moneyline"),
            "spread":           analytics["spread"],
            "totals":           analytics["totals"],
            "player_props":     analytics["player_props"],
            "key_factors":      analytics["key_factors"],
        })

    return {
        "sport": sport_upper,
        "period": effective_period,
        "total_picks": len(picks),
        "premium": sum(1 for p in picks if p["tier"] == "PREMIUM"),
        "value":   sum(1 for p in picks if p["tier"] == "VALUE"),
        "skip":    sum(1 for p in picks if p["tier"] == "SKIP"),
        "picks":   picks,
    }


@router.get("/experimental/{sport}", summary="Probabilidades offline no aprobadas para producción")
async def get_experimental_predictions(
    sport: str,
    lookahead_days: int = Query(default=14, ge=1, le=90),
):
    """Serve only hash-verified offline probabilities; this endpoint never emits picks/EV."""
    sport_lower = sport.lower()
    if sport_lower not in {"nba", "nfl"}:
        raise HTTPException(status_code=404, detail="Deporte no soportado")
    output_root = project_root() / "data" / "predictions" / "experimental"
    manifests = sorted(output_root.glob(f"{sport_lower}_predictions_*.manifest.json"), reverse=True)
    if not manifests:
        raise HTTPException(status_code=404, detail="No existe una corrida experimental de inferencia")
    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=lookahead_days)
    for manifest_path in manifests[:1]:
        try:
            run = json.loads(manifest_path.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            run_as_of = pd.to_datetime(run["as_of"], utc=True, errors="coerce")
            schedule_retrieved = pd.to_datetime(run["schedule_retrieved_at"], utc=True, errors="coerce")
            if (pd.isna(run_as_of) or pd.isna(schedule_retrieved)
                    or run_as_of > pd.Timestamp(now) + pd.Timedelta(minutes=5)
                    or now - run_as_of.to_pydatetime() > timedelta(hours=24)
                    or now - schedule_retrieved.to_pydatetime() > timedelta(days=7)):
                raise ValueError("La corrida o el calendario están vencidos")
            expected_model = f"{sport_lower}_temporal_v1"
            if run.get("model_version") != expected_model:
                raise ValueError(f"Versión de modelo desconocida o no autorizada para {sport_lower}")
            csv_path = manifest_path.with_name(run["output_file"])
            if hashlib.sha256(csv_path.read_bytes()).hexdigest() != run.get("output_sha256"):
                continue
            frame = pd.read_csv(csv_path)
            required_cols = {"game_id", "game_date", "home_win_probability", "away_win_probability"}
            if not required_cols.issubset(frame.columns):
                raise ValueError("CSV truncado o con esquema incorrecto")
            
            frame["game_date_dt"] = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
            if frame["game_id"].isna().any() or frame["game_date_dt"].isna().any():
                raise ValueError("Filas con game_id o game_date nulos/inválidos")
            
            probs_valid = frame["home_win_probability"].between(0, 1) & frame["away_win_probability"].between(0, 1)
            probs_sum = (frame["home_win_probability"] + frame["away_win_probability"]).sub(1).abs() < 1e-5
            if not (probs_valid & probs_sum).all():
                raise ValueError("Probabilidades fuera de rango [0, 1] o suma incoherente")
            
            frame["game_date"] = frame["game_date_dt"]
            frame = frame[(frame.game_date >= today) & (frame.game_date <= end_date)]
            results = []
            for row in frame.to_dict("records"):
                results.append({
                    key: row.get(key) for key in (
                        "game_id", "game_date", "home_team_id", "away_team_id",
                        "home_team_name", "away_team_name", "home_team_abbreviation",
                        "away_team_abbreviation", "home_win_probability", "away_win_probability",
                        "feature_cutoff_exclusive", "feature_max_observed_game_date",
                        "schedule_source_ref",
                    ) if key in row
                })
            return {
                "status": "experimental_only", "sport": sport_lower.upper(),
                "predictions_enabled": False, "production_eligible": False,
                "lookahead_days": lookahead_days, "as_of": run.get("as_of"),
                "model_version": run.get("model_version"),
                "dataset_sha256": run.get("dataset_sha256"),
                "artifact_sha256": run.get("artifact_sha256"),
                "schedule_source_refs": run.get("schedule_source_refs", []),
                "game_count": len(results), "games": results,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    raise HTTPException(status_code=503, detail="No hay una corrida experimental con hash válido")


@router.get("/top", summary="Mejores picks EV+ del día")
async def get_top_picks(
    sport: str = Query(default="NBA"),
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Retorna los N mejores picks por EV, solo PREMIUM y VALUE."""
    all_picks = await get_today_picks(sport=sport, tier="all", db=db)

    top = [
        p for p in all_picks["picks"]
        if p["tier"] in ("PREMIUM", "VALUE") and p["tiene_ventaja"]
    ]
    top.sort(key=lambda x: x.get("ev_dolares") or -999, reverse=True)

    return {
        "sport": sport.upper(),
        "top_picks": top[:limit],
        "mensaje": f"Mostrando {min(limit, len(top))} de {len(top)} picks con ventaja matemática"
    }


@router.get("/game/{game_id}", summary="Predicción de un partido específico")
async def get_game_prediction(
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Predicción detallada de un partido con EV, Kelly y análisis de confluencia."""
    if not any(_production_model_enabled(sport) for sport in ("nba", "nfl")):
        raise HTTPException(status_code=503, detail="Predicciones deshabilitadas: no hay modelo aprobado para producción.")
    stmt = (
        select(Prediction)
        .where(Prediction.game_id == game_id)
        .where(Prediction.model_version.in_(["nba_temporal_v1", "nfl_temporal_v1"]))
        .order_by(Prediction.predicted_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    pred = result.scalar_one_or_none()

    if not pred:
        raise HTTPException(status_code=404, detail=f"No hay predicción para game_id={game_id}")

    return {
        "game_id":          pred.game_id,
        "model_version":    pred.model_version,
        "predicted_at":     pred.predicted_at.isoformat(),
        "probabilidades": {
            "home_win":     round(pred.home_win_prob, 4),
            "away_win":     round(pred.away_win_prob, 4),
        },
        "confluencia": {
            "tier":         pred.confluence_tier,
            "acuerdo":      f"{pred.model_agreement}/3 modelos",
        },
        "ev_analysis": {
            "bookmaker":    pred.best_bookmaker,
            "home": {
                "momio":        pred.best_home_ml,
                "ev_dolares":   round(pred.home_ev, 2) if pred.home_ev else None,
                "kelly":        round(pred.home_kelly, 4) if pred.home_kelly else None,
                "tiene_ventaja":pred.home_has_edge,
            },
            "away": {
                "momio":        pred.best_away_ml,
                "ev_dolares":   round(pred.away_ev, 2) if pred.away_ev else None,
                "kelly":        round(pred.away_kelly, 4) if pred.away_kelly else None,
                "tiene_ventaja":pred.away_has_edge,
            },
        },
        "recomendacion": (
            "PREMIUM — Apostar con criterio Kelly"    if pred.confluence_tier and "PREMIUM" in pred.confluence_tier.upper()
            else "VALUE — Apostar 50% Kelly" if pred.confluence_tier and "VALUE" in pred.confluence_tier.upper()
            else "PASAR — Sin ventaja matemática suficiente"
        ),
    }


class SimulationRequest(BaseModel):
    home_team_name: str = Field(default="Boston Celtics", description="Nombre del equipo local")
    visitor_team_name: str = Field(default="Philadelphia 76ers", description="Nombre del visitante")
    home_stats: Optional[Dict[str, Any]] = Field(default=None, description="Estadísticas avanzadas del local")
    visitor_stats: Optional[Dict[str, Any]] = Field(default=None, description="Estadísticas avanzadas del visitante")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Contexto (descanso, B2B, altitud)")
    home_odds: Optional[int] = Field(default=-130, description="Momio americano para el local")
    away_odds: Optional[int] = Field(default=110, description="Momio americano para el visitante")
    bankroll: float = Field(default=1000.0, gt=0, description="Bankroll del apostador en $")


@router.get("/model-info", summary="Estado de validación de los modelos")
async def get_model_info():
    """Expose only current temporal-training metadata, never legacy ROI claims."""
    backend_root = Path(__file__).resolve().parents[3]
    models = {}
    for sport in ("nba", "nfl"):
        model_root = backend_root / "ml" / "models" / sport
        metadata_path = model_root / "model_metadata.json"
        if not metadata_path.is_file():
            models[sport.upper()] = {"status": "unavailable", "production_enabled": False}
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            logistic = metadata.get("evaluation", {}).get("logistic", {})
            models[sport.upper()] = {
                "status": metadata.get("validation_status", "unvalidated"),
                "model_version": metadata.get("model_version"),
                "sample_count": metadata.get("sample_count"),
                "date_from": metadata.get("date_from"),
                "date_to": metadata.get("date_to"),
                "production_enabled": metadata.get("production_enabled") is True,
                "metrics": {
                    key: logistic.get(key)
                    for key in ("mean_accuracy", "mean_auc", "mean_brier", "mean_log_loss",
                                "beats_majority_brier_in_all_folds")
                },
            }
        except (OSError, json.JSONDecodeError):
            models[sport.upper()] = {"status": "metadata_error", "production_enabled": False}
    return {
        "status": "experimental",
        "artifact_loaded": False,
        "predictions_enabled": False,
        "reason": "Los modelos temporales todavía requieren revisión de frescura, calibración y persistencia de provenance.",
        "models": models,
    }


@router.post("/simulate", summary="Predicción deshabilitada hasta validar el modelo")
async def simulate_matchup(req: SimulationRequest):
    """Fail closed: custom/default team features are not observed game data."""
    raise HTTPException(
        status_code=503,
        detail="Predicción deshabilitada: faltan features observadas para este partido y validación temporal del modelo.",
    )
