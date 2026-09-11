"""Phase 2 Piece 8, Requirements 5, 7, 8, 9, 10, 11, 14: end-to-end
correlation, no-look-ahead, real-data verification, and safety proofs
for the artifact/evidence layer.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agents.orchestrator import Orchestrator
from agents.trading_agents import PostTradeAgent
from ai.provider import UnavailableProvider
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from config import IST, Settings
from evidence.agent_output_artifact import AGENT_OUTPUT_MEMORY_TYPE
from evidence.ai_evidence import AI_EVIDENCE_MEMORY_TYPE
from evidence.decision_artifact import (
    DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE,
    DECISION_ARTIFACT_MEMORY_TYPE,
    build_decision_artifact,
    record_decision_artifact,
)
from evidence.reconstruction import ABSENT, reconstruct_decision_trace
from execution.decision_ledger import build_market_state_snapshot
from execution.live_context import assemble_context
from intelligence.market_regime import Regime
from storage.database import Database
from tests.test_decision_ledger import _trend_up_candles
from tests.test_trade_outcome import _real_shaped_review_context

NOW = datetime(2026, 9, 10, 9, 20, tzinfo=IST)


def _snapshot_kwargs(now: datetime, spot: float) -> dict:
    return {
        "now": now,
        "spot": spot,
        "features": {"ema_fast": 1, "ema_slow": 1, "close": spot, "vwap": spot, "atr": 5.0, "momentum": 0.0},
        "regime": Regime.TREND_UP,
        "trend_direction": "CALL",
        "gap_pct": 0.0,
        "option_quotes": [],
        "previous_option_quotes": [],
        "global_context": [],
        "news_items": [],
        "setups_evaluated": (),
        "winning_setup_type": "OPENING_RANGE_BREAKOUT",
        "winning_direction": "CALL",
    }


def _real_evaluated_cycle(tmp_path):
    """A real, full evaluated cycle -- real setup detection (execution/
    live_context.py::assemble_context, the exact same fixture tests/
    test_decision_ledger.py already uses to prove a real candidate
    forms), a real decision-ledger candidate id, run through a real
    Orchestrator with dry_run=True (no real notification transport)."""
    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(database_path=tmp_path / "paper.db", signal_threshold=50.0)
    db = Database(settings.database_path)
    db.initialize()
    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)
    orchestrator = Orchestrator(settings, db, dry_run=True)
    result = orchestrator.run_cycle(context)
    return orchestrator, db, result


class _FakeSuccessfulProvider:
    model = "claude-haiku-4-5-20251001"

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        if "hypothesis" in task.lower() or "condition" in str(facts).lower():
            pass
        return AIAnalysis(
            "real hypothesis proposal",
            65.0,
            source_facts={
                "task": task,
                "structured": {
                    "metric": "win_rate",
                    "setup_type": "OPENING_RANGE_BREAKOUT",
                    "regime": "TREND_UP",
                    "operator": ">=",
                    "threshold": 55.0,
                    "min_samples": 5,
                    "rationale": "real fake rationale",
                },
            },
        )


# --- 1. real evaluated cycle -> correlated agent-output + decision artifacts ---


def test_a_real_evaluated_cycle_produces_a_correlated_decision_artifact_and_agent_output_artifacts(tmp_path):
    orchestrator, _db, result = _real_evaluated_cycle(tmp_path)
    candidate_id = result.decision_ledger_candidate_id
    assert candidate_id is not None

    artifacts = orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)
    assert len(artifacts) == 1
    assert artifacts[0]["payload"]["decision_ledger_id"] == candidate_id

    agent_outputs = orchestrator.memory.recent(memory_type=AGENT_OUTPUT_MEMORY_TYPE, limit=50)
    assert len(agent_outputs) == len(result.agent_results)
    assert {a["payload"]["agent_name"] for a in agent_outputs} == set(result.agent_results.keys())
    assert all(a["payload"]["correlation_id"] == candidate_id for a in agent_outputs)


# --- 2. full real reconstruction for an evaluated-but-unclosed cycle ---


def test_reconstruct_decision_trace_returns_the_full_real_chain_for_an_evaluated_but_unclosed_cycle(tmp_path):
    orchestrator, db, result = _real_evaluated_cycle(tmp_path)
    candidate_id = result.decision_ledger_candidate_id

    trace = reconstruct_decision_trace(db, orchestrator.memory, candidate_id)

    assert trace["market_state"]["candidate_id"] == candidate_id
    assert trace["decision_artifact"]["decision_ledger_id"] == candidate_id
    assert set(trace["agent_outputs"].keys()) == set(result.agent_results.keys())
    assert all(v != ABSENT for v in trace["agent_outputs"].values())
    # Nothing closed yet -- honestly absent, never fabricated.
    assert trace["execution_fill"] == ABSENT
    assert trace["trade_outcome"] == ABSENT
    assert trace["learning_event"] == ABSENT
    assert trace["ai_hypothesis_evidence"] == ABSENT
    assert trace["corrections"] == []


# --- 3. repeated reconstruction is byte-identical ---


def test_reconstruction_is_deterministic_and_repeatable(tmp_path):
    orchestrator, db, result = _real_evaluated_cycle(tmp_path)
    candidate_id = result.decision_ledger_candidate_id

    first = reconstruct_decision_trace(db, orchestrator.memory, candidate_id)
    second = reconstruct_decision_trace(db, orchestrator.memory, candidate_id)

    assert first == second


# --- 4. an unknown candidate id reconstructs as fully, explicitly absent ---


def test_unknown_candidate_id_reconstructs_as_fully_and_explicitly_absent(tmp_path):
    db = Database(tmp_path / "paper.db")
    db.initialize()
    from learning.memory import MemoryStore

    store = MemoryStore(tmp_path / "learning.db")

    trace = reconstruct_decision_trace(db, store, "CAND-DOES-NOT-EXIST")

    assert trace["market_state"] == ABSENT
    assert trace["decision_artifact"] == ABSENT
    assert trace["agent_outputs"] == {}
    assert trace["trade_outcome"] == ABSENT
    assert trace["learning_event"] == ABSENT
    assert trace["execution_fill"] == ABSENT
    assert trace["ai_hypothesis_evidence"] == ABSENT
    assert trace["risk_decision"] == ABSENT
    assert trace["corrections"] == []


# --- 5. orphan detection: a decision artifact whose parent decision-ledger row is missing ---


def test_a_decision_artifact_referencing_a_missing_decision_ledger_row_reports_market_state_absent(tmp_path):
    """Requirement 5: 'No orphaned artifacts should be silently accepted
    where a required parent reference exists' -- the artifact itself is
    still returned (not silently dropped), but the missing parent is
    explicitly ABSENT, never fabricated to paper over the gap."""
    db = Database(tmp_path / "paper.db")
    db.initialize()  # deliberately never save_decision_ledger_entry
    from learning.memory import MemoryStore

    store = MemoryStore(tmp_path / "learning.db")
    artifact = build_decision_artifact(
        now=NOW, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id="CAND-ORPHAN"
    )
    record_decision_artifact(store, artifact, NOW)

    trace = reconstruct_decision_trace(db, store, "CAND-ORPHAN")

    assert trace["market_state"] == ABSENT
    assert trace["decision_artifact"]["decision_ledger_id"] == "CAND-ORPHAN"


# --- 6. no-look-ahead: reconstruction never returns a later observation ---


def test_no_look_ahead_reconstruction_only_ever_returns_data_at_or_before_the_referenced_decision(tmp_path):
    """Requirement 7's dedicated regression test: two real decision-
    ledger snapshots exist for the same real day, an earlier one (T1)
    and a later one (T2, different spot/timestamp) -- the decision
    artifact references ONLY T1. Reconstruction must return exactly T1's
    market state, never T2's, even though T2 already exists in the same
    real database by the time reconstruction runs."""
    db = Database(tmp_path / "paper.db")
    db.initialize()
    t1 = datetime(2026, 9, 10, 9, 20, tzinfo=IST)
    t2 = datetime(2026, 9, 10, 9, 35, tzinfo=IST)
    t1_id, t2_id = "CAND-20260910-092000-001", "CAND-20260910-093500-002"
    db.save_decision_ledger_entry(build_market_state_snapshot(candidate_id=t1_id, **_snapshot_kwargs(t1, 24080.0)))
    db.save_decision_ledger_entry(build_market_state_snapshot(candidate_id=t2_id, **_snapshot_kwargs(t2, 24200.0)))
    from learning.memory import MemoryStore

    store = MemoryStore(tmp_path / "learning.db")
    artifact = build_decision_artifact(
        now=t1, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id=t1_id
    )
    record_decision_artifact(store, artifact, t1)

    trace = reconstruct_decision_trace(db, store, t1_id)

    assert trace["market_state"]["candidate_id"] == t1_id
    assert trace["market_state"]["timestamp"] == t1.isoformat()
    assert trace["market_state"]["spot"] == 24080.0
    assert trace["market_state"]["spot"] != 24200.0
    assert t2_id not in str(trace)


# --- 7. closed-trade correlation, all the way through the learning event ---


def test_closed_trade_correlates_all_the_way_to_the_learning_event(tmp_path):
    """The real chain: pre-recorded DecisionArtifact -> PostTradeAgent
    closes the trade and proposes a real (fake-provider) hypothesis ->
    a DecisionArtifactCorrection links it in -> reconstruction surfaces
    the whole thing, including the real TradeOutcomeRecord and
    LearningEvent Piece 5 already builds."""
    db = Database(tmp_path / "paper.db")
    db.initialize()
    from learning.memory import MemoryStore

    store = MemoryStore(tmp_path / "learning.db")
    candidate_id = "CAND-20260910-093000-001"  # matches _real_shaped_review_context's candidate
    db.save_decision_ledger_entry(
        build_market_state_snapshot(candidate_id=candidate_id, **_snapshot_kwargs(NOW, 24080.0))
    )
    artifact = build_decision_artifact(
        now=NOW, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id=candidate_id
    )
    record_decision_artifact(store, artifact, NOW)

    review = PostTradeAgent(store, AIRouter(_FakeSuccessfulProvider())).run(
        {
            "outcome": "WIN",
            "pnl": 650.0,
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "exit_reason": "TAKE_PROFIT",
            "trade_review_context": _real_shaped_review_context(pnl=650.0, outcome="WIN"),
        }
    )
    assert review.data["outcome_id"] is not None

    trace = reconstruct_decision_trace(db, store, candidate_id)

    assert trace["trade_outcome"] != ABSENT
    assert trace["trade_outcome"]["realized_pnl"] == 650.0
    assert trace["execution_fill"] == {
        "entry_timestamp": "2026-09-10T09:20:00+05:30",
        "entry_price": 100.0,
        "exit_timestamp": "2026-09-10T10:00:00+05:30",
        "exit_price": 110.0,
    }
    assert trace["learning_event"] != ABSENT
    assert trace["learning_event"]["outcome_id"] == trace["trade_outcome"]["outcome_id"]
    assert len(trace["corrections"]) == 1
    assert trace["corrections"][0]["fields"]["ai_hypothesis_reference"] is not None
    assert trace["ai_hypothesis_evidence"]["available"] is True
    assert trace["ai_hypothesis_evidence"]["structured_output"]["metric"] == "win_rate"


# --- 8. missing optional AI evidence: provider genuinely unavailable ---


def test_missing_optional_ai_evidence_when_the_provider_is_unavailable_is_recorded_not_fabricated(tmp_path):
    from learning.memory import MemoryStore

    store = MemoryStore(tmp_path / "learning.db")
    candidate_id = "CAND-20260910-093000-001"
    artifact = build_decision_artifact(
        now=NOW, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id=candidate_id
    )
    record_decision_artifact(store, artifact, NOW)

    PostTradeAgent(store, AIRouter(UnavailableProvider())).run(
        {
            "outcome": "LOSS",
            "pnl": -100.0,
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "exit_reason": "STOP_LOSS",
            "trade_review_context": _real_shaped_review_context(pnl=-100.0, outcome="LOSS"),
        }
    )

    ai_evidence_rows = store.recent(memory_type=AI_EVIDENCE_MEMORY_TYPE, limit=10)
    assert len(ai_evidence_rows) >= 1
    assert all(row["payload"]["available"] is False for row in ai_evidence_rows)
    # No hypothesis was ever parsed from UnavailableProvider's canned
    # response, so no correction was recorded -- nothing to correct.
    assert store.recent(memory_type=DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE, limit=10) == []


# --- 9. deterministic rejection is recorded as evidence ---


def test_a_no_candidate_cycle_records_a_deterministic_rejection_as_evidence(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings, dry_run=True)

    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})

    artifacts = orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)
    assert len(artifacts) == 1
    assert artifacts[0]["payload"]["validation_decision"] == "REJECT"
    assert artifacts[0]["payload"]["risk_approved"] is None  # RiskAgent never ran
    agent_names = {
        a["payload"]["agent_name"]
        for a in orchestrator.memory.recent(memory_type=AGENT_OUTPUT_MEMORY_TYPE, limit=50)
    }
    assert "risk" not in agent_names
    assert "trade_builder" not in agent_names


# --- 10. DB isolation ---


def test_evidence_recording_never_touches_a_real_separately_populated_database(tmp_path):
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    _real_evaluated_cycle(tmp_path)  # runs entirely against its own isolated tmp_path files

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0


# --- 11. no notification side effects from evidence recording ---


def test_evidence_recording_reaches_no_real_notification_transport(tmp_path):
    def failing_transport(*args, **kwargs):
        raise AssertionError("evidence recording must never reach a real transport")

    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(database_path=tmp_path / "paper.db", signal_threshold=50.0)
    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)
    orchestrator = Orchestrator(settings, dry_run=True)
    orchestrator.telegram.transport = failing_transport
    orchestrator.discord.transport = failing_transport

    result = orchestrator.run_cycle(context)  # must not raise

    assert result.decision_ledger_candidate_id is not None
    assert len(orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)) == 1
