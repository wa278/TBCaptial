#!/usr/bin/env python3
"""Download auditable A-share company, corporate-action, and financial history."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from importlib import metadata
from pathlib import Path

import akshare as ak
import pandas as pd
from download_akshare_data import (
    FetchResult,
    install_default_http_timeout,
    iso_utc,
    persist_raw_batch,
    sina_symbol,
    utc_now,
    write_json,
)

DATASETS = (
    "profile",
    "ipo",
    "dividend",
    "share_change",
    "industry_change",
    "financial_indicator_em",
    "financial_indicator_quarterly_em",
    "main_business_em",
    "balance_sheet",
    "income_statement",
    "cash_flow",
)
OPTIONAL_EMPTY_DATASETS = {"dividend", "share_change", "industry_change"}


@dataclass(frozen=True)
class Task:
    dataset: str
    symbol: str
    endpoint: str
    parameters: dict[str, object]
    operation: Callable[[], pd.DataFrame]
    allow_empty: bool


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_root = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    default_data_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument(
        "--symbols",
        nargs="+",
        help=(
            "six-digit symbols; default discovers every symbol already present "
            "in local Raw daily bars"
        ),
    )
    parser.add_argument("--datasets", nargs="+", choices=DATASETS, default=list(DATASETS))
    parser.add_argument("--start-date", default="19900101")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="fetch tasks even if an identical endpoint and parameter set already exists in Raw",
    )
    return parser.parse_args(argv)


def normalize_symbol(value: object) -> str:
    symbol = str(value).lower()
    if len(symbol) == 8 and symbol[:2] in {"sh", "sz", "bj"}:
        return symbol[2:]
    return symbol


def validate_args(args: argparse.Namespace) -> None:
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
    if args.symbols is not None:
        for symbol in args.symbols:
            if len(symbol) != 6 or not symbol.isdigit():
                raise ValueError(f"Invalid A-share symbol: {symbol!r}")


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def discover_daily_symbols(data_root: Path) -> list[str]:
    symbols: set[str] = set()
    source_root = data_root / "raw" / "source=akshare"
    for endpoint in ("stock_zh_a_daily", "stock_zh_a_hist"):
        pattern = f"endpoint={endpoint}/ingest_date=*/batch=*/request.json"
        for request_path in source_root.glob(pattern):
            request = read_json(request_path)
            parameters = request.get("parameters")
            if not isinstance(parameters, dict):
                continue
            symbol = normalize_symbol(parameters.get("symbol"))
            if len(symbol) == 6 and symbol.isdigit():
                symbols.add(symbol)
    if not symbols:
        raise FileNotFoundError("No A-share symbols found in local Raw daily-bar requests")
    return sorted(symbols)


def request_key(endpoint: str, parameters: dict[str, object]) -> str:
    return f"{endpoint}:{json.dumps(parameters, ensure_ascii=False, sort_keys=True)}"


def existing_request_keys(data_root: Path) -> set[str]:
    keys: set[str] = set()
    source_root = data_root / "raw" / "source=akshare"
    for request_path in source_root.glob("endpoint=*/ingest_date=*/batch=*/request.json"):
        request = read_json(request_path)
        endpoint = request.get("endpoint")
        parameters = request.get("parameters")
        if isinstance(endpoint, str) and isinstance(parameters, dict):
            keys.add(request_key(endpoint, parameters))
    return keys


def build_task(dataset: str, symbol: str, start_date: str, end_date: str) -> Task:
    prefixed_symbol = sina_symbol(symbol)
    exchange = prefixed_symbol[:2].upper()
    eastmoney_symbol = f"{symbol}.{exchange}"
    eastmoney_prefixed_symbol = f"{exchange}{symbol}"
    if dataset == "profile":
        endpoint = "stock_profile_cninfo"
        parameters: dict[str, object] = {"symbol": symbol}
        operation = partial(ak.stock_profile_cninfo, symbol=symbol)
    elif dataset == "ipo":
        endpoint = "stock_ipo_summary_cninfo"
        parameters = {"symbol": symbol}
        operation = partial(ak.stock_ipo_summary_cninfo, symbol=symbol)
    elif dataset == "dividend":
        endpoint = "stock_dividend_cninfo"
        parameters = {"symbol": symbol}
        operation = partial(ak.stock_dividend_cninfo, symbol=symbol)
    elif dataset == "share_change":
        endpoint = "stock_share_change_cninfo"
        parameters = {"symbol": symbol, "start_date": start_date, "end_date": end_date}
        operation = partial(
            ak.stock_share_change_cninfo,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    elif dataset == "industry_change":
        endpoint = "stock_industry_change_cninfo"
        parameters = {"symbol": symbol, "start_date": start_date, "end_date": end_date}
        operation = partial(
            ak.stock_industry_change_cninfo,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    elif dataset in {"financial_indicator_em", "financial_indicator_quarterly_em"}:
        indicator = "按单季度" if dataset == "financial_indicator_quarterly_em" else "按报告期"
        endpoint = "stock_financial_analysis_indicator_em"
        parameters = {"symbol": eastmoney_symbol, "indicator": indicator}
        operation = partial(
            ak.stock_financial_analysis_indicator_em,
            symbol=eastmoney_symbol,
            indicator=indicator,
        )
    elif dataset == "main_business_em":
        endpoint = "stock_zygc_em"
        parameters = {"symbol": eastmoney_prefixed_symbol}
        operation = partial(ak.stock_zygc_em, symbol=eastmoney_prefixed_symbol)
    elif dataset in {"balance_sheet", "income_statement", "cash_flow"}:
        report_name = {
            "balance_sheet": "资产负债表",
            "income_statement": "利润表",
            "cash_flow": "现金流量表",
        }[dataset]
        endpoint = "stock_financial_report_sina"
        parameters = {"stock": prefixed_symbol, "symbol": report_name}
        operation = partial(
            ak.stock_financial_report_sina,
            stock=prefixed_symbol,
            symbol=report_name,
        )
    else:
        raise ValueError(f"Unsupported dataset: {dataset}")

    return Task(
        dataset=dataset,
        symbol=symbol,
        endpoint=endpoint,
        parameters=parameters,
        operation=operation,
        allow_empty=dataset in OPTIONAL_EMPTY_DATASETS,
    )


def fetch_task(task: Task, retries: int) -> FetchResult:
    started = utc_now()
    started_clock = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            print(
                f"[{task.symbol} {task.dataset}] attempt {attempt}/{retries}",
                flush=True,
            )
            frame = task.operation()
            if frame.empty and not task.allow_empty:
                raise RuntimeError("AKShare returned an empty DataFrame for a required dataset")
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
            print(
                f"[{task.symbol} {task.dataset}] {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(
        f"{task.symbol} {task.dataset} failed after {retries} attempts"
    ) from last_error


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_args(args)
    install_default_http_timeout(args.timeout)

    data_root = args.data_root.expanduser().resolve()
    symbols = sorted(set(args.symbols)) if args.symbols else discover_daily_symbols(data_root)
    datasets = list(dict.fromkeys(args.datasets))
    completed_keys = set() if args.no_resume else existing_request_keys(data_root)
    run_started = utc_now()
    run_id = f"{run_started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    batches: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    skipped = 0
    interrupted = False
    sequence = 0

    print(
        f"completeness run: symbols={len(symbols)} datasets={len(datasets)} "
        f"tasks={len(symbols) * len(datasets)} resume={not args.no_resume}",
        flush=True,
    )
    try:
        for symbol in symbols:
            for dataset in datasets:
                task = build_task(dataset, symbol, args.start_date, args.end_date)
                key = request_key(task.endpoint, task.parameters)
                if key in completed_keys:
                    skipped += 1
                    print(f"[{symbol} {dataset}] skipped: identical Raw request exists", flush=True)
                    continue
                sequence += 1
                try:
                    result = fetch_task(task, args.retries)
                    batches.append(
                        persist_raw_batch(
                            data_root,
                            run_id,
                            sequence,
                            task.endpoint,
                            task.parameters,
                            result,
                        )
                    )
                    completed_keys.add(key)
                except Exception as error:
                    failures.append(
                        {
                            "symbol": symbol,
                            "dataset": dataset,
                            "endpoint": task.endpoint,
                            "error_type": type(error.__cause__ or error).__name__,
                            "error": str(error.__cause__ or error),
                        }
                    )
                time.sleep(args.request_interval)
    except KeyboardInterrupt:
        interrupted = True
        print("interrupted; persisting a PARTIAL run manifest", file=sys.stderr, flush=True)

    status = "COMPLETED" if not failures and not interrupted else "PARTIAL"
    run_manifest = {
        "run_id": run_id,
        "status": status,
        "provider": "akshare",
        "akshare_version": metadata.version("akshare"),
        "started_at": iso_utc(run_started),
        "completed_at": iso_utc(utc_now()),
        "requested": {
            "symbols": symbols,
            "datasets": datasets,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "request_interval_seconds": args.request_interval,
            "resume": not args.no_resume,
        },
        "task_summary": {
            "requested": len(symbols) * len(datasets),
            "saved": len(batches),
            "skipped_existing": skipped,
            "failed": len(failures),
        },
        "failures": failures,
        "batches": batches,
        "note": "Raw enrichment manifest only; this is not a validated Silver snapshot.",
    }
    manifest_path = data_root / "manifests" / "enrichment" / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(manifest_path, run_manifest)
    shutil.rmtree(data_root / "staging" / run_id, ignore_errors=True)

    print(f"enrichment manifest: {manifest_path}")
    print(
        f"status={status} saved={len(batches)} skipped={skipped} "
        f"failed={len(failures)} rows={sum(int(batch['rows']) for batch in batches)}"
    )
    if interrupted:
        return 130
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
