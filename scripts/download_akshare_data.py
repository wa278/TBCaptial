#!/usr/bin/env python3
"""Download a small, auditable AKShare data slice into the local data root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

DEFAULT_SYMBOLS = ("000001", "600000", "300750")
DEFAULT_BENCHMARK = "sh000300"
DAILY_SOURCES = ("auto", "eastmoney", "sina")


@dataclass(frozen=True)
class FetchResult:
    frame: pd.DataFrame
    attempts: int
    started_at: str
    completed_at: str
    elapsed_seconds: float


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def install_default_http_timeout(timeout: float) -> None:
    original_request = requests.sessions.Session.request

    def request_with_timeout(
        session: requests.Session, method: str, url: str, **kwargs: object
    ) -> requests.Response:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original_request(session, method, url, **kwargs)

    requests.sessions.Session.request = request_with_timeout  # type: ignore[method-assign]


def fetch_with_retry(
    label: str,
    operation: Callable[[], pd.DataFrame],
    retries: int,
) -> FetchResult:
    started = utc_now()
    started_clock = time.monotonic()
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            print(f"[{label}] attempt {attempt}/{retries}", flush=True)
            frame = operation()
            if frame.empty:
                raise RuntimeError("AKShare returned an empty DataFrame")
            completed = utc_now()
            return FetchResult(
                frame=frame,
                attempts=attempt,
                started_at=iso_utc(started),
                completed_at=iso_utc(completed),
                elapsed_seconds=round(time.monotonic() - started_clock, 3),
            )
        except Exception as error:  # AKShare exposes provider-specific exceptions.
            last_error = error
            print(f"[{label}] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 4))

    raise RuntimeError(f"{label} failed after {retries} attempts") from last_error


def persist_raw_batch(
    data_root: Path,
    run_id: str,
    sequence: int,
    endpoint: str,
    parameters: dict[str, object],
    result: FetchResult,
) -> dict[str, object]:
    ingest_date = date.today().isoformat()
    batch_id = f"{run_id}-{sequence:02d}-{uuid.uuid4().hex[:8]}"
    relative_dir = Path(
        "raw",
        "source=akshare",
        f"endpoint={endpoint}",
        f"ingest_date={ingest_date}",
        f"batch={batch_id}",
    )
    final_dir = data_root / relative_dir
    staging_dir = data_root / "staging" / run_id / f"batch-{sequence:02d}"
    staging_dir.mkdir(parents=True, exist_ok=False)

    try:
        parquet_path = staging_dir / "data.parquet"
        result.frame.to_parquet(
            parquet_path,
            engine="pyarrow",
            compression="zstd",
            index=False,
        )
        data_hash = sha256_file(parquet_path)

        request_payload = {
            "provider": "akshare",
            "endpoint": endpoint,
            "parameters": parameters,
            "akshare_version": metadata.version("akshare"),
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "attempts": result.attempts,
        }
        response_payload = {
            "rows": len(result.frame),
            "columns": [str(column) for column in result.frame.columns],
            "empty": False,
            "elapsed_seconds": result.elapsed_seconds,
            "data_sha256": data_hash,
        }
        write_json(staging_dir / "request.json", request_payload)
        write_json(staging_dir / "response.json", response_payload)

        files = []
        for filename in ("data.parquet", "request.json", "response.json"):
            file_path = staging_dir / filename
            files.append(
                {
                    "path": str(relative_dir / filename),
                    "bytes": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                }
            )
        batch_manifest = {
            "batch_id": batch_id,
            "source": "akshare",
            "endpoint": endpoint,
            "rows": len(result.frame),
            "files": files,
        }
        write_json(staging_dir / "manifest.json", batch_manifest)

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.replace(final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    print(f"[{endpoint}] saved {len(result.frame)} rows -> {final_dir}", flush=True)
    return {
        **batch_manifest,
        "manifest": {
            "path": str(relative_dir / "manifest.json"),
            "bytes": (final_dir / "manifest.json").stat().st_size,
            "sha256": sha256_file(final_dir / "manifest.json"),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_root = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    default_data_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    parser.add_argument(
        "--daily-source",
        choices=DAILY_SOURCES,
        default="auto",
        help="auto tries Eastmoney once and circuit-breaks to Sina on repeated failure",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=2.0,
        help="minimum pause between provider calls, in seconds",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for symbol in args.symbols:
        if len(symbol) != 6 or not symbol.isdigit():
            raise ValueError(f"Invalid A-share symbol: {symbol!r}")
    for field_name in ("start_date", "end_date"):
        datetime.strptime(getattr(args, field_name), "%Y%m%d")
    if args.start_date > args.end_date:
        raise ValueError("start date must not be later than end date")
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    if args.retries < 1:
        raise ValueError("retries must be at least 1")
    if args.request_interval < 0:
        raise ValueError("request interval must not be negative")


def sina_symbol(symbol: str) -> str:
    exchange = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{exchange}{symbol}"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_args(args)
    install_default_http_timeout(args.timeout)

    data_root = args.data_root.expanduser().resolve()
    run_started = utc_now()
    run_id = f"{run_started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    batches: list[dict[str, object]] = []
    eastmoney_available = args.daily_source != "sina"

    calendar = fetch_with_retry(
        "trade calendar",
        ak.tool_trade_date_hist_sina,
        args.retries,
    )
    batches.append(
        persist_raw_batch(
            data_root,
            run_id,
            1,
            "tool_trade_date_hist_sina",
            {},
            calendar,
        )
    )
    time.sleep(args.request_interval)

    for offset, symbol in enumerate(args.symbols, start=2):
        if eastmoney_available:
            endpoint = "stock_zh_a_hist"
            parameters = {
                "symbol": symbol,
                "period": "daily",
                "start_date": args.start_date,
                "end_date": args.end_date,
                "adjust": "",
            }
            try:
                daily_bar = fetch_with_retry(
                    f"daily bar {symbol} (Eastmoney)",
                    lambda symbol=symbol: ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=args.start_date,
                        end_date=args.end_date,
                        adjust="",
                        timeout=args.timeout,
                    ),
                    args.retries,
                )
            except RuntimeError:
                if args.daily_source == "eastmoney":
                    raise
                eastmoney_available = False
        if not eastmoney_available:
            endpoint = "stock_zh_a_daily"
            prefixed_symbol = sina_symbol(symbol)
            parameters = {
                "symbol": prefixed_symbol,
                "start_date": args.start_date,
                "end_date": args.end_date,
                "adjust": "",
                "fallback_for": "stock_zh_a_hist",
            }
            action = "using" if args.daily_source == "sina" else "falling back to"
            print(
                f"[daily bar {symbol}] {action} AKShare Sina endpoint",
                file=sys.stderr,
                flush=True,
            )
            daily_bar = fetch_with_retry(
                f"daily bar {symbol} (Sina)",
                lambda prefixed_symbol=prefixed_symbol: ak.stock_zh_a_daily(
                    symbol=prefixed_symbol,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    adjust="",
                ),
                args.retries,
            )
        batches.append(
            persist_raw_batch(
                data_root,
                run_id,
                offset,
                endpoint,
                parameters,
                daily_bar,
            )
        )
        time.sleep(args.request_interval)

    benchmark = fetch_with_retry(
        f"benchmark {args.benchmark}",
        lambda: ak.stock_zh_index_daily(symbol=args.benchmark),
        args.retries,
    )
    batches.append(
        persist_raw_batch(
            data_root,
            run_id,
            len(args.symbols) + 2,
            "stock_zh_index_daily",
            {"symbol": args.benchmark},
            benchmark,
        )
    )

    run_manifest = {
        "run_id": run_id,
        "status": "COMPLETED",
        "provider": "akshare",
        "akshare_version": metadata.version("akshare"),
        "started_at": iso_utc(run_started),
        "completed_at": iso_utc(utc_now()),
        "requested": {
            "symbols": args.symbols,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "benchmark": args.benchmark,
            "adjust": "",
            "daily_source": args.daily_source,
            "request_interval_seconds": args.request_interval,
        },
        "batches": batches,
        "note": "Raw download manifest only; this is not a validated Silver snapshot.",
    }
    manifest_path = data_root / "manifests" / "downloads" / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(manifest_path, run_manifest)
    shutil.rmtree(data_root / "staging" / run_id, ignore_errors=True)

    print(f"download manifest: {manifest_path}")
    print(f"total rows: {sum(int(batch['rows']) for batch in batches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
