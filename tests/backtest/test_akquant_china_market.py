"""Direct acceptance tests for AKQuant's China-market execution contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

import akquant as aq


class TPlusOneProbe(aq.Strategy):
    """Buy on day one and observe available shares before and after settlement."""

    def __init__(self) -> None:
        super().__init__()
        self.available: dict[str, int] = {}

    def on_bar(self, bar: aq.Bar) -> None:
        local = datetime.fromtimestamp(bar.timestamp / 1e9, tz=timezone(timedelta(hours=8)))
        if local.day == 4 and local.hour == 10:
            self.buy(bar.symbol, 100)
        elif local.day == 4 and local.hour == 14:
            self.available["same_day"] = int(self.get_available_position(bar.symbol))
        elif local.day == 5:
            self.available["next_day"] = int(self.get_available_position(bar.symbol))


def test_akquant_china_market_unlocks_stock_on_next_trading_day() -> None:
    """A same-day stock buy is unavailable to sell until the next day."""
    shanghai = timezone(timedelta(hours=8))
    timestamps = [
        datetime(2023, 1, 4, 10, 0, tzinfo=shanghai),
        datetime(2023, 1, 4, 14, 0, tzinfo=shanghai),
        datetime(2023, 1, 5, 10, 0, tzinfo=shanghai),
    ]
    bars = [
        aq.Bar(
            int(timestamp.timestamp() * 1e9),
            10.0,
            10.5,
            9.5,
            10.0,
            10_000.0,
            "000001.SZ",
        )
        for timestamp in timestamps
    ]

    engine = aq.Engine()
    engine.use_china_market()
    engine.set_cash(100_000.0)
    cast(Any, engine).set_fill_policy("close", 0, "same_cycle")
    engine.add_instrument(
        aq.Instrument(
            symbol="000001.SZ",
            asset_type=aq.AssetType.Stock,
            multiplier=1.0,
            margin_ratio=1.0,
            tick_size=0.01,
            lot_size=100.0,
        )
    )
    engine.add_bars(bars)
    strategy = TPlusOneProbe()

    engine.run(strategy, show_progress=False)

    assert strategy.available == {"same_day": 0, "next_day": 100}
