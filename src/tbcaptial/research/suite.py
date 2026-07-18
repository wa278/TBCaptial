"""Execution and performance calculation for the three-factor research suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt

import pandas as pd

from tbcaptial.backtest import AkQuantBacktestEngine, AkQuantRunConfig, BacktestResult

from .raw_data import RawResearchDataset
from .strategies import FactorStrategy, build_default_factor_strategies

TRADING_DAYS_PER_YEAR = 252.0
STAMP_TAX_EFFECTIVE_DATE = date(2023, 8, 28)


@dataclass(frozen=True, slots=True)
class FactorSuiteConfig:
    """Execution settings shared by all three factor backtests."""

    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    min_commission: float = 5.0
    lot_size: int = 100
    volume_limit_pct: float = 1.0

    def to_engine_config(self) -> AkQuantRunConfig:
        """Translate the suite settings to the accepted backend subset."""
        return AkQuantRunConfig(
            initial_cash=self.initial_cash,
            commission_rate=self.commission_rate,
            stamp_tax_rate=self.stamp_tax_rate,
            transfer_fee_rate=self.transfer_fee_rate,
            min_commission=self.min_commission,
            lot_size=self.lot_size,
            volume_limit_pct=self.volume_limit_pct,
        )


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Small, auditable performance summary for one strategy."""

    strategy: str
    start_date: str
    end_date: str
    observations: int
    ending_equity: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    calmar_ratio: float
    benchmark_total_return: float
    excess_total_return: float
    orders: int
    filled_orders: int
    executions: int
    rejected_orders: int
    unresolved_orders: int
    decisions: int
    total_commission: float
    traded_notional: float

    def as_dict(self) -> dict[str, str | int | float]:
        """Return a stable tabular/JSON representation."""
        return {
            "strategy": self.strategy,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "observations": self.observations,
            "ending_equity": self.ending_equity,
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "annualized_volatility": self.annualized_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "calmar_ratio": self.calmar_ratio,
            "benchmark_total_return": self.benchmark_total_return,
            "excess_total_return": self.excess_total_return,
            "orders": self.orders,
            "filled_orders": self.filled_orders,
            "executions": self.executions,
            "rejected_orders": self.rejected_orders,
            "unresolved_orders": self.unresolved_orders,
            "decisions": self.decisions,
            "total_commission": self.total_commission,
            "traded_notional": self.traded_notional,
        }


@dataclass(frozen=True, slots=True)
class FactorBacktestRun:
    """One strategy, its backend result, and normalized daily analytics."""

    strategy: FactorStrategy
    result: BacktestResult
    equity: pd.Series
    drawdown: pd.Series
    metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class FactorSuiteResult:
    """Three factor runs plus a common benchmark equity curve."""

    dataset: RawResearchDataset
    config: FactorSuiteConfig
    benchmark_equity: pd.Series
    runs: tuple[FactorBacktestRun, ...]
    warnings: tuple[str, ...]


def _normalize_equity(series: pd.Series, name: str) -> pd.Series:
    values = pd.to_numeric(series, errors="raise").astype(float)
    index = pd.DatetimeIndex(pd.to_datetime(values.index, utc=True)).tz_convert("Asia/Shanghai")
    normalized = pd.Series(values.to_numpy(), index=index, name=name).sort_index(kind="stable")
    if normalized.index.has_duplicates:
        normalized = normalized.groupby(level=0, sort=True).last()
    if normalized.empty or normalized.isna().any() or (normalized <= 0.0).any():
        raise ValueError(f"invalid equity curve returned for {name}")
    return normalized


def _drawdown(equity: pd.Series) -> pd.Series:
    drawdown = equity / equity.cummax() - 1.0
    drawdown.name = equity.name
    return drawdown


def _benchmark_equity(dataset: RawResearchDataset, initial_cash: float) -> pd.Series:
    close = dataset.benchmark_close.astype(float)
    return (close / float(close.iloc[0]) * initial_cash).rename(dataset.benchmark_symbol)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0.0 else 0.0


