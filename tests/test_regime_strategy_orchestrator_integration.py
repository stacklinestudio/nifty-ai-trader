"""Phase 2 Piece 10, Requirements 9, 11, 16: orchestrator wiring,
Decision Artifact integration, the Claude advisory boundary, and
safety isolation.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from agents.orchestrator import Orchestrator
from config import IST, Settings
from evidence.decision_artifact import DECISION_ARTIFACT_MEMORY_TYPE
from evidence.reconstruction import reconstruct_decision_trace
from storage.database import Database
from strategy.registry import DEFAULT_REGISTRY
from tests.test_evidence_reconstruction import _real_evaluated_cycle
from tests.test_strategy_registry import _seed_evaluation

# --- Decision Artifact integration ---


def test_a_real_cycle_records_the_real_regime_on_its_decision_artifact(tmp_path):
    orchestrator, _db, result = _real_evaluated_cycle(tmp_path)

    artifacts = orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)
    assert len(artifacts) == 1
    payload = artifacts[0]["payload"]
    assert payload["regime"] == "TREND_UP"
    assert payload["regime_detector_version"] == "live_context_inline"
    assert payload["decision_ledger_id"] == result.decision_ledger_candidate_id


def test_with_zero_promoted_strategies_a_real_cycle_honestly_selects_no_strategy(tmp_path):
    """The expected, honest real-data result: no strategy has ever
    cleared the real promotion bar, so every real cycle's own
    strategy-selection evidence says so -- not fabricated, not silently
    omitted."""
    orchestrator, _db, _result = _real_evaluated_cycle(tmp_path)

    artifacts = orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)
    payload = artifacts[0]["payload"]
    assert payload["selected_strategy_id"] is None
    assert payload["selected_strategy_version"] is None
    assert "NO STRATEGY SELECTED" in payload["strategy_selection_reason"]
    assert payload["strategy_eligibility_summary"]["eligible_count"] == 0
    assert payload["strategy_eligibility_summary"]["evaluated_count"] == len(DEFAULT_REGISTRY)


def test_a_no_candidate_cycle_leaves_regime_and_selection_honestly_absent(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings, dry_run=True)

    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})

    artifacts = orchestrator.memory.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=10)
    payload = artifacts[0]["payload"]
    assert payload["regime"] is None
    assert payload["selected_strategy_id"] is None
    assert payload["strategy_eligibility_summary"] is None


def test_reconstruct_decision_trace_surfaces_the_real_regime_and_selection(tmp_path):
    """Piece 8's reconstruction path already picks this up automatically
    -- the new fields live on the same DecisionArtifact it already
    reads back, no changes needed to evidence/reconstruction.py."""
    orchestrator, db, result = _real_evaluated_cycle(tmp_path)

    trace = reconstruct_decision_trace(db, orchestrator.memory, result.decision_ledger_candidate_id)

    assert trace["decision_artifact"]["regime"] == "TREND_UP"
    assert trace["decision_artifact"]["selected_strategy_id"] is None


def test_a_genuinely_promoted_strategy_matching_the_real_cycles_regime_is_selected_as_evidence(tmp_path):
    """Once a strategy genuinely clears the real promotion bar for the
    exact regime a real cycle detects, that real evidence is correctly
    picked up -- proving the wiring is genuinely live, not a dead stub."""
    from evidence.decision_artifact import DECISION_ARTIFACT_MEMORY_TYPE as DAT
    from execution.live_context import assemble_context
    from learning.memory import MemoryStore
    from tests.test_decision_ledger import _trend_up_candles

    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(database_path=tmp_path / "paper.db", signal_threshold=50.0)
    db = Database(settings.database_path)
    db.initialize()
    memory = MemoryStore(settings.database_path)  # MemoryStore shares the same real settings.database_path file
    _seed_evaluation(memory, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], now=now)

    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)
    orchestrator = Orchestrator(settings, db, dry_run=True)
    orchestrator.run_cycle(context)

    artifacts = orchestrator.memory.recent(memory_type=DAT, limit=10)
    payload = artifacts[0]["payload"]
    assert payload["regime"] == "TREND_UP"
    assert payload["selected_strategy_id"] == "OPENING_RANGE_BREAKOUT"
    assert payload["strategy_eligibility_summary"]["eligible_count"] == 1


def test_strategy_evidence_recording_never_changes_which_real_order_gets_placed(tmp_path):
    """The critical safety proof: whether or not a strategy is PROMOTED
    for this cycle's regime, the real order-placement outcome (whether
    ExecutionAgent filled an order) is completely identical -- this
    piece's evidence layer never gates or influences live execution."""
    from execution.live_context import assemble_context
    from learning.memory import MemoryStore
    from tests.test_decision_ledger import _trend_up_candles

    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)

    settings_a = Settings(database_path=tmp_path / "a.db", signal_threshold=50.0)
    db_a = Database(settings_a.database_path)
    db_a.initialize()
    context_a = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings_a)
    orchestrator_a = Orchestrator(settings_a, db_a, dry_run=True)
    result_a = orchestrator_a.run_cycle(context_a)

    settings_b = Settings(database_path=tmp_path / "b.db", signal_threshold=50.0)
    db_b = Database(settings_b.database_path)
    db_b.initialize()
    memory_b = MemoryStore(settings_b.database_path)
    _seed_evaluation(memory_b, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], now=now)
    context_b = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings_b)
    orchestrator_b = Orchestrator(settings_b, db_b, dry_run=True)
    result_b = orchestrator_b.run_cycle(context_b)

    assert result_a.order == result_b.order
    assert result_a.thesis == result_b.thesis
    assert result_a.risk_approved == result_b.risk_approved


