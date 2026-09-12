"""Phase 2 Piece 10, Requirement 7: deterministic strategy eligibility.

strategy + version + current regime -> eligible / ineligible.

Eligible means the FULL real promotion bar has been cleared -- status
PROMOTED (`strategy/registry.py::derive_strategy_status`), which itself
requires a real, human-approved `promote=True` decision from
`learning.promotion_engine.decide()` (Piece 4/9, unmodified). A
DRAFT/VALIDATED/REJECTED/RETIRED strategy is always ineligible here,
regardless of how strong its structural or outcome evidence looks --
VALIDATED specifically means "every deterministic gate passed, human
approval still pending," which is exactly the state Piece 9's own
absolute safety line exists to keep un-promotable until a human acts.
There is no code path in this module, or anywhere it is called from,
that can flip that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from learning.memory import MemoryStore
from strategy.registry import STATUS_PROMOTED, StrategyDefinition, derive_strategy_status


@dataclass(frozen=True)
class EligibilityResult:
    strategy_id: str
    version: str
    regime: str
    eligible: bool
    status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "regime": self.regime,
            "eligible": self.eligible,
            "status": self.status,
            "reason": self.reason,
        }


def evaluate_strategy_eligibility(
    store: MemoryStore, strategy: StrategyDefinition, regime: str, as_of: datetime
) -> EligibilityResult:
    if regime not in strategy.supported_regimes:
        return EligibilityResult(
            strategy.strategy_id, strategy.version, regime, False, "N/A",
            f"regime {regime} is not among this strategy's supported regimes {strategy.supported_regimes}.",
        )
    status = derive_strategy_status(store, strategy.strategy_id, regime, as_of)
    if status == STATUS_PROMOTED:
        return EligibilityResult(
            strategy.strategy_id, strategy.version, regime, True, status,
            "real, human-approved promotion evidence exists for this exact strategy+regime.",
        )
    return EligibilityResult(
        strategy.strategy_id, strategy.version, regime, False, status,
        f"strategy status is {status}, not PROMOTED -- ineligible for automatic selection.",
    )


def evaluate_registry_eligibility(
    store: MemoryStore, registry: tuple[StrategyDefinition, ...], regime: str, as_of: datetime
) -> tuple[EligibilityResult, ...]:
    return tuple(evaluate_strategy_eligibility(store, strategy, regime, as_of) for strategy in registry)
