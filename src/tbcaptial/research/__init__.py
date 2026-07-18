"""Research-only factor strategies and local backtest orchestration."""

from .raw_data import (
    RawResearchDataset,
    load_raw_research_dataset,
    select_latest_usable_download_manifest,
)
from .reporting import write_factor_suite_artifacts
from .strategies import (
    CrossSectionalMomentumStrategy,
    FactorDecision,
    FactorStrategy,
    LowVolatilityStrategy,
    ShortTermReversalStrategy,
    build_default_factor_strategies,
)
from .suite import (
    STAMP_TAX_EFFECTIVE_DATE,
    FactorBacktestRun,
    FactorSuiteConfig,
    FactorSuiteResult,
    PerformanceMetrics,
    run_factor_suite,
)

__all__ = [
    "STAMP_TAX_EFFECTIVE_DATE",
    "CrossSectionalMomentumStrategy",
    "FactorBacktestRun",
    "FactorDecision",
    "FactorStrategy",
    "FactorSuiteConfig",
    "FactorSuiteResult",
    "LowVolatilityStrategy",
    "PerformanceMetrics",
    "RawResearchDataset",
    "ShortTermReversalStrategy",
    "build_default_factor_strategies",
    "load_raw_research_dataset",
    "run_factor_suite",
    "select_latest_usable_download_manifest",
    "write_factor_suite_artifacts",
]
