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
import numpy as np

from app.services.math.odds_converter import (
    american_to_decimal,
    american_to_implied_prob,
    remove_vig,
)
from app.services.math.kelly import calculate_kelly_fraction, calculate_kelly_amount
from app.services.math.ev import TierStr
from scipy.stats import norm

logger = structlog.get_logger(__name__)

_CALIBRATED_TOTALS_SIGMA: Optional[float] = None


def get_nfl_totals_calibrated_sigma(
    force_refresh: bool = False,
    metadata_override: Optional[Dict[str, Any]] = None,
) -> float:
    """Obtiene la residual_sigma calibrada de NFL Totals desde metadata OOF/training o fallback 14.6.

    Excluye estrictamente cualquier métrica de holdout_evaluation para evitar que los datos
    de holdout/lockbox intervengan en la calibración o en las probabilidades servidas en producción.
    """
    global _CALIBRATED_TOTALS_SIGMA
    if not force_refresh and metadata_override is None and _CALIBRATED_TOTALS_SIGMA is not None:
        return _CALIBRATED_TOTALS_SIGMA
    try:
        if metadata_override is not None:
            meta = metadata_override
        else:
            from ml.data_pipeline.common import backend_root
            meta_path = backend_root() / "ml" / "models" / "nfl" / "model_metadata.json"
            if not meta_path.is_file():
                return 14.6
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        t_eval = meta.get("totals_evaluation", {})
        # EXCLUSIÓN ESTRICTA DE HOLDOUT:
        # Jamás consultar t_eval.get("holdout_evaluation") ni holdout de modelos individuales.
        # Solo se extrae residual_sigma de metrics de entrenamiento/OOF o de folds de desarrollo.
        parsed_sigma = None
        raw_sigma = t_eval.get("metrics", {}).get("residual_sigma")
        if raw_sigma is not None and not isinstance(raw_sigma, (bool, np.bool_)):
            try:
                candidate = float(raw_sigma)
                if math.isfinite(candidate) and candidate > 0:
                    parsed_sigma = candidate
            except (TypeError, ValueError):
                parsed_sigma = None

        if parsed_sigma is None:
            sel_model = t_eval.get("selected_model", "ridge")
            model_info = t_eval.get("models", {}).get(sel_model, {})
            folds = model_info.get("folds", [])
            valid_fold_sigmas = []
            for f in folds:
                f_sig = f.get("residual_sigma")
                if f_sig is not None and not isinstance(f_sig, (bool, np.bool_)):
                    try:
                        f_candidate = float(f_sig)
                        if math.isfinite(f_candidate) and f_candidate > 0:
                            valid_fold_sigmas.append(f_candidate)
                    except (TypeError, ValueError):
                        pass
            if valid_fold_sigmas:
                parsed_sigma = float(np.mean(valid_fold_sigmas))

        if parsed_sigma is not None:
            if metadata_override is None:
                _CALIBRATED_TOTALS_SIGMA = parsed_sigma
            return parsed_sigma
    except Exception:
        pass
    return 14.6


