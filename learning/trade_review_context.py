"""Phase 2 Piece 1: real historical context for PostTradeAgent.

Assembles, for one just-closed trade, everything real already computed
and persisted elsewhere: the real decision-ledger market-state snapshot
(Phase 1) for the exact cycle that produced this trade, the real
candidate/thesis facts, the real execution/order facts, the real outcome
facts, and the real prior pattern-memory statistics for this setup_type+
regime combination (learning/pattern_memory.py -- already real,
already-tested, not reimplemented here).

No fabricated/missing context: every field is either a real value already
computed by an existing, tested piece of this codebase, or explicitly
None when genuinely unavailable (e.g. a trade whose originating cycle
predates the Phase 1 decision-ledger feature, or ran with decision-ledger
persistence disabled/failed that cycle) -- never a guessed placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from execution.position_supervisor import PositionState
from learning.memory import MemoryStore
from learning.pattern_memory import stats_for
from storage.database import Database


@dataclass(frozen=True)
class TradeReviewContext:
    decision_ledger_entry: dict[str, Any] | None
    candidate: dict[str, Any]
    execution: dict[str, Any]
    outcome: dict[str, Any]
    prior_pattern_stats: dict[str, Any]
    # Phase 2 Piece 5: the real, entry-time score_attribution dict (the 7-
    # component breakdown -- see execution/live_context.py::_add_candidate)
    # this exact trade's candidate was selected under -- the same real
    # dict agents/orchestrator.py::open_position already carries on
    # PositionState.entry_score_attribution for the Obsidian trade
    # journal, surfaced here too so the canonical trade-outcome record
    # (learning/trade_outcome.py) doesn't need a second lookup. None
    # whenever the entry cycle never went through the live-context
    # pipeline (e.g. hand-built test contexts), never fabricated.
    score_attribution: dict[str, Any] | None = None

    def to_facts(self) -> dict[str, Any]:
        """Flat, JSON-safe dict -- what's actually threaded into
        PostTradeAgent's context and, from there, into the real facts
        sent to the AI and stored in memory."""
        return {
            "decision_ledger_entry": self.decision_ledger_entry,
            "candidate": self.candidate,
            "execution": self.execution,
            "outcome": self.outcome,
            "prior_pattern_stats": self.prior_pattern_stats,
            "score_attribution": self.score_attribution,
        }


def build_trade_review_context(
    database: Database,
    memory: MemoryStore,
    state: PositionState,
    order: dict[str, Any],
    pnl: float,
    exit_reason: str | None,
    hold_seconds: float,
    exit_timestamp: datetime,
) -> TradeReviewContext:
    """`exit_timestamp` is the real `now` agents/orchestrator.py::
    _close_position already has at the moment this trade closed -- not a
    fresh wall-clock read here, which would silently diverge from the
    real close time (the same real-timestamp discipline execution/
    live_context.py::_add_candidate already documents for its own `now`
    parameter)."""
    candidate = state.thesis.candidate

    decision_ledger_entry = None
    if state.entry_decision_ledger_candidate_id:
        decision_ledger_entry = database.decision_ledger_entry(state.entry_decision_ledger_candidate_id)

    # Real prior statistics -- BEFORE this trade's own outcome is folded
    # in (record_trade for THIS trade has not run yet at the point
    # _close_position calls this, see agents/orchestrator.py::
    # _close_position's own ordering) -- so this is genuinely the real
    # base rate this trade's prediction should be judged against, not a
    # number this trade's own result has already contaminated.
    stats = stats_for(memory, candidate.setup_type, state.entry_regime or "")

    return TradeReviewContext(
        decision_ledger_entry=decision_ledger_entry,
        candidate={
            "candidate_id": candidate.candidate_id,
            "decision_ledger_candidate_id": state.entry_decision_ledger_candidate_id,
            "direction": candidate.direction,
            "setup_type": candidate.setup_type,
            "confidence": candidate.confidence,
            "evidence": list(candidate.evidence),
            "invalidations": list(candidate.invalidations),
            "entry_zone": list(candidate.entry_zone),
            "stop_zone": list(candidate.stop_zone),
            "target_zone": list(candidate.target_zone),
        },
        execution={
            "order_id": order.get("order_id"),
            "entry_timestamp": state.opened_at.isoformat(),
            "entry_price": state.thesis.entry,
            "exit_timestamp": exit_timestamp.isoformat(),
            "exit_price": order.get("fill_price"),
            "quantity": state.thesis.quantity,
            "estimated_costs": order.get("estimated_costs"),
            "symbol": state.thesis.symbol,
        },
        outcome={
            "pnl": pnl,
            "outcome": "WIN" if pnl > 0 else "LOSS",
            "exit_reason": exit_reason,
            "mae": state.mae,
            "mfe": state.mfe,
            "hold_seconds": hold_seconds,
            "stop_was_trailed": state.current_stop != state.thesis.stop,
            "entry_regime": state.entry_regime,
            "entry_volatility_regime": state.entry_volatility_regime,
            "entry_consensus": state.entry_consensus,
            "agent_agreement": state.entry_agent_directions,
        },
        prior_pattern_stats={
            "setup_type": stats.setup_type,
            "regime": stats.regime,
            "sample_size": stats.sample_size,
            "win_rate": stats.win_rate,
            "expectancy": stats.expectancy,
            "low_confidence": stats.low_confidence,
        },
        score_attribution=state.entry_score_attribution,
    )
