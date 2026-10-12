"""
Servicio del Parlay Builder.
Escanea los partidos programados, filtra eventos con alta probabilidad (>65% o confluencia PREMIUM/VALUE)
y construye combinaciones óptimas de 2 y 3 legs con ventaja matemática (EV+) y momio objetivo ~2.0 (+100).
"""
import itertools
import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.game import Game, Prediction, OddsSnapshot, Team
from app.services.ev_engine import (
    BetEvent,
    american_to_decimal,
    decimal_to_american,
    calculate_ev,
)
from app.api.v1.predictions import _production_model_enabled, get_approved_model_version


def evaluate_custom_parlay(
    legs: List[Dict[str, Any]],
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
) -> Dict[str, Any]:
    """
    Evalúa matemáticamente un parlay personalizado de N patas.
    Cada leg debe tener: { "team_name": str, "prob_model": float, "odds_american": int }
    """
    if len(legs) < 2:
        return {
            "viable": False,
            "razon": "Un parlay requiere al menos 2 eventos independientes.",
            "n_legs": len(legs),
            "prob_combinada": 0.0,
            "momio_decimal": 0.0,
            "momio_americano": 0,
            "ev_dolares": 0.0,
            "kelly_monto": 0.0,
            "legs_evaluadas": [],
        }

    evaluated_legs = []
    prob_combined = 1.0
    odds_decimal = 1.0

    for leg in legs:
        p = float(leg["prob_model"])
        odds_am = int(leg["odds_american"])
        p_dec = american_to_decimal(odds_am)

        leg_ev = calculate_ev(p, odds_am, bankroll=bankroll)
        evaluated_legs.append({
            "team_name": leg.get("team_name", "Desconocido"),
            "prob_model": round(p, 4),
            "odds_american": odds_am,
            "odds_decimal": round(p_dec, 3),
            "edge_pct": round(leg_ev.edge_pct, 4),
            "has_individual_edge": leg_ev.has_edge,
            "tier": leg_ev.tier,
        })

        prob_combined *= p
        odds_decimal *= p_dec

    payout_net = odds_decimal - 1.0
    odds_american_comb = decimal_to_american(odds_decimal)

    # EV por cada $100 apostados
    q = 1.0 - prob_combined
    ev_dollars = (prob_combined * payout_net * 100.0) - (q * 100.0)

    # Kelly Criterion fraccional para el parlay
    if payout_net > 0:
        kelly_full = (payout_net * prob_combined - q) / payout_net
        kelly_applied = max(0.0, min(kelly_full * kelly_fraction, 0.05))
    else:
        kelly_applied = 0.0

    kelly_amount = round(bankroll * kelly_applied, 2)

    # Criterios de viabilidad:
    # 1. EV combinado positivo (> $2 por cada $100)
    # 2. Probabilidad acumulada >= 42% (para micro-parlays con ventaja real)
    # 3. Ningún leg debe tener EV fuertemente negativo (edge < -0.05)
    legs_have_heavy_negative = any(l["edge_pct"] < -0.05 for l in evaluated_legs)
    is_viable = (ev_dollars > 2.0) and (prob_combined >= 0.42) and not legs_have_heavy_negative

    if is_viable:
        razon = f"Parlay viable de {len(legs)} patas: Probabilidad combinada {prob_combined:.1%}, EV +${ev_dollars:.2f} con cuota {odds_american_comb:+d}."
    elif ev_dollars <= 0:
        razon = f"EV negativo (-${abs(ev_dollars):.2f}). La casa de apuestas mantiene ventaja matemática sobre esta combinación."
    elif prob_combined < 0.42:
        razon = f"Probabilidad combinada muy baja ({prob_combined:.1%}). Alto riesgo de quiebra a largo plazo."
    else:
        razon = "Alguna pata seleccionada presenta expectativa fuertemente desfavorable."

    return {
        "viable": is_viable,
        "razon": razon,
        "n_legs": len(legs),
        "prob_combinada": round(prob_combined, 4),
        "momio_decimal": round(odds_decimal, 3),
        "momio_americano": odds_american_comb,
        "ev_dolares": round(ev_dollars, 2),
        "kelly_fraccion": round(kelly_applied, 4),
        "kelly_monto": kelly_amount,
        "legs_evaluadas": evaluated_legs,
    }