def generate_nfl_matchup_analytics(
    home_team: str,
    away_team: str,
    game_date: datetime,
    home_ml: int,
    away_ml: int,
    spread_line: Optional[float] = None,
    total_line: Optional[float] = None,
    home_spread_ml: Optional[int] = None,
    away_spread_ml: Optional[int] = None,
    over_ml: Optional[int] = None,
    under_ml: Optional[int] = None,
    bookmaker: Optional[str] = None,
    home_bookmaker: Optional[str] = None,
    away_bookmaker: Optional[str] = None,
    spread_home_bookmaker: Optional[str] = None,
    spread_away_bookmaker: Optional[str] = None,
    total_over_bookmaker: Optional[str] = None,
    total_under_bookmaker: Optional[str] = None,
    h2h_benchmark_bookmaker: Optional[str] = None,
    benchmark_bookmaker: Optional[str] = None,
    benchmark_observed_at: Optional[Any] = None,
    market_prob_home_no_vig: Optional[float] = None,
    market_prob_away_no_vig: Optional[float] = None,
    bankroll: Optional[float] = None,
    live_player_props: Optional[List[Dict[str, Any]]] = None,
    model_prob_home: Optional[float] = None,
    model_prob_away: Optional[float] = None,
    model_agreement: Optional[int] = None,
    confluence_tier: Optional[str] = None,
    qb_expected_epa_lost_home: Optional[float] = None,
    qb_expected_epa_lost_away: Optional[float] = None,
    model_projected_total: Optional[float] = None,
    totals_residual_sigma: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Genera el análisis probabilístico, líneas de spread, totales y player props reales para un partido NFL.
    Calcula de forma explícita el EV y la asignación Kelly en CADA uno de los mercados, seleccionando
    el Best Bet (#1 Pick) para indicar con total transparencia dónde se encuentra la ventaja matemática.
    """
    # 0. Sanitización de líneas de spread, total y momios observados (fail-closed)
    if total_line is not None:
        if isinstance(total_line, (bool, np.bool_)):
            total_line = None
        else:
            try:
                tl = float(total_line)
                if not math.isfinite(tl) or tl <= 0:
                    total_line = None
                else:
                    total_line = tl
            except (TypeError, ValueError):
                total_line = None

    parsed_proj_total = None
    if model_projected_total is not None:
        if isinstance(model_projected_total, (int, float, np.floating, np.integer, str)) and not isinstance(model_projected_total, (bool, np.bool_)):
            try:
                pt = float(model_projected_total)
                if math.isfinite(pt) and pt > 0:
                    parsed_proj_total = pt
            except (TypeError, ValueError):
                parsed_proj_total = None

    calibrated_default_sigma = get_nfl_totals_calibrated_sigma()
    parsed_totals_sigma = calibrated_default_sigma
    if totals_residual_sigma is not None:
        if isinstance(totals_residual_sigma, (int, float, np.floating, np.integer, str)) and not isinstance(totals_residual_sigma, (bool, np.bool_)):
            try:
                ts = float(totals_residual_sigma)
                if math.isfinite(ts) and ts > 0:
                    parsed_totals_sigma = ts
            except (TypeError, ValueError):
                parsed_totals_sigma = calibrated_default_sigma

    if spread_line is not None:
        if isinstance(spread_line, (bool, np.bool_)):
            spread_line = None
        else:
            try:
                sl = float(spread_line)
                if not math.isfinite(sl):
                    spread_line = None
                else:
                    spread_line = sl
            except (TypeError, ValueError):
                spread_line = None

    def _is_valid_american(val: Any) -> bool:
        if not isinstance(val, (int, float, np.floating, np.integer, str)) or isinstance(val, (bool, np.bool_)):
            return False
        try:
            f = float(val)
            return math.isfinite(f) and f.is_integer() and f != 0 and (-100 >= f or f >= 100)
        except (TypeError, ValueError):
            return False

    home_spread_explicit_invalid = home_spread_ml is not None and not _is_valid_american(home_spread_ml)
    away_spread_explicit_invalid = away_spread_ml is not None and not _is_valid_american(away_spread_ml)
    spread_odds_corrupt = home_spread_explicit_invalid or away_spread_explicit_invalid

    over_explicit_invalid = over_ml is not None and not _is_valid_american(over_ml)
    under_explicit_invalid = under_ml is not None and not _is_valid_american(under_ml)
    totals_odds_corrupt = over_explicit_invalid or under_explicit_invalid

    # 1. Probabilidades implícitas de mercado (sin vig)
    valid_ml = (
        home_ml is not None and away_ml is not None
        and home_ml != 0 and away_ml != 0
        and math.isfinite(home_ml) and math.isfinite(away_ml)
        and (-100 >= home_ml or home_ml >= 100)
        and (-100 >= away_ml or away_ml >= 100)
    )
    valid_market_fair = (
        market_prob_home_no_vig is not None
        and market_prob_away_no_vig is not None
        and isinstance(market_prob_home_no_vig, (int, float, np.floating, np.integer))
        and isinstance(market_prob_away_no_vig, (int, float, np.floating, np.integer))
        and not isinstance(market_prob_home_no_vig, (bool, np.bool_))
        and not isinstance(market_prob_away_no_vig, (bool, np.bool_))
        and math.isfinite(market_prob_home_no_vig)
        and math.isfinite(market_prob_away_no_vig)
        and 0.0 < market_prob_home_no_vig < 1.0
        and 0.0 < market_prob_away_no_vig < 1.0
    )
    if valid_market_fair:
        p_h_fair = float(market_prob_home_no_vig)
        p_a_fair = float(market_prob_away_no_vig)
        if valid_ml:
            p_h_raw = american_to_implied_prob(home_ml)
            p_a_raw = american_to_implied_prob(away_ml)
            vig = round((p_h_raw + p_a_raw) - 1.0, 4)
        else:
            vig = 0.0
    elif valid_ml:
        p_h_raw = american_to_implied_prob(home_ml)
        p_a_raw = american_to_implied_prob(away_ml)
        p_h_fair, p_a_fair, vig = remove_vig(p_h_raw, p_a_raw)
    else:
        p_h_fair, p_a_fair, vig = None, None, 0.0

    # 2. Probabilidades del Modelo (SSOT: Respeta la probabilidad generada por el motor de inferencia)
    model_valid = (
        model_prob_home is not None
        and model_prob_away is not None
        and not isinstance(model_prob_home, (bool, np.bool_))
        and not isinstance(model_prob_away, (bool, np.bool_))
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
        # El margen base deriva solo de la probabilidad del modelo. El estimador
        # QB se mantiene como diagnóstico hasta completar la ablación de Punto 6.
        proj_margin_raw = proj_margin_base
        t_base = total_line if total_line is not None else 44.0
        if parsed_proj_total is not None:
            proj_total = round(parsed_proj_total, 1)
        else:
            proj_total = None

        effective_total_for_scores = proj_total if proj_total is not None else t_base
        proj_home_score = round((effective_total_for_scores + proj_margin_raw) / 2.0, 1)
        proj_away_score = round((effective_total_for_scores - proj_margin_raw) / 2.0, 1)
        proj_margin = round(proj_home_score - proj_away_score, 1)
    else:
        proj_margin_raw = None
        proj_margin = None
        proj_home_score = None
        proj_away_score = None
        proj_total = None

    # 3. Cálculo de EV y Kelly en Moneyline (confluencia y cuota >= 1.40)
    if model_valid and valid_ml:
        dec_h = american_to_decimal(home_ml)
        dec_a = american_to_decimal(away_ml)

        ev_h = (p_home_model * (dec_h - 1.0) * 100.0) - ((1.0 - p_home_model) * 100.0)
        ev_a = (p_away_model * (dec_a - 1.0) * 100.0) - ((1.0 - p_away_model) * 100.0)

        edge_h = p_home_model - p_h_fair if p_h_fair is not None else None
        edge_a = p_away_model - p_a_fair if p_a_fair is not None else None

        confluence_ok = (model_agreement is None or model_agreement >= 2) and (
            confluence_tier is None or confluence_tier.upper() != "NONE"
        )

        home_eligible = confluence_ok and (dec_h >= 1.40) and (ev_h is not None and ev_h > 0)
        away_eligible = confluence_ok and (dec_a >= 1.40) and (ev_a is not None and ev_a > 0)

        home_is_confianza = home_eligible and (p_home_model > 0.55) and (edge_h is not None and edge_h >= 0.03) and (model_agreement is None or model_agreement >= 3)
        away_is_confianza = away_eligible and (p_away_model > 0.55) and (edge_a is not None and edge_a >= 0.03) and (model_agreement is None or model_agreement >= 3)
    else:
        dec_h, dec_a = None, None
        ev_h, ev_a = None, None
        edge_h, edge_a = None, None
        confluence_ok = False
        home_eligible, away_eligible = False, False
        home_is_confianza, away_is_confianza = False, False

    if not model_valid or not valid_ml:
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
    # Si los momios de spread son explícitamente inválidos, no se sintetiza -110 ni se produce EV/Kelly.
    if model_valid and spread_line is not None and not spread_odds_corrupt:
        z_cover = (proj_margin_raw + spread_line) / 13.5
        p_home_cover = float(norm.cdf(z_cover))
        p_away_cover = 1.0 - p_home_cover

        # Preservar precio observado o fallback estándar -110 ÚNICAMENTE si está ausente (None)
        h_spr_odds = home_spread_ml if home_spread_ml is not None else -110
        a_spr_odds = away_spread_ml if away_spread_ml is not None else -110
        dec_home_spread = american_to_decimal(h_spr_odds)
        dec_away_spread = american_to_decimal(a_spr_odds)

        ev_home_spread = (p_home_cover * (dec_home_spread - 1.0) * 100.0) - ((1.0 - p_home_cover) * 100.0)
        ev_away_spread = (p_away_cover * (dec_away_spread - 1.0) * 100.0) - ((1.0 - p_away_cover) * 100.0)

        # De-vigging riguroso de la línea de spread observada
        p_h_spr_raw = american_to_implied_prob(h_spr_odds)
        p_a_spr_raw = american_to_implied_prob(a_spr_odds)
        fair_h_spr, fair_a_spr, spr_vig = remove_vig(p_h_spr_raw, p_a_spr_raw)

        edge_home_spread = p_home_cover - fair_h_spr
        edge_away_spread = p_away_cover - fair_a_spr

        spread_confluence_ok = (model_agreement is None or model_agreement >= 2) and (
            confluence_tier is None or confluence_tier.upper() != "NONE"
        )

        if ev_home_spread > 0 and p_home_cover > p_away_cover and dec_home_spread >= 1.40 and spread_confluence_ok and p_home_cover > 0.55 and edge_home_spread >= 0.03:
            spread_pick = f"{home_team} {spread_line:+0.1f}"
            spread_cover_prob = round(p_home_cover, 4)
            ev_spread = round(ev_home_spread, 2)
            spread_edge = round(edge_home_spread * 100.0, 1)
            spread_tier = TierStr("CONFIANZA")
            spread_kelly_frac = calculate_kelly_fraction(p_home_cover, dec_home_spread, fraction_multiplier=0.15, max_cap=0.03)
            spread_odds_american = h_spr_odds
        elif ev_away_spread > 0 and p_away_cover > p_home_cover and dec_away_spread >= 1.40 and spread_confluence_ok and p_away_cover > 0.55 and edge_away_spread >= 0.03:
            spread_pick = f"{away_team} {-spread_line:+0.1f}"
            spread_cover_prob = round(p_away_cover, 4)
            ev_spread = round(ev_away_spread, 2)
            spread_edge = round(edge_away_spread * 100.0, 1)
            spread_tier = TierStr("CONFIANZA")
            spread_kelly_frac = calculate_kelly_fraction(p_away_cover, dec_away_spread, fraction_multiplier=0.15, max_cap=0.03)
            spread_odds_american = a_spr_odds
        else:
            spread_pick = None
            spread_cover_prob = round(max(p_home_cover, p_away_cover), 4)
            ev_spread = round(max(ev_home_spread, ev_away_spread), 2)
            spread_edge = round(max(edge_home_spread, edge_away_spread) * 100.0, 1)
            spread_tier = TierStr("NO PICK")
            spread_kelly_frac = 0.0
            spread_odds_american = None

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
        spread_odds_american = None
        fair_h_spr, fair_a_spr, spr_vig = None, None, None

    # 5. Análisis Cuantitativo de Totales
    # Respetar totals_evaluation.status == "experimental" y production_enabled == false:
    # no presentar Totals como pick oficial ni EV validado;
    # cualquier vista debe dejar claro el estado experimental hasta que exista evidencia prospectiva de skill.
    totals_is_available = (
        total_line is not None
        and not totals_odds_corrupt
        and model_valid
        and proj_total is not None
    )

    if totals_is_available:
        z_tot = (proj_total - total_line) / parsed_totals_sigma
        p_over_raw = float(norm.cdf(z_tot))
        p_under_raw = 1.0 - p_over_raw

        has_observed_totals_odds = (
            over_ml is not None
            and under_ml is not None
            and not totals_odds_corrupt
        )

        if has_observed_totals_odds:
            dec_over = american_to_decimal(over_ml)
            dec_under = american_to_decimal(under_ml)
            ev_over = (p_over_raw * (dec_over - 1.0) * 100.0) - ((1.0 - p_over_raw) * 100.0)
            ev_under = (p_under_raw * (dec_under - 1.0) * 100.0) - ((1.0 - p_under_raw) * 100.0)
            p_o_raw = american_to_implied_prob(over_ml)
            p_u_raw = american_to_implied_prob(under_ml)
            fair_o, fair_u, totals_vig = remove_vig(p_o_raw, p_u_raw)
            edge_over = p_over_raw - fair_o
            edge_under = p_under_raw - fair_u
        else:
            ev_over = None
            ev_under = None
            fair_o, fair_u, totals_vig = None, None, None
            edge_over = None
            edge_under = None

        # Manejo de Push y selección de lado
        if math.isclose(proj_total, total_line, abs_tol=1e-3):
            totals_pick = "PUSH"
            totals_prob = 0.5
            p_over = 0.5
            p_under = 0.5
            ev_totals = 0.0 if has_observed_totals_odds else None
            totals_edge = 0.0 if has_observed_totals_odds else None
            totals_odds_american = None
            totals_recommendation = (
                f"Modelo experimental (sin producción): Proyección {proj_total:.1f} vs Línea {total_line:.1f} (PUSH / Línea Neutra)."
            )
        elif proj_total > total_line:
            totals_pick = "OVER"
            totals_prob = round(p_over_raw, 4)
            p_over = round(p_over_raw, 4)
            p_under = round(p_under_raw, 4)
            ev_totals = round(ev_over, 2) if has_observed_totals_odds else None
            totals_edge = round(edge_over * 100.0, 1) if (has_observed_totals_odds and edge_over is not None) else None
            totals_odds_american = over_ml if has_observed_totals_odds else None
            if has_observed_totals_odds:
                totals_recommendation = (
                    f"Modelo experimental (sin producción): Proyección {proj_total:.1f} vs Línea {total_line:.1f} (OVER)."
                )
            else:
                totals_recommendation = (
                    f"Modelo experimental (sin producción): Proyección {proj_total:.1f} vs Línea {total_line:.1f} (OVER). Sin momios observados para cálculo de EV."
                )
        else:
            totals_pick = "UNDER"
            totals_prob = round(p_under_raw, 4)
            p_over = round(p_over_raw, 4)
            p_under = round(p_under_raw, 4)
            ev_totals = round(ev_under, 2) if has_observed_totals_odds else None
            totals_edge = round(edge_under * 100.0, 1) if (has_observed_totals_odds and edge_under is not None) else None
            totals_odds_american = under_ml if has_observed_totals_odds else None
            if has_observed_totals_odds:
                totals_recommendation = (
                    f"Modelo experimental (sin producción): Proyección {proj_total:.1f} vs Línea {total_line:.1f} (UNDER)."
                )
            else:
                totals_recommendation = (
                    f"Modelo experimental (sin producción): Proyección {proj_total:.1f} vs Línea {total_line:.1f} (UNDER). Sin momios observados para cálculo de EV."
                )

        # Totals se mantiene estrictamente experimental: NO tier CONFIANZA ni pick oficial
        totals_tier = "EXPERIMENTAL"
        totals_kelly_frac = 0.0
        totals_kelly_stake = None
        totals_has_kelly_edge = False
    else:
        totals_pick = None
        totals_prob = None
        p_over = None
        p_under = None
        totals_recommendation = (
            "Totales no disponibles sin modelo de inferencia o proyección."
            if proj_total is None
            else (
                "Totales no disponibles en cuotas observadas."
                if total_line is None
                else (
                    "Momios de totales explícitamente inválidos."
                    if totals_odds_corrupt
                    else "Totales no disponibles."
                )
            )
        )
        totals_tier = "UNAVAILABLE"
        totals_edge = None
        ev_totals = None
        totals_kelly_frac = None
        totals_kelly_stake = None
        totals_has_kelly_edge = False
        totals_odds_american = None
        if over_ml is not None and under_ml is not None and not totals_odds_corrupt:
            p_o_raw = american_to_implied_prob(over_ml)
            p_u_raw = american_to_implied_prob(under_ml)
            fair_o, fair_u, totals_vig = remove_vig(p_o_raw, p_u_raw)
        else:
            fair_o, fair_u, totals_vig = None, None, None

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
    # Compara objetivamente la rentabilidad y ventaja entre Moneyline, Spread y Props con cuotas reales
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
            "edge_pct": round(max(edge_h, edge_a) * 100, 1) if (edge_h is not None and edge_a is not None) else 0.0,
            "tier": ml_tier,
            "reason": f"Probabilidad modelo de {max(p_home_model, p_away_model)*100:.1f}% supera la cuota de la casa.",
        })

    if spread_tier in ("CONFIANZA", "PREMIUM", "VALUE") and spread_pick is not None and ev_spread is not None and ev_spread > 0:
        spr_odds_str = f" ({spread_odds_american:+d})" if spread_odds_american is not None else " (-110)"
        candidates.append({
            "market": "spread",
            "market_label": "Hándicap / Spread",
            "selection": f"{spread_pick}{spr_odds_str}",
            "odds_american": spread_odds_american or -110,
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

    # Priorizar por Tier CONFIANZA / PREMIUM y mayor EV
    candidates.sort(key=lambda c: (c["tier"] in ("CONFIANZA", "PREMIUM", "VALUE"), c["ev_dollars"]), reverse=True)
    best_bet = candidates[0] if candidates else None

    # 8. Factores Clave Explicados para Humanos (SSOT con recomendación clara de Kelly)
    if model_valid:
        favorite_team = away_team if p_away_model > p_home_model else home_team
        fav_prob = max(p_home_model, p_away_model)
        factors = [f"Probabilidad del modelo: {favorite_team} {fav_prob:.1%}."]
    else:
        factors = ["Probabilidad del modelo no disponible; no se deriva de la cuota de mercado."]
    if spread_line is not None and model_valid and not spread_odds_corrupt:
        factors.append(
            f"Línea de Hándicap en {spread_line:+0.1f}: Margen proyectado {proj_margin:+0.1f} pts ({proj_home_score:.1f} vs {proj_away_score:.1f}); prob de cubrir {spread_cover_prob:.1%}."
        )
    elif spread_line is not None and spread_odds_corrupt:
        factors.append(
            f"Línea de Hándicap en {spread_line:+0.1f}: Momio observado explícitamente inválido; se abstiene de inferir precios sintéticos o calcular EV/Kelly."
        )
    elif spread_line is not None:
        factors.append(
            f"Línea de Hándicap en {spread_line:+0.1f}: Sin modelo validado de spread; se omiten proyecciones de margen y picks."
        )
    else:
        factors.append("Línea de Hándicap: No disponible en cuotas observadas.")

    if total_line is not None and totals_is_available:
        if totals_pick == "PUSH":
            factors.append(
                f"Línea de Puntos Totales en {total_line}: Proyección {proj_total:.1f} pts (PUSH / Línea Neutra, prob 50.0%); Modelo Experimental (No Producción)."
            )
        else:
            factors.append(
                f"Línea de Puntos Totales en {total_line}: Proyección {proj_total:.1f} pts ({totals_pick}, prob {totals_prob:.1%}); Modelo Experimental (No Producción)."
            )
    elif total_line is not None and totals_odds_corrupt:
        factors.append(
            f"Línea de Puntos Totales en {total_line}: Momio observado explícitamente inválido; se abstiene de calcular probabilidades o EV."
        )
    elif total_line is not None:
        factors.append(
            f"Línea de Puntos Totales en {total_line}: Sin modelo validado de totales; se omiten proyecciones OVER/UNDER."
        )
    else:
        factors.append("Línea de Puntos Totales: No disponible en cuotas observadas.")

    if best_bet:
        if best_bet.get("market") == "moneyline":
            best_bet["bookmaker"] = (home_bookmaker if ml_side == "home" else away_bookmaker) or bookmaker
        elif best_bet.get("market") == "spread":
            best_bet["bookmaker"] = (spread_home_bookmaker if spread_pick and home_team in spread_pick else (spread_away_bookmaker if spread_pick and away_team in spread_pick else (spread_home_bookmaker or bookmaker)))
        factors.append(
            f"Oportunidad calculada con precio observado: {best_bet['market_label']} ({best_bet['selection']}); EV +${best_bet['ev_dollars']} por cada $100 apostados."
        )
    else:
        factors.append("No hay oportunidad calculable con los inputs disponibles; no se infiere eficiencia del mercado.")

    if qb_expected_epa_lost_home is not None:
        factors.append(
            f"QB EPA vs reemplazo de liga ({home_team}, diagnóstico; sin ajuste al margen): {qb_expected_epa_lost_home:+.1f} EPA."
        )
    if qb_expected_epa_lost_away is not None:
        factors.append(
            f"QB EPA vs reemplazo de liga ({away_team}, diagnóstico; sin ajuste al margen): {qb_expected_epa_lost_away:+.1f} EPA."
        )

    bench_bm = h2h_benchmark_bookmaker or benchmark_bookmaker or home_bookmaker or bookmaker
    bench_obs_str = benchmark_observed_at.isoformat() if hasattr(benchmark_observed_at, "isoformat") else (str(benchmark_observed_at) if benchmark_observed_at else None)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "game_date": game_date.isoformat(),
        "sport": "NFL",
        "bookmaker": bookmaker,
        "status": "available" if model_valid and bookmaker else "unavailable",
        "prob_home": p_home_model,
        "prob_away": p_away_model,
        "h2h_benchmark_bookmaker": bench_bm,
        "benchmark_bookmaker": bench_bm,
        "benchmark_observed_at": bench_obs_str,
        "market_prob_home_no_vig": round(p_h_fair, 4) if p_h_fair is not None else None,
        "market_prob_away_no_vig": round(p_a_fair, 4) if p_a_fair is not None else None,
        "qb_expected_epa_lost_home": qb_expected_epa_lost_home,
        "qb_expected_epa_lost_away": qb_expected_epa_lost_away,
        "tier": ml_tier,
        "has_edge": ml_tier in ["CONFIANZA", "PREMIUM", "VALUE"],
        "best_bet": best_bet,
        "moneyline": {
            "status": "available" if model_valid and (bookmaker or home_bookmaker) else "unavailable",
            "pick_team": ml_pick_team,
            "pick_side": ml_side,
            "odds_american": ml_odds,
            "home_bookmaker": home_bookmaker or bookmaker,
            "away_bookmaker": away_bookmaker or bookmaker,
            "h2h_benchmark_bookmaker": bench_bm,
            "benchmark_bookmaker": bench_bm,
            "benchmark_observed_at": bench_obs_str,
            "bookmaker": (home_bookmaker if ml_side == "home" else away_bookmaker) or bookmaker,
            "prob_modelo": round(ml_prob, 4) if ml_prob is not None else None,
            "ev_dollars": round(ml_ev, 2) if ml_ev is not None else None,
            "kelly_stake": kelly_stake_ml,
            "has_kelly_edge": ml_ev is not None and ml_ev > 0 and kelly_frac > 0,
            "tier": ml_tier,
        },
        "spread": {
            "status": "available" if (spread_line is not None and not spread_odds_corrupt) else "unavailable",
            "market_line": spread_line,
            "odds_american": spread_odds_american,
            "home_spread_ml": home_spread_ml if not home_spread_explicit_invalid else None,
            "away_spread_ml": away_spread_ml if not away_spread_explicit_invalid else None,
            "home_bookmaker": spread_home_bookmaker or bookmaker,
            "away_bookmaker": spread_away_bookmaker or bookmaker,
            "bookmaker": (spread_home_bookmaker if spread_pick and home_team in spread_pick else (spread_away_bookmaker if spread_pick and away_team in spread_pick else (spread_home_bookmaker or bookmaker))),
            "market_prob_home_cover_no_vig": round(fair_h_spr, 4) if fair_h_spr is not None else None,
            "market_prob_away_cover_no_vig": round(fair_a_spr, 4) if fair_a_spr is not None else None,
            "market_vig": round(spr_vig, 4) if spr_vig is not None else None,
            "projected_margin": round(proj_margin, 1) if (proj_margin is not None and not spread_odds_corrupt) else None,
            "projected_home_score": round(proj_home_score, 1) if (proj_home_score is not None and not spread_odds_corrupt) else None,
            "projected_away_score": round(proj_away_score, 1) if (proj_away_score is not None and not spread_odds_corrupt) else None,
            "pick": spread_pick,
            "cover_probability": spread_cover_prob,
            "edge_pct": spread_edge,
            "ev_dollars": round(ev_spread, 2) if ev_spread is not None else None,
            "kelly_stake": spread_kelly_stake,
            "has_kelly_edge": spread_has_edge,
            "tier": spread_tier,
        },
        "totals": {
            "status": "available" if totals_is_available else "unavailable",
            "is_experimental": True,
            "market_line": total_line,
            "odds_american": totals_odds_american,
            "over_ml": over_ml if not over_explicit_invalid else None,
            "under_ml": under_ml if not under_explicit_invalid else None,
            "over_bookmaker": total_over_bookmaker or bookmaker,
            "under_bookmaker": total_under_bookmaker or bookmaker,
            "bookmaker": total_over_bookmaker if total_over_bookmaker == total_under_bookmaker else (f"{total_over_bookmaker}/{total_under_bookmaker}" if total_over_bookmaker and total_under_bookmaker else bookmaker),
            "market_prob_over_no_vig": round(fair_o, 4) if fair_o is not None else None,
            "market_prob_under_no_vig": round(fair_u, 4) if fair_u is not None else None,
            "market_vig": round(totals_vig, 4) if totals_vig is not None else None,
            "projected_total": round(proj_total, 1) if proj_total is not None else None,
            "pick": totals_pick,
            "recommendation": totals_recommendation,
            "probability": totals_prob,
            "prob_over": round(p_over, 4) if p_over is not None else None,
            "prob_under": round(p_under, 4) if p_under is not None else None,
            "edge_pct": totals_edge,
            "ev_dollars": round(ev_totals, 2) if ev_totals is not None else None,
            "kelly_stake": totals_kelly_stake,
            "has_kelly_edge": False,
            "tier": totals_tier,
            "residual_sigma": parsed_totals_sigma,
        },
        "player_props": enriched_props,
        "key_factors": factors,
    }
