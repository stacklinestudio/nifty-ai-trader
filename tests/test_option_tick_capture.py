"""Brief 19 (Phase 4A-1): field discovery + minimal single-session raw
tick capture. Real, live field-discovery findings (documented in
V2_BUILD_REPORT.md) drive these tests directly: neither REST quote() nor
a real KiteTicker tick carries tradingsymbol/expiry/strike/option_type/
underlying price, so the capture path must never fabricate them, and the
bounded strike universe must be real, ATM-centered, and re-center on a
real, justified threshold -- never the full option chain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import ClassVar

from config import Settings
from data.option_tick_capture import (
    CAPTURED,
    DATA_UNAVAILABLE,
    RECONNECT_FAILED,
    ContractUniverse,
    build_universe,
    read_capture_gaps,
    run_capture_session,
    should_recenter,
)


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


# --- Part B: real, bounded, ATM-centered contract universe -------------


def _fake_instrument_row(strike: float, expiry: date, option_type: str, token: int) -> dict:
    return {
        "name": "NIFTY",
        "segment": "NFO-OPT",
        "exchange": "NFO",
        "expiry": expiry.isoformat(),
        "strike": strike,
        "instrument_type": option_type,
        "instrument_token": token,
        "tradingsymbol": f"NIFTY26SEP{int(strike)}{option_type}",
        "lot_size": 65,
    }


def _fake_chain(expiry: date, strikes: list[float]) -> list[dict]:
    rows = []
    token = 90000000
    for strike in strikes:
        rows.append(_fake_instrument_row(strike, expiry, "CE", token))
        token += 1
        rows.append(_fake_instrument_row(strike, expiry, "PE", token))
        token += 1
    return rows


def test_build_universe_selects_a_real_bounded_atm_centered_window():
    expiry = date(2026, 9, 8)
    strikes = [22000.0 + i * 50 for i in range(80)]  # a real-shaped wide chain
    instruments = _fake_chain(expiry, strikes)
    spot = 23900.0  # matches a real strike exactly

    universe = build_universe(instruments, spot, expiry, index_instrument_token=256265, strikes_either_side=10)

    assert universe.center_strike == 23900.0
    assert universe.strike_interval == 50.0
    assert len(universe.strikes) == 21  # 10 either side + ATM itself
    assert min(universe.strikes) == 23900.0 - 10 * 50
    assert max(universe.strikes) == 23900.0 + 10 * 50
    assert len(universe.tokens_by_strike_type) == 21 * 2  # CE + PE per strike
    assert universe.index_instrument_token == 256265
    assert len(universe.all_tokens()) == 21 * 2 + 1  # + the real index token


def test_build_universe_never_subscribes_to_the_full_real_chain():
    expiry = date(2026, 9, 8)
    strikes = [22000.0 + i * 50 for i in range(80)]  # 80 real strikes available
    instruments = _fake_chain(expiry, strikes)

    universe = build_universe(instruments, 23900.0, expiry, strikes_either_side=10)

    assert len(universe.strikes) < len(strikes)  # a real, bounded subset -- never exhaustive


def test_build_universe_bounds_cleanly_at_the_real_chain_edge():
    """A real spot near the edge of the available real chain must not
    error or wrap -- it simply gets fewer real strikes on that side."""
    expiry = date(2026, 9, 8)
    strikes = [22000.0 + i * 50 for i in range(15)]
    instruments = _fake_chain(expiry, strikes)

    universe = build_universe(instruments, 22000.0, expiry, strikes_either_side=10)

    assert universe.center_strike == 22000.0
    assert min(universe.strikes) == 22000.0  # can't go below the real chain's first strike
    # Only 10 strikes above ATM exist to take (indices 1..10 of the 15
    # available), not all 15 -- the window is still bounded to
    # strikes_either_side on the side that has room, never "everything
    # left over."
    assert max(universe.strikes) == strikes[10]
    assert len(universe.strikes) == 11


def test_build_universe_only_includes_the_given_real_expiry():
    near_expiry = date(2026, 9, 8)
    far_expiry = date(2026, 9, 15)
    near_strikes = [23800.0, 23850.0, 23900.0, 23950.0, 24000.0]
    instruments = _fake_chain(near_expiry, near_strikes) + _fake_chain(far_expiry, near_strikes)

    universe = build_universe(instruments, 23900.0, near_expiry, strikes_either_side=10)

    assert universe.expiry == near_expiry
    assert len(universe.tokens_by_strike_type) == len(near_strikes) * 2  # only the near expiry's CE+PE


def test_should_recenter_true_only_past_the_real_threshold():
    universe = ContractUniverse(
        expiry=date(2026, 9, 8),
        center_strike=23900.0,
        strike_interval=50.0,
        strikes=(23900.0,),
        tokens_by_strike_type={},
        index_instrument_token=256265,
    )
    # Real threshold: 5 strikes (RECENTER_THRESHOLD_STRIKES) x 50 = 250 points.
    assert not should_recenter(universe, new_spot_price=23900.0 + 240.0)
    assert not should_recenter(universe, new_spot_price=23900.0 - 240.0)
    assert should_recenter(universe, new_spot_price=23900.0 + 260.0)
    assert should_recenter(universe, new_spot_price=23900.0 - 260.0)


def test_should_recenter_detects_drift_even_past_the_tracked_windows_edge():
    """A large real spot move can carry the true ATM entirely outside the
    tracked window -- this must still be detected via real spot price,
    not silently clamped to the window's known strikes."""
    universe = ContractUniverse(
        expiry=date(2026, 9, 8),
        center_strike=23900.0,
        strike_interval=50.0,
        strikes=(23900.0, 23950.0, 24000.0),  # a deliberately tiny real window
        tokens_by_strike_type={},
        index_instrument_token=256265,
    )

    assert should_recenter(universe, new_spot_price=24500.0)  # far outside the tiny window


