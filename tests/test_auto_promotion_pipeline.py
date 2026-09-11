"""Phase 2 Piece 9: learning/auto_promotion_pipeline.py -- the missing
automatic connective wiring from a real LearningEvent to a real
promotion_engine.decide() call.

Section 1's audit, proven here rather than merely narrated:
1. AI hypothesis generation is ALREADY automatic on every real trade
   close (Piece 2/5/8, agents/trading_agents.py::PostTradeAgent) --
   test_connection_1.
2. A parsed AI hypothesis ALREADY automatically becomes a real,
   schema-complete Experiment row (learning/experiment_manager.py) --
   test_connection_2.
3. Before this piece, NOTHING automatically carried that Experiment
   through backtest/daily_walk_forward.py -- test_connection_3 proves
   the new wiring this piece adds closes exactly that gap.
4. The walk-forward/OOS result now automatically reaches
   learning.promotion_engine.decide() (via Piece 4's own, completely
   unmodified evaluate_experiment_for_promotion) -- test_connection_4.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import learning.promotion_pipeline as promotion_pipeline_module
from agents.trading_agents import PostTradeAgent
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from config import IST, Settings
from learning.auto_promotion_pipeline import (
    AI_PROPOSED_SOURCE,
    EXPERIMENT_MEMORY_TYPE,
    MAX_EXPERIMENTS_EVALUATED_PER_AUTO_RUN,
    PROMOTION_EVALUATION_MEMORY_TYPE,
    PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE,
    run_automatic_promotion_cycle,
)
from learning.experiment_manager import Experiment, create_experiment
from learning.hypothesis import HypothesisCondition
from learning.memory import MemoryStore
from learning.trade_memory import record_trade
from storage.database import Database
from tests.test_daily_walk_forward import _continuous_trending_days
from tests.test_trade_outcome import _real_shaped_review_context

NOW = datetime(2026, 9, 10, tzinfo=IST)


def _matching_condition(threshold: float = 0.5, min_samples: int = 3) -> HypothesisCondition:
    return HypothesisCondition(
        metric="win_rate",
        setup_type="OPENING_RANGE_BREAKOUT",
        regime="TREND_UP",
        operator=">=",
        threshold=threshold,
        min_samples=min_samples,
        rationale="test",
    )


def _seed_ai_proposed_experiment(
    memory: MemoryStore, condition: HypothesisCondition, now: datetime = NOW
) -> str:
    """The exact real shape agents/trading_agents.py::PostTradeAgent
    already writes automatically -- reproduced directly here (not via a
    full PostTradeAgent.run() call) so tests that only care about the
    downstream auto_promotion_pipeline behavior don't need to also
    drive a real AI call."""
    return create_experiment(
        memory,
        Experiment(
            f"AI hypothesis: {condition.metric} on {condition.setup_type}/{condition.regime}",
            {"source": AI_PROPOSED_SOURCE, "hypothesis_condition": condition.to_dict()},
            "v2",
        ),
        now,
    )


def _seed_winning_trades(memory: MemoryStore, count: int, now: datetime = NOW) -> None:
    for _ in range(count):
        record_trade(
            memory,
            {
                "outcome": "WIN",
                "pnl": 100.0,
                "setup_type": "OPENING_RANGE_BREAKOUT",
                "entry_regime": "TREND_UP",
                "exit_reason": "TAKE_PROFIT",
            },
            now,
        )


class _FakeHypothesisProvider:
    model = "claude-haiku-4-5-20251001"

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            "real fake hypothesis proposal",
            60.0,
            source_facts={
                "task": task,
                "structured": {
                    "metric": "win_rate",
                    "setup_type": "OPENING_RANGE_BREAKOUT",
                    "regime": "TREND_UP",
                    "operator": ">=",
                    "threshold": 0.5,
                    "min_samples": 3,
                    "rationale": "real fake rationale",
                },
            },
        )


# --- connection 1: AI hypothesis generation is already automatic on trade close ---


def test_connection_1_ai_hypothesis_generation_is_already_automatic_on_trade_close(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")

    review = PostTradeAgent(memory, AIRouter(_FakeHypothesisProvider())).run(
        {
            "outcome": "WIN",
            "pnl": 100.0,
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "exit_reason": "TAKE_PROFIT",
            "trade_review_context": _real_shaped_review_context(pnl=100.0, outcome="WIN"),
        }
    )

    # No manual call to _propose_hypothesis anywhere in this test -- a
    # single real PostTradeAgent.run() (exactly what a real trade close
    # already triggers) is enough for a real hypothesis to be proposed.
    assert review.data["ai_hypothesis_condition"] is not None
    assert review.data["ai_hypothesis_condition"]["setup_type"] == "OPENING_RANGE_BREAKOUT"


# --- connection 2: a parsed hypothesis already becomes a real Experiment row ---


def test_connection_2_a_parsed_ai_hypothesis_already_automatically_becomes_a_real_experiment_row(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")

    PostTradeAgent(memory, AIRouter(_FakeHypothesisProvider())).run(
        {
            "outcome": "WIN",
            "pnl": 100.0,
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "exit_reason": "TAKE_PROFIT",
            "trade_review_context": _real_shaped_review_context(pnl=100.0, outcome="WIN"),
        }
    )

    experiments = memory.recent(memory_type=EXPERIMENT_MEMORY_TYPE, limit=10)
    ai_proposed = [e for e in experiments if (e["payload"]["parameters"] or {}).get("source") == AI_PROPOSED_SOURCE]
    assert len(ai_proposed) == 1
    assert ai_proposed[0]["payload"]["parameters"]["hypothesis_condition"]["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert ai_proposed[0]["payload"]["status"] == "CANDIDATE"


# --- connection 3: the new wiring -- a pending experiment is automatically carried through backtest ---


def test_connection_3_a_pending_ai_proposed_experiment_is_automatically_carried_through_to_a_real_backtest(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition()
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 5)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    summary = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert len(summary.evaluated_experiment_ids) == 1
    evaluations = memory.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=10)
    assert len(evaluations) == 1
    # Real backtest evidence -- not zero/fabricated -- genuinely reflects
    # the real, consistently-trending fixture data.
    assert evaluations[0]["payload"]["structural_evidence"]["has_historical"] is True
    assert evaluations[0]["payload"]["structural_evidence"]["historical_candidates"] > 0


def test_connection_3_does_not_fire_when_no_pending_experiment_exists(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    summary = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert summary.total_pending_before_run == 0
    assert summary.evaluated_experiment_ids == ()
    assert memory.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=10) == []


def test_connection_3_ignores_non_ai_proposed_experiments():
    """Every real trade close ALSO creates a plain, non-AI, deterministic
    Experiment (agents/trading_agents.py's `hypothesis = f"..."` literal
    summary) -- this pipeline must never mistake that for a real,
    falsifiable AI-proposed hypothesis."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryStore(Path(tmp) / "memory.db")
        create_experiment(
            memory,
            Experiment("OPENING_STRUCTURE exiting via TAKE_PROFIT produced WIN (pnl=100).", {}, "v2"),
            NOW,
        )
        candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
        settings = Settings(signal_threshold=50.0, database_path=Path(tmp) / "settings.db")

        summary = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert summary.total_pending_before_run == 0


