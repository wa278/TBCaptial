#!/usr/bin/env python3
"""Preview Parquet data referenced by an AKShare download manifest."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

DATASET_ENDPOINTS = {
    "calendar": {"tool_trade_date_hist_sina"},
    "daily": {"stock_zh_a_hist", "stock_zh_a_daily"},
    "benchmark": {"stock_zh_index_daily"},
    "enrichment": {
        "stock_profile_cninfo",
        "stock_ipo_summary_cninfo",
        "stock_dividend_cninfo",
        "stock_share_change_cninfo",
        "stock_industry_change_cninfo",
        "stock_financial_analysis_indicator_em",
        "stock_zygc_em",
        "stock_financial_report_sina",
    },
}


def resolve_member(data_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"Manifest path must be relative: {relative_path}")
    resolved = (data_root / candidate).resolve()
    if not resolved.is_relative_to(data_root):
        raise ValueError(f"Manifest path escapes data root: {relative_path}")
    return resolved


def normalize_symbol(value: object) -> str:
    symbol = str(value).lower()
    if len(symbol) == 8 and symbol[:2] in {"sh", "sz", "bj"}:
        return symbol[2:]
    if len(symbol) == 9 and symbol[6:] in {".sh", ".sz", ".bj"}:
        return symbol[:6]
    return symbol


def request_symbol(endpoint: str, parameters: dict[str, object]) -> object:
    if endpoint == "stock_financial_report_sina":
        return parameters.get("stock")
    return parameters.get("symbol")


def select_manifest(data_root: Path, requested: Path | None) -> Path:
    if requested is not None:
        path = requested.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Download manifest not found: {path}")
        return path

    candidates = sorted((data_root / "manifests" / "downloads").glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"No download manifests found under {data_root}")
    return candidates[-1]


def dataset_for_endpoint(endpoint: str) -> str | None:
    for dataset, endpoints in DATASET_ENDPOINTS.items():
        if endpoint in endpoints:
            return dataset
    return None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_root = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    default_data_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=default_data_root)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--dataset",
        choices=("all", *DATASET_ENDPOINTS),
        default="all",
    )
    parser.add_argument("--symbol", help="six-digit symbol filter for daily bars")
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument(
        "--max-columns",
        type=int,
        default=20,
        help="maximum displayed columns; use 0 to show every column",
    )
    parser.add_argument("--head", action="store_true", help="show earliest rows instead of latest")
    parser.add_argument("--list-runs", action="store_true")
    return parser.parse_args(argv)


def list_runs(data_root: Path) -> int:
    manifests = sorted((data_root / "manifests" / "downloads").glob("*.json"))
    if not manifests:
        raise FileNotFoundError(f"No download manifests found under {data_root}")
    print("run_id                         batches  rows")
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        batches = manifest.get("batches", [])
        rows = sum(int(batch["rows"]) for batch in batches)
        print(f"{manifest['run_id']:<30} {len(batches):>7}  {rows:>6}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.rows <= 100:
        raise ValueError("rows must be between 1 and 100")
    if not 0 <= args.max_columns <= 500:
        raise ValueError("max-columns must be between 0 and 500")
    if args.symbol is not None and (len(args.symbol) != 6 or not args.symbol.isdigit()):
        raise ValueError("symbol must contain exactly six digits")

    data_root = args.data_root.expanduser().resolve()
    if args.list_runs:
        return list_runs(data_root)

    manifest_path = select_manifest(data_root, args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETED":
        raise AssertionError(f"Download is not completed: {manifest.get('status')!r}")

    print(f"manifest: {manifest_path}")
    print(f"run_id:   {manifest['run_id']}")
    print(f"request:  {json.dumps(manifest.get('requested', {}), ensure_ascii=False)}")

    matched = 0
    for batch in manifest["batches"]:
        endpoint = str(batch["endpoint"])
        dataset = dataset_for_endpoint(endpoint)
        if dataset is None or (args.dataset != "all" and args.dataset != dataset):
            continue

        request_file = next(
            item for item in batch["files"] if str(item["path"]).endswith("request.json")
        )
        request_path = resolve_member(data_root, str(request_file["path"]))
        request = json.loads(request_path.read_text(encoding="utf-8"))
        parameters = request.get("parameters", {})
        raw_symbol = request_symbol(endpoint, parameters)
        if args.symbol is not None and (
            dataset not in {"daily", "enrichment"} or normalize_symbol(raw_symbol) != args.symbol
        ):
            continue

        data_file = next(
            item for item in batch["files"] if str(item["path"]).endswith("data.parquet")
        )
        data_path = resolve_member(data_root, str(data_file["path"]))
        frame = pd.read_parquet(data_path)
        sample = frame.head(args.rows) if args.head else frame.tail(args.rows)
        direction = "earliest" if args.head else "latest"

        matched += 1
        print()
        print(
            f"[{dataset}] endpoint={endpoint} parameters={parameters} "
            f"total_rows={len(frame)} columns={len(frame.columns)} "
            f"showing={direction}:{len(sample)}"
        )
        with pd.option_context(
            "display.max_columns",
            None if args.max_columns == 0 else args.max_columns,
            "display.width",
            200,
            "display.max_colwidth",
            60,
        ):
            print(sample.to_string(index=False))

    if matched == 0:
        raise LookupError("No batch matched the requested dataset and symbol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
