"""Brief 13 Part 2: proactive daily NFO instrument archiving. Real,
live-confirmed problem this closes: Kite's /instruments endpoint purges
every contract whose expiry has already passed, and this project lost
its already-elapsed 42-day window's option data because no dump was
ever saved before those contracts expired. Proves the archive writes a
real file with the real raw response, and that a missing/invalid real
session fails closed (skipped, logged, never a crash, never a
fabricated placeholder archive) -- the real operational constraint
(Kite access tokens are single-day, requiring a genuine daily
interactive login) means most scheduled runs on most days will hit
exactly this path.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import ClassVar

import pytest

from config import Settings
from data.calendar import NseCalendar
from data.instrument_archive import (
    ARCHIVE_DIR,
    archive_nfo_instruments,
    check_and_notify_missing_archive,
    find_missing_previous_archive,
    is_date_validated,
    run_daily_archive,
    validate_archive,
)


def _valid_nifty_option_row(strike: float, expiry: str, option_type: str, token: int) -> dict:
    """A real-shaped NIFTY option record -- every field
    data/instruments.py::parse_kite_instruments needs, matching the real
    schema confirmed against this project's own real archived file
    (data/private/instrument_archives/nfo_instruments_2026-09-05.json)."""
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


def _valid_nifty_option_rows(n: int, expiry: str = "2026-09-24") -> list[dict]:
    return [
        _valid_nifty_option_row(24000.0 + i * 50, expiry, "CE" if i % 2 == 0 else "PE", token=90000000 + i)
        for i in range(n)
    ]


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class _FakeKite:
    def __init__(self, rows: list[dict] | None = None, raise_error: Exception | None = None) -> None:
        self._rows = rows if rows is not None else [{"tradingsymbol": "NIFTY26SEPFUT", "name": "NIFTY"}]
        self._raise_error = raise_error

    def instruments(self, segment: str) -> list[dict]:
        if self._raise_error:
            raise self._raise_error
        assert segment == "NFO"
        return self._rows


def test_archive_writes_the_real_raw_response_to_a_timestamped_file(tmp_path):
    kite = _FakeKite([{"tradingsymbol": "NIFTY2690824200CE", "strike": 24200.0, "expiry": "2026-09-08"}])

    path = archive_nfo_instruments(kite, archive_dir=tmp_path, today=date(2026, 9, 5))

    assert path == tmp_path / "nfo_instruments_2026-09-05.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == [{"tradingsymbol": "NIFTY2690824200CE", "strike": 24200.0, "expiry": "2026-09-08"}]


def test_archive_is_idempotent_for_the_same_real_day(tmp_path):
    kite = _FakeKite([{"tradingsymbol": "A"}])
    archive_nfo_instruments(kite, archive_dir=tmp_path, today=date(2026, 9, 5))

    kite2 = _FakeKite([{"tradingsymbol": "B"}])  # a second real run the same day -- overwrites cleanly
    path = archive_nfo_instruments(kite2, archive_dir=tmp_path, today=date(2026, 9, 5))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == [{"tradingsymbol": "B"}]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_run_daily_archive_fails_closed_with_no_credentials_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="", kite_access_token="")

    result = run_daily_archive(settings)  # must not raise

    assert result is None


def test_run_daily_archive_fails_closed_on_a_real_expired_token_error(tmp_path, monkeypatch):
    """The real, expected everyday case: Kite access tokens are single-day
    -- a scheduled run on a day nobody has logged in yet hits this exact
    path, not a crash."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="looks-real", kite_access_token="stale-token")

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                self.api_key = api_key

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                raise ConnectionError("simulated real TokenException: Incorrect api_key or access_token.")

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    result = run_daily_archive(settings)  # must not raise

    assert result is None


def test_run_daily_archive_succeeds_with_a_real_looking_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    rows = _valid_nifty_option_rows(5)

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                return rows

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 9, 0, tzinfo=tz)  # a real Tuesday, deterministic

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)

    result = run_daily_archive(settings)

    assert result is not None
    assert result.exists()
    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved == rows


# --- Real, same-day missing-archive safeguard -------------------------


def _write_fake_archive(archive_dir, day: date) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"nfo_instruments_{day.isoformat()}.json").write_text("[]", encoding="utf-8")


class _RecordingDiscord:
    """Records every real send_message call instead of making a real
    HTTP request -- proves check_and_notify_missing_archive actually
    calls the existing Discord "system" channel wiring, without a real
    webhook."""

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


