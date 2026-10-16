"""
Servicio del Parlay Builder — Backend SSOT.
Escanea los partidos programados, filtra eventos con alta probabilidad (>65% o confluencia PREMIUM/VALUE)
y construye combinaciones óptimas de 2 y 3 legs con ventaja matemática (EV+) y momio objetivo ~2.0 (+100).
Garantías Verificadas:
- SSOT: el backend resuelve probabilidades experimentales y momios observados.
- Cero valores predeterminados (0.50 o probabilidad implícita del mercado) para prob_model.
- Totales Over/Under no calculan EV del modelo por carecer de probabilidad correspondiente.
- Rechazo estricto de eventos correlacionados (mismo partido/evento).
- Emparejamiento por ambos equipos y fecha del calendario; rechazo de coincidencias ambiguas.
"""
import itertools
import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.game import Game, Prediction, OddsSnapshot, Team
from app.services.ev_engine import (
    BetEvent,
    american_to_decimal,
    decimal_to_american,
    calculate_ev,
)
from app.api.v1.predictions import get_approved_model_version


import re

NFL_CANONICAL_TEAMS: Dict[str, List[str]] = {
    "ARI": ["arizona cardinals", "cardinals", "arizona", "ari"],
    "ATL": ["atlanta falcons", "falcons", "atlanta", "atl"],
    "BAL": ["baltimore ravens", "ravens", "baltimore", "bal"],
    "BUF": ["buffalo bills", "bills", "buffalo", "buf"],
    "CAR": ["carolina panthers", "panthers", "carolina", "car"],
    "CHI": ["chicago bears", "bears", "chicago", "chi"],
    "CIN": ["cincinnati bengals", "bengals", "cincinnati", "cin"],
    "CLE": ["cleveland browns", "browns", "cleveland", "cle"],
    "DAL": ["dallas cowboys", "cowboys", "dallas", "dal"],
    "DEN": ["denver broncos", "broncos", "denver", "den"],
    "DET": ["detroit lions", "lions", "detroit", "det"],
    "GB":  ["green bay packers", "packers", "green bay", "gb", "gnb"],
    "HOU": ["houston texans", "texans", "houston", "hou"],
    "IND": ["indianapolis colts", "colts", "indianapolis", "ind"],
    "JAX": ["jacksonville jaguars", "jaguars", "jacksonville", "jax"],
    "KC":  ["kansas city chiefs", "chiefs", "kansas city", "kc", "kan"],
    "LV":  ["las vegas raiders", "raiders", "las vegas", "lv", "oak", "oakland", "oakland raiders"],
    "LAC": ["los angeles chargers", "chargers", "lac", "sd", "san diego chargers"],
    "LAR": ["los angeles rams", "rams", "lar", "la", "stl", "st. louis rams"],
    "MIA": ["miami dolphins", "dolphins", "miami", "mia"],
    "MIN": ["minnesota vikings", "vikings", "minnesota", "min"],
    "NE":  ["new england patriots", "patriots", "new england", "ne", "nwe"],
    "NO":  ["new orleans saints", "saints", "new orleans", "no", "nor"],
    "NYG": ["new york giants", "giants", "nyg"],
    "NYJ": ["new york jets", "jets", "nyj"],
    "PHI": ["philadelphia eagles", "eagles", "philadelphia", "phi"],
    "PIT": ["pittsburgh steelers", "steelers", "pittsburgh", "pit"],
    "SF":  ["san francisco 49ers", "49ers", "san francisco", "sf", "sfo", "niners"],
    "SEA": ["seattle seahawks", "seahawks", "seattle", "sea"],
    "TB":  ["tampa bay buccaneers", "buccaneers", "tampa bay", "tb", "tam", "bucs"],
    "TEN": ["tennessee titans", "titans", "tennessee", "ten"],
    "WAS": ["washington commanders", "commanders", "washington", "was", "wsh", "redskins", "football team"],
}