# --- Part C: minimal single-session capture, auth-lifecycle handling ---


def _minimal_universe() -> ContractUniverse:
    return ContractUniverse(
        expiry=date(2026, 9, 8),
        center_strike=23900.0,
        strike_interval=50.0,
        strikes=(23900.0,),
        tokens_by_strike_type={(23900.0, "CE"): 10914562},
        index_instrument_token=256265,
    )


def test_run_capture_session_reports_data_unavailable_with_no_credentials(tmp_path, monkeypatch):
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr("data.option_tick_capture.DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr("data.option_tick_capture.TelegramNotifier", _RecordingTelegram)
    settings = Settings(kite_api_key="", kite_access_token="")

    result = run_capture_session(settings, _minimal_universe(), duration_seconds=0, capture_dir=tmp_path)

    assert result.status == DATA_UNAVAILABLE
    assert result.path is None
    assert result.tick_count == 0
    assert result.reason == "no_kite_credentials_configured"
    # A real notification fired -- never a silent empty success.
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert "no_kite_credentials_configured" in message
    assert len(_RecordingTelegram.instances) == 1
    assert _RecordingTelegram.instances[0].calls[0][0] == "WARNING"


def test_run_capture_session_never_writes_a_file_when_credentials_are_missing(tmp_path):
    settings = Settings(kite_api_key="", kite_access_token="")

    run_capture_session(settings, _minimal_universe(), duration_seconds=0, capture_dir=tmp_path)

    assert list(tmp_path.glob("*.jsonl")) == []  # never a silent, empty, fabricated success file


class _FakeKiteTicker:
    """Synchronously simulates a real KiteTicker session: on connect(),
    fires on_connect then delivers the given canned real-shaped ticks --
    no real thread/sleep needed, so tests run instantly."""

    MODE_FULL = "full"

    def __init__(self, canned_ticks: list[dict]) -> None:
        self._canned_ticks = canned_ticks
        self.on_connect = None
        self.on_ticks = None
        self.on_close = None
        self.on_error = None
        self.subscribed: list[int] = []
        self.mode_calls: list[tuple] = []
        self.closed = False

    def subscribe(self, tokens: list[int]) -> None:
        self.subscribed = list(tokens)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        self.mode_calls.append((mode, list(tokens)))

    def connect(self, threaded: bool = True) -> None:
        if self.on_connect:
            self.on_connect(self, {"peer": "tcp4:real:443"})
        if self.on_ticks and self._canned_ticks:
            self.on_ticks(self, self._canned_ticks)

    def close(self) -> None:
        self.closed = True


def _real_shaped_option_tick(token: int = 10914562) -> dict:
    """Exactly the real fields confirmed live on 2026-09-06 (structure
    only, market closed) -- deliberately excludes tradingsymbol, expiry,
    strike, option_type, and underlying price, none of which a real
    KiteTicker FULL-mode option tick actually carries."""
    return {
        "tradable": True,
        "mode": "full",
        "instrument_token": token,
        "last_price": 119.6,
        "last_traded_quantity": 0,
        "average_traded_price": 0.0,
        "volume_traded": 0,
        "total_buy_quantity": 0,
        "total_sell_quantity": 0,
        "ohlc": {"open": 145.0, "high": 180.5, "low": 112.0, "close": 120.95},
        "change": -1.1161637040099284,
        "last_trade_time": "2026-09-04 15:39:59",
        "oi": 5456100,
        "oi_day_high": 0,
        "oi_day_low": 0,
        "exchange_timestamp": "2026-09-04 16:52:00",
        "depth": {
            "buy": [{"quantity": 0, "price": 0.0, "orders": 0}] * 5,
            "sell": [{"quantity": 0, "price": 0.0, "orders": 0}] * 5,
        },
    }


def test_run_capture_session_subscribes_the_real_universe_in_full_mode(tmp_path):
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    universe = _minimal_universe()
    fake = _FakeKiteTicker(canned_ticks=[_real_shaped_option_tick()])

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, kite_ticker_factory=lambda: fake
    )

    assert result.status == CAPTURED
    assert set(fake.subscribed) == set(universe.all_tokens())
    assert fake.mode_calls == [("full", fake.subscribed)]
    assert fake.closed  # Part C: the session is always closed at the end, no lingering connection


