"""Phase 2 Piece 5, Requirement 1-2: the canonical closed-trade outcome
record.

Requirement 1's audit finding, explicit: storage/database.py's `trades`
table + Database.save_trade() are fully built and fully tested (tests/
test_execution.py) but never called from the live orchestrator path --
confirmed dead code by Phase 1's own audit, and re-confirmed here before
writing a line of new persistence code. This module deliberately does
NOT reactivate that table, and does NOT add a second, parallel SQL table
for the same concept. Two real reasons:

1. learning/pattern_memory.py::stats_for -- the one deterministic
   evaluator the whole Phase 2 learning loop already depends on
   (learning.hypothesis.evaluate_hypothesis, and through it
   learning.promotion_pipeline) -- already reads MemoryStore's "trade"
   memory_type records, not the `trades` SQL table. Building a parallel
   canonical record in `trades` would either duplicate every future
   write, or force stats_for's real, already-tested query onto a second
   data source -- exactly the fragmentation Requirement 1 warns against.
2. `trades`'s fixed SQL schema (order_id, symbol, side, quantity,
   entry_price, exit_price, opened_at, closed_at, exit_reason, net_pnl)
   is missing most of what a canonical outcome record needs here
   (candidate_id, regime, score_attribution, MFE/MAE, an AI-hypothesis
   back-reference) -- extending it would mean a real schema migration.
   MemoryStore's existing append-only, JSON-payload "trade" record
   already has exactly the right shape for a record that grows richer
   over time (Phase 2 Piece 1 already enriched it once, with the real
   decision-ledger snapshot).

Decision: TradeOutcomeRecord below is exactly what learning/trade_
memory.py::record_trade() is called with -- the SAME "trade" memory_type
MemoryStore record every existing reader (pattern_memory.stats_for,
Obsidian's learning export) already depends on, now canonically
complete rather than a smaller ad hoc dict. Nothing new is created at
the storage layer; the `trades` SQL table remains untouched, dead, and
explicitly NOT deleted -- removing already-tested public API is outside
this piece's stated scope, and it is inert, not actively harmful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TradeOutcomeRecord:
    outcome_id: str
    candidate_id: str | None  # the real TradeCandidate.candidate_id (agents/contracts.py)
    decision_ledger_candidate_id: str | None  # the real Phase 1 CAND-<date>-<time>-<seq> id
    strategy_version: str
    setup_type: str | None
    direction: str | None
    entry_timestamp: str | None
    entry_price: float | None
    exit_timestamp: str | None
    exit_price: float | None
    realized_pnl: float | None
    outcome: str  # WIN | LOSS
    exit_reason: str | None
    holding_duration_seconds: float | None
    mfe: float | None
    mae: float | None
    entry_regime: str | None
    entry_volatility_regime: str | None
    entry_consensus: str | None
    agent_agreement: dict[str, str] | None
    confidence: float | None
    stop_was_trailed: bool | None
    score_attribution: dict[str, Any] | None
    market_state_snapshot: dict[str, Any] | None  # the real Phase 1 decision-ledger entry, when available
    ai_hypothesis_reference: str | None = field(default=None)  # set after an AI hypothesis, if any, is proposed for this trade
    # Phase 2 Piece 7: real, tick-level reconstructed price/P&L/MFE-MAE
    # evidence (data/option_price_reconstruction.py, learning/option_pnl.py)
    # -- SUPPLEMENTARY only. realized_pnl/mfe/mae above remain the live-
    # authoritative figures from execution/paper_broker.py's own real
    # fill; this field is a separate, clearly-labeled cross-check, never
    # a second source of truth -- see learning/option_pnl.py's own
    # module docstring. None (never fabricated) whenever no real raw
    # capture data was available/attached for this trade.
    option_price_evidence: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "candidate_id": self.candidate_id,
            "decision_ledger_candidate_id": self.decision_ledger_candidate_id,
            "strategy_version": self.strategy_version,
            "setup_type": self.setup_type,
            "direction": self.direction,
            "entry_timestamp": self.entry_timestamp,
            "entry_price": self.entry_price,
            "exit_timestamp": self.exit_timestamp,
            "exit_price": self.exit_price,
            "realized_pnl": self.realized_pnl,
            # "pnl" kept alongside "realized_pnl" -- learning/pattern_memory.py
            # ::stats_for reads entry["payload"]["pnl"] specifically; this
            # record replaces record_trade's old, thinner dict, so the exact
            # key pattern_memory already depends on must still be present.
            "pnl": self.realized_pnl,
            "outcome": self.outcome,
            "exit_reason": self.exit_reason,
            "holding_duration_seconds": self.holding_duration_seconds,
            # "hold_seconds" kept for the same backward-compatibility reason.
            "hold_seconds": self.holding_duration_seconds,
            "mfe": self.mfe,
            "mae": self.mae,
            "entry_regime": self.entry_regime,
            "entry_volatility_regime": self.entry_volatility_regime,
            "entry_consensus": self.entry_consensus,
            "agent_agreement": self.agent_agreement,
            "confidence": self.confidence,
            "stop_was_trailed": self.stop_was_trailed,
            "score_attribution": self.score_attribution,
            "market_state_snapshot": self.market_state_snapshot,
            "ai_hypothesis_reference": self.ai_hypothesis_reference,
            "option_price_evidence": self.option_price_evidence,
        }


def build_trade_outcome_record(
    review_context_facts: dict[str, Any],
    strategy_version: str = "v2",
) -> TradeOutcomeRecord:
    """Pure, deterministic assembly from learning/trade_review_context.py
    ::TradeReviewContext.to_facts() -- every field here is either a real
    value already present in that context, or explicitly None. No I/O,
    no AI call, no decision of any kind; outcome_id is a fresh uuid4 (the
    same real generation MemoryStore.append already uses for memory_id),
    not derived from any input, so this function is only "deterministic"
    in the sense that it never fabricates a field's VALUE -- callers that
    need a reproducible id for testing should compare .to_dict() with
    the id key removed, or read it back off the returned record.
    """
    candidate = review_context_facts.get("candidate") or {}
    execution = review_context_facts.get("execution") or {}
    outcome = review_context_facts.get("outcome") or {}
    score_attribution = review_context_facts.get("score_attribution")
    market_state_snapshot = review_context_facts.get("decision_ledger_entry")

    return TradeOutcomeRecord(
        outcome_id=str(uuid4()),
        candidate_id=candidate.get("candidate_id"),
        decision_ledger_candidate_id=candidate.get("decision_ledger_candidate_id"),
        strategy_version=strategy_version,
        setup_type=candidate.get("setup_type"),
        direction=candidate.get("direction"),
        entry_timestamp=execution.get("entry_timestamp"),
        entry_price=execution.get("entry_price"),
        exit_timestamp=execution.get("exit_timestamp"),
        exit_price=execution.get("exit_price"),
        realized_pnl=outcome.get("pnl"),
        outcome=outcome.get("outcome", "LOSS"),
        exit_reason=outcome.get("exit_reason"),
        holding_duration_seconds=outcome.get("hold_seconds"),
        mfe=outcome.get("mfe"),
        mae=outcome.get("mae"),
        entry_regime=outcome.get("entry_regime"),
        entry_volatility_regime=outcome.get("entry_volatility_regime"),
        entry_consensus=outcome.get("entry_consensus"),
        agent_agreement=outcome.get("agent_agreement"),
        confidence=candidate.get("confidence"),
        stop_was_trailed=outcome.get("stop_was_trailed"),
        score_attribution=score_attribution,
        market_state_snapshot=market_state_snapshot,
    )
