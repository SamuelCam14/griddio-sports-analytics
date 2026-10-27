"""
Router: Predictions — /api/v1/predictions

Endpoints:
  GET /game/{game_id}     → Predicción para un partido específico (con EV)
  GET /today              → Todos los picks del día clasificados por tier
  GET /top                → Mejores picks EV+ disponibles
"""
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import hashlib
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db
from app.models.game import Game, Prediction, OddsSnapshot
from app.services.ev_engine import calculate_ev
from ml.data_pipeline.common import project_root

router = APIRouter()


ALLOWED_SPORTS = {"NFL", "NBA"}


def get_approved_model_version(sport: str) -> Optional[str]:
    """Retorna la versión exacta del modelo aprobada en metadata si production_enabled es True.

    Falla cerrado (retorna None) ante:
    - Deporte no permitido, vacío, no-string o intentos de alteración de ruta.
    - Archivo de metadata inexistente o con errores de lectura/codificación/parseo.
    - JSON raíz que no sea un objeto/dict.
    - Discrepancia entre el deporte solicitado y el campo 'sport' de la metadata.
    - 'production_enabled' que no sea estrictamente True.
    - 'model_version' faltante, no-string, o vacío tras strip().
    """
    if not isinstance(sport, str):
        return None
    normalized_sport = sport.strip().upper()
    if normalized_sport not in ALLOWED_SPORTS:
        return None

    try:
        backend_root = Path(__file__).resolve().parents[3]
        metadata_path = backend_root / "ml" / "models" / normalized_sport.lower() / "model_metadata.json"
        if not metadata_path.is_file():
            return None
        raw_text = metadata_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    metadata_sport = data.get("sport")
    if not isinstance(metadata_sport, str) or metadata_sport.strip().upper() != normalized_sport:
        return None

    if data.get("production_enabled") is not True:
        return None

    version = data.get("model_version")
    if not isinstance(version, str):
        return None
    clean_version = version.strip()
    if not clean_version:
        return None

    return clean_version


def _production_model_enabled(sport: str) -> bool:
    """Require explicit per-sport approval before any stored prediction is served."""
    return get_approved_model_version(sport) is not None


