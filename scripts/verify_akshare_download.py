#!/usr/bin/env python3
"""Verify files and row counts referenced by an AKShare download manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

import pyarrow.parquet as pq


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_member(data_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"Manifest path must be relative: {relative_path}")
    resolved = (data_root / candidate).resolve()
    if not resolved.is_relative_to(data_root):
        raise ValueError(f"Manifest path escapes data root: {relative_path}")
    return resolved


def verify_file(data_root: Path, file_metadata: dict[str, object]) -> Path:
    relative_path = str(file_metadata["path"])
    path = resolve_member(data_root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"Missing manifest file: {relative_path}")
    expected_bytes = int(file_metadata["bytes"])
    if path.stat().st_size != expected_bytes:
        raise AssertionError(f"Size mismatch: {relative_path}")
    expected_hash = str(file_metadata["sha256"])
    if sha256_file(path) != expected_hash:
        raise AssertionError(f"SHA-256 mismatch: {relative_path}")
    return path


def verify_download_manifest(data_root: Path, manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETED":
        raise AssertionError(f"Download is not completed: {manifest.get('status')!r}")

    batches = manifest.get("batches")
    if not isinstance(batches, list) or not batches:
        raise AssertionError("Download manifest has no batches")

    total_rows = 0
    verified_files = 0
    endpoint_rows: dict[str, int] = {}
    for batch in batches:
        if not isinstance(batch, dict):
            raise TypeError("Batch entry must be an object")
        batch_manifest = batch.get("manifest")
        if not isinstance(batch_manifest, dict):
            raise TypeError("Batch manifest metadata must be an object")
        verify_file(data_root, batch_manifest)
        verified_files += 1

        files = batch.get("files")
        if not isinstance(files, list) or not files:
            raise AssertionError("Batch has no files")
        parquet_path: Path | None = None
        for file_metadata in files:
            if not isinstance(file_metadata, dict):
                raise TypeError("File metadata must be an object")
            path = verify_file(data_root, file_metadata)
            verified_files += 1
            if path.name == "data.parquet":
                parquet_path = path

        if parquet_path is None:
            raise AssertionError("Batch is missing data.parquet")
        expected_rows = int(batch["rows"])
        actual_rows = pq.read_metadata(parquet_path).num_rows
        if actual_rows != expected_rows:
            raise AssertionError(
                f"Parquet row mismatch for {parquet_path}: {actual_rows} != {expected_rows}"
            )
        endpoint = str(batch["endpoint"])
        endpoint_rows[endpoint] = endpoint_rows.get(endpoint, 0) + actual_rows
        total_rows += actual_rows

    return {
        "status": "PASS",
        "run_id": manifest["run_id"],
        "manifest": str(manifest_path),
        "batches": len(batches),
        "verified_files": verified_files,
        "total_rows": total_rows,
        "endpoint_rows": dict(sorted(endpoint_rows.items())),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_root = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    default_data_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    data_root = args.data_root.expanduser().resolve()
    if args.manifest is None:
        candidates = sorted((data_root / "manifests" / "downloads").glob("*.json"))
        if not candidates:
            raise FileNotFoundError(f"No download manifests found under {data_root}")
        manifest_path = candidates[-1]
    else:
        manifest_path = args.manifest.expanduser().resolve()

    report = verify_download_manifest(data_root, manifest_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
