#!/usr/bin/env python3
"""Run three local factor backtests and publish an offline HTML report."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from tbcaptial.research import (
    STAMP_TAX_EFFECTIVE_DATE,
    FactorSuiteConfig,
    load_raw_research_dataset,
    run_factor_suite,
    select_latest_usable_download_manifest,
    write_factor_suite_artifacts,
)


def iso_date(value: str) -> date:
    """Parse an ISO calendar date for argparse."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    configured_data = Path(os.environ.get("TBCAPTIAL_DATA_DIR", "var/data"))
    data_root = (
        configured_data if configured_data.is_absolute() else repository_root / configured_data
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=data_root)
    parser.add_argument(
        "--manifest",
        type=Path,
        help=(
            "explicit Raw download manifest; default selects the newest usable manifest "
            "with at least 3 stocks"
        ),
    )
    parser.add_argument("--output-root", type=Path, default=repository_root / "var/runs/factors")
    parser.add_argument(
        "--start-date",
        type=iso_date,
        default=STAMP_TAX_EFFECTIVE_DATE,
        help=(
            "first research date (default: 2023-08-28, when the configured 0.05%% "
            "stamp-tax regime began)"
        ),
    )
    parser.add_argument("--end-date", type=iso_date)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.initial_cash <= 0.0:
        raise ValueError("initial cash must be positive")
    if (
        args.start_date is not None
        and args.end_date is not None
        and args.start_date > args.end_date
    ):
        raise ValueError("start date must not be later than end date")

    repository_root = Path(__file__).resolve().parents[1]
    data_root = args.data_root.expanduser().resolve()
    manifest = (
        args.manifest.expanduser().resolve()
        if args.manifest is not None
        else select_latest_usable_download_manifest(data_root)
    )
    dataset = load_raw_research_dataset(
        data_root,
        manifest,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(
        f"Input Raw manifest: {dataset.manifest_path}\n"
        f"Universe: {', '.join(dataset.symbols)}\n"
        f"Period: {dataset.start_date} .. {dataset.end_date} "
        f"({dataset.observations} observations)"
    )
    for warning in dataset.warnings:
        print(f"WARNING: {warning}")

    suite = run_factor_suite(
        dataset,
        config=FactorSuiteConfig(initial_cash=args.initial_cash),
    )
    for warning in suite.warnings[len(dataset.warnings) :]:
        print(f"WARNING: {warning}")
    artifact_dir = write_factor_suite_artifacts(
        suite,
        args.output_root,
        repository_root,
    )
    summary = pd.DataFrame([run.metrics.as_dict() for run in suite.runs]).set_index("strategy")
    columns = [
        "total_return",
        "annualized_return",
        "sharpe_ratio",
        "max_drawdown",
        "excess_total_return",
        "executions",
        "rejected_orders",
        "unresolved_orders",
        "total_commission",
    ]
    print("\nBacktest summary:")
    with pd.option_context("display.width", 160, "display.max_columns", None):
        print(summary.loc[:, columns].to_string(float_format=lambda value: f"{value:.4f}"))
    print(f"\nArtifacts: {artifact_dir}")
    print(f"Interactive report: {artifact_dir / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