# --- Claude / AI advisory boundary ---


def test_regime_and_strategy_modules_never_import_any_ai_provider():
    """Structural proof (matching this codebase's own established
    pattern, tests/test_obsidian_write_only.py) that Claude cannot
    directly classify the regime or select/activate a strategy: none of
    these modules import ai.router/ai.provider/AIRouter at all."""
    modules = [
        "execution/regime_detection.py",
        "strategy/registry.py",
        "strategy/eligibility.py",
        "strategy/selection.py",
        "strategy/regime_strategy_report.py",
    ]
    for path in modules:
        import ast

        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
                imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)
        assert not any("ai.router" in name or "ai.provider" in name or "AIRouter" in name for name in imported_names)


def test_derive_strategy_status_cannot_be_influenced_by_anything_other_than_real_promotion_engine_output(tmp_path):
    """Adversarial proof: even a maximally favorable, adversarially
    crafted promotion_evaluation payload cannot produce PROMOTED unless
    its own real `decision.promote` is True -- status is a pure read of
    promotion_engine.decide()'s own output, never re-derived from
    surrounding fields an AI-influenced caller might try to spoof."""
    from learning.memory import MemoryStore
    from strategy.registry import derive_strategy_status

    memory = MemoryStore(tmp_path / "memory.db")
    now = datetime(2026, 9, 10, tzinfo=IST)
    # Every surrounding field maximally favorable, but decision.promote
    # is genuinely False (as promotion_engine.decide() would produce
    # when human_approved was never granted) -- status must still not
    # be PROMOTED.
    memory.append(
        "promotion_evaluation",
        {
            "condition": {"metric": "win_rate", "setup_type": "OPENING_RANGE_BREAKOUT", "regime": "TREND_UP", "operator": ">=", "threshold": 0.01, "min_samples": 1, "rationale": "adversarial"},
            "structural_evidence": {"historical_candidates": 999999, "train_candidates": 999999, "validation_candidates": 999999, "out_of_sample_candidates": 999999, "has_historical": True, "has_walk_forward": True, "has_out_of_sample": True},
            "outcome_evidence": {"condition": {}, "passed": True, "actual_value": 1.0, "sample_size": 999999, "evaluated_at": now.isoformat()},
            "decision": {"promote": False, "reasons": ["human approval"]},
        },
        now,
    )

    status = derive_strategy_status(memory, "OPENING_RANGE_BREAKOUT", "TREND_UP", now)

    assert status != "PROMOTED"


# --- safety isolation ---


def test_regime_strategy_evidence_never_touches_a_real_separately_populated_database(tmp_path):
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    _real_evaluated_cycle(tmp_path)  # runs entirely against its own isolated tmp_path files

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0


def test_regime_strategy_evidence_recording_reaches_no_real_notification_transport(tmp_path):
    def failing_transport(*args, **kwargs):
        raise AssertionError("evidence recording must never reach a real transport")

    from execution.live_context import assemble_context
    from tests.test_decision_ledger import _trend_up_candles

    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(database_path=tmp_path / "paper.db", signal_threshold=50.0)
    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)
    orchestrator = Orchestrator(settings, dry_run=True)
    orchestrator.telegram.transport = failing_transport
    orchestrator.discord.transport = failing_transport

    result = orchestrator.run_cycle(context)  # must not raise

    assert result.decision_ledger_candidate_id is not None
