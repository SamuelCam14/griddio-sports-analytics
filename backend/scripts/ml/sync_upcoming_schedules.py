"""Refresh real future schedules without mixing them into historical labels."""
from __future__ import annotations

import argparse
import json

from ml.data_pipeline.nba_source import sync_nba_upcoming_schedules
from ml.data_pipeline.nfl_source import sync_nfl_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nba-season-end", type=int, default=2027)
    parser.add_argument("--nfl-season", type=int, default=2026)
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    args = parser.parse_args()
    report = {}
    if args.sport in ("nba", "all"):
        result = sync_nba_upcoming_schedules([args.nba_season_end])
        report["nba"] = {"seasons": result["seasons"], "files": len(result["files"])}
    if args.sport in ("nfl", "all"):
        # Merge this live season into the historical snapshot manifest. Scores and PBP
        # are retained only when nflverse has published them; no schedule values are guessed.
        result = sync_nfl_source([args.nfl_season])
        report["nfl"] = {"seasons": result["seasons"], "files": len(result["files"])}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