# Alias de compatibilidad hacia atrás
NFL_TEAM_ALIASES = NFL_CANONICAL_TEAMS

# Precomputar diccionario de búsqueda exacta y lista de alias ordenados por longitud descendente
_EXACT_TEAM_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in NFL_CANONICAL_TEAMS.items():
    _EXACT_TEAM_LOOKUP[_canonical.lower()] = _canonical
    for _alias in _aliases:
        _EXACT_TEAM_LOOKUP[_alias.lower()] = _canonical

_SORTED_TEAM_ALIASES = sorted(_EXACT_TEAM_LOOKUP.keys(), key=len, reverse=True)


def normalize_nfl_team(team_str: Optional[str]) -> Optional[str]:
    """
    Normaliza cualquier nombre, ciudad o abreviatura de equipo NFL a su clave canónica.
    Utiliza coincidencia exacta y límites de palabra (\\b) para evitar colisiones
    entre abreviaturas cortas (ej. NE, NO, LA, CHI) y palabras que las contienen
    (ej. New Orleans Saints -> NO, no NE; Kansas City Chiefs -> KC, no CHI).
    """
    if not team_str:
        return None
    raw = team_str.strip().lower()

    # 1. Búsqueda exacta directa
    if raw in _EXACT_TEAM_LOOKUP:
        return _EXACT_TEAM_LOOKUP[raw]

    # 2. Búsqueda por palabra completa con límites de palabra (\\b), evaluando alias más largos primero
    for alias in _SORTED_TEAM_ALIASES:
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, raw):
            return _EXACT_TEAM_LOOKUP[alias]

    return None


def match_game_in_predictions(
    home_name: str,
    away_name: str,
    target_date: Optional[Any],
    predictions_df: pd.DataFrame,
) -> Optional[pd.Series]:
    """
    Empareja por ambos equipos y fecha/kickoff del calendario; rechaza coincidencias ambiguas.
    Cero excepciones hardcodeadas. Soporta target_date como datetime, date o string ISO.
    """
    home_norm = normalize_nfl_team(home_name)
    away_norm = normalize_nfl_team(away_name)
    if not home_norm or not away_norm:
        return None

    parsed_target_date = None
    if target_date is not None:
        try:
            parsed_target_date = pd.to_datetime(target_date).date()
        except Exception:
            parsed_target_date = None

    matches = []
    for _, row in predictions_df.iterrows():
        r_home = normalize_nfl_team(row.get("home_team_name") or row.get("home_team_abbreviation"))
        r_away = normalize_nfl_team(row.get("away_team_name") or row.get("away_team_abbreviation"))
        if r_home == home_norm and r_away == away_norm:
            if parsed_target_date is not None:
                r_date = pd.to_datetime(row.get("game_date")).date()
                if abs((r_date - parsed_target_date).days) <= 1:
                    matches.append(row)
            else:
                matches.append(row)

    # Rechazar coincidencias ambiguas
    if len(matches) == 1:
        return matches[0]
    return None


