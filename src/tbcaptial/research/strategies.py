"""Long-only factor strategies for the first local research suite."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

import numpy as np

from tbcaptial.backtest import BarSlice, BarSliceStrategy, StrategyContext


@dataclass(frozen=True, slots=True)
class FactorDecision:
    """Auditable factor scores and portfolio weights at one close."""

    timestamp_ns: int
    scores: Mapping[str, float]
    target_weights: Mapping[str, float]

    @classmethod
    def build(
        cls,
        timestamp_ns: int,
        scores: Mapping[str, float],
        target_weights: Mapping[str, float],
    ) -> FactorDecision:
        """Create a deterministically ordered immutable decision."""
        return cls(
            timestamp_ns=timestamp_ns,
            scores=MappingProxyType(dict(sorted(scores.items()))),
            target_weights=MappingProxyType(dict(sorted(target_weights.items()))),
        )


class FactorStrategy(BarSliceStrategy, ABC):
    """History-buffered factor strategy with periodic target-weight decisions."""

    factor_name: str
    description: str

    def __init__(
        self,
        *,
        lookback: int,
        rebalance_every: int,
        gross_exposure: float = 0.90,
        tolerance: float = 0.01,
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be at least 2")
        if rebalance_every < 1:
            raise ValueError("rebalance_every must be positive")
        if not isfinite(gross_exposure) or not 0.0 < gross_exposure < 1.0:
            raise ValueError("gross_exposure must be finite and in (0, 1)")
        if not isfinite(tolerance) or not 0.0 <= tolerance <= 1.0:
            raise ValueError("tolerance must be finite and in [0, 1]")
        self.lookback = lookback
        self.rebalance_every = rebalance_every
        self.gross_exposure = gross_exposure
        self.tolerance = tolerance
        self._closes: dict[str, list[float]] = {}
        self._tradable: dict[str, list[bool]] = {}
        self._observations = 0
        self._decisions: list[FactorDecision] = []
        self._pending_retry: dict[str, float] | None = None

    @property
    def decisions(self) -> tuple[FactorDecision, ...]:
        """Return immutable decision audit records."""
        return tuple(self._decisions)

    @property
    def parameters(self) -> dict[str, bool | float | int | str]:
        """Return manifest-safe strategy parameters."""
        return {
            "factor_name": self.factor_name,
            "lookback": self.lookback,
            "rebalance_every": self.rebalance_every,
            "gross_exposure": self.gross_exposure,
            "tolerance": self.tolerance,
            "retry_target_next_bar": True,
            "require_full_tradable_window": True,
        }

    def on_bars(self, context: StrategyContext, bars: BarSlice) -> None:
        """Update close-only history and submit targets on the rebalance schedule."""
        if not self._closes:
            self._closes = {symbol: [] for symbol in bars.symbols}
            self._tradable = {symbol: [] for symbol in bars.symbols}
        if tuple(self._closes) != bars.symbols:
            raise RuntimeError("factor strategy symbol universe changed during replay")
        for symbol in bars.symbols:
            self._closes[symbol].append(bars.bars[symbol].close)
            self._tradable[symbol].append(bars.bars[symbol].volume > 0.0)
        self._observations += 1

        minimum = self.lookback + 1
        if self._observations < minimum:
            return
        if self._pending_retry is not None:
            context.set_target_weights(
                self._pending_retry,
                liquidate_unmentioned=True,
                tolerance=self.tolerance,
            )
            self._pending_retry = None
            return
        if (self._observations - minimum) % self.rebalance_every != 0:
            return

        eligible_symbols = {
            symbol
            for symbol, history in self._tradable.items()
            if all(history[-self.lookback - 1 :])
        }
        scores, targets = self._calculate_targets(self._closes, eligible_symbols)
        context.set_target_weights(
            targets,
            liquidate_unmentioned=True,
            tolerance=self.tolerance,
        )
        self._decisions.append(FactorDecision.build(bars.timestamp_ns, scores, targets))
        # With next-event fills, sell proceeds are unavailable when same-close buy orders are
        # risk-checked. Reassert once on the next close so a constrained rotation can complete
        # at the following open without recalculating the factor from future observations.
        self._pending_retry = dict(targets)

    @abstractmethod
    def _calculate_targets(
        self,
        closes: Mapping[str, list[float]],
        eligible_symbols: Collection[str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Calculate scores and long-only target weights from past and current closes."""


class CrossSectionalMomentumStrategy(FactorStrategy):
    """Hold the strongest positive medium-term performer."""

    factor_name = "momentum_60d"
    description = "60-day cross-sectional momentum; top positive stock, rebalance every 20 days"

    def __init__(self) -> None:
        super().__init__(lookback=60, rebalance_every=20, gross_exposure=0.90)

    def _calculate_targets(
        self,
        closes: Mapping[str, list[float]],
        eligible_symbols: Collection[str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        scores = {
            symbol: closes[symbol][-1] / closes[symbol][-self.lookback - 1] - 1.0
            for symbol in sorted(eligible_symbols)
        }
        eligible = [(symbol, score) for symbol, score in scores.items() if score > 0.0]
        targets: dict[str, float] = {}
        if eligible:
            selected = max(eligible, key=lambda item: (item[1], item[0]))[0]
            targets[selected] = self.gross_exposure
        return scores, targets


class ShortTermReversalStrategy(FactorStrategy):
    """Buy the most oversold stock after a negative five-day move."""

    factor_name = "reversal_5d"
    description = "5-day cross-sectional reversal; worst negative return, rebalance every 5 days"

    def __init__(self) -> None:
        super().__init__(lookback=5, rebalance_every=5, gross_exposure=0.85)

    def _calculate_targets(
        self,
        closes: Mapping[str, list[float]],
        eligible_symbols: Collection[str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        returns = {
            symbol: closes[symbol][-1] / closes[symbol][-self.lookback - 1] - 1.0
            for symbol in sorted(eligible_symbols)
        }
        scores = {symbol: -value for symbol, value in returns.items()}
        eligible = [(symbol, score) for symbol, score in scores.items() if score > 0.0]
        targets: dict[str, float] = {}
        if eligible:
            selected = max(eligible, key=lambda item: (item[1], item[0]))[0]
            targets[selected] = self.gross_exposure
        return scores, targets


class LowVolatilityStrategy(FactorStrategy):
    """Allocate across all stocks in proportion to inverse realized volatility."""

    factor_name = "low_volatility_20d"
    description = "20-day inverse-volatility allocation; rebalance every 20 days"

    def __init__(self) -> None:
        super().__init__(lookback=20, rebalance_every=20, gross_exposure=0.90)

    def _calculate_targets(
        self,
        closes: Mapping[str, list[float]],
        eligible_symbols: Collection[str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        inverse_volatility: dict[str, float] = {}
        for symbol in sorted(eligible_symbols):
            values = closes[symbol]
            window = np.asarray(values[-self.lookback - 1 :], dtype=float)
            volatility = float(np.std(np.diff(np.log(window)), ddof=1) * np.sqrt(252.0))
            if isfinite(volatility) and volatility > 0.0:
                inverse_volatility[symbol] = 1.0 / volatility
        denominator = sum(inverse_volatility.values())
        targets = {
            symbol: self.gross_exposure * score / denominator
            for symbol, score in inverse_volatility.items()
        }
        return inverse_volatility, targets


def build_default_factor_strategies() -> tuple[FactorStrategy, ...]:
    """Build fresh strategy instances for the standard three-backtest suite."""
    return (
        CrossSectionalMomentumStrategy(),
        ShortTermReversalStrategy(),
        LowVolatilityStrategy(),
    )
