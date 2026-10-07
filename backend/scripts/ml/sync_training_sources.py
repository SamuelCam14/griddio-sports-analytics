"""Download immutable public source snapshots used by the training pipeline."""
from __future__ import annotations

import argparse
import json

from ml.data_pipeline.nba_source import sync_nba_source
from ml.data_pipeline.nfl_source import sync_nfl_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    parser.add_argument("--seasons", nargs="+", type=int,
                        help="NBA end-year seasons (e.g. 2023 2024); NFL seasons (e.g. 2022 2023)")
    parser.add_argument("--nba-seasons", nargs="+", type=int, default=[2022, 2023, 2024, 2025, 2026])
    parser.add_argument("--nfl-seasons", nargs="+", type=int, default=list(range(2010, 2026)))
    args = parser.parse_args()
    result = {}
    if args.sport in ("nba", "all"):
        result["nba"] = sync_nba_source(args.seasons or args.nba_seasons)
    if args.sport in ("nfl", "all"):
        result["nfl"] = sync_nfl_source(args.seasons or args.nfl_seasons)
    print(json.dumps({sport: {"seasons": item["seasons"], "files": len(item["files"])}
                      for sport, item in result.items()}, indent=2))


if __name__ == "__main__":
    main()