def test_connection_3_gracefully_skips_a_malformed_hypothesis_condition(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    create_experiment(
        memory,
        Experiment("AI hypothesis: malformed", {"source": AI_PROPOSED_SOURCE, "hypothesis_condition": {"metric": "win_rate"}}, "v2"),
        NOW,
    )
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    summary = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert len(summary.malformed_skipped_ids) == 1
    assert summary.evaluated_experiment_ids == ()


# --- connection 4: the real walk-forward/OOS result now automatically reaches decide() ---


def test_connection_4_the_real_computed_booleans_automatically_reach_promotion_engine_decide(tmp_path, monkeypatch):
    """Same real proof standard as Piece 4's own spy test
    (tests/test_promotion_pipeline.py), applied through this piece's new
    automatic entry point instead of a direct evaluate_experiment_for_
    promotion call."""
    calls = []
    real_decide = promotion_pipeline_module.decide

    def spy(has_historical, has_walk_forward, has_out_of_sample, human_approved):
        calls.append((has_historical, has_walk_forward, has_out_of_sample, human_approved))
        return real_decide(has_historical, has_walk_forward, has_out_of_sample, human_approved)

    monkeypatch.setattr(promotion_pipeline_module, "decide", spy)

    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition()
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 5)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert len(calls) == 1
    has_historical, has_walk_forward, has_out_of_sample, human_approved = calls[0]
    assert has_historical is True
    assert has_walk_forward is True
    assert has_out_of_sample is True  # real structural OOS evidence AND real 100% win rate
    assert human_approved is False  # see the adversarial test below


# --- the absolute line: no automatic self-promotion, ever ---


def test_adversarial_no_self_promotion_even_when_every_other_condition_is_satisfied(tmp_path):
    """The dedicated adversarial test the brief requires: every other
    gate is real and satisfied (strong structural evidence, a real
    100% win rate clearing threshold) -- the pipeline must still be
    structurally incapable of producing promote=True, because
    human_approved is never settable to True by this automatic path."""
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(threshold=0.5, min_samples=3)
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 10)  # strong, real, unambiguous winning track record
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    run_automatic_promotion_cycle(settings, candles, memory, NOW)

    evaluations = memory.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=10)
    assert len(evaluations) == 1
    payload = evaluations[0]["payload"]
    # Every other real gate genuinely passed...
    assert payload["structural_evidence"]["has_historical"] is True
    assert payload["structural_evidence"]["has_walk_forward"] is True
    assert payload["outcome_evidence"]["passed"] is True
    # ...and promotion is STILL refused, on human approval alone.
    assert payload["decision"]["promote"] is False
    assert "human approval" in " ".join(payload["decision"]["reasons"])