def test_find_missing_previous_archive_detects_a_real_gap(tmp_path):
    calendar = NseCalendar()
    _write_fake_archive(tmp_path, date(2026, 9, 7))  # real Monday: archived
    # 2026-09-08 (real Tuesday) is deliberately NOT archived -- the gap.
    today = date(2026, 9, 9)  # real Wednesday, the next scheduled run

    missing = find_missing_previous_archive(tmp_path, calendar, today)

    assert missing == date(2026, 9, 8)


def test_find_missing_previous_archive_stays_silent_for_unbroken_history(tmp_path):
    calendar = NseCalendar()
    _write_fake_archive(tmp_path, date(2026, 9, 8))  # real Tuesday: archived
    today = date(2026, 9, 9)  # real Wednesday

    missing = find_missing_previous_archive(tmp_path, calendar, today)

    assert missing is None


def test_find_missing_previous_archive_skips_real_weekends(tmp_path):
    """A real Monday's scheduled run must compare against last real
    Friday, not the weekend -- otherwise every real Monday would falsely
    alarm."""
    calendar = NseCalendar()
    _write_fake_archive(tmp_path, date(2026, 9, 4))  # real Friday: archived
    today = date(2026, 9, 7)  # real Monday

    missing = find_missing_previous_archive(tmp_path, calendar, today)

    assert missing is None


def test_find_missing_previous_archive_is_silent_on_the_real_first_ever_day(tmp_path):
    """No real archive has ever been written yet -- nothing to compare
    against, so this must never be treated as a gap."""
    calendar = NseCalendar()
    today = date(2026, 9, 9)

    missing = find_missing_previous_archive(tmp_path, calendar, today)

    assert missing is None


def test_check_and_notify_missing_archive_fires_a_real_notification_on_a_real_gap(tmp_path, monkeypatch):
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr("data.instrument_archive.TelegramNotifier", _RecordingTelegram)
    _write_fake_archive(tmp_path, date(2026, 9, 7))  # Monday archived
    # Tuesday 2026-09-08 missing -- the real, simulated gap.
    settings = Settings()

    missing = check_and_notify_missing_archive(settings, archive_dir=tmp_path, today=date(2026, 9, 9))

    assert missing == date(2026, 9, 8)
    assert len(_RecordingDiscord.instances) == 1
    discord_calls = _RecordingDiscord.instances[0].calls
    assert discord_calls == [
        ("WARNING", "instrument archive missing for 2026-09-08, check the scheduled task", "system")
    ]
    telegram_calls = _RecordingTelegram.instances[0].calls
    assert telegram_calls == [
        ("WARNING", "instrument archive missing for 2026-09-08, check the scheduled task")
    ]


def test_check_and_notify_missing_archive_stays_silent_with_no_false_alarms(tmp_path, monkeypatch):
    """A real, unbroken archive history must never fire a notification --
    proves this safeguard won't spam every single day it runs."""
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr("data.instrument_archive.TelegramNotifier", _RecordingTelegram)
    _write_fake_archive(tmp_path, date(2026, 9, 8))  # Tuesday archived
    settings = Settings()

    missing = check_and_notify_missing_archive(settings, archive_dir=tmp_path, today=date(2026, 9, 9))

    assert missing is None
    assert _RecordingDiscord.instances == []
    assert _RecordingTelegram.instances == []


def test_check_and_notify_missing_archive_never_raises_if_notification_transport_fails(tmp_path, monkeypatch):
    class _ExplodingDiscord:
        def __init__(self, *args, **kwargs) -> None:
            raise ConnectionError("simulated real network failure")

    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _ExplodingDiscord)
    _write_fake_archive(tmp_path, date(2026, 9, 7))
    settings = Settings()

    missing = check_and_notify_missing_archive(settings, archive_dir=tmp_path, today=date(2026, 9, 9))  # must not raise

    assert missing == date(2026, 9, 8)


def test_run_daily_archive_still_checks_for_a_gap_even_with_no_kite_credentials(tmp_path, monkeypatch):
    """Part 3's own requirement: the safeguard must fire the same day it's
    noticed, as part of the *next scheduled run's own startup check* --
    even a run whose own archive attempt fails closed on missing
    credentials must still have already run the gap check."""
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr("data.instrument_archive.TelegramNotifier", _RecordingTelegram)
    archive_dir = tmp_path / "data" / "private" / "instrument_archives"
    _write_fake_archive(archive_dir, date(2026, 9, 7))  # Monday archived, Tuesday missing

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, 9, 0, tzinfo=tz)

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)
    settings = Settings(kite_api_key="", kite_access_token="")

    result = run_daily_archive(settings)

    assert result is None  # still fails closed on no credentials, as before
    # Both real notifications fire independently: the gap safeguard (a
    # prior day's silent gap) and the new per-attempt status message
    # (today's own real outcome) -- one Discord send each.
    assert len(_RecordingDiscord.instances) == 2
    assert _RecordingDiscord.instances[0].calls == [
        ("WARNING", "instrument archive missing for 2026-09-08, check the scheduled task", "system")
    ]
    assert _RecordingDiscord.instances[1].calls == [
        (
            "WARNING",
            "Instrument archive failed: no_kite_credentials_configured -- check the scheduled task",
            "system",
        )
    ]


