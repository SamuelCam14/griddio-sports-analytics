"""Shared provenance and validation helpers for real sports data."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


class DataQualityError(ValueError):
    """Raised when source data cannot safely support the requested dataset."""


def project_root() -> Path:
    code_root = backend_root()
    return code_root.parent if code_root.name == "backend" else code_root


def backend_root() -> Path:
    """Resolve the backend root on host layouts and the `/app` container layout."""
    return Path(__file__).resolve().parents[2]


def manifest_path(path: str | Path) -> Path:
    """Resolve a project-relative path stored in a portable source manifest."""
    value = Path(path)
    return value if value.is_absolute() else project_root() / value


def stored_path(path: str | Path) -> str:
    """Store paths relative to the repository whenever the file lives in it."""
    value = Path(path).resolve()
    try:
        return value.relative_to(project_root()).as_posix()
    except ValueError:
        return str(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_file(path: str | Path, expected_sha256: str) -> None:
    file_path = Path(path)
    if not file_path.is_file():
        raise DataQualityError(f"No existe snapshot fuente: {file_path}")
    actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise DataQualityError(f"Snapshot alterado: {file_path} (hash SHA-256 no coincide)")


def download_versioned_asset(url: str, directory: Path, stem: str) -> dict[str, Any]:
    """Download a public source asset and retain immutable, checksummed bytes."""
    response = httpx.get(url, timeout=90.0, follow_redirects=True,
                         headers={"User-Agent": "sports-analytics-data-pipeline/1.0"})
    response.raise_for_status()
    payload = response.content
    digest = sha256_bytes(payload)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".bin"
    target = directory / f"{stem}_{digest[:16]}{suffix}"
    if not target.exists():
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(payload)
        os.replace(temp, target)
    return {
        "source_url": url,
        "sha256": digest,
        "path": stored_path(target),
        "bytes": len(payload),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def require_columns(frame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise DataQualityError(f"{label}: faltan columnas fuente requeridas: {missing}")


def require_complete(frame, columns: list[str], label: str) -> None:
    absent = [column for column in columns if column not in frame.columns]
    if absent:
        raise DataQualityError(f"{label}: faltan campos requeridos: {absent}")
    null_counts = frame[columns].isna().sum()
    bad = {key: int(value) for key, value in null_counts.items() if value}
    if bad:
        raise DataQualityError(f"{label}: datos fuente incompletos; no se imputan: {bad}")