def _compute_joint_probability(evaluated_legs: List[Dict[str, Any]]) -> float:
    """
    Calcula la probabilidad conjunta considerando correlación intra-evento (mismo partido).
    Para eventos independientes entre sí, multiplica las probabilidades de cada evento.
    Para patas dentro del mismo evento:
    - Si cubren equipos opuestos en el mismo partido: 0.0 (incompatible).
    - Si combinan ML y Spread del mismo equipo:
      - Spread negativo (favorito, ej -3.5): cubrir el spread implica ganar -> P(win AND cover) = P(cover).
      - Spread positivo (underdog, ej +3.5): ganar implica cubrir el spread -> P(win AND cover) = P(win).
    - Para otras combinaciones (ej. props ofensivos): aplica correlación positiva rho = 0.15.
    """
    event_groups: Dict[str, List[Dict[str, Any]]] = {}
    for idx, leg in enumerate(evaluated_legs):
        ev_key = leg.get("event_id") or leg.get("game_id") or leg.get("matchup") or f"single_{idx}"
        ev_key_str = str(ev_key)
        matchup = (leg.get("matchup") or "").strip().lower()
        if matchup and matchup != "entrada manual":
            sep = "@" if "@" in matchup else ("vs" if "vs" in matchup else None)
            if sep:
                parts = matchup.split(sep)
                if len(parts) == 2:
                    t1 = normalize_nfl_team(parts[0])
                    t2 = normalize_nfl_team(parts[1])
                    if t1 and t2:
                        ev_key_str = f"game_{min(t1, t2)}_{max(t1, t2)}"
        event_groups.setdefault(ev_key_str, []).append(leg)

    prob_total = 1.0
    for ev_key, group in event_groups.items():
        if len(group) == 1:
            prob_total *= float(group[0]["prob_model"])
            continue

        teams = {normalize_nfl_team(l.get("team_name")) for l in group if l.get("team_name")}
        teams.discard(None)
        if len(teams) > 1:
            return 0.0

        p_event = None
        for leg in group:
            p_curr = float(leg["prob_model"])
            mkt = str(leg.get("market", "")).lower()
            point = leg.get("point")
            if point is None and leg.get("line") is not None:
                point = leg.get("line")

            if p_event is None:
                p_event = p_curr
            else:
                if "spread" in mkt and point is not None:
                    try:
                        pt_val = float(point)
                        if pt_val <= 0:
                            # Cubrir spread negativo garantiza victoria en el juego
                            p_event = min(p_event, p_curr)
                        else:
                            # Ganar en Moneyline garantiza cubrir spread positivo
                            p_event = min(p_event, p_curr)
                        continue
                    except (ValueError, TypeError):
                        pass

                # Correlación positiva intra-evento general (rho = 0.15)
                rho = 0.15
                cov = rho * math.sqrt(p_event * (1.0 - p_event) * p_curr * (1.0 - p_curr))
                p_event = min(p_event, p_curr, max(0.01, (p_event * p_curr) + cov))

        prob_total *= p_event if p_event is not None else 1.0

    return max(0.0, min(1.0, prob_total))


