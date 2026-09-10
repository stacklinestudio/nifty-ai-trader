"""Phase 2 Piece 5, Requirement 5: deterministic aggregation of learning
evidence.
"""

from __future__ import annotations

from agents.trading_agents import PostTradeAgent
from learning.learning_aggregation import (
    aggregate_all_learning_evidence,
    aggregate_learning_evidence,
)
from learning.memory import MemoryStore
from tests.test_trade_outcome import _real_shaped_review_context


def _seed_real_learning_events(memory: MemoryStore, outcomes: list[tuple[float, str]]) -> None:
    agent = PostTradeAgent(memory)
    for pnl, outcome in outcomes:
        agent.analyze(
            {
                "outcome": outcome, "pnl": pnl, "setup_type": "OPENING_RANGE_BREAKOUT", "exit_reason": "TAKE_PROFIT" if outcome == "WIN" else "STOP_LOSS",
                "mae": 2.0, "mfe": 12.0, "entry_regime": "TREND_UP", "hold_seconds": 900, "confidence": 80.0,
                "trade_review_context": _real_shaped_review_context(pnl=pnl, outcome=outcome),
            }
        )


def test_aggregate_learning_evidence_is_honestly_empty_with_zero_real_events(tmp_path):
    """Matches this project's own real, current state: zero real trades
    have ever closed. The aggregation must report exactly that, never a
    fabricated non-zero sample."""
    memory = MemoryStore(tmp_path / "memory.db")

    aggregate = aggregate_learning_evidence(memory, "OPENING_RANGE_BREAKOUT", "TREND_UP")

    assert aggregate.sample_size == 0
    assert aggregate.win_rate is None
    assert aggregate.expectancy is None
    assert aggregate.success_rate is None
    assert aggregate.low_confidence is True


def test_aggregate_learning_evidence_reflects_real_seeded_win_loss_mix(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    _seed_real_learning_events(memory, [(650.0, "WIN"), (650.0, "WIN"), (-325.0, "LOSS")])

    aggregate = aggregate_learning_evidence(memory, "OPENING_RANGE_BREAKOUT", "TREND_UP")

    assert aggregate.sample_size == 3
    assert aggregate.win_rate == 2 / 3
    assert aggregate.expectancy == (650.0 + 650.0 - 325.0) / 3
    # success_rate mirrors win_rate here since the per-trade evaluation_
    # result is deterministically outcome=="WIN" (see learning/
    # prediction_review.py's own success_condition).
    assert aggregate.success_rate == 2 / 3
    assert aggregate.avg_mfe == 12.0
    assert aggregate.avg_mae == 2.0
    assert aggregate.low_confidence is True  # 3 real samples, below MIN_SAMPLES_FOR_CONFIDENCE=20


def test_aggregate_learning_evidence_never_mixes_a_different_setup_type_or_regime(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    _seed_real_learning_events(memory, [(650.0, "WIN")])

    other_setup = aggregate_learning_evidence(memory, "VWAP_BREAKOUT", "TREND_UP")
    other_regime = aggregate_learning_evidence(memory, "OPENING_RANGE_BREAKOUT", "TREND_DOWN")

    assert other_setup.sample_size == 0
    assert other_regime.sample_size == 0


def test_aggregate_all_learning_evidence_is_empty_with_zero_real_events(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")

    assert aggregate_all_learning_evidence(memory) == []


def test_aggregate_all_learning_evidence_finds_the_real_seeded_pair(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    _seed_real_learning_events(memory, [(650.0, "WIN"), (-325.0, "LOSS")])

    aggregates = aggregate_all_learning_evidence(memory)

    assert len(aggregates) == 1
    assert aggregates[0].setup_type == "OPENING_RANGE_BREAKOUT"
    assert aggregates[0].regime == "TREND_UP"
    assert aggregates[0].sample_size == 2
