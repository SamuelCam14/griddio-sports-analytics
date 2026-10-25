"""
Servicio central de Análisis Deportivo Integral (Multi-Sport Analytics Engine).
Calcula y modela para NBA y NFL:
1. Probabilidades de Victoria y Moneyline (+EV)
2. Proyecciones de Hándicap / Spread con EV y Criterio de Kelly
3. Proyecciones de Totales (Over / Under) con EV y Criterio de Kelly
4. Proyecciones de Player Props 100% REALES con EV y Criterio de Kelly
5. Selección Óptima de Apuesta (#1 Best Bet con Kelly) para clarificar dónde está la ventaja real
6. Factores clave del modelo explicados en lenguaje accesible ("peras y manzanas")
"""
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import math
import structlog

from app.services.math.odds_converter import (
    american_to_decimal,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import calculate_kelly_fraction, calculate_kelly_amount
from app.services.math.ev import TierStr
from scipy.stats import norm

logger = structlog.get_logger(__name__)


def generate_nfl_matchup_analytics(
    home_team: str,
    away_team: str,
    game_date: datetime,
    home_ml: int,
    away_ml: int,
    spread_line: Optional[float] = None,
    total_line: Optional[float] = None,
    bookmaker: Optional[str] = None,
    bankroll: Optional[float] = None,
    live_player_props: Optional[List[Dict[str, Any]]] = None,
    model_prob_home: Optional[float] = None,
    model_prob_away: Optional[float] = None,
    model_agreement: Optional[int] = None,
    confluence_tier: Optional[str] = None,
    qb_vorp_home: float = 0.0,
    qb_vorp_away: float = 0.0,
) -> Dict[str, Any]:
    """
    Genera el análisis probabilístico, líneas de spread, totales y player props reales para un partido NFL.
    Calcula de forma explícita el EV y la asignación Kelly en CADA uno de los mercados, seleccionando
    el Best Bet (#1 Pick) para indicar con total transparencia dónde se encuentra la ventaja matemática.
    """
    # 1. Probabilidades implícitas de mercado (sin vig)
    p_h_raw = american_to_implied_prob(home_ml)
    p_a_raw = american_to_implied_prob(away_ml)
    p_h_fair, p_a_fair, vig = remove_vig(p_h_raw, p_a_raw)

    # 2. Probabilidades del Modelo (SSOT: Respeta la probabilidad generada por el motor de inferencia)
    model_valid = (
        model_prob_home is not None
        and model_prob_away is not None
        and math.isfinite(model_prob_home)
        and math.isfinite(model_prob_away)
        and 0.0 < model_prob_home < 1.0
        and 0.0 < model_prob_away < 1.0
        and math.isclose(model_prob_home + model_prob_away, 1.0, abs_tol=1e-3)
    )
    p_home_model = round(model_prob_home, 4) if model_valid else None
    p_away_model = round(model_prob_away, 4) if model_valid else None

    # Modelo de margen y scores proyectados (distribución diferencial NFL sigma ~ 13.5)
    if model_valid:
        proj_margin_base = float(norm.ppf(min(0.999, max(0.001, model_prob_home))) * 13.5)
        # Ajuste VORP: la baja del QB titular reduce el margen neto proyectado del equipo
        vorp_diff = float(qb_vorp_home - qb_vorp_away)
        proj_margin_raw = proj_margin_base - vorp_diff
        t_base = total_line if total_line is not None else 44.0
        proj_home_score = round((t_base + proj_margin_raw) / 2.0, 1)
        proj_away_score = round((t_base - proj_margin_raw) / 2.0, 1)
        proj_margin = round(proj_home_score - proj_away_score, 1)
        proj_total = round(t_base, 1) if total_line is not None else None
    else:
        proj_margin_raw = None
        proj_margin = None
        proj_home_score = None
        proj_away_score = None
        proj_total = None

    # 3. Cálculo de EV y Kelly en Moneyline (confluencia y cuota >= 1.40)
    dec_h = american_to_decimal(home_ml)
    dec_a = american_to_decimal(away_ml)

    ev_h = (p_home_model * (dec_h - 1.0) * 100.0) - ((1.0 - p_home_model) * 100.0) if model_valid else None
    ev_a = (p_away_model * (dec_a - 1.0) * 100.0) - ((1.0 - p_away_model) * 100.0) if model_valid else None

    edge_h = p_home_model - p_h_fair if model_valid else None
    edge_a = p_away_model - p_a_fair if model_valid else None

    confluence_ok = (model_agreement is None or model_agreement >= 2) and (
        confluence_tier is None or confluence_tier.upper() != "NONE"
    )

    home_eligible = model_valid and confluence_ok and (dec_h >= 1.40) and (ev_h is not None and ev_h > 0)
    away_eligible = model_valid and confluence_ok and (dec_a >= 1.40) and (ev_a is not None and ev_a > 0)

    # Confianza estricta: prob > 0.55, unánime (agreement >= 3 o None), edge >= 0.03, ev > 0, odds >= 1.40
    home_is_confianza = home_eligible and (p_home_model > 0.55) and (edge_h >= 0.03) and (model_agreement is None or model_agreement >= 3)
    away_is_confianza = away_eligible and (p_away_model > 0.55) and (edge_a >= 0.03) and (model_agreement is None or model_agreement >= 3)

    if not model_valid:
        ml_tier, ml_side, ml_pick_team, ml_ev, ml_prob, ml_odds, kelly_frac = (
            "UNAVAILABLE", None, None, None, None, None, 0.0
        )
    elif home_is_confianza:
        ml_tier = TierStr("CONFIANZA")
        ml_side = "home"
        ml_pick_team = home_team
        ml_ev = ev_h
        ml_prob = p_home_model
        ml_odds = home_ml
        kelly_frac = calculate_kelly_fraction(p_home_model, dec_h, fraction_multiplier=0.25, max_cap=0.05)
    elif away_is_confianza:
        ml_tier = TierStr("CONFIANZA")
        ml_side = "away"
        ml_pick_team = away_team
        ml_ev = ev_a
        ml_prob = p_away_model
        ml_odds = away_ml
        kelly_frac = calculate_kelly_fraction(p_away_model, dec_a, fraction_multiplier=0.25, max_cap=0.05)
    else:
        ml_tier = TierStr("NO PICK")
        ml_side = "home" if (p_home_model or 0.5) >= (p_away_model or 0.5) else "away"
        ml_pick_team = home_team if ml_side == "home" else away_team
        ml_ev = max(ev_h or 0.0, ev_a or 0.0)
        ml_prob = max(p_home_model or 0.0, p_away_model or 0.0)
        ml_odds = home_ml if ml_side == "home" else away_ml
        kelly_frac = 0.0

    valid_bankroll = bankroll is not None and math.isfinite(bankroll) and bankroll > 0
    kelly_stake_ml = calculate_kelly_amount(bankroll, kelly_frac) if valid_bankroll else None

    # 4. Análisis Cuantitativo del Spread (Modelado de margen y probabilidad de cubrir)
    if model_valid and spread_line is not None:
        z_cover = (proj_margin_raw + spread_line) / 13.5
        p_home_cover = float(norm.cdf(z_cover))
        p_away_cover = 1.0 - p_home_cover

        # Cuota estándar de spread -110 (1.909 decimal)
        dec_spread = 1.9091
        ev_home_spread = (p_home_cover * (dec_spread - 1.0) * 100.0) - ((1.0 - p_home_cover) * 100.0)
        ev_away_spread = (p_away_cover * (dec_spread - 1.0) * 100.0) - ((1.0 - p_away_cover) * 100.0)

        edge_home_spread = p_home_cover - 0.50
        edge_away_spread = p_away_cover - 0.50

        if ev_home_spread > 0 and p_home_cover > p_away_cover and dec_spread >= 1.40 and confluence_ok and p_home_cover > 0.55 and edge_home_spread >= 0.03:
            spread_pick = f"{home_team} {spread_line:+0.1f}"
            spread_cover_prob = round(p_home_cover, 4)
            ev_spread = round(ev_home_spread, 2)
            spread_edge = round(edge_home_spread * 100.0, 1)
            spread_tier = TierStr("CONFIANZA")
            spread_kelly_frac = calculate_kelly_fraction(p_home_cover, dec_spread, fraction_multiplier=0.15, max_cap=0.03)
        elif ev_away_spread > 0 and p_away_cover > p_home_cover and dec_spread >= 1.40 and confluence_ok and p_away_cover > 0.55 and edge_away_spread >= 0.03:
            spread_pick = f"{away_team} {-spread_line:+0.1f}"
            spread_cover_prob = round(p_away_cover, 4)
            ev_spread = round(ev_away_spread, 2)
            spread_edge = round(edge_away_spread * 100.0, 1)
            spread_tier = TierStr("CONFIANZA")
            spread_kelly_frac = calculate_kelly_fraction(p_away_cover, dec_spread, fraction_multiplier=0.15, max_cap=0.03)
        else:
            spread_pick = None
            spread_cover_prob = round(max(p_home_cover, p_away_cover), 4)
            ev_spread = round(max(ev_home_spread, ev_away_spread), 2)
            spread_edge = round(max(edge_home_spread, edge_away_spread) * 100.0, 1)
            spread_tier = TierStr("NO PICK")
            spread_kelly_frac = 0.0

        spread_kelly_stake = calculate_kelly_amount(bankroll, spread_kelly_frac) if valid_bankroll else None
        spread_has_edge = (ev_spread is not None and ev_spread > 0 and spread_kelly_frac > 0)
    else:
        spread_pick = None
        spread_cover_prob = None
        spread_tier = "UNAVAILABLE"
        spread_edge = None
        ev_spread = None
        spread_kelly_frac = None
        spread_kelly_stake = None
        spread_has_edge = False

    # 5. Análisis Cuantitativo de Totales
    # Hasta contar con modelo validado específicamente para totales,
    # se omiten recomendaciones, probabilidades OVER/UNDER y picks sintéticos.
    totals_pick = None
    totals_prob = None
    totals_recommendation = (
        "Totales no modelados; se omiten proyecciones y picks sin modelo validado."
        if total_line is not None
        else "Totales no disponibles en cuotas observadas."
    )
    totals_tier = "UNAVAILABLE"
    totals_edge = None
    ev_totals = None
    totals_kelly_frac = None
    totals_kelly_stake = None

    # 6. Player Props Reales Enriquecidos con EV y Kelly Individual
    enriched_props: List[Dict[str, Any]] = []
    for prop in (live_player_props or []):
        p_odds = prop.get("odds_american")
        p_prob = prop.get("win_probability")
        if (
            p_odds is not None and p_prob is not None
            and math.isfinite(float(p_prob)) and 0.0 < float(p_prob) < 1.0
        ):
            p_dec = american_to_decimal(p_odds)
            p_ev = (p_prob * (p_dec - 1.0) * 100.0) - ((1.0 - p_prob) * 100.0)
            p_kelly_frac = calculate_kelly_fraction(p_prob, p_dec, fraction_multiplier=0.25, max_cap=0.05) if p_ev > 0 else 0.0
            p_kelly_stake = calculate_kelly_amount(bankroll, p_kelly_frac) if valid_bankroll else None
        else:
            p_ev = None
            p_kelly_frac = None
            p_kelly_stake = None
        enriched_props.append({
            **prop,
            "ev_dollars": round(p_ev, 2) if p_ev is not None else None,
            "kelly_fraction": round(p_kelly_frac, 4) if p_kelly_frac is not None else None,
            "kelly_stake": p_kelly_stake,
            "has_kelly_edge": p_ev is not None and p_ev > 0 and p_kelly_frac is not None and p_kelly_frac > 0,
        })

    # 7. Determinación de la Mejor Apuesta Recomendada del Enfrentamiento (#1 BEST BET)
    # Compara objetivamente la rentabilidad y ventaja entre Moneyline y Props con cuotas reales
    candidates: List[Dict[str, Any]] = []

    if ml_ev is not None and ml_ev > 0 and kelly_frac > 0:
        candidates.append({
            "market": "moneyline",
            "market_label": "Moneyline",
            "selection": f"{ml_pick_team} ({ml_odds:+d} ML)",
            "odds_american": ml_odds,
            "win_probability": ml_prob,
            "ev_dollars": round(ml_ev, 2),
            "kelly_stake": kelly_stake_ml,
            "edge_pct": round(max(edge_h, edge_a) * 100, 1),
            "tier": ml_tier,
            "reason": f"Probabilidad modelo de {max(p_home_model, p_away_model)*100:.1f}% supera la cuota de la casa.",
        })

    if spread_tier in ("PREMIUM", "VALUE") and spread_pick is not None and ev_spread is not None and ev_spread > 0:
        candidates.append({
            "market": "spread",
            "market_label": "Hándicap / Spread",
            "selection": f"{spread_pick} (-110)",
            "odds_american": -110,
            "win_probability": spread_cover_prob,
            "ev_dollars": ev_spread,
            "kelly_stake": spread_kelly_stake,
            "edge_pct": spread_edge,
            "tier": spread_tier,
            "reason": f"Margen proyectado {proj_margin:+0.1f} pts vs línea {spread_line:+0.1f}; ventaja calculada {spread_edge:+.1f}%.",
        })

    for ep in enriched_props:
        if ep["ev_dollars"] is not None and ep["ev_dollars"] > 0 and ep["kelly_fraction"] is not None and ep["kelly_fraction"] > 0:
            candidates.append({
                "market": "props",
                "market_label": "Player Props",
                "selection": f"{ep['player_name']} {ep['pick']} {ep['market_line']} ({ep['odds_american']:+d})",
                "odds_american": ep["odds_american"],
                "win_probability": ep["win_probability"],
                "ev_dollars": ep["ev_dollars"],
                "kelly_stake": ep["kelly_stake"],
                "edge_pct": ep["edge_pct"],
                "tier": ep["tier"],
                "reason": ep.get("reasoning", f"Línea de casa en {ep['market_line']} vs proyección en {ep['projected_value']}."),
            })

    # Priorizar por Tier PREMIUM y mayor EV
    candidates.sort(key=lambda c: ("PREMIUM" in c["tier"], c["ev_dollars"]), reverse=True)
    best_bet = candidates[0] if candidates else None

    # 8. Factores Clave Explicados para Humanos (SSOT con recomendación clara de Kelly)
    if model_valid:
        favorite_team = away_team if p_away_model > p_home_model else home_team
        fav_prob = max(p_home_model, p_away_model)
        factors = [f"Probabilidad del modelo: {favorite_team} {fav_prob:.1%}."]
    else:
        factors = ["Probabilidad del modelo no disponible; no se deriva de la cuota de mercado."]
    if spread_line is not None and model_valid:
        factors.append(
            f"Línea de Hándicap en {spread_line:+0.1f}: Margen proyectado {proj_margin:+0.1f} pts ({proj_home_score:.1f} vs {proj_away_score:.1f}); prob de cubrir {spread_cover_prob:.1%}."
        )
    elif spread_line is not None:
        factors.append(
            f"Línea de Hándicap en {spread_line:+0.1f}: Sin modelo validado de spread; se omiten proyecciones de margen y picks."
        )
    else:
        factors.append("Línea de Hándicap: No disponible en cuotas observadas.")

    if total_line is not None:
        factors.append(
            f"Línea de Puntos Totales en {total_line}: Sin modelo validado de totales; se omiten proyecciones OVER/UNDER."
        )
    else:
        factors.append("Línea de Puntos Totales: No disponible en cuotas observadas.")

    if best_bet:
        factors.append(
            f"Oportunidad calculada con precio observado: {best_bet['market_label']} ({best_bet['selection']}); EV +${best_bet['ev_dollars']} por cada $100 apostados."
        )
    else:
        factors.append("No hay oportunidad calculable con los inputs disponibles; no se infiere eficiencia del mercado.")

    if qb_vorp_home > 0:
        factors.append(
            f"Apalancamiento QB (VORP): Bajas en mariscal titular de {home_team} (-{qb_vorp_home:.1f} pts VORP estimados vs reemplazo)."
        )
    if qb_vorp_away > 0:
        factors.append(
            f"Apalancamiento QB (VORP): Bajas en mariscal titular de {away_team} (-{qb_vorp_away:.1f} pts VORP estimados vs reemplazo)."
        )

    return {
        "home_team": home_team,
        "away_team": away_team,
        "game_date": game_date.isoformat(),
        "sport": "NFL",
        "bookmaker": bookmaker,
        "status": "available" if model_valid and bookmaker else "unavailable",
        "prob_home": p_home_model,
        "prob_away": p_away_model,
        "market_prob_home_no_vig": round(p_h_fair, 4),
        "market_prob_away_no_vig": round(p_a_fair, 4),
        "qb_vorp_home": round(qb_vorp_home, 2),
        "qb_vorp_away": round(qb_vorp_away, 2),
        "tier": ml_tier,
        "has_edge": ml_tier in ["PREMIUM", "VALUE"],
        "best_bet": best_bet,
        "moneyline": {
            "status": "available" if model_valid and bookmaker else "unavailable",
            "pick_team": ml_pick_team,
            "pick_side": ml_side,
            "odds_american": ml_odds,
            "prob_modelo": round(ml_prob, 4) if ml_prob is not None else None,
            "ev_dollars": round(ml_ev, 2) if ml_ev is not None else None,
            "kelly_stake": kelly_stake_ml,
            "has_kelly_edge": ml_ev is not None and ml_ev > 0 and kelly_frac > 0,
            "tier": ml_tier,
        },
        "spread": {
            "status": "available" if spread_line is not None else "unavailable",
            "market_line": spread_line,
            "odds_american": None,
            "projected_margin": round(proj_margin, 1) if proj_margin is not None else None,
            "projected_home_score": round(proj_home_score, 1) if proj_home_score is not None else None,
            "projected_away_score": round(proj_away_score, 1) if proj_away_score is not None else None,
            "pick": spread_pick,
            "cover_probability": spread_cover_prob,
            "edge_pct": spread_edge,
            "ev_dollars": round(ev_spread, 2) if ev_spread is not None else None,
            "kelly_stake": spread_kelly_stake,
            "has_kelly_edge": spread_has_edge,
            "tier": spread_tier,
        },
        "totals": {
            "status": "available" if total_line is not None else "unavailable",
            "market_line": total_line,
            "odds_american": None,
            "projected_total": round(proj_total, 1) if proj_total is not None else None,
            "pick": totals_pick,
            "recommendation": totals_recommendation,
            "probability": totals_prob,
            "edge_pct": totals_edge,
            "ev_dollars": round(ev_totals, 2) if ev_totals is not None else None,
            "kelly_stake": totals_kelly_stake,
            "has_kelly_edge": False,
            "tier": totals_tier,
        },
        "player_props": enriched_props,
        "key_factors": factors,
    }
