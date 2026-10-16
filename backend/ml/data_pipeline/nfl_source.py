from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import importlib.metadata
import io
import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import polars as pl

from ml.data_pipeline.common import DataQualityError, manifest_path as resolve_manifest_path, project_root, stored_path, verify_file, write_manifest

# nflverse updates play-by-play franchise abbreviations while historical schedule
# rows retain the abbreviation used at game time. Normalize both to one franchise.
NFL_TEAM_ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}


def _team_id(value: Any) -> str:
    team = str(value)
    return NFL_TEAM_ALIASES.get(team, team)


def resolve_nfl_kickoff_utc(gameday: Any, gametime: Any, game_id: str = "") -> pd.Timestamp:
    """Resuelve el kickoff UTC inequívoco a partir de gameday (YYYY-MM-DD) y gametime (HH:MM ET).

    nflverse publica gametime en hora del Este (America/New_York).
    Falla cerrado levantando DataQualityError si gameday o gametime faltan o son inválidos.
    """
    if pd.isna(gameday) or not str(gameday).strip():
        raise DataQualityError(f"NFL {game_id}: gameday ausente; no se puede resolver kickoff UTC")
    if pd.isna(gametime) or not str(gametime).strip():
        raise DataQualityError(f"NFL {game_id}: gametime ausente; no se puede resolver kickoff UTC")

    clean_day = str(gameday).strip().split(" ")[0].split("T")[0]
    clean_time = str(gametime).strip()
    parts = clean_time.split(":")
    if len(parts) < 2:
        raise DataQualityError(f"NFL {game_id}: gametime '{gametime}' con formato inválido; se espera HH:MM")

    hh, mm = parts[0].zfill(2), parts[1].zfill(2)
    dt_str = f"{clean_day} {hh}:{mm}"
    try:
        dt_naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        dt_et = dt_naive.replace(tzinfo=ZoneInfo("America/New_York"))
        dt_utc = dt_et.astimezone(timezone.utc)
        return pd.Timestamp(dt_utc)
    except Exception as exc:
        raise DataQualityError(f"NFL {game_id}: error parseando kickoff UTC para '{dt_str}': {exc}") from exc


