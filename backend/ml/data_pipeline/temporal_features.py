"""Pure point-in-time features shared by NBA and NFL dataset builders."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from ml.data_pipeline.common import DataQualityError

NBA_GAME_STATS = [
    "fg_pct", "offensive_rebounds", "total_rebounds", "turnovers",
]
NFL_GAME_STATS = [
    "off_epa_per_play", "off_success_rate",
    "def_epa_allowed_per_play", "def_success_allowed",
]
BASE_FEATURES = [
    "win_rate", "avg_margin", "avg_points_for", "avg_points_against",
    "days_rest", "is_back_to_back", "games_prior",
]


def _team_feature_values(
    team_history: list[dict], game_date, side: str, stat_names: list[str]
) -> dict[str, float | int]:
    """Single implementation of rolling features used by train and inference."""
    count = len(team_history)
    if count == 0:
        raise DataQualityError("No hay historial observado suficiente para calcular features")
    current = team_history[-1]
    result: dict[str, float | int] = {
        f"{side}_games_prior": count,
        f"{side}_win_rate": sum(item["outcome"] for item in team_history) / count,
        f"{side}_avg_margin": sum(item["margin"] for item in team_history) / count,
        f"{side}_avg_points_for": sum(item["points_for"] for item in team_history) / count,
        f"{side}_avg_points_against": sum(item["points_against"] for item in team_history) / count,
        f"{side}_days_rest": (game_date - current["game_date"]).days,
    }
    result[f"{side}_is_back_to_back"] = int(result[f"{side}_days_rest"] == 1)
    for metric in stat_names:
        result[f"{side}_avg_{metric}"] = sum(item[metric] for item in team_history) / count
    return result


def build_temporal_dataset(
    games: pd.DataFrame,
    sport: str,
    game_stat_names: Iterable[str],
    window: int = 10,
    minimum_history: int = 1,
) -> tuple[pd.DataFrame, dict]:
    """Build one row per eligible game using only completed games on earlier dates.

    All games on a calendar date are processed as a batch, so one game's final
    score can never become another same-day game's feature. Ties contribute 0.5
    to the historical win rate and are omitted from the binary winner target.
    """
    if window < 1 or minimum_history < 1:
        raise ValueError("window and minimum_history must be positive")
    required = {
        "game_id", "game_date", "season", "home_team_id", "away_team_id",
        "home_score", "away_score", "home_win", "source", "source_ref",
    }
    missing = required.difference(games.columns)
    if missing:
        raise DataQualityError(f"{sport}: faltan campos canónicos: {sorted(missing)}")
    stat_names = list(game_stat_names)
    for side in ("home", "away"):
        absent = [f"{side}_{name}" for name in stat_names if f"{side}_{name}" not in games.columns]
        if absent:
            raise DataQualityError(f"{sport}: faltan estadísticas fuente: {absent}")
    data = games.copy()
    data["game_date"] = pd.to_datetime(data["game_date"], errors="coerce").dt.date
    data["home_score"] = pd.to_numeric(data["home_score"], errors="coerce")
    data["away_score"] = pd.to_numeric(data["away_score"], errors="coerce")
    if data[["game_date", "home_score", "away_score", "home_team_id", "away_team_id"]].isna().any().any():
        raise DataQualityError(f"{sport}: schedule incompleto; no se rellenan campos ausentes")
    if data["game_id"].duplicated().any():
        raise DataQualityError(f"{sport}: game_id duplicado")
    metric_columns = [f"{side}_{name}" for side in ("home", "away") for name in stat_names]
    metric_values = data[metric_columns].apply(pd.to_numeric, errors="coerce")
    if metric_values.isna().any().any() or not metric_values.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError(f"{sport}: estadísticas de juego incompletas/no finitas; no se imputan")
    data[metric_columns] = metric_values
    data = data.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    history: dict[str, list[dict]] = defaultdict(list)
    rows: list[dict] = []
    skipped_no_history = 0
    skipped_ties = 0
    for game_date, same_day in data.groupby("game_date", sort=True):
        for game in same_day.to_dict("records"):
            home_id, away_id = str(game["home_team_id"]), str(game["away_team_id"])
            home_prior = history[home_id][-window:]
            away_prior = history[away_id][-window:]
            if len(home_prior) < minimum_history or len(away_prior) < minimum_history:
                skipped_no_history += 1
                continue
            row = {
                "game_id": str(game["game_id"]),
                "game_date": game_date.isoformat(),
                "feature_cutoff_exclusive": game_date.isoformat(),
                "feature_max_observed_game_date": max(
                    max(item["game_date"] for item in home_prior),
                    max(item["game_date"] for item in away_prior),
                ).isoformat(),
                "season": int(game["season"]),
                "sport": sport.upper(),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "source": game["source"],
                "source_ref": game["source_ref"],
                "home_score": int(game["home_score"]),
                "away_score": int(game["away_score"]),
                "target_home_win": game["home_win"],
            }
            row.update(_team_feature_values(home_prior, game_date, "home", stat_names))
            row.update(_team_feature_values(away_prior, game_date, "away", stat_names))
            
            # Placeholders only; historical coverage must be joined and validated downstream.
            row.update({
                "home_ppr_lost_pct": None, "home_off_snaps_lost_pct": None, "home_def_snaps_lost_pct": None,
                "away_ppr_lost_pct": None, "away_off_snaps_lost_pct": None, "away_def_snaps_lost_pct": None,
            })
            row["diff_win_rate"] = row["home_win_rate"] - row["away_win_rate"]
            row["diff_avg_margin"] = row["home_avg_margin"] - row["away_avg_margin"]
            row["diff_avg_points_for"] = row["home_avg_points_for"] - row["away_avg_points_for"]
            row["diff_days_rest"] = row["home_days_rest"] - row["away_days_rest"]
            if game["home_win"] is None or pd.isna(game["home_win"]):
                skipped_ties += 1
            else:
                row["target_home_win"] = int(game["home_win"])
                rows.append(row)
        # Add observed results only after every target on this date was built.
        for game in same_day.to_dict("records"):
            home_id, away_id = str(game["home_team_id"]), str(game["away_team_id"])
            home_score, away_score = int(game["home_score"]), int(game["away_score"])
            home_outcome = 0.5 if home_score == away_score else float(home_score > away_score)
            away_outcome = 0.5 if home_score == away_score else float(away_score > home_score)
            base = {f"{metric}": float(game[f"home_{metric}"]) for metric in stat_names}
            away_stats = {f"{metric}": float(game[f"away_{metric}"]) for metric in stat_names}
            history[home_id].append({
                "game_date": game_date, "outcome": home_outcome,
                "points_for": home_score, "points_against": away_score,
                "margin": home_score - away_score, **base,
            })
            history[away_id].append({
                "game_date": game_date, "outcome": away_outcome,
                "points_for": away_score, "points_against": home_score,
                "margin": away_score - home_score, **away_stats,
            })

    result = pd.DataFrame(rows)
    if result.empty:
        raise DataQualityError(f"{sport}: no rows with the required pre-game history")
    if not (pd.to_datetime(result["feature_max_observed_game_date"]) < pd.to_datetime(result["game_date"])).all():
        raise DataQualityError(f"{sport}: se detectó un registro de feature no anterior al partido")
    features = feature_columns(stat_names)
    values = result[features].apply(pd.to_numeric, errors="coerce")
    checked_values = values.drop(columns=[
        "home_ppr_lost_pct", "home_off_snaps_lost_pct", "home_def_snaps_lost_pct",
        "away_ppr_lost_pct", "away_off_snaps_lost_pct", "away_def_snaps_lost_pct",
    ], errors="ignore")
    if checked_values.isna().any().any() or not checked_values.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError(f"{sport}: features incompletas/no finitas; no se imputan")
    result[features] = values
    metadata = {
        "sport": sport.upper(), "source_rows": int(len(data)),
        "training_rows": int(len(result)), "skipped_no_history": skipped_no_history,
        "skipped_ties": skipped_ties, "window_games": window,
        "feature_rule": "completed games with game_date strictly before target game_date",
        "features": features,
    }
    return result, metadata


def feature_columns(
    game_stat_names: Iterable[str], *, include_availability: bool = True,
) -> list[str]:
    columns = []
    for side in ("home", "away"):
        columns.extend([
            f"{side}_win_rate", f"{side}_avg_margin",
            f"{side}_avg_points_for", f"{side}_avg_points_against",
            f"{side}_days_rest", f"{side}_is_back_to_back", f"{side}_games_prior",
        ])
        if include_availability:
            columns.extend([
                f"{side}_ppr_lost_pct", f"{side}_off_snaps_lost_pct",
                f"{side}_def_snaps_lost_pct",
            ])
        columns.extend(f"{side}_avg_{metric}" for metric in game_stat_names)
    columns.extend(["diff_win_rate", "diff_avg_margin", "diff_avg_points_for", "diff_days_rest"])
    return columns


def build_matchup_features(
    completed_games: pd.DataFrame,
    upcoming_games: pd.DataFrame,
    game_stat_names: Iterable[str],
    window: int = 10,
    minimum_history: int = 1,
    as_of=None,
) -> tuple[pd.DataFrame, dict]:
    """Build inference inputs from observed completed games strictly before each target date.

    `as_of` is the last fully observed calendar date/time. Targets must be on a later
    date, or on the same date with a verified kickoff later than the exact `as_of`.
    Completed games through the observed date are allowed, matching training semantics.
    """
    if window < 1 or minimum_history < 1:
        raise ValueError("window and minimum_history must be positive")
    stats = list(game_stat_names)
    past_required = {
        "game_id", "game_date", "home_team_id", "away_team_id", "home_score",
        "away_score", "home_win", "source_ref",
        *(f"{side}_{stat}" for side in ("home", "away") for stat in stats),
    }
    target_required = {"game_id", "game_date", "home_team_id", "away_team_id", "source_ref"}
    missing_past = past_required.difference(completed_games.columns)
    missing_target = target_required.difference(upcoming_games.columns)
    if missing_past or missing_target:
        raise DataQualityError(
            f"Features de inferencia incompletas; historial={sorted(missing_past)}, "
            f"schedule={sorted(missing_target)}"
        )
    past = completed_games.copy()
    targets = upcoming_games.copy()
    past["game_date"] = pd.to_datetime(past["game_date"], errors="coerce").dt.date
    targets["game_date"] = pd.to_datetime(targets["game_date"], errors="coerce").dt.date
    if past[["game_date", "home_team_id", "away_team_id", "home_score", "away_score"]].isna().any().any():
        raise DataQualityError("Historial incompleto para inferencia; no se imputan valores")
    if targets[["game_date", "home_team_id", "away_team_id"]].isna().any().any():
        raise DataQualityError("Schedule futuro incompleto para inferencia")
    if targets["game_id"].duplicated().any():
        raise DataQualityError("Schedule futuro contiene game_id duplicados")
    as_of_ts = pd.to_datetime(as_of or datetime.now(timezone.utc), utc=True)
    as_of_date = as_of_ts.date()
    target_dates = pd.to_datetime(targets["game_date"], errors="coerce").dt.date
    kickoff_values = targets["game_start_utc"] if "game_start_utc" in targets else pd.Series(
        pd.NaT, index=targets.index
    )
    same_day_kickoff = pd.to_datetime(kickoff_values, errors="coerce", utc=True)
    is_future_target = (target_dates > as_of_date) | (
        (target_dates == as_of_date) & same_day_kickoff.notna() & (same_day_kickoff > as_of_ts)
    )
    if not targets.empty and not is_future_target.all():
        raise DataQualityError("El schedule de inferencia contiene partidos no futuros al cutoff as_of")
    metrics = [f"{side}_{stat}" for side in ("home", "away") for stat in stats]
    values = past[metrics].apply(pd.to_numeric, errors="coerce")
    if values.isna().any().any() or not values.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError("Features fuente del historial nulas/no finitas; no se imputan")
    past[metrics] = values

    rows = []
    skipped_games = []
    # Every target must be after as_of, so the exact same observed history is
    # eligible for all targets. Aggregate once instead of rescanning the season
    # for each scheduled game.
    history: dict[str, list[dict]] = defaultdict(list)
    observed = past[past["game_date"] <= as_of_date]
    for game in observed.sort_values(["game_date", "game_id"]).to_dict("records"):
        home_id, away_id = str(game["home_team_id"]), str(game["away_team_id"])
        hs, aw = int(game["home_score"]), int(game["away_score"])
        if hs == aw:
            home_result = away_result = 0.5
        else:
            home_result, away_result = float(hs > aw), float(aw > hs)
        history[home_id].append({
            "game_date": game["game_date"], "outcome": home_result,
            "points_for": hs, "points_against": aw, "margin": hs - aw,
            **{stat: float(game[f"home_{stat}"]) for stat in stats},
        })
        history[away_id].append({
            "game_date": game["game_date"], "outcome": away_result,
            "points_for": aw, "points_against": hs, "margin": aw - hs,
            **{stat: float(game[f"away_{stat}"]) for stat in stats},
        })

    for target in targets.sort_values(["game_date", "game_id"]).to_dict("records"):
        target_date = target["game_date"]
        home_prior = history[str(target["home_team_id"])][-window:]
        away_prior = history[str(target["away_team_id"])][-window:]
        if min(len(home_prior), len(away_prior)) < minimum_history:
            skipped_games.append({
                "game_id": str(target["game_id"]), "reason": "insufficient_observed_history",
                "home_games": len(home_prior), "away_games": len(away_prior),
            })
            continue
        row = {
            "game_id": str(target["game_id"]), "game_date": target_date.isoformat(),
            "feature_cutoff_exclusive": target_date.isoformat(),
            "feature_max_observed_game_date": max(
                max(item["game_date"] for item in home_prior),
                max(item["game_date"] for item in away_prior),
            ).isoformat(),
            "home_team_id": str(target["home_team_id"]),
            "away_team_id": str(target["away_team_id"]),
            "schedule_source_ref": target["source_ref"],
            "feature_source_refs": sorted(observed["source_ref"].dropna().unique().tolist()),
        }
        for column in ("game_start_utc", "season", "week", "home_team_name", "away_team_name", "home_team_abbreviation", "away_team_abbreviation"):
            if column in target:
                row[column] = target[column]
        row.update(_team_feature_values(home_prior, target_date, "home", stats))
        row.update(_team_feature_values(away_prior, target_date, "away", stats))
        row.update({"home_ppr_lost_pct": None, "home_off_snaps_lost_pct": None, "home_def_snaps_lost_pct": None, "away_ppr_lost_pct": None, "away_off_snaps_lost_pct": None, "away_def_snaps_lost_pct": None})
        row["diff_win_rate"] = row["home_win_rate"] - row["away_win_rate"]
        row["diff_avg_margin"] = row["home_avg_margin"] - row["away_avg_margin"]
        row["diff_avg_points_for"] = row["home_avg_points_for"] - row["away_avg_points_for"]
        row["diff_days_rest"] = row["home_days_rest"] - row["away_days_rest"]
        rows.append(row)

    features = feature_columns(stats)
    if not rows:
        raise DataQualityError(f"Ningún partido tiene historial prepartido suficiente: {skipped_games[:5]}")
    result = pd.DataFrame(rows)
    numeric = result[features].apply(pd.to_numeric, errors="coerce")
    checked_numeric = numeric.drop(columns=[
        "home_ppr_lost_pct", "home_off_snaps_lost_pct", "home_def_snaps_lost_pct",
        "away_ppr_lost_pct", "away_off_snaps_lost_pct", "away_def_snaps_lost_pct",
    ], errors="ignore")
    if checked_numeric.isna().any().any() or not checked_numeric.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError("Inferencia produjo features nulas/no finitas")
    result[features] = numeric
    return result, {
        "features": features, "window_games": window,
        "feature_rule": "completed games with game_date strictly before target game_date",
        "max_observed_game_date": result["feature_max_observed_game_date"].max(),
        "skipped_games": skipped_games,
        "source_refs": sorted({
            ref for refs in result["feature_source_refs"].tolist() for ref in refs
        }),
    }
