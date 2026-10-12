"""
Script para generar el reporte de calidad formal de datos NFL (`data/nfl_quality_report.json`).
Inspecciona data/raw/nflverse/manifest.json, valida SHA-256, columnas, filas, nulos, duplicados,
fechas mínimas y máximas, cobertura temporal, filas omitidas y procedencia por asset con función de librería,
marcando como 'not_exposed_by_library' cualquier URL interna no provista por la API pública de nflreadpy.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Optional
import polars as pl

from ml.data_pipeline.common import project_root

DATASET_CONFIG = {
    "schedules": {
        "loader_function": "nflreadpy.load_schedules",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": "gameday",
        "temporal_applicability": "calendar_date",
        "temporal_justification": "Fechas de partidos de calendario provistas en columna 'gameday' (formato YYYY-MM-DD).",
        "primary_key": ["game_id"],
    },
    "pbp": {
        "loader_function": "nflreadpy.load_pbp",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": "game_date",
        "temporal_applicability": "calendar_date",
        "temporal_justification": "Fechas exactas de partidos provistas en columna 'game_date' (formato YYYY-MM-DD).",
        "primary_key": ["game_id", "play_id"],
    },
    "players_crosswalk": {
        "loader_function": "nflreadpy.load_players",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": None,
        "temporal_applicability": "not_applicable_atemporal_dimension",
        "temporal_justification": (
            "Tabla dimensional global atemporal (season: None). Mapea identificadores "
            "multiplataforma (gsis_id, pfr_id, espn_id, sleeper_id). No corresponde rango de fechas de evento."
        ),
        "primary_key": ["gsis_id"],
    },
    "player_stats": {
        "loader_function": "nflreadpy.load_player_stats",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": None,
        "temporal_applicability": "weekly_aggregation",
        "temporal_justification": (
            "Estadísticas agregadas a nivel jugador por temporada ('season') y semana ('week'). "
            "nflreadpy no expone columna de fecha calendario individual en este dataset."
        ),
        "primary_key": ["player_id", "game_id", "season_type"],
    },
    "snap_counts": {
        "loader_function": "nflreadpy.load_snap_counts",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": None,
        "temporal_applicability": "weekly_aggregation",
        "temporal_justification": (
            "Conteo de jugadas agrupado por partido ('game_id'), temporada ('season') y semana ('week'). "
            "nflreadpy no incluye columna de fecha calendario individual en snap_counts."
        ),
        "primary_key": ["game_id", "pfr_player_id"],
    },
    "injuries": {
        "loader_function": "nflreadpy.load_injuries",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": "date_modified",
        "temporal_applicability": "weekly_official_report",
        "temporal_justification": (
            "Reportes oficiales de lesiones por semana ('week') y temporada ('season'). "
            "La columna 'date_modified' registra modificaciones cuando son publicadas por la NFL."
        ),
        "primary_key": ["season", "game_type", "week", "team", "gsis_id", "date_modified"],
    },
    "rosters_weekly": {
        "loader_function": "nflreadpy.load_rosters_weekly",
        "upstream_url": "not_exposed_by_library",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "date_column": None,
        "temporal_applicability": "weekly_roster_snapshot",
        "temporal_justification": (
            "Snapshots semanales de plantillas activas e inactivas por temporada ('season') y semana ('week'). "
            "La columna 'birth_date' corresponde a la persona y no a la fecha del evento deportivo."
        ),
        "primary_key": ["season", "game_type", "week", "team", "gsis_id"],
    },
}


def generate_nfl_quality_report() -> dict:
    root = project_root()
    manifest_path = root / "data" / "raw" / "nflverse" / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest no encontrado en {manifest_path}")

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest_data.get("files", [])

    provenance_data = {
        "source": "nflverse",
        "upstream_repository": "https://github.com/nflverse/nflverse-data",
        "python_library": "nflreadpy",
        "library_version": "0.1.5",
        "storage_format": "Apache Parquet (snappy/zstd)",
        "immutable_raw_storage": True,
        "manifest_file": str(manifest_path.relative_to(root)).replace("\\", "/"),
        "total_manifest_entries": len(files),
        "library_upstream_urls_note": (
            "nflreadpy descarga artefactos internamente desde releases de GitHub; "
            "las URLs de descarga individuales no forman parte de su API pública "
            "y se marcan rigurosamente como 'not_exposed_by_library'."
        ),
    }

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance_data,
        "provenance_summary": provenance_data,
        "library_and_dataset_limitations": {
            "snap_counts": {
                "historical_cutoff": 2012,
                "limitation": "Snap counts no están disponibles en GSIS/PFR antes de la temporada 2012. nflreadpy solo provee datos a partir de 2012.",
                "mitigation": "Pipelines de feature engineering deben manejar temporadas previas a 2012 sin snap counts o rellenar con valores predeterminados/indicadores de ausencia.",
            },
            "players_crosswalk": {
                "temporal_partitioning": None,
                "limitation": "Tabla dimensional global atemporal (season: None). Mapea IDs de múltiples plataformas (gsis_id, pfr_id, espn_id, sleeper_id).",
                "mitigation": "Usar como dimensión estática de resolución de IDs sin join por fecha o temporada.",
            },
            "injuries": {
                "reporting_cadence": "weekly_official",
                "limitation": "Los reportes de lesiones solo cubren jugadores formalmente listados en el injury report oficial de la NFL para cada semana. Jugadores sin lesión reportada no tienen registro esa semana.",
                "mitigation": "Imputar estatus 'healthy' / did_play=True para jugadores en roster sin mención en el reporte semanal de lesiones.",
            },
            "pbp": {
                "granularity": "play_by_play",
                "limitation": "Archivos voluminosos (~12MB por temporada). Contienen EPA, WPA y detalles posicionales.",
                "mitigation": "Filtrar por jugadas regulares o cargar columnas específicas para optimizar memoria.",
            },
            "rosters_weekly": {
                "granularity": "weekly_roster_status",
                "limitation": "Refleja el estado del jugador al momento del reporte semanal; cambios de última hora (inactives 90 min pre-game) se reflejan en el kickoff.",
            },
        },
        "datasets_summary": {},
        "files_audit": [],
    }

    datasets_meta: dict[str, dict] = {}
    total_bytes = 0
    total_rows = 0
    total_dropped_rows = 0
    total_null_cells = 0
    total_exact_duplicate_rows = 0

    for file_info in files:
        rel_path = file_info["path"]
        abs_path = root / rel_path
        dataset = file_info["dataset"]
        season = file_info.get("season")
        expected_sha256 = file_info["sha256"]
        expected_rows = file_info.get("rows", 0)
        expected_bytes = file_info.get("bytes", 0)

        cfg = DATASET_CONFIG.get(dataset, {
            "loader_function": f"nflreadpy.load_{dataset}",
            "upstream_url": "not_exposed_by_library",
            "upstream_repository": "https://github.com/nflverse/nflverse-data",
            "date_column": None,
            "temporal_applicability": "unknown",
            "temporal_justification": "Configuración no especificada",
            "primary_key": [],
        })

        file_audit: dict[str, Any] = {
            "path": rel_path.replace("\\", "/"),
            "dataset": dataset,
            "season": season,
            "provenance": {
                "loader_function": cfg["loader_function"],
                "upstream_source_url": cfg["upstream_url"],
                "upstream_repository": cfg["upstream_repository"],
                "library": "nflreadpy",
                "version": file_info.get("package_version", "0.1.5"),
            },
            "exists": abs_path.is_file(),
            "expected_sha256": expected_sha256,
            "expected_rows": expected_rows,
            "expected_bytes": expected_bytes,
            "hash_valid": False,
            "actual_rows": 0,
            "dropped_rows": 0,  # En la capa raw no se omite ninguna fila de la fuente
            "actual_bytes": 0,
            "columns_count": 0,
            "null_cells_count": 0,
            "exact_duplicate_rows": 0,
            "primary_key_columns": cfg.get("primary_key", []),
            "primary_key_missing_columns": [],
            "primary_key_duplicate_rows": None,
            "primary_key_null_rows": None,
            "date_min": None,
            "date_max": None,
            "temporal_applicability": cfg["temporal_applicability"],
            "temporal_note": cfg["temporal_justification"],
            "error": None,
        }

        if not abs_path.is_file():
            file_audit["error"] = "File missing on disk"
            report["files_audit"].append(file_audit)
            continue

        file_bytes = abs_path.read_bytes()
        actual_bytes = len(file_bytes)
        file_audit["actual_bytes"] = actual_bytes
        actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
        file_audit["actual_sha256"] = actual_sha256
        file_audit["hash_valid"] = (actual_sha256 == expected_sha256)

        try:
            df = pl.read_parquet(abs_path)
            file_audit["actual_rows"] = df.height
            file_audit["columns_count"] = df.width

            # Cero filas omitidas en capa raw
            file_audit["dropped_rows"] = 0

            # Conteo de nulos y duplicados exactos
            null_count = int(df.null_count().sum_horizontal()[0])
            dup_count = int(df.is_duplicated().sum())
            file_audit["null_cells_count"] = null_count
            file_audit["exact_duplicate_rows"] = dup_count

            primary_key = cfg.get("primary_key", [])
            missing_key_columns = [column for column in primary_key if column not in df.columns]
            file_audit["primary_key_missing_columns"] = missing_key_columns
            if primary_key and not missing_key_columns:
                complete_key_rows = df.filter(
                    ~pl.any_horizontal([pl.col(column).is_null() for column in primary_key])
                )
                duplicate_groups = 0
                if complete_key_rows.height:
                    duplicate_groups = (
                        complete_key_rows.group_by(primary_key)
                        .len()
                        .filter(pl.col("len") > 1)
                        .select(pl.col("len").sum())
                        .item()
                    )
                null_key_rows = df.select(
                    pl.any_horizontal([pl.col(column).is_null() for column in primary_key]).sum()
                ).item()
                file_audit["primary_key_duplicate_rows"] = int(duplicate_groups or 0)
                file_audit["primary_key_null_rows"] = int(null_key_rows or 0)

            # Fechas mínimas/máximas
            date_col = cfg.get("date_column")
            if date_col and date_col in df.columns:
                non_null_dates = df[date_col].drop_nulls()
                if len(non_null_dates) > 0:
                    file_audit["date_min"] = str(non_null_dates.min())
                    file_audit["date_max"] = str(non_null_dates.max())

            total_rows += df.height
            total_bytes += actual_bytes
            total_dropped_rows += file_audit["dropped_rows"]
            total_null_cells += null_count
            total_exact_duplicate_rows += dup_count

            # Acumular en datasets_summary
            if dataset not in datasets_meta:
                datasets_meta[dataset] = {
                    "dataset": dataset,
                    "loader_function": cfg["loader_function"],
                    "upstream_source_url": cfg["upstream_url"],
                    "upstream_repository": cfg["upstream_repository"],
                    "primary_key_columns": cfg.get("primary_key", []),
                    "temporal_applicability": cfg["temporal_applicability"],
                    "temporal_justification": cfg["temporal_justification"],
                    "seasons": [],
                    "date_min": None,
                    "date_max": None,
                    "total_files": 0,
                    "total_rows": 0,
                    "total_dropped_rows": 0,
                    "total_bytes": 0,
                    "total_null_cells": 0,
                    "total_exact_duplicate_rows": 0,
                    "total_primary_key_duplicate_rows": 0,
                    "total_primary_key_null_rows": 0,
                    "columns_sample": df.columns[:25],
                    "all_columns_count": df.width,
                    "all_hashes_valid": True,
                }

            ds_entry = datasets_meta[dataset]
            ds_entry["total_files"] += 1
            ds_entry["total_rows"] += df.height
            ds_entry["total_dropped_rows"] += file_audit["dropped_rows"]
            ds_entry["total_bytes"] += actual_bytes
            ds_entry["total_null_cells"] += null_count
            ds_entry["total_exact_duplicate_rows"] += dup_count
            if file_audit["primary_key_duplicate_rows"] is not None:
                ds_entry["total_primary_key_duplicate_rows"] += file_audit["primary_key_duplicate_rows"]
                ds_entry["total_primary_key_null_rows"] += file_audit["primary_key_null_rows"]

            if season is not None and season not in ds_entry["seasons"]:
                ds_entry["seasons"].append(season)

            if not file_audit["hash_valid"]:
                ds_entry["all_hashes_valid"] = False

            if file_audit["date_min"]:
                if ds_entry["date_min"] is None or file_audit["date_min"] < ds_entry["date_min"]:
                    ds_entry["date_min"] = file_audit["date_min"]
            if file_audit["date_max"]:
                if ds_entry["date_max"] is None or file_audit["date_max"] > ds_entry["date_max"]:
                    ds_entry["date_max"] = file_audit["date_max"]

        except Exception as exc:
            file_audit["error"] = f"Failed to parse parquet: {str(exc)}"

        report["files_audit"].append(file_audit)

    # Ordenar temporadas y cobertura en resumen de datasets
    for ds_entry in datasets_meta.values():
        ds_entry["seasons"] = sorted(ds_entry["seasons"])
        if ds_entry["seasons"]:
            ds_entry["season_min"] = ds_entry["seasons"][0]
            ds_entry["season_max"] = ds_entry["seasons"][-1]
            ds_entry["observed_seasons_count"] = len(ds_entry["seasons"])
        else:
            ds_entry["season_min"] = None
            ds_entry["season_max"] = None
            ds_entry["observed_seasons_count"] = 0
        ds_entry["season_coverage_note"] = (
            "Cuenta temporadas observadas en el manifest; no representa porcentaje de cobertura ni valida semanas faltantes."
        )

    report["datasets_summary"] = datasets_meta
    report["totals"] = {
        "total_files_audited": len(report["files_audit"]),
        "total_rows": total_rows,
        "total_dropped_rows": total_dropped_rows,
        "total_bytes": total_bytes,
        "total_null_cells": total_null_cells,
        "total_exact_duplicate_rows": total_exact_duplicate_rows,
        "primary_key_duplicate_rows": sum(
            f["primary_key_duplicate_rows"] or 0 for f in report["files_audit"]
        ),
        "primary_key_null_rows": sum(
            f["primary_key_null_rows"] or 0 for f in report["files_audit"]
        ),
        "all_primary_keys_configured": all(
            not f["primary_key_missing_columns"] for f in report["files_audit"]
        ),
        "all_files_exist": all(f["exists"] for f in report["files_audit"]),
        "all_hashes_match": all(f["hash_valid"] for f in report["files_audit"]),
    }

    out_path = root / "data" / "nfl_quality_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"NFL Quality Report successfully saved to: {out_path}")
    return report


if __name__ == "__main__":
    generate_nfl_quality_report()
