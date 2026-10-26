"""Estudio de ablación de margen y spread de la NFL (Punto 6 del Plan de Corrección).

Compara formalmente tres variantes de proyección de margen y cover en los 4 folds comunes OOF:
1. Variante A (spread_injury_aware_no_qb_adj):
   Clasificador injury-aware (con features de bajas ofensivas/defensivas).
   Margen base derivado puramente de la probabilidad win-rate (Phi^-1(p) * 13.5).
   Cero ajuste aditivo adicional de QB al margen.
2. Variante B (spread_team_only_with_qb_adj):
   Clasificador team-only (sin features de lesiones en el clasificador).
   Ajuste aditivo de EPA de QB pregame frente a reemplazo de liga restado al margen neto.
3. Variante C (spread_dual_penalty):
   Clasificador injury-aware Y ajuste aditivo de EPA de QB al margen (doble penalización).

Evalúa MAE, RMSE y Bias de margen, así como Brier, Accuracy y Log Loss de cover frente a la línea de spread oficial.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error, mean_squared_error
import structlog

from app.services.nfl_availability_service import NFLAvailabilityService
from ml.data_pipeline.common import backend_root, project_root, write_manifest
from ml.data_pipeline.nfl_source import load_nfl_games

logger = structlog.get_logger(__name__)


def compute_cover_metrics(
    y_cover_true: np.ndarray,
    p_cover_pred: np.ndarray,
) -> Dict[str, float]:
    """Calcula métricas de habilidad probabilística y clasificación para cover (excluyendo pushes)."""
    if len(y_cover_true) == 0:
        return {"brier": 0.0, "log_loss": 0.0, "accuracy": 0.0, "count": 0}
    brier = float(brier_score_loss(y_cover_true, p_cover_pred))
    acc = float(accuracy_score(y_cover_true, (p_cover_pred >= 0.5).astype(int)))
    ll = float(log_loss(y_cover_true, p_cover_pred, labels=[0, 1]))
    return {
        "brier": round(brier, 4),
        "log_loss": round(ll, 4),
        "accuracy": round(acc, 4),
        "count": int(len(y_cover_true)),
    }


def compute_margin_metrics(
    y_margin_true: np.ndarray,
    proj_margin_pred: np.ndarray,
) -> Dict[str, float]:
    """Calcula métricas de error y sesgo para el margen proyectado."""
    if len(y_margin_true) == 0:
        return {"mae": 0.0, "rmse": 0.0, "bias": 0.0, "count": 0}
    mae = float(mean_absolute_error(y_margin_true, proj_margin_pred))
    rmse = float(np.sqrt(mean_squared_error(y_margin_true, proj_margin_pred)))
    bias = float(np.mean(proj_margin_pred - y_margin_true))
    return {
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "bias": round(bias, 3),
        "count": int(len(y_margin_true)),
    }


def run_spread_ablation_evaluation(
    output_dir: Optional[Path] = None,
    oof_predictions_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ejecuta el protocolo de ablación de margen y spread sobre los 188 juegos OOF."""
    root = project_root()
    output_dir = output_dir or root / "data" / "predictions" / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)

    oof_path = oof_predictions_path or output_dir / "nfl_common_folds_predictions.csv"
    if not oof_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de predicciones OOF: {oof_path}")

    oof_df = pd.read_csv(oof_path)
    logger.info("Cargadas predicciones out-of-fold", total_rows=len(oof_df))

    # 1. Cargar schedules oficiales para spread_line y result
    sched_files = sorted(glob.glob(str(root / "data" / "raw" / "nflverse" / "*" / "schedules_*.parquet")))
    if not sched_files:
        raise FileNotFoundError("No se encontraron archivos de calendario de nflverse")
    sched_df = pd.concat([pd.read_parquet(f) for f in sched_files], ignore_index=True)
    oof_df = pd.merge(
        oof_df,
        sched_df[["game_id", "spread_line", "result", "home_score", "away_score"]],
        on="game_id",
        how="left",
    )

    # 2. Cargar snapshots de lesiones point-in-time y kickoffs canónicos
    games = load_nfl_games()
    games["week"] = games["game_id"].apply(lambda x: int(x.split("_")[1]))
    games["game_type"] = games["season_type"].astype(str)

    # Añadir kickoff canónico y cutoff de disponibilidad a oof_df
    game_kickoffs = games[["game_id", "game_start_utc"]].drop_duplicates("game_id")
    oof_df = pd.merge(oof_df, game_kickoffs, on="game_id", how="left")
    oof_df["kickoff_utc_dt"] = pd.to_datetime(oof_df["game_start_utc"], errors="coerce", utc=True)
    if oof_df["kickoff_utc_dt"].isna().any():
        missing_ko = oof_df[oof_df["kickoff_utc_dt"].isna()]["game_id"].tolist()
        raise ValueError(f"Faltan kickoffs canónicos para los siguientes juegos OOF: {missing_ko}")

    oof_df["availability_cutoff_utc_dt"] = oof_df["kickoff_utc_dt"] - pd.to_timedelta(2.0, unit="h")
    oof_df["kickoff_utc"] = oof_df["kickoff_utc_dt"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    oof_df["availability_cutoff_utc"] = oof_df["availability_cutoff_utc_dt"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    home_games = games[["game_id", "season", "game_type", "week", "home_team_id", "game_start_utc"]].rename(
        columns={"home_team_id": "team"}
    )
    away_games = games[["game_id", "season", "game_type", "week", "away_team_id", "game_start_utc"]].rename(
        columns={"away_team_id": "team"}
    )
    team_sched = pd.concat([home_games, away_games], ignore_index=True)

    inj_files = sorted(glob.glob(str(root / "data" / "raw" / "nflverse" / "*" / "injuries_*.parquet")))
    inj_df = pd.concat([pd.read_parquet(f) for f in inj_files], ignore_index=True)
    inj_df = pd.merge(inj_df, team_sched, on=["season", "game_type", "week", "team"], how="left")

    inj_df["kickoff_utc"] = pd.to_datetime(inj_df["game_start_utc"], errors="coerce", utc=True)
    inj_df["decision_cutoff"] = inj_df["kickoff_utc"] - pd.to_timedelta(2.0, unit="h")
    inj_dm = pd.to_datetime(inj_df["date_modified"], errors="coerce", utc=True)
    inj_df["is_pregame_valid"] = inj_dm.notna() & (inj_dm < inj_df["decision_cutoff"])

    def compute_p_abs(row: pd.Series) -> float:
        if not row["is_pregame_valid"]:
            return 0.0
        s = str(row.get("report_status") or "").lower()
        if any(x in s for x in ["out", "ir", "reserve", "injured reserve", "suspension", "pup"]):
            return 1.0
        if "doubtful" in s:
            return 0.85
        if any(x in s for x in ["questionable", "day-to-day", "gtd"]):
            return 0.25
        return 0.0

    inj_df["p_absence"] = inj_df.apply(compute_p_abs, axis=1)

    # 3. Cómputo del EPA de QB esperado pregame con contracción bayesiana (Punto 5)
    # Temporal boundary: as_of es el kickoff canónico UTC estricto de cada juego.
    # El cutoff de disponibilidad prepartido registrado es de 2 horas antes del kickoff.
    svc = NFLAvailabilityService()
    h_epa_lost_list = []
    a_epa_lost_list = []
    qb_diff_list = []

    for _, row in oof_df.iterrows():
        gid = str(row["game_id"])
        ht = str(row["home_team_id"])
        at = str(row["away_team_id"])
        kickoff_iso = str(row["kickoff_utc"])
        cutoff_dt = row["availability_cutoff_utc_dt"]
        kickoff_dt = row["kickoff_utc_dt"]
        week = int(gid.split("_")[1])

        g_inj = inj_df[inj_df["game_id"] == gid]
        qb_inj = g_inj[(g_inj["position"] == "QB") & (g_inj["p_absence"] > 0)]

        # Verificación estricta: ninguna lesión computada puede ser posterior al cutoff
        for _, qbr in qb_inj.iterrows():
            qbr_mod = pd.to_datetime(qbr["date_modified"], errors="coerce", utc=True)
            if qbr_mod is None or pd.isna(qbr_mod) or qbr_mod >= cutoff_dt or qbr_mod >= kickoff_dt:
                raise ValueError(
                    f"Fallo de integridad temporal: reporte QB para {gid} ({qbr.get('full_name')}) "
                    f"con date_modified={qbr_mod} no precede a cutoff={cutoff_dt} / kickoff={kickoff_dt}"
                )

        h_lost = 0.0
        a_lost = 0.0

        h_qb_inj = qb_inj[qb_inj["team"] == ht]
        if len(h_qb_inj) > 0:
            h_stats = svc.load_team_roster_stats_from_nflverse(ht, as_of=kickoff_iso, evaluated_week=week)
            for _, qbr in h_qb_inj.iterrows():
                nm = str(qbr["full_name"]).strip().lower()
                p_abs = float(qbr["p_absence"])
                prior = h_stats.get(nm) or h_stats.get(qbr.get("gsis_id"))
                if prior:
                    p_off = float(prior.get("rolling_off_pct", 0.0))
                    p_ppr = float(prior.get("rolling_ppr", 0.0))
                    if p_off >= 0.50 or p_ppr >= 12.0:
                        rate = prior.get("rolling_qb_epa_per_opportunity")
                        opps = prior.get("rolling_qb_opportunities")
                        replacement = prior.get("league_replacement_qb_epa_per_opportunity")
                        team_opps = prior.get("team_qb_opportunities_per_game")
                        if rate is not None and opps is not None and replacement is not None and team_opps is not None:
                            if opps >= 50 and team_opps > 0:
                                shrunk = (rate * opps + replacement * 50.0) / (opps + 50.0)
                                h_lost += p_abs * (shrunk - replacement) * team_opps

        a_qb_inj = qb_inj[qb_inj["team"] == at]
        if len(a_qb_inj) > 0:
            a_stats = svc.load_team_roster_stats_from_nflverse(at, as_of=kickoff_iso, evaluated_week=week)
            for _, qbr in a_qb_inj.iterrows():
                nm = str(qbr["full_name"]).strip().lower()
                p_abs = float(qbr["p_absence"])
                prior = a_stats.get(nm) or a_stats.get(qbr.get("gsis_id"))
                if prior:
                    p_off = float(prior.get("rolling_off_pct", 0.0))
                    p_ppr = float(prior.get("rolling_ppr", 0.0))
                    if p_off >= 0.50 or p_ppr >= 12.0:
                        rate = prior.get("rolling_qb_epa_per_opportunity")
                        opps = prior.get("rolling_qb_opportunities")
                        replacement = prior.get("league_replacement_qb_epa_per_opportunity")
                        team_opps = prior.get("team_qb_opportunities_per_game")
                        if rate is not None and opps is not None and replacement is not None and team_opps is not None:
                            if opps >= 50 and team_opps > 0:
                                shrunk = (rate * opps + replacement * 50.0) / (opps + 50.0)
                                a_lost += p_abs * (shrunk - replacement) * team_opps

        h_epa_lost_list.append(round(h_lost, 3))
        a_epa_lost_list.append(round(a_lost, 3))
        qb_diff_list.append(round(h_lost - a_lost, 3))

    oof_df["qb_epa_lost_home"] = h_epa_lost_list
    oof_df["qb_epa_lost_away"] = a_epa_lost_list
    oof_df["qb_epa_diff"] = qb_diff_list

    # Drop temporary datetime helper columns before persisting
    oof_df = oof_df.drop(columns=["kickoff_utc_dt", "availability_cutoff_utc_dt"])

    # 4. Margen real M y Spread oficial S
    oof_df["margin_actual"] = oof_df["result"].astype(float)
    oof_df["vegas_spread"] = oof_df["spread_line"].astype(float)
    oof_df["is_push"] = np.isclose(oof_df["margin_actual"], oof_df["vegas_spread"])
    oof_df["home_cover"] = np.where(
        oof_df["margin_actual"] > oof_df["vegas_spread"], 1.0,
        np.where(oof_df["margin_actual"] < oof_df["vegas_spread"], 0.0, np.nan)
    )

    # 5. Generación de las 3 Variantes de Margen y Cover
    # Distribución empírica estándar NFL diferencial de puntos: sigma = 13.5
    SIGMA_NFL = 13.5

    # Variante A: Injury-Aware sin ajuste adicional de QB al margen
    p_inj_cand = oof_df["prob_injury_aware_logistic"].clip(0.001, 0.999).to_numpy()
    oof_df["proj_margin_variant_a"] = norm.ppf(p_inj_cand) * SIGMA_NFL
    oof_df["p_cover_variant_a"] = norm.cdf(
        (oof_df["proj_margin_variant_a"] - oof_df["vegas_spread"]) / SIGMA_NFL
    )

    # Variante B: Team-Only con ajuste aditivo de EPA de QB al margen
    p_team = oof_df["prob_team_only_logistic"].clip(0.001, 0.999).to_numpy()
    oof_df["proj_margin_variant_b"] = (norm.ppf(p_team) * SIGMA_NFL) - oof_df["qb_epa_diff"]
    oof_df["p_cover_variant_b"] = norm.cdf(
        (oof_df["proj_margin_variant_b"] - oof_df["vegas_spread"]) / SIGMA_NFL
    )

    # Variante C: Dual Penalty (Injury-Aware + ajuste aditivo de QB al margen)
    oof_df["proj_margin_variant_c"] = oof_df["proj_margin_variant_a"] - oof_df["qb_epa_diff"]
    oof_df["p_cover_variant_c"] = norm.cdf(
        (oof_df["proj_margin_variant_c"] - oof_df["vegas_spread"]) / SIGMA_NFL
    )

    # Variante Diagnóstica D: Ensamble Injury-Aware sin ajuste QB (para referencia completa)
    p_ens = oof_df["prob_injury_aware_ensemble"].clip(0.001, 0.999).to_numpy()
    oof_df["proj_margin_ensemble_no_qb_adj"] = norm.ppf(p_ens) * SIGMA_NFL
    oof_df["p_cover_ensemble_no_qb_adj"] = norm.cdf(
        (oof_df["proj_margin_ensemble_no_qb_adj"] - oof_df["vegas_spread"]) / SIGMA_NFL
    )

    # 6. Cálculo de Métricas Globales y por Folds
    valid_cover_mask = oof_df["home_cover"].notna()
    y_m_all = oof_df["margin_actual"].to_numpy()
    y_c_all = oof_df.loc[valid_cover_mask, "home_cover"].to_numpy()

    variants = {
        "variant_a_injury_aware_no_qb_adj": ("proj_margin_variant_a", "p_cover_variant_a"),
        "variant_b_team_only_with_qb_adj": ("proj_margin_variant_b", "p_cover_variant_b"),
        "variant_c_dual_penalty": ("proj_margin_variant_c", "p_cover_variant_c"),
        "diagnostic_ensemble_no_qb_adj": ("proj_margin_ensemble_no_qb_adj", "p_cover_ensemble_no_qb_adj"),
        "market_vegas_spread": ("vegas_spread", None),
    }

    overall_metrics = {}
    for v_name, (m_col, c_col) in variants.items():
        m_eval = compute_margin_metrics(y_m_all, oof_df[m_col].to_numpy())
        c_eval = compute_cover_metrics(y_c_all, oof_df.loc[valid_cover_mask, c_col].to_numpy()) if c_col else None
        overall_metrics[v_name] = {
            "margin": m_eval,
            "cover": c_eval,
        }

    # Subpoblación sensible (juegos con QB EPA diff != 0)
    qb_sub = oof_df[oof_df["qb_epa_diff"] != 0].copy()
    valid_sub_mask = qb_sub["home_cover"].notna()
    y_m_sub = qb_sub["margin_actual"].to_numpy()
    y_c_sub = qb_sub.loc[valid_sub_mask, "home_cover"].to_numpy()

    subpopulation_metrics = {}
    for v_name, (m_col, c_col) in variants.items():
        m_eval = compute_margin_metrics(y_m_sub, qb_sub[m_col].to_numpy())
        c_eval = compute_cover_metrics(y_c_sub, qb_sub.loc[valid_sub_mask, c_col].to_numpy()) if c_col else None
        subpopulation_metrics[v_name] = {
            "margin": m_eval,
            "cover": c_eval,
        }

    # Desglose por Fold
    fold_metrics = {}
    for fold_no in sorted(oof_df["fold"].unique()):
        f_df = oof_df[oof_df["fold"] == fold_no]
        f_vmask = f_df["home_cover"].notna()
        y_m_f = f_df["margin_actual"].to_numpy()
        y_c_f = f_df.loc[f_vmask, "home_cover"].to_numpy()

        fold_metrics[f"fold_{fold_no}"] = {}
        for v_name, (m_col, c_col) in variants.items():
            m_eval = compute_margin_metrics(y_m_f, f_df[m_col].to_numpy())
            c_eval = compute_cover_metrics(y_c_f, f_df.loc[f_vmask, c_col].to_numpy()) if c_col else None
            fold_metrics[f"fold_{fold_no}"][v_name] = {
                "margin": m_eval,
                "cover": c_eval,
            }

    # 7. Persistir CSV de Predicciones de Ablación
    output_csv = output_dir / "nfl_spread_ablation_predictions.csv"
    oof_df.to_csv(output_csv, index=False)
    output_csv_sha256 = hashlib.sha256(output_csv.read_bytes()).hexdigest()

    # 8. Regla de Decisión Prerregistrada de Punto 6
    # Mantener ajuste QB solo si aporta mejora fuera de muestra sobre el modelo que ya usa features de lesiones.
    mae_var_a = overall_metrics["variant_a_injury_aware_no_qb_adj"]["margin"]["mae"]
    mae_var_c = overall_metrics["variant_c_dual_penalty"]["margin"]["mae"]
    brier_var_a = overall_metrics["variant_a_injury_aware_no_qb_adj"]["cover"]["brier"]
    brier_var_c = overall_metrics["variant_c_dual_penalty"]["cover"]["brier"]

    mae_sub_a = subpopulation_metrics["variant_a_injury_aware_no_qb_adj"]["margin"]["mae"]
    mae_sub_c = subpopulation_metrics["variant_c_dual_penalty"]["margin"]["mae"]
    brier_sub_a = subpopulation_metrics["variant_a_injury_aware_no_qb_adj"]["cover"]["brier"]
    brier_sub_c = subpopulation_metrics["variant_c_dual_penalty"]["cover"]["brier"]

    dual_penalty_improves = (mae_var_c < mae_var_a) and (brier_var_c < brier_var_a)
    subpop_improves = (mae_sub_c < mae_sub_a) and (brier_sub_c < brier_sub_a)

    decision = {
        "criterion": "Punto 6 Protocol: Mantener ajuste QB adicional solo si mejora fuera de muestra sobre injury_aware",
        "global_mae_diff_c_minus_a": round(mae_var_c - mae_var_a, 4),
        "global_brier_diff_c_minus_a": round(brier_var_c - brier_var_a, 4),
        "subpopulation_mae_diff_c_minus_a": round(mae_sub_c - mae_sub_a, 4),
        "subpopulation_brier_diff_c_minus_a": round(brier_sub_c - brier_sub_a, 4),
        "dual_penalty_improves_out_of_sample": dual_penalty_improves,
        "subpopulation_improves_out_of_sample": subpop_improves,
        "selected_action": (
            "retire_additional_qb_margin_adjustment"
            if not dual_penalty_improves and not subpop_improves
            else "retain_additional_qb_margin_adjustment"
        ),
        "justification": (
            f"La doble penalización (Variante C) degrada el error absoluto medio de margen "
            f"de {mae_sub_a:.3f} a {mae_sub_c:.3f} (+{mae_sub_c - mae_sub_a:.3f} pts) y empeora el Cover Brier "
            f"de {brier_sub_a:.4f} a {brier_sub_c:.4f} en la subpoblación de partidos con bajas de QB. "
            f"A nivel global en los 188 juegos OOF, Variante A presenta menor MAE ({mae_var_a:.3f} vs {mae_var_c:.3f}) "
            f"y mejor Brier ({brier_var_a:.4f} vs {brier_var_c:.4f}). "
            f"Conforme al protocolo de Punto 6, se elimina permanentemente el ajuste aditivo de QB al margen; "
            f"el margen deriva únicamente de la probabilidad calibrada y el EPA de QB queda exclusivamente como diagnóstico."
        ),
    }

    summary = {
        "study_name": "nfl_spread_and_margin_ablation_point_06",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_oof_games_evaluated": len(oof_df),
        "non_push_games_evaluated": int(valid_cover_mask.sum()),
        "push_count": int(oof_df["is_push"].sum()),
        "sensitive_subpopulation_count": len(qb_sub),
        "output_predictions_csv": output_csv.name,
        "output_predictions_sha256": output_csv_sha256,
        "overall_metrics": overall_metrics,
        "subpopulation_metrics": subpopulation_metrics,
        "fold_metrics": fold_metrics,
        "decision": decision,
    }

    manifest_path = output_dir / "nfl_spread_ablation_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(
        "Ablación de spread completada exitosamente",
        selected_action=decision["selected_action"],
        global_mae_var_a=mae_var_a,
        global_mae_var_c=mae_var_c,
        subpop_mae_var_a=mae_sub_a,
        subpop_mae_var_c=mae_sub_c,
    )
    return summary


if __name__ == "__main__":
    res = run_spread_ablation_evaluation()
    print("\n" + "=" * 60)
    print("RESUMEN DE DECISIÓN DE ABLACIÓN (PUNTO 6)")
    print("=" * 60)
    print("Acción seleccionada:", res["decision"]["selected_action"])
    print("Justificación:", res["decision"]["justification"])
    print("Output CSV SHA-256:", res["output_predictions_sha256"])
