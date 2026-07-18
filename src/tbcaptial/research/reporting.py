"""Local, auditable artifacts and an offline Plotly report for factor runs."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]
from plotly.subplots import make_subplots  # type: ignore[import-untyped]

from .raw_data import sha256_file
from .suite import STAMP_TAX_EFFECTIVE_DATE, FactorBacktestRun, FactorSuiteResult


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _git_output(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_tree_sha256(repository_root: Path) -> str:
    """Hash executable project sources, including files not yet tracked by Git."""
    candidates = [
        repository_root / "Makefile",
        repository_root / "environment.yml",
        repository_root / "pyproject.toml",
    ]
    candidates.extend(sorted((repository_root / "src").rglob("*.py")))
    candidates.extend(sorted((repository_root / "scripts").glob("*.py")))
    candidates.extend(sorted((repository_root / "scripts").glob("*.sh")))
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in candidates if candidate.is_file()):
        relative_path = str(path.relative_to(repository_root))
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _object_columns_to_strings(frame: pd.DataFrame) -> pd.DataFrame:
    safe = frame.copy(deep=True)
    for column in safe.select_dtypes(include=["object"]).columns:
        safe[column] = safe[column].map(lambda value: None if pd.isna(value) else str(value))
    return safe


def _write_strategy_artifacts(directory: Path, run: FactorBacktestRun) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    run.equity.rename("equity").to_frame().to_parquet(
        directory / "equity.parquet", compression="zstd"
    )
    run.drawdown.rename("drawdown").to_frame().to_parquet(
        directory / "drawdown.parquet", compression="zstd"
    )
    _object_columns_to_strings(run.result.positions).to_parquet(
        directory / "positions.parquet", compression="zstd"
    )
    _object_columns_to_strings(run.result.orders).to_parquet(
        directory / "orders.parquet", compression="zstd"
    )
    _object_columns_to_strings(run.result.executions).to_parquet(
        directory / "executions.parquet", compression="zstd"
    )
    decisions = [
        {
            "timestamp_ns": decision.timestamp_ns,
            "timestamp": pd.Timestamp(decision.timestamp_ns, tz="UTC")
            .tz_convert("Asia/Shanghai")
            .isoformat(),
            "scores": dict(decision.scores),
            "target_weights": dict(decision.target_weights),
        }
        for decision in run.strategy.decisions
    ]
    _write_json(directory / "decisions.json", decisions)
    _write_json(
        directory / "strategy.json",
        {
            "name": run.strategy.factor_name,
            "description": run.strategy.description,
            "parameters": run.strategy.parameters,
            "metrics": run.metrics.as_dict(),
        },
    )


def _build_report(suite: FactorSuiteResult, destination: Path) -> None:
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.08,
        row_heights=[0.48, 0.27, 0.25],
        specs=[[{"type": "xy"}], [{"type": "xy"}], [{"type": "table"}]],
        subplot_titles=("Normalized equity", "Drawdown", "Performance summary"),
    )
    benchmark_nav = suite.benchmark_equity / float(suite.benchmark_equity.iloc[0])
    figure.add_trace(
        go.Scatter(
            x=benchmark_nav.index,
            y=benchmark_nav,
            name=suite.dataset.benchmark_symbol,
            line={"color": "#6b7280", "dash": "dash"},
        ),
        row=1,
        col=1,
    )
    for run in suite.runs:
        nav = run.equity / float(run.equity.iloc[0])
        figure.add_trace(
            go.Scatter(x=nav.index, y=nav, name=run.strategy.factor_name), row=1, col=1
        )
        figure.add_trace(
            go.Scatter(
                x=run.drawdown.index,
                y=run.drawdown,
                name=f"{run.strategy.factor_name} drawdown",
                showlegend=False,
                fill="tozeroy",
            ),
            row=2,
            col=1,
        )

    metric_headers = [
        "Strategy",
        "Total return",
        "Annual return",
        "Volatility",
        "Sharpe",
        "Max drawdown",
        "Excess vs benchmark",
        "Executions",
        "Rejected orders",
        "Open orders",
        "Commission",
    ]
    rows = [run.metrics for run in suite.runs]
    metric_columns: list[list[str | int]] = [
        [item.strategy for item in rows],
        [f"{item.total_return:.2%}" for item in rows],
        [f"{item.annualized_return:.2%}" for item in rows],
        [f"{item.annualized_volatility:.2%}" for item in rows],
        [f"{item.sharpe_ratio:.2f}" for item in rows],
        [f"{item.max_drawdown:.2%}" for item in rows],
        [f"{item.excess_total_return:.2%}" for item in rows],
        [item.executions for item in rows],
        [item.rejected_orders for item in rows],
        [item.unresolved_orders for item in rows],
        [f"¥{item.total_commission:,.2f}" for item in rows],
    ]
    figure.add_trace(
        go.Table(
            header={"values": metric_headers, "fill_color": "#1f2937", "font": {"color": "white"}},
            cells={"values": metric_columns, "fill_color": "#f3f4f6", "align": "left"},
        ),
        row=3,
        col=1,
    )
    warning_text = " | ".join(suite.warnings)
    figure.update_layout(
        title={
            "text": (
                "TBCaptial factor research suite"
                f"<br><sup>{suite.dataset.start_date} to {suite.dataset.end_date} | "
                f"Raw manifest {suite.dataset.download_run_id}</sup>"
            )
        },
        height=1100,
        template="plotly_white",
        hovermode="x unified",
        margin={"l": 70, "r": 40, "t": 100, "b": 110},
        annotations=[
            *list(figure.layout.annotations),
            {
                "text": warning_text,
                "xref": "paper",
                "yref": "paper",
                "x": 0.0,
                "y": -0.08,
                "showarrow": False,
                "align": "left",
                "font": {"size": 11, "color": "#b45309"},
            },
        ],
    )
    figure.update_yaxes(tickformat=".2f", title_text="Net value", row=1, col=1)
    figure.update_yaxes(tickformat=".1%", title_text="Drawdown", row=2, col=1)
    figure.write_html(destination, include_plotlyjs=True, full_html=True)


def _artifact_inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "manifest.json":
            continue
        inventory.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return inventory


def write_factor_suite_artifacts(
    suite: FactorSuiteResult,
    output_root: Path,
    repository_root: Path,
) -> Path:
    """Atomically publish tables, audit records and an offline interactive report."""
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    resolved_output = output_root.expanduser().resolve()
    staging = resolved_output / ".staging" / run_id
    final = resolved_output / run_id
    staging.mkdir(parents=True, exist_ok=False)
    try:
        metrics = pd.DataFrame([run.metrics.as_dict() for run in suite.runs])
        metrics.to_csv(staging / "metrics.csv", index=False)
        metrics.to_parquet(staging / "metrics.parquet", compression="zstd", index=False)

        equity = pd.concat(
            [
                suite.benchmark_equity.rename(suite.dataset.benchmark_symbol),
                *(run.equity for run in suite.runs),
            ],
            axis=1,
        ).sort_index()
        equity.to_parquet(staging / "equity_curves.parquet", compression="zstd")
        pd.concat([run.drawdown for run in suite.runs], axis=1).sort_index().to_parquet(
            staging / "drawdowns.parquet", compression="zstd"
        )

        strategies_root = staging / "strategies"
        for run in suite.runs:
            _write_strategy_artifacts(strategies_root / run.strategy.factor_name, run)
        _build_report(suite, staging / "report.html")

        completed = datetime.now(UTC)
        manifest = {
            "status": "COMPLETED",
            "run_id": run_id,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "mode": "raw_manifest_research_only",
            "quality_status": "RESEARCH_ONLY_WITH_WARNINGS" if suite.warnings else "PASS",
            "input": {
                "download_run_id": suite.dataset.download_run_id,
                "manifest": str(suite.dataset.manifest_path),
                "manifest_sha256": suite.dataset.manifest_sha256,
                "symbols": list(suite.dataset.symbols),
                "benchmark": suite.dataset.benchmark_symbol,
                "start_date": suite.dataset.start_date.isoformat(),
                "end_date": suite.dataset.end_date.isoformat(),
                "observations": suite.dataset.observations,
                "synthetic_bar_counts": suite.dataset.synthetic_bar_counts,
            },
            "environment": {
                "tbcaptial_git_commit": _git_output(repository_root, "rev-parse", "HEAD"),
                "tbcaptial_worktree_dirty": bool(
                    _git_output(repository_root, "status", "--porcelain")
                ),
                "tbcaptial_source_tree_sha256": _source_tree_sha256(repository_root),
                "environment_yml_sha256": sha256_file(repository_root / "environment.yml"),
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "numpy_version": metadata.version("numpy"),
                "pandas_version": metadata.version("pandas"),
                "pyarrow_version": metadata.version("pyarrow"),
                "plotly_version": metadata.version("plotly"),
                "akquant_version": metadata.version("akquant"),
                "akquant_commit": _git_output(
                    repository_root / "third_party" / "akquant", "rev-parse", "HEAD"
                ),
            },
            "execution": {
                "initial_cash": suite.config.initial_cash,
                "commission_rate": suite.config.commission_rate,
                "stamp_tax_rate": suite.config.stamp_tax_rate,
                "stamp_tax_effective_from": STAMP_TAX_EFFECTIVE_DATE.isoformat(),
                "transfer_fee_rate": suite.config.transfer_fee_rate,
                "min_commission": suite.config.min_commission,
                "lot_size": suite.config.lot_size,
                "volume_limit_pct": suite.config.volume_limit_pct,
                "slippage_model": "none",
                "signal_time": "daily_close",
                "fill_time": "next_event_open",
                "target_retry_policy": "reassert_same_target_once_at_next_close",
                "maximum_fill_lag_events": 2,
                "t_plus_one": True,
            },
            "strategies": [
                {
                    "name": run.strategy.factor_name,
                    "description": run.strategy.description,
                    "parameters": run.strategy.parameters,
                    "metrics": run.metrics.as_dict(),
                }
                for run in suite.runs
            ],
            "warnings": list(suite.warnings),
            "artifacts": _artifact_inventory(staging),
        }
        _write_json(staging / "manifest.json", manifest)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final
