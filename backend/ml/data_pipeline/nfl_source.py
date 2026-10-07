"""NFL schedules and per-game team metrics from nflverse via nflreadpy."""
from __future__ import annotations

import importlib.metadata
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from ml.data_pipeline.common import DataQualityError, manifest_path as resolve_manifest_path, project_root, stored_path, verify_file, write_manifest

# nflverse updates play-by-play franchise abbreviations while historical schedule
# rows retain the abbreviation used at game time. Normalize both to one franchise.
NFL_TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}


def _team_id(value: Any) -> str:
    team = str(value)
    return NFL_TEAM_ALIASES.get(team, team)


def _persist_frame(frame: pl.DataFrame, directory: Path, stem: str, metadata: dict) -> dict:
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    payload = buffer.getvalue()
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stem}_{digest[:16]}.parquet"
    if not target.exists():
        temp = target.with_suffix(".parquet.tmp")
        temp.write_bytes(payload)
        temp.replace(target)
    return {**metadata, "path": stored_path(target), "sha256": digest,
            "bytes": len(payload), "rows": frame.height}


def sync_nfl_source(seasons: list[int], raw_root: Path | None = None) -> dict[str, Any]:
    """Persist real nflverse schedules and play-by-play by season with content hashes."""
    try:
        import nflreadpy
    except ImportError as exc:
        raise RuntimeError("Instala backend/requirements.txt para obtener nflreadpy") from exc
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    files = []
    selected_seasons = sorted(set(seasons))
    previous_manifest = raw_root / "manifest.json"
    retained = []
    if previous_manifest.is_file():
        previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
        retained = [item for item in previous.get("files", [])
                    if item.get("season") not in selected_seasons]
    version = importlib.metadata.version("nflreadpy")
    for season in selected_seasons:
        schedules = nflreadpy.load_schedules(seasons=season)
        pbp = nflreadpy.load_pbp(seasons=season)
        files.append(_persist_frame(schedules, raw_root / str(season), "schedules", {
            "dataset": "schedules", "season": season, "package": "nflreadpy", "package_version": version,
        }))
        files.append(_persist_frame(pbp, raw_root / str(season), "pbp", {
            "dataset": "pbp", "season": season, "package": "nflreadpy", "package_version": version,
        }))
    manifest = {
        "source": "nflverse via nflreadpy",
        "source_home": "https://github.com/nflverse/nflreadpy",
        "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "seasons": sorted({item["season"] for item in retained + files}),
        "package_version": version, "files": retained + files,
    }
    write_manifest(raw_root / "manifest.json", manifest)
    return manifest


