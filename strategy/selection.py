"""Phase 2 Piece 10, Requirement 8: deterministic strategy selection.

Selects among strategies `strategy/eligibility.py` already found
eligible (status PROMOTED for the current regime) -- never re-derives
eligibility itself. Explicit, measurable tie-break criteria only:

1. Most real historical_candidates on the strategy's own latest
   `promotion_evaluation` record (learning/auto_promotion_pipeline.py)
   -- more real backtest evidence behind the SAME already-promoted
   strategy is preferred over less, a genuine, already-computed,
   real number, never an opinion.
2. Alphabetical `strategy_id`, as a final, total-ordering tiebreak so
   selection is always fully deterministic even between two equally-
   evidenced strategies.

No AI preference, no opaque ranking, no future performance, no future
trade outcome -- confirmed by this module never importing `ai.router`
or reading anything timestamped after `as_of` (the same `as_of` already
threaded through the eligibility evidence it reads).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from learning.memory import MemoryStore
from strategy.eligibility import EligibilityResult
from strategy.registry import latest_promotion_evaluation

NO_STRATEGY_SELECTED = "NO STRATEGY SELECTED"


@dataclass(frozen=True)
class SelectionResult:
    regime: str
    selected_strategy_id: str | None
    selected_strategy_version: str | None
    reason: str
    evaluated_count: int
    eligible_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "selected_strategy_id": self.selected_strategy_id,
            "selected_strategy_version": self.selected_strategy_version,
            "reason": self.reason,
            "evaluated_count": self.evaluated_count,
            "eligible_count": self.eligible_count,
        }


def _historical_candidates(store: MemoryStore, result: EligibilityResult, as_of: datetime) -> int:
    payload = latest_promotion_evaluation(store, result.strategy_id, result.regime, as_of)
    if payload is None:
        return 0
    return int(payload["structural_evidence"]["historical_candidates"])


def select_strategy(
    store: MemoryStore, regime: str, eligibility_results: tuple[EligibilityResult, ...], as_of: datetime
) -> SelectionResult:
    eligible = [r for r in eligibility_results if r.eligible]
    if not eligible:
        return SelectionResult(
            regime, None, None, f"{NO_STRATEGY_SELECTED} -- no eligible (PROMOTED) strategy for regime {regime}.",
            len(eligibility_results), 0,
        )
    ranked = sorted(
        eligible,
        key=lambda r: (-_historical_candidates(store, r, as_of), r.strategy_id),
    )
    winner = ranked[0]
    return SelectionResult(
        regime, winner.strategy_id, winner.version,
        f"selected {winner.strategy_id} (most real historical_candidates among {len(eligible)} eligible "
        "strategies for this regime, strategy_id alphabetical as final tiebreak).",
        len(eligibility_results), len(eligible),
    )