async def generate_recommended_parlays(
    db: AsyncSession,
    bankroll: float = 1000.0,
    max_combos: int = 5,
    sport: str = "NFL",
) -> List[Dict[str, Any]]:
    """
    Escanea la base de datos buscando partidos programados con predicciones y momios,
    identifica los candidatos con prob > 60% y genera combinaciones óptimas de 2 y 3 legs.
    Opera exclusivamente sobre el deporte especificado (NFL por defecto en esta fase).
    Solo considera predicciones cuya versión coincida exactamente con la aprobada en model_metadata.json.
    """
    target_sport = (sport or "NFL").upper()
    if target_sport != "NFL":
        return []

    approved_version = get_approved_model_version(target_sport)
    if not approved_version:
        return []

    # Consultar partidos con predicciones y momios estrictamente para NFL y versión aprobada
    stmt = (
        select(Prediction, Game, Team)
        .join(Game, Prediction.game_id == Game.id)
        .join(Team, Game.home_team_id == Team.id)
        .where(Game.sport == target_sport)
        .where(Game.status == "scheduled")
        .where(Prediction.model_version == approved_version)
        .order_by(Game.game_date)
    )
    res = await db.execute(stmt)
    rows = res.all()

    candidates: List[Dict[str, Any]] = []

    for pred, game, home_team in rows:
        # Validación de seguridad: rechazar registros que no coincidan con el deporte o versión aprobada
        if getattr(game, "sport", None) != target_sport or pred.model_version != approved_version:
            continue

        # Determinar si el favorito tiene ventaja
        h_prob = pred.home_win_prob
        a_prob = pred.away_win_prob
        h_ml = pred.best_home_ml
        a_ml = pred.best_away_ml

        if not h_ml or not a_ml:
            continue

        # Si el local tiene probabilidad atractiva (> 62%)
        if h_prob >= 0.62:
            ev = calculate_ev(h_prob, h_ml, bankroll=bankroll)
            if ev.edge_pct >= 0.01:  # Al menos leve ventaja o paridad
                candidates.append({
                    "game_id": game.id,
                    "team_name": home_team.name,
                    "opponent": "Visitante",
                    "side": "home",
                    "prob_model": h_prob,
                    "odds_american": h_ml,
                    "edge_pct": ev.edge_pct,
                    "tier": pred.confluence_tier,
                })

        # Si el visitante tiene probabilidad atractiva (> 62%)
        if a_prob >= 0.62:
            ev = calculate_ev(a_prob, a_ml, bankroll=bankroll)
            if ev.edge_pct >= 0.01:
                candidates.append({
                    "game_id": game.id,
                    "team_name": f"Visita vs {home_team.abbreviation}",
                    "opponent": home_team.name,
                    "side": "away",
                    "prob_model": a_prob,
                    "odds_american": a_ml,
                    "edge_pct": ev.edge_pct,
                    "tier": pred.confluence_tier,
                })

    # Si hay menos de 2 candidatos reales con gate aprobado en DB, no hay parlay viable
    if len(candidates) < 2:
        return []

    # Ordenar candidatos por ventaja descendente
    candidates.sort(key=lambda x: x.get("edge_pct", x["prob_model"]), reverse=True)

    generated_parlays = []

    # Probar combinaciones de 2 legs
    for combo in itertools.combinations(candidates[:8], 2):
        # Evitar patas del mismo partido
        if combo[0].get("game_id") and combo[0].get("game_id") == combo[1].get("game_id"):
            continue

        result = evaluate_custom_parlay(list(combo), bankroll=bankroll)
        if result["viable"]:
            generated_parlays.append({
                "type": "2-Leg Micro Parlay",
                **result,
            })

    # Probar combinaciones de 3 legs
    for combo in itertools.combinations(candidates[:6], 3):
        game_ids = [c.get("game_id") for c in combo if c.get("game_id")]
        if len(game_ids) != len(set(game_ids)):
            continue

        result = evaluate_custom_parlay(list(combo), bankroll=bankroll)
        if result["viable"]:
            generated_parlays.append({
                "type": "3-Leg Sweet Spot Parlay",
                **result,
            })

    # Ordenar por EV en dólares descendente
    generated_parlays.sort(key=lambda x: x["ev_dolares"], reverse=True)
    return generated_parlays[:max_combos]
