"""
Constructor de Features de Lesiones Point-in-Time para la NFL (Fase 02).
Implementa estrictamente los requisitos auditados por Codex:
1. Decision cutoff configurable por partido (inicialmente dos horas antes del kickoff).
2. Filtro temporal estricto: solo observaciones con date_modified verificado estrictamente
   anterior al decision_cutoff (date_modified < decision_cutoff). Timestamps ausentes o
   posteriores se excluyen de la información pregame.
3. Uniones inequívocas por partido y equipo a partir del schedule oficial.
4. Cero uso de did_play como feature prepartido. did_play se aísla exclusivamente en
   build_nfl_availability_ground_truth() con asociación exacta por (game_id, team, player_id)
   y diferenciación explícita entre no participación (0 snaps) y falta de registro en snaps.
5. Paridad estricta de schema y transformación entre entrenamiento e inferencia.
"""
from __future__ import annotations

import warnings
from typing import List

import pandas as pd
import polars as pl
import structlog

from ml.data_pipeline.common import DataQualityError
from ml.data_pipeline.nfl_source import (
    load_nfl_games,
    load_nfl_player_crosswalk,
    load_nfl_snapshot,
)

warnings.filterwarnings("ignore")
logger = structlog.get_logger(__name__)

# nflverse rosters use canonical position families (e.g. OL, DL, DB) for some
# seasons and specialist positions (e.g. T, G, C, DE, CB) in others.
_PPR_POSITIONS = {"QB", "RB", "FB", "WR", "TE"}
_DEFENSE_POSITIONS = {
    "DL", "DE", "DT", "NT", "EDGE", "LB", "ILB", "OLB", "MLB",
    "DB", "CB", "S", "FS", "SS",
}
_OFFENSE_POSITIONS = {
    "OL", "OT", "T", "G", "OG", "C", "QB", "RB", "FB", "WR", "TE",
}


def _position_metrics(position: object) -> tuple[bool, bool, bool]:
    """Return whether PPR/offense/defense history is applicable to a roster role.

    A non-applicable metric is a domain-defined zero. An applicable metric with
    missing history remains unknown and cannot be silently imputed.
    """
    pos = str(position or "").strip().upper()
    if pos in _PPR_POSITIONS:
        return True, True, False
    if pos in _DEFENSE_POSITIONS:
        return False, False, True
    if pos in _OFFENSE_POSITIONS:
        return False, True, False
    if pos in {"K", "P", "PK", "LS", "ST"}:
        return False, False, False
    return True, True, True


