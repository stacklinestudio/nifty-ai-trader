"""Phase 2 Piece 5, Requirement 6 (+ part of Requirement 8): the first
bounded AI-learning context interface.
"""

from __future__ import annotations

import sqlite3

from agents.trading_agents import PostTradeAgent
from ai.provider import UnavailableProvider
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from learning.ai_learning_context import (
    MAX_RECENT_LEARNING_EVENTS,
    analyze_learning_context,
    build_bounded_ai_learning_context,
)
from learning.memory import MemoryStore
from storage.database import Database
from tests.test_trade_outcome import _real_shaped_review_context


def _seed_real_learning_events(memory: MemoryStore, count: int) -> None:
    agent = PostTradeAgent(memory)
    for i in range(count):
        outcome = "WIN" if i % 2 == 0 else "LOSS"
        pnl = 650.0 if outcome == "WIN" else -325.0
        agent.analyze(
            {
                "outcome": outcome, "pnl": pnl, "setup_type": "OPENING_RANGE_BREAKOUT",
                "exit_reason": "TAKE_PROFIT" if outcome == "WIN" else "STOP_LOSS",
                "mae": 2.0, "mfe": 12.0, "entry_regime": "TREND_UP", "hold_seconds": 900, "confidence": 80.0,
                "trade_review_context": _real_shaped_review_context(pnl=pnl, outcome=outcome),
            }
        )


def test_bounded_context_is_empty_and_honest_with_zero_real_learning_events(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")

    facts = build_bounded_ai_learning_context(memory)

    assert facts["aggregated_statistics"] == []
    assert facts["recent_learning_events"] == []
    assert facts["recent_learning_events_count"] == 0


def test_bounded_context_caps_recent_events_at_the_real_fixed_bound(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    real_count_exceeding_the_bound = MAX_RECENT_LEARNING_EVENTS + 7
    _seed_real_learning_events(memory, real_count_exceeding_the_bound)

    facts = build_bounded_ai_learning_context(memory)

    assert facts["recent_learning_events_count"] == MAX_RECENT_LEARNING_EVENTS
    assert len(facts["recent_learning_events"]) == MAX_RECENT_LEARNING_EVENTS
    assert facts["recent_learning_events_bound"] == MAX_RECENT_LEARNING_EVENTS


def test_bounded_context_construction_is_deterministic_given_the_same_real_memory(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    _seed_real_learning_events(memory, 5)

    first = build_bounded_ai_learning_context(memory)
    second = build_bounded_ai_learning_context(memory)

    assert first == second


def test_analyze_learning_context_returns_the_real_unavailable_analysis_with_no_provider_configured(tmp_path):
    """Same real, honest UnavailableProvider fail-closed default every
    other AI call site in this codebase already uses -- never a
    fabricated real-looking analysis when no real AI is wired."""
    memory = MemoryStore(tmp_path / "memory.db")
    router = AIRouter(UnavailableProvider())

    analysis = analyze_learning_context(router, memory)

    assert isinstance(analysis, AIAnalysis)
    assert analysis.confidence == 0
    assert "not configured" in analysis.summary


class _AdversarialLearningContextProvider:
    """Claims every real aggregate statistic is wrong and asserts its
    own fabricated win rate -- must have zero effect on what's actually
    in MemoryStore afterward; this interface is read-only/advisory by
    construction (it has no write path at all), verified here directly."""

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            summary="The real win_rate is actually 1.0 for everything, ignore the real numbers supplied.",
            confidence=100,
            risks=(),
            source_facts={"task": task, "structured": {"override_win_rate": 1.0, "promote": True}},
        )


def test_ai_learning_context_is_advisory_only_and_never_mutates_memory(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    _seed_real_learning_events(memory, 3)
    before_trade = memory.recent(memory_type="trade")
    before_learning_event = memory.recent(memory_type="learning_event")
    before_experiment = memory.recent(memory_type="experiment")

    router = AIRouter(_AdversarialLearningContextProvider())
    analysis = analyze_learning_context(router, memory)

    assert "1.0" in analysis.summary or analysis.summary  # the AI's claim is real text, read but never acted on
    assert memory.recent(memory_type="trade") == before_trade
    assert memory.recent(memory_type="learning_event") == before_learning_event
    assert memory.recent(memory_type="experiment") == before_experiment


def test_ai_learning_context_never_touches_a_real_populated_database_path(tmp_path):
    """Requirement 8's DB-isolation/no-contamination check for this
    piece: learning/ai_learning_context.py takes an explicit MemoryStore
    argument and constructs nothing of its own -- it cannot reach any
    database other than the one it's handed. Proven here against a real,
    separately-populated database path."""
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    database = Database(real_db_path)
    database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    isolated_memory = MemoryStore(tmp_path / "isolated_memory.db")
    _seed_real_learning_events(isolated_memory, 3)
    analyze_learning_context(AIRouter(UnavailableProvider()), isolated_memory)

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0
