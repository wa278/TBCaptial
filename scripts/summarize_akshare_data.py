#!/usr/bin/env python3
"""Summarize all locally persisted AKShare Raw batches without modifying them."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

DAILY_ENDPOINTS = {"stock_zh_a_hist", "stock_zh_a_daily"}
CALENDAR_ENDPOINT = "tool_trade_date_hist_sina"
BENCHMARK_ENDPOINT = "stock_zh_index_daily"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_root = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    default_data_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args(argv)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def normalize_symbol(value: object) -> str:
    symbol = str(value).lower()
    if len(symbol) == 8 and symbol[:2] in {"sh", "sz", "bj"}:
        return symbol[2:]
    return symbol


def read_iso_dates(data_path: Path, column: str) -> set[str]:
    frame = pd.read_parquet(data_path, columns=[column])
    converted = pd.to_datetime(frame[column], errors="raise")
    return {value.date().isoformat() for value in converted}


def date_range(values: set[str]) -> dict[str, str] | None:
    if not values:
        return None
    return {"start": min(values), "end": max(values)}


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def summarize(data_root: Path) -> dict[str, object]:
    raw_root = data_root / "raw" / "source=akshare"
    if not raw_root.is_dir():
        raise FileNotFoundError(f"AKShare Raw root not found: {raw_root}")

    download_manifests = sorted((data_root / "manifests" / "downloads").glob("*.json"))
    completed_runs = 0
    completed_run_rows = 0
    referenced_batch_ids: set[str] = set()
    for path in download_manifests:
        manifest = read_json(path)
        if manifest.get("status") != "COMPLETED":
            continue
        completed_runs += 1
        batches = manifest.get("batches")
        if not isinstance(batches, list):
            raise TypeError(f"Expected batches to be a list: {path}")
        for batch in batches:
            if not isinstance(batch, dict):
                raise TypeError(f"Expected batch to be an object: {path}")
            referenced_batch_ids.add(str(batch["batch_id"]))
            completed_run_rows += int(batch["rows"])

    enrichment_manifests = sorted((data_root / "manifests" / "enrichment").glob("*.json"))
    enrichment_statuses: Counter[str] = Counter()
    enrichment_rows = 0
    enrichment_batches = 0
    enrichment_failures = 0
    for path in enrichment_manifests:
        manifest = read_json(path)
        enrichment_statuses[str(manifest.get("status"))] += 1
        failures = manifest.get("failures", [])
        if isinstance(failures, list):
            enrichment_failures += len(failures)
        batches = manifest.get("batches")
        if not isinstance(batches, list):
            raise TypeError(f"Expected batches to be a list: {path}")
        for batch in batches:
            if not isinstance(batch, dict):
                raise TypeError(f"Expected batch to be an object: {path}")
            referenced_batch_ids.add(str(batch["batch_id"]))
            enrichment_rows += int(batch["rows"])
            enrichment_batches += 1

    endpoint_rows: Counter[str] = Counter()
    raw_batch_ids: set[str] = set()
    raw_rows = 0
    daily_raw_rows = 0
    daily_observations: set[tuple[str, str]] = set()
    daily_symbols: set[str] = set()
    daily_dates: set[str] = set()
    calendar_dates: set[str] = set()
    benchmark_observations: set[tuple[str, str]] = set()
    benchmark_symbols: set[str] = set()
    benchmark_dates: set[str] = set()
    batch_rows: dict[str, int] = {}
    batch_symbols: dict[str, str] = {}

    raw_manifests = sorted(raw_root.glob("endpoint=*/ingest_date=*/batch=*/manifest.json"))
    for manifest_path in raw_manifests:
        batch = read_json(manifest_path)
        batch_id = str(batch["batch_id"])
        if batch_id in raw_batch_ids:
            raise ValueError(f"Duplicate Raw batch_id: {batch_id}")
        raw_batch_ids.add(batch_id)

        endpoint = str(batch["endpoint"])
        rows = int(batch["rows"])
        endpoint_rows[endpoint] += rows
        raw_rows += rows
        batch_rows[batch_id] = rows

        batch_dir = manifest_path.parent
        data_path = batch_dir / "data.parquet"
        request_path = batch_dir / "request.json"
        if not data_path.is_file() or not request_path.is_file():
            raise FileNotFoundError(f"Incomplete Raw batch: {batch_dir}")
        request = read_json(request_path)
        parameters = request.get("parameters")
        if not isinstance(parameters, dict):
            raise TypeError(f"Expected request parameters to be an object: {request_path}")

        if endpoint in DAILY_ENDPOINTS:
            symbol = normalize_symbol(parameters.get("symbol"))
            if len(symbol) != 6 or not symbol.isdigit():
                raise ValueError(f"Invalid daily symbol in {request_path}: {symbol!r}")
            dates = read_iso_dates(data_path, "date")
            daily_raw_rows += rows
            daily_symbols.add(symbol)
            daily_dates.update(dates)
            daily_observations.update((symbol, date) for date in dates)
            batch_symbols[batch_id] = symbol
        elif endpoint == CALENDAR_ENDPOINT:
            calendar_dates.update(read_iso_dates(data_path, "trade_date"))
        elif endpoint == BENCHMARK_ENDPOINT:
            symbol = str(parameters.get("symbol"))
            dates = read_iso_dates(data_path, "date")
            benchmark_symbols.add(symbol)
            benchmark_dates.update(dates)
            benchmark_observations.update((symbol, date) for date in dates)

    all_files = [path for path in data_root.rglob("*") if path.is_file()]
    orphan_batch_ids = raw_batch_ids - referenced_batch_ids
    orphan_symbols = {
        batch_symbols[batch_id] for batch_id in orphan_batch_ids if batch_id in batch_symbols
    }

    return {
        "data_root": str(data_root),
        "storage_bytes": sum(path.stat().st_size for path in all_files),
        "files": len(all_files),
        "parquet_files": sum(path.suffix == ".parquet" for path in all_files),
        "completed_runs": completed_runs,
        "completed_run_rows": completed_run_rows,
        "enrichment": {
            "runs": len(enrichment_manifests),
            "statuses": dict(sorted(enrichment_statuses.items())),
            "batches": enrichment_batches,
            "rows": enrichment_rows,
            "failures": enrichment_failures,
        },
        "raw_batches": len(raw_batch_ids),
        "raw_rows": raw_rows,
        "endpoint_rows": dict(sorted(endpoint_rows.items())),
        "daily": {
            "symbols": len(daily_symbols),
            "raw_rows": daily_raw_rows,
            "unique_symbol_date_rows": len(daily_observations),
            "duplicate_symbol_date_rows": daily_raw_rows - len(daily_observations),
            "date_range": date_range(daily_dates),
        },
        "calendar": {
            "unique_dates": len(calendar_dates),
            "date_range": date_range(calendar_dates),
        },
        "benchmark": {
            "symbols": sorted(benchmark_symbols),
            "unique_symbol_date_rows": len(benchmark_observations),
            "date_range": date_range(benchmark_dates),
        },
        "orphan_raw": {
            "batches": len(orphan_batch_ids),
            "rows": sum(batch_rows[batch_id] for batch_id in orphan_batch_ids),
            "daily_symbols": sorted(orphan_symbols),
        },
    }


def print_summary(summary: dict[str, object]) -> None:
    daily = summary["daily"]
    calendar = summary["calendar"]
    benchmark = summary["benchmark"]
    enrichment = summary["enrichment"]
    orphan = summary["orphan_raw"]
    if not all(
        isinstance(item, dict) for item in (daily, calendar, benchmark, enrichment, orphan)
    ):
        raise TypeError("Invalid summary sections")

    print(f"data root:       {summary['data_root']}")
    print(f"storage:         {format_bytes(int(summary['storage_bytes']))}")
    print(f"files:           {summary['files']} ({summary['parquet_files']} Parquet)")
    print(
        f"completed runs:  {summary['completed_runs']} "
        f"({summary['completed_run_rows']} referenced rows)"
    )
    print(
        "enrichment:      "
        f"{enrichment['runs']} runs, statuses={enrichment['statuses']}, "
        f"{enrichment['batches']} batches, {enrichment['rows']} rows, "
        f"failures={enrichment['failures']}"
    )
    print(f"Raw:             {summary['raw_batches']} batches, {summary['raw_rows']} rows")
    print(f"by endpoint:     {json.dumps(summary['endpoint_rows'], ensure_ascii=False)}")
    print(
        "daily bars:     "
        f"{daily['symbols']} symbols, {daily['raw_rows']} Raw rows, "
        f"{daily['unique_symbol_date_rows']} unique symbol-date rows, "
        f"range={daily['date_range']}"
    )
    print(
        f"calendar:       {calendar['unique_dates']} unique dates, range={calendar['date_range']}"
    )
    print(
        "benchmark:      "
        f"symbols={benchmark['symbols']}, "
        f"{benchmark['unique_symbol_date_rows']} unique symbol-date rows, "
        f"range={benchmark['date_range']}"
    )
    print(
        "orphan Raw:     "
        f"{orphan['batches']} batches, {orphan['rows']} rows, "
        f"daily_symbols={orphan['daily_symbols']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = summarize(args.data_root.expanduser().resolve())
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
