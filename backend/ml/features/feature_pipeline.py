"""
Feature Engineering Pipeline para predicción de partidos NBA.
Transforma estadísticas brutas, métricas avanzadas y datos de calendario en
un vector normalizado de features para los modelos CatBoost, XGBoost y LogisticRegression.
"""
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

# Lista canónica y ordenada de features utilizadas para entrenamiento e inferencia
CANONICAL_FEATURES: List[str] = [
    # ── 1. Desempeño General & Registro ──────────────────────────────────────
    "H_WPCT",              # Porcentaje de victorias local
    "V_WPCT",              # Porcentaje de victorias visitante
    "DIFF_WPCT",          # Diferencial de W%
    "H_LOC_WPCT",          # W% del local jugando en casa
    "V_LOC_WPCT",          # W% del visitante jugando fuera
    "DIFF_LOC_WPCT",      # Ventaja contextual de localía

    # ── 2. Forma Reciente & Momentum ─────────────────────────────────────────
    "H_L10_WPCT",          # W% últimos 10 partidos local
    "V_L10_WPCT",          # W% últimos 10 partidos visitante
    "DIFF_L10_WPCT",      # Diferencial últimos 10
    "H_MOMENTUM",          # (L10_WPCT - WPCT): tendencia ascendente o descendente
    "V_MOMENTUM",
    "DIFF_MOMENTUM",

    # ── 3. Ratings Avanzados de Eficiencia ───────────────────────────────────
    "H_OFF_RTG",           # Puntos anotados por 100 posesiones
    "V_OFF_RTG",
    "DIFF_OFF_RTG",       # H_OFF - V_OFF
    "H_DEF_RTG",           # Puntos permitidos por 100 posesiones (menor es mejor)
    "V_DEF_RTG",
    "DIFF_DEF_RTG_INV",   # V_DEF - H_DEF (positivo = local defiende mejor)
    "H_NET_RTG",           # OFF_RTG - DEF_RTG
    "V_NET_RTG",
    "DIFF_NET_RTG",       # Diferencial neto

    # ── 4. Ritmo de Juego (Pace) ─────────────────────────────────────────────
    "H_PACE",              # Posesiones por 48 minutos
    "V_PACE",
    "DIFF_PACE",          # H_PACE - V_PACE
    "AVG_PACE",           # Ritmo proyectado del juego

    # ── 5. Interacciones Ofensiva vs Defensiva ────────────────────────────────
    "H_OFF_VS_V_DEF",     # H_OFF_RTG - V_DEF_RTG (ventaja anotadora de casa)
    "V_OFF_VS_H_DEF",     # V_OFF_RTG - H_DEF_RTG (ventaja anotadora de visita)
    "NET_MATCHUP_EDGE",   # H_OFF_VS_V_DEF - V_OFF_VS_H_DEF

    # ── 6. Fatiga, Calendario & Altitud ──────────────────────────────────────
    "H_DAYS_REST",         # Días de descanso (1 a 7+)
    "V_DAYS_REST",
    "DIFF_REST_DAYS",     # H_REST - V_REST
    "H_IS_B2B",           # 1 si juega en noches consecutivas (Back-to-Back)
    "V_IS_B2B",
    "B2B_DISADVANTAGE",   # 1 si Visitante en B2B y Local descansado; -1 si inverso
    "ALTITUDE_ADVANTAGE", # 1 si Local es Denver (Nuggets) o Utah (Jazz)
]

# Equipos con ventaja geográfica por altitud sobre el nivel del mar (>4,000 pies)
ALTITUDE_TEAMS = {"DEN", "UTA", "1610612743", "1610612762"}