def build_nfl_historical_injury_features(
    seasons: List[int],
    rolling_window: int = 6,
    cutoff_hours: float = 2.0,
) -> pd.DataFrame:
    """
    Construye features de lesiones point-in-time para entrenamiento histórico a partir de
    snapshots locales verificados en manifest.json.

    Garantías de Dominio:
    - Corte de decisión configurable por juego: cutoff = game_start_utc - cutoff_hours (default 2h).
    - Exige date_modified verificado < decision_cutoff.
    - Rol e historial derivados estrictamente de semanas previas (t-1).
    - Cero uso de did_play del juego objetivo en las features.
    """
    logger.info(
        "Cargando snapshots históricos de la NFL para injury features point-in-time...",
        seasons=seasons,
        cutoff_hours=cutoff_hours,
    )
    stats_frames = []
    snaps_frames = []
    injuries_frames = []
    rosters_frames = []

    for s in seasons:
        stats_frames.append(load_nfl_snapshot("player_stats", s).to_pandas())
        snaps_frames.append(load_nfl_snapshot("snap_counts", s).to_pandas())
        injuries_frames.append(load_nfl_snapshot("injuries", s).to_pandas())
        rosters_frames.append(load_nfl_snapshot("rosters_weekly", s).to_pandas())

    stats = pd.concat(stats_frames, ignore_index=True) if stats_frames else pd.DataFrame()
    snaps = pd.concat(snaps_frames, ignore_index=True) if snaps_frames else pd.DataFrame()
    injuries = pd.concat(injuries_frames, ignore_index=True) if injuries_frames else pd.DataFrame()
    rosters = pd.concat(rosters_frames, ignore_index=True) if rosters_frames else pd.DataFrame()
    players = load_nfl_player_crosswalk().to_pandas()

    # Mapeo de PFR_ID a GSIS_ID para snaps
    crosswalk = players[["pfr_id", "gsis_id"]].dropna().drop_duplicates(subset=["pfr_id"])
    snaps = pd.merge(snaps, crosswalk, left_on="pfr_player_id", right_on="pfr_id", how="left")
    snaps.rename(columns={"gsis_id": "player_id"}, inplace=True)
    stats.rename(columns={"player_id": "player_id_stats"}, inplace=True)
    stats.rename(columns={"player_id_stats": "player_id"}, inplace=True)
    rosters.rename(columns={"gsis_id": "player_id"}, inplace=True)
    injuries.rename(columns={"gsis_id": "player_id"}, inplace=True)
    # Unresolved roster identities cannot contribute to a player-level denominator.
    rosters = rosters.dropna(subset=["player_id"]).copy()

    # Consolidar Stats y Snaps
    snaps_sub = snaps[["season", "week", "player_id", "team", "offense_pct", "defense_pct"]].dropna(subset=["player_id"])
    stats_sub = stats[["season", "week", "player_id", "fantasy_points_ppr"]].dropna(subset=["player_id"])
    game_logs = pd.merge(snaps_sub, stats_sub, on=["season", "week", "player_id"], how="outer")
    # Missing player stats/snaps are unknown observations, not zero production.
    for column in ("offense_pct", "defense_pct", "fantasy_points_ppr"):
        game_logs[column] = pd.to_numeric(game_logs[column], errors="coerce")
    game_logs = game_logs.sort_values(by=["season", "week"])

    # Ventana Móvil de Desempeño
    logger.info("Calculando promedios móviles previos del jugador...")
    game_logs["rolling_ppr"] = game_logs.groupby("player_id")["fantasy_points_ppr"].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )
    game_logs["rolling_off_pct"] = game_logs.groupby("player_id")["offense_pct"].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )
    game_logs["rolling_def_pct"] = game_logs.groupby("player_id")["defense_pct"].transform(
        lambda x: x.rolling(window=rolling_window, min_periods=1).mean()
    )

    # Anti-Leak Roster ASOF: Unirse estrictamente con semanas previas (time_idx - 1)
    game_logs["time_idx"] = game_logs["season"] * 100 + game_logs["week"]
    rosters["time_idx"] = rosters["season"] * 100 + rosters["week"]
    rosters = rosters.sort_values("time_idx")
    game_logs = game_logs.sort_values("time_idx").dropna(subset=["time_idx"])
    rosters["time_idx_search"] = rosters["time_idx"] - 1

    roster_strength = pd.merge_asof(
        rosters[["season", "week", "team", "player_id", "time_idx", "time_idx_search"]],
        game_logs[["player_id", "time_idx", "rolling_ppr", "rolling_off_pct", "rolling_def_pct"]],
        left_on="time_idx_search",
        right_on="time_idx",
        by="player_id",
        direction="backward",
    )
    roster_positions = rosters[["season", "week", "team", "player_id", "position"]].drop_duplicates(
        subset=["season", "week", "team", "player_id"]
    )
    roster_strength = roster_strength.merge(
        roster_positions, on=["season", "week", "team", "player_id"], how="left", validate="one_to_one"
    )
    role_flags = roster_strength["position"].apply(_position_metrics)
    roster_strength[["ppr_applicable", "off_applicable", "def_applicable"]] = pd.DataFrame(
        role_flags.tolist(), index=roster_strength.index
    )
    # Metrics outside a player's role are true structural zeros; required
    # dimensions still need observed prior history.
    for metric, applicable in (
        ("rolling_ppr", "ppr_applicable"),
        ("rolling_off_pct", "off_applicable"),
        ("rolling_def_pct", "def_applicable"),
    ):
        roster_strength.loc[~roster_strength[applicable], metric] = 0.0
    roster_strength["has_required_prior_history"] = (
        (~roster_strength["ppr_applicable"] | roster_strength["rolling_ppr"].notna())
        & (~roster_strength["off_applicable"] | roster_strength["rolling_off_pct"].notna())
        & (~roster_strength["def_applicable"] | roster_strength["rolling_def_pct"].notna())
    )
    team_potential = roster_strength.groupby(["season", "week", "team"]).agg(
        team_potential_ppr=("rolling_ppr", lambda values: values.sum(min_count=1)),
        roster_player_count=("player_id", "count"),
        players_with_ppr_history=("rolling_ppr", "count"),
        players_with_all_history=("has_required_prior_history", "sum"),
    ).reset_index()

    # ── Mapeo inequívoco por partido con decision cutoff configurable (2h pregame) ──
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

    # Validación de no ambigüedad: cada equipo debe tener a lo sumo 1 partido por semana
    schedule_key = ["season", "game_type", "week", "team"]
    dups = team_schedule.duplicated(subset=schedule_key)
    if dups.any():
        raise DataQualityError("Schedule NFL contiene partidos duplicados/ambiguos para un equipo en la misma semana")

    team_schedule["kickoff_utc"] = pd.to_datetime(team_schedule["game_start_utc"], errors="coerce", utc=True)
    if team_schedule["kickoff_utc"].isna().any():
        raise DataQualityError("Schedule NFL contiene partidos sin kickoff UTC verificable")

    team_schedule["decision_cutoff"] = team_schedule["kickoff_utc"] - pd.to_timedelta(cutoff_hours, unit="h")

    injuries = pd.merge(
        injuries,
        team_schedule[["season", "game_type", "week", "team", "game_id", "kickoff_utc", "decision_cutoff"]],
        on=["season", "game_type", "week", "team"],
        how="left",
    )

    # ── Filtro temporal estricto: date_modified verificado < decision_cutoff ──
    inj_dm = pd.to_datetime(injuries["date_modified"], errors="coerce", utc=True)
    cutoff_utc = injuries["decision_cutoff"]

    # Falla cerrado: si falta date_modified o cutoff, o si date_modified >= decision_cutoff -> NO es pregame válido
    is_pregame_valid = inj_dm.notna() & cutoff_utc.notna() & (inj_dm < cutoff_utc)

    def compute_pregame_p_absence(row, valid_mask_val: bool) -> float:
        if not valid_mask_val:
            return 0.0  # Reporte no verificable antes del corte
        if pd.isna(row["report_status"]):
            practice = str(row.get("practice_status") or "").lower()
            return 0.0 if "full participation" in practice else 0.0
        s = str(row["report_status"]).lower()
        if any(x in s for x in ["out", "ir", "reserve", "injured reserve", "suspension", "pup"]):
            return 1.0
        if "doubtful" in s:
            return 0.85
        if any(x in s for x in ["questionable", "day-to-day", "gtd"]):
            return 0.25
        return 0.0

    injuries["p_absence"] = [
        compute_pregame_p_absence(r, v)
        for r, v in zip(injuries.to_dict("records"), is_pregame_valid)
    ]
    known_status = injuries["report_status"].astype(str).str.lower().map(
        lambda value: any(token in value for token in (
            "out", "ir", "reserve", "injured reserve", "suspension", "pup",
            "doubtful", "questionable", "day-to-day", "gtd", "active", "probable", "full",
        ))
    )
    known_status = known_status | (
        injuries["report_status"].isna()
        & injuries["practice_status"].astype(str).str.lower().str.contains("full participation", regex=False)
    )
    injuries["pregame_known"] = is_pregame_valid & injuries["player_id"].notna() & known_status
    report_coverage = injuries.groupby(["season", "week", "team"], as_index=False).agg(
        report_rows=("pregame_known", "size"), all_rows_verified=("pregame_known", "all")
    )

    # Filtrar únicamente jugadores con pérdida positiva
    at_risk_injuries = injuries[injuries["p_absence"] > 0.0].copy()

    lost_prod = pd.merge(
        at_risk_injuries[["season", "week", "team", "player_id", "p_absence"]],
        roster_strength[[
            "season", "week", "team", "player_id", "position", "rolling_ppr",
            "rolling_off_pct", "rolling_def_pct", "ppr_applicable", "off_applicable", "def_applicable",
        ]],
        on=["season", "week", "team", "player_id"],
        how="left",
    )
    for flag in ("ppr_applicable", "off_applicable", "def_applicable"):
        # A missing roster match/position stays conservative: require all axes.
        lost_prod[flag] = lost_prod[flag].astype("boolean").fillna(True).astype(bool)
    lost_prod["prior_stats_valid"] = (
        lost_prod["position"].notna()
        & (~lost_prod["ppr_applicable"] | lost_prod["rolling_ppr"].notna())
        & (~lost_prod["off_applicable"] | lost_prod["rolling_off_pct"].notna())
        & (~lost_prod["def_applicable"] | lost_prod["rolling_def_pct"].notna())
    )
    invalid_prior_teams = set(
        map(tuple, lost_prod.loc[~lost_prod["prior_stats_valid"], ["season", "week", "team"]].drop_duplicates().to_numpy())
    )

    lost_prod["expected_ppr_lost"] = lost_prod["rolling_ppr"] * lost_prod["p_absence"]
    lost_prod["expected_off_lost"] = lost_prod["rolling_off_pct"] * lost_prod["p_absence"]
    lost_prod["expected_def_lost"] = lost_prod["rolling_def_pct"] * lost_prod["p_absence"]

    team_lost = lost_prod.groupby(["season", "week", "team"]).agg({
        "expected_ppr_lost": "sum",
        "expected_off_lost": "sum",
        "expected_def_lost": "sum",
    }).reset_index()
    team_lost.rename(columns={
        "expected_ppr_lost": "team_lost_ppr",
        "expected_off_lost": "team_lost_off_pct",
        "expected_def_lost": "team_lost_def_pct",
    }, inplace=True)

    final_df = pd.merge(team_potential, team_lost, on=["season", "week", "team"], how="left")
    final_df = final_df.merge(report_coverage, on=["season", "week", "team"], how="left")
    final_df = final_df[
        final_df["all_rows_verified"].eq(True)
        & (final_df["report_rows"].fillna(0) > 0)
        & (final_df["team_potential_ppr"].notna())
        & (final_df["team_potential_ppr"] > 0)
        & ~final_df.apply(lambda r: (r["season"], r["week"], r["team"]) in invalid_prior_teams, axis=1)
    ].copy()
    # A zero loss is valid only for a team-week with a verified, complete report.
    for column in ("team_lost_ppr", "team_lost_off_pct", "team_lost_def_pct"):
        final_df[column] = final_df[column].fillna(0.0)

    final_df["ppr_lost_pct"] = final_df["team_lost_ppr"] / final_df["team_potential_ppr"].clip(lower=1.0)
    final_df["off_snaps_lost_pct"] = (final_df["team_lost_off_pct"] / 11.0).clip(upper=1.0)
    final_df["def_snaps_lost_pct"] = (final_df["team_lost_def_pct"] / 11.0).clip(upper=1.0)

    logger.info("Pipeline histórico point-in-time con cutoff 2h finalizado.", rows=len(final_df))
    return final_df[["season", "week", "team", "ppr_lost_pct", "off_snaps_lost_pct", "def_snaps_lost_pct"]]


