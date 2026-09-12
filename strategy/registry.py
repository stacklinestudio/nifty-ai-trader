"""Phase 2 Piece 10, Requirements 5, 6, 10: the canonical Strategy
Registry.

Audit finding (Piece 10 completion report has the full detail): no
`Strategy` class, `StrategyRegistry`, `strategy_id` field, or lifecycle-
status enum (DRAFT/VALIDATED/PROMOTED/RETIRED/REJECTED) existed
anywhere in this repo before this piece. The only real, already-
implemented "strategies" are the 6 setup_type detectors in
`execution/live_context.py::_select_setup` (OPENING_RANGE_BREAKOUT,
TREND_CONTINUATION, MOMENTUM_CONTINUATION, VWAP_BREAKOUT,
VWAP_REJECTION, SUPPORT_RESISTANCE_REACTION) -- `DEFAULT_REGISTRY`
below catalogs exactly those 6, using their own real setup_type strings
as `strategy_id` (never a new, parallel identifier), with
`supported_regimes` set to the REAL regime gating `_select_setup`
already, independently, enforces (trend/gap setups only tried when
`trend_direction is not None`; VWAP_REJECTION/SUPPORT_RESISTANCE_
REACTION only tried under RANGE/UNCERTAIN) -- read directly off that
function, not guessed.

Status is NEVER stored as an independent, hand-set field (Requirement
6: "Do NOT create a second promotion mechanism"). `derive_strategy_status`
is the ONLY place a status is decided, and it is a pure, deterministic
READ of Piece 9's own, already-existing, unmodified evidence
(`learning/auto_promotion_pipeline.py`'s "promotion_evaluation" /
"promotion_evaluation_skipped" MemoryStore records) for the exact
(strategy_id, regime) pair being asked about, as of a given timestamp
(no look-ahead: only evidence timestamped at or before `as_of` is ever
considered). `learning.promotion_engine.decide()` remains the sole
authority over whether a candidate promotes; this module only reads
the real, already-decided outcome back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from learning.memory import MemoryStore
from learning.promotion_evidence_types import PROMOTION_EVALUATION_MEMORY_TYPE

STATUS_DRAFT = "DRAFT"
STATUS_VALIDATED = "VALIDATED"
STATUS_PROMOTED = "PROMOTED"
STATUS_RETIRED = "RETIRED"
STATUS_REJECTED = "REJECTED"

# Real, explicit, small bound (same reasoning as learning/auto_promotion_
# pipeline.py's own MAX_EXPERIMENTS_EVALUATED_PER_AUTO_RUN) -- this
# project's real data volumes are small; a full type-scoped scan is the
# honest, simplest correct implementation (matches evidence/
# reconstruction.py's own established _SCAN_LIMIT convention).
_SCAN_LIMIT = 100_000

# Real regime-gating, read directly off execution/live_context.py::
# _select_setup -- trend/gap setups are tried only when a real
# trend_direction was determined (Regime.TREND_UP/TREND_DOWN/GAP_UP/
# GAP_DOWN), range setups only under Regime.RANGE/Regime.UNCERTAIN.
# Regime.RANGE is included for completeness even though
# execution/regime_detection.py's own audit found classify() can never
# actually produce it today -- a strategy's SUPPORTED regimes describe
# what the underlying detector is gated to accept, not what the live
# classifier happens to be capable of emitting.
_TREND_REGIMES = ("TREND_UP", "TREND_DOWN", "GAP_UP", "GAP_DOWN")
_RANGE_REGIMES = ("RANGE", "UNCERTAIN")


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str  # the real setup_type string this strategy detects
    version: str
    name: str
    description: str
    supported_regimes: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "supported_regimes": list(self.supported_regimes),
            "created_at": self.created_at,
        }


# The real registry epoch -- when this catalog of 6 real, already-live
# setup detectors was first defined. Not a live timestamp read at
# import time (which would make every RegistryDefinition's `created_at`
# non-reproducible between runs).
_REGISTRY_EPOCH = "2026-09-12T00:00:00+05:30"

DEFAULT_REGISTRY: tuple[StrategyDefinition, ...] = (
    StrategyDefinition(
        "OPENING_RANGE_BREAKOUT", "v1", "Opening Range Breakout",
        "Trades a break of the real opening range in the real trend direction "
        "(strategy/orb.py, dispatched from execution/live_context.py::_select_setup).",
        _TREND_REGIMES, _REGISTRY_EPOCH,
    ),
    StrategyDefinition(
        "TREND_CONTINUATION", "v1", "Trend Continuation",
        "execution/live_context.py::_trend_continuation_setup -- continuation of an already-established real trend.",
        _TREND_REGIMES, _REGISTRY_EPOCH,
    ),
    StrategyDefinition(
        "MOMENTUM_CONTINUATION", "v1", "Momentum Continuation",
        "execution/live_context.py::_momentum_continuation_setup -- real momentum-feature-driven continuation.",
        _TREND_REGIMES, _REGISTRY_EPOCH,
    ),
    StrategyDefinition(
        "VWAP_BREAKOUT", "v1", "VWAP Breakout",
        "execution/live_context.py::_vwap_breakout_setup -- a real break through session VWAP in the trend direction.",
        _TREND_REGIMES, _REGISTRY_EPOCH,
    ),
    StrategyDefinition(
        "VWAP_REJECTION", "v1", "VWAP Rejection",
        "execution/live_context.py::_vwap_rejection_setup -- a real rejection off session VWAP in a non-trending regime.",
        _RANGE_REGIMES, _REGISTRY_EPOCH,
    ),
    StrategyDefinition(
        "SUPPORT_RESISTANCE_REACTION", "v1", "Support/Resistance Reaction",
        "execution/live_context.py::_support_resistance_reaction_setup -- a real reaction off a prior real level in a non-trending regime.",
        _RANGE_REGIMES, _REGISTRY_EPOCH,
    ),
)


def latest_promotion_evaluation(
    store: MemoryStore, strategy_id: str, regime: str, as_of: datetime
) -> dict[str, Any] | None:
    """The most recent real "promotion_evaluation" payload (learning/
    auto_promotion_pipeline.py) for this exact (strategy_id, regime)
    pair, timestamped at or before `as_of` -- shared by
    `derive_strategy_status` below and `strategy/selection.py`'s own
    deterministic tie-breaking, so both read the identical real
    evidence rather than two independent, potentially-diverging scans."""
    as_of_iso = as_of.isoformat()
    matching = [
        entry
        for entry in store.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=_SCAN_LIMIT)
        if entry["timestamp"] <= as_of_iso
        and entry["payload"]["condition"]["setup_type"] == strategy_id
        and entry["payload"]["condition"]["regime"] == regime
    ]
    if not matching:
        return None
    return max(matching, key=lambda entry: entry["timestamp"])["payload"]


def derive_strategy_status(store: MemoryStore, strategy_id: str, regime: str, as_of: datetime) -> str:
    """DRAFT: no real promotion_evaluation evidence exists yet for this
    exact (strategy_id, regime) pair (only, at most, a real
    promotion_evaluation_skipped record, or nothing at all).
    VALIDATED: the most recent real promotion_evaluation's decision has
    every deterministic gate satisfied (historical/walk-forward/OOS) and
    is withheld ONLY on human approval -- promotion_engine.decide()'s
    own real reasons list is read directly, never re-derived.
    PROMOTED: the most recent real promotion_evaluation's decision is
    promote=True (requires a genuine, already-recorded human_approved=
    True re-evaluation through whatever process already exists for
    that -- this function never produces one itself).
    REJECTED: the most recent real promotion_evaluation genuinely failed
    a deterministic gate (historical, walk-forward, or out-of-sample),
    not merely awaiting human sign-off.
    RETIRED is never derived automatically -- there is no real
    mechanism anywhere in this codebase that retires a strategy; it
    exists as a status value for a future, explicitly human-driven
    action, never produced by this function.
    """
    payload = latest_promotion_evaluation(store, strategy_id, regime, as_of)
    if payload is None:
        return STATUS_DRAFT
    decision = payload["decision"]
    if decision["promote"] is True:
        return STATUS_PROMOTED
    if set(decision["reasons"]) == {"human approval"}:
        return STATUS_VALIDATED
    return STATUS_REJECTED