def test_run_capture_session_stores_the_real_raw_tick_without_fabricating_missing_fields(tmp_path):
    """The real, honest response to Part A's own finding: tradingsymbol,
    expiry, strike, option_type, and underlying price are genuinely
    absent from a real tick -- the stored record must reflect that
    truthfully, not paper over it with guessed/placeholder values."""
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    universe = _minimal_universe()
    real_tick = _real_shaped_option_tick()
    fake = _FakeKiteTicker(canned_ticks=[real_tick])

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, kite_ticker_factory=lambda: fake
    )

    assert result.status == CAPTURED
    assert result.tick_count == 1
    lines = result.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "received_at" in record  # the one real enrichment applied
    stored_tick = record["tick"]
    assert stored_tick == real_tick  # exactly as received -- raw, unmodified
    for absent_field in ("tradingsymbol", "expiry", "strike", "option_type"):
        assert absent_field not in stored_tick  # never fabricated


def test_run_capture_session_stops_cleanly_if_no_real_ticks_ever_arrive(tmp_path):
    """Part C #2: no reconnect resilience required -- a session with zero
    real ticks (e.g. a closed real market) must still complete cleanly,
    reporting a real, honest zero count, never an error."""
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    fake = _FakeKiteTicker(canned_ticks=[])

    result = run_capture_session(
        settings, _minimal_universe(), duration_seconds=0, capture_dir=tmp_path, kite_ticker_factory=lambda: fake
    )

    assert result.status == CAPTURED
    assert result.tick_count == 0
    assert result.path.exists()


# --- Standing rule (Brief 20): raw capture immutability, permanent -----