def build_nfl_availability_ground_truth(
    seasons: List[int],
    cutoff_hours: float = 2.0,
) -> pd.DataFrame:
    """
    Construye el dataset de ETIQUETAS / GROUND TRUTH para medir y evaluar disponibilidad prepartido.
    Vinculación inequívoca por (game_id, team, player_id):
    - did_play: True si participó con > 0 snaps en el juego objetivo.
    - did_play: False si fue observado en snaps del juego pero con 0 snaps.
    - did_play: None si no existe registro en snaps para ese juego (etiqueta no observada).
    - pregame_valid: True si date_modified fue anterior a decision_cutoff (2h pregame).
    """
    stats_frames = []
    snaps_frames = []
    injuries_frames = []
    rosters_frames = []
    for s in seasons:
        stats_frames.append(load_nfl_snapshot("player_stats", s).to_pandas())
        snaps_frames.append(load_nfl_snapshot("snap_counts", s).to_pandas())
        injuries_frames.append(load_nfl_snapshot("injuries", s).to_pandas())
        rosters_frames.append(load_nfl_snapshot("rosters_weekly", s).to_pandas())

    stats = pd.concat(stats_frames, ignore_index=True) if stats_frames else pd.DataFrame()
    snaps = pd.concat(snaps_frames, ignore_index=True) if snaps_frames else pd.DataFrame()
    injuries = pd.concat(injuries_frames, ignore_index=True) if injuries_frames else pd.DataFrame()
    rosters = pd.concat(rosters_frames, ignore_index=True) if rosters_frames else pd.DataFrame()
    players = load_nfl_player_crosswalk().to_pandas()

    crosswalk = players[["pfr_id", "gsis_id"]].dropna().drop_duplicates(subset=["pfr_id"])
    snaps = pd.merge(snaps, crosswalk, left_on="pfr_player_id", right_on="pfr_id", how="left")
    snaps.rename(columns={"gsis_id": "player_id"}, inplace=True)
    injuries.rename(columns={"gsis_id": "player_id"}, inplace=True)
    rosters.rename(columns={"gsis_id": "player_id"}, inplace=True)

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
        raise DataQualityError("Schedule NFL ambiguo al construir ground truth (season, game_type, week, team)")
    team_schedule["kickoff_utc"] = pd.to_datetime(team_schedule["game_start_utc"], errors="coerce", utc=True)
    if team_schedule["kickoff_utc"].isna().any():
        raise DataQualityError("Schedule NFL sin kickoff UTC verificable al construir ground truth")
    team_schedule["decision_cutoff"] = team_schedule["kickoff_utc"] - pd.to_timedelta(cutoff_hours, unit="h")

    injuries = pd.merge(
        injuries,
        team_schedule[["season", "game_type", "week", "team", "game_id", "kickoff_utc", "decision_cutoff"]],
        on=["season", "game_type", "week", "team"],
        how="left",
    )

    # Evaluación temporal
    inj_dm = pd.to_datetime(injuries["date_modified"], errors="coerce", utc=True)
    cutoff_utc = injuries["decision_cutoff"]
    injuries["pregame_valid"] = inj_dm.notna() & cutoff_utc.notna() & (inj_dm < cutoff_utc)

    # Snaps por juego y jugador
    snaps["total_snaps"] = (
        snaps["offense_snaps"].fillna(0)
        + snaps["defense_snaps"].fillna(0)
        + snaps["st_snaps"].fillna(0)
    )
    snaps_map = snaps.set_index(["game_id", "team", "player_id"])["total_snaps"].to_dict()
    roster_key_columns = ["season", "game_type", "week", "team", "player_id"]
    rostered_rows = rosters.dropna(subset=["player_id"])
    roster_keys = set(map(tuple, rostered_rows[roster_key_columns].itertuples(index=False, name=None)))
    roster_status_map = {}
    for key, rows in rostered_rows.groupby(roster_key_columns, dropna=False):
        statuses = rows["status"].dropna().astype(str).unique()
        if len(statuses) > 1:
            raise DataQualityError(f"Roster status NFL ambiguo para {key}: {statuses.tolist()}")
        roster_status_map[key] = statuses[0] if len(statuses) else None

    did_play_list = []
    label_status_list = []

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
                raise DataQualityError(
                    f"Conflicto de ground truth: roster INA con snaps positivos para {roster_key}"
                )
            did_play_list.append(bool(tot > 0))
            label_status_list.append("observed")
        elif roster_status_map.get(roster_key) == "INA":
            # INA es una designación explícita de inactivo de partido en el roster semanal.
            # No se generaliza esta regla a ACT/RES ni a una mera ausencia de fila en snaps.
            did_play_list.append(False)
            label_status_list.append("observed_inactive_roster")
        else:
            # Absence of a player row is not a verified zero snap observation.
            did_play_list.append(None)
            label_status_list.append("missing_snap_record")

    injuries["did_play"] = did_play_list
    injuries["label_status"] = label_status_list

    return injuries[[
        "game_id", "season", "game_type", "week", "team", "player_id", "report_status",
        "date_modified", "decision_cutoff", "pregame_valid", "did_play", "label_status"
    ]].dropna(subset=["report_status"])
