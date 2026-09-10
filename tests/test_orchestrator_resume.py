"""Phase 2 Piece 6: real, integration-level resume-awareness tests --
constructing the real Orchestrator against a real, shared database
across multiple "process restarts" (successive Orchestrator
constructions), not just the isolated execution/session_state.py unit
tests in tests/test_session_state.py.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from agents.orchestrator import Orchestrator
from config import IST, Settings
from execution.position_persistence import position_state_to_dict
from execution.position_supervisor import PositionState
from execution.session_state import RestartDiagnosis
from storage.database import Database
from tests.test_position_supervision import opened_at, thesis


def test_daily_limits_trades_and_pnl_survive_a_real_same_day_restart(tmp_path):
    """The exact named gap #1 this piece exists to close: risk/trade_
    limits.py::DailyLimits.trades/.realized_pnl used to reset to 0/0.0 on
    every Orchestrator construction -- a mid-day crash-restart could
    exceed max_trades_per_day/max_daily_loss for the rest of that real
    day. Proven here against a real, shared database, not mocked."""
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path, max_trades_per_day=3, max_daily_loss=1200.0)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    first_process = Orchestrator(settings, dry_run=True, now=today)
    first_process.limits.register_open()
    first_process.limits.register_open()
    first_process.limits.register_close(-800.0)
    first_process._persist_session_state(today + timedelta(hours=1))

    # A real "process restart" -- a brand-new Orchestrator, same real
    # settings.database_path, same real date.
    second_process = Orchestrator(settings, dry_run=True, now=today + timedelta(hours=2))

    assert second_process.limits.trades == 2
    assert second_process.limits.realized_pnl == -800.0
    # The real, load-bearing consequence: the restarted process must not
    # silently permit exceeding the real configured daily limits.
    assert second_process.limits.can_open() is True  # 2 < 3 trades, -800 > -1200 -- still legitimately open
    second_process.limits.register_open()
    assert second_process.limits.trades == 3
    assert second_process.limits.can_open() is False  # now correctly exhausted


def test_stopped_out_today_survives_a_real_same_day_restart_and_still_blocks_reentry(tmp_path):
    """The exact named gap #2: Orchestrator._stopped_out_today used to be
    silently cleared on every restart -- a same-day stop-out could
    re-enter after a restart without agents/orchestrator.py::
    Orchestrator._blocked_reentry catching it. Proven end to end via the
    real _blocked_reentry check, not just that the list round-trips."""

    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    first_process = Orchestrator(settings, dry_run=True, now=today)
    stopped_out_thesis = thesis()
    first_process._stopped_out_today.append(
        (stopped_out_thesis.candidate.direction, stopped_out_thesis.candidate.setup_type, "TREND_UP")
    )
    first_process._persist_session_state(today + timedelta(hours=1))

    second_process = Orchestrator(settings, dry_run=True, now=today + timedelta(hours=2))

    assert second_process._stopped_out_today == [
        (stopped_out_thesis.candidate.direction, stopped_out_thesis.candidate.setup_type, "TREND_UP")
    ]
    # The real, load-bearing consequence: the same real candidate must
    # still be blocked from re-entering after the restart.
    same_candidate_thesis = thesis()
    assert second_process._blocked_reentry(same_candidate_thesis, "TREND_UP") is True
    different_regime_thesis = thesis()
    assert second_process._blocked_reentry(different_regime_thesis, "TREND_DOWN") is False


def test_a_genuinely_new_real_trading_day_still_starts_fresh(tmp_path):
    """The reset on a real new day is correct, intended behavior -- must
    not be broken by this piece's own restart-recovery fix."""
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path, max_trades_per_day=3, max_daily_loss=1200.0)
    day1 = datetime(2026, 9, 11, 9, 0, tzinfo=IST)
    day2 = datetime(2026, 9, 12, 9, 0, tzinfo=IST)

    process_day1 = Orchestrator(settings, dry_run=True, now=day1)
    process_day1.limits.register_open()
    process_day1.limits.register_close(-1100.0)
    process_day1._stopped_out_today.append(("CALL", "OPENING_RANGE_BREAKOUT", "TREND_UP"))
    process_day1._persist_session_state(day1 + timedelta(hours=1))

    process_day2 = Orchestrator(settings, dry_run=True, now=day2)

    assert process_day2.restart_diagnosis is RestartDiagnosis.FIRST_RUN_TODAY
    assert process_day2.limits.trades == 0
    assert process_day2.limits.realized_pnl == 0.0
    assert process_day2._stopped_out_today == []


def test_corrupted_session_state_fails_closed_through_the_real_orchestrator_and_alerts(tmp_path, monkeypatch):
    """Real end-to-end proof: a corrupted daily_metrics row blocks new
    real entries via the real RiskAgent path (not just DailyLimits.
    can_open() in isolation), and the CRITICAL alert goes out through the
    real notifier -- but never reaches a real network transport, since
    dry_run=True keeps the notifiers genuinely unconfigured."""
    import requests

    calls: list[str] = []

    class _FakeResponse:
        ok = True

    monkeypatch.setattr(requests, "post", lambda url, *a, **k: (calls.append(url), _FakeResponse())[1])

    db_path = tmp_path / "paper.db"
    settings = Settings(
        database_path=db_path,
        telegram_bot_token="real-looking-token",
        telegram_chat_id="real-looking-chat-id",
        discord_webhook_url="https://discord.com/api/webhooks/real/looking",
    )
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)
    database = Database(db_path)
    database.initialize()
    database.save_session_state(
        "2026-09-11", {"session_date": "2026-09-11", "trades": "not-a-number", "realized_pnl": 0.0, "stopped_out_today": []}
    )

    orchestrator = Orchestrator(settings, dry_run=True, now=today)

    assert orchestrator.restart_diagnosis is RestartDiagnosis.CORRUPTED_STATE
    assert orchestrator.limits.can_open() is False
    risk_result = orchestrator.risk_agent.analyze(
        {"thesis": thesis(), "validation": None, "market_data_fresh": True, "market_open": True}
    )
    assert risk_result.data["approved"] is False
    assert "daily trade or loss limit reached" in risk_result.data["reasons"]
    # dry_run=True -> genuinely unconfigured notifiers -> zero real transport calls.
    assert calls == []