def test_a_second_real_capture_run_never_touches_the_first_runs_already_written_bytes(tmp_path):
    """The permanent regression test for the standing rule: the raw tick,
    exactly as Kite sent it, is never modified in place at any pipeline
    stage. Runs two real capture sessions against the same real capture
    file (the same real, honest scenario as a same-day re-run) and
    proves byte-for-byte, via a real hash, that the first run's content
    survives completely untouched as an exact prefix of the file after
    the second run -- not merely "still present somewhere," but at the
    same real byte offsets, unmodified. If this test ever fails, that is
    an immediate, hard stop before any 4A-2/4A-3/4A-4 work continues.
    """
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    today = date(2026, 9, 6)

    first_run = run_capture_session(
        settings,
        universe,
        duration_seconds=0,
        capture_dir=tmp_path,
        today=today,
        kite_ticker_factory=lambda: _FakeKiteTicker(canned_ticks=[_real_shaped_option_tick()]),
    )
    content_after_first_run = first_run.path.read_bytes()
    hash_after_first_run = hashlib.sha256(content_after_first_run).hexdigest()

    # A second real capture attempt for the SAME real date -- a
    # different (but still real-shaped) tick this time, exactly the kind
    # of "processing exists today" this rule must survive.
    different_tick = _real_shaped_option_tick()
    different_tick["last_price"] = 121.3
    second_run = run_capture_session(
        settings,
        universe,
        duration_seconds=0,
        capture_dir=tmp_path,
        today=today,
        kite_ticker_factory=lambda: _FakeKiteTicker(canned_ticks=[different_tick]),
    )

    assert second_run.path == first_run.path  # the same real file, same real date
    content_after_second_run = second_run.path.read_bytes()
    assert len(content_after_second_run) > len(content_after_first_run)  # real new bytes were appended

    real_prefix = content_after_second_run[: len(content_after_first_run)]
    assert real_prefix == content_after_first_run  # byte-for-byte, at the same real offsets
    assert hashlib.sha256(real_prefix).hexdigest() == hash_after_first_run  # the real, permanent hash check


# --- Phase 4A-2: reconnection, resilience, gap detection ---------------


class _ResilientFakeTicker:
    """Replays a real, scripted sequence of KiteTicker callback firings
    synchronously within connect() -- no real thread/sleep needed. Each
    script step is one of ("connect",), ("ticks", [tick, ...]),
    ("close", code, reason), ("error", code, reason),
    ("reconnect", attempt), or ("noreconnect",) -- the same real shapes
    KiteTicker itself calls its callbacks with. `after_step(i)`, if
    given, fires after step `i` -- used to snapshot real file state at a
    specific real moment in the sequence (e.g. right before a simulated
    disconnect), extending Brief 20's hash-comparison pattern to this
    scenario."""

    MODE_FULL = "full"

    def __init__(self, script, after_step=None) -> None:
        self.script = script
        self.after_step = after_step or (lambda i: None)
        self.on_connect = None
        self.on_ticks = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None
        self.on_noreconnect = None
        self.subscribed: list[int] = []
        self.mode_calls: list[tuple] = []
        self.closed = False

    def subscribe(self, tokens) -> None:
        self.subscribed = list(tokens)

    def set_mode(self, mode, tokens) -> None:
        self.mode_calls.append((mode, list(tokens)))

    def connect(self, threaded: bool = True) -> None:
        for i, step in enumerate(self.script):
            kind = step[0]
            if kind == "connect" and self.on_connect:
                self.on_connect(self, {})
            elif kind == "ticks" and self.on_ticks:
                self.on_ticks(self, step[1])
            elif kind == "close" and self.on_close:
                self.on_close(self, step[1], step[2])
            elif kind == "error" and self.on_error:
                self.on_error(self, step[1], step[2])
            elif kind == "reconnect" and self.on_reconnect:
                self.on_reconnect(self, step[1])
            elif kind == "noreconnect" and self.on_noreconnect:
                self.on_noreconnect(self)
            self.after_step(i)

    def close(self) -> None:
        self.closed = True


