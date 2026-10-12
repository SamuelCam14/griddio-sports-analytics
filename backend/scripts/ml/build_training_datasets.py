"""Build strict point-in-time training tables from retained source snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from ml.data_pipeline.common import DataQualityError, project_root, stored_path, write_manifest
from ml.data_pipeline.nba_source import load_nba_games
from ml.data_pipeline.nfl_source import load_nfl_games
from ml.data_pipeline.temporal_features import (
    NFL_GAME_STATS, NBA_GAME_STATS, build_temporal_dataset, feature_columns,
)


def build(sport: str, window: int = 10, minimum_history: int = 1,
          data_root: Path | None = None, variant: str = "injury_aware") -> dict:
    sport = sport.lower()
    if variant not in {"injury_aware", "team_only"}:
        raise ValueError("variant must be injury_aware or team_only")
    if sport != "nfl" and variant != "injury_aware":
        raise ValueError("team_only variant is currently supported only for NFL")
    data_root = data_root or project_root() / "data"
    if sport == "nba":
        games, stats, raw_manifest = load_nba_games(), NBA_GAME_STATS, data_root / "raw/nba_espn/manifest.json"
    elif sport == "nfl":
        games, stats, raw_manifest = load_nfl_games(), NFL_GAME_STATS, data_root / "raw/nflverse/manifest.json"
    else:
        raise ValueError("sport must be nba or nfl")
    dataset, quality = build_temporal_dataset(games, sport, stats, window, minimum_history)

    if sport == "nfl" and variant == "injury_aware":
        from ml.data_pipeline.injury_feature_builder import build_nfl_historical_injury_features
        import structlog
        logger = structlog.get_logger()
        logger.info("Construyendo features de lesiones históricos para NFL...")
        
        # Extraer semanas y seasons disponibles
        dataset['week'] = dataset['game_id'].apply(lambda x: int(x.split('_')[1]))
        raw_manifest_data = json.loads(raw_manifest.read_text(encoding="utf-8"))
        available_by_dataset = {
            dataset_name: {
                int(item["season"])
                for item in raw_manifest_data.get("files", [])
                if item.get("dataset") == dataset_name and item.get("season") is not None
                and not item.get("error")
            }
            for dataset_name in ("player_stats", "snap_counts", "injuries", "rosters_weekly")
        }
        complete_availability_seasons = set.intersection(*available_by_dataset.values())
        seasons_to_fetch = sorted(
            int(season) for season in dataset['season'].unique()
            if int(season) in complete_availability_seasons
        )
        if not seasons_to_fetch:
            raise DataQualityError(
                "NFL: los snapshots locales no contienen temporadas completas para availability; "
                "no se imputan features faltantes."
            )
        
        injury_features = build_nfl_historical_injury_features(seasons_to_fetch)
        availability_columns = [
            'home_ppr_lost_pct', 'home_off_snaps_lost_pct', 'home_def_snaps_lost_pct',
            'away_ppr_lost_pct', 'away_off_snaps_lost_pct', 'away_def_snaps_lost_pct',
        ]
        dataset = dataset.drop(columns=availability_columns, errors='ignore')
        
        # Home Team merge
        dataset = dataset.merge(
            injury_features,
            left_on=['season', 'week', 'home_team_id'],
            right_on=['season', 'week', 'team'],
            how='left'
        ).rename(columns={
            'ppr_lost_pct': 'home_ppr_lost_pct',
            'off_snaps_lost_pct': 'home_off_snaps_lost_pct',
            'def_snaps_lost_pct': 'home_def_snaps_lost_pct'
        }).drop(columns=['team'])
        
        # Away Team merge
        dataset = dataset.merge(
            injury_features,
            left_on=['season', 'week', 'away_team_id'],
            right_on=['season', 'week', 'team'],
            how='left'
        ).rename(columns={
            'ppr_lost_pct': 'away_ppr_lost_pct',
            'off_snaps_lost_pct': 'away_off_snaps_lost_pct',
            'def_snaps_lost_pct': 'away_def_snaps_lost_pct'
        }).drop(columns=['team', 'week'])
        
        before_coverage_filter = len(dataset)
        dataset = dataset.dropna(subset=availability_columns).copy()
        quality['rows_without_verified_availability'] = int(before_coverage_filter - len(dataset))
        quality['training_rows'] = int(len(dataset))
        if dataset.empty:
            raise DataQualityError(
                "NFL: ningún partido tiene cobertura histórica de disponibilidad completa; "
                "no se convierten datos desconocidos en cero."
            )
        logger.info(
            "Features de lesiones verificados inyectados.",
            rows_before=before_coverage_filter, rows_after=len(dataset),
        )
    elif sport == "nfl":
        availability_columns = [
            "home_ppr_lost_pct", "home_off_snaps_lost_pct", "home_def_snaps_lost_pct",
            "away_ppr_lost_pct", "away_off_snaps_lost_pct", "away_def_snaps_lost_pct",
        ]
        dataset = dataset.drop(columns=availability_columns, errors="ignore")
        quality["feature_variant"] = "team_only"
        quality["availability_features_used"] = False
        quality["features"] = feature_columns(stats, include_availability=False)
        
    processed = data_root / "processed"

    processed.mkdir(parents=True, exist_ok=True)
    dataset_stem = f"{sport}_team_only_training" if variant == "team_only" else f"{sport}_training"
    path = processed / f"{dataset_stem}.csv"
    temp = path.with_suffix(".csv.tmp")
    dataset.to_csv(temp, index=False)
    temp.replace(path)
    source_path = data_root / raw_manifest
    if not source_path.exists():
        raise FileNotFoundError(f"Falta el manifest de fuente {source_path}")
    manifest = {
        "sport": sport.upper(),
        "dataset": path.name,
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "source_manifest": stored_path(source_path),
        "feature_metadata": {"game_stats": stats, "window_games": window,
                             "minimum_history": minimum_history,
                             "variant": variant,
                             "include_availability": variant == "injury_aware",
                             "feature_rule": quality["feature_rule"]},
        "quality": quality,
        "rows": int(len(dataset)),
        "date_from": str(dataset.game_date.min()), "date_to": str(dataset.game_date.max()),
        "sources": sorted(dataset.source.unique().tolist()),
    }
    write_manifest(processed / f"{dataset_stem}.manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--minimum-history", type=int, default=1)
    parser.add_argument("--variant", choices=["injury_aware", "team_only"], default="injury_aware")
    args = parser.parse_args()
    for sport in (["nba", "nfl"] if args.sport == "all" else [args.sport]):
        variant = args.variant if sport == "nfl" else "injury_aware"
        result = build(sport, args.window, args.minimum_history, variant=variant)
        print(json.dumps({key: result[key] for key in ("sport", "rows", "date_from", "date_to", "quality")}, indent=2))


if __name__ == "__main__":
    main()
