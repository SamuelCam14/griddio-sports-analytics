"""Score source-backed upcoming games for offline analysis only."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ml.data_pipeline.nba_source import load_nba_games, load_nba_upcoming_games
from ml.data_pipeline.nfl_source import load_nfl_games, load_nfl_upcoming_games
from ml.inference import predict_matchups
from ml.data_pipeline.common import project_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=["nba", "nfl", "all"], default="all")
    parser.add_argument("--allow-experimental", action="store_true",
                        help="Required while the selected artifact is not approved for production")
    args = parser.parse_args()
    if not args.allow_experimental:
        raise SystemExit("No hay modelos aprobados para producción; use --allow-experimental solo para análisis offline.")
    as_of = datetime.now(timezone.utc)
    sports = ["nba", "nfl"] if args.sport == "all" else [args.sport]
    output_root = project_root() / "data" / "predictions" / "experimental"
    output_root.mkdir(parents=True, exist_ok=True)
    for sport in sports:
        if sport == "nba":
            history, upcoming = load_nba_games(), load_nba_upcoming_games(as_of=as_of)
        else:
            history, upcoming = load_nfl_games(), load_nfl_upcoming_games(as_of=as_of)
        if upcoming.empty:
            print(json.dumps({"sport": sport.upper(), "status": "no_future_games_in_source", "as_of": as_of.isoformat()}))
            continue
        predictions, run = predict_matchups(
            sport, history, upcoming, model_name="logistic", as_of=as_of,
            allow_experimental=True,
        )
        path = output_root / f"{sport}_predictions_{as_of:%Y%m%dT%H%M%SZ}.csv"
        predictions.to_csv(path, index=False)
        (path.with_suffix(".manifest.json")).write_text(json.dumps({
            **run, "output_file": path.name, "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "game_count": len(predictions),
            "schedule_retrieved_at": upcoming.attrs.get("schedule_retrieved_at"),
            "incomplete_schedule_games": upcoming.attrs.get("skipped_incomplete", []),
        }, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"sport": sport.upper(), "status": "experimental_predictions_written",
                          "games": len(predictions), "output": str(path),
                          "model_version": run["model_version"],
                          "production_eligible": run["production_eligible"]}, indent=2))


if __name__ == "__main__":
    main()
