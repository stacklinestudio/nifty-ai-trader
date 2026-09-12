"""Phase 2 Piece 10, Requirements 14, 15: the combined regime -> eligible
strategies -> deterministic selection pipeline, and the historical
reporting utility built on top of it.

For a single real point in time, `evaluate_regime_and_strategy`
deterministically answers exactly the Piece 10 acceptance-criterion
question: what regime, which strategies were eligible, which one (if
any) was selected, and why. No-look-ahead is inherited structurally --
`execution/regime_detection.py::detect_regime` truncates candles to
`<= as_of` itself, and `strategy/eligibility.py`/`strategy/selection.py`
only ever read MemoryStore evidence timestamped at or before `as_of`.

`historical_regime_strategy_report` runs that same real function across
a caller-supplied series of real timestamps -- read-only, reports facts
only. It never claims profitability from regime segmentation, and never
tunes any threshold (in this module, `execution/regime_detection.py`,
or `strategy/*`) using its own output -- every threshold this pipeline
depends on was fixed before this report was ever run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from execution.regime_detection import INSUFFICIENT_DATA, RegimeRecord, detect_regime
from learning.memory import MemoryStore
from strategy.eligibility import EligibilityResult, evaluate_registry_eligibility
from strategy.registry import DEFAULT_REGISTRY, StrategyDefinition
from strategy.selection import NO_STRATEGY_SELECTED, SelectionResult, select_strategy


@dataclass(frozen=True)
class RegimeStrategyEvaluation:
    timestamp: str
    regime_record: RegimeRecord
    eligibility_results: tuple[EligibilityResult, ...]
    selection: SelectionResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "regime_record": self.regime_record.to_dict(),
            "eligibility_results": [r.to_dict() for r in self.eligibility_results],
            "selection": self.selection.to_dict(),
        }


def evaluate_regime_and_strategy(
    candles: pd.DataFrame,
    as_of: datetime,
    store: MemoryStore,
    registry: tuple[StrategyDefinition, ...] = DEFAULT_REGISTRY,
) -> RegimeStrategyEvaluation:
    regime_record = detect_regime(candles, as_of)
    if regime_record.regime == INSUFFICIENT_DATA:
        eligibility_results = tuple(
            EligibilityResult(
                s.strategy_id, s.version, regime_record.regime, False, "N/A",
                "regime could not be determined (insufficient real data) -- no strategy can be evaluated.",
            )
            for s in registry
        )
        selection = SelectionResult(
            regime_record.regime, None, None,
            f"{NO_STRATEGY_SELECTED} -- regime is INSUFFICIENT_DATA.", len(registry), 0,
        )
        return RegimeStrategyEvaluation(as_of.isoformat(), regime_record, eligibility_results, selection)

    eligibility_results = evaluate_registry_eligibility(store, registry, regime_record.regime, as_of)
    selection = select_strategy(store, regime_record.regime, eligibility_results, as_of)
    return RegimeStrategyEvaluation(as_of.isoformat(), regime_record, eligibility_results, selection)


def historical_regime_strategy_report(
    candles: pd.DataFrame,
    timestamps: list[datetime],
    store: MemoryStore,
    registry: tuple[StrategyDefinition, ...] = DEFAULT_REGISTRY,
) -> list[dict[str, Any]]:
    """One row per real, caller-supplied timestamp. Facts only -- no
    P&L, no profitability claim of any kind."""
    return [
        evaluate_regime_and_strategy(candles, timestamp, store, registry).to_dict()
        for timestamp in timestamps
    ]