def load_nfl_upcoming_games(
    as_of: pd.Timestamp | None = None, raw_root: Path | None = None,
) -> pd.DataFrame:
    """Read real, unscored regular-season schedule entries after a cutoff date."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nfl_source")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cutoff_date = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC")).date()
    schedule_items = [item for item in manifest["files"] if item.get("dataset") == "schedules"]
    records = []
    required = {"game_id", "season", "game_type", "gameday", "home_team", "away_team", "home_score", "away_score"}
    for item in schedule_items:
        path = resolve_manifest_path(item["path"])
        verify_file(path, item["sha256"])
        schedule = pd.DataFrame(pl.read_parquet(path).to_dicts())
        missing = required.difference(schedule.columns)
        if missing:
            raise DataQualityError(f"NFL upcoming schedule {item['season']}: faltan {sorted(missing)}")
        games = schedule[schedule["game_type"].eq("REG")].copy()
        for game in games.to_dict("records"):
            date = pd.to_datetime(game["gameday"], errors="coerce")
            if pd.isna(date):
                raise DataQualityError(f"NFL schedule {game['game_id']}: fecha ausente")
            if date.date() <= cutoff_date or pd.notna(game["home_score"]) or pd.notna(game["away_score"]):
                continue
            if not game["home_team"] or not game["away_team"]:
                raise DataQualityError(f"NFL schedule {game['game_id']}: equipos ausentes")
            records.append({
                "game_id": str(game["game_id"]), "game_date": date.date(),
                "game_start_utc": pd.NaT, "season": int(game["season"]),
                "home_team_id": _team_id(game["home_team"]),
                "away_team_id": _team_id(game["away_team"]),
                "home_team_name": _team_id(game["home_team"]),
                "away_team_name": _team_id(game["away_team"]),
                "home_team_abbreviation": _team_id(game["home_team"]),
                "away_team_abbreviation": _team_id(game["away_team"]),
                "source": "nflverse schedule",
                "source_ref": f"nflverse:schedule:{item['season']}:{item['sha256']}",
            })
    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(columns=["game_id", "game_date", "game_start_utc", "season",
                                     "home_team_id", "away_team_id", "home_team_name", "away_team_name",
                                     "home_team_abbreviation", "away_team_abbreviation", "source", "source_ref"])
    if result["game_id"].duplicated().any():
        raise DataQualityError("NFL upcoming schedule contiene game_id duplicados")
    result = result.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    result.attrs["schedule_retrieved_at"] = manifest.get("retrieved_at")
    return result


def load_nfl_games(raw_root: Path | None = None) -> pd.DataFrame:
    """Join final nflverse schedules to team PBP summaries, preserving only observed inputs."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nfl_source primero")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schedules: dict[int, pd.DataFrame] = {}
    pbp_by_season: dict[int, pd.DataFrame] = {}
    source_hash: dict[int, str] = {}
    for item in manifest["files"]:
        resolved_path = resolve_manifest_path(item["path"])
        verify_file(resolved_path, item["sha256"])
        frame = pl.read_parquet(resolved_path).to_dicts()
        if item["dataset"] == "schedules":
            schedules[item["season"]] = pd.DataFrame(frame)
            source_hash[item["season"]] = item["sha256"]
        elif item["dataset"] == "pbp":
            pbp_by_season[item["season"]] = pd.DataFrame(frame)
            source_hash[item["season"]] += ":" + item["sha256"]

    schedule_required = {
        "game_id", "season", "game_type", "gameday", "gametime", "away_team",
        "home_team", "away_score", "home_score",
    }
    pbp_required = {"game_id", "posteam", "defteam", "epa", "success"}
    records = []
    for season in sorted(schedules):
        schedule = schedules[season]
        pbp = pbp_by_season.get(season)
        missing_schedule = schedule_required.difference(schedule.columns)
        if missing_schedule:
            raise DataQualityError(f"NFL schedule {season}: faltan columnas {sorted(missing_schedule)}")
        if pbp is None:
            raise DataQualityError(f"NFL {season}: falta PBP")
        missing_pbp = pbp_required.difference(pbp.columns)
        if missing_pbp:
            raise DataQualityError(f"NFL PBP {season}: faltan columnas {sorted(missing_pbp)}")
        games = schedule[
            schedule["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])
            & schedule["home_score"].notna()
            & schedule["away_score"].notna()
        ].copy()
        games["game_id"] = games["game_id"].astype(str)
        if games["game_id"].duplicated().any():
            raise DataQualityError(f"NFL {season}: game_id duplicado en schedules")
        # A play is observed only if offense, defense and both EPA/success values exist.
        valid = pbp[
            pbp["posteam"].notna() & pbp["defteam"].notna()
            & pbp["epa"].notna() & pbp["success"].notna()
        ].copy()
        valid["game_id"] = valid["game_id"].astype(str)
        valid["posteam"] = valid["posteam"].map(_team_id)
        valid["defteam"] = valid["defteam"].map(_team_id)
        # Aggregate the season once; filtering all plays once per game was quadratic.
        off = valid.groupby(["game_id", "posteam"], sort=False).agg(
            epa=("epa", "mean"), success=("success", "mean"), plays=("epa", "size")
        )
        defense = valid.groupby(["game_id", "defteam"], sort=False).agg(
            epa_allowed=("epa", "mean"), success_allowed=("success", "mean"), plays=("epa", "size")
        )
        for game in games.itertuples(index=False):
            game_id = str(game.game_id)
            home_team_id, away_team_id = _team_id(game.home_team), _team_id(game.away_team)
            keys = [(game_id, team) for team in (home_team_id, away_team_id)]
            if any(key not in off.index or key not in defense.index for key in keys):
                raise DataQualityError(f"NFL {game_id}: PBP insuficiente para ambas ofensivas/defensivas")
            home_off, away_off = off.loc[keys[0]], off.loc[keys[1]]
            home_def, away_def = defense.loc[keys[0]], defense.loc[keys[1]]
            gameday = pd.to_datetime(game.gameday, errors="coerce")
            if pd.isna(gameday):
                raise DataQualityError(f"NFL {game_id}: gameday ausente")
            home_score, away_score = int(game.home_score), int(game.away_score)
            tie = home_score == away_score
            records.append({
                "game_id": game_id, "game_date": gameday.date(), "game_start_utc": pd.NaT,
                "season": int(game.season), "season_type": str(game.game_type),
                "home_team_id": home_team_id, "away_team_id": away_team_id,
                "home_score": home_score, "away_score": away_score,
                "home_win": None if tie else int(home_score > away_score),
                "is_tie": tie,
                "home_off_epa_per_play": float(home_off["epa"]),
                "away_off_epa_per_play": float(away_off["epa"]),
                "home_off_success_rate": float(home_off["success"]),
                "away_off_success_rate": float(away_off["success"]),
                "home_def_epa_allowed_per_play": float(home_def["epa_allowed"]),
                "away_def_epa_allowed_per_play": float(away_def["epa_allowed"]),
                "home_def_success_allowed": float(home_def["success_allowed"]),
                "away_def_success_allowed": float(away_def["success_allowed"]),
                "source": "nflverse via nflreadpy",
                "source_ref": f"nflverse:{season}:{source_hash[season]}",
            })
    result = pd.DataFrame(records)
    if result.empty:
        raise DataQualityError("NFL: no hay partidos completados con PBP válido")
    if result["game_id"].duplicated().any():
        raise DataQualityError("NFL: game_id duplicado después de normalizar")
    return result.sort_values(["game_date", "game_id"]).reset_index(drop=True)
