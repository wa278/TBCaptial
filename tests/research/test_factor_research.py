"""Offline integration tests for Raw-manifest factor research backtests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from tbcaptial.backtest import BarSlice, BarView, StrategyContext
from tbcaptial.research import (
    FactorSuiteConfig,
    ShortTermReversalStrategy,
    load_raw_research_dataset,
    run_factor_suite,
    select_latest_usable_download_manifest,
    write_factor_suite_artifacts,
)

STRATEGY_NAMES = {
    "low_volatility_20d",
    "momentum_60d",
    "reversal_5d",
}


class _RecordingContext:
    def __init__(self) -> None:
        self.targets: list[dict[str, float]] = []

    def set_target_weights(
        self,
        targets: Mapping[str, float],
        *,
        liquidate_unmentioned: bool,
        tolerance: float = 0.0,
    ) -> tuple[str, ...]:
        assert liquidate_unmentioned is True
        assert tolerance == 0.01
        self.targets.append(dict(targets))
        return ()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _file_metadata(data_root: Path, path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.relative_to(data_root)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _persist_batch(
    data_root: Path,
    *,
    run_id: str,
    sequence: int,
    endpoint: str,
    parameters: Mapping[str, object],
    frame: pd.DataFrame,
) -> dict[str, Any]:
    batch_id = f"{run_id}-{sequence:02d}"
    directory = (
        data_root
        / "raw"
        / "source=akshare"
        / f"endpoint={endpoint}"
        / "ingest_date=2024-08-01"
        / f"batch={batch_id}"
    )
    directory.mkdir(parents=True)
    frame.to_parquet(directory / "data.parquet", compression="zstd", index=False)
    _write_json(
        directory / "request.json",
        {
            "provider": "akshare",
            "endpoint": endpoint,
            "parameters": dict(parameters),
            "attempts": 1,
        },
    )
    _write_json(
        directory / "response.json",
        {
            "rows": len(frame),
            "columns": [str(column) for column in frame.columns],
            "empty": False,
        },
    )
    files = [
        _file_metadata(data_root, directory / filename)
        for filename in ("data.parquet", "request.json", "response.json")
    ]
    batch: dict[str, Any] = {
        "batch_id": batch_id,
        "source": "akshare",
        "endpoint": endpoint,
        "rows": len(frame),
        "files": files,
    }
    _write_json(directory / "manifest.json", batch)
    batch["manifest"] = _file_metadata(data_root, directory / "manifest.json")
    return batch


def _stock_frames(dates: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    step = np.arange(len(dates), dtype=float)
    close_by_symbol = {
        "sz000001": 10.0 + 0.025 * step + 0.10 * np.sin(step / 8.0),
        "sh600000": 18.0 - 0.012 * step + 0.18 * np.sin(step / 5.0),
        "sz300750": 25.0 + 0.010 * step + 0.65 * np.sin(step / 4.0),
    }
    result: dict[str, pd.DataFrame] = {}
    for offset, (symbol, close) in enumerate(close_by_symbol.items(), start=1):
        open_price = close * (1.0 + 0.0015 * np.sin(step / (offset + 2.0)))
        result[symbol] = pd.DataFrame(
            {
                "date": dates,
                "open": open_price,
                "high": np.maximum(open_price, close) * 1.01,
                "low": np.minimum(open_price, close) * 0.99,
                "close": close,
                "volume": np.full(len(dates), 2_000_000.0),
            }
        )
    return result


def _build_raw_download(
    tmp_path: Path,
    *,
    invalid_high: bool = False,
    eastmoney_first_stock: bool = False,
) -> tuple[Path, Path, pd.DatetimeIndex]:
    data_root = tmp_path / "data"
    run_id = "20240801T000000Z-offline"
    dates = pd.bdate_range("2024-01-02", periods=150)
    stocks = _stock_frames(dates)

    # Deliberately vary coverage and row ordering; the loader must align and sort them.
    stocks["sh600000"] = stocks["sh600000"].iloc[2:].drop(index=40).reset_index(drop=True)
    stocks["sz300750"] = stocks["sz300750"].iloc[:-3].iloc[::-1].reset_index(drop=True)
    if invalid_high:
        stocks["sz000001"].loc[10, "high"] = 0.01

    batches = []
    for sequence, symbol in enumerate(("sz000001", "sh600000", "sz300750"), start=1):
        endpoint = "stock_zh_a_daily"
        parameters: dict[str, object] = {"symbol": symbol, "adjust": ""}
        frame = stocks[symbol]
        if sequence == 1 and eastmoney_first_stock:
            endpoint = "stock_zh_a_hist"
            parameters["symbol"] = "000001"
            frame = frame.rename(
                columns={
                    "date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "volume": "成交量",
                }
            ).copy()
            frame["成交量"] /= 100.0
        batches.append(
            _persist_batch(
                data_root,
                run_id=run_id,
                sequence=sequence,
                endpoint=endpoint,
                parameters=parameters,
                frame=frame,
            )
        )

    benchmark = pd.DataFrame(
        {
            "date": dates[1:-1],
            "close": 3_500.0 + 1.5 * np.arange(len(dates[1:-1]), dtype=float),
        }
    )
    batches.append(
        _persist_batch(
            data_root,
            run_id=run_id,
            sequence=4,
            endpoint="stock_zh_index_daily",
            parameters={"symbol": "sh000300"},
            frame=benchmark,
        )
    )
    manifest = {
        "run_id": run_id,
        "status": "COMPLETED",
        "provider": "akshare",
        "batches": batches,
        "note": "Synthetic Raw download manifest; not a Silver snapshot.",
    }
    manifest_path = data_root / "manifests" / "downloads" / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True)
    _write_json(manifest_path, manifest)
    return data_root, manifest_path, dates[2:-3]


def test_raw_download_manifest_is_strictly_verified_aligned_and_sorted(tmp_path: Path) -> None:
    data_root, manifest_path, expected_dates = _build_raw_download(tmp_path)

    assert select_latest_usable_download_manifest(data_root) == manifest_path.resolve()
    dataset = load_raw_research_dataset(data_root, manifest_path)

    assert dataset.download_run_id == "20240801T000000Z-offline"
    assert dataset.symbols == ("000001.SZ", "300750.SZ", "600000.SH")
    assert dataset.benchmark_symbol == "SH000300"
    assert dataset.observations == len(expected_dates) == 145
    assert "not a validated Silver snapshot" in dataset.warnings[0]
    assert dataset.synthetic_bar_counts == {
        "000001.SZ": 0,
        "300750.SZ": 0,
        "600000.SH": 1,
    }

    expected_index = (expected_dates.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)).rename(
        "timestamp"
    )
    for frame in dataset.market_data.values():
        pdt.assert_index_equal(frame.index, expected_index, check_names=False)
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert frame.index.is_monotonic_increasing
    pdt.assert_index_equal(dataset.benchmark_close.index, expected_index, check_names=False)
    missing_timestamp = pd.Timestamp(expected_dates[38]).tz_localize(
        "Asia/Shanghai"
    ) + pd.Timedelta(hours=15)
    carried_frame = dataset.market_data["600000.SH"]
    assert carried_frame.loc[missing_timestamp, "volume"] == 0.0
    assert (
        carried_frame.loc[missing_timestamp, "close"]
        == carried_frame.loc[missing_timestamp - pd.offsets.BDay(), "close"]
    )


def test_eastmoney_hist_schema_and_lot_volume_are_normalized(tmp_path: Path) -> None:
    data_root, manifest_path, _ = _build_raw_download(tmp_path, eastmoney_first_stock=True)

    dataset = load_raw_research_dataset(data_root, manifest_path)

    assert "000001.SZ" in dataset.market_data
    assert dataset.market_data["000001.SZ"]["volume"].iloc[0] == 2_000_000.0


def test_non_tradable_factor_window_is_excluded_and_target_is_retried_unchanged() -> None:
    strategy = ShortTermReversalStrategy()
    recording = _RecordingContext()
    closes = {
        "000001.SZ": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0],
        "300750.SZ": [10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 20.0],
        "600000.SH": [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
    }
    timestamps = pd.bdate_range("2024-01-02", periods=7, tz="Asia/Shanghai")
    for offset, timestamp in enumerate(timestamps):
        bars = {
            symbol: BarView(
                timestamp_ns=timestamp.value,
                symbol=symbol,
                open=value[offset],
                high=value[offset],
                low=value[offset],
                close=value[offset],
                volume=0.0 if symbol == "000001.SZ" and offset == 2 else 1_000_000.0,
            )
            for symbol, value in closes.items()
        }
        strategy.on_bars(
            cast(StrategyContext, recording),
            BarSlice.build(timestamp.value, bars),
        )

    assert len(strategy.decisions) == 1
    assert "000001.SZ" not in strategy.decisions[0].scores
    assert strategy.decisions[0].target_weights == {"300750.SZ": 0.85}
    assert recording.targets == [
        {"300750.SZ": 0.85},
        {"300750.SZ": 0.85},
    ]


def test_raw_loader_rejects_tampering_and_invalid_ohlcv(tmp_path: Path) -> None:
    data_root, manifest_path, _ = _build_raw_download(tmp_path / "tampered")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    request_path = data_root / manifest["batches"][0]["files"][1]["path"]
    original = request_path.read_text(encoding="utf-8")
    request_path.write_text(original.replace("sz000001", "sz000002"), encoding="utf-8")

    with pytest.raises(AssertionError, match="SHA-256 mismatch"):
        load_raw_research_dataset(data_root, manifest_path)

    invalid_root, invalid_manifest, _ = _build_raw_download(
        tmp_path / "invalid-ohlcv", invalid_high=True
    )
    with pytest.raises(ValueError, match="invalid high prices"):
        load_raw_research_dataset(invalid_root, invalid_manifest)


def test_three_real_akquant_factor_runs_publish_complete_offline_report(tmp_path: Path) -> None:
    data_root, manifest_path, _ = _build_raw_download(tmp_path)
    dataset = load_raw_research_dataset(data_root, manifest_path)

    suite = run_factor_suite(
        dataset,
        config=FactorSuiteConfig(initial_cash=1_000_000.0),
    )

    assert len(suite.runs) == 3
    assert {run.strategy.factor_name for run in suite.runs} == STRATEGY_NAMES
    for run in suite.runs:
        assert len(run.result.bar_slices) == dataset.observations
        assert run.metrics.observations == dataset.observations
        assert run.metrics.decisions == len(run.strategy.decisions) > 0
        assert run.metrics.orders > 0
        assert run.metrics.executions > 0
        assert run.metrics.unresolved_orders == 0
        assert not run.equity.empty
        equity_index = run.equity.index
        assert isinstance(equity_index, pd.DatetimeIndex)
        assert equity_index.tz is not None

    repository_root = Path(__file__).resolve().parents[2]
    run_directory = write_factor_suite_artifacts(
        suite,
        output_root=tmp_path / "runs",
        repository_root=repository_root,
    )

    assert run_directory.is_dir()
    assert (run_directory / "report.html").stat().st_size > 1_000_000
    report = (run_directory / "report.html").read_text(encoding="utf-8")
    assert "TBCaptial factor research suite" in report
    assert '<script src="https://cdn.plot.ly' not in report
    assert all(name in report for name in STRATEGY_NAMES)

    metrics = pd.read_csv(run_directory / "metrics.csv")
    parquet_metrics = pd.read_parquet(run_directory / "metrics.parquet")
    assert set(metrics["strategy"]) == STRATEGY_NAMES
    pdt.assert_frame_equal(metrics, parquet_metrics, check_dtype=False)

    artifact_manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    assert artifact_manifest["status"] == "COMPLETED"
    assert artifact_manifest["mode"] == "raw_manifest_research_only"
    assert artifact_manifest["input"]["observations"] == dataset.observations
    assert artifact_manifest["input"]["synthetic_bar_counts"]["600000.SH"] == 1
    assert artifact_manifest["execution"]["volume_limit_pct"] == 1.0
    assert artifact_manifest["execution"]["slippage_model"] == "none"
    assert artifact_manifest["execution"]["maximum_fill_lag_events"] == 2
    assert len(artifact_manifest["environment"]["tbcaptial_source_tree_sha256"]) == 64
    assert {strategy["name"] for strategy in artifact_manifest["strategies"]} == STRATEGY_NAMES
    inventory = {item["path"] for item in artifact_manifest["artifacts"]}
    assert {"report.html", "metrics.csv", "metrics.parquet"}.issubset(inventory)

    expected_strategy_files = {
        "decisions.json",
        "drawdown.parquet",
        "equity.parquet",
        "executions.parquet",
        "orders.parquet",
        "positions.parquet",
        "strategy.json",
    }
    for strategy_name in STRATEGY_NAMES:
        strategy_directory = run_directory / "strategies" / strategy_name
        assert {path.name for path in strategy_directory.iterdir()} == expected_strategy_files
        decisions = json.loads((strategy_directory / "decisions.json").read_text(encoding="utf-8"))
        assert decisions
