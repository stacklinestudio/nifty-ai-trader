"""Phase 2 Piece 6: execution/session_state.py -- real, date-keyed
persistence for DailyLimits.trades/realized_pnl and _stopped_out_today.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from config import IST
from execution.session_state import (
    RestartDiagnosis,
    SessionState,
    build_fail_closed_state,
    diagnose_restart,
    fresh_session_state,
    load_session_state,
    parse_session_state,
    save_session_state,
    validate_session_state,
)
from storage.database import Database


def test_fresh_session_state_starts_at_zero():
    state = fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST))

    assert state.trades == 0
    assert state.realized_pnl == 0.0
    assert state.stopped_out_today == ()
    assert state.session_date == "2026-09-11"


def test_save_and_load_round_trips_through_the_real_database(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    state = SessionState(
        session_date="2026-09-11",
        trades=2,
        realized_pnl=-450.0,
        stopped_out_today=(("CALL", "OPENING_RANGE_BREAKOUT", "TREND_UP"),),
        strategy_version="v2",
        last_processed_timestamp="2026-09-11T10:00:00+05:30",
        updated_at="2026-09-11T10:00:00+05:30",
    )

    save_session_state(database, state)
    raw = load_session_state(database, date(2026, 9, 11))

    assert raw is not None
    assert validate_session_state(raw)
    restored = parse_session_state(raw)
    assert restored == state


def test_load_returns_none_for_a_date_with_no_real_persisted_row(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()

    assert load_session_state(database, date(2026, 9, 11)) is None


def test_save_session_state_upserts_not_duplicates(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    state_v1 = fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST))
    save_session_state(database, state_v1)
    state_v2 = SessionState(
        "2026-09-11", 1, -100.0, (), "v2", "2026-09-11T10:00:00+05:30", "2026-09-11T10:00:00+05:30"
    )
    save_session_state(database, state_v2)

    with sqlite3.connect(tmp_path / "paper.db") as conn:
        rows = conn.execute("SELECT COUNT(*) FROM daily_metrics WHERE date = ?", ("2026-09-11",)).fetchone()
    assert rows[0] == 1  # exactly one real row, the update replaced it -- not a second row


def test_validate_session_state_accepts_a_real_well_formed_payload():
    payload = fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST)).to_dict()
    assert validate_session_state(payload) is True


def test_validate_session_state_rejects_a_non_integer_trades_field():
    payload = {"session_date": "2026-09-11", "trades": "two", "realized_pnl": 0.0, "stopped_out_today": []}
    assert validate_session_state(payload) is False


def test_validate_session_state_rejects_a_negative_trades_field():
    payload = {"session_date": "2026-09-11", "trades": -1, "realized_pnl": 0.0, "stopped_out_today": []}
    assert validate_session_state(payload) is False


def test_validate_session_state_rejects_nan_realized_pnl():
    payload = {"session_date": "2026-09-11", "trades": 1, "realized_pnl": float("nan"), "stopped_out_today": []}
    assert validate_session_state(payload) is False


def test_incomplete_but_structurally_valid_state_still_parses_honestly(tmp_path):
    """A real "incomplete" state -- the required fields (session_date/
    trades/realized_pnl/stopped_out_today) are present and well-formed,
    but the newer optional fields (strategy_version/last_processed_
    timestamp/updated_at) are missing, e.g. a row saved by an older
    version of this module. Must parse honestly (validate_session_state
    still True, real trades/realized_pnl preserved) with the missing
    optional fields defaulting explicitly, never silently treated as a
    corrupted row."""
    payload = {"session_date": "2026-09-11", "trades": 2, "realized_pnl": -300.0, "stopped_out_today": []}

    assert validate_session_state(payload) is True
    restored = parse_session_state(payload)
    assert restored.trades == 2
    assert restored.realized_pnl == -300.0
    assert restored.strategy_version == "v2"  # real, documented default
    assert restored.last_processed_timestamp is None  # honestly absent, not fabricated


def test_validate_session_state_rejects_a_malformed_stopped_out_entry():
    payload = {
        "session_date": "2026-09-11", "trades": 1, "realized_pnl": 0.0,
        "stopped_out_today": [["CALL", "OPENING_RANGE_BREAKOUT"]],  # missing the 3rd element
    }
    assert validate_session_state(payload) is False


def test_validate_session_state_rejects_a_missing_stopped_out_today_field():
    payload = {"session_date": "2026-09-11", "trades": 1, "realized_pnl": 0.0}
    assert validate_session_state(payload) is False


def test_build_fail_closed_state_blocks_new_entries():
    from risk.trade_limits import DailyLimits

    state = build_fail_closed_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST))
    limits = DailyLimits(3, 1200.0, trades=state.trades, realized_pnl=state.realized_pnl)

    assert limits.can_open() is False


def test_diagnose_restart_first_run_today():
    assert diagnose_restart(None, open_position_count=0) is RestartDiagnosis.FIRST_RUN_TODAY


def test_diagnose_restart_clean_continuation():
    payload = fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST)).to_dict()
    assert diagnose_restart(payload, open_position_count=0) is RestartDiagnosis.CLEAN_CONTINUATION


def test_diagnose_restart_interrupted_with_open_position():
    payload = fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST)).to_dict()
    assert diagnose_restart(payload, open_position_count=1) is RestartDiagnosis.INTERRUPTED_WITH_OPEN_POSITION


def test_diagnose_restart_corrupted_state():
    payload = {"session_date": "2026-09-11", "trades": "not-a-number", "realized_pnl": 0.0, "stopped_out_today": []}
    assert diagnose_restart(payload, open_position_count=0) is RestartDiagnosis.CORRUPTED_STATE


def test_session_state_never_touches_a_real_separately_populated_database_path(tmp_path):
    """Requirement 8/9's DB-isolation proof for this module specifically:
    load_session_state/save_session_state take an explicit Database
    argument and construct nothing of their own."""
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    isolated_database = Database(tmp_path / "isolated.db")
    isolated_database.initialize()
    save_session_state(
        isolated_database, fresh_session_state(date(2026, 9, 11), "v2", datetime(2026, 9, 11, 9, 0, tzinfo=IST))
    )
    load_session_state(isolated_database, date(2026, 9, 11))

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0