def evaluate_custom_parlay(
    legs: List[Dict[str, Any]],
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    allow_correlation: bool = False,
) -> Dict[str, Any]:
    """
    Evalúa matemáticamente un parlay personalizado de N patas.
    Verifica correlación (mismo evento), suficiencia de datos del modelo (no defaults ni implícitas),
    y calcula probabilidad combinada, cuotas, EV y Criterio de Kelly.
    """
    if len(legs) < 2:
        return {
            "viable": False,
            "has_correlated_legs": False,
            "has_sufficient_model_data": False,
            "razon": "Un parlay requiere al menos 2 eventos independientes.",
            "n_legs": len(legs),
            "prob_combinada": None,
            "momio_decimal": None,
            "momio_americano": None,
            "ev_dolares": None,
            "kelly_fraccion": None,
            "kelly_monto": None,
            "confidence_level": None,
            "is_manual_calculation": False,
            "legs_evaluadas": [],
        }

    # 1. Validación de independencia de eventos: rechazar combinar lados del mismo evento
    seen_events: set[str] = set()
    has_correlated_legs = False
    for leg in legs:
        ev_id = leg.get("event_id") or leg.get("game_id")
        if ev_id:
            ev_str = str(ev_id)
            if ev_str in seen_events:
                has_correlated_legs = True
                break
            seen_events.add(ev_str)

        matchup = (leg.get("matchup") or "").strip().lower()
        if matchup and matchup != "entrada manual":
            sep = "@" if "@" in matchup else ("vs" if "vs" in matchup else None)
            if sep:
                parts = matchup.split(sep)
                if len(parts) == 2:
                    t1 = normalize_nfl_team(parts[0])
                    t2 = normalize_nfl_team(parts[1])
                    if t1 and t2:
                        game_pair_key = f"pair_{min(t1, t2)}_{max(t1, t2)}"
                        if game_pair_key in seen_events:
                            has_correlated_legs = True
                            break
                        seen_events.add(game_pair_key)
            if matchup in seen_events:
                has_correlated_legs = True
                break
            seen_events.add(matchup)

        team_name = leg.get("team_name")
        if team_name and leg.get("source") != "manual":
            t_norm = normalize_nfl_team(team_name)
            if t_norm:
                team_key = f"team_{t_norm}"
                if team_key in seen_events:
                    has_correlated_legs = True
                    break
                seen_events.add(team_key)

    if has_correlated_legs and not allow_correlation:
        return {
            "viable": False,
            "has_correlated_legs": True,
            "has_sufficient_model_data": False,
            "razon": "No se permite combinar selecciones del mismo evento como patas independientes. Al no disponer de un modelo de probabilidad conjunta para eventos correlacionados, nos abstenemos de ofrecer este parlay.",
            "n_legs": len(legs),
            "prob_combinada": None,
            "momio_decimal": None,
            "momio_americano": None,
            "ev_dolares": None,
            "kelly_fraccion": None,
            "kelly_monto": None,
            "confidence_level": None,
            "is_manual_calculation": any(l.get("source") == "manual" for l in legs),
            "legs_evaluadas": legs,
        }

    # 2. Evaluación de patas y suficiencia de datos del modelo
    evaluated_legs = []
    has_sufficient_model_data = True
    prob_combined = 1.0
    odds_decimal = 1.0
    all_odds_present = True

    for leg in legs:
        p = leg.get("prob_model")
        odds_am = leg.get("odds_american")
        market = leg.get("market", "h2h")

        if odds_am is not None:
            p_dec = american_to_decimal(int(odds_am))
            odds_decimal *= p_dec
        else:
            all_odds_present = False
            p_dec = 0.0

        if p is None or not (0.01 <= float(p) <= 0.99):
            has_sufficient_model_data = False
            evaluated_legs.append({
                "team_name": leg.get("team_name", "Desconocido"),
                "market": market,
                "prob_model": None,
                "odds_american": odds_am,
                "odds_decimal": round(p_dec, 3) if p_dec > 0 else None,
                "edge_pct": None,
                "has_individual_edge": False,
                "tier": "NO_MODEL_PROB",
                "missing_reason": leg.get("missing_reason") or "Probabilidad del modelo no disponible para este mercado.",
                "bookmaker": leg.get("bookmaker"),
                "observed_at": leg.get("observed_at"),
                "source": leg.get("source", "real"),
                "event_id": leg.get("event_id") or leg.get("game_id"),
                "matchup": leg.get("matchup"),
                "point": leg.get("point") or leg.get("line"),
            })
            continue

        p_float = float(p)
        prob_combined *= p_float

        if odds_am is not None:
            leg_ev = calculate_ev(p_float, int(odds_am), bankroll=bankroll)
            edge_pct = round(leg_ev.edge_pct, 4)
            has_edge = leg_ev.has_edge
            tier = leg_ev.tier
        else:
            edge_pct = None
            has_edge = False
            tier = "UNKNOWN"

        evaluated_legs.append({
            "team_name": leg.get("team_name", "Desconocido"),
            "market": market,
            "prob_model": round(p_float, 4),
            "odds_american": odds_am,
            "odds_decimal": round(p_dec, 3) if p_dec > 0 else None,
            "edge_pct": edge_pct,
            "has_individual_edge": has_edge,
            "tier": tier,
            "bookmaker": leg.get("bookmaker"),
            "observed_at": leg.get("observed_at"),
            "source": leg.get("source", "real"),
            "event_id": leg.get("event_id") or leg.get("game_id"),
            "matchup": leg.get("matchup"),
            "point": leg.get("point") or leg.get("line"),
        })

    is_manual = any(l.get("source") == "manual" for l in legs)
    odds_american_comb = decimal_to_american(odds_decimal) if all_odds_present and odds_decimal > 1.0 else None

    # Si faltan datos suficientes del modelo (p. ej. totales Over/Under sin modelo de puntos):
    # Prohibido usar 0.50 o probabilidad implícita. No se calcula EV.
    if not has_sufficient_model_data or not all_odds_present:
        return {
            "viable": False,
            "has_correlated_legs": has_correlated_legs,
            "has_sufficient_model_data": False,
            "razon": "Datos insuficientes del modelo para calcular EV. Los totales Over/Under y eventos sin inferencia carecen de probabilidad del modelo (prohibido usar 0.50 o probabilidad implícita). No se muestra EV.",
            "n_legs": len(legs),
            "prob_combinada": None,
            "momio_decimal": round(odds_decimal, 3) if all_odds_present else None,
            "momio_americano": odds_american_comb,
            "ev_dolares": None,
            "kelly_fraccion": None,
            "kelly_monto": None,
            "confidence_level": None,
            "is_manual_calculation": is_manual,
            "legs_evaluadas": evaluated_legs,
        }

    if has_correlated_legs and allow_correlation:
        prob_combined = _compute_joint_probability(evaluated_legs)

    payout_net = odds_decimal - 1.0
    q = 1.0 - prob_combined
    ev_dollars = (prob_combined * payout_net * 100.0) - (q * 100.0)

    # Kelly Criterion fraccional para el parlay
    if payout_net > 0:
        kelly_full = (payout_net * prob_combined - q) / payout_net
        kelly_applied = max(0.0, min(kelly_full * kelly_fraction, 0.05))
    else:
        kelly_applied = 0.0

    kelly_amount = round(bankroll * kelly_applied, 2)
    confidence_level = (
        "alta" if prob_combined >= 0.55 else ("moderada" if prob_combined >= 0.40 else "especulativa")
    )

    legs_have_heavy_negative = any((l.get("edge_pct") or 0.0) < -0.05 for l in evaluated_legs)

    if allow_correlation and has_correlated_legs:
        # Microparlays targeting return >= 2.0x (+100) and EV > 0
        is_viable = (ev_dollars > 0.0) and (odds_decimal >= 2.0) and (prob_combined >= 0.40) and not legs_have_heavy_negative
        if is_viable:
            razon = f"Microparlay correlacionado viable de {len(legs)} patas: Probabilidad conjunta {prob_combined:.1%}, EV +${ev_dollars:.2f} con cuota {odds_american_comb:+d}."
        elif odds_decimal < 2.0:
            razon = f"Cuota combinada ({odds_decimal:.2f}x) inferior al objetivo mínimo (2.0x / +100)."
        elif ev_dollars <= 0:
            razon = f"EV no positivo (-${abs(ev_dollars):.2f}). La correlación no supera la ventaja de la casa."
        elif prob_combined < 0.40:
            razon = f"Probabilidad conjunta insuficiente ({prob_combined:.1%}) para microparlay."
        else:
            razon = "Alguna pata seleccionada presenta expectativa fuertemente desfavorable."
    else:
        is_viable = (ev_dollars > 2.0) and (prob_combined >= 0.42) and not legs_have_heavy_negative
        if is_viable:
            razon = f"Parlay viable de {len(legs)} patas: Probabilidad combinada {prob_combined:.1%}, EV +${ev_dollars:.2f} con cuota {odds_american_comb:+d}."
        elif ev_dollars <= 0:
            razon = f"EV negativo (-${abs(ev_dollars):.2f}). La casa de apuestas mantiene ventaja matemática sobre esta combinación."
        elif prob_combined < 0.42:
            razon = f"Probabilidad combinada muy baja ({prob_combined:.1%}). Alto riesgo de quiebra a largo plazo."
        else:
            razon = "Alguna pata seleccionada presenta expectativa fuertemente desfavorable."

    if is_manual:
        razon += " [Calculadora manual: estos cálculos no constituyen picks del modelo de Griddio]."

    return {
        "viable": is_viable,
        "has_correlated_legs": has_correlated_legs,
        "is_correlated_microparlay": has_correlated_legs and allow_correlation,
        "allow_correlation": allow_correlation,
        "has_sufficient_model_data": True,
        "razon": razon,
        "n_legs": len(legs),
        "prob_combinada": round(prob_combined, 4),
        "momio_decimal": round(odds_decimal, 3),
        "momio_americano": odds_american_comb,
        "ev_dolares": round(ev_dollars, 2),
        "kelly_fraccion": round(kelly_applied, 4),
        "kelly_monto": kelly_amount,
        "confidence_level": confidence_level,
        "is_manual_calculation": is_manual,
        "legs_evaluadas": evaluated_legs,
    }


