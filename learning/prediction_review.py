"""Phase 2 Piece 3: Prediction vs Outcome.

compute_prediction_error is a pure, deterministic function -- the real
"error" between what the deterministic pipeline's own signal predicted
(SignalHunterAgent's candidate.direction/confidence, the real numbers
that actually drove this trade -- not an AI guess) and what really
happened (the real outcome/pnl already computed by the time a trade
closes). This is computed in code, never by the AI, and handed to the AI
only as an already-true fact for it to write a "lesson" narrative about
-- the AI never gets to invent its own version of what was predicted or
what happened.

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

    return {
        "candidate_id": candidate.get("candidate_id"),
        "setup_type": candidate["setup_type"],
        "regime": outcome.get("entry_regime"),
        "predicted_direction": predicted_direction,
        "predicted_confidence": predicted_confidence,
        "realized_outcome": realized_outcome,
        "pnl": outcome["pnl"],
        "confidence_calibration_gap": confidence_calibration_gap,
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
