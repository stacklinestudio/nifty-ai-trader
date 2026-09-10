"""Phase 2 Piece 5, Requirement 5: deterministic aggregation of learning
evidence, so a future AI consumer (learning/ai_learning_context.py) reads
structured statistics, never arbitrary raw history.

Reuses the real, already-tested query shape learning/pattern_memory.py::
stats_for already established (group by setup_type+regime over real
recorded trades) -- this module aggregates over the richer "learning_event"
records (Phase 2 Piece 5, Requirement 4) instead, adding real statistics
pattern_memory's narrower win_rate/expectancy pair doesn't carry
(confidence-calibration accuracy, MFE/MAE, the per-trade prediction
success rate). Does not replace pattern_memory.stats_for -- that
function remains the one learning.hypothesis.evaluate_hypothesis (and,
through it, learning.promotion_pipeline) reads; this module is a
separate, additive real view for the AI-facing learning context only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from learning.memory import MemoryStore

_ALL_LEARNING_EVENTS_LIMIT = 100_000
MIN_SAMPLES_FOR_CONFIDENCE = 20  # matches learning/pattern_memory.py's own real bar, reused not re-invented


@dataclass(frozen=True)
class LearningAggregate:
    setup_type: str | None
    regime: str | None
    sample_size: int
    win_rate: float | None
    expectancy: float | None
    success_rate: float | None  # fraction of real trades whose deterministic prediction evaluation_result was True
    avg_confidence_calibration_gap: float | None
    avg_mfe: float | None
    avg_mae: float | None
    low_confidence: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_type": self.setup_type,
            "regime": self.regime,
            "sample_size": self.sample_size,
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "success_rate": self.success_rate,
            "avg_confidence_calibration_gap": self.avg_confidence_calibration_gap,
            "avg_mfe": self.avg_mfe,
            "avg_mae": self.avg_mae,
            "low_confidence": self.low_confidence,
        }


def _real_events(memory: MemoryStore) -> list[dict[str, Any]]:
    return [e["payload"] for e in memory.recent(memory_type="learning_event", limit=_ALL_LEARNING_EVENTS_LIMIT)]


def _average(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def aggregate_learning_evidence(
    memory: MemoryStore, setup_type: str | None = None, regime: str | None = None
) -> LearningAggregate:
    """Real, deterministic aggregation over real "learning_event" records
    matching (setup_type, regime) -- both None means "all real events."
    sample_size=0 (never a fabricated non-zero count) when no real
    events match yet, matching pattern_memory.stats_for's own honest
    empty-result shape."""
    events = _real_events(memory)
    matching = [
        e
        for e in events
        if (setup_type is None or e["outcome_record"].get("setup_type") == setup_type)
        and (regime is None or e["outcome_record"].get("entry_regime") == regime)
    ]
    sample_size = len(matching)
    if sample_size == 0:
        return LearningAggregate(setup_type, regime, 0, None, None, None, None, None, None, True)

    pnls = [e["outcome_record"]["realized_pnl"] for e in matching if e["outcome_record"].get("realized_pnl") is not None]
    wins = sum(1 for e in matching if e["outcome_record"].get("outcome") == "WIN")
    successes = sum(1 for e in matching if e["prediction_evaluation"].get("evaluation_result") is True)
    calibration_gaps = [
        e["prediction_evaluation"]["confidence_calibration_gap"]
        for e in matching
        if e["prediction_evaluation"].get("confidence_calibration_gap") is not None
    ]
    mfes = [e["outcome_record"]["mfe"] for e in matching if e["outcome_record"].get("mfe") is not None]
    maes = [e["outcome_record"]["mae"] for e in matching if e["outcome_record"].get("mae") is not None]

    return LearningAggregate(
        setup_type=setup_type,
        regime=regime,
        sample_size=sample_size,
        win_rate=wins / sample_size,
        expectancy=_average(pnls),
        success_rate=successes / sample_size,
        avg_confidence_calibration_gap=_average(calibration_gaps),
        avg_mfe=_average(mfes),
        avg_mae=_average(maes),
        low_confidence=sample_size < MIN_SAMPLES_FOR_CONFIDENCE,
    )


def aggregate_all_learning_evidence(memory: MemoryStore) -> list[LearningAggregate]:
    """Real per-(setup_type, regime) breakdown across every real
    "learning_event" recorded so far -- the structured statistics view
    Requirement 5 exists to provide. Empty list (never fabricated rows)
    when no real learning events exist yet."""
    events = _real_events(memory)
    pairs = sorted(
        {(e["outcome_record"].get("setup_type"), e["outcome_record"].get("entry_regime")) for e in events}
    )
    return [aggregate_learning_evidence(memory, setup_type, regime) for setup_type, regime in pairs]
