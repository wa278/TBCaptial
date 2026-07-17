"""Minimal, strict adapter around the pinned AKQuant backtest backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Final, cast

import akquant as aq
import pandas as pd

AKQUANT_BACKEND_VERSION: Final = "0.3.2"


class BackendCompatibilityError(RuntimeError):
    """The installed AKQuant backend does not match the accepted contract."""


class StrategyContractError(RuntimeError):
    """A user strategy violated the TBCaptial strategy contract."""


@dataclass(frozen=True, slots=True)
class BarView:
    """Backend-neutral, immutable daily bar visible to a TBCaptial strategy."""

    timestamp_ns: int
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_akquant(cls, bar: aq.Bar) -> BarView:
        """Copy an AKQuant bar so no mutable backend object escapes."""
        return cls(
            timestamp_ns=int(bar.timestamp),
            symbol=str(bar.symbol),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )


@dataclass(frozen=True, slots=True)
class BarSlice:
    """One complete, deterministically ordered cross-section."""

    timestamp_ns: int
    bars: Mapping[str, BarView]

    @classmethod
    def build(cls, timestamp_ns: int, bars: Mapping[str, BarView]) -> BarSlice:
        """Freeze bars in symbol order."""
        ordered = {symbol: bars[symbol] for symbol in sorted(bars)}
        return cls(timestamp_ns=timestamp_ns, bars=MappingProxyType(ordered))

    @property
    def symbols(self) -> tuple[str, ...]:
        """Return the stable symbol sequence."""
        return tuple(self.bars)


class BarSliceStrategy(ABC):
    """Public strategy surface used by the first AKQuant adapter slice."""

    @abstractmethod
    def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
        """Handle one complete market timestamp."""


class StrategyContext:
    """Controlled target-portfolio command surface for a user strategy."""

    __slots__ = ("_bridge", "_timestamp_ns")

    def __init__(self, bridge: _TBCaptialStrategyBridge, timestamp_ns: int) -> None:
        self._bridge = bridge
        self._timestamp_ns = timestamp_ns

    @property
    def timestamp_ns(self) -> int:
        """Current decision timestamp."""
        return self._timestamp_ns

    def set_target_positions(
        self,
        targets: Mapping[str, int],
        *,
        liquidate_unmentioned: bool,
    ) -> tuple[str, ...]:
        """Submit one non-negative target-position intent for this timestamp."""
        normalized: dict[str, float] = {}
        for symbol, quantity in targets.items():
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
                raise StrategyContractError(
                    f"target quantity for {symbol!r} must be a non-negative integer"
                )
            normalized[str(symbol)] = float(quantity)
        return self._bridge.submit_target_positions(
            self._timestamp_ns,
            normalized,
            liquidate_unmentioned=liquidate_unmentioned,
        )

    def set_target_weights(
        self,
        targets: Mapping[str, float],
        *,
        liquidate_unmentioned: bool,
        tolerance: float = 0.0,
    ) -> tuple[str, ...]:
        """Submit one long-only target-weight intent for this timestamp."""
        normalized = {str(symbol): float(weight) for symbol, weight in targets.items()}
        if any(not isfinite(weight) or not 0.0 <= weight <= 1.0 for weight in normalized.values()):
            raise StrategyContractError("target weights must be finite values in [0, 1]")
        if sum(normalized.values()) > 1.0 + 1e-12:
            raise StrategyContractError("target weights must sum to at most 1")
        if not 0.0 <= tolerance <= 1.0:
            raise StrategyContractError("tolerance must be in [0, 1]")
        return self._bridge.submit_target_weights(
            self._timestamp_ns,
            normalized,
            liquidate_unmentioned=liquidate_unmentioned,
            tolerance=tolerance,
        )


class _TBCaptialStrategyBridge(aq.Strategy):
    """The only TBCaptial type allowed to inherit AKQuant Strategy."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        strategy: BarSliceStrategy,
        lot_size: int,
    ) -> None:
        super().__init__()
        self._expected_symbols = tuple(sorted(symbols))
        self._expected_set = frozenset(self._expected_symbols)
        self._user_strategy = strategy
        self._lot_size = lot_size
        self._pending: dict[int, dict[str, BarView]] = {}
        self._submitted_timestamps: set[int] = set()
        self._completed_slices: list[BarSlice] = []

    @property
    def completed_slices(self) -> tuple[BarSlice, ...]:
        """Expose immutable slice audit data to the result translator."""
        return tuple(self._completed_slices)

    def on_bar(self, bar: aq.Bar) -> None:
        """Bucket AKQuant per-symbol callbacks into one complete BarSlice."""
        view = BarView.from_akquant(bar)
        if view.symbol not in self._expected_set:
            raise BackendCompatibilityError(f"unexpected symbol from AKQuant: {view.symbol}")

        bucket = self._pending.setdefault(view.timestamp_ns, {})
        if view.symbol in bucket:
            raise BackendCompatibilityError(
                f"duplicate AKQuant bar for {view.symbol} at {view.timestamp_ns}"
            )
        bucket[view.symbol] = view
        if len(bucket) < len(self._expected_symbols):
            return
        if frozenset(bucket) != self._expected_set:
            raise BackendCompatibilityError(
                f"incomplete cross-section at {view.timestamp_ns}: {sorted(bucket)}"
            )

        self._pending.pop(view.timestamp_ns)
        bar_slice = BarSlice.build(view.timestamp_ns, bucket)
        self._completed_slices.append(bar_slice)
        self._user_strategy.on_bars(StrategyContext(self, view.timestamp_ns), bar_slice)

    def on_stop(self) -> None:
        """Reject incomplete final cross-sections instead of silently dropping them."""
        if self._pending:
            pending = {timestamp: sorted(bars) for timestamp, bars in self._pending.items()}
            raise BackendCompatibilityError(f"incomplete final cross-sections: {pending}")

    def _claim_intent(self, timestamp_ns: int) -> None:
        if timestamp_ns in self._submitted_timestamps:
            raise StrategyContractError(
                f"only one target intent is allowed at timestamp {timestamp_ns}"
            )
        self._submitted_timestamps.add(timestamp_ns)

    def submit_target_positions(
        self,
        timestamp_ns: int,
        targets: Mapping[str, float],
        *,
        liquidate_unmentioned: bool,
    ) -> tuple[str, ...]:
        """Map a TBCaptial target-position intent to AKQuant."""
        self._claim_intent(timestamp_ns)
        normalized_targets: dict[str, float] = {}
        for symbol, target in sorted(targets.items()):
            if symbol not in self._expected_set:
                raise StrategyContractError(f"unknown target symbol: {symbol}")
            current = int(self.get_position(symbol))
            desired = int(target)
            if desired > current:
                buy_quantity = desired - current
                desired = current + buy_quantity // self._lot_size * self._lot_size
            elif 0 < desired < current:
                sell_quantity = current - desired
                desired = current - sell_quantity // self._lot_size * self._lot_size
            normalized_targets[symbol] = float(desired)
        order_ids = self.rebalance_positions(
            normalized_targets,
            liquidate_unmentioned=liquidate_unmentioned,
            rebalance_tolerance=0.0,
            allow_short=False,
            missing_price_mode="fail",
        )
        return tuple(str(order_id) for order_id in order_ids)

    def submit_target_weights(
        self,
        timestamp_ns: int,
        targets: Mapping[str, float],
        *,
        liquidate_unmentioned: bool,
        tolerance: float,
    ) -> tuple[str, ...]:
        """Map a TBCaptial target-weight intent to AKQuant."""
        self._claim_intent(timestamp_ns)
        order_ids = self.rebalance_weights(
            dict(sorted(targets.items())),
            liquidate_unmentioned=liquidate_unmentioned,
            allow_leverage=False,
            rebalance_tolerance=tolerance,
        )
        return tuple(str(order_id) for order_id in order_ids)


