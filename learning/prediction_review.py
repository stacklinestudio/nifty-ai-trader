"""Phase 2 Piece 3 (extended in Piece 5): Prediction vs Outcome.

compute_prediction_error is a pure, deterministic function -- the real
"error" between what the deterministic pipeline's own signal predicted
(SignalHunterAgent's candidate.direction/confidence, the real numbers
that actually drove this trade -- not an AI guess) and what really
happened (the real outcome/pnl already computed by the time a trade
closes). This is computed in code, never by the AI, and handed to the AI
only as an already-true fact for it to write a "lesson" narrative about
-- the AI never gets to invent its own version of what was predicted or
what happened.

Phase 2 Piece 5, Requirement 3: the returned dict must explicitly carry
a measurable success/failure condition and its evaluation result, not
just the raw calibration-gap number -- both computed here, deterministically,
never by the AI. The real, per-trade condition is the simplest one the
deterministic pipeline's own signal actually implies: this candidate was
taken because the deterministic pipeline expected it to be profitable,
so "the real outcome is a WIN" is the real, literal, already-defined
success condition for that expectation -- not a new invented rule. This
is a real, DIFFERENT evaluation than learning/hypothesis.py's (which
grades an aggregate setup+regime hypothesis across many trades, keyed on
a real accumulated win_rate/expectancy sample) -- this one grades a
single trade's own prediction the moment it closes. Both are
deterministic and neither lets the AI grade itself; they answer
different real questions and do not duplicate each other.

record_prediction_review persists both the real error facts and the
AI's real lesson text to MemoryStore under a new, additive memory_type
("prediction_review") -- it never touches the existing "trade"/
"experiment" records learning/trade_memory.py and
learning/experiment_manager.py already write.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from learning.memory import MemoryStore


def compute_prediction_error(review_context_facts: dict[str, Any]) -> dict[str, Any]:
    """`review_context_facts` is TradeReviewContext.to_facts() (see
    learning/trade_review_context.py) -- every value read here is a real
    number/string already computed elsewhere, never invented. Losing and
    winning trades are computed identically -- no branch here treats a
    loss's facts as less complete than a win's."""
    candidate = review_context_facts["candidate"]
    outcome = review_context_facts["outcome"]
    prior = review_context_facts["prior_pattern_stats"]

    predicted_direction = candidate["direction"]
    predicted_confidence = candidate["confidence"]
    realized_outcome = outcome["outcome"]
    realized_value = 1.0 if realized_outcome == "WIN" else 0.0
    # A real, comparable calibration gap: predicted_confidence (0-100, "how
    # sure the deterministic signal was") normalized to 0-1 against the
    # real binary realized outcome. 0.0 = perfectly calibrated this trade
    # (high confidence and it won, or low confidence and it lost); larger
    # values mean the real confidence and the real outcome diverged more.
    confidence_calibration_gap = abs((predicted_confidence / 100.0) - realized_value)

    # Requirement 3's explicit measurable success/failure condition and
    # evaluation result -- deterministic, stated as a real string (not
    # just implied by other fields), so a reader (human or AI) sees
    # exactly what rule was applied, not just the verdict.
    success_condition = "realized_outcome == WIN"
    evaluation_result = realized_outcome == "WIN"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "setup_type": candidate["setup_type"],
        "regime": outcome.get("entry_regime"),
        "prediction": {"direction": predicted_direction, "confidence": predicted_confidence},
        "predicted_direction": predicted_direction,
        "predicted_confidence": predicted_confidence,
        "actual_outcome": {"outcome": realized_outcome, "pnl": outcome["pnl"]},
        "realized_outcome": realized_outcome,
        "pnl": outcome["pnl"],
        "prediction_error": confidence_calibration_gap,
        "confidence_calibration_gap": confidence_calibration_gap,
        "success_condition": success_condition,
        "evaluation_result": evaluation_result,
        "prior_win_rate_for_setup_regime": prior["win_rate"],
        "prior_sample_size_for_setup_regime": prior["sample_size"],
        "prior_low_confidence": prior["low_confidence"],
    }


def record_prediction_review(
    store: MemoryStore, prediction_error: dict[str, Any], ai_lesson: str | None, timestamp: datetime
) -> str:
    return store.append(
        "prediction_review", {**prediction_error, "ai_lesson": ai_lesson}, timestamp
    )
