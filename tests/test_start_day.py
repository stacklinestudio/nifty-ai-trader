"""`python main.py start-day`: runs everything after the real Kite login
in one shot, in order. Real, explicit decision under test: a BLOCKED
health gate does not stop the sequence, but a failed real kite_connection
check does -- and one step's real failure (e.g. tick capture) must never
prevent the other steps from being attempted.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import main
from config import Settings
from data.calendar import NseCalendar
from monitoring.system_health_gate import GateCheck, GateReport

_A_REAL_TRADING_DAY = date(2026, 9, 8)  # a real Tuesday
_A_REAL_NON_TRADING_DAY = date(2026, 9, 6)  # a real Sunday


def _gate(verdict: str, kite_status: str = "OK", kite_detail: str = "real session valid") -> GateReport:
    checks = (
        GateCheck("kite_connection", kite_status, kite_detail),
        GateCheck("ai_provider", "OK", "provider=anthropic"),
        GateCheck("option_tick_capture", "OK", "1 real segment(s)"),
        GateCheck("instrument_archive", "OK", "real archive valid"),
        GateCheck("data_completeness", "OK", "50.0%"),
        GateCheck("notifications", "OK", "telegram=reachable"),
        GateCheck("risk_and_broker", "OK", "constructs cleanly"),
    )
    if verdict == "BLOCKED" and kite_status == "OK":
        # A real, non-kite failure so the gate is genuinely BLOCKED
        # without touching kite_connection.
        checks = (checks[0],) + (GateCheck("ai_provider", "FAIL", "AI provider unavailable"),) + checks[2:]
    return GateReport(verdict, checks)


class _CallTracker:
    """Records every real call made to it, and either returns a fixed
    real value or raises a fixed real exception -- used as a stand-in
    for archive_runner/capture_starter/scheduler_runner."""

    def __init__(self, return_value=None, raises: Exception | None = None):
        self.calls: list = []
        self.return_value = return_value
        self.raises = raises

    def __call__(self, settings):
        self.calls.append(settings)
        if self.raises is not None:
            raise self.raises
        return self.return_value


class _RecordingDiscord:
    instances: ClassVar[list[_RecordingDiscord]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []
        _RecordingDiscord.instances.append(self)

    def send_message(self, severity: str, message: str, category: str | None = None) -> bool:
        self.calls.append((severity, message, category))
        return True


class _RecordingTelegram:
    instances: ClassVar[list[_RecordingTelegram]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []
        _RecordingTelegram.instances.append(self)

    def send_message(self, severity: str, message: str) -> bool:
        self.calls.append((severity, message))
        return True


def _patch_notifiers(monkeypatch):
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr(main, "DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr(main, "TelegramNotifier", _RecordingTelegram)


def test_a_real_kite_connection_failure_stops_the_whole_sequence(tmp_path, monkeypatch):
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("BLOCKED", kite_status="FAIL", kite_detail="real session invalid: TokenException")
    archive_runner = _CallTracker(return_value=None)
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner,
    )

    assert result["stopped_after_gate"] is True
    assert archive_runner.calls == []  # never even attempted
    assert capture_starter.calls == []
    assert scheduler_runner.calls == []
    # The real gate result was still printed/notified regardless of outcome.
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert "kite_connection" in message


def test_a_blocked_gate_without_a_kite_failure_still_runs_the_whole_sequence(tmp_path, monkeypatch):
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("BLOCKED", kite_status="OK")  # BLOCKED for a different, non-kite reason
    archive_runner = _CallTracker(return_value=tmp_path / "archive.json")
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_TRADING_DAY,
    )

    assert result["stopped_after_gate"] is False
    assert len(archive_runner.calls) == 1
    assert len(capture_starter.calls) == 1
    assert len(scheduler_runner.calls) == 1
    assert result["archive"]["status"] == "OK"
    assert result["capture"]["status"] == "STARTED"
    assert result["scheduler"]["status"] == "OK"


def test_a_real_ready_gate_runs_the_whole_sequence(tmp_path, monkeypatch):
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("READY")
    archive_runner = _CallTracker(return_value=tmp_path / "archive.json")
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_TRADING_DAY,
    )

    assert result["gate"]["verdict"] == "READY"
    assert result["stopped_after_gate"] is False
    assert result["archive"]["status"] == "OK"
    assert result["capture"]["status"] == "STARTED"
    assert result["scheduler"]["status"] == "OK"
    severity, _message, _category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "INFO"


def test_a_non_kite_failure_eg_tick_capture_lets_the_rest_proceed_with_the_failure_reported(tmp_path, monkeypatch):
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("READY")
    archive_runner = _CallTracker(return_value=tmp_path / "archive.json")
    capture_starter = _CallTracker(raises=RuntimeError("simulated real tick-capture setup failure"))
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_TRADING_DAY,
    )

    # The one real failure is reported clearly...
    assert result["capture"]["status"] == "FAILED"
    assert "simulated real tick-capture setup failure" in result["capture"]["detail"]
    # ...but does not take down the other real steps.
    assert len(archive_runner.calls) == 1
    assert result["archive"]["status"] == "OK"
    assert len(scheduler_runner.calls) == 1
    assert result["scheduler"]["status"] == "OK"


def test_an_archive_failure_lets_capture_and_scheduler_still_proceed(tmp_path, monkeypatch):
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("READY")
    archive_runner = _CallTracker(raises=RuntimeError("simulated real archive failure"))
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_TRADING_DAY,
    )

    assert result["archive"]["status"] == "FAILED"
    assert "simulated real archive failure" in result["archive"]["detail"]
    assert len(capture_starter.calls) == 1
    assert result["capture"]["status"] == "STARTED"
    assert len(scheduler_runner.calls) == 1
    assert result["scheduler"]["status"] == "OK"


def test_a_real_health_gate_is_computed_when_none_is_injected(tmp_path, monkeypatch):
    """Production callers never pass `gate` -- confirms the default path
    actually runs the real System Health Gate, not a stub."""
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db", kite_api_key="", kite_access_token="")
    archive_runner = _CallTracker(return_value=None)
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    result = main.start_day(
        settings, archive_runner=archive_runner, capture_starter=capture_starter, scheduler_runner=scheduler_runner
    )

    # No real credentials configured -- the real gate's own kite_connection
    # check must genuinely fail, stopping the sequence for real.
    assert result["gate"]["verdict"] == "BLOCKED"
    assert result["stopped_after_gate"] is True
    assert archive_runner.calls == []


def test_capture_is_skipped_not_started_on_a_real_non_trading_day(tmp_path, monkeypatch):
    """Real finding from actually running start-day end to end: nothing
    would stream on a real non-trading day anyway (Brief 19), and
    launching a real, multi-hour background thread just to have it
    silently killed once the scheduler's own instant not-a-trading-day
    short-circuit returns is wasteful and dishonest about what really
    happened."""
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("READY")
    archive_runner = _CallTracker(return_value=tmp_path / "archive.json")
    capture_starter = _CallTracker(return_value=None)
    scheduler_runner = _CallTracker(return_value={"day_ran": False, "day_reason": "not_a_trading_day"})

    result = main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_NON_TRADING_DAY,
    )

    assert result["capture"]["status"] == "SKIPPED"
    assert "not a real NSE trading day" in result["capture"]["detail"]
    assert capture_starter.calls == []  # never even attempted
    # The other real steps still ran.
    assert len(archive_runner.calls) == 1
    assert len(scheduler_runner.calls) == 1


def test_the_real_capture_thread_is_joined_before_start_day_returns(tmp_path, monkeypatch):
    """The real bug found by actually running this end to end: a
    background daemon capture thread must never be silently killed by
    start_day returning first once the scheduler finishes."""
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    gate = _gate("READY")
    archive_runner = _CallTracker(return_value=tmp_path / "archive.json")

    class _FakeThread:
        def __init__(self):
            self.joined = False

        def join(self):
            self.joined = True

    fake_thread = _FakeThread()
    capture_starter = _CallTracker(return_value=fake_thread)
    scheduler_runner = _CallTracker(return_value={"day_ran": True})

    main.start_day(
        settings, gate=gate, archive_runner=archive_runner, capture_starter=capture_starter,
        scheduler_runner=scheduler_runner, today=_A_REAL_TRADING_DAY,
    )

    assert fake_thread.joined is True


def test_start_option_tick_capture_in_background_uses_a_real_nsecalendar_by_default():
    """Sanity check that the real NseCalendar this brief relies on
    behaves as expected for the two real dates used throughout these
    tests -- if this ever fails, every other test's premise is wrong."""
    calendar = NseCalendar()
    assert calendar.is_trading_day(_A_REAL_TRADING_DAY) is True
    assert calendar.is_trading_day(_A_REAL_NON_TRADING_DAY) is False