# --- Real per-attempt status notification (success or failure) --------


def test_run_daily_archive_sends_a_real_success_status_notification(tmp_path, monkeypatch):
    """Requirement #1/#3: a real successful run must send the real date,
    real instrument count, and real timestamp -- not placeholders."""
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    rows = _valid_nifty_option_rows(3)

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                return rows

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 9, 0, tzinfo=tz)

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)

    result = run_daily_archive(settings)

    assert result is not None
    # No prior archive history exists yet in this fresh tmp_path, so the
    # gap safeguard stays silent (Part D's "first real day" rule) --
    # exactly one real Discord send, the status notification itself.
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "INFO"
    assert category == "system"
    assert "2026-09-08" in message  # the real archived date
    assert "3 real instruments" in message  # the real, exact count -- matches the 3 fake rows above
    assert "2026-09-08T09:00:00" in message  # the real timestamp


def test_run_daily_archive_sends_a_real_failure_status_notification_with_the_real_reason(tmp_path, monkeypatch):
    """Requirement #1/#3: a real failed run must send the real captured
    error reason, not a generic message."""
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    settings = Settings(kite_api_key="looks-real", kite_access_token="stale-token")

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                raise ConnectionError("simulated real TokenException: Incorrect api_key or access_token.")

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    result = run_daily_archive(settings)

    assert result is None
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert "ConnectionError" in message  # the real exception type, captured
    assert "simulated real TokenException: Incorrect api_key or access_token." in message  # the real reason
    assert message.endswith("-- check the scheduled task")


def test_run_daily_archive_sends_a_real_failure_status_notification_with_no_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    settings = Settings(kite_api_key="", kite_access_token="")

    result = run_daily_archive(settings)

    assert result is None
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert message == (
        "Instrument archive failed: no_kite_credentials_configured -- check the scheduled task"
    )


# --- Brief 18: real archive content validation -------------------------


def test_validate_archive_accepts_a_real_valid_archive(tmp_path):
    day = date(2026, 9, 8)  # a real Tuesday
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    _write_json(path, _valid_nifty_option_rows(10))

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert result.valid
    assert result.reason == ""
    assert result.nifty_option_count == 10
    assert result.total_record_count == 10


def test_validate_archive_catches_invalid_json_specifically(tmp_path):
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    path.write_text("{not valid json", encoding="utf-8")

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "invalid JSON" in result.reason


def test_validate_archive_catches_a_missing_required_field_specifically(tmp_path):
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    rows = _valid_nifty_option_rows(3)
    del rows[1]["lot_size"]  # a real, injected schema gap
    _write_json(path, rows)

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "missing required field" in result.reason
    assert "lot_size" in result.reason


def test_validate_archive_catches_a_segment_exchange_mismatch_specifically(tmp_path):
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    rows = _valid_nifty_option_rows(3)
    rows[0]["exchange"] = "BSE"  # a real, injected mixed-exchange record
    _write_json(path, rows)

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "segment mismatch" in result.reason
    assert "BSE" in result.reason


def test_validate_archive_catches_zero_real_nifty_options(tmp_path):
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    row = _valid_nifty_option_row(24000.0, "2026-09-24", "CE", 1)
    row["name"] = "BANKNIFTY"  # a real-shaped archive with zero NIFTY options
    _write_json(path, [row])

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "zero real NIFTY option records" in result.reason


def test_validate_archive_catches_a_real_sudden_drop_against_the_rolling_average(tmp_path):
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    _write_json(path, _valid_nifty_option_rows(10))  # a real, sudden drop vs. real recent history

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[1500, 1600, 1550])

    assert not result.valid
    assert "below 50% of the rolling average" in result.reason


def test_validate_archive_skips_the_rolling_average_check_with_only_one_prior_archive(tmp_path):
    """Part A #4: "once more than one exists" -- a single prior real
    data point is not a real rolling average yet, so it must not by
    itself fail an otherwise-real, valid archive."""
    day = date(2026, 9, 8)
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    _write_json(path, _valid_nifty_option_rows(10))

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[1500])

    assert result.valid


