"""Automated end-of-day report: the real, reusable compilation logic
extracted from the manually-compiled 2026-09-07 Day 1 report, plus its
real, absolute fail-closed entry point (`generate_and_notify_day_report`)
called automatically from `main.py::start_day()`. See also
`tests/test_start_day.py` for the call-site wiring itself.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from config import IST, Settings
from events.contracts import Event, EventType
from reports import day_session_report
from reports.day_session_report import (
    build_day_session_report,
    generate_and_notify_day_report,
    save_day_session_report,
)
from storage.database import Database
from storage.models import SignalRecord


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
    monkeypatch.setattr(day_session_report, "DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr(day_session_report, "TelegramNotifier", _RecordingTelegram)


def _seed_a_real_day(database: Database, today: date) -> None:
    now = datetime(today.year, today.month, today.day, 11, 0, 0, tzinfo=IST)
    database.save_event(Event(EventType.SYSTEM_STARTED, "orchestrator", now, output_summary={"trading_mode": "paper"}))
    database.save_event(
        Event(EventType.RISK_REJECTED, "orchestrator", now, output_summary={"reasons": ["no candidate"]})
    )
    database.save_signal(
        SignalRecord(
            timestamp=now,
            direction="PUT",
            confidence=58.0,
            features={
                "setup_type": "TREND_CONTINUATION",
                "regime": "TREND_DOWN",
                "technical_score": 75.0,
                "opening_score": 58.9,
                "volume_score": 50.2,
                "option_score": 20.0,
                "global_score": -0.0,
                "news_score": 0.0,
                "risk_penalty": 0.0,
                "data_completeness": 100.0,
                "cleared_threshold": False,
            },
        )
    )


# --- build_day_session_report: the real, reusable compilation logic ------


def test_build_day_session_report_reflects_real_seeded_signals_and_events(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    today = date(2026, 9, 8)
    _seed_a_real_day(database, today)

    report = build_day_session_report(database, settings, today)

    assert "# 2026-09-08 Live Session Report" in report
    assert "SYSTEM_STARTED" in report
    assert "RISK_REJECTED" in report
    assert "no candidate" in report
    # The same real honesty rule as the manually-compiled original: EV is
    # not fabricated or backfilled per historical signal.
    assert "EV is not a stored field" in report


def test_build_day_session_report_is_honest_about_an_empty_day(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    report = build_day_session_report(database, settings, date(2026, 9, 8))

    assert "No real signal records for this date." in report
    assert "No real events recorded for this date." in report


def test_build_day_session_report_keeps_the_incidents_heading_honestly_unfilled(tmp_path):
    """Section 5 in the original manual report ("incidents found and
    fixed") has no real automated data source -- this pins down that the
    automated version says so plainly rather than inventing a fake
    incident detector."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    report = build_day_session_report(database, settings, date(2026, 9, 8))

    assert "## 5. Real incidents found and fixed during today's live session" in report
    assert "Not automatically detected" in report


# --- save_day_session_report: the real, predictable, dated path ----------


def test_save_day_session_report_writes_to_the_real_predictable_path(tmp_path, monkeypatch):
    monkeypatch.setattr(day_session_report, "REPORTS_DIR", tmp_path / "reports" / "generated")
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    today = date(2026, 9, 8)
    _seed_a_real_day(database, today)

    path = save_day_session_report(database, settings, today)

    assert path == tmp_path / "reports" / "generated" / "day_2026-09-08_live_session_report.md"
    assert path.exists()
    assert "SYSTEM_STARTED" in path.read_text(encoding="utf-8")


# --- generate_and_notify_day_report: the real, absolute fail-closed entry point ---


def test_generate_and_notify_day_report_success_saves_and_sends_a_real_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(day_session_report, "REPORTS_DIR", tmp_path / "reports" / "generated")
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    today = date(2026, 9, 8)
    _seed_a_real_day(database, today)

    result = generate_and_notify_day_report(settings, database=database, today=today)

    assert result["status"] == "OK"
    saved_path = tmp_path / "reports" / "generated" / "day_2026-09-08_live_session_report.md"
    assert result["detail"] == str(saved_path)
    assert saved_path.exists()

    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "INFO"
    assert category == "daily_report"
    assert "2026-09-08" in message
    assert str(saved_path) in message
    assert len(_RecordingTelegram.instances[0].calls) == 1


def test_generate_and_notify_day_report_never_raises_on_a_real_failure_and_notifies(tmp_path, monkeypatch):
    """The real, explicit requirement under test: a report-generation
    failure must never propagate, and must still send a real (WARNING)
    notification via the same real daily_report channel."""
    _patch_notifiers(monkeypatch)
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    today = date(2026, 9, 8)

    def _broken_save(*args, **kwargs):
        raise RuntimeError("simulated real report-generation failure")

    monkeypatch.setattr(day_session_report, "save_day_session_report", _broken_save)

    result = generate_and_notify_day_report(settings, database=database, today=today)

    assert result["status"] == "FAILED"
    assert "simulated real report-generation failure" in result["detail"]
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "daily_report"
    assert "FAILED" in message
    assert "simulated real report-generation failure" in message


def test_generate_and_notify_day_report_never_raises_even_if_notification_itself_fails(tmp_path, monkeypatch):
    """Defense in depth: even a real failure inside the notification
    step itself (not just report generation) must never propagate."""
    monkeypatch.setattr(day_session_report, "REPORTS_DIR", tmp_path / "reports" / "generated")
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    class _BrokenDiscord:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated real notifier construction failure")

    monkeypatch.setattr(day_session_report, "DiscordNotifier", _BrokenDiscord)

    result = generate_and_notify_day_report(settings, database=database, today=date(2026, 9, 8))

    # The real report was still saved -- only the notification step broke.
    assert result["status"] == "OK"