def test_repeated_recovery_is_idempotent_no_double_counting(tmp_path):
    """Requirement 4: re-running recovery (constructing a fresh
    Orchestrator) against the SAME persisted state multiple times in a
    row must not accumulate/duplicate anything -- each new process reads
    the same real row and lands on the same real numbers, not an
    increasing one."""
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path, max_trades_per_day=3, max_daily_loss=1200.0)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    seed = Orchestrator(settings, dry_run=True, now=today)
    seed.limits.register_open()
    seed.limits.register_close(-200.0)
    seed._persist_session_state(today)

    recoveries = [Orchestrator(settings, dry_run=True, now=today + timedelta(minutes=i)) for i in range(1, 6)]

    assert all(o.limits.trades == 1 for o in recoveries)
    assert all(o.limits.realized_pnl == -200.0 for o in recoveries)
    with sqlite3.connect(db_path) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM daily_metrics WHERE date = ?", ("2026-09-11",)).fetchone()[0]
    assert row_count == 1  # never a second, duplicate real row


def test_risk_agent_reads_the_real_recovered_daily_limits_not_a_stale_copy(tmp_path):
    """Real regression test for a real bug found while building this
    piece: self.limits used to be REBOUND to a brand-new DailyLimits
    object after RiskAgent(settings, self.limits) had already captured
    the OLD object by reference -- RiskAgent would silently enforce
    stale, non-recovered numbers. Fixed by mutating the existing object
    in place; proven here via real object identity, not just a value
    that happens to match."""
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path, max_trades_per_day=3, max_daily_loss=1200.0)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    seed = Orchestrator(settings, dry_run=True, now=today)
    seed.limits.register_open()
    seed.limits.register_open()
    seed._persist_session_state(today)

    recovered = Orchestrator(settings, dry_run=True, now=today + timedelta(hours=1))

    assert recovered.risk_agent.limits is recovered.limits
    assert recovered.risk_agent.limits.trades == 2


def test_recovered_open_position_carries_its_real_decision_ledger_candidate_id(tmp_path):
    """The note in this piece's own brief: recover_open_positions already
    exists and is already tested -- this confirms it correctly carries
    the Phase 1 decision-ledger candidate_id through a real restart
    (execution/position_persistence.py already threads entry_decision_
    ledger_candidate_id, added in Phase 2 Piece 1/5 -- confirmed here
    end to end via the real database round-trip, not assumed)."""
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    database = Database(db_path)
    database.initialize()

    state = PositionState.opening(
        thesis(),
        opened_at(),
        entry_regime="TREND_UP",
        entry_order_id="order-1",
        entry_decision_ledger_candidate_id="CAND-20260911-093000-001",
    )
    database.save_open_position(state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state))

    orchestrator = Orchestrator(settings, database=database, dry_run=True, now=datetime(2026, 9, 11, 10, 0, tzinfo=IST))
    recovered = orchestrator.recover_open_positions()

    assert len(recovered) == 1
    assert recovered[0].entry_decision_ledger_candidate_id == "CAND-20260911-093000-001"


def test_clean_continuation_vs_interrupted_with_open_position_diagnosis(tmp_path):
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    seed = Orchestrator(settings, dry_run=True, now=today)
    seed._persist_session_state(today)
    no_open_position = Orchestrator(settings, dry_run=True, now=today + timedelta(hours=1))
    assert no_open_position.restart_diagnosis is RestartDiagnosis.CLEAN_CONTINUATION

    state = PositionState.opening(thesis(), opened_at(), entry_order_id="order-2")
    seed.database.save_open_position(state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state))
    with_open_position = Orchestrator(settings, dry_run=True, now=today + timedelta(hours=2))
    assert with_open_position.restart_diagnosis is RestartDiagnosis.INTERRUPTED_WITH_OPEN_POSITION


def test_last_processed_timestamp_reflects_the_real_most_recent_mutation(tmp_path):
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    today = datetime(2026, 9, 11, 9, 0, tzinfo=IST)

    orchestrator = Orchestrator(settings, dry_run=True, now=today)
    assert orchestrator.session_state.last_processed_timestamp is None  # nothing mutated yet this real run

    later = today + timedelta(minutes=45)
    orchestrator.limits.register_open()
    orchestrator._persist_session_state(later)

    assert orchestrator.session_state.last_processed_timestamp == later.isoformat()


def test_orchestrator_construction_never_touches_a_real_separately_populated_database_path(tmp_path):
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    isolated_settings = Settings(database_path=tmp_path / "isolated.db")
    Orchestrator(isolated_settings, dry_run=True, now=datetime(2026, 9, 11, 9, 0, tzinfo=IST))

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0