def test_validate_archive_catches_a_non_trading_day_specifically(tmp_path):
    day = date(2026, 9, 6)  # a real Sunday
    path = tmp_path / f"nfo_instruments_{day.isoformat()}.json"
    _write_json(path, _valid_nifty_option_rows(10))

    result = validate_archive(path, day, NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "not a real NSE trading day" in result.reason


def test_validate_archive_against_the_real_existing_archived_file():
    """Real, live evidence, not a synthetic fixture: the one real archived
    file this project has ever produced predates this brief's validation
    and is dated a real Saturday -- confirms the trading-day check
    correctly flags it against actual real data."""
    real_path = ARCHIVE_DIR / "nfo_instruments_2026-09-05.json"
    if not real_path.exists():
        pytest.skip("no real archived file present in this environment")

    result = validate_archive(real_path, date(2026, 9, 5), NseCalendar(), recent_validated_counts=[])

    assert not result.valid
    assert "not a real NSE trading day" in result.reason


def test_run_daily_archive_sends_the_normal_success_notification_for_a_real_valid_archive(tmp_path, monkeypatch):
    """Acceptance: a real, valid archive still gets Brief 17's existing
    success notification -- unaffected by this brief's new validation."""
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    rows = _valid_nifty_option_rows(20)

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                return rows

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 9, 0, tzinfo=tz)

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)

    result = run_daily_archive(settings)

    assert result is not None
    severity, message, category = _RecordingDiscord.instances[-1].calls[0]
    assert severity == "INFO"
    assert category == "system"
    assert "20 real instruments archived" in message
    archive_dir = tmp_path / "data" / "private" / "instrument_archives"
    assert is_date_validated(archive_dir, date(2026, 9, 8))


def test_run_daily_archive_sends_a_distinct_validation_failure_notification(tmp_path, monkeypatch):
    """Part B: a written-but-invalid file must send a distinct message
    naming the specific check that failed -- not the generic Brief 17
    failure message -- and the real file must not be deleted or
    silently replaced."""
    monkeypatch.chdir(tmp_path)
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.instrument_archive.DiscordNotifier", _RecordingDiscord)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    rows = _valid_nifty_option_rows(3)
    rows[0]["exchange"] = "BSE"  # a real, injected segment mismatch

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                return rows

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 9, 0, tzinfo=tz)

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)

    result = run_daily_archive(settings)

    assert result is None  # a written-but-invalid archive is not a trustworthy result
    archive_dir = tmp_path / "data" / "private" / "instrument_archives"
    written_path = archive_dir / "nfo_instruments_2026-09-08.json"
    assert written_path.exists()  # Part B: the real file is kept, never deleted
    saved = json.loads(written_path.read_text(encoding="utf-8"))
    assert saved == rows  # untouched -- kept exactly as written, for real inspection
    assert not is_date_validated(archive_dir, date(2026, 9, 8))
    severity, message, category = _RecordingDiscord.instances[-1].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert message.startswith("archive for 2026-09-08 written but failed validation: segment mismatch")


def test_run_daily_archive_never_silently_overwrites_an_already_validated_date(tmp_path, monkeypatch):
    """Part C: once a real archive for a date has passed validation, a
    second real write attempt for the same date must not touch it, even
    if the second attempt's real session would have returned different
    content."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    first_rows = _valid_nifty_option_rows(20)
    second_rows = _valid_nifty_option_rows(5)  # a real, different (smaller) payload if it were ever written
    call_count = {"n": 0}

    class _FakeKiteConnectModule:
        class KiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, token):
                pass

            def instruments(self, segment):
                call_count["n"] += 1
                return first_rows if call_count["n"] == 1 else second_rows

    import sys

    monkeypatch.setitem(sys.modules, "kiteconnect", _FakeKiteConnectModule)

    class _FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 8, 9, 0, tzinfo=tz)

    monkeypatch.setattr("data.instrument_archive.datetime", _FixedDate)

    first_result = run_daily_archive(settings)
    second_result = run_daily_archive(settings)  # a real second attempt, same real date

    assert first_result is not None
    assert second_result == first_result
    archive_dir = tmp_path / "data" / "private" / "instrument_archives"
    saved = json.loads((archive_dir / "nfo_instruments_2026-09-08.json").read_text(encoding="utf-8"))
    assert saved == first_rows  # untouched by the second attempt
    assert call_count["n"] == 1  # Kite was never called a second time for an already-validated date
