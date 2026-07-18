"""Offline tests for the executable AKShare download and verification scripts."""

from __future__ import annotations

import json
import sys
from importlib import util
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest


def load_script(module_name: str, filename: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / filename
    spec = util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


download = load_script("tbcaptial_download_script", "download_akshare_data.py")
preview = load_script("tbcaptial_preview_script", "preview_akshare_data.py")
summary = load_script("tbcaptial_summary_script", "summarize_akshare_data.py")
verify = load_script("tbcaptial_verify_script", "verify_akshare_download.py")


def test_auto_daily_source_circuit_breaks_to_sina(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One Eastmoney failure must move the rest of the run to Sina."""
    eastmoney_symbols: list[str] = []
    sina_symbols: list[str] = []

    monkeypatch.setattr(
        download.ak,
        "tool_trade_date_hist_sina",
        lambda: pd.DataFrame({"trade_date": [pd.Timestamp("2026-07-17").date()]}),
    )

    def fail_eastmoney(**kwargs: object) -> pd.DataFrame:
        eastmoney_symbols.append(str(kwargs["symbol"]))
        raise ConnectionError("provider closed the connection")

    def fetch_sina(**kwargs: object) -> pd.DataFrame:
        sina_symbols.append(str(kwargs["symbol"]))
        return pd.DataFrame(
            {
                "date": [pd.Timestamp("2026-07-17")],
                "open": [10.0],
                "high": [10.2],
                "low": [9.9],
                "close": [10.1],
                "volume": [1000.0],
            }
        )

    monkeypatch.setattr(download.ak, "stock_zh_a_hist", fail_eastmoney)
    monkeypatch.setattr(download.ak, "stock_zh_a_daily", fetch_sina)
    monkeypatch.setattr(
        download.ak,
        "stock_zh_index_daily",
        lambda **kwargs: pd.DataFrame(
            {
                "date": [pd.Timestamp("2026-07-17")],
                "open": [4000.0],
                "high": [4010.0],
                "low": [3990.0],
                "close": [4005.0],
                "volume": [1000.0],
            }
        ),
    )
    monkeypatch.setattr(download.time, "sleep", lambda seconds: None)

    exit_code = download.main(
        [
            "--data-root",
            str(tmp_path),
            "--start-date",
            "20260717",
            "--end-date",
            "20260717",
            "--symbols",
            "000001",
            "600000",
            "--daily-source",
            "auto",
            "--retries",
            "1",
            "--request-interval",
            "0",
        ]
    )

    assert exit_code == 0
    assert eastmoney_symbols == ["000001"]
    assert sina_symbols == ["sz000001", "sh600000"]

    manifest_path = next((tmp_path / "manifests" / "downloads").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [batch["endpoint"] for batch in manifest["batches"]] == [
        "tool_trade_date_hist_sina",
        "stock_zh_a_daily",
        "stock_zh_a_daily",
        "stock_zh_index_daily",
    ]
    assert verify.verify_download_manifest(tmp_path.resolve(), manifest_path)["status"] == "PASS"

    preview_exit_code = preview.main(
        [
            "--data-root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--dataset",
            "daily",
            "--symbol",
            "600000",
            "--rows",
            "1",
        ]
    )
    output = capsys.readouterr().out
    assert preview_exit_code == 0
    assert "[daily] endpoint=stock_zh_a_daily" in output
    assert "sh600000" in output

    result = summary.summarize(tmp_path.resolve())
    assert result["completed_runs"] == 1
    assert result["raw_rows"] == 4
    assert result["daily"] == {
        "symbols": 2,
        "raw_rows": 2,
        "unique_symbol_date_rows": 2,
        "duplicate_symbol_date_rows": 0,
        "date_range": {"start": "2026-07-17", "end": "2026-07-17"},
    }
    assert result["orphan_raw"] == {"batches": 0, "rows": 0, "daily_symbols": []}


def test_sina_symbol_prefixes_shanghai_and_shenzhen() -> None:
    assert download.sina_symbol("600000") == "sh600000"
    assert download.sina_symbol("000001") == "sz000001"
