"""Free historical NBA schedule and per-game box scores from ESPN releases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from ml.data_pipeline.common import (
    DataQualityError,
    download_versioned_asset,
    project_root,
    manifest_path as resolve_manifest_path,
    require_columns,
    require_complete,
    verify_file,
    write_manifest,
)

NBA_RELEASES = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"


def sync_nba_upcoming_schedules(seasons: list[int], raw_root: Path | None = None) -> dict[str, Any]:
    """Download schedule-only snapshots for future seasons without requiring boxscores."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nba_espn_upcoming"
    files = []
    for season in sorted(set(seasons)):
        filename = f"nba_schedule_{season}.parquet"
        url = f"{NBA_RELEASES}/espn_nba_schedules/{filename}"
        item = download_versioned_asset(url, raw_root / str(season), f"nba_schedule_{season}")
        item.update({"dataset": "espn_nba_schedules", "season": season})
        files.append(item)
    manifest = {
        "source": "SportsDataverse ESPN NBA schedules",
        "source_home": "https://github.com/sportsdataverse/sportsdataverse-data",
        "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "seasons": sorted(set(seasons)), "files": files,
    }
    write_manifest(raw_root / "manifest.json", manifest)
    return manifest


def load_nba_upcoming_games(
    as_of: pd.Timestamp | None = None, raw_root: Path | None = None,
) -> pd.DataFrame:
    """Read scheduled regular-season NBA games strictly after the supplied cutoff date."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nba_espn_upcoming"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nba_upcoming_schedules")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cutoff = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC"))
    cutoff_date = cutoff.date()
    records = []
    skipped_incomplete = []
    required = {
        "game_id", "season", "season_type", "game_date", "game_date_time",
        "status_type_completed", "home_id", "away_id",
    }
    for item in manifest["files"]:
        path = resolve_manifest_path(item["path"])
        verify_file(path, item["sha256"])
        schedule = pd.DataFrame(pl.read_parquet(path).to_dicts())
        require_columns(schedule, required, f"NBA upcoming schedule {item['season']}")
        future = schedule[
            schedule["season_type"].eq(2)
            & ~schedule["status_type_completed"].eq(True)
        ].copy()
        for game in future.to_dict("records"):
            date = pd.to_datetime(game["game_date"], errors="coerce")
            start = pd.to_datetime(game["game_date_time"], utc=True, errors="coerce")
            if pd.isna(date) or pd.isna(start) or pd.isna(game["home_id"]) or pd.isna(game["away_id"]):
                skipped_incomplete.append({"game_id": str(game.get("game_id")), "reason": "date_or_team_id_missing"})
                continue
            if date.date() <= cutoff_date:
                continue
            if int(game["home_id"]) <= 0 or int(game["away_id"]) <= 0:
                skipped_incomplete.append({"game_id": str(game["game_id"]), "reason": "teams_not_announced_by_source"})
                continue
            records.append({
                "game_id": str(game["game_id"]), "game_date": date.date(),
                "game_start_utc": start, "season": int(game["season"]),
                "home_team_id": str(game["home_id"]), "away_team_id": str(game["away_id"]),
                "home_team_name": game.get("home_display_name") or game.get("home_name"),
                "away_team_name": game.get("away_display_name") or game.get("away_name"),
                "home_team_abbreviation": game.get("home_abbreviation"),
                "away_team_abbreviation": game.get("away_abbreviation"),
                "source": "SportsDataverse ESPN schedule",
                "source_ref": f"sportsdataverse:espn_nba_schedule:{item['season']}:{item['sha256']}",
            })
    result = pd.DataFrame(records)
    if result.empty:
        result = pd.DataFrame(columns=["game_id", "game_date", "game_start_utc", "season",
                                       "home_team_id", "away_team_id", "home_team_name", "away_team_name",
                                       "home_team_abbreviation", "away_team_abbreviation", "source", "source_ref"])
        result.attrs["skipped_incomplete"] = skipped_incomplete
        result.attrs["schedule_retrieved_at"] = manifest.get("retrieved_at")
        return result
    if result["game_id"].duplicated().any():
        raise DataQualityError("NBA upcoming schedule contiene game_id duplicados")
    result = result.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    result.attrs["skipped_incomplete"] = skipped_incomplete
    result.attrs["schedule_retrieved_at"] = manifest.get("retrieved_at")
    return result


def sync_nba_source(seasons: list[int], raw_root: Path | None = None) -> dict[str, Any]:
    """Fetch checksummed ESPN schedule and team-boxscore Parquets by NBA season end year."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nba_espn"
    files = []
    for season in sorted(set(seasons)):
        for dataset, filename in (
            ("espn_nba_schedules", f"nba_schedule_{season}.parquet"),
            ("espn_nba_team_boxscores", f"team_box_{season}.parquet"),
        ):
            url = f"{NBA_RELEASES}/{dataset}/{filename}"
            item = download_versioned_asset(url, raw_root / str(season), filename[:-8])
            item.update({"dataset": dataset, "season": season})
            files.append(item)
    manifest = {
        "source": "SportsDataverse ESPN NBA releases",
        "source_home": "https://github.com/sportsdataverse/sportsdataverse-data",
        "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "seasons": sorted(set(seasons)),
        "files": files,
    }
    write_manifest(raw_root / "manifest.json", manifest)
    return manifest