def test_disconnect_mid_session_starts_a_new_segment_leaving_the_first_byte_for_byte_unchanged(tmp_path):
    """The real test that matters most for this brief: extends Brief
    20's own hash-comparison pattern to a real reconnect specifically.
    Segment 1's real bytes, snapshotted the instant before the real
    disconnect, must be byte-for-byte identical to its final content
    after the whole session (including the reconnect) completes."""
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    tick_before = _real_shaped_option_tick()
    tick_before["exchange_timestamp"] = "2026-09-07 09:20:00"
    tick_after = _real_shaped_option_tick()
    tick_after["exchange_timestamp"] = "2026-09-07 09:20:47"
    tick_after["last_price"] = 130.0

    snapshot: dict = {}

    def after_step(i):
        if i == 1:  # right after tick_before is written, before the disconnect
            path = tmp_path / "nifty_option_ticks_2026-09-07.jsonl"
            snapshot["bytes"] = path.read_bytes()
            snapshot["hash"] = hashlib.sha256(snapshot["bytes"]).hexdigest()

    fake = _ResilientFakeTicker(
        [
            ("connect",),
            ("ticks", [tick_before]),
            ("close", 1006, "connection was closed uncleanly (going away)"),
            ("connect",),  # a real, successful reconnect
            ("ticks", [tick_after]),
        ],
        after_step=after_step,
    )

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert result.status == CAPTURED
    assert len(result.segments) == 2
    seg1, seg2 = result.segments
    assert seg1.name == "nifty_option_ticks_2026-09-07.jsonl"
    assert seg2.name == "nifty_option_ticks_2026-09-07_seg2.jsonl"

    final_seg1_bytes = seg1.read_bytes()
    assert final_seg1_bytes == snapshot["bytes"]  # byte-for-byte unchanged by the reconnect
    assert hashlib.sha256(final_seg1_bytes).hexdigest() == snapshot["hash"]

    seg2_lines = seg2.read_text(encoding="utf-8").strip().splitlines()
    assert len(seg2_lines) == 1
    assert json.loads(seg2_lines[0])["tick"] == tick_after  # the real post-reconnect tick, in the NEW segment


def test_real_gap_record_captures_the_real_before_after_timestamps_and_duration(tmp_path):
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    tick_before = _real_shaped_option_tick()
    tick_before["exchange_timestamp"] = "2026-09-07 09:20:00"
    tick_after = _real_shaped_option_tick()
    tick_after["exchange_timestamp"] = "2026-09-07 09:20:47"

    fake = _ResilientFakeTicker(
        [
            ("connect",),
            ("ticks", [tick_before]),
            ("close", 1006, "connection was closed uncleanly (going away)"),
            ("connect",),
            ("ticks", [tick_after]),
        ]
    )

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.gap_start == "2026-09-07T09:20:00+05:30"
    assert gap.gap_end == "2026-09-07T09:20:47+05:30"
    assert gap.duration_seconds == 47.0
    assert gap.segment_before == "nifty_option_ticks_2026-09-07.jsonl"
    assert gap.segment_after == "nifty_option_ticks_2026-09-07_seg2.jsonl"

    # The real, queryable record -- not just implied by file boundaries.
    stored_gaps = read_capture_gaps(tmp_path, date(2026, 9, 7))
    assert len(stored_gaps) == 1
    assert stored_gaps[0]["duration_seconds"] == 47.0