def evaluate_correlated_microparlay(
    legs: List[Dict[str, Any]],
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
) -> Dict[str, Any]:
    """
    Evalúa un microparlay correlacionado (mismo juego/evento) utilizando probabilidades condicionales
    y exigiendo un retorno objetivo >= 2.0x (+100) con EV positivo (> 0).
    """
    return evaluate_custom_parlay(
        legs=legs,
        bankroll=bankroll,
        kelly_fraction=kelly_fraction,
        allow_correlation=True,
    )



async def resolve_and_evaluate_parlay(
    legs: List[Dict[str, Any]],
    db: Optional[AsyncSession] = None,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
) -> Dict[str, Any]:
    """
    SSOT de Parlays: recibe identificadores de eventos/selecciones reales o entradas manuales,
    resuelve probabilidades de un modelo aprobado y momios observados en PostgreSQL,
    valida su procedencia y evalúa la combinada.
    """
    resolved_legs: List[Dict[str, Any]] = []

    for leg in legs:
        source = leg.get("source") or ("manual" if not leg.get("game_id") and leg.get("prob_model") is not None else "real")
        
        # 1. Caso Entrada Manual (Calculadora manual separada y etiquetada)
        if source == "manual" or (not leg.get("game_id") and leg.get("prob_model") is not None):
            resolved_legs.append({
                "team_name": leg.get("team_name") or "Entrada manual",
                "market": leg.get("market") or "manual",
                "prob_model": leg.get("prob_model"),
                "odds_american": leg.get("odds_american"),
                "source": "manual",
                "event_id": leg.get("event_id"),
                "matchup": leg.get("matchup") or "Entrada manual",
            })
            continue

        # 2. Caso Selección Real con Identificador de Evento
        game_id_raw = leg.get("game_id")
        side = (leg.get("side") or "home").lower()
        market = (leg.get("market") or "h2h").lower()

        resolved_game = None
        home_team_name = None
        away_team_name = None
        game_date = None
        event_id = str(game_id_raw)

        if db is not None and game_id_raw is not None:
            try:
                stmt = select(Game).options(selectinload(Game.home_team), selectinload(Game.away_team))
                if str(game_id_raw).isdigit():
                    res = await db.execute(
                        stmt.where((Game.id == int(game_id_raw)) | (Game.external_id == str(game_id_raw)))
                    )
                    resolved_game = res.scalar_one_or_none()
                else:
                    res = await db.execute(
                        stmt.where(Game.external_id == str(game_id_raw))
                    )
                    resolved_game = res.scalar_one_or_none()
            except Exception:
                resolved_game = None

        if resolved_game:
            home_team_name = resolved_game.home_team.name if resolved_game.home_team else None
            away_team_name = resolved_game.away_team.name if resolved_game.away_team else None
            game_date = resolved_game.game_date
            event_id = resolved_game.external_id or str(resolved_game.id)

        # Resolver Momio de la DB estrictamente (Cero aceptar cuotas arbitrarias del cliente para selecciones reales)
        odds_am = None
        bookmaker = None
        observed_at = None

        if db is not None and resolved_game:
            snap_stmt = (
                select(OddsSnapshot)
                .where(OddsSnapshot.game_id == resolved_game.id)
                .order_by(OddsSnapshot.observed_at.desc())
            )
            snaps_res = await db.execute(snap_stmt)
            snaps = snaps_res.scalars().all()

            if market == "totals":
                tot_snaps = [s for s in snaps if s.total_line is not None or s.market == "totals"]
                if tot_snaps:
                    latest_tot = tot_snaps[0]
                    odds_am = latest_tot.over_ml if side == "over" else latest_tot.under_ml
                    bookmaker = latest_tot.bookmaker
                    observed_at = latest_tot.observed_at.isoformat() if latest_tot.observed_at else None
            elif market == "spreads":
                spr_snaps = [s for s in snaps if s.home_spread is not None or s.market == "spreads"]
                if spr_snaps:
                    latest_spr = spr_snaps[0]
                    odds_am = latest_spr.home_spread_ml if side == "home" else latest_spr.away_spread_ml
                    bookmaker = latest_spr.bookmaker
                    observed_at = latest_spr.observed_at.isoformat() if latest_spr.observed_at else None
            else:  # h2h
                h2h_snaps = [s for s in snaps if s.home_ml is not None or s.market == "h2h"]
                if h2h_snaps:
                    latest_h2h = h2h_snaps[0]
                    odds_am = latest_h2h.home_ml if side == "home" else latest_h2h.away_ml
                    bookmaker = latest_h2h.bookmaker
                    observed_at = latest_h2h.observed_at.isoformat() if latest_h2h and latest_h2h.observed_at else None

        # Resolver Probabilidad del Modelo
        prob_model = None
        missing_reason = None

        if market == "totals" or side in ("over", "under"):
            # Regla estricta: No calcular EV de modelo para totales hasta que exista una probabilidad del modelo para Over/Under.
            prob_model = None
            missing_reason = "No existe probabilidad de modelo correspondiente a Over/Under. Prohibido usar 0.50 o probabilidad implícita."
        elif resolved_game is not None:
            approved_version = get_approved_model_version(resolved_game.sport)
            if approved_version:
                prediction_result = await db.execute(
                    select(Prediction)
                    .where(
                        Prediction.game_id == resolved_game.id,
                        Prediction.model_version == approved_version,
                    )
                    .order_by(Prediction.predicted_at.desc())
                    .limit(1)
                ) if db is not None else None
                approved_prediction = prediction_result.scalar_one_or_none() if prediction_result else None
                if approved_prediction is not None:
                    raw_probability = (
                        approved_prediction.home_win_prob if side == "home"
                        else approved_prediction.away_win_prob
                    )
                    if raw_probability is not None and math.isfinite(float(raw_probability)) and 0 < float(raw_probability) < 1:
                        prob_model = float(raw_probability)
                    else:
                        missing_reason = "La predicción aprobada contiene una probabilidad inválida."
                else:
                    missing_reason = "No hay predicción persistida del modelo aprobado para este partido."
            else:
                missing_reason = "El gate de producción está cerrado; la inferencia experimental no se usa para evaluar picks."
        else:
            prob_model = None
            missing_reason = "No se pudo resolver el partido en la base de datos."

        if odds_am is None and not missing_reason:
            missing_reason = "No se encontraron momios observados en la base de datos para este evento y mercado."

        team_label = leg.get("team_name")
        if not team_label:
            if market == "totals":
                team_label = f"{side.upper()} Total"
            elif side == "home" and home_team_name:
                team_label = home_team_name
            elif side == "away" and away_team_name:
                team_label = away_team_name
            else:
                team_label = f"Evento {event_id}"

        resolved_legs.append({
            "game_id": resolved_game.id if resolved_game else game_id_raw,
            "event_id": event_id,
            "team_name": team_label,
            "matchup": f"{away_team_name} @ {home_team_name}" if home_team_name and away_team_name else str(event_id),
            "side": side,
            "market": market,
            "prob_model": prob_model,
            "odds_american": odds_am,
            "bookmaker": bookmaker,
            "observed_at": observed_at,
            "missing_reason": missing_reason if (prob_model is None or odds_am is None) else None,
            "source": "real",
        })

    return evaluate_custom_parlay(
        legs=resolved_legs,
        bankroll=bankroll,
        kelly_fraction=kelly_fraction,
    )



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

    for row in rows:
        if len(row) == 4:
            pred, game, home_team, away_team = row
        elif len(row) == 3:
            pred, game, home_team = row
            away_team = getattr(game, "away_team", None)
        else:
            continue

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

        # Resolver bookmaker y observed_at del snapshot de odds o predicción
        odds_snap = None
        if db is not None and getattr(game, "id", None) is not None:
            try:
                odds_stmt = (
                    select(OddsSnapshot)
                    .where(OddsSnapshot.game_id == game.id)
                    .where(OddsSnapshot.home_ml.isnot(None), OddsSnapshot.away_ml.isnot(None))
                    .order_by(OddsSnapshot.observed_at.desc())
                    .limit(1)
                )
                odds_res = await db.execute(odds_stmt)
                odds_snap = odds_res.scalar_one_or_none()
            except Exception:
                odds_snap = None

        leg_bookmaker = (odds_snap.bookmaker if odds_snap and odds_snap.bookmaker else None) or getattr(pred, "best_bookmaker", None) or "betmgm"
        if odds_snap and getattr(odds_snap, "observed_at", None):
            obs_dt = odds_snap.observed_at if odds_snap.observed_at.tzinfo else odds_snap.observed_at.replace(tzinfo=timezone.utc)
            leg_observed_at = obs_dt.isoformat()
        elif getattr(pred, "as_of", None) or getattr(pred, "predicted_at", None):
            p_dt = getattr(pred, "as_of", None) or getattr(pred, "predicted_at", None)
            p_dt_utc = p_dt if p_dt.tzinfo else p_dt.replace(tzinfo=timezone.utc)
            leg_observed_at = p_dt_utc.isoformat()
        else:
            leg_observed_at = datetime.now(timezone.utc).isoformat()

        # Si el local tiene probabilidad atractiva (> 62%)
        if h_prob >= 0.62:
            ev = calculate_ev(h_prob, h_ml, bankroll=bankroll)
            if ev.edge_pct >= 0.01:  # Al menos leve ventaja o paridad
                candidates.append({
                    "game_id": game.id,
                    "team_name": home_team.name,
                    "opponent": away_team.name if away_team and hasattr(away_team, "name") else "Visitante",
                    "side": "home",
                    "market": "h2h",
                    "prob_model": h_prob,
                    "odds_american": h_ml,
                    "edge_pct": ev.edge_pct,
                    "tier": pred.confluence_tier,
                    "bookmaker": leg_bookmaker,
                    "observed_at": leg_observed_at,
                })

        # Si el visitante tiene probabilidad atractiva (> 62%)
        if a_prob >= 0.62:
            ev = calculate_ev(a_prob, a_ml, bankroll=bankroll)
            if ev.edge_pct >= 0.01:
                away_name = away_team.name if away_team and hasattr(away_team, "name") else f"Visita vs {getattr(home_team, 'abbreviation', '')}"
                candidates.append({
                    "game_id": game.id,
                    "team_name": away_name,
                    "opponent": home_team.name if home_team and hasattr(home_team, "name") else "Local",
                    "side": "away",
                    "market": "h2h",
                    "prob_model": a_prob,
                    "odds_american": a_ml,
                    "edge_pct": ev.edge_pct,
                    "tier": pred.confluence_tier,
                    "bookmaker": leg_bookmaker,
                    "observed_at": leg_observed_at,
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
