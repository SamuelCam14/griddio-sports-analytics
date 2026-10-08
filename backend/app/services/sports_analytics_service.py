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
from scipy.stats import norm

from app.services.math.odds_converter import (
    american_to_decimal,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import calculate_kelly_fraction, calculate_kelly_amount

logger = structlog.get_logger(__name__)


def generate_nfl_matchup_analytics(
    home_team: str,
    away_team: str,
    game_date: datetime,
    home_ml: int,
    away_ml: int,
    spread_line: Optional[float] = -3.0,
    total_line: Optional[float] = 38.0,
    bookmaker: str = "DraftKings",
    bankroll: float = 1000.0,
    live_player_props: Optional[List[Dict[str, Any]]] = None,
    model_prob_home: Optional[float] = None,
    model_prob_away: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Genera el análisis probabilístico, líneas de spread, totales y player props reales para un partido NFL.
    Calcula de forma explícita el EV y la asignación Kelly en CADA uno de los mercados, seleccionando
    el Best Bet (#1 Pick) para indicar con total transparencia dónde se encuentra la ventaja matemática.
    """
    spread_line = spread_line if spread_line is not None else -3.0
    total_line = total_line if total_line is not None else 38.0
    sigma_nfl = 13.5  # Desviación estándar histórica de márgenes en la NFL

    # 1. Probabilidades implícitas de mercado (sin vig)
    p_h_raw = american_to_implied_prob(home_ml)
    p_a_raw = american_to_implied_prob(away_ml)
    p_h_fair, p_a_fair, vig = remove_vig(p_h_raw, p_a_raw)

    # 2. Probabilidades del Modelo (SSOT: Respeta la probabilidad generada por el motor de inferencia)
    if model_prob_home is not None:
        p_home_model = round(model_prob_home, 4)
        p_away_model = round(model_prob_away if model_prob_away is not None else (1.0 - p_home_model), 4)
        proj_margin = round(float(norm.ppf(max(0.01, min(0.99, p_home_model)))) * sigma_nfl, 1)
    else:
        market_implied_margin = -float(spread_line)
        proj_margin = round(market_implied_margin + (0.5 if p_h_fair > 0.5 else -0.5), 1)
        p_home_model = round(float(norm.cdf(proj_margin / sigma_nfl)), 4)
        p_home_model = max(0.15, min(0.85, p_home_model))
        p_away_model = round(1.0 - p_home_model, 4)

    # Puntuaciones proyectadas basadas en el total y margen del modelo
    proj_total_base = float(total_line)
    proj_home_score = round(max(10.0, (proj_total_base + proj_margin) / 2.0), 1)
    proj_away_score = round(max(10.0, (proj_total_base - proj_margin) / 2.0), 1)
    proj_total = round(proj_home_score + proj_away_score, 1)

    # 3. Cálculo de EV y Kelly en Moneyline
    dec_h = american_to_decimal(home_ml)
    dec_a = american_to_decimal(away_ml)

    ev_h = (p_home_model * (dec_h - 1.0) * 100.0) - ((1.0 - p_home_model) * 100.0)
    ev_a = (p_away_model * (dec_a - 1.0) * 100.0) - ((1.0 - p_away_model) * 100.0)

    edge_h = p_home_model - p_h_fair
    edge_a = p_away_model - p_a_fair

    if edge_h >= 0.045 and ev_h >= 3.5:
        ml_tier = "PREMIUM"
        ml_side = "home"
        ml_pick_team = home_team
        ml_ev = ev_h
        ml_prob = p_home_model
        ml_odds = home_ml
        kelly_frac = calculate_kelly_fraction(p_home_model, dec_h, fraction_multiplier=0.25, max_cap=0.05)
    elif edge_a >= 0.045 and ev_a >= 3.5:
        ml_tier = "PREMIUM"
        ml_side = "away"
        ml_pick_team = away_team
        ml_ev = ev_a
        ml_prob = p_away_model
        ml_odds = away_ml
        kelly_frac = calculate_kelly_fraction(p_away_model, dec_a, fraction_multiplier=0.25, max_cap=0.05)
    elif edge_h >= 0.02 and ev_h > 0:
        ml_tier = "VALUE"
        ml_side = "home"
        ml_pick_team = home_team
        ml_ev = ev_h
        ml_prob = p_home_model
        ml_odds = home_ml
        kelly_frac = calculate_kelly_fraction(p_home_model, dec_h, fraction_multiplier=0.15, max_cap=0.03)
    elif edge_a >= 0.02 and ev_a > 0:
        ml_tier = "VALUE"
        ml_side = "away"
        ml_pick_team = away_team
        ml_ev = ev_a
        ml_prob = p_away_model
        ml_odds = away_ml
        kelly_frac = calculate_kelly_fraction(p_away_model, dec_a, fraction_multiplier=0.15, max_cap=0.03)
    else:
        ml_tier = "SKIP"
        ml_side = "home" if p_home_model >= p_away_model else "away"
        ml_pick_team = home_team if p_home_model >= p_away_model else away_team
        ml_ev = max(ev_h, ev_a)
        ml_prob = max(p_home_model, p_away_model)
        ml_odds = home_ml if p_home_model >= p_away_model else away_ml
        kelly_frac = 0.0

    kelly_stake_ml = calculate_kelly_amount(bankroll, kelly_frac)

    # 4. Análisis Cuantitativo del Spread con EV y Kelly
    cover_prob_home = round(float(norm.cdf((proj_margin - (-spread_line)) / sigma_nfl)), 3)
    cover_prob_away = round(1.0 - cover_prob_home, 3)

    dec_spread = american_to_decimal(-110)
    if cover_prob_home >= cover_prob_away:
        spread_pick = f"{home_team} {spread_line:+0.1f}"
        spread_cover_prob = max(0.505, cover_prob_home)
    else:
        spread_pick = f"{away_team} {-spread_line:+0.1f}"
        spread_cover_prob = max(0.505, cover_prob_away)

    spread_edge = round(max(0.0, (spread_cover_prob - 0.524) * 100), 1)
    ev_spread = (spread_cover_prob * (dec_spread - 1.0) * 100.0) - ((1.0 - spread_cover_prob) * 100.0)
    spread_kelly_frac = calculate_kelly_fraction(spread_cover_prob, dec_spread, fraction_multiplier=0.25, max_cap=0.05) if ev_spread > 0 else 0.0
    spread_kelly_stake = calculate_kelly_amount(bankroll, spread_kelly_frac)
    spread_tier = "PREMIUM" if spread_edge >= 4.0 and ev_spread > 3.0 else ("VALUE" if spread_edge >= 1.5 and ev_spread > 0 else "NEUTRAL")

    # 5. Análisis Cuantitativo de Totales con EV y Kelly
    sigma_totals = 13.8
    prob_over = round(1.0 - float(norm.cdf(total_line, loc=proj_total, scale=sigma_totals)), 3)
    prob_under = round(1.0 - prob_over, 3)
    dec_totals = american_to_decimal(-110)

    if prob_over >= prob_under:
        totals_pick = "OVER"
        totals_prob = prob_over
        totals_recommendation = f"OVER {total_line} pts (Proyección: {proj_total:.1f} pts)"
    else:
        totals_pick = "UNDER"
        totals_prob = prob_under
        totals_recommendation = f"UNDER {total_line} pts (Proyección: {proj_total:.1f} pts)"

    totals_edge = round(max(0.0, (totals_prob - 0.524) * 100), 1)
    ev_totals = (totals_prob * (dec_totals - 1.0) * 100.0) - ((1.0 - totals_prob) * 100.0)
    totals_kelly_frac = calculate_kelly_fraction(totals_prob, dec_totals, fraction_multiplier=0.25, max_cap=0.05) if ev_totals > 0 else 0.0
    totals_kelly_stake = calculate_kelly_amount(bankroll, totals_kelly_frac)
    totals_tier = "PREMIUM" if totals_edge >= 4.0 and ev_totals > 3.0 else ("VALUE" if totals_edge >= 1.5 and ev_totals > 0 else "NEUTRAL")

    # 6. Player Props Reales Enriquecidos con EV y Kelly Individual
    enriched_props: List[Dict[str, Any]] = []
    for prop in (live_player_props or []):
        p_odds = prop.get("odds_american", -110)
        p_prob = prop.get("win_probability", 0.50)
        p_dec = american_to_decimal(p_odds)
        p_ev = (p_prob * (p_dec - 1.0) * 100.0) - ((1.0 - p_prob) * 100.0)
        p_kelly_frac = calculate_kelly_fraction(p_prob, p_dec, fraction_multiplier=0.25, max_cap=0.05) if p_ev > 0 else 0.0
        p_kelly_stake = calculate_kelly_amount(bankroll, p_kelly_frac)
        enriched_props.append({
            **prop,
            "ev_dollars": round(p_ev, 2),
            "kelly_fraction": round(p_kelly_frac, 4),
            "kelly_stake": p_kelly_stake,
            "has_kelly_edge": p_ev > 0 and p_kelly_stake > 0,
        })

    # 7. Determinación de la Mejor Apuesta Recomendada del Enfrentamiento (#1 BEST BET)
    # Compara objetivamente la rentabilidad y ventaja entre Moneyline, Spread, Totales y Props
    candidates: List[Dict[str, Any]] = []

    if ml_ev > 0 and kelly_stake_ml > 0:
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

    if ev_spread > 0 and spread_kelly_stake > 0:
        candidates.append({
            "market": "spread",
            "market_label": "Spread / Handicap",
            "selection": f"{spread_pick} (-110)",
            "odds_american": -110,
            "win_probability": spread_cover_prob,
            "ev_dollars": round(ev_spread, 2),
            "kelly_stake": spread_kelly_stake,
            "edge_pct": spread_edge,
            "tier": spread_tier,
            "reason": f"Marcador proyectado ({proj_away_score}-{proj_home_score}) cubre la línea de {spread_line:+0.1f}.",
        })

    if ev_totals > 0 and totals_kelly_stake > 0:
        candidates.append({
            "market": "totals",
            "market_label": "Totales (Over/Under)",
            "selection": f"{totals_recommendation} (-110)",
            "odds_american": -110,
            "win_probability": totals_prob,
            "ev_dollars": round(ev_totals, 2),
            "kelly_stake": totals_kelly_stake,
            "edge_pct": totals_edge,
            "tier": totals_tier,
            "reason": f"Puntos proyectados ({proj_total:.1f}) ofrecen ventaja en el {totals_pick}.",
        })

    for ep in enriched_props:
        if ep["ev_dollars"] > 0 and ep["kelly_stake"] > 0:
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
    favorite_team = away_team if p_away_model > p_home_model else home_team
    fav_prob = max(p_home_model, p_away_model)

    factors = [
        f"Ventaja de victoria: El modelo sitúa a {favorite_team} como favorito con un {(fav_prob * 100):.1f}% de probabilidad.",
        f"Línea de Hándicap en {spread_line:+0.1f}: Marcador esperado de {away_team} {proj_away_score} vs {home_team} {proj_home_score} (Diferencial proyectado: {abs(proj_margin):.1f} pts).",
        f"Línea de Puntos Totales: Proyección combinada de {proj_total:.1f} puntos frente a la línea de casa de {total_line} puntos ({totals_pick}).",
    ]

    if best_bet:
        factors.append(
            f"Recomendación Kelly: La mayor ventaja matemática está en {best_bet['market_label']} ({best_bet['selection']}) con EV +${best_bet['ev_dollars']} y asignación Kelly de ${best_bet['kelly_stake']}."
        )
    else:
        factors.append("Criterio de Kelly: El mercado está altamente eficiente en todos los renglones; se sugiere no forzar apuestas.")

    return {
        "home_team": home_team,
        "away_team": away_team,
        "game_date": game_date.isoformat(),
        "sport": "NFL",
        "bookmaker": bookmaker,
        "prob_home": round(p_home_model, 4),
        "prob_away": round(p_away_model, 4),
        "tier": ml_tier,
        "has_edge": ml_tier in ["PREMIUM", "VALUE"],
        "best_bet": best_bet,
        "moneyline": {
            "pick_team": ml_pick_team,
            "pick_side": ml_side,
            "odds_american": ml_odds,
            "prob_modelo": round(ml_prob, 4),
            "ev_dollars": round(ml_ev, 2),
            "kelly_stake": kelly_stake_ml,
            "has_kelly_edge": ml_ev > 0 and kelly_stake_ml > 0,
            "tier": ml_tier,
        },
        "spread": {
            "market_line": spread_line,
            "odds_american": -110,
            "projected_margin": round(proj_margin, 1),
            "projected_home_score": round(proj_home_score, 1),
            "projected_away_score": round(proj_away_score, 1),
            "pick": spread_pick,
            "cover_probability": spread_cover_prob,
            "edge_pct": spread_edge,
            "ev_dollars": round(ev_spread, 2),
            "kelly_stake": spread_kelly_stake,
            "has_kelly_edge": ev_spread > 0 and spread_kelly_stake > 0,
            "tier": spread_tier,
        },
        "totals": {
            "market_line": total_line,
            "odds_american": -110,
            "projected_total": round(proj_total, 1),
            "pick": totals_pick,
            "recommendation": totals_recommendation,
            "probability": totals_prob,
            "edge_pct": totals_edge,
            "ev_dollars": round(ev_totals, 2),
            "kelly_stake": totals_kelly_stake,
            "has_kelly_edge": ev_totals > 0 and totals_kelly_stake > 0,
            "tier": totals_tier,
        },
        "player_props": enriched_props,
        "key_factors": factors,
    }