def test_human_approved_is_not_a_parameter_of_the_public_automatic_entry_point():
    """Structural proof, not just behavioral: run_automatic_promotion_
    cycle's real signature has no human_approved parameter at all -- so
    no caller, config value, or future edit-by-typo of a default value
    can accidentally make it settable to True from this automatic path."""
    import inspect

    from learning.auto_promotion_pipeline import run_automatic_promotion_cycle as fn

    assert "human_approved" not in inspect.signature(fn).parameters


# --- rate-limit / bounding ---


def test_rate_limit_bounds_experiments_evaluated_in_one_run(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(min_samples=1000)  # deliberately unreachable -- forces the cheap skip path
    for _ in range(7):
        _seed_ai_proposed_experiment(memory, condition)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    summary = run_automatic_promotion_cycle(settings, candles, memory, NOW, max_experiments=3)

    assert summary.total_pending_before_run == 7
    assert len(summary.skipped_insufficient_evidence_ids) == 3
    assert len(memory.recent(memory_type=PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE, limit=10)) == 3


def test_the_default_bound_is_a_real_small_explicit_number():
    assert MAX_EXPERIMENTS_EVALUATED_PER_AUTO_RUN == 5


def test_insufficient_real_evidence_skips_the_expensive_backtest_entirely(tmp_path, monkeypatch):
    """The second real bound: a hypothesis with too few real accumulated
    live-trade samples can never pass the outcome gate regardless of
    backtest structure, so the real, expensive backtest/walk-forward
    replay must never even be attempted for it."""
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("the real, expensive backtest replay must not run for insufficient evidence")

    monkeypatch.setattr(promotion_pipeline_module, "gather_structural_backtest_evidence", _must_not_be_called)
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(min_samples=50)  # far more than the 0 real trades seeded
    _seed_ai_proposed_experiment(memory, condition)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    summary = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert len(summary.skipped_insufficient_evidence_ids) == 1
    skipped = memory.recent(memory_type=PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE, limit=10)
    assert skipped[0]["payload"]["sample_size"] == 0


# --- resume / idempotency ---


def test_an_interrupted_run_resumes_without_duplicating_any_evaluation(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(min_samples=1000)  # cheap skip path -- fast, deterministic
    for _ in range(3):
        _seed_ai_proposed_experiment(memory, condition)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    first = run_automatic_promotion_cycle(settings, candles, memory, NOW, max_experiments=2)
    assert first.total_pending_before_run == 3
    assert len(first.skipped_insufficient_evidence_ids) == 2

    # Simulates a fresh process re-invoking the same real pipeline against
    # the same real, durable memory store -- not a resumed in-memory object.
    second = run_automatic_promotion_cycle(settings, candles, memory, NOW, max_experiments=2)

    assert second.total_pending_before_run == 1  # only the real remainder, not all 3 again
    assert len(second.skipped_insufficient_evidence_ids) == 1
    all_skipped = memory.recent(memory_type=PROMOTION_EVALUATION_SKIPPED_MEMORY_TYPE, limit=10)
    assert len(all_skipped) == 3  # exactly one record per real experiment, never duplicated
    referenced = {e["payload"]["source_experiment_memory_id"] for e in all_skipped}
    assert len(referenced) == 3


def test_a_fully_evaluated_experiment_is_never_re_evaluated_on_a_second_run(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition()
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 5)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")

    run_automatic_promotion_cycle(settings, candles, memory, NOW)
    second = run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert second.total_pending_before_run == 0
    assert len(memory.recent(memory_type=PROMOTION_EVALUATION_MEMORY_TYPE, limit=10)) == 1


# --- production DB isolation / no notification side effects ---


def test_the_real_backtest_replay_never_touches_a_real_separately_populated_database(tmp_path):
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition()
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 5)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=real_db_path)

    run_automatic_promotion_cycle(settings, candles, memory, NOW)

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0


def test_no_real_notification_side_effects(tmp_path, monkeypatch):
    import requests

    calls: list[str] = []

    class _FakeResponse:
        ok = True

    def tracking_transport(url, *args, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", tracking_transport)

    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition()
    _seed_ai_proposed_experiment(memory, condition)
    _seed_winning_trades(memory, 5)
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(
        signal_threshold=50.0,
        database_path=tmp_path / "settings.db",
        telegram_bot_token="real-looking-token",
        telegram_chat_id="real-looking-chat-id",
        discord_webhook_url="https://discord.com/api/webhooks/real/looking",
    )

    run_automatic_promotion_cycle(settings, candles, memory, NOW)

    assert calls == []