def compute_derived_features(row: Dict[str, Any]) -> Dict[str, float]:
    """
    Calcula todas las métricas derivadas a partir de estadísticas base
    para una fila o partido en tiempo real.
    """
    f: Dict[str, float] = {}

    def safe_float(val: Any, default: float = 0.0) -> float:
        if val is None or pd.isna(val):
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # 1. Registro
    h_wpct = safe_float(row.get("H_WPCT"), 0.5)
    v_wpct = safe_float(row.get("V_WPCT"), 0.5)
    f["H_WPCT"] = h_wpct
    f["V_WPCT"] = v_wpct
    f["DIFF_WPCT"] = h_wpct - v_wpct

    h_loc_wpct = safe_float(row.get("H_LOC_WPCT"), h_wpct)
    v_loc_wpct = safe_float(row.get("V_LOC_WPCT"), v_wpct)
    f["H_LOC_WPCT"] = h_loc_wpct
    f["V_LOC_WPCT"] = v_loc_wpct
    f["DIFF_LOC_WPCT"] = h_loc_wpct - v_loc_wpct

    # 2. Forma & Momentum
    h_l10 = safe_float(row.get("H_L10_WPCT"), h_wpct)
    v_l10 = safe_float(row.get("V_L10_WPCT"), v_wpct)
    f["H_L10_WPCT"] = h_l10
    f["V_L10_WPCT"] = v_l10
    f["DIFF_L10_WPCT"] = h_l10 - v_l10

    h_mom = h_l10 - h_wpct
    v_mom = v_l10 - v_wpct
    f["H_MOMENTUM"] = h_mom
    f["V_MOMENTUM"] = v_mom
    f["DIFF_MOMENTUM"] = h_mom - v_mom

    # 3. Ratings
    h_off = safe_float(row.get("H_OFF_RTG"), 112.0)
    v_off = safe_float(row.get("V_OFF_RTG"), 112.0)
    f["H_OFF_RTG"] = h_off
    f["V_OFF_RTG"] = v_off
    f["DIFF_OFF_RTG"] = h_off - v_off

    h_def = safe_float(row.get("H_DEF_RTG"), 112.0)
    v_def = safe_float(row.get("V_DEF_RTG"), 112.0)
    f["H_DEF_RTG"] = h_def
    f["V_DEF_RTG"] = v_def
    f["DIFF_DEF_RTG_INV"] = v_def - h_def  # Mayor es mejor para local

    h_net = safe_float(row.get("H_NET_RTG"), h_off - h_def)
    v_net = safe_float(row.get("V_NET_RTG"), v_off - v_def)
    f["H_NET_RTG"] = h_net
    f["V_NET_RTG"] = v_net
    f["DIFF_NET_RTG"] = h_net - v_net

    # 4. Pace
    h_pace = safe_float(row.get("H_PACE"), 99.0)
    v_pace = safe_float(row.get("V_PACE"), 99.0)
    f["H_PACE"] = h_pace
    f["V_PACE"] = v_pace
    f["DIFF_PACE"] = h_pace - v_pace
    f["AVG_PACE"] = (h_pace + v_pace) / 2.0

    # 5. Interacciones de Matchup
    h_vs_v = h_off - v_def
    v_vs_h = v_off - h_def
    f["H_OFF_VS_V_DEF"] = h_vs_v
    f["V_OFF_VS_H_DEF"] = v_vs_h
    f["NET_MATCHUP_EDGE"] = h_vs_v - v_vs_h

    # 6. Fatiga, Calendario & Altitud
    h_rest = safe_float(row.get("H_DAYS_REST"), 2.0)
    v_rest = safe_float(row.get("V_DAYS_REST"), 2.0)
    f["H_DAYS_REST"] = h_rest
    f["V_DAYS_REST"] = v_rest
    f["DIFF_REST_DAYS"] = h_rest - v_rest

    h_b2b = 1.0 if h_rest <= 1.0 or row.get("H_IS_B2B") in (1, True, "1") else 0.0
    v_b2b = 1.0 if v_rest <= 1.0 or row.get("V_IS_B2B") in (1, True, "1") else 0.0
    f["H_IS_B2B"] = h_b2b
    f["V_IS_B2B"] = v_b2b

    # Desventaja asimétrica de B2B (local descansado vs visitante en 2da noche)
    if v_b2b == 1.0 and h_b2b == 0.0:
        f["B2B_DISADVANTAGE"] = 1.0
    elif h_b2b == 1.0 and v_b2b == 0.0:
        f["B2B_DISADVANTAGE"] = -1.0
    else:
        f["B2B_DISADVANTAGE"] = 0.0

    # Altitud (Denver y Utah tienen ventaja fisiológica comprobada)
    home_abbr = str(row.get("HOME_TEAM_ABBR", "")).upper()
    home_id = str(row.get("HOME_TEAM_ID", ""))
    f["ALTITUDE_ADVANTAGE"] = 1.0 if (home_abbr in ALTITUDE_TEAMS or home_id in ALTITUDE_TEAMS) else 0.0

    return f


def build_feature_dataframe(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Recibe un DataFrame con columnas base históricas y calcula
    la matriz de features completa según CANONICAL_FEATURES.
    """
    records = []
    for _, row in df_raw.iterrows():
        feat_dict = compute_derived_features(row.to_dict())
        records.append(feat_dict)

    df_feats = pd.DataFrame(records)
    # Asegurar el orden canónico exacto
    return df_feats[CANONICAL_FEATURES]