def load_nba_games(raw_root: Path | None = None) -> pd.DataFrame:
    """Join completed regular-season schedules to the two real team box scores."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nba_espn"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nba_source primero")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schedules: dict[int, pd.DataFrame] = {}
    boxes: dict[int, pd.DataFrame] = {}
    source_hash: dict[int, str] = {}
    for item in manifest["files"]:
        resolved_path = resolve_manifest_path(item["path"])
        verify_file(resolved_path, item["sha256"])
        if item["dataset"] == "espn_nba_schedules":
            schedules[item["season"]] = pd.DataFrame(pl.read_parquet(resolved_path).to_dicts())
            source_hash[item["season"]] = item["sha256"]
        elif item["dataset"] == "espn_nba_team_boxscores":
            boxes[item["season"]] = pd.DataFrame(pl.read_parquet(resolved_path).to_dicts())
            source_hash[item["season"]] += ":" + item["sha256"]

    records = []
    schedule_fields = {
        "game_id", "season", "season_type", "game_date", "game_date_time",
        "status_type_completed", "home_id", "away_id", "home_score", "away_score",
    }
    box_fields = {
        "game_id", "season", "team_id", "team_score", "opponent_team_score",
        "field_goals_made", "field_goals_attempted", "free_throws_attempted",
        "offensive_rebounds", "total_rebounds", "turnovers",
    }
    for season in sorted(schedules):
        schedule = schedules[season].copy()
        box = boxes.get(season)
        if box is None:
            raise DataQualityError(f"NBA {season}: no se encontró el boxscore publicado")
        require_columns(schedule, schedule_fields, f"NBA schedule {season}")
        require_columns(box, box_fields, f"NBA team box {season}")
        schedule = schedule[
            schedule["status_type_completed"].eq(True)
            & schedule["season_type"].eq(2)
            & schedule["home_score"].notna()
            & schedule["away_score"].notna()
        ].copy()
        schedule["game_id"] = schedule["game_id"].astype(str)
        box["game_id"] = box["game_id"].astype(str)
        box["team_id"] = box["team_id"].astype(str)
        schedule["home_id"] = schedule["home_id"].astype(str)
        schedule["away_id"] = schedule["away_id"].astype(str)
        duplicate_ids = schedule["game_id"].duplicated(keep=False)
        if duplicate_ids.any():
            raise DataQualityError(f"NBA {season}: calendario duplica {int(duplicate_ids.sum())} ids")
        box_by_game = {key: group for key, group in box.groupby("game_id", sort=False)}
        for game in schedule.itertuples(index=False):
            game_id = str(game.game_id)
            team_rows = box_by_game.get(game_id)
            if team_rows is None or len(team_rows) != 2:
                raise DataQualityError(f"NBA {game_id}: se esperaban 2 boxscores; recibidos {0 if team_rows is None else len(team_rows)}")
            by_id = {str(row.team_id): row for row in team_rows.itertuples(index=False)}
            home = by_id.get(str(game.home_id))
            away = by_id.get(str(game.away_id))
            if home is None or away is None:
                raise DataQualityError(f"NBA {game_id}: ids de equipos de schedule y boxscore no coinciden")
            stats = [
                home.field_goals_made, home.field_goals_attempted,
                home.free_throws_attempted, home.offensive_rebounds,
                home.total_rebounds, home.turnovers,
                away.field_goals_made, away.field_goals_attempted,
                away.free_throws_attempted, away.offensive_rebounds,
                away.total_rebounds, away.turnovers,
            ]
            if any(pd.isna(value) for value in stats):
                raise DataQualityError(f"NBA {game_id}: boxscore incompleto; no se rellenará")
            if int(home.team_score) != int(game.home_score) or int(away.team_score) != int(game.away_score):
                raise DataQualityError(f"NBA {game_id}: marcador de calendario y boxscore no coincide")
            if int(home.field_goals_attempted) <= 0 or int(away.field_goals_attempted) <= 0:
                raise DataQualityError(f"NBA {game_id}: intentos de campo inválidos")
            dt = pd.to_datetime(game.game_date_time, utc=True, errors="coerce")
            if pd.isna(dt):
                raise DataQualityError(f"NBA {game_id}: falta hora del partido para el cutoff temporal")
            records.append({
                "game_id": game_id,
                "game_date": pd.Timestamp(game.game_date).date(),
                "game_start_utc": dt,
                "season": int(game.season),
                "home_team_id": str(game.home_id),
                "away_team_id": str(game.away_id),
                "home_score": int(game.home_score),
                "away_score": int(game.away_score),
                "home_win": int(game.home_score > game.away_score),
                "home_fg_pct": float(home.field_goals_made / home.field_goals_attempted),
                "away_fg_pct": float(away.field_goals_made / away.field_goals_attempted),
                "home_offensive_rebounds": float(home.offensive_rebounds),
                "away_offensive_rebounds": float(away.offensive_rebounds),
                "home_total_rebounds": float(home.total_rebounds),
                "away_total_rebounds": float(away.total_rebounds),
                "home_turnovers": float(home.turnovers),
                "away_turnovers": float(away.turnovers),
                "source": "ESPN via SportsDataverse",
                "source_ref": f"sportsdataverse:espn_nba:{season}:{source_hash[season]}",
            })
    result = pd.DataFrame(records)
    if result.empty:
        raise DataQualityError("NBA: no hay partidos completados con boxscores válidos")
    if result["game_id"].duplicated().any():
        raise DataQualityError("NBA: game_id duplicado después de normalizar")
    return result.sort_values(["game_start_utc", "game_id"]).reset_index(drop=True)
