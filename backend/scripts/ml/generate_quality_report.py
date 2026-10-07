import json
import hashlib
from pathlib import Path
import pandas as pd
from datetime import datetime, timezone
from ml.data_pipeline.common import project_root

def generate_report():
    data_dir = project_root() / "data"
    processed_dir = data_dir / "processed"
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "datasets": {}}
    
    for manifest_path in processed_dir.glob("*_training.manifest.json"):
        sport = manifest_path.name.split("_")[0]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            dataset_path = manifest_path.with_name(manifest["dataset"])
            if not dataset_path.exists():
                report["datasets"][sport] = {"error": "Dataset file not found"}
                continue
            
            file_bytes = dataset_path.read_bytes()
            actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
            manifest_sha256 = manifest.get("dataset_sha256")
            
            hash_valid = (actual_sha256 == manifest_sha256)
            
            df = pd.read_csv(dataset_path)
            
            # Basic stats
            report["datasets"][sport] = {
                "manifest_path": str(manifest_path.relative_to(project_root())),
                "dataset_path": str(dataset_path.relative_to(project_root())),
                "hash_valid": hash_valid,
                "actual_sha256": actual_sha256,
                "manifest_sha256": manifest_sha256,
                "total_rows": len(df),
                "columns": list(df.columns),
                "date_min": str(df["game_date"].min()),
                "date_max": str(df["game_date"].max()),
                "seasons": df["season"].unique().tolist(),
                "missing_values": df.isna().sum().to_dict(),
                "duplicates": int(df.duplicated(subset=["game_id"]).sum()),
                "unique_home_teams": df["home_team_id"].nunique(),
                "unique_away_teams": df["away_team_id"].nunique(),
                "target_distribution": df["target_home_win"].value_counts().to_dict(),
                "feature_max_observed_game_date_valid": bool((pd.to_datetime(df["feature_max_observed_game_date"]) < pd.to_datetime(df["game_date"])).all())
            }
        except Exception as e:
            report["datasets"][sport] = {"error": str(e)}
            
    report_path = data_dir / "quality_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Report generated at {report_path.relative_to(project_root())}")

if __name__ == "__main__":
    generate_report()