def calculate_metrics(
    strategy: FactorStrategy,
    result: BacktestResult,
    equity: pd.Series,
    benchmark_equity: pd.Series,
) -> PerformanceMetrics:
    """Calculate zero-risk-rate daily metrics from a backend-neutral result."""
    returns = equity.pct_change().dropna()
    periods = max(len(equity) - 1, 1)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    annualized_return = float(
        (equity.iloc[-1] / equity.iloc[0]) ** (TRADING_DAYS_PER_YEAR / periods) - 1.0
    )
    daily_volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    annualized_volatility = daily_volatility * sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = _safe_ratio(float(returns.mean()) * sqrt(TRADING_DAYS_PER_YEAR), daily_volatility)
    drawdown = _drawdown(equity)
    max_drawdown = float(drawdown.min())
    calmar = _safe_ratio(annualized_return, abs(max_drawdown))

    benchmark_aligned = benchmark_equity.reindex(equity.index).dropna()
    if benchmark_aligned.empty:
        raise ValueError("benchmark and strategy equity have no aligned dates")
    benchmark_return = float(benchmark_aligned.iloc[-1] / benchmark_aligned.iloc[0] - 1.0)

    orders = result.orders
    executions = result.executions
    filled_orders = 0
    rejected_orders = 0
    unresolved_orders = 0
    if "status" in orders:
        order_status = orders["status"].astype(str).str.lower()
        filled_orders = int(order_status.eq("filled").sum())
        rejected_orders = int(order_status.eq("rejected").sum())
        terminal_statuses = {"canceled", "cancelled", "expired", "filled", "rejected"}
        unresolved_orders = int((~order_status.isin(terminal_statuses)).sum())
    commissions = (
        pd.to_numeric(executions["commission"], errors="raise").astype(float)
        if "commission" in executions
        else pd.Series(dtype=float)
    )
    if {"quantity", "price"}.issubset(executions.columns):
        traded_notional = float(
            (
                pd.to_numeric(executions["quantity"], errors="raise").astype(float).abs()
                * pd.to_numeric(executions["price"], errors="raise").astype(float)
            ).sum()
        )
    else:
        traded_notional = 0.0
    return PerformanceMetrics(
        strategy=strategy.factor_name,
        start_date=equity.index[0].date().isoformat(),
        end_date=equity.index[-1].date().isoformat(),
        observations=len(equity),
        ending_equity=float(equity.iloc[-1]),
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        calmar_ratio=calmar,
        benchmark_total_return=benchmark_return,
        excess_total_return=total_return - benchmark_return,
        orders=len(orders),
        filled_orders=filled_orders,
        executions=len(executions),
        rejected_orders=rejected_orders,
        unresolved_orders=unresolved_orders,
        decisions=len(strategy.decisions),
        total_commission=float(commissions.sum()),
        traded_notional=traded_notional,
    )


def run_factor_suite(
    dataset: RawResearchDataset,
    *,
    config: FactorSuiteConfig | None = None,
) -> FactorSuiteResult:
    """Run the standard three strategies against one verified Raw dataset."""
    resolved = config or FactorSuiteConfig()
    if dataset.start_date < STAMP_TAX_EFFECTIVE_DATE:
        raise ValueError(
            "the fixed 0.05% stamp-tax model is only valid from "
            f"{STAMP_TAX_EFFECTIVE_DATE}; restrict the research start date"
        )
    benchmark = _benchmark_equity(dataset, resolved.initial_cash)
    engine_config = resolved.to_engine_config()
    runs: list[FactorBacktestRun] = []
    for strategy in build_default_factor_strategies():
        result = AkQuantBacktestEngine().run(
            dataset.market_data,
            strategy,
            config=engine_config,
        )
        equity = _normalize_equity(result.equity_curve, strategy.factor_name)
        metrics = calculate_metrics(strategy, result, equity, benchmark)
        runs.append(
            FactorBacktestRun(
                strategy=strategy,
                result=result,
                equity=equity,
                drawdown=_drawdown(equity),
                metrics=metrics,
            )
        )
    rejected_orders = sum(run.metrics.rejected_orders for run in runs)
    warnings = dataset.warnings
    if rejected_orders:
        warnings += (
            f"AKQuant rejected {rejected_orders} target orders under execution constraints; "
            "each target is reasserted once at the next close and results reflect actual fills, "
            "so inspect each strategy's orders.parquet.",
        )
    unresolved_orders = sum(run.metrics.unresolved_orders for run in runs)
    if unresolved_orders:
        warnings += (
            f"{unresolved_orders} orders remain non-terminal at the final date because no later "
            "bar can execute them; ending equity excludes those intended fills.",
        )
    return FactorSuiteResult(
        dataset=dataset,
        config=resolved,
        benchmark_equity=benchmark,
        runs=tuple(runs),
        warnings=warnings,
    )