def _persist_frame(frame: pl.DataFrame | pd.DataFrame, directory: Path, stem: str, metadata: dict) -> dict:
    if isinstance(frame, pd.DataFrame):
        frame = pl.from_pandas(frame)
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
    return {
        **metadata,
        "path": stored_path(target),
        "sha256": digest,
        "bytes": len(payload),
        "rows": frame.height,
        "columns": frame.columns,
        "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }


def sync_nfl_source(
    seasons: list[int],
    datasets: list[str] | None = None,
    raw_root: Path | None = None,
) -> dict[str, Any]:
    """Persist real nflverse schedules, play-by-play, player stats, snaps, injuries and rosters by season with content hashes."""
    try:
        import nflreadpy
    except ImportError as exc:
        raise RuntimeError("Instala backend/requirements.txt para obtener nflreadpy") from exc
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    files: list[dict] = []
    selected_seasons = sorted(set(seasons))
    target_datasets = set(datasets or ["schedules", "pbp", "player_stats", "snap_counts", "injuries", "rosters_weekly", "players_crosswalk"])
    previous_manifest = raw_root / "manifest.json"
    retained: list[dict] = []
    existing_by_key: dict[tuple[str, Any], dict] = {}
    if previous_manifest.is_file():
        previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
        for item in previous.get("files", []):
            k = (item.get("dataset"), item.get("season"))
            existing_by_key[k] = item
            # No retener si el dataset/temporada está en target_datasets y se va a sincronizar ahora
            if item.get("dataset") in {"players", "players_crosswalk"} and (
                "players" in target_datasets or "players_crosswalk" in target_datasets
            ):
                continue
            if item.get("season") in selected_seasons and item.get("dataset") in target_datasets:
                continue
            retained.append(item)

    version = importlib.metadata.version("nflreadpy")

    # 1. Sincronizar crosswalk de jugadores si está solicitado
    if "players" in target_datasets or "players_crosswalk" in target_datasets:
        existing = existing_by_key.get(("players_crosswalk", None))
        if existing and (resolve_manifest_path(existing["path"])).is_file():
            files.append(existing)
        else:
            try:
                players_df = nflreadpy.load_players()
                files.append(_persist_frame(players_df, raw_root, "players", {
                    "dataset": "players_crosswalk",
                    "season": None,
                    "package": "nflreadpy",
                    "package_version": version,
                    "provider": "nflverse via nflreadpy",
                }))
            except Exception as exc:
                files.append({
                    "dataset": "players_crosswalk",
                    "season": None,
                    "error": str(exc),
                    "package": "nflreadpy",
                    "package_version": version,
                })

    # 2. Sincronizar datasets por temporada
    for season in selected_seasons:
        season_dir = raw_root / str(season)

        if "schedules" in target_datasets:
            # Reutilizar si ya existe en manifest y en disco
            existing = existing_by_key.get(("schedules", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                schedules = nflreadpy.load_schedules(seasons=season)
                files.append(_persist_frame(schedules, season_dir, "schedules", {
                    "dataset": "schedules", "season": season, "package": "nflreadpy",
                    "package_version": version, "provider": "nflverse via nflreadpy",
                }))

        if "pbp" in target_datasets:
            existing = existing_by_key.get(("pbp", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                pbp = nflreadpy.load_pbp(seasons=season)
                files.append(_persist_frame(pbp, season_dir, "pbp", {
                    "dataset": "pbp", "season": season, "package": "nflreadpy",
                    "package_version": version, "provider": "nflverse via nflreadpy",
                }))

        if "player_stats" in target_datasets:
            existing = existing_by_key.get(("player_stats", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                try:
                    pstats = nflreadpy.load_player_stats(seasons=season, summary_level="week")
                    files.append(_persist_frame(pstats, season_dir, "player_stats", {
                        "dataset": "player_stats", "season": season, "package": "nflreadpy",
                        "package_version": version, "provider": "nflverse via nflreadpy",
                    }))
                except Exception as exc:
                    files.append({
                        "dataset": "player_stats", "season": season, "error": str(exc),
                        "package": "nflreadpy", "package_version": version,
                    })

        if "snap_counts" in target_datasets:
            existing = existing_by_key.get(("snap_counts", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                try:
                    snaps = nflreadpy.load_snap_counts(seasons=season)
                    files.append(_persist_frame(snaps, season_dir, "snap_counts", {
                        "dataset": "snap_counts", "season": season, "package": "nflreadpy",
                        "package_version": version, "provider": "nflverse via nflreadpy",
                    }))
                except Exception as exc:
                    files.append({
                        "dataset": "snap_counts", "season": season, "error": str(exc),
                        "package": "nflreadpy", "package_version": version,
                    })

        if "injuries" in target_datasets:
            existing = existing_by_key.get(("injuries", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                try:
                    inj = nflreadpy.load_injuries(seasons=season)
                    files.append(_persist_frame(inj, season_dir, "injuries", {
                        "dataset": "injuries", "season": season, "package": "nflreadpy",
                        "package_version": version, "provider": "nflverse via nflreadpy",
                    }))
                except Exception as exc:
                    files.append({
                        "dataset": "injuries", "season": season, "error": str(exc),
                        "package": "nflreadpy", "package_version": version,
                    })

        if "rosters_weekly" in target_datasets:
            existing = existing_by_key.get(("rosters_weekly", season))
            if existing and (resolve_manifest_path(existing["path"])).is_file():
                files.append(existing)
            else:
                try:
                    rw = nflreadpy.load_rosters_weekly(seasons=season)
                    files.append(_persist_frame(rw, season_dir, "rosters_weekly", {
                        "dataset": "rosters_weekly", "season": season, "package": "nflreadpy",
                        "package_version": version, "provider": "nflverse via nflreadpy",
                    }))
                except Exception as exc:
                    files.append({
                        "dataset": "rosters_weekly", "season": season, "error": str(exc),
                        "package": "nflreadpy", "package_version": version,
                    })

    # Deduplicar estrictamente por (dataset, season) para garantizar una única entrada por asset
    deduped_files: dict[tuple[str, Any], dict] = {}
    for item in retained + files:
        key = (item.get("dataset"), item.get("season"))
        deduped_files[key] = item
    all_files = list(deduped_files.values())

    manifest = {
        "source": "nflverse via nflreadpy",
        "source_home": "https://github.com/nflverse/nflreadpy",
        "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "seasons": sorted({item["season"] for item in all_files if item.get("season") is not None}),
        "package_version": version,
        "files": all_files,
    }
    write_manifest(raw_root / "manifest.json", manifest)
    return manifest


def load_nfl_snapshot(
    dataset: str,
    season: int | None = None,
    raw_root: Path | None = None,
) -> pl.DataFrame:
    """Lee un snapshot fuente de nflverse validando estrictamente su hash SHA-256 en manifest.json.

    Falla cerrado levantando DataQualityError o FileNotFoundError si el snapshot falta,
    está corrupto o su hash no coincide.
    """
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    manifest_file = raw_root / "manifest.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"No existe manifest en {manifest_file}; ejecuta sync_nfl_source")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    for item in manifest.get("files", []):
        if item.get("dataset") == dataset and item.get("season") == season:
            if "error" in item and item["error"]:
                raise DataQualityError(f"Snapshot '{dataset}' temporada {season} falló en origen: {item['error']}")
            path = resolve_manifest_path(item["path"])
            verify_file(path, item["sha256"])
            return pl.read_parquet(path)

    raise DataQualityError(
        f"Snapshot no encontrado en manifest para dataset='{dataset}', season={season}. "
        "Ejecuta sync_nfl_source para descargarlo y registrarlo de forma inmutable."
    )


def load_nfl_player_crosswalk(raw_root: Path | None = None) -> pl.DataFrame:
    """Carga la tabla de identidades y crosswalk de jugadores de la NFL validada por hash."""
    return load_nfl_snapshot(dataset="players_crosswalk", season=None, raw_root=raw_root)


def load_nfl_upcoming_games(
    as_of: pd.Timestamp | None = None, raw_root: Path | None = None,
) -> pd.DataFrame:
    """Read real, unscored regular-season schedule entries after a cutoff date."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nfl_source")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cutoff_ts = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC"))
    if cutoff_ts.tzinfo is None:
        cutoff_ts = cutoff_ts.tz_localize("UTC")
    cutoff_date = cutoff_ts.date()
    schedule_items = [item for item in manifest["files"] if item.get("dataset") == "schedules"]
    records = []
    required = {"game_id", "season", "game_type", "gameday", "gametime", "home_team", "away_team", "home_score", "away_score"}
    for item in schedule_items:
        path = resolve_manifest_path(item["path"])
        verify_file(path, item["sha256"])
        schedule = pl.read_parquet(path).to_pandas()
        missing = required.difference(schedule.columns)
        if missing:
            raise DataQualityError(f"NFL upcoming schedule {item['season']}: faltan {sorted(missing)}")
        games = schedule[schedule["game_type"].eq("REG")].copy()
        for game in games.to_dict("records"):
            date = pd.to_datetime(game["gameday"], errors="coerce")
            if pd.isna(date):
                raise DataQualityError(f"NFL schedule {game['game_id']}: fecha ausente")
            kickoff_utc = resolve_nfl_kickoff_utc(game["gameday"], game.get("gametime"), str(game["game_id"]))
            if kickoff_utc is None or kickoff_utc <= cutoff_ts:
                continue
            if as_of is None and (pd.notna(game["home_score"]) or pd.notna(game["away_score"])):
                continue
            if not game["home_team"] or not game["away_team"]:
                raise DataQualityError(f"NFL schedule {game['game_id']}: equipos ausentes")
            if date.date() < cutoff_date:
                continue
            records.append({
                "game_id": str(game["game_id"]), "game_date": date.date(),
                "game_start_utc": kickoff_utc, "season": int(game["season"]),
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


def load_nfl_schedule_results(
    as_of: pd.Timestamp | None = None, raw_root: Path | None = None,
) -> pd.DataFrame:
    """Load only completed regular-season scores from hash-verified schedules."""
    raw_root = raw_root or project_root() / "data" / "raw" / "nflverse"
    manifest_path = raw_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe {manifest_path}; ejecuta sync_nfl_source")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cutoff = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC"))
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    records = []
    source_refs = []
    for item in manifest.get("files", []):
        if item.get("dataset") != "schedules" or item.get("error") or "path" not in item:
            continue
        path = resolve_manifest_path(item["path"])
        verify_file(path, item["sha256"])
        schedule = pl.read_parquet(path).to_pandas()
        required = {"game_id", "season", "game_type", "gameday", "gametime", "home_team", "away_team", "home_score", "away_score"}
        missing = required.difference(schedule.columns)
        if missing:
            raise DataQualityError(f"NFL schedule {item.get('season')}: faltan {sorted(missing)}")
        completed = schedule[schedule["game_type"].eq("REG") & schedule["home_score"].notna() & schedule["away_score"].notna()]
        for game in completed.to_dict("records"):
            game_id = str(game.get("game_id") or "").strip()
            kickoff = resolve_nfl_kickoff_utc(game["gameday"], game.get("gametime"), game_id)
            if not game_id or kickoff is None or kickoff > cutoff:
                continue
            home_score, away_score = int(game["home_score"]), int(game["away_score"])
            records.append({
                "game_id": game_id,
                "season": int(game["season"]),
                "game_date": pd.Timestamp(game["gameday"]).date(),
                "game_start_utc": kickoff,
                "home_team_id": _team_id(game["home_team"]),
                "away_team_id": _team_id(game["away_team"]),
                "home_score": home_score,
                "away_score": away_score,
                "home_win": None if home_score == away_score else bool(home_score > away_score),
                "source_ref": f"nflverse:schedule:{item.get('season')}:{item['sha256']}",
            })
            source_refs.append(f"nflverse:schedule:{item.get('season')}:{item['sha256']}")
    result = pd.DataFrame(records)
    if not result.empty and result["game_id"].duplicated().any():
        raise DataQualityError("NFL schedule results contiene game_id duplicados")
    result.attrs["schedule_source_refs"] = sorted(set(source_refs))
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
        if item.get("dataset") not in {"schedules", "pbp"} or item.get("error") or "path" not in item:
            continue
        resolved_path = resolve_manifest_path(item["path"])
        verify_file(resolved_path, item["sha256"])
        if item["dataset"] == "schedules":
            schedules[item["season"]] = pl.read_parquet(resolved_path).to_pandas()
            prev = source_hash.get(item["season"], "")
            source_hash[item["season"]] = f"{item['sha256']}:{prev}".strip(":")
        elif item["dataset"] == "pbp":
            pbp_by_season[item["season"]] = pl.read_parquet(resolved_path).to_pandas()
            prev = source_hash.get(item["season"], "")
            source_hash[item["season"]] = f"{prev}:{item['sha256']}".strip(":")

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
            kickoff_utc = resolve_nfl_kickoff_utc(game.gameday, getattr(game, "gametime", None), game_id)
            home_score, away_score = int(game.home_score), int(game.away_score)
            tie = home_score == away_score
            records.append({
                "game_id": game_id, "game_date": gameday.date(), "game_start_utc": kickoff_utc,
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
