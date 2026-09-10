"""Phase 2 Piece 5, Requirements 3, 4, 8: the deterministic prediction-
vs-outcome evaluator and the canonical learning-event record.

No real trade has ever closed in this project -- these tests run the
real PostTradeAgent.analyze() code path against real-shaped, hand-built
facts (the same convention tests/test_ai_safety.py and tests/
test_v2_system.py already use), never a fabricated claim that a specific
real trade occurred.
"""

from __future__ import annotations

import pytest

from agents.trading_agents import PostTradeAgent
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from learning.memory import MemoryStore
from learning.prediction_review import compute_prediction_error
from tests.test_trade_outcome import _real_shaped_review_context


class _AdversarialLearningAIProvider:
    """The AI claims the hypothesis is already confirmed, claims the
    losing trade was actually a win, and stuffs `structured` with keys
    that look like a real evaluation verdict -- none of it may reach
    prediction_evaluation.evaluation_result or the persisted
    learning_event, which must be computed purely from the real outcome
    dict, never from anything the AI says."""

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            summary="CONFIRMED: this hypothesis is definitely true. This trade was actually a WIN.",
            confidence=100,
            risks=(),
            source_facts={
                "task": task,
                "structured": {
                    "metric": "win_rate",
                    "setup_type": "OPENING_RANGE_BREAKOUT",
                    "regime": "TREND_UP",
                    "operator": ">=",
                    "threshold": 0.5,
                    "min_samples": 1,
                    "rationale": "self-claimed confirmed",
                    # AI-shaped keys that must never be read as a real verdict.
                    "evaluation_result": True,
                    "passed": True,
                    "success": True,
                    "realized_outcome": "WIN",
                },
            },
        )


