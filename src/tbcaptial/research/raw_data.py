"""Strict, research-only loading of an explicit AKShare Raw download manifest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

import numpy as np
import pandas as pd
import pyarrow.parquet as pq  # type: ignore[import-untyped]

DAILY_ENDPOINTS: Final = frozenset({"stock_zh_a_daily", "stock_zh_a_hist"})
BENCHMARK_ENDPOINT: Final = "stock_zh_index_daily"
REQUIRED_COLUMNS: Final = ("open", "high", "low", "close", "volume")
EASTMONEY_COLUMN_ALIASES: Final = {
    "日期": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
}
SHANGHAI_TZ: Final = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class RawResearchDataset:
    """Verified Raw inputs for exploratory backtests, explicitly not a snapshot."""

    manifest_path: Path
    manifest_sha256: str
    download_run_id: str
    benchmark_symbol: str
    start_date: date
    end_date: date
    observations: int
    symbols: tuple[str, ...]
    warnings: tuple[str, ...]
    _market_data: Mapping[str, pd.DataFrame]
    _benchmark_close: pd.Series
    _synthetic_bar_counts: Mapping[str, int]

    @property
    def market_data(self) -> dict[str, pd.DataFrame]:
        """Return defensive copies of aligned OHLCV frames."""
        return {symbol: frame.copy(deep=True) for symbol, frame in self._market_data.items()}

    @property
    def benchmark_close(self) -> pd.Series:
        """Return a defensive copy of the aligned benchmark close."""
        return self._benchmark_close.copy(deep=True)

    @property
    def synthetic_bar_counts(self) -> dict[str, int]:
        """Return counts of zero-volume carry-forward bars inserted per stock."""
        return dict(self._synthetic_bar_counts)


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return cast(dict[str, Any], value)


def _as_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _as_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be an array")
    return cast(list[object], value)


def _resolve_member(data_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"manifest member must be relative: {relative_path}")
    resolved = (data_root / candidate).resolve()
    if not resolved.is_relative_to(data_root):
        raise ValueError(f"manifest member escapes data root: {relative_path}")
    return resolved


def _verify_file(data_root: Path, metadata: Mapping[str, object]) -> Path:
    relative_path = str(metadata["path"])
    path = _resolve_member(data_root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"manifest member is missing: {relative_path}")
    expected_bytes = metadata["bytes"]
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int):
        raise TypeError(f"manifest byte count must be an integer: {relative_path}")
    if path.stat().st_size != expected_bytes:
        raise AssertionError(f"manifest byte count mismatch: {relative_path}")
    if sha256_file(path) != str(metadata["sha256"]):
        raise AssertionError(f"manifest SHA-256 mismatch: {relative_path}")
    return path


def _batch_files(data_root: Path, batch: Mapping[str, Any]) -> dict[str, Path]:
    manifest_metadata = _as_object(batch.get("manifest"), "batch.manifest")
    _verify_file(data_root, manifest_metadata)

    resolved: dict[str, Path] = {}
    for raw_metadata in _as_list(batch.get("files"), "batch.files"):
        metadata = _as_object(raw_metadata, "batch file")
        path = _verify_file(data_root, metadata)
        resolved[path.name] = path
    required = {"data.parquet", "request.json", "response.json"}
    if not required.issubset(resolved):
        raise AssertionError(f"batch is missing files: {sorted(required - resolved.keys())}")
    expected_rows = batch["rows"]
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int):
        raise TypeError(f"batch row count must be an integer: {batch.get('batch_id')}")
    if pq.read_metadata(resolved["data.parquet"]).num_rows != expected_rows:
        raise AssertionError(f"batch Parquet row mismatch: {batch.get('batch_id')}")
    return resolved


def _canonical_symbol(raw_symbol: object) -> str:
    value = str(raw_symbol).strip().lower()
    if len(value) == 8 and value[:2] in {"sh", "sz", "bj"} and value[2:].isdigit():
        return f"{value[2:]}.{value[:2].upper()}"
    if len(value) == 6 and value.isdigit():
        if value.startswith(("5", "6", "9")):
            exchange = "SH"
        elif value.startswith(("4", "8")):
            exchange = "BJ"
        else:
            exchange = "SZ"
        return f"{value}.{exchange}"
    raise ValueError(f"unsupported AKShare A-share symbol: {raw_symbol!r}")


def _normalize_ohlcv(path: Path, symbol: str, endpoint: str) -> pd.DataFrame:
    raw = pd.read_parquet(path).rename(columns=EASTMONEY_COLUMN_ALIASES)
    required = {"date", *REQUIRED_COLUMNS}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{symbol} Raw data is missing columns: {sorted(missing)}")

    dates = pd.DatetimeIndex(pd.to_datetime(raw["date"], errors="raise"))
    dates = dates.tz_localize(SHANGHAI_TZ) if dates.tz is None else dates.tz_convert(SHANGHAI_TZ)
    dates = dates.normalize() + pd.Timedelta(hours=15)

    frame = raw.loc[:, list(REQUIRED_COLUMNS)].copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    if endpoint == "stock_zh_a_hist":
        # Eastmoney's 成交量 is expressed in hands, while AKQuant expects shares.
        frame["volume"] *= 100.0
    frame.index = dates
    frame.index.name = "timestamp"
    frame = frame.sort_index(kind="stable")

    if frame.index.has_duplicates:
        raise ValueError(f"{symbol} contains duplicate trading dates")
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError(f"{symbol} contains NaN or infinite OHLCV values")
    prices = frame.loc[:, ["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (prices <= 0.0).any():
        raise ValueError(f"{symbol} contains non-positive prices")
    if (frame["volume"] < 0.0).any():
        raise ValueError(f"{symbol} contains negative volume")
    high_floor = np.maximum.reduce(
        [
            frame["open"].to_numpy(dtype=float),
            frame["close"].to_numpy(dtype=float),
            frame["low"].to_numpy(dtype=float),
        ]
    )
    if (frame["high"].to_numpy(dtype=float) < high_floor).any():
        raise ValueError(f"{symbol} contains invalid high prices")
    low_ceiling = np.minimum.reduce(
        [
            frame["open"].to_numpy(dtype=float),
            frame["close"].to_numpy(dtype=float),
            frame["high"].to_numpy(dtype=float),
        ]
    )
    if (frame["low"].to_numpy(dtype=float) > low_ceiling).any():
        raise ValueError(f"{symbol} contains invalid low prices")
    return frame


def _normalize_benchmark(path: Path, symbol: str) -> pd.Series:
    raw = pd.read_parquet(path)
    if not {"date", "close"}.issubset(raw.columns):
        raise ValueError("benchmark Raw data must contain date and close")
    dates = pd.DatetimeIndex(pd.to_datetime(raw["date"], errors="raise"))
    dates = dates.tz_localize(SHANGHAI_TZ) if dates.tz is None else dates.tz_convert(SHANGHAI_TZ)
    dates = dates.normalize() + pd.Timedelta(hours=15)
    values = pd.to_numeric(raw["close"], errors="raise").astype(float).to_numpy()
    close = pd.Series(values, index=dates, name=symbol).sort_index(kind="stable")
    if close.index.has_duplicates or not np.isfinite(close.to_numpy(dtype=float)).all():
        raise ValueError("benchmark contains duplicate dates or invalid close values")
    if (close <= 0.0).any():
        raise ValueError("benchmark contains non-positive close values")
    return close


def _date_boundary(value: date, *, end: bool) -> pd.Timestamp:
    boundary = pd.Timestamp(value).tz_localize(SHANGHAI_TZ)
    return boundary + pd.Timedelta(hours=23 if end else 0, minutes=59 if end else 0)


def load_raw_research_dataset(
    data_root: Path,
    manifest_path: Path,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    min_observations: int = 120,
) -> RawResearchDataset:
    """Verify and align Raw AKShare bars for exploratory, non-production research."""
    if min_observations < 2:
        raise ValueError("min_observations must be at least 2")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start date must not be later than end date")
    resolved_root = data_root.expanduser().resolve()
    resolved_manifest = manifest_path.expanduser().resolve()
    if not resolved_manifest.is_relative_to(resolved_root):
        raise ValueError("download manifest must be inside the configured data root")
    manifest = _load_json(resolved_manifest)
    if manifest.get("status") != "COMPLETED" or manifest.get("provider") != "akshare":
        raise ValueError("research input must be a completed AKShare download manifest")

    market_data: dict[str, pd.DataFrame] = {}
    benchmark: pd.Series | None = None
    benchmark_symbol = ""
    for raw_batch in _as_list(manifest.get("batches"), "manifest.batches"):
        batch = _as_object(raw_batch, "batch")
        endpoint = str(batch.get("endpoint"))
        if endpoint not in DAILY_ENDPOINTS and endpoint != BENCHMARK_ENDPOINT:
            continue
        files = _batch_files(resolved_root, batch)
        request = _load_json(files["request.json"])
        parameters = _as_object(request.get("parameters"), "request.parameters")

        if endpoint in DAILY_ENDPOINTS:
            if str(parameters.get("adjust", "")) != "":
                raise ValueError("mixed or adjusted Raw prices are not supported by this loader")
            symbol = _canonical_symbol(parameters.get("symbol"))
            if symbol in market_data:
                raise ValueError(f"duplicate daily batch for {symbol} in one manifest")
            market_data[symbol] = _normalize_ohlcv(files["data.parquet"], symbol, endpoint)
        else:
            if benchmark is not None:
                raise ValueError("download manifest contains more than one benchmark batch")
            benchmark_symbol = str(parameters.get("symbol", "benchmark")).upper()
            benchmark = _normalize_benchmark(files["data.parquet"], benchmark_symbol)

    if len(market_data) < 2:
        raise ValueError("factor research requires at least two stock series")
    if benchmark is None:
        raise ValueError("download manifest does not contain a benchmark series")

    stock_starts = [pd.DatetimeIndex(frame.index).min() for frame in market_data.values()]
    stock_ends = [pd.DatetimeIndex(frame.index).max() for frame in market_data.values()]
    benchmark_index = pd.DatetimeIndex(benchmark.index)
    shared_start = max([*stock_starts, benchmark_index.min()])
    shared_end = min([*stock_ends, benchmark_index.max()])
    if shared_start > shared_end:
        raise ValueError("stock and benchmark series do not share a date range")
    common_index = benchmark_index[
        (benchmark_index >= shared_start) & (benchmark_index <= shared_end)
    ].sort_values()
    if start_date is not None:
        common_index = common_index[common_index >= _date_boundary(start_date, end=False)]
    if end_date is not None:
        common_index = common_index[common_index <= _date_boundary(end_date, end=True)]
    if len(common_index) < min_observations:
        raise ValueError(
            f"only {len(common_index)} aligned observations; at least {min_observations} required"
        )

    aligned: dict[str, pd.DataFrame] = {}
    synthetic_bar_counts: dict[str, int] = {}
    for symbol, frame in sorted(market_data.items()):
        frame_index = pd.DatetimeIndex(frame.index)
        expanded_index = frame_index.union(common_index).sort_values()
        expanded = frame.reindex(expanded_index)
        expanded.loc[:, ["open", "high", "low", "close"]] = expanded.loc[
            :, ["open", "high", "low", "close"]
        ].ffill()
        aligned_frame = expanded.loc[common_index, list(REQUIRED_COLUMNS)].copy(deep=True)
        synthetic_mask = ~common_index.isin(frame_index)
        aligned_frame.loc[synthetic_mask, "volume"] = 0.0
        if aligned_frame.isna().any(axis=None):
            raise ValueError(f"cannot carry forward missing bars for {symbol}")
        aligned_frame.index.name = "timestamp"
        aligned[symbol] = aligned_frame
        synthetic_bar_counts[symbol] = int(synthetic_mask.sum())
    aligned_benchmark = benchmark.loc[common_index].copy(deep=True)
    warnings: tuple[str, ...] = (
        "Input is a verified Raw download manifest, not a validated Silver snapshot.",
        "Prices are unadjusted; dividends, splits and survivorship can distort factor returns.",
        (
            "The current universe contains only manifest-listed survivors and is not "
            "investable evidence."
        ),
    )
    total_synthetic_bars = sum(synthetic_bar_counts.values())
    if total_synthetic_bars:
        counts = ", ".join(
            f"{symbol}={count}" for symbol, count in synthetic_bar_counts.items() if count
        )
        warnings += (
            f"Inserted {total_synthetic_bars} zero-volume carry-forward bars for missing stock "
            f"dates ({counts}); they are non-tradable suspension/data-gap placeholders.",
        )
    return RawResearchDataset(
        manifest_path=resolved_manifest,
        manifest_sha256=sha256_file(resolved_manifest),
        download_run_id=str(manifest["run_id"]),
        benchmark_symbol=benchmark_symbol,
        start_date=common_index[0].date(),
        end_date=common_index[-1].date(),
        observations=len(common_index),
        symbols=tuple(aligned),
        warnings=warnings,
        _market_data=MappingProxyType(aligned),
        _benchmark_close=aligned_benchmark,
        _synthetic_bar_counts=MappingProxyType(synthetic_bar_counts),
    )


def select_latest_usable_download_manifest(
    data_root: Path,
    *,
    min_symbols: int = 3,
    min_rows: int = 120,
) -> Path:
    """Select the newest completed Raw manifest with enough stock history for demos."""
    resolved_root = data_root.expanduser().resolve()
    candidates = sorted((resolved_root / "manifests" / "downloads").glob("*.json"), reverse=True)
    for path in candidates:
        manifest = _load_json(path)
        if manifest.get("status") != "COMPLETED" or manifest.get("provider") != "akshare":
            continue
        qualifying = 0
        has_benchmark = False
        for raw_batch in _as_list(manifest.get("batches", []), "manifest.batches"):
            batch = _as_object(raw_batch, "batch")
            if str(batch.get("endpoint")) == BENCHMARK_ENDPOINT and int(batch.get("rows", 0)) > 0:
                has_benchmark = True
            if (
                str(batch.get("endpoint")) in DAILY_ENDPOINTS
                and int(batch.get("rows", 0)) >= min_rows
            ):
                qualifying += 1
        if qualifying >= min_symbols and has_benchmark:
            return path.resolve()
    raise FileNotFoundError(
        f"no completed download manifest has {min_symbols} stocks with {min_rows} rows"
    )
