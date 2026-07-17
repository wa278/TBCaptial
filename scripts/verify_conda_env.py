#!/usr/bin/env python3
"""Offline smoke test for the TBCaptial Conda environment."""

from __future__ import annotations

import importlib
import json
import platform
import sys
import tempfile
from importlib import metadata
from pathlib import Path

EXPECTED_PYTHON = (3, 11)
PACKAGES = {
    "akshare": "akshare",
    "duckdb": "duckdb",
    "hypothesis": "hypothesis",
    "mypy": "mypy",
    "numpy": "numpy",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "pytest": "pytest",
    "PyYAML": "yaml",
    "ruff": "ruff",
    "tenacity": "tenacity",
    "tushare": "tushare",
    "typer": "typer",
}


def verify_imports() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution, module in PACKAGES.items():
        importlib.import_module(module)
        versions[distribution] = metadata.version(distribution)
    return versions


def verify_local_parquet_and_duckdb() -> None:
    import duckdb
    import pandas as pd
    import pyarrow.parquet as pq

    expected = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "600000.SH"],
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "close": [9.21, 6.61],
            "volume_shares": [1000, 2000],
        }
    )

    with tempfile.TemporaryDirectory(prefix="tbcaptial-env-smoke-") as temp_dir:
        root = Path(temp_dir)
        parquet_path = root / "bars.parquet"
        database_path = root / "catalog.duckdb"

        expected.to_parquet(parquet_path, engine="pyarrow", compression="zstd", index=False)
        metadata_rows = pq.read_metadata(parquet_path).num_rows
        if metadata_rows != len(expected):
            raise AssertionError(f"Parquet row count mismatch: {metadata_rows} != {len(expected)}")

        with duckdb.connect(str(database_path)) as connection:
            result = connection.execute(
                """
                SELECT count(*) AS row_count,
                       round(sum(close), 2) AS close_sum,
                       sum(volume_shares) AS volume_sum
                FROM read_parquet(?)
                """,
                [str(parquet_path)],
            ).fetchone()

        if result != (2, 15.82, 3000):
            raise AssertionError(f"DuckDB result mismatch: {result!r}")


def main() -> int:
    if sys.version_info[:2] != EXPECTED_PYTHON:
        raise RuntimeError(
            f"Expected Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}, "
            f"got {platform.python_version()}"
        )

    versions = verify_imports()
    verify_local_parquet_and_duckdb()

    report = {
        "status": "PASS",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": dict(sorted(versions.items())),
        "checks": [
            "core package imports",
            "local Parquet ZSTD write and metadata read",
            "local DuckDB file query over Parquet",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
