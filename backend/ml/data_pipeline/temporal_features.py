"""Pure point-in-time features shared by NBA and NFL dataset builders."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import pandas as pd
import numpy as np

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
            
            # Placeholders only; historical coverage must be joined and validated downstream for NFL.
            if sport.lower() == "nfl":
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
    features = feature_columns(stat_names, include_availability=(sport.lower() == "nfl"))
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
    is_nfl = any("epa" in s for s in stats)
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
    if past["game_id"].duplicated().any():
        raise DataQualityError("Historial de partidos completados contiene game_id duplicados")
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
    # Determine if as_of represents an intraday timestamp with a specific time.
    # Datetime and Timestamp instances represent exact points in time (even at midnight).
    # Date-only semantics are preserved only for datetime.date or date-only strings ("YYYY-MM-DD").
    if as_of is None:
        as_of_has_intraday_time = True
    elif isinstance(as_of, (datetime, pd.Timestamp)):
        as_of_has_intraday_time = True
    elif isinstance(as_of, str):
        as_of_has_intraday_time = any(sep in as_of for sep in ("T", ":", " "))
    elif isinstance(as_of, date):
        as_of_has_intraday_time = False
    else:
        as_of_has_intraday_time = False

    observed = past[past["game_date"] <= as_of_date].copy()

    # Reject future kickoffs fail-closed
    if "game_start_utc" in observed.columns:
        past_kickoffs = pd.to_datetime(observed["game_start_utc"], errors="coerce", utc=True)
        is_future_kickoff = (past_kickoffs > as_of_ts) if as_of_has_intraday_time else (past_kickoffs.dt.date > as_of_date)
        if is_future_kickoff.any():
            raise DataQualityError("completed_games contiene partidos con kickoff posterior al cutoff as_of")

    # In intraday cutoffs, a kickoff prior to the cutoff does NOT guarantee that the final score
    # was available at decision time (a match can take 2.5-3.5h and still be in progress).
    # Furthermore, games that concluded after the cutoff, cross-midnight games, or games with
    # corrupted timestamps (end preceding kickoff) must never be incorporated into observed history.
    def _is_game_validly_observed(row) -> bool:
        # 1. Parse completion timestamp if present in any canonical column
        end_ts = None
        for col in ("game_end_utc", "end_utc", "completed_at_utc", "finalized_at_utc"):
            if col in row and pd.notna(row[col]):
                cand = pd.to_datetime(row[col], errors="coerce", utc=True)
                if pd.notna(cand):
                    end_ts = cand
                    break
                else:
                    return False

        # 2. Parse kickoff timestamp if present
        k_ts = None
        if "game_start_utc" in row and pd.notna(row["game_start_utc"]):
            cand_k = pd.to_datetime(row["game_start_utc"], errors="coerce", utc=True)
            if pd.notna(cand_k):
                k_ts = cand_k
            else:
                return False

        # 3. If both timestamps are present, check internal temporal consistency
        if k_ts is not None and end_ts is not None:
            if end_ts < k_ts:
                return False

        # 4. If end timestamp is present, it must NEVER be in the future relative to cutoff
        if end_ts is not None:
            if as_of_has_intraday_time:
                if end_ts > as_of_ts:
                    return False
            else:
                if end_ts.date() > as_of_date:
                    return False

        # 5. For intraday cutoffs, games on the cutoff date or near the cutoff
        # (kickoff on cutoff date in UTC or local date, or kickoff within 6 hours before cutoff)
        # MUST demonstrate both a verified kickoff and verified completion <= as_of_ts.
        if as_of_has_intraday_time:
            is_cutoff_day_game = (
                row["game_date"] == as_of_date
                or (k_ts is not None and k_ts.date() == as_of_date)
                or (k_ts is not None and k_ts > as_of_ts - timedelta(hours=6))
            )
            if is_cutoff_day_game:
                if k_ts is None or k_ts > as_of_ts:
                    return False
                if end_ts is None or end_ts > as_of_ts:
                    return False
                if end_ts < k_ts:
                    return False

        return True

    if not observed.empty:
        keep_mask = observed.apply(_is_game_validly_observed, axis=1)
        observed = observed[keep_mask]
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
        home_prior = [item for item in history[str(target["home_team_id"])] if item["game_date"] < target_date][-window:]
        away_prior = [item for item in history[str(target["away_team_id"])] if item["game_date"] < target_date][-window:]
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
        if is_nfl:
            row.update({"home_ppr_lost_pct": None, "home_off_snaps_lost_pct": None, "home_def_snaps_lost_pct": None, "away_ppr_lost_pct": None, "away_off_snaps_lost_pct": None, "away_def_snaps_lost_pct": None})
        row["diff_win_rate"] = row["home_win_rate"] - row["away_win_rate"]
        row["diff_avg_margin"] = row["home_avg_margin"] - row["away_avg_margin"]
        row["diff_avg_points_for"] = row["home_avg_points_for"] - row["away_avg_points_for"]
        row["diff_days_rest"] = row["home_days_rest"] - row["away_days_rest"]
        rows.append(row)

    features = feature_columns(stats, include_availability=is_nfl)
    if not rows:
        raise DataQualityError(f"Ningún partido tiene historial prepartido suficiente: {skipped_games[:5]}")
    result = pd.DataFrame(rows)
    if not (pd.to_datetime(result["feature_max_observed_game_date"]) < pd.to_datetime(result["game_date"])).all():
        raise DataQualityError("Inferencia: feature_max_observed_game_date no es estrictamente anterior a game_date")
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


# ─── NBA DEDICATED DAILY FEATURES PIPELINE (TAREA-11) ─────────────────────────

NBA_DAILY_GAME_STATS = [
    "pace", "off_rating", "def_rating", "net_rating",
    "fg_pct", "offensive_rebounds", "total_rebounds", "turnovers",
]

NBA_DAILY_FATIGUE_STATS = [
    "days_rest", "is_back_to_back", "is_3_in_4", "games_in_last_4_days", "games_prior",
]


def nba_daily_feature_columns() -> list[str]:
    """
    Retorna la lista de features exclusiva para la dinámica diaria de la NBA.
    Comprende métricas de fatiga de calendario (B2B, days rest, 3-en-4),
    ratings avanzados rodantes (Pace, ORtg, DRtg, NetRtg con .shift(1))
    y estadísticas tradicionales de tiro/posesión, sin variables semanales de NFL.
    """
    cols: list[str] = []
    for side in ("home", "away"):
        # Fatiga de calendario
        cols.extend([
            f"{side}_days_rest",
            f"{side}_is_back_to_back",
            f"{side}_is_3_in_4",
            f"{side}_games_in_last_4_days",
            f"{side}_games_prior",
        ])
        # Récord y margen rodante
        cols.extend([
            f"{side}_win_rate",
            f"{side}_avg_margin",
            f"{side}_avg_points_for",
            f"{side}_avg_points_against",
        ])
        # Ratings avanzados rodantes
        cols.extend([
            f"{side}_rolling_pace",
            f"{side}_rolling_off_rating",
            f"{side}_rolling_def_rating",
            f"{side}_rolling_net_rating",
        ])
        # Box score rodante
        cols.extend([
            f"{side}_avg_fg_pct",
            f"{side}_avg_offensive_rebounds",
            f"{side}_avg_total_rebounds",
            f"{side}_avg_turnovers",
        ])

    # Diferenciales (Home - Away)
    cols.extend([
        "diff_days_rest",
        "diff_is_back_to_back",
        "diff_is_3_in_4",
        "diff_pace",
        "diff_off_rating",
        "diff_def_rating",
        "diff_net_rating",
        "diff_win_rate",
        "diff_avg_margin",
        "diff_avg_points_for",
    ])
    return cols


def _require_nba_stat(game: dict, keys: tuple[str, ...], name: str) -> float:
    for k in keys:
        if k in game and game[k] is not None and not pd.isna(game[k]):
            v = game[k]
            if isinstance(v, (bool, np.bool_)):
                raise DataQualityError(f"Valor booleano inválido en estadística {name} para game_id={game.get('game_id')}")
            try:
                f = float(v)
            except (ValueError, TypeError) as exc:
                raise DataQualityError(f"Valor no numérico en {name} para game_id={game.get('game_id')}: {v!r}") from exc
            if not math.isfinite(f) or f < 0.0:
                raise DataQualityError(f"Valor no finito o negativo en {name} para game_id={game.get('game_id')}: {f}")
            return f
    raise DataQualityError(f"Estadística requerida {name} ausente en partido NBA game_id={game.get('game_id')}; prohibido sintetizar")


def _ensure_nba_game_ratings(games: pd.DataFrame) -> pd.DataFrame:
    """Verifica y calcula posesiones y ratings de posesión en partidos NBA si faltan.
    Falla cerrado ante datos incompletos o ausentes; nunca sintetiza estadísticas con defaults."""
    df = games.copy()
    if "home_pace" in df.columns and "home_off_rating" in df.columns:
        if not (np.isfinite(df["home_pace"]) & np.isfinite(df["home_off_rating"])).all():
            raise DataQualityError("Partidos NBA contienen home_pace o home_off_rating no finitos")
        return df

    from ml.data_pipeline.nba_source import compute_nba_game_possessions_and_ratings
    ratings_records = []
    for row in df.to_dict("records"):
        r = compute_nba_game_possessions_and_ratings(
            home_fga=_require_nba_stat(row, ("home_field_goals_attempted", "home_fga"), "home_fga"),
            home_fta=_require_nba_stat(row, ("home_free_throws_attempted", "home_fta"), "home_fta"),
            home_oreb=_require_nba_stat(row, ("home_offensive_rebounds", "home_oreb"), "home_oreb"),
            home_tov=_require_nba_stat(row, ("home_turnovers", "home_tov"), "home_tov"),
            home_score=_require_nba_stat(row, ("home_score",), "home_score"),
            away_fga=_require_nba_stat(row, ("away_field_goals_attempted", "away_fga"), "away_fga"),
            away_fta=_require_nba_stat(row, ("away_free_throws_attempted", "away_fta"), "away_fta"),
            away_oreb=_require_nba_stat(row, ("away_offensive_rebounds", "away_oreb"), "away_oreb"),
            away_tov=_require_nba_stat(row, ("away_turnovers", "away_tov"), "away_tov"),
            away_score=_require_nba_stat(row, ("away_score",), "away_score"),
        )
        ratings_records.append(r)
    ratings_df = pd.DataFrame(ratings_records, index=df.index)
    for col in ratings_df.columns:
        df[col] = ratings_df[col]
    return df


def build_nba_daily_temporal_dataset(
    games: pd.DataFrame,
    window: int = 10,
    minimum_history: int = 1,
) -> tuple[pd.DataFrame, dict]:
    """
    Construye el dataset temporal de entrenamiento/evaluación diario para NBA.
    - Fatiga de calendario: days_rest (acotado [1, 10]), is_back_to_back, is_3_in_4.
    - Ratings avanzados (Pace, ORtg, DRtg, NetRtg) rodantes estrictamente agrupados
      por equipo mediante transform() con .shift(1), garantizando que no exista
      fuga entre equipos contiguos ni leakage del partido evaluado.
    - Cero variables semanales de NFL (sin availability, ppr, snaps, epa).
    """
    if window < 1 or minimum_history < 1:
        raise ValueError("window and minimum_history must be positive")

    required = {
        "game_id", "game_date", "home_team_id", "away_team_id",
        "home_score", "away_score", "home_win",
    }
    missing = required.difference(games.columns)
    if missing:
        raise DataQualityError(f"NBA daily dataset: faltan campos canónicos: {sorted(missing)}")

    df = _ensure_nba_game_ratings(games)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    if df[["game_date", "home_score", "away_score", "home_team_id", "away_team_id"]].isna().any().any():
        raise DataQualityError("NBA daily dataset: datos incompletos en schedule")
    if df["game_id"].duplicated().any():
        raise DataQualityError("NBA daily dataset: game_id duplicado")

    df = df.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    # Construir log por equipo
    home_rows = []
    away_rows = []
    for game in df.to_dict("records"):
        gid = str(game["game_id"])
        gdate = game["game_date"]
        hs, aw = float(game["home_score"]), float(game["away_score"])
        hw = float(game["home_win"])
        home_rows.append({
            "game_id": gid, "game_date": gdate, "team_id": str(game["home_team_id"]),
            "opp_id": str(game["away_team_id"]), "is_home": 1,
            "outcome": hw, "margin": hs - aw, "points_for": hs, "points_against": aw,
            "pace": float(game["home_pace"]),
            "off_rating": float(game["home_off_rating"]),
            "def_rating": float(game["home_def_rating"]),
            "net_rating": float(game["home_net_rating"]),
            "fg_pct": _require_nba_stat(game, ("home_fg_pct",), "home_fg_pct"),
            "offensive_rebounds": _require_nba_stat(game, ("home_offensive_rebounds",), "home_offensive_rebounds"),
            "total_rebounds": _require_nba_stat(game, ("home_total_rebounds",), "home_total_rebounds"),
            "turnovers": _require_nba_stat(game, ("home_turnovers",), "home_turnovers"),
        })
        away_rows.append({
            "game_id": gid, "game_date": gdate, "team_id": str(game["away_team_id"]),
            "opp_id": str(game["home_team_id"]), "is_home": 0,
            "outcome": 1.0 - hw, "margin": aw - hs, "points_for": aw, "points_against": hs,
            "pace": float(game["away_pace"]),
            "off_rating": float(game["away_off_rating"]),
            "def_rating": float(game["away_def_rating"]),
            "net_rating": float(game["away_net_rating"]),
            "fg_pct": _require_nba_stat(game, ("away_fg_pct",), "away_fg_pct"),
            "offensive_rebounds": _require_nba_stat(game, ("away_offensive_rebounds",), "away_offensive_rebounds"),
            "total_rebounds": _require_nba_stat(game, ("away_total_rebounds",), "away_total_rebounds"),
            "turnovers": _require_nba_stat(game, ("away_turnovers",), "away_turnovers"),
        })

    team_games = pd.DataFrame(home_rows + away_rows)
    team_games["game_date_dt"] = pd.to_datetime(team_games["game_date"])
    team_games = team_games.sort_values(["team_id", "game_date_dt", "game_id"]).reset_index(drop=True)

    # Agrupación por equipo y cálculo vectorial con .shift(1)
    g = team_games.groupby("team_id")

    # Fatiga de calendario (días de descanso acotados en [1.0, 10.0]):
    prev_date = g["game_date_dt"].shift(1)
    days_rest_series = (team_games["game_date_dt"] - prev_date).dt.days
    team_games["is_back_to_back"] = (days_rest_series == 1).astype(int)
    team_games["days_rest"] = days_rest_series.fillna(7.0).clip(lower=1.0, upper=10.0)

    # Densidad 3-en-4 (juego 2 partidos antes dentro de <= 3 días):
    prev_2_date = g["game_date_dt"].shift(2)
    days_since_2_prior = (team_games["game_date_dt"] - prev_2_date).dt.days
    team_games["is_3_in_4"] = ((days_since_2_prior <= 3) & (days_since_2_prior > 0)).astype(int)

    def _count_prior_in_window(sub_s):
        dates = sub_s.tolist()
        counts = []
        for i, d in enumerate(dates):
            c = sum(1 for j in range(i) if 0 < (d - dates[j]).days <= 3)
            counts.append(c)
        return pd.Series(counts, index=sub_s.index)

    team_games["games_in_last_4_days"] = g["game_date_dt"].transform(_count_prior_in_window)
    team_games["games_prior"] = g.cumcount()
    team_games["feature_max_observed_game_date"] = g["game_date"].shift(1)

    # Métricas rodantes estrictamente calculadas por equipo mediante transform() con .shift(1):
    stat_metrics = [
        "pace", "off_rating", "def_rating", "net_rating",
        "outcome", "margin", "points_for", "points_against",
        "fg_pct", "offensive_rebounds", "total_rebounds", "turnovers",
    ]
    for m in stat_metrics:
        team_games[f"rolling_{m}"] = g[m].transform(
            lambda s: s.shift(1).rolling(window, min_periods=minimum_history).mean()
        )

    # Separar en home y away
    h_df = team_games[team_games["is_home"] == 1].copy()
    a_df = team_games[team_games["is_home"] == 0].copy()

    # Prefijar columnas
    rename_h = {
        "days_rest": "home_days_rest", "is_back_to_back": "home_is_back_to_back",
        "is_3_in_4": "home_is_3_in_4", "games_in_last_4_days": "home_games_in_last_4_days",
        "games_prior": "home_games_prior", "feature_max_observed_game_date": "home_max_obs",
        "rolling_pace": "home_rolling_pace", "rolling_off_rating": "home_rolling_off_rating",
        "rolling_def_rating": "home_rolling_def_rating", "rolling_net_rating": "home_rolling_net_rating",
        "rolling_outcome": "home_win_rate", "rolling_margin": "home_avg_margin",
        "rolling_points_for": "home_avg_points_for", "rolling_points_against": "home_avg_points_against",
        "rolling_fg_pct": "home_avg_fg_pct", "rolling_offensive_rebounds": "home_avg_offensive_rebounds",
        "rolling_total_rebounds": "home_avg_total_rebounds", "rolling_turnovers": "home_avg_turnovers",
    }
    rename_a = {
        "days_rest": "away_days_rest", "is_back_to_back": "away_is_back_to_back",
        "is_3_in_4": "away_is_3_in_4", "games_in_last_4_days": "away_games_in_last_4_days",
        "games_prior": "away_games_prior", "feature_max_observed_game_date": "away_max_obs",
        "rolling_pace": "away_rolling_pace", "rolling_off_rating": "away_rolling_off_rating",
        "rolling_def_rating": "away_rolling_def_rating", "rolling_net_rating": "away_rolling_net_rating",
        "rolling_outcome": "away_win_rate", "rolling_margin": "away_avg_margin",
        "rolling_points_for": "away_avg_points_for", "rolling_points_against": "away_avg_points_against",
        "rolling_fg_pct": "away_avg_fg_pct", "rolling_offensive_rebounds": "away_avg_offensive_rebounds",
        "rolling_total_rebounds": "away_avg_total_rebounds", "rolling_turnovers": "away_avg_turnovers",
    }

    h_df = h_df.rename(columns=rename_h)
    a_df = a_df.rename(columns=rename_a)

    cols_h = ["game_id"] + list(rename_h.values())
    cols_a = ["game_id"] + list(rename_a.values())

    merged = df.merge(h_df[cols_h], on="game_id").merge(a_df[cols_a], on="game_id")

    # Filtrar partidos con historial suficiente en ambos lados
    valid_mask = (merged["home_games_prior"] >= minimum_history) & (merged["away_games_prior"] >= minimum_history)
    skipped_no_history = int((~valid_mask).sum())
    result = merged[valid_mask].copy()

    if result.empty:
        raise DataQualityError("NBA daily dataset: ningún partido tiene historial suficiente")

    # Max observed date
    result["feature_max_observed_game_date"] = result.apply(
        lambda r: max(r["home_max_obs"], r["away_max_obs"]).isoformat(), axis=1
    )
    result["feature_cutoff_exclusive"] = result["game_date"].apply(lambda d: d.isoformat())

    # Comprobar ausencia estricta de fuga temporal: max_obs < game_date
    if not (pd.to_datetime(result["feature_max_observed_game_date"]) < pd.to_datetime(result["game_date"])).all():
        raise DataQualityError("NBA daily dataset: feature_max_observed_game_date no es estrictamente anterior a game_date")

    # Calcular diferenciales (Home - Away)
    result["diff_days_rest"] = result["home_days_rest"] - result["away_days_rest"]
    result["diff_is_back_to_back"] = result["home_is_back_to_back"] - result["away_is_back_to_back"]
    result["diff_is_3_in_4"] = result["home_is_3_in_4"] - result["away_is_3_in_4"]
    result["diff_pace"] = result["home_rolling_pace"] - result["away_rolling_pace"]
    result["diff_off_rating"] = result["home_rolling_off_rating"] - result["away_rolling_off_rating"]
    result["diff_def_rating"] = result["home_rolling_def_rating"] - result["away_rolling_def_rating"]
    result["diff_net_rating"] = result["home_rolling_net_rating"] - result["away_rolling_net_rating"]
    result["diff_win_rate"] = result["home_win_rate"] - result["away_win_rate"]
    result["diff_avg_margin"] = result["home_avg_margin"] - result["away_avg_margin"]
    result["diff_avg_points_for"] = result["home_avg_points_for"] - result["away_avg_points_for"]

    result["target_home_win"] = result["home_win"].astype(int)

    features = nba_daily_feature_columns()
    numeric_values = result[features].apply(pd.to_numeric, errors="coerce")
    if numeric_values.isna().any().any() or not numeric_values.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError("NBA daily dataset: features no finitas o incompletas")
    result[features] = numeric_values

    metadata = {
        "sport": "NBA",
        "dataset_type": "nba_daily_temporal",
        "source_rows": int(len(df)),
        "training_rows": int(len(result)),
        "skipped_no_history": skipped_no_history,
        "window_games": window,
        "features": features,
        "feature_rule": "daily features with .shift(1) strictly before target game_date",
    }
    return result, metadata


def build_nba_daily_matchup_features(
    completed_games: pd.DataFrame,
    upcoming_games: pd.DataFrame,
    window: int = 10,
    minimum_history: int = 1,
    as_of: Any = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Construye las features de inferencia para partidos futuros de NBA.
    - Fatiga de calendario (days_rest, is_back_to_back, is_3_in_4, games_in_last_4_days).
    - Ratings avanzados (Pace, ORtg, DRtg, NetRtg) rodantes desde juegos completados hasta as_of.
    - Cero variables de NFL.
    - Verificación temporal estricta: feature_max_observed_game_date < game_date.
    """
    if window < 1 or minimum_history < 1:
        raise ValueError("window and minimum_history must be positive")

    past_required = {"game_id", "game_date", "home_team_id", "away_team_id", "home_score", "away_score", "home_win"}
    target_required = {"game_id", "game_date", "home_team_id", "away_team_id"}
    missing_past = past_required.difference(completed_games.columns)
    missing_target = target_required.difference(upcoming_games.columns)
    if missing_past or missing_target:
        raise DataQualityError(
            f"Features NBA de inferencia incompletas; historial={sorted(missing_past)}, schedule={sorted(missing_target)}"
        )

    as_of_ts = pd.to_datetime(as_of or datetime.now(timezone.utc), utc=True)
    as_of_date = as_of_ts.date()

    past = _ensure_nba_game_ratings(completed_games.copy())
    past["game_date"] = pd.to_datetime(past["game_date"], errors="coerce").dt.date
    targets = upcoming_games.copy()
    targets["game_date"] = pd.to_datetime(targets["game_date"], errors="coerce").dt.date

    # Validar targets futuros
    target_dates = targets["game_date"]
    kickoffs = targets["game_start_utc"] if "game_start_utc" in targets else pd.Series(pd.NaT, index=targets.index)
    same_day_kickoff = pd.to_datetime(kickoffs, errors="coerce", utc=True)
    is_future = (target_dates > as_of_date) | (
        (target_dates == as_of_date) & same_day_kickoff.notna() & (same_day_kickoff > as_of_ts)
    )
    if not targets.empty and not is_future.all():
        raise DataQualityError("El schedule NBA de inferencia contiene partidos no futuros al cutoff as_of")

    # Filtrar historial observado
    observed = past[past["game_date"] <= as_of_date].copy()
    if "game_start_utc" in observed.columns:
        past_kickoffs = pd.to_datetime(observed["game_start_utc"], errors="coerce", utc=True)
        if (past_kickoffs > as_of_ts).any():
            raise DataQualityError("completed_games NBA contiene partidos con kickoff posterior a as_of")

    # Construir historial por equipo ordenado cronológicamente
    history: dict[str, list[dict]] = defaultdict(list)
    for game in observed.sort_values(["game_date", "game_id"]).to_dict("records"):
        h_id, a_id = str(game["home_team_id"]), str(game["away_team_id"])
        gdate = game["game_date"]
        hs, aw = float(game["home_score"]), float(game["away_score"])
        hw = float(game["home_win"])
        base_h = {
            "game_date": gdate, "outcome": hw, "margin": hs - aw,
            "points_for": hs, "points_against": aw,
            "pace": float(game["home_pace"]),
            "off_rating": float(game["home_off_rating"]),
            "def_rating": float(game["home_def_rating"]),
            "net_rating": float(game["home_net_rating"]),
            "fg_pct": _require_nba_stat(game, ("home_fg_pct",), "home_fg_pct"),
            "offensive_rebounds": _require_nba_stat(game, ("home_offensive_rebounds",), "home_offensive_rebounds"),
            "total_rebounds": _require_nba_stat(game, ("home_total_rebounds",), "home_total_rebounds"),
            "turnovers": _require_nba_stat(game, ("home_turnovers",), "home_turnovers"),
        }
        base_a = {
            "game_date": gdate, "outcome": 1.0 - hw, "margin": aw - hs,
            "points_for": aw, "points_against": hs,
            "pace": float(game["away_pace"]),
            "off_rating": float(game["away_off_rating"]),
            "def_rating": float(game["away_def_rating"]),
            "net_rating": float(game["away_net_rating"]),
            "fg_pct": _require_nba_stat(game, ("away_fg_pct",), "away_fg_pct"),
            "offensive_rebounds": _require_nba_stat(game, ("away_offensive_rebounds",), "away_offensive_rebounds"),
            "total_rebounds": _require_nba_stat(game, ("away_total_rebounds",), "away_total_rebounds"),
            "turnovers": _require_nba_stat(game, ("away_turnovers",), "away_turnovers"),
        }
        history[h_id].append(base_h)
        history[a_id].append(base_a)

    def _calc_nba_side_features(side: str, team_history: list[dict], target_date) -> dict:
        prior = [item for item in team_history if item["game_date"] < target_date]
        count = len(prior)
        if count == 0:
            raise DataQualityError("No hay historial previo para el equipo")
        last_g = prior[-1]
        raw_days_rest = (target_date - last_g["game_date"]).days
        is_b2b = int(raw_days_rest == 1)
        days_rest = float(min(max(raw_days_rest, 1), 10))
        if count >= 2:
            prev_2_days = (target_date - prior[-2]["game_date"]).days
            is_3_in_4 = int(prev_2_days <= 3)
        else:
            is_3_in_4 = 0
        games_last_4 = sum(1 for item in prior if 0 < (target_date - item["game_date"]).days <= 3)

        w_prior = prior[-window:]
        w_len = len(w_prior)
        res = {
            f"{side}_days_rest": days_rest,
            f"{side}_is_back_to_back": is_b2b,
            f"{side}_is_3_in_4": is_3_in_4,
            f"{side}_games_in_last_4_days": games_last_4,
            f"{side}_games_prior": count,
            f"{side}_win_rate": sum(x["outcome"] for x in w_prior) / w_len,
            f"{side}_avg_margin": sum(x["margin"] for x in w_prior) / w_len,
            f"{side}_avg_points_for": sum(x["points_for"] for x in w_prior) / w_len,
            f"{side}_avg_points_against": sum(x["points_against"] for x in w_prior) / w_len,
            f"{side}_rolling_pace": sum(x["pace"] for x in w_prior) / w_len,
            f"{side}_rolling_off_rating": sum(x["off_rating"] for x in w_prior) / w_len,
            f"{side}_rolling_def_rating": sum(x["def_rating"] for x in w_prior) / w_len,
            f"{side}_rolling_net_rating": (sum(x["off_rating"] for x in w_prior) - sum(x["def_rating"] for x in w_prior)) / w_len,
            f"{side}_avg_fg_pct": sum(x["fg_pct"] for x in w_prior) / w_len,
            f"{side}_avg_offensive_rebounds": sum(x["offensive_rebounds"] for x in w_prior) / w_len,
            f"{side}_avg_total_rebounds": sum(x["total_rebounds"] for x in w_prior) / w_len,
            f"{side}_avg_turnovers": sum(x["turnovers"] for x in w_prior) / w_len,
            f"{side}_max_obs": last_g["game_date"],
        }
        return res

    rows = []
    skipped_games = []
    for target in targets.sort_values(["game_date", "game_id"]).to_dict("records"):
        target_date = target["game_date"]
        h_id, a_id = str(target["home_team_id"]), str(target["away_team_id"])
        h_prior = [x for x in history[h_id] if x["game_date"] < target_date]
        a_prior = [x for x in history[a_id] if x["game_date"] < target_date]
        if min(len(h_prior), len(a_prior)) < minimum_history:
            skipped_games.append({"game_id": str(target["game_id"]), "reason": "insufficient_history"})
            continue

        h_feat = _calc_nba_side_features("home", history[h_id], target_date)
        a_feat = _calc_nba_side_features("away", history[a_id], target_date)

        max_obs = max(h_feat.pop("home_max_obs"), a_feat.pop("away_max_obs"))
        row = {
            "game_id": str(target["game_id"]),
            "game_date": target_date.isoformat(),
            "feature_cutoff_exclusive": target_date.isoformat(),
            "feature_max_observed_game_date": max_obs.isoformat(),
            "home_team_id": h_id,
            "away_team_id": a_id,
        }
        for col in ("game_start_utc", "season", "home_team_name", "away_team_name", "home_team_abbreviation", "away_team_abbreviation", "source_ref"):
            if col in target:
                row[col] = target[col]
        row.update(h_feat)
        row.update(a_feat)

        row["diff_days_rest"] = row["home_days_rest"] - row["away_days_rest"]
        row["diff_is_back_to_back"] = row["home_is_back_to_back"] - row["away_is_back_to_back"]
        row["diff_is_3_in_4"] = row["home_is_3_in_4"] - row["away_is_3_in_4"]
        row["diff_pace"] = row["home_rolling_pace"] - row["away_rolling_pace"]
        row["diff_off_rating"] = row["home_rolling_off_rating"] - row["away_rolling_off_rating"]
        row["diff_def_rating"] = row["home_rolling_def_rating"] - row["away_rolling_def_rating"]
        row["diff_net_rating"] = row["home_rolling_net_rating"] - row["away_rolling_net_rating"]
        row["diff_win_rate"] = row["home_win_rate"] - row["away_win_rate"]
        row["diff_avg_margin"] = row["home_avg_margin"] - row["away_avg_margin"]
        row["diff_avg_points_for"] = row["home_avg_points_for"] - row["away_avg_points_for"]
        rows.append(row)

    if not rows:
        raise DataQualityError(f"NBA daily matchup features: ningún partido con historial suficiente: {skipped_games[:5]}")

    result = pd.DataFrame(rows)
    if not (pd.to_datetime(result["feature_max_observed_game_date"]) < pd.to_datetime(result["game_date"])).all():
        raise DataQualityError("Inferencia NBA: feature_max_observed_game_date no es estrictamente anterior a game_date")

    features = nba_daily_feature_columns()
    numeric_values = result[features].apply(pd.to_numeric, errors="coerce")
    if numeric_values.isna().any().any() or not numeric_values.map(lambda x: math.isfinite(float(x))).all().all():
        raise DataQualityError("Inferencia NBA: features no finitas o incompletas")
    result[features] = numeric_values

    return result, {
        "features": features,
        "window_games": window,
        "feature_rule": "daily features strictly before target game_date",
        "max_observed_game_date": result["feature_max_observed_game_date"].max(),
        "skipped_games": skipped_games,
    }