@dataclass(frozen=True, slots=True)
class AkQuantRunConfig:
    """Supported subset of AKQuant execution configuration."""

    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    min_commission: float = 5.0
    lot_size: int = 100
    volume_limit_pct: float = 1.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if not 0.0 < self.volume_limit_pct <= 1.0:
            raise ValueError("volume_limit_pct must be in (0, 1]")
        for name in (
            "commission_rate",
            "stamp_tax_rate",
            "transfer_fee_rate",
            "min_commission",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


class BacktestResult:
    """Backend-neutral copies of the AKQuant result plus bridge audit data."""

    __slots__ = ("_equity_curve", "_executions", "_orders", "_positions", "bar_slices")

    def __init__(self, raw_result: Any, bridge: _TBCaptialStrategyBridge) -> None:
        self._equity_curve = cast(pd.Series, raw_result.equity_curve.copy(deep=True))
        self._positions = cast(pd.DataFrame, raw_result.positions.copy(deep=True))
        self._orders = cast(pd.DataFrame, raw_result.orders_df.copy(deep=True))
        self._executions = cast(pd.DataFrame, raw_result.executions_df.copy(deep=True))
        self.bar_slices = bridge.completed_slices

    @property
    def equity_curve(self) -> pd.Series:
        """Return a defensive equity-curve copy."""
        return self._equity_curve.copy(deep=True)

    @property
    def positions(self) -> pd.DataFrame:
        """Return a defensive position-history copy."""
        return self._positions.copy(deep=True)

    @property
    def orders(self) -> pd.DataFrame:
        """Return a defensive order-history copy."""
        return self._orders.copy(deep=True)

    @property
    def executions(self) -> pd.DataFrame:
        """Return a defensive execution-history copy."""
        return self._executions.copy(deep=True)


class AkQuantBacktestEngine:
    """Strict TBCaptial entry point for the pinned AKQuant backend."""

    def __init__(self) -> None:
        if aq.__version__ != AKQUANT_BACKEND_VERSION:
            raise BackendCompatibilityError(
                f"AKQuant {AKQUANT_BACKEND_VERSION} is required; found {aq.__version__}"
            )

    def run(
        self,
        data: Mapping[str, pd.DataFrame],
        strategy: BarSliceStrategy,
        *,
        config: AkQuantRunConfig | None = None,
    ) -> BacktestResult:
        """Run a deterministic long-only A-share backtest through AKQuant."""
        resolved = config or AkQuantRunConfig()
        symbols = tuple(sorted(str(symbol) for symbol in data))
        if not symbols:
            raise ValueError("at least one symbol is required")

        normalized_data: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = data[symbol].copy(deep=True)
            if frame.empty:
                raise ValueError(f"market data for {symbol} is empty")
            frame["symbol"] = symbol
            normalized_data[symbol] = frame.sort_index(kind="stable")

        bridge = _TBCaptialStrategyBridge(
            symbols=symbols,
            strategy=strategy,
            lot_size=resolved.lot_size,
        )
        raw_result = aq.run_backtest(
            data=normalized_data,
            strategy=bridge,
            symbols=list(symbols),
            initial_cash=resolved.initial_cash,
            commission_rate=resolved.commission_rate,
            stamp_tax_rate=resolved.stamp_tax_rate,
            transfer_fee_rate=resolved.transfer_fee_rate,
            min_commission=resolved.min_commission,
            volume_limit_pct=resolved.volume_limit_pct,
            timezone="Asia/Shanghai",
            t_plus_one=True,
            lot_size=resolved.lot_size,
            fill_policy={
                "price_basis": "open",
                "bar_offset": 1,
                "temporal": "next_event",
            },
            strategy_runtime_config={"error_mode": "raise", "re_raise_on_error": True},
            strict_strategy_params=True,
            show_progress=False,
        )
        return BacktestResult(raw_result, bridge)
