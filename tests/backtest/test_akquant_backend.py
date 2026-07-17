"""Contract tests for the real AKQuant-backed TBCaptial entry point."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
import pandas.testing as pdt
import pytest

from tbcaptial.backtest import (
    AKQUANT_BACKEND_VERSION,
    AkQuantBacktestEngine,
    AkQuantRunConfig,
    BarSlice,
    BarSliceStrategy,
    StrategyContext,
    StrategyContractError,
)


def daily_bars(opens: Sequence[float]) -> pd.DataFrame:
    """Build deterministic, synthetic A-share daily bars."""
    index = pd.date_range(
        "2024-01-02 15:00:00",
        periods=len(opens),
        freq="B",
        tz="Asia/Shanghai",
    )
    values = [float(value) for value in opens]
    return pd.DataFrame(
        {
            "open": values,
            "high": [value + 1.0 for value in values],
            "low": [value - 1.0 for value in values],
            "close": [value + 0.5 for value in values],
            "volume": [100_000.0] * len(values),
        },
        index=index,
    )


def zero_fee_config(*, lot_size: int = 100) -> AkQuantRunConfig:
    """Return an execution config useful for exact price assertions."""
    return AkQuantRunConfig(
        initial_cash=100_000.0,
        commission_rate=0.0,
        stamp_tax_rate=0.0,
        transfer_fee_rate=0.0,
        min_commission=0.0,
        lot_size=lot_size,
    )


class RoundTripStrategy(BarSliceStrategy):
    """Buy first, then liquidate after the position passes T+1 settlement."""

    def __init__(self, symbol: str, target: int = 100) -> None:
        self.symbol = symbol
        self.target = target
        self.calls = 0

    def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
        del bars
        self.calls += 1
        if self.calls == 1:
            context.set_target_positions({self.symbol: self.target}, liquidate_unmentioned=True)
        elif self.calls == 3:
            context.set_target_positions({}, liquidate_unmentioned=True)


class RecordingStrategy(BarSliceStrategy):
    """Record complete cross-sections without trading."""

    def __init__(self) -> None:
        self.observed: list[tuple[int, tuple[str, ...], tuple[float, ...]]] = []

    def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
        del context
        self.observed.append(
            (
                bars.timestamp_ns,
                bars.symbols,
                tuple(bars.bars[symbol].close for symbol in bars.symbols),
            )
        )


def test_real_backend_version_and_next_open_execution() -> None:
    """The pinned backend must fill decisions on the following bar's open."""
    import akquant

    assert akquant.__version__ == AKQUANT_BACKEND_VERSION

    strategy = RoundTripStrategy("000001.SZ")
    result = AkQuantBacktestEngine().run(
        {"000001.SZ": daily_bars([10.0, 11.0, 12.0, 13.0])},
        strategy,
        config=zero_fee_config(),
    )

    executions = result.executions.reset_index(drop=True)
    assert strategy.calls == 4
    assert list(executions["side"].astype(str).str.lower()) == ["buy", "sell"]
    assert list(executions["quantity"].astype(float)) == [100.0, 100.0]
    assert list(executions["price"].astype(float)) == [11.0, 13.0]


def test_target_position_buy_delta_is_rounded_down_to_board_lot() -> None:
    """A 150-share target from flat must create one 100-share buy."""
    result = AkQuantBacktestEngine().run(
        {"000001.SZ": daily_bars([10.0, 11.0, 12.0, 13.0])},
        RoundTripStrategy("000001.SZ", target=150),
        config=zero_fee_config(lot_size=100),
    )

    assert list(result.executions["quantity"].astype(float)) == [100.0, 100.0]


def test_china_fee_model_charges_stamp_tax_only_on_sell() -> None:
    """The sell execution must cost more because it includes stamp tax."""
    result = AkQuantBacktestEngine().run(
        {"000001.SZ": daily_bars([10.0, 11.0, 12.0, 13.0])},
        RoundTripStrategy("000001.SZ"),
        config=AkQuantRunConfig(),
    )

    executions = result.executions.reset_index(drop=True)
    assert len(executions) == 2
    commissions = executions["commission"].astype(float).tolist()
    assert commissions[0] > 0.0
    assert commissions[1] > commissions[0]


def test_multisymbol_replay_is_complete_sorted_and_input_order_independent() -> None:
    """Every strategy call sees one sorted cross-section with stable results."""
    first_data = {
        "600000.SH": daily_bars([20.0, 21.0, 22.0]),
        "000001.SZ": daily_bars([10.0, 11.0, 12.0]),
    }
    second_data = dict(reversed(list(first_data.items())))
    first_strategy = RecordingStrategy()
    second_strategy = RecordingStrategy()

    first = AkQuantBacktestEngine().run(first_data, first_strategy, config=zero_fee_config())
    second = AkQuantBacktestEngine().run(second_data, second_strategy, config=zero_fee_config())

    assert first_strategy.observed == second_strategy.observed
    assert len(first_strategy.observed) == 3
    assert all(symbols == ("000001.SZ", "600000.SH") for _, symbols, _ in first_strategy.observed)
    pdt.assert_series_equal(first.equity_curve, second.equity_curve)
    pdt.assert_frame_equal(first.positions, second.positions)
    pdt.assert_frame_equal(first.orders, second.orders)
    pdt.assert_frame_equal(first.executions, second.executions)


class DoubleIntentStrategy(BarSliceStrategy):
    """Deliberately violate the one-intent-per-timestamp contract."""

    def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
        symbol = bars.symbols[0]
        context.set_target_positions({symbol: 100}, liquidate_unmentioned=True)
        context.set_target_positions({symbol: 200}, liquidate_unmentioned=True)


def test_strategy_cannot_submit_two_target_intents_in_one_slice() -> None:
    """Ambiguous same-timestamp target changes fail immediately."""
    with pytest.raises(StrategyContractError, match="only one target intent"):
        AkQuantBacktestEngine().run(
            {"000001.SZ": daily_bars([10.0, 11.0])},
            DoubleIntentStrategy(),
            config=zero_fee_config(),
        )


@pytest.mark.parametrize(
    "targets",
    [
        {"000001.SZ": -1},
        {"000001.SZ": 1.5},
        {"000001.SZ": True},
    ],
)
def test_invalid_target_positions_are_rejected(
    targets: Mapping[str, int],
) -> None:
    """Only non-negative integer share targets enter AKQuant."""

    class InvalidTargetStrategy(BarSliceStrategy):
        def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
            del bars
            context.set_target_positions(targets, liquidate_unmentioned=True)

    with pytest.raises(StrategyContractError, match="non-negative integer"):
        AkQuantBacktestEngine().run(
            {"000001.SZ": daily_bars([10.0, 11.0])},
            InvalidTargetStrategy(),
            config=zero_fee_config(),
        )