@router.get("/today", summary="Todos los picks y cartelera deportiva")
async def get_today_picks(
    sport: str = Query(default="NBA", description="NBA | NFL"),
    tier: str = Query(default="all", description="all | PREMIUM | VALUE | SKIP"),
    period: str = Query(default="auto", description="auto | today | week | all"),
    status: str = Query(default="all", description="all | preview | official_cohort"),
    mode: Optional[str] = Query(default=None, description="Alias for status: all | preview | official_cohort"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna los partidos y predicciones organizados por tier de confianza y enriquecidos con:
    - Probabilidades de victoria Moneyline
    - Análisis de Spread / Hándicap (márgenes proyectados)
    - Totales (Over / Under de puntos)
    - Player Props (Yardas aéreas, terrestres y de pase)
    - Factores clave del análisis cuantitativo
    - Estado de pick (preview / official_cohort) y motivo de status
    """
    from datetime import date, datetime, timezone, timedelta
    from sqlalchemy.orm import aliased
    from app.models.game import Team, OddsSnapshot
    from app.services.sports_analytics_service import generate_nfl_matchup_analytics

    sport_val = sport.default if hasattr(sport, "default") else sport
    sport_str = (sport_val if isinstance(sport_val, str) else "NBA").strip()
    tier_val = tier.default if hasattr(tier, "default") else tier
    tier_str = (tier_val if isinstance(tier_val, str) else "all").strip()
    period_val = period.default if hasattr(period, "default") else period
    period_str = (period_val if isinstance(period_val, str) else "auto").strip()
    status_val = status.default if hasattr(status, "default") else status
    mode_val = mode.default if hasattr(mode, "default") else mode
    effective_status = (mode_val or status_val or "all")
    status_str = (effective_status if isinstance(effective_status, str) else "all").strip().lower()

    approved_version = get_approved_model_version(sport_str)
    if not approved_version or not _production_model_enabled(sport_str):
        return {
            "sport": sport_str.upper(), "period": period_str, "total_picks": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "official_cohort_picks": 0, "preview_picks": 0,
            "confianza": 0, "no_pick": 0,
            "premium": 0, "value": 0, "skip": 0, "picks": [],
            "predictions_enabled": False,
            "reason": "No hay un modelo aprobado para producción con features en paridad temporal.",
        }

    if db is None:
        return {
            "sport": sport_str.upper(), "period": period_str, "total_picks": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "official_cohort_picks": 0, "preview_picks": 0,
            "confianza": 0, "no_pick": 0,
            "premium": 0, "value": 0, "skip": 0, "picks": [],
            "predictions_enabled": True,
            "reason": "Sin sesión de base de datos provista.",
        }

    now_utc = datetime.now(timezone.utc)
    us_tz = ZoneInfo("America/New_York")
    now_us = now_utc.astimezone(us_tz)
    today_us_start = datetime.combine(now_us.date(), datetime.min.time()).replace(tzinfo=us_tz)
    today_us_end   = datetime.combine(now_us.date(), datetime.max.time()).replace(tzinfo=us_tz)

    # Determinar rango temporal según período
    sport_upper = sport_str.upper()
    effective_period = period_str
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

    latest_pred_subq = (
        select(
            Prediction.game_id,
            func.max(Prediction.id).label("max_id"),
        )
        .where(Prediction.model_version == approved_version)
        .group_by(Prediction.game_id)
        .subquery()
    )

    stmt = (
        select(Prediction, Game, HomeTeam, AwayTeam)
        .join(latest_pred_subq, Prediction.id == latest_pred_subq.c.max_id)
        .join(Game, Prediction.game_id == Game.id)
        .join(HomeTeam, Game.home_team_id == HomeTeam.id)
        .join(AwayTeam, Game.away_team_id == AwayTeam.id)
        .where(Game.sport == sport_upper)
        .where(Game.status == "scheduled")
        .where(Game.game_date > now_utc)
        # Solo aceptaremos artefactos entrenados y validados con partición
        # temporal walk-forward. Los registros seed/heurísticos heredados no
        # son predicciones válidas y no deben presentarse como picks.
        .where(Prediction.model_version == approved_version)
        .where(func.coalesce(Prediction.as_of, Prediction.predicted_at) <= Game.game_date)
        .where(Game.game_date >= start_date)
        .where(Game.game_date <= end_date)
        .order_by(Game.game_date.asc(), Prediction.home_ev.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    picks = []
    excluded_missing_market_data = 0
    freshness_limit = (
        settings.FRESHNESS_HOURS_NFL_ODDS
        if sport_upper == "NFL"
        else settings.FRESHNESS_HOURS_NBA_ODDS
    )
    cohort_svc = None
    cohort_records = []
    if sport_upper == "NFL":
        from app.services.nfl_prospective_cohort_service import (
            NFLProspectiveCohortService,
            DEFAULT_VARIANT_TAG,
        )
        cohort_svc = NFLProspectiveCohortService()
        try:
            cohort_records = cohort_svc.load_all_records()
        except Exception:
            cohort_records = []

    for pred, game, home, away in rows:
        # Prepartido estricto: descartar si ya inició o no está programado
        g_kickoff = game.game_date if game.game_date.tzinfo else game.game_date.replace(tzinfo=timezone.utc)
        if g_kickoff <= now_utc or game.status != "scheduled":
            excluded_missing_market_data += 1
            continue

        # Inferencia debe ser estrictamente anterior o igual al kickoff
        p_as_of = pred.as_of or pred.predicted_at
        if p_as_of:
            p_as_of_utc = p_as_of if p_as_of.tzinfo else p_as_of.replace(tzinfo=timezone.utc)
            if p_as_of_utc > now_utc or p_as_of_utc >= g_kickoff:
                excluded_missing_market_data += 1
                continue
        elif pred.predicted_at is None:
            excluded_missing_market_data += 1
            continue

        if (
            pred.home_win_prob is None or pred.away_win_prob is None
            or not math.isfinite(pred.home_win_prob) or not math.isfinite(pred.away_win_prob)
            or not math.isclose(pred.home_win_prob + pred.away_win_prob, 1.0, abs_tol=1e-3)
        ):
            excluded_missing_market_data += 1
            continue

        # Consultar momios específicamente por mercado (independencia por mercado)
        h2h_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.game_id == game.id)
            .where(OddsSnapshot.home_ml.isnot(None), OddsSnapshot.away_ml.isnot(None))
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        )
        h2h_res = await db.execute(h2h_stmt)
        h2h_snap = h2h_res.scalar_one_or_none()

        # Validación de presencia y temporalidad física pre-kickoff
        if not h2h_snap:
            excluded_missing_market_data += 1
            continue

        h2h_obs = h2h_snap.observed_at if h2h_snap.observed_at.tzinfo else h2h_snap.observed_at.replace(tzinfo=timezone.utc)
        h2h_age = (now_utc - h2h_obs).total_seconds() / 3600.0

        if h2h_obs >= g_kickoff or h2h_age < 0:
            # Cuota capturada post-kickoff o con timestamp en el futuro: corrupta, abstención estricta
            excluded_missing_market_data += 1
            continue

        spread_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.game_id == game.id)
            .where(OddsSnapshot.home_spread.isnot(None))
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        )
        spread_res = await db.execute(spread_stmt)
        spread_snap = spread_res.scalar_one_or_none()

        total_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.game_id == game.id)
            .where(OddsSnapshot.total_line.isnot(None))
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        )
        total_res = await db.execute(total_stmt)
        total_snap = total_res.scalar_one_or_none()

        def usable_market_snapshot(snapshot, market_type: str = "any", allow_stale: bool = True):
            if snapshot is None or snapshot.observed_at is None:
                return False
            observed = (
                snapshot.observed_at
                if snapshot.observed_at.tzinfo
                else snapshot.observed_at.replace(tzinfo=timezone.utc)
            )
            age = (now_utc - observed).total_seconds() / 3600.0
            if age < 0 or observed >= g_kickoff:
                return False
            if not allow_stale and age > freshness_limit:
                return False
            if market_type == "spread":
                try:
                    h_spr = float(snapshot.home_spread) if snapshot.home_spread is not None else None
                    a_spr = float(snapshot.away_spread) if snapshot.away_spread is not None else None
                    if h_spr is None or not math.isfinite(h_spr):
                        return False
                    if a_spr is not None and (not math.isfinite(a_spr) or not math.isclose(h_spr + a_spr, 0.0, abs_tol=1e-3)):
                        return False
                    h_spr_ml = float(snapshot.home_spread_ml) if snapshot.home_spread_ml is not None else None
                    a_spr_ml = float(snapshot.away_spread_ml) if snapshot.away_spread_ml is not None else None
                    if h_spr_ml is not None and (not math.isfinite(h_spr_ml) or (-100 < h_spr_ml < 100) or h_spr_ml == 0):
                        return False
                    if a_spr_ml is not None and (not math.isfinite(a_spr_ml) or (-100 < a_spr_ml < 100) or a_spr_ml == 0):
                        return False
                except (TypeError, ValueError):
                    return False
            elif market_type == "total":
                try:
                    tot = float(snapshot.total_line) if snapshot.total_line is not None else None
                    if tot is None or not math.isfinite(tot) or tot <= 0:
                        return False
                    o_ml = float(snapshot.over_ml) if snapshot.over_ml is not None else None
                    u_ml = float(snapshot.under_ml) if snapshot.under_ml is not None else None
                    if o_ml is not None and (not math.isfinite(o_ml) or (-100 < o_ml < 100) or o_ml == 0):
                        return False
                    if u_ml is not None and (not math.isfinite(u_ml) or (-100 < u_ml < 100) or u_ml == 0):
                        return False
                except (TypeError, ValueError):
                    return False
            return True

        if not usable_market_snapshot(spread_snap, "spread", allow_stale=True):
            spread_snap = None
        if not usable_market_snapshot(total_snap, "total", allow_stale=True):
            total_snap = None

        spread_line = spread_snap.home_spread if spread_snap else None
        total_line = total_snap.total_line if total_snap else None
        # EV debe usar el mismo precio H2H fresco cuya vigencia acabamos de validar.
        h2h_home_bm = getattr(h2h_snap, "home_bookmaker", None) or h2h_snap.bookmaker
        h2h_away_bm = getattr(h2h_snap, "away_bookmaker", None) or h2h_snap.bookmaker
        h2h_bench_bm = getattr(h2h_snap, "h2h_benchmark_bookmaker", None) or getattr(h2h_snap, "benchmark_bookmaker", None) or h2h_home_bm
        bench_obs = getattr(h2h_snap, "benchmark_observed_at", None) or h2h_snap.observed_at
        spread_home_bm = getattr(spread_snap, "home_bookmaker", None) or (spread_snap.bookmaker if spread_snap else None)
        spread_away_bm = getattr(spread_snap, "away_bookmaker", None) or (spread_snap.bookmaker if spread_snap else None)
        total_over_bm = getattr(total_snap, "over_bookmaker", None) or getattr(total_snap, "home_bookmaker", None) or (total_snap.bookmaker if total_snap else None)
        total_under_bm = getattr(total_snap, "under_bookmaker", None) or getattr(total_snap, "away_bookmaker", None) or (total_snap.bookmaker if total_snap else None)

        bookmaker = h2h_snap.bookmaker
        home_ml = h2h_snap.home_ml
        away_ml = h2h_snap.away_ml

        try:
            h_ml_f = float(home_ml) if home_ml is not None else None
            a_ml_f = float(away_ml) if away_ml is not None else None
        except (TypeError, ValueError):
            excluded_missing_market_data += 1
            continue

        if (
            h_ml_f is None or a_ml_f is None or h_ml_f == 0 or a_ml_f == 0 or not bookmaker
            or not math.isfinite(h_ml_f) or not math.isfinite(a_ml_f)
            or (-100 < h_ml_f < 100) or (-100 < a_ml_f < 100)
            or pred.home_win_prob is None or pred.away_win_prob is None
        ):
            excluded_missing_market_data += 1
            continue

        home_bet = calculate_ev(
            prob_model=pred.home_win_prob,
            odds_american=home_ml,
            bankroll=0.0,
            model_agreement=pred.model_agreement,
            confluence_tier=pred.confluence_tier,
        )
        away_bet = calculate_ev(
            prob_model=pred.away_win_prob,
            odds_american=away_ml,
            bankroll=0.0,
            model_agreement=pred.model_agreement,
            confluence_tier=pred.confluence_tier,
        )

        # Regla estricta: El pick siempre corresponde al equipo proyectado a ganar por el modelo
        # (Nunca recomendar un underdog con baja probabilidad)
        side = "home" if pred.home_win_prob >= pred.away_win_prob else "away"
        fav_bet = home_bet if side == "home" else away_bet
        ml = home_ml if side == "home" else away_ml
        prob = pred.home_win_prob if side == "home" else pred.away_win_prob
        pick_bm = h2h_home_bm if side == "home" else h2h_away_bm

        # Confianza requiere estrictamente:
        # 1. Probabilidad del modelo > 55%
        # 2. Todos los modelos coinciden (model_agreement >= 3 o unánime)
        # 3. Ventaja matemática positiva (+EV y edge >= 3%)
        # 4. Momios válidos (>= 1.40)
        is_confianza = (fav_bet.tier == "CONFIANZA") and fav_bet.has_edge and (prob > 0.55)
        normalized_tier = "CONFIANZA" if is_confianza else "NO PICK"
        tier_label = normalized_tier
        ev = fav_bet.ev_dollars if is_confianza else 0.0
        kelly = fav_bet.kelly_fraction if is_confianza else 0.0

        # Detectar si el partido es hoy según el calendario oficial del evento
        is_today = (game.game_date.astimezone(us_tz).date() == now_us.date())

        # Player props en picks: GET no realiza llamadas HTTP de red
        real_props = []

        # Generar análisis cuantitativo enriquecido con Fuente Única de Verdad (SSOT)
        analytics = generate_nfl_matchup_analytics(
            home_team=home.name,
            away_team=away.name,
            game_date=game.game_date,
            home_ml=home_ml,
            away_ml=away_ml,
            spread_line=spread_line,
            total_line=total_line,
            home_spread_ml=spread_snap.home_spread_ml if spread_snap else None,
            away_spread_ml=spread_snap.away_spread_ml if spread_snap else None,
            over_ml=total_snap.over_ml if total_snap else None,
            under_ml=total_snap.under_ml if total_snap else None,
            bookmaker=pick_bm,
            home_bookmaker=h2h_home_bm,
            away_bookmaker=h2h_away_bm,
            spread_home_bookmaker=spread_home_bm,
            spread_away_bookmaker=spread_away_bm,
            total_over_bookmaker=total_over_bm,
            total_under_bookmaker=total_under_bm,
            h2h_benchmark_bookmaker=h2h_bench_bm,
            benchmark_bookmaker=h2h_bench_bm,
            benchmark_observed_at=bench_obs,
            market_prob_home_no_vig=h2h_snap.home_implied_prob,
            market_prob_away_no_vig=h2h_snap.away_implied_prob,
            live_player_props=real_props,
            model_prob_home=pred.home_win_prob,
            model_prob_away=pred.away_win_prob,
            model_agreement=pred.model_agreement,
            confluence_tier=pred.confluence_tier,
        )

        p_pred_at = pred.predicted_at if (pred.predicted_at and pred.predicted_at.tzinfo) else (pred.predicted_at.replace(tzinfo=timezone.utc) if pred.predicted_at else None)
        p_as_of_val = pred.as_of or pred.predicted_at
        p_as_of_dt = p_as_of_val if (p_as_of_val and p_as_of_val.tzinfo) else (p_as_of_val.replace(tzinfo=timezone.utc) if p_as_of_val else None)
        pred_ts = p_as_of_dt or p_pred_at
        pred_age_ko = (g_kickoff - pred_ts).total_seconds() / 3600.0 if pred_ts else 999.0

        if sport_upper == "NFL":
            if cohort_svc is None:
                from app.services.nfl_prospective_cohort_service import (
                    NFLProspectiveCohortService,
                    DEFAULT_VARIANT_TAG,
                )
                cohort_svc = NFLProspectiveCohortService()

            odds_dict = {
                "bookmaker": pick_bm or h2h_snap.bookmaker,
                "home_ml": int(h_ml_f),
                "away_ml": int(a_ml_f),
                "home_fair_prob": h2h_snap.home_implied_prob,
                "away_fair_prob": h2h_snap.away_implied_prob,
                "captured_at_utc": (
                    h2h_snap.captured_at.isoformat()
                    if getattr(h2h_snap, "captured_at", None)
                    else None
                ),
                "is_stale": getattr(h2h_snap, "is_stale", False),
            }
            attempt_ts = pred_ts or now_utc
            is_official, reason_detail = cohort_svc.check_cohort_eligibility(
                kickoff_utc=g_kickoff,
                attempt_timestamp_utc=attempt_ts,
                as_of_cutoff_utc=attempt_ts,
                market_odds=odds_dict,
                freshness_limit_hours=freshness_limit,
                decision_window_hours=2.0,
            )

            # Correlación exacta con el registro append-only de cohorte:
            reg_attempt, corr_reason = cohort_svc.find_correlated_attempt(
                game_id=game.id,
                canonical_external_id=getattr(game, "external_id", None),
                pred_ts=pred_ts,
                model_version=pred.model_version,
                variant_tag=getattr(pred, "variant_tag", None) or DEFAULT_VARIANT_TAG,
                home_win_prob=pred.home_win_prob,
                away_win_prob=pred.away_win_prob,
                market_odds=odds_dict,
                all_records=cohort_records,
            )

            if is_official and (pred_age_ko <= 2.0) and reg_attempt is not None:
                pick_status = "official_cohort"
                status_reason = "Pick oficial confirmado para cohorte prospectiva (ventana pre-kickoff ≤ 2h con cuota vigente)"
            elif is_official and (pred_age_ko <= 2.0) and reg_attempt is None:
                pick_status = "preview"
                status_reason = corr_reason
            else:
                pick_status = "preview"
                if pred_age_ko > 2.0 and h2h_age > freshness_limit:
                    status_reason = f"Early Lean: Evaluación a {round(pred_age_ko, 1)}h del kickoff con cuota en caché ({round(h2h_age, 1)}h de antigüedad)"
                elif pred_age_ko > 2.0:
                    status_reason = f"Early Lean: Predicción preliminar generada a {round(pred_age_ko, 1)}h del kickoff (confirmación oficial requiere inferencia a ≤ 2h)"
                elif h2h_age > freshness_limit:
                    status_reason = f"Early Lean: Cuota en caché observada hace {round(h2h_age, 1)}h (límite oficial {freshness_limit}h)"
                else:
                    status_reason = f"Early Lean: {reason_detail}"
        else:
            pick_status = "preview"
            status_reason = f"Early Lean ({sport_upper}): Evaluación preliminar de mercado"

        if status_str in ("preview", "early_lean") and pick_status != "preview":
            continue
        elif status_str in ("official_cohort", "cohort", "official") and pick_status != "official_cohort":
            continue

        if tier_str != "all":
            t_req = tier_str.upper()
            if t_req in ("CONFIANZA", "PREMIUM", "VALUE") and normalized_tier != "CONFIANZA":
                continue
            elif t_req in ("NO PICK", "NO_PICK", "SKIP") and normalized_tier != "NO PICK":
                continue
            elif t_req not in ("CONFIANZA", "PREMIUM", "VALUE", "NO PICK", "NO_PICK", "SKIP") and normalized_tier != t_req:
                continue

        picks.append({
            "game_id":          game.id,
            "sport":            game.sport,
            "game_date":        game.game_date.isoformat(),
            "is_today":         is_today,
            "matchup_label":    f"{away.abbreviation} @ {home.abbreviation}",
            "home_team_id":     home.id,
            "away_team_id":     away.id,
            "canonical_external_id": game.external_id,
            "model_version":    pred.model_version,
            "pick_status":      pick_status,
            "status_reason":    status_reason,
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
            "confluence_tier":  pred.confluence_tier,
            "model_agreement":  pred.model_agreement,
            "pick_side":        side,
            "pick_team_name":   home.name if side == "home" else away.name,
            "prob_modelo":      round(prob, 4) if prob else None,
            "momio":            ml,
            "ev_dolares":       round(ev, 2) if ev else 0.0,
            "kelly_fraccion":   round(kelly, 4) if kelly else 0.0,
            "kelly_monto":      None,
            "home_win_prob":    round(pred.home_win_prob, 4),
            "away_win_prob":    round(pred.away_win_prob, 4),
            "tiene_ventaja":    home_bet.has_edge or away_bet.has_edge,
            "bookmaker":        pick_bm,
            "ml_home_bookmaker": h2h_home_bm,
            "ml_away_bookmaker": h2h_away_bm,
            "h2h_benchmark_bookmaker": h2h_bench_bm,
            "benchmark_bookmaker": h2h_bench_bm,
            "benchmark_observed_at": bench_obs.isoformat() if bench_obs else None,
            "spread_home_bookmaker": spread_home_bm,
            "spread_away_bookmaker": spread_away_bm,
            "total_over_bookmaker": total_over_bm,
            "total_under_bookmaker": total_under_bm,
            "observed_at":      h2h_obs.isoformat() if h2h_obs else None,
            "predicted_at":     p_pred_at.isoformat() if p_pred_at else None,
            "as_of":            p_as_of_dt.isoformat() if p_as_of_dt else None,
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
        "as_of": now_utc.isoformat(),
        "predictions_enabled": True,
        "total_picks": len(picks),
        "excluded_missing_market_data": excluded_missing_market_data,
        "official_cohort_picks": sum(1 for p in picks if p.get("pick_status") == "official_cohort"),
        "preview_picks": sum(1 for p in picks if p.get("pick_status") == "preview"),
        "confianza": sum(1 for p in picks if p["tier"] == "CONFIANZA"),
        "no_pick":   sum(1 for p in picks if p["tier"] == "NO PICK"),
        "premium":   sum(1 for p in picks if p["tier"] == "CONFIANZA"),
        "value":     0,
        "skip":      sum(1 for p in picks if p["tier"] == "NO PICK"),
        "picks":     picks,
    }


@router.get("/experimental/{sport}", summary="Probabilidades offline no aprobadas para producción")
async def get_experimental_predictions(
    sport: str,
    lookahead_days: int = Query(default=14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Serve only hash-verified offline probabilities; this endpoint never emits picks/EV."""
    sport_lower = sport.lower()
    if sport_lower not in {"nba", "nfl"}:
        raise HTTPException(status_code=404, detail="Deporte no soportado")
    output_root = project_root() / "data" / "predictions" / "experimental"
    manifests = sorted(output_root.glob(f"{sport_lower}_predictions_*.manifest.json"), reverse=True)
    if not manifests:
        raise HTTPException(status_code=404, detail="No existe una corrida experimental de inferencia")
    now_utc = datetime.now(timezone.utc)
    us_tz = ZoneInfo("America/New_York")
    today = now_utc.astimezone(us_tz).date()
    start_date = today - timedelta(days=1)
    end_date = today + timedelta(days=lookahead_days)
    for manifest_path in manifests[:1]:
        try:
            run = json.loads(manifest_path.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            run_as_of = pd.to_datetime(run["as_of"], utc=True, errors="coerce")
            schedule_retrieved = pd.to_datetime(run["schedule_retrieved_at"], utc=True, errors="coerce")
            if (pd.isna(run_as_of) or pd.isna(schedule_retrieved)
                    or run_as_of > pd.Timestamp(now) + timedelta(minutes=5)
                    or now - run_as_of.to_pydatetime() > timedelta(days=30)
                    or now - schedule_retrieved.to_pydatetime() > timedelta(days=30)):
                raise ValueError("La corrida o el calendario están vencidos")
            allowed_models = {f"{sport_lower}_temporal_v1"}
            if sport_lower == "nfl":
                allowed_models.add("nfl_team_only_temporal_v1")
            if run.get("model_version") not in allowed_models:
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
            frame = frame[(frame.game_date >= start_date) & (frame.game_date <= end_date)]
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
            # Experimental rows use the provider's NFLverse ID, while the
            # dashboard's canonical Games can retain another external ID.
            # Link only through the persisted provider crosswalk; never infer
            # identity from a fuzzy matchup/date comparison in the UI.
            from app.models.game import ProviderGameCrosswalk, Game

            source_ids = [str(row["game_id"]) for row in results]
            canonical_by_source_id = {}
            if source_ids:
                providers = ["nflverse"] if sport_lower == "nfl" else ["sportsdataverse", "espn"]
                crosswalk_result = await db.execute(
                    select(ProviderGameCrosswalk.provider_event_id, Game.id, Game.external_id)
                    .join(Game, Game.id == ProviderGameCrosswalk.game_id)
                    .where(ProviderGameCrosswalk.provider.in_(providers))
                    .where(ProviderGameCrosswalk.provider_event_id.in_(source_ids))
                    .where(Game.sport == sport_lower.upper())
                )
                canonical_by_source_id = {
                    str(source_id): {"canonical_game_id": game_id, "canonical_external_id": external_id}
                    for source_id, game_id, external_id in crosswalk_result.all()
                }
                unmatched = [sid for sid in source_ids if sid not in canonical_by_source_id]
                if unmatched:
                    direct_res = await db.execute(
                        select(Game.external_id, Game.id)
                        .where(Game.external_id.in_(unmatched))
                        .where(Game.sport == sport_lower.upper())
                    )
                    for ext_id, g_id in direct_res.all():
                        canonical_by_source_id[str(ext_id)] = {
                            "canonical_game_id": g_id,
                            "canonical_external_id": str(ext_id),
                        }
            linked_results = [
                {**row, **canonical_by_source_id[str(row["game_id"])]}
                for row in results
                if str(row["game_id"]) in canonical_by_source_id
            ]
            return {
                "status": "experimental_only", "sport": sport_lower.upper(),
                "predictions_enabled": False, "production_eligible": False,
                "lookahead_days": lookahead_days, "as_of": run.get("as_of"),
                "model_version": run.get("model_version"),
                "dataset_sha256": run.get("dataset_sha256"),
                "artifact_sha256": run.get("artifact_sha256"),
                "schedule_source_refs": run.get("schedule_source_refs", []),
                "source_game_count": len(results), "game_count": len(linked_results),
                "unlinked_game_count": len(results) - len(linked_results),
                "games": linked_results,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    raise HTTPException(status_code=503, detail="No hay una corrida experimental con hash válido")


@router.get("/top", summary="Mejores picks EV+ del día")
async def get_top_picks(
    sport: str = Query(default="NBA"),
    limit: int = Query(default=5, ge=1, le=20),
    period: str = Query(default="all"),
    status: str = Query(default="all", description="all | preview | official_cohort"),
    mode: Optional[str] = Query(default=None, description="Alias for status: all | preview | official_cohort"),
    db: AsyncSession = Depends(get_db),
):
    """Retorna los N mejores picks por EV, solo picks con ventaja matemática demostrada."""
    sport_val = sport.default if hasattr(sport, "default") else sport
    sport_str = (sport_val if isinstance(sport_val, str) else "NBA").strip()
    limit_val = limit.default if hasattr(limit, "default") else limit
    clean_limit = int(limit_val) if isinstance(limit_val, int) or (isinstance(limit_val, str) and str(limit_val).isdigit()) else 5
    period_val = period.default if hasattr(period, "default") else period
    period_str = (period_val if isinstance(period_val, str) else "all").strip()
    status_val = status.default if hasattr(status, "default") else status
    mode_val = mode.default if hasattr(mode, "default") else mode
    effective_status = (mode_val or status_val or "all")
    status_str = (effective_status if isinstance(effective_status, str) else "all").strip().lower()

    all_picks = await get_today_picks(sport=sport_str, tier="all", period=period_str, status=status_str, db=db)

    top = [
        p for p in all_picks.get("picks", [])
        if p.get("tiene_ventaja") and p.get("tier") in ("CONFIANZA", "PREMIUM", "VALUE")
    ]
    top.sort(key=lambda x: x.get("ev_dolares") or -999, reverse=True)

    return {
        "sport": sport_str.upper(),
        "top_picks": top[:clean_limit],
        "mensaje": f"Mostrando {min(clean_limit, len(top))} de {len(top)} picks con ventaja matemática"
    }


@router.get("/game/{game_id}", summary="Predicción de un partido específico")
async def get_game_prediction(
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Predicción detallada de un partido con EV, Kelly y análisis de confluencia."""
    if db is None:
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

    pred_sport = "nfl" if pred.model_version == "nfl_temporal_v1" else "nba"
    if not _production_model_enabled(pred_sport):
        raise HTTPException(status_code=503, detail=f"Predicciones deshabilitadas para {pred_sport.upper()}: no hay modelo aprobado para producción.")

    game_res = await db.execute(select(Game).where(Game.id == game_id))
    game_obj = game_res.scalar_one_or_none()
    pick_status = "preview"
    status_reason = "Evaluación preliminar de partido"
    if game_obj:
        from app.services.nfl_prospective_cohort_service import (
            NFLProspectiveCohortService,
            DEFAULT_VARIANT_TAG,
        )
        h2h_stmt = (
            select(OddsSnapshot)
            .where(OddsSnapshot.game_id == game_id)
            .where(OddsSnapshot.home_ml.isnot(None), OddsSnapshot.away_ml.isnot(None))
            .order_by(OddsSnapshot.observed_at.desc())
            .limit(1)
        )
        h2h_res = await db.execute(h2h_stmt)
        h2h_snap = h2h_res.scalar_one_or_none()
        now_utc = datetime.now(timezone.utc)
        g_kickoff = game_obj.game_date if game_obj.game_date.tzinfo else game_obj.game_date.replace(tzinfo=timezone.utc)
        freshness_limit = settings.FRESHNESS_HOURS_NFL_ODDS if pred_sport == "nfl" else settings.FRESHNESS_HOURS_NBA_ODDS
        if pred_sport == "nfl" and h2h_snap:
            cohort_svc = NFLProspectiveCohortService()
            p_pred_at = pred.predicted_at if (pred.predicted_at and pred.predicted_at.tzinfo) else (pred.predicted_at.replace(tzinfo=timezone.utc) if pred.predicted_at else None)
            p_as_of_val = pred.as_of or pred.predicted_at
            p_as_of_dt = p_as_of_val if (p_as_of_val and p_as_of_val.tzinfo) else (p_as_of_val.replace(tzinfo=timezone.utc) if p_as_of_val else None)
            pred_ts = p_as_of_dt or p_pred_at
            pred_age_ko = (g_kickoff - pred_ts).total_seconds() / 3600.0 if pred_ts else 999.0
            h2h_obs = h2h_snap.observed_at if h2h_snap.observed_at.tzinfo else h2h_snap.observed_at.replace(tzinfo=timezone.utc)
            h2h_age = (now_utc - h2h_obs).total_seconds() / 3600.0

            try:
                h_ml_val = int(float(h2h_snap.home_ml)) if h2h_snap.home_ml is not None else None
                a_ml_val = int(float(h2h_snap.away_ml)) if h2h_snap.away_ml is not None else None
            except (TypeError, ValueError):
                h_ml_val = None
                a_ml_val = None
            odds_dict = {
                "bookmaker": h2h_snap.bookmaker,
                "home_ml": h_ml_val,
                "away_ml": a_ml_val,
                "home_fair_prob": h2h_snap.home_implied_prob,
                "away_fair_prob": h2h_snap.away_implied_prob,
                "captured_at_utc": (
                    h2h_snap.captured_at.isoformat()
                    if getattr(h2h_snap, "captured_at", None)
                    else None
                ),
                "is_stale": getattr(h2h_snap, "is_stale", False),
            }
            attempt_ts = pred_ts or now_utc
            is_official, reason_detail = cohort_svc.check_cohort_eligibility(
                kickoff_utc=g_kickoff,
                attempt_timestamp_utc=attempt_ts,
                as_of_cutoff_utc=attempt_ts,
                market_odds=odds_dict,
                freshness_limit_hours=freshness_limit,
                decision_window_hours=2.0,
            )
            reg_attempt, corr_reason = cohort_svc.find_correlated_attempt(
                game_id=str(game_id),
                canonical_external_id=getattr(game_obj, "external_id", None),
                pred_ts=pred_ts,
                model_version=pred.model_version,
                variant_tag=getattr(pred, "variant_tag", None) or DEFAULT_VARIANT_TAG,
                home_win_prob=pred.home_win_prob,
                away_win_prob=pred.away_win_prob,
                market_odds=odds_dict,
            )
            if is_official and (pred_age_ko <= 2.0) and reg_attempt is not None:
                pick_status = "official_cohort"
                status_reason = "Pick oficial confirmado para cohorte prospectiva (ventana pre-kickoff ≤ 2h con cuota vigente)"
            elif is_official and (pred_age_ko <= 2.0) and reg_attempt is None:
                pick_status = "preview"
                status_reason = corr_reason
            else:
                pick_status = "preview"
                if pred_age_ko > 2.0 and h2h_age > freshness_limit:
                    status_reason = f"Early Lean: Evaluación a {round(pred_age_ko, 1)}h del kickoff con cuota en caché ({round(h2h_age, 1)}h de antigüedad)"
                elif pred_age_ko > 2.0:
                    status_reason = f"Early Lean: Predicción preliminar generada a {round(pred_age_ko, 1)}h del kickoff (confirmación oficial requiere inferencia a ≤ 2h)"
                elif h2h_age > freshness_limit:
                    status_reason = f"Early Lean: Cuota en caché observada hace {round(h2h_age, 1)}h (límite oficial {freshness_limit}h)"
                else:
                    status_reason = f"Early Lean: {reason_detail}"

    return {
        "game_id":          pred.game_id,
        "model_version":    pred.model_version,
        "pick_status":      pick_status,
        "status_reason":    status_reason,
        "predicted_at":     pred.predicted_at.isoformat() if pred.predicted_at else None,
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
    home_team_name: Optional[str] = Field(default=None, description="Nombre del equipo local")
    visitor_team_name: Optional[str] = Field(default=None, description="Nombre del visitante")
    home_stats: Optional[Dict[str, Any]] = Field(default=None, description="Estadísticas avanzadas del local")
    visitor_stats: Optional[Dict[str, Any]] = Field(default=None, description="Estadísticas avanzadas del visitante")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Contexto (descanso, B2B, altitud)")
    home_odds: Optional[int] = Field(default=None, description="Momio americano para el local")
    away_odds: Optional[int] = Field(default=None, description="Momio americano para el visitante")
    bankroll: float = Field(default=1000.0, gt=0, description="Bankroll del apostador en $")


@router.get("/model-info", summary="Estado de validación de los modelos")
async def get_model_info():
    """Expose only current temporal-training metadata, never legacy ROI claims."""
    backend_root = Path(__file__).resolve().parents[3]
    models = {}
    for sport in ("nba", "nfl"):
        model_root = backend_root / "ml" / "models" / sport
        metadata_path = model_root / "model_metadata.json"
        if not metadata_path.is_file() and sport == "nfl":
            team_only_path = model_root / "team_only" / "model_metadata.json"
            if team_only_path.is_file():
                metadata_path = team_only_path
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


@router.get("/prospective-cohort/nfl", summary="Informe y métricas de la cohorte prospectiva NFL")
async def get_nfl_prospective_cohort(
    variant_tag: str = Query("baseline_logistic_c672f6a3", description="Tag de variante del modelo")
) -> Dict[str, Any]:
    """Retorna la evaluación auditada de la cohorte prospectiva NFL segregada en 4 dimensiones."""
    from app.services.nfl_prospective_cohort_service import NFLProspectiveCohortService
    svc = NFLProspectiveCohortService()
    return svc.evaluate_cohort(variant_tag=variant_tag)
