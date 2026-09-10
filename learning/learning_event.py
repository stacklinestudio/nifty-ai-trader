"""Phase 2 Piece 5, Requirement 4: the canonical learning-event record.

One real, single record per closed trade tying together the canonical
outcome record (learning/trade_outcome.py) and its deterministic
prediction-vs-outcome evaluation (learning/prediction_review.py) --
generated unconditionally for both winning and losing trades (no branch
anywhere in this module treats a loss's facts as less complete than a
win's; the only place outcome sign matters at all is the WIN/LOSS label
already computed upstream).

Persisted under its own, new, additive memory_type ("learning_event") --
this is a deliberate synthesis of the "trade" and "prediction_review"
records (both of which keep being written exactly as before, unchanged,
since pattern_memory.stats_for and the AI-lesson pipeline already depend
on them), not a replacement for either and not a duplicate of either on
its own: it is the one place a future reader (human or the bounded AI
learning-context interface, learning/ai_learning_context.py) can read
one record and get the complete real picture of what happened and what
the deterministic evaluation said about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from learning.memory import MemoryStore
from learning.trade_outcome import TradeOutcomeRecord


@dataclass(frozen=True)
class LearningEvent:
    learning_event_id: str
    outcome_id: str
    created_at: str
    outcome_record: dict[str, Any]
    prediction_evaluation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "learning_event_id": self.learning_event_id,
            "outcome_id": self.outcome_id,
            "created_at": self.created_at,
            "outcome_record": self.outcome_record,
            "prediction_evaluation": self.prediction_evaluation,
        }


def build_learning_event(
    outcome_record: TradeOutcomeRecord, prediction_evaluation: dict[str, Any], now: datetime
) -> LearningEvent:
    return LearningEvent(
        learning_event_id=str(uuid4()),
        outcome_id=outcome_record.outcome_id,
        created_at=now.isoformat(),
        outcome_record=outcome_record.to_dict(),
        prediction_evaluation=prediction_evaluation,
    )


def record_learning_event(store: MemoryStore, event: LearningEvent, timestamp: datetime) -> str:
    return store.append("learning_event", event.to_dict(), timestamp)
