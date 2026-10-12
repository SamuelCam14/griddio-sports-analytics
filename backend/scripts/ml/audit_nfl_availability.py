"""
Script de Auditoría de Disponibilidad e Injuries Point-in-Time para la NFL (Fase 02).
Audita exhaustivamente los snapshots locales de nflverse (2020-2024):
- Estados de reporte (OUT, Doubtful, Questionable, None).
- Verificación temporal de pregame con decision cutoff exacto (kickoff - 2 horas).
- Identidad y pertenencia al roster semanal.
- Tasa de participación observada en el boxscore (Ground Truth did_play por game_id, team, player_id).
- Diferenciación explícita entre no participación (0 snaps) y falta de registro en snaps (None).
- Desglose y tasas de 'unknown' por motivo específico.
- Evaluación probabilística descriptiva empírica (NO es un modelo ML calibrado; claims de calibración retirados).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import structlog

from ml.data_pipeline.common import project_root
from ml.data_pipeline.nfl_source import (
    load_nfl_games,
    load_nfl_player_crosswalk,
    load_nfl_snapshot,
)

logger = structlog.get_logger(__name__)


def run_availability_audit(
    seasons: List[int] = [2020, 2021, 2022, 2023, 2024],
    cutoff_hours: float = 2.0,
) -> Dict[str, Any]:
    logger.info("Iniciando auditoría exhaustiva de disponibilidad NFL...", seasons=seasons, cutoff_hours=cutoff_hours)

    # 1. Cargar y concatenar snapshots
    injuries_list = []
    rosters_list = []
    snaps_list = []
    stats_list = []

    for s in seasons:
        injuries_list.append(load_nfl_snapshot("injuries", s).to_pandas())
        rosters_list.append(load_nfl_snapshot("rosters_weekly", s).to_pandas())
        snaps_list.append(load_nfl_snapshot("snap_counts", s).to_pandas())
        stats_list.append(load_nfl_snapshot("player_stats", s).to_pandas())

    injuries = pd.concat(injuries_list, ignore_index=True)
    rosters = pd.concat(rosters_list, ignore_index=True)
    snaps = pd.concat(snaps_list, ignore_index=True)
    stats = pd.concat(stats_list, ignore_index=True)
    players = load_nfl_player_crosswalk().to_pandas()

    # Mapeo crosswalk para snaps
    cw = players[["pfr_id", "gsis_id"]].dropna().drop_duplicates(subset=["pfr_id"])
    snaps = pd.merge(snaps, cw, left_on="pfr_player_id", right_on="pfr_id", how="left")
    snaps.rename(columns={"gsis_id": "player_id"}, inplace=True)
    injuries.rename(columns={"gsis_id": "player_id"}, inplace=True)
    rosters.rename(columns={"gsis_id": "player_id"}, inplace=True)

    # 2. Cargar schedule oficial y kickoffs oficiales UTC con cutoff de 2 horas
    games = load_nfl_games()
    games_sub = games[games["season"].isin(seasons)].copy()
    games_sub["week"] = games_sub["game_id"].apply(lambda x: int(x.split("_")[1]))
    games_sub["game_type"] = games_sub["season_type"].astype(str)

    home_games = games_sub[["game_id", "season", "game_type", "week", "home_team_id", "game_start_utc"]].rename(
        columns={"home_team_id": "team"}
    )
    away_games = games_sub[["game_id", "season", "game_type", "week", "away_team_id", "game_start_utc"]].rename(
        columns={"away_team_id": "team"}
    )
    team_schedule = pd.concat([home_games, away_games], ignore_index=True)
    schedule_key = ["season", "game_type", "week", "team"]
    if team_schedule.duplicated(subset=schedule_key).any():
        raise ValueError("Schedule NFL ambiguo por season/game_type/week/team; auditoría detenida")
    team_schedule["kickoff_utc"] = pd.to_datetime(team_schedule["game_start_utc"], errors="coerce", utc=True)
    if team_schedule["kickoff_utc"].isna().any():
        raise ValueError("Schedule NFL contiene kickoff UTC ausente/no verificable")
    team_schedule["decision_cutoff"] = team_schedule["kickoff_utc"] - pd.to_timedelta(cutoff_hours, unit="h")

    injuries = pd.merge(
        injuries,
        team_schedule[["season", "game_type", "week", "team", "game_id", "kickoff_utc", "decision_cutoff"]],
        on=["season", "game_type", "week", "team"],
        how="left",
    )

    total_records = len(injuries)

    # 3. Distribución de Estados de Reporte
    status_counts = injuries["report_status"].value_counts(dropna=False).to_dict()
    status_dist = {}
    for k, v in status_counts.items():
        label = "None" if pd.isna(k) else str(k)
        status_dist[label] = {
            "count": int(v),
            "percentage": round(float(v / total_records) * 100, 2),
        }

    # 4. Auditoría Temporal estricta (date_modified vs decision_cutoff y kickoff)
    inj_dm = pd.to_datetime(injuries["date_modified"], errors="coerce", utc=True)
    cutoff_utc = injuries["decision_cutoff"]
    game_ko = injuries["kickoff_utc"]

    has_dm = inj_dm.notna()
    pre_cutoff = has_dm & cutoff_utc.notna() & (inj_dm < cutoff_utc)
    in_cutoff_window = has_dm & cutoff_utc.notna() & game_ko.notna() & (inj_dm >= cutoff_utc) & (inj_dm < game_ko)
    post_kickoff = has_dm & game_ko.notna() & (inj_dm >= game_ko)
    missing_dm = inj_dm.isna()

    timing_audit = {
        "total_records": total_records,
        "cutoff_hours_before_kickoff": cutoff_hours,
        "pre_cutoff_verified": int(pre_cutoff.sum()),
        "pre_cutoff_pct": round(float(pre_cutoff.sum() / total_records) * 100, 2),
        "in_cutoff_window_excluded": int(in_cutoff_window.sum()),
        "in_cutoff_window_pct": round(float(in_cutoff_window.sum() / total_records) * 100, 4),
        "post_kickoff_excluded": int(post_kickoff.sum()),
        "post_kickoff_pct": round(float(post_kickoff.sum() / total_records) * 100, 4),
        "missing_date_modified": int(missing_dm.sum()),
        "missing_date_modified_pct": round(float(missing_dm.sum() / total_records) * 100, 2),
    }

    # 5. Auditoría de Roster Semanal
    roster_key_columns = ["season", "game_type", "week", "team", "player_id"]
    rostered_rows = rosters.dropna(subset=["player_id"])
    roster_keys = set(map(tuple, rostered_rows[roster_key_columns].itertuples(index=False, name=None)))
    roster_status_map = {}
    for key, rows in rostered_rows.groupby(roster_key_columns, dropna=False):
        statuses = rows["status"].dropna().astype(str).unique()
        if len(statuses) > 1:
            raise ValueError(f"Roster status NFL ambiguo para {key}: {statuses.tolist()}")
        roster_status_map[key] = statuses[0] if len(statuses) else None
    is_rostered = [
        (r["season"], r["game_type"], r["week"], r["team"], r["player_id"]) in roster_keys
        for r in injuries.to_dict("records")
    ]
    rostered_count = sum(is_rostered)
    roster_audit = {
        "rostered_count": int(rostered_count),
        "rostered_pct": round(float(rostered_count / total_records) * 100, 2),
        "unrostered_count": int(total_records - rostered_count),
        "unrostered_pct": round(float((total_records - rostered_count) / total_records) * 100, 2),
    }

    # 6. Participación Ground Truth vinculada por (game_id, team, player_id)
    snaps["total_snaps"] = (
        snaps["offense_snaps"].fillna(0)
        + snaps["defense_snaps"].fillna(0)
        + snaps["st_snaps"].fillna(0)
    )
    snaps_map = snaps.set_index(["game_id", "team", "player_id"])["total_snaps"].to_dict()

    did_play_list: List[Any] = []
    label_status_list: List[str] = []

    for r in injuries.to_dict("records"):
        g_id = r.get("game_id")
        t = r.get("team")
        p_id = r.get("player_id")

        roster_key = (r.get("season"), r.get("game_type"), r.get("week"), t, p_id)
        if not p_id or roster_key not in roster_keys:
            did_play_list.append(None)
            label_status_list.append("missing_snap_record")
        elif (g_id, t, p_id) in snaps_map:
            tot = snaps_map[(g_id, t, p_id)]
            if roster_status_map.get(roster_key) == "INA" and tot > 0:
                raise ValueError(f"Conflicto de ground truth: roster INA con snaps positivos para {roster_key}")
            did_play_list.append(bool(tot > 0))
            label_status_list.append("observed")
        elif roster_status_map.get(roster_key) == "INA":
            # INA es una designación explícita de inactivo de partido en el roster semanal.
            # No se generaliza esta regla a ACT/RES ni a una mera ausencia de fila en snaps.
            did_play_list.append(False)
            label_status_list.append("observed_inactive_roster")
        else:
            did_play_list.append(None)
            label_status_list.append("missing_snap_record")

    injuries["did_play"] = did_play_list
    injuries["label_status"] = label_status_list
    injuries["pre_cutoff_valid"] = pre_cutoff

    # Ground truth evaluado sobre observaciones con corte pregame verificado y etiqueta observada
    observed_labels = injuries["label_status"].isin(["observed", "observed_inactive_roster"])
    valid_observed = injuries[injuries["pre_cutoff_valid"] & observed_labels].copy()

    ground_truth_by_status = {}
    for status_val in ["Out", "Doubtful", "Questionable", None]:
        if status_val is None:
            sub = valid_observed[valid_observed["report_status"].isna()]
            key_name = "None"
        else:
            sub = valid_observed[valid_observed["report_status"] == status_val]
            key_name = status_val

        n_sub = len(sub)
        n_played = int(sub["did_play"].sum()) if n_sub > 0 else 0
        play_rate = round(float(n_played / n_sub), 4) if n_sub > 0 else None

        if key_name == "Out":
            p_play_baseline = 0.0
        elif key_name == "Doubtful":
            p_play_baseline = 0.15
        elif key_name == "Questionable":
            p_play_baseline = 0.75
        else:
            p_play_baseline = 1.0

        brier = round(float(np.mean((sub["did_play"].astype(int) - p_play_baseline) ** 2)), 4) if n_sub > 0 else None

        ground_truth_by_status[key_name] = {
            "pre_cutoff_observed_total": n_sub,
            "did_play_count": n_played,
            "actual_play_rate": play_rate,
            "actual_absence_rate": round(1.0 - play_rate, 4) if play_rate is not None else None,
            "descriptive_baseline_p_play": p_play_baseline,
            "descriptive_brier_score": brier,
        }

    # Brier Score Global descriptivo empírico
    non_null_sub = valid_observed[valid_observed["report_status"].notna()].copy()
    baseline_preds = non_null_sub["report_status"].map({
        "Out": 0.0,
        "Doubtful": 0.15,
        "Questionable": 0.75,
    }).fillna(0.50)
    global_brier = round(float(np.mean((non_null_sub["did_play"].astype(int) - baseline_preds) ** 2)), 4) if not non_null_sub.empty else None

    # 7. Desglose de Motivos de 'UNKNOWN' / Falta de Cobertura
    missing_status_count = int(injuries["report_status"].isna().sum())
    cutoff_excluded_count = int(in_cutoff_window.sum()) + int(post_kickoff.sum())
    missing_timestamp_count = int(missing_dm.sum())
    missing_snaps_count = int((injuries["label_status"] == "missing_snap_record").sum())
    inactive_roster_labels_count = int((injuries["label_status"] == "observed_inactive_roster").sum())
    unrostered_status_count = int(injuries[injuries["report_status"].notna() & (~pd.Series(is_rostered))].shape[0])
    no_canonical_id_count = int(injuries["player_id"].isna().sum())

    unknown_breakdown = {
        "missing_report_status": {
            "count": missing_status_count,
            "rate_pct": round(float(missing_status_count / total_records) * 100, 2),
            "description": "Jugadores en el reporte de prácticas pero sin designación médica de partido asignada",
        },
        "post_cutoff_or_window_observation": {
            "count": cutoff_excluded_count,
            "rate_pct": round(float(cutoff_excluded_count / total_records) * 100, 4),
            "description": "Reporte observado dentro de la ventana de 2 horas pregame o posterior al kickoff (excluido pregame)",
        },
        "missing_date_modified": {
            "count": missing_timestamp_count,
            "rate_pct": round(float(missing_timestamp_count / total_records) * 100, 2),
            "description": "Reporte sin timestamp upstream verificable (falla cerrado a unknown)",
        },
        "missing_snap_record": {
            "count": missing_snaps_count,
            "rate_pct": round(float(missing_snaps_count / total_records) * 100, 2),
            "description": "Sin registro en snaps del juego (distinguido de no participación comprobada 0 snaps)",
        },
        "observed_inactive_roster": {
            "count": inactive_roster_labels_count,
            "rate_pct": round(float(inactive_roster_labels_count / total_records) * 100, 2),
            "description": "Label did_play=False respaldado explícitamente por status INA del roster semanal",
        },
        "unrostered_with_status": {
            "count": unrostered_status_count,
            "rate_pct": round(float(unrostered_status_count / total_records) * 100, 2),
            "description": "Jugadores reportados con estado médico que no figuran en el roster activo semanal",
        },
        "unresolved_canonical_id": {
            "count": no_canonical_id_count,
            "rate_pct": round(float(no_canonical_id_count / total_records) * 100, 4),
            "description": "Identidad sin mapeo verificado a GSIS ID canónico",
        },
    }

    report = {
        "audit_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "seasons_audited": seasons,
        "total_records": total_records,
        "status_distribution": status_dist,
        "timing_verification": timing_audit,
        "roster_integrity": roster_audit,
        "ground_truth_participation": ground_truth_by_status,
        "descriptive_empirical_baseline_brier": global_brier,
        "calibration_audit_notice": {
            "claims_calibration": False,
            "evaluation_type": "descriptive_empirical_baseline_on_historical_observations",
            "note": (
                "Este Brier score es un baseline descriptivo empírico sobre categorías históricas nflverse. "
                "NO representa un modelo predictivo calibrado ni evaluación out-of-fold. Las claims de calibración "
                "quedan diferidas hasta entrenar y validar un modelo formal con corte temporal cronológico."
            ),
        },
        "unknown_breakdown": unknown_breakdown,
        "resolver_version": "nfl_availability_v2",
    }

    out_file = project_root() / "data" / "nfl_availability_audit.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info("Auditoría de disponibilidad completada exitosamente.", output=str(out_file), global_brier=global_brier)
    return report


if __name__ == "__main__":
    rep = run_availability_audit()
    print("=== RESUMEN AUDITORÍA DISPONIBILIDAD NFL (v2) ===")
    print(f"Total registros: {rep['total_records']}")
    print(f"Brier score descriptivo empírico: {rep['descriptive_empirical_baseline_brier']}")
    print("Participación Ground Truth por estado:")
    for k, v in rep["ground_truth_participation"].items():
        play_rate = v["actual_play_rate"]
        play_text = f"{play_rate * 100:.1f}%" if play_rate is not None else "N/A"
        print(f"  {k}: total={v['pre_cutoff_observed_total']}, jugó={play_text}, Brier={v['descriptive_brier_score']}")
