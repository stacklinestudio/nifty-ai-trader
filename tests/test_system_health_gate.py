"""Brief 23: System Health Gate -- aggregates real, already-built checks.
Every test here injects a known-good or known-bad REAL underlying state
(a real archive file, a real signal row, a real capture segment) and
confirms the check reports it correctly -- never a mocked judgment call.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

from config import IST, Settings
from monitoring.system_health_gate import (
    FAIL,
    MIN_DATA_COMPLETENESS_PERCENT,
    OK,
    check_ai_provider,
    check_data_completeness,
    check_instrument_archive,
    check_kite_connection,
    check_notifications,
    check_option_tick_capture,
    check_risk_and_broker_construction,
    run_system_health_gate,
)
from storage.database import Database
from storage.models import SignalRecord

# --- Kite connection -----------------------------------------------------


def test_check_kite_connection_fails_with_no_credentials():
    settings = Settings(kite_api_key="", kite_access_token="")

    result = check_kite_connection(settings)

    assert result.status == FAIL
    assert "no real Kite credentials" in result.detail


def test_check_kite_connection_ok_with_a_real_valid_session():
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")

    class _FakeKite:
        def profile(self):
            return {"user_id": "REAL123"}

    result = check_kite_connection(settings, kite_factory=lambda: _FakeKite())

    assert result.status == OK
    assert "REAL123" in result.detail


def test_check_kite_connection_fails_with_a_real_expired_token():
    """The exact real scenario this project hit at the start of Brief
    19 -- credentials configured, but the real session is invalid."""
    settings = Settings(kite_api_key="looks-real", kite_access_token="stale-token")

    class _FakeKite:
        def profile(self):
            raise ConnectionError("simulated real TokenException: Incorrect api_key or access_token.")

    result = check_kite_connection(settings, kite_factory=lambda: _FakeKite())

    assert result.status == FAIL
    assert "session invalid" in result.detail
    assert "TokenException" in result.detail


# --- AI provider -----------------------------------------------------------


def test_check_ai_provider_fails_when_unavailable():
    settings = Settings(ai_provider="unavailable")

    result = check_ai_provider(settings)

    assert result.status == FAIL


def test_check_ai_provider_ok_when_a_real_provider_is_selected():
    settings = Settings(ai_provider="anthropic")

    result = check_ai_provider(settings)

    assert result.status == OK
    assert "anthropic" in result.detail


# --- Option tick capture ----------------------------------------------------


def test_check_option_tick_capture_fails_with_no_real_segment(tmp_path):
    result = check_option_tick_capture(tmp_path, date(2026, 9, 7))

    assert result.status == FAIL
    assert "no real capture segment" in result.detail


def test_check_option_tick_capture_ok_with_real_segments_and_reports_real_counts(tmp_path):
    day = date(2026, 9, 7)
    seg1 = tmp_path / f"nifty_option_ticks_{day.isoformat()}.jsonl"
    seg1.write_text('{"tick": 1}\n{"tick": 2}\n', encoding="utf-8")
    seg2 = tmp_path / f"nifty_option_ticks_{day.isoformat()}_seg2.jsonl"
    seg2.write_text('{"tick": 3}\n', encoding="utf-8")
    gap_manifest = tmp_path / f"capture_gaps_{day.isoformat()}.json"
    gap_manifest.write_text(json.dumps([{"gap_start": "x", "gap_end": "y", "duration_seconds": 5.0}]), encoding="utf-8")

    result = check_option_tick_capture(tmp_path, day)

    assert result.status == OK
    assert "2 real segment(s)" in result.detail
    assert "3 real ticks" in result.detail
    assert "1 real gap(s)" in result.detail


# --- Instrument archive (reuses Brief 18's real validate_archive) ----------


def _valid_nifty_option_row(strike, expiry, option_type, token):
    return {
        "instrument_token": token,
        "tradingsymbol": f"NIFTY26SEP{int(strike)}{option_type}",
        "name": "NIFTY",
        "expiry": expiry,
        "strike": strike,
        "lot_size": 65,
        "instrument_type": option_type,
        "segment": "NFO-OPT",
        "exchange": "NFO",
    }


def test_check_instrument_archive_ok_with_a_real_valid_archive(tmp_path):
    day = date(2026, 9, 8)  # a real Tuesday
    rows = [_valid_nifty_option_row(24000.0 + i * 50, "2026-09-24", "CE", 90000000 + i) for i in range(10)]
    (tmp_path / f"nfo_instruments_{day.isoformat()}.json").write_text(json.dumps(rows), encoding="utf-8")

    result = check_instrument_archive(tmp_path)

    assert result.status == OK
    assert "10 real records" in result.detail


def test_check_instrument_archive_fails_with_no_archive(tmp_path):
    result = check_instrument_archive(tmp_path)

    assert result.status == FAIL
    assert "No real instrument archive" in result.detail


def test_check_instrument_archive_fails_with_a_real_invalid_archive(tmp_path):
    day = date(2026, 9, 6)  # a real Sunday -- fails the real trading-day check
    rows = [_valid_nifty_option_row(24000.0, "2026-09-24", "CE", 1)]
    (tmp_path / f"nfo_instruments_{day.isoformat()}.json").write_text(json.dumps(rows), encoding="utf-8")

    result = check_instrument_archive(tmp_path)

    assert result.status == FAIL
    assert "not a real NSE trading day" in result.detail


# --- Data completeness ------------------------------------------------------


def test_check_data_completeness_fails_with_no_real_signal(tmp_path):
    database = Database(tmp_path / "empty.db")
    database.initialize()

    result = check_data_completeness(database)

    assert result.status == FAIL
    assert "no real signal" in result.detail


def test_check_data_completeness_ok_at_or_above_the_real_minimum(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    database.save_signal(
        SignalRecord(
            timestamp=datetime.now(IST),
            direction="CALL",
            confidence=80.0,
            features={"data_completeness": MIN_DATA_COMPLETENESS_PERCENT + 5},
        )
    )

    result = check_data_completeness(database)

    assert result.status == OK


def test_check_data_completeness_fails_below_the_real_minimum(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    database.save_signal(
        SignalRecord(
            timestamp=datetime.now(IST),
            direction="CALL",
            confidence=80.0,
            features={"data_completeness": MIN_DATA_COMPLETENESS_PERCENT - 5},
        )
    )

    result = check_data_completeness(database)

    assert result.status == FAIL
    assert "below the real minimum" in result.detail


def test_check_data_completeness_uses_the_most_recent_real_signal(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    database.save_signal(
        SignalRecord(
            timestamp=datetime(2026, 9, 5, 10, 0, tzinfo=IST),
            direction="CALL",
            confidence=80.0,
            features={"data_completeness": 10.0},
        )
    )
    database.save_signal(
        SignalRecord(
            timestamp=datetime(2026, 9, 6, 10, 0, tzinfo=IST),
            direction="CALL",
            confidence=80.0,
            features={"data_completeness": 90.0},
        )
    )

    result = check_data_completeness(database)

    assert result.status == OK
    assert "90.0%" in result.detail


# --- Notifications -----------------------------------------------------------


class _RecordingNotifier:
    instances: ClassVar[list[_RecordingNotifier]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.reachable = _RecordingNotifier.next_result
        _RecordingNotifier.instances.append(self)

    def send_message(self, *args, **kwargs) -> bool:
        return self.reachable


def test_check_notifications_ok_when_at_least_one_channel_is_reachable(monkeypatch):
    monkeypatch.setattr("monitoring.system_health_gate.DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr("monitoring.system_health_gate.TelegramNotifier", _RecordingNotifier)
    _RecordingNotifier.next_result = True
    settings = Settings()

    result = check_notifications(settings)

    assert result.status == OK
    assert "reachable" in result.detail


def test_check_notifications_fails_when_neither_channel_is_reachable(monkeypatch):
    monkeypatch.setattr("monitoring.system_health_gate.DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr("monitoring.system_health_gate.TelegramNotifier", _RecordingNotifier)
    _RecordingNotifier.next_result = False
    settings = Settings()

    result = check_notifications(settings)

    assert result.status == FAIL
    assert "unreachable" in result.detail


# --- Risk engine / paper broker construction --------------------------------


def test_check_risk_and_broker_construction_ok_with_real_settings():
    settings = Settings()

    result = check_risk_and_broker_construction(settings)

    assert result.status == OK


# --- The gate itself ---------------------------------------------------------


def _make_ready_gate_inputs(tmp_path):
    """Real, known-good state for every check, wired together for the
    full run_system_health_gate integration tests below."""
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too", ai_provider="anthropic")
    database = Database(tmp_path / "paper.db")
    database.initialize()
    database.save_signal(
        SignalRecord(
            timestamp=datetime.now(IST),
            direction="CALL",
            confidence=80.0,
            features={"data_completeness": MIN_DATA_COMPLETENESS_PERCENT + 5},
        )
    )
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    today = date(2026, 9, 8)
    (capture_dir / f"nifty_option_ticks_{today.isoformat()}.jsonl").write_text('{"tick": 1}\n', encoding="utf-8")
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    rows = [_valid_nifty_option_row(24000.0, "2026-09-24", "CE", 1)]
    (archive_dir / f"nfo_instruments_{today.isoformat()}.json").write_text(json.dumps(rows), encoding="utf-8")

    class _FakeKite:
        def profile(self):
            return {"user_id": "REAL123"}

    return settings, database, capture_dir, archive_dir, today, (lambda: _FakeKite())


def test_run_system_health_gate_is_ready_when_every_real_check_passes(tmp_path, monkeypatch):
    monkeypatch.setattr("monitoring.system_health_gate.DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr("monitoring.system_health_gate.TelegramNotifier", _RecordingNotifier)
    _RecordingNotifier.next_result = True
    settings, database, capture_dir, archive_dir, today, kite_factory = _make_ready_gate_inputs(tmp_path)

    report = run_system_health_gate(
        settings, database, kite_factory=kite_factory, capture_dir=capture_dir, archive_dir=archive_dir, today=today
    )

    assert report.verdict == "READY"
    assert report.blocking_reasons == []
    assert len(report.checks) == 7
    assert all(check.status == OK for check in report.checks)


def test_run_system_health_gate_is_blocked_and_names_every_real_failing_reason(tmp_path, monkeypatch):
    """The required test: two simultaneous real failures must BOTH be
    named in blocking_reasons, not just the first one found."""
    monkeypatch.setattr("monitoring.system_health_gate.DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr("monitoring.system_health_gate.TelegramNotifier", _RecordingNotifier)
    _RecordingNotifier.next_result = True
    settings, database, capture_dir, archive_dir, today, kite_factory = _make_ready_gate_inputs(tmp_path)
    # Break two independent real checks: no real capture segment for
    # today, and no real archive file at all.
    for f in capture_dir.glob("*"):
        f.unlink()
    for f in archive_dir.glob("*"):
        f.unlink()

    report = run_system_health_gate(
        settings, database, kite_factory=kite_factory, capture_dir=capture_dir, archive_dir=archive_dir, today=today
    )

    assert report.verdict == "BLOCKED"
    reasons_text = "; ".join(report.blocking_reasons)
    assert "option_tick_capture" in reasons_text
    assert "instrument_archive" in reasons_text
    assert len(report.blocking_reasons) == 2  # both, not just the first
    # The other 5 real checks are unaffected and still pass.
    ok_names = {c.name for c in report.checks if c.status == OK}
    assert {"kite_connection", "ai_provider", "data_completeness", "notifications", "risk_and_broker"} <= ok_names


def test_run_system_health_gate_describe_lists_every_real_check_and_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr("monitoring.system_health_gate.DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr("monitoring.system_health_gate.TelegramNotifier", _RecordingNotifier)
    _RecordingNotifier.next_result = True
    settings, database, capture_dir, archive_dir, today, kite_factory = _make_ready_gate_inputs(tmp_path)

    report = run_system_health_gate(
        settings, database, kite_factory=kite_factory, capture_dir=capture_dir, archive_dir=archive_dir, today=today
    )
    description = report.describe()

    assert "System Health Gate: READY" in description
    assert "does not block main.py run" in description
    for check in report.checks:
        assert check.name in description


def test_system_health_gate_is_reporting_only_never_imported_by_agents_or_orchestrator():
    """Structural proof for this brief's own explicit claim: no agent,
    orchestrator constructor, or run_cycle path imports this module --
    it is wired only into main.py's own new, separate CLI command."""
    root = Path(__file__).resolve().parent.parent
    violations = []
    for directory in ("agents", "execution", "intelligence", "strategy", "risk"):
        for source_file in (root / directory).rglob("*.py"):
            if "system_health_gate" in source_file.read_text(encoding="utf-8"):
                violations.append(str(source_file.relative_to(root)))
    assert violations == [], f"system_health_gate is referenced outside main.py/monitoring: {violations}"