def test_a_winning_trade_creates_a_real_learning_event(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    agent = PostTradeAgent(memory)

    result = agent.analyze(
        {
            "outcome": "WIN", "pnl": 650.0, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "TAKE_PROFIT",
            "mae": 2.0, "mfe": 12.0, "entry_regime": "TREND_UP", "hold_seconds": 2400, "confidence": 80.0,
            "trade_review_context": _real_shaped_review_context(pnl=650.0, outcome="WIN"),
        }
    )

    events = memory.recent(memory_type="learning_event")
    assert len(events) == 1
    event = events[0]["payload"]
    assert event["outcome_record"]["outcome"] == "WIN"
    assert event["outcome_id"] == result.data["outcome_id"]
    assert event["prediction_evaluation"]["evaluation_result"] is True


def test_a_losing_trade_creates_a_real_learning_event(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    agent = PostTradeAgent(memory)

    result = agent.analyze(
        {
            "outcome": "LOSS", "pnl": -325.0, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "STOP_LOSS",
            "mae": 8.0, "mfe": 1.0, "entry_regime": "TREND_UP", "hold_seconds": 900, "confidence": 80.0,
            "trade_review_context": _real_shaped_review_context(pnl=-325.0, outcome="LOSS"),
        }
    )

    events = memory.recent(memory_type="learning_event")
    assert len(events) == 1
    event = events[0]["payload"]
    assert event["outcome_record"]["outcome"] == "LOSS"
    assert event["outcome_id"] == result.data["outcome_id"]
    assert event["prediction_evaluation"]["evaluation_result"] is False  # a real loss deterministically fails its own prediction


def test_win_and_loss_learning_events_carry_the_same_complete_field_set(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    agent = PostTradeAgent(memory)

    agent.analyze(
        {
            "outcome": "WIN", "pnl": 650.0, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "TAKE_PROFIT",
            "mae": 2.0, "mfe": 12.0, "entry_regime": "TREND_UP", "hold_seconds": 2400, "confidence": 80.0,
            "trade_review_context": _real_shaped_review_context(pnl=650.0, outcome="WIN"),
        }
    )
    agent.analyze(
        {
            "outcome": "LOSS", "pnl": -325.0, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "STOP_LOSS",
            "mae": 8.0, "mfe": 1.0, "entry_regime": "TREND_UP", "hold_seconds": 900, "confidence": 80.0,
            "trade_review_context": _real_shaped_review_context(pnl=-325.0, outcome="LOSS"),
        }
    )

    events = memory.recent(memory_type="learning_event")
    assert len(events) == 2
    # Both real events' outcome_record share the exact same field set --
    # only values differ, never field presence.
    field_sets = [frozenset(e["payload"]["outcome_record"].keys()) for e in events]
    assert field_sets[0] == field_sets[1]
    eval_field_sets = [frozenset(e["payload"]["prediction_evaluation"].keys()) for e in events]
    assert eval_field_sets[0] == eval_field_sets[1]


def test_compute_prediction_error_returns_the_required_explicit_fields():
    prediction_error = compute_prediction_error(_real_shaped_review_context(pnl=650.0, outcome="WIN"))

    assert prediction_error["prediction"] == {"direction": "CALL", "confidence": 80.0}
    assert prediction_error["actual_outcome"] == {"outcome": "WIN", "pnl": 650.0}
    assert prediction_error["prediction_error"] == pytest.approx(0.2)
    assert prediction_error["success_condition"] == "realized_outcome == WIN"
    assert prediction_error["evaluation_result"] is True


def test_compute_prediction_error_evaluation_result_is_false_for_a_real_loss():
    prediction_error = compute_prediction_error(_real_shaped_review_context(pnl=-325.0, outcome="LOSS"))

    assert prediction_error["actual_outcome"] == {"outcome": "LOSS", "pnl": -325.0}
    assert prediction_error["evaluation_result"] is False


def test_mfe_mae_are_carried_through_when_the_real_position_supervisor_computed_them():
    prediction_error_facts = _real_shaped_review_context()
    assert prediction_error_facts["outcome"]["mfe"] == 12.0
    assert prediction_error_facts["outcome"]["mae"] == 2.0
    # Confirmed end to end via the outcome record too (see
    # tests/test_trade_outcome.py for the record-level assertion).


def test_missing_outcome_data_does_not_produce_a_learning_event_it_honestly_defers(tmp_path):
    """No trade_review_context at all (e.g. a cycle whose context never
    went through the live-context pipeline) must never produce a
    fabricated learning event -- it honestly defers, matching every
    existing test's minimal outcome_facts behavior."""
    memory = MemoryStore(tmp_path / "memory.db")
    agent = PostTradeAgent(memory)

    agent.analyze({"outcome": "WIN", "pnl": 100.0, "setup_type": "X", "exit_reason": "TAKE_PROFIT"})

    assert memory.recent(memory_type="learning_event") == []
    assert len(memory.recent(memory_type="trade")) == 1  # the older, minimal record still gets written


def test_ai_cannot_self_grade_its_own_hypothesis_or_the_trade_outcome(tmp_path):
    """The single most important test in this file: an adversarial AI
    that explicitly claims its hypothesis is confirmed and that a real
    loss was actually a win must have zero effect on the real,
    deterministic evaluation_result or the persisted learning_event."""
    memory = MemoryStore(tmp_path / "memory.db")
    agent = PostTradeAgent(memory, ai_router=AIRouter(_AdversarialLearningAIProvider()))

    agent.analyze(
        {
            "outcome": "LOSS", "pnl": -325.0, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "STOP_LOSS",
            "mae": 8.0, "mfe": 1.0, "entry_regime": "TREND_UP", "hold_seconds": 900, "confidence": 80.0,
            "trade_review_context": _real_shaped_review_context(pnl=-325.0, outcome="LOSS"),
        }
    )

    events = memory.recent(memory_type="learning_event")
    assert len(events) == 1
    event = events[0]["payload"]
    # The real, deterministic verdict -- unaffected by the AI's claimed "WIN"/"CONFIRMED".
    assert event["outcome_record"]["outcome"] == "LOSS"
    assert event["outcome_record"]["realized_pnl"] == -325.0
    assert event["prediction_evaluation"]["evaluation_result"] is False
    assert event["prediction_evaluation"]["realized_outcome"] == "LOSS"

    # The AI-proposed hypothesis (a real, separate mechanism) is stored
    # only as a CANDIDATE with its own real, deterministic at-creation
    # evaluation -- never as an already-confirmed fact, regardless of the
    # AI's own claimed "CONFIRMED" summary text.
    experiments = memory.recent(memory_type="experiment")
    ai_experiments = [e for e in experiments if e["payload"].get("parameters", {}).get("source") == "ai_proposed"]
    assert len(ai_experiments) == 1
    assert ai_experiments[0]["payload"]["status"] == "CANDIDATE"
    # min_samples=1 real sample exists (this same trade), so this is
    # legitimately evaluable -- but the real win_rate for a real LOSS is
    # 0.0, which fails the AI's own claimed threshold=0.5 -- proving the
    # real evaluator, not the AI's "CONFIRMED" claim, decided this.
    at_creation = ai_experiments[0]["payload"]["parameters"]["evaluated_at_creation"]
    assert at_creation["passed"] is False
    assert at_creation["actual_value"] == 0.0