def test_reconnection_gives_up_after_bounded_retries_and_sends_a_real_alert(tmp_path, monkeypatch):
    """Reconnection must genuinely give up -- not loop forever -- and a
    real Discord/Telegram alert must fire on final failure. A huge
    duration_seconds proves this doesn't just happen to finish within
    the test's own patience: the give-up signal short-circuits the wait
    immediately, real evidence "do not silently hang" holds."""
    _RecordingDiscord.instances = []
    _RecordingTelegram.instances = []
    monkeypatch.setattr("data.option_tick_capture.DiscordNotifier", _RecordingDiscord)
    monkeypatch.setattr("data.option_tick_capture.TelegramNotifier", _RecordingTelegram)
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")

    fake = _ResilientFakeTicker(
        [
            ("connect",),
            ("ticks", [_real_shaped_option_tick()]),
            ("close", 1006, "connection was closed uncleanly (going away)"),
            ("error", 1006, "connection was closed uncleanly (going away)"),
            ("reconnect", 1),
            ("error", 1006, "connection was closed uncleanly (going away)"),
            ("reconnect", 2),
            ("noreconnect",),
        ]
    )

    result = run_capture_session(
        settings, universe, duration_seconds=999, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert result.status == RECONNECT_FAILED
    assert result.auth_failure_suspected is False
    assert len(_RecordingDiscord.instances) == 1
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "WARNING"
    assert category == "system"
    assert "reconnection failed" in message.lower()
    assert len(_RecordingTelegram.instances) == 1


def test_mid_session_auth_expiry_is_handled_via_the_same_fail_closed_path_distinct_alert(tmp_path, monkeypatch):
    """Part C: a real mid-session auth expiry -- using the exact real
    close-event signature confirmed live against a real, intentionally
    invalid access token (code 1006, "403 - Forbidden") -- is handled by
    the same fail-closed give-up path as a plain disconnect, but with a
    distinct, evidence-based CRITICAL alert."""
    _RecordingDiscord.instances = []
    monkeypatch.setattr("data.option_tick_capture.DiscordNotifier", _RecordingDiscord)
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    real_auth_failure_reason = (
        "connection was closed uncleanly (WebSocket connection upgrade failed (403 - Forbidden))"
    )

    fake = _ResilientFakeTicker(
        [
            ("connect",),
            ("ticks", [_real_shaped_option_tick()]),
            ("error", 1006, real_auth_failure_reason),
            ("close", 1006, real_auth_failure_reason),
            ("reconnect", 1),
            ("error", 1006, real_auth_failure_reason),
            ("close", 1006, real_auth_failure_reason),
            ("noreconnect",),
        ]
    )

    result = run_capture_session(
        settings, universe, duration_seconds=5, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert result.status == RECONNECT_FAILED
    assert result.auth_failure_suspected is True
    severity, message, category = _RecordingDiscord.instances[0].calls[0]
    assert severity == "CRITICAL"
    assert category == "system"
    assert "auth" in message.lower()


def test_out_of_order_tick_is_recorded_and_flagged_never_reordered_or_dropped(tmp_path):
    """Part B #3: a real out-of-order tick (later real arrival, earlier
    real exchange_timestamp) is recorded exactly as delivered and
    flagged -- never silently reordered, never dropped."""
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    tick1 = _real_shaped_option_tick()
    tick1["exchange_timestamp"] = "2026-09-07 09:20:10"
    tick2 = _real_shaped_option_tick()
    tick2["exchange_timestamp"] = "2026-09-07 09:20:05"  # earlier than tick1, arriving after it

    fake = _ResilientFakeTicker([("connect",), ("ticks", [tick1, tick2])])

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert result.out_of_order_count == 1
    lines = result.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # never dropped
    first_record = json.loads(lines[0])
    second_record = json.loads(lines[1])
    assert first_record["tick"] == tick1
    assert "out_of_order" not in first_record
    assert second_record["tick"] == tick2  # never reordered -- stored in the real order received
    assert second_record.get("out_of_order") is True


def test_a_normal_session_with_no_disconnect_still_produces_exactly_one_segment(tmp_path):
    universe = _minimal_universe()
    settings = Settings(kite_api_key="looks-real", kite_access_token="looks-real-too")
    fake = _ResilientFakeTicker([("connect",), ("ticks", [_real_shaped_option_tick()])])

    result = run_capture_session(
        settings, universe, duration_seconds=0, capture_dir=tmp_path, today=date(2026, 9, 7),
        kite_ticker_factory=lambda: fake,
    )

    assert result.status == CAPTURED
    assert len(result.segments) == 1
    assert result.gaps == ()
    assert result.out_of_order_count == 0
