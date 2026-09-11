"""Phase 2 Piece 9: the missing automatic connective wiring from a real
LearningEvent all the way to a real promotion_engine.decide() call.

Audit finding this closes (see the Piece 9 completion report for the
full four-question audit): three of the four stages already wire
together automatically, and always have --

1. Every real trade close (agents/orchestrator.py::_close_position)
   already automatically calls Orchestrator.review_trade ->
   agents/trading_agents.py::PostTradeAgent.analyze, which already
   automatically proposes an AI hypothesis (Piece 2) whenever
   `trade_review_context` is available -- not gated behind any manual
   invocation.
2. That AI-proposed hypothesis, once it parses (learning/hypothesis.py
   ::parse_hypothesis_condition -- strict, never a partially-fabricated
   condition), already automatically becomes a real, schema-complete
   `Experiment` row (learning/experiment_manager.py::create_experiment,
   tagged parameters["source"]=="ai_proposed") -- not advisory text.

What does NOT exist anywhere before this piece: anything that
automatically carries that real Experiment through backtest/
daily_walk_forward.py and into learning.promotion_pipeline.
evaluate_experiment_for_promotion (Piece 4's own, already-correct
bridge to promotion_engine.decide() -- confirmed by repo-wide grep to
have exactly one real call site before this piece: its own test file).
`daily-backtest` is a manual CLI subcommand requiring a human to supply
`--data` and re-type a condition by hand; nothing connects a newly
created Experiment to it automatically.

This module is glue only. It reuses, completely unmodified:
- learning/hypothesis.py::evaluate_hypothesis (Piece 2)
- learning/promotion_pipeline.py::evaluate_experiment_for_promotion /
  gather_structural_backtest_evidence (Piece 4 -- including its own
  already-proven isolated-scratch-database backtest replay)
- learning/promotion_engine.py::decide (never touched, never will be)

ABSOLUTE SAFETY LINE (see this piece's own brief): `human_approved` is
ALWAYS False in every call this module makes to
evaluate_experiment_for_promotion. There is no parameter, flag, config
value, or code path anywhere in this module that can make it True. An
unattended automatic run has no human in the loop to approve anything;
the resulting PromotionDecision will therefore always report
promote=False with "human approval required" among its reasons unless
and until a human separately, explicitly re-evaluates the SAME real
condition with human_approved=True through whatever process already
exists for that (unchanged, untouched, out of this piece's scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from config import Settings
from learning.hypothesis import HypothesisCondition, evaluate_hypothesis
from learning.memory import MemoryStore
from learning.promotion_pipeline import evaluate_experiment_for_promotion

EXPERIMENT_MEMORY_TYPE = "experiment"
PROMOTION_EVALUATION_MEMORY_TYPE = "promotion_evaluation"
PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE = "promotion_evaluation_skipped"
AI_PROPOSED_SOURCE = "ai_proposed"

# Real, explicit, documented bound (Requirement 3): a full evaluation
# runs a real historical backtest AND a real walk-forward replay
# (backtest/daily_backtest.py, backtest/daily_walk_forward.py) --
# Piece 4's own tests already show a single real historical run costs
# real, non-trivial wall-clock time. An unbounded backlog of pending
# AI-proposed experiments must never turn one automatic-pipeline
# invocation into an unbounded number of full historical replays. 5
# keeps one real run's cost to "a handful of full historical windows"
# -- small enough to run unattended on a real schedule, large enough to
# make real, non-glacial progress against a real backlog. Idempotency
# (see _pending_ai_proposed_experiments below) means a backlog larger
# than this is simply finished across several real runs, never lost or
# silently dropped.
MAX_EXPERIMENTS_EVALUATED_PER_AUTO_RUN = 5


def _condition_from_experiment(payload: dict[str, Any]) -> HypothesisCondition | None:
    """Reconstructs the real HypothesisCondition this experiment was
    created from (learning/hypothesis.py::HypothesisCondition.to_dict(),
    stored verbatim as parameters["hypothesis_condition"] by
    agents/trading_agents.py::PostTradeAgent). Returns None (never a
    fabricated/guessed condition) for a malformed or legacy record that
    predates this shape -- such a record is simply never carried
    forward by this pipeline, not treated as an error."""
    raw = (payload.get("parameters") or {}).get("hypothesis_condition")
    if not isinstance(raw, dict):
        return None
    try:
        return HypothesisCondition(
            metric=str(raw["metric"]),
            setup_type=str(raw["setup_type"]),
            regime=str(raw["regime"]),
            operator=str(raw["operator"]),
            threshold=float(raw["threshold"]),
            min_samples=int(raw["min_samples"]),
            rationale=str(raw.get("rationale", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _pending_ai_proposed_experiments(memory: MemoryStore) -> list[dict[str, Any]]:
    """Every real, already-created AI-proposed Experiment record that
    this pipeline has not yet carried through to a real promotion_
    evaluation OR an explicit promotion_evaluation_skipped record --
    the real idempotency mechanism (Requirement 4's resume-awareness):
    both outcomes are recorded by `source_experiment_memory_id`, a
    genuine reference to the real experiment record's own MemoryStore
    id, re-derived fresh from durable storage on every call rather than
    tracked in memory across a process restart. Returned oldest-first,
    so a real backlog is worked down in creation order."""
    processed_ids = {
        entry["payload"].get("source_experiment_memory_id")
        for entry in memory.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=100_000)
    } | {
        entry["payload"].get("source_experiment_memory_id")
        for entry in memory.recent(memory_type=PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE, limit=100_000)
    }
    experiments = memory.recent(memory_type=EXPERIMENT_MEMORY_TYPE, limit=100_000)
    pending = [
        entry
        for entry in experiments
        if (entry["payload"].get("parameters") or {}).get("source") == AI_PROPOSED_SOURCE
        and entry["memory_id"] not in processed_ids
    ]
    pending.sort(key=lambda entry: entry["timestamp"])
    return pending


@dataclass(frozen=True)
class AutoPromotionRunSummary:
    total_pending_before_run: int
    evaluated_experiment_ids: tuple[str, ...]
    skipped_insufficient_evidence_ids: tuple[str, ...]
    malformed_skipped_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_pending_before_run": self.total_pending_before_run,
            "evaluated_experiment_ids": list(self.evaluated_experiment_ids),
            "skipped_insufficient_evidence_ids": list(self.skipped_insufficient_evidence_ids),
            "malformed_skipped_ids": list(self.malformed_skipped_ids),
        }


def run_automatic_promotion_cycle(
    settings: Settings,
    all_candles: pd.DataFrame,
    memory: MemoryStore,
    now: datetime,
    max_experiments: int = MAX_EXPERIMENTS_EVALUATED_PER_AUTO_RUN,
) -> AutoPromotionRunSummary:
    """The one real automatic bridge this piece adds. For each pending,
    not-yet-processed AI-proposed Experiment (bounded by
    `max_experiments`, oldest first), this:

    1. Cheaply re-checks real accumulated live-trade evidence
       (learning/hypothesis.py::evaluate_hypothesis -- the same real,
       already-tested aggregation, no backtest involved) against the
       hypothesis's own min_samples. Insufficient real evidence means
       the final decision can never promote regardless of backtest
       structure (evaluate_experiment_for_promotion requires
       outcome.passed is True) -- so the real, expensive backtest/
       walk-forward replay is skipped for this experiment (Requirement
       3's second bound), and an honest, explicit
       promotion_evaluation_skipped record is written instead of
       silently doing nothing.
    2. Otherwise, calls learning.promotion_pipeline.
       evaluate_experiment_for_promotion -- completely unmodified,
       human_approved=False always (see this module's own docstring) --
       and records the real, complete result.

    Every write here is a fresh, immutable MemoryStore.append -- no
    write overwrites or depends on this function's own prior in-memory
    state, so a process killed mid-run and re-invoked later with the
    same real memory/settings simply continues from real, durable
    state (see _pending_ai_proposed_experiments), never duplicating a
    promotion evaluation for an experiment already processed.
    """
    pending = _pending_ai_proposed_experiments(memory)
    to_process = pending[:max_experiments]

    evaluated: list[str] = []
    skipped_insufficient: list[str] = []
    skipped_malformed: list[str] = []

    for entry in to_process:
        memory_id = entry["memory_id"]
        condition = _condition_from_experiment(entry["payload"])
        if condition is None:
            skipped_malformed.append(memory_id)
            continue

        cheap_outcome = evaluate_hypothesis(condition, memory, now)
        if cheap_outcome.sample_size < condition.min_samples:
            memory.append(
                PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE,
                {
                    "source_experiment_memory_id": memory_id,
                    "condition": condition.to_dict(),
                    "sample_size": cheap_outcome.sample_size,
                    "reason": (
                        f"real accumulated live-trade sample size {cheap_outcome.sample_size} is "
                        f"below this hypothesis's own min_samples={condition.min_samples} -- "
                        "promotion cannot succeed yet regardless of backtest structure, so the "
                        "real, expensive backtest/walk-forward replay was not run."
                    ),
                },
                now,
            )
            skipped_insufficient.append(memory_id)
            continue

        result = evaluate_experiment_for_promotion(
            settings, all_candles, memory, condition, False, now
        )
        payload = result.to_dict()
        payload["source_experiment_memory_id"] = memory_id
        memory.append(PROMOTION_EVALUATION_MEMORY_TYPE, payload, now)
        evaluated.append(memory_id)

    return AutoPromotionRunSummary(
        total_pending_before_run=len(pending),
        evaluated_experiment_ids=tuple(evaluated),
        skipped_insufficient_evidence_ids=tuple(skipped_insufficient),
        malformed_skipped_ids=tuple(skipped_malformed),
    )
