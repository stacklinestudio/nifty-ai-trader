"""Brief 19 (Phase 4A-1): field discovery + minimal single-session raw
tick capture. Brief 22 (Phase 4A-2) added real reconnection, resilience,
and gap detection (below) -- integrity validation at tick level and
coverage reporting remain separate, future, sequenced pieces (4A-3/4A-4;
see V2_BUILD_REPORT.md for what each will need to address).

Phase 4A-2 real findings this module's resilience design depends on
(2026-09-06, see V2_BUILD_REPORT.md for full evidence):

- `KiteTicker` already has built-in auto-reconnect, enabled by default
  (confirmed from the installed library's own source, `kiteconnect/
  ticker.py`) -- real exponential backoff starting near 2s, capped at a
  real, configurable `reconnect_max_delay` (library default 60s), up to
  a real, configurable `reconnect_max_tries` (library default 50). This
  module reuses that real, already-proven mechanism rather than writing
  a new retry loop -- it only tightens `reconnect_max_tries` down to
  `settings.max_consecutive_tick_failures` (an existing, real config
  value, already used for an analogous bounded-retry purpose in
  `Orchestrator.run_supervised`) so a genuinely unrecoverable failure
  (e.g. an expired token) is not retried for the library's full default
  of up to ~50 attempts before this module gives up and alerts.
- Empirically confirmed live, using a real, intentionally invalid access
  token: a real Kite auth failure surfaces as repeated `on_error`/
  `on_close` calls with WebSocket close code 1006 and a reason
  containing "403 - Forbidden" -- the library retries this exactly like
  a network disconnect (it cannot tell the difference), so this module's
  own give-up notification inspects the real, last-seen close reason for
  that signature to report "likely an auth/session issue" honestly,
  without changing the actual reconnect/give-up mechanics either way.

Real, live field-discovery findings (2026-09-06, market closed;
structure confirmed, live tick-frequency/real-time behavior NOT
verified -- see V2_BUILD_REPORT.md) this module's design depends on:

- Neither REST `kite.quote()` nor a `KiteTicker` tick carries
  tradingsymbol, expiry, strike, option_type, or the underlying NIFTY
  price inline. `tradingsymbol` is only the REST response's dict key
  (absent even as a key from a WebSocket tick, which is keyed by
  `instrument_token` only). Recovering contract identity requires a
  separate join against a real instrument list (data/instrument_
  archive.py); recovering the underlying price requires a separate real
  subscription to the NIFTY 50 index instrument_token (256265),
  alongside the option tokens, in the same WebSocket session.
- `KiteTicker` `MODE_FULL` is required for real market depth/OI --
  `MODE_QUOTE`/`MODE_LTP` omit depth entirely (confirmed by reading the
  installed `kiteconnect` library's own real tick-parsing logic,
  `kiteconnect/ticker.py`, not merely assumed from documentation).
- Real bid/ask is 5-level market depth (`depth.buy`/`depth.sell`, each
  `{price, quantity, orders}`), not a single flat bid/ask pair --
  broader than this project's own pre-existing `data/option_chain.py::
  OptionQuote` model assumes (`bid: float | None`, `ask: float | None`).
  Not touched here: Brief 19 stores raw ticks, not `OptionQuote`s.

This module therefore never attempts to read tradingsymbol/expiry/
strike/option_type/underlying-price out of a tick record -- doing so
would either KeyError or silently return None, and either way would be
guessing at data that reconnaissance already proved isn't there.

STANDING RULE (locked in Brief 20, permanent, applies to every future
piece built on this module -- 4A-2/4A-3/4A-4 and beyond): the raw tick,
exactly as Kite sent it, is never modified in place at any pipeline
stage, ever. The real data flow is:

    RAW (this module's own output -- exactly as Kite sent it, untouched,
         forever; new real sessions only ever APPEND new records, never
         edit or overwrite an already-written one)
      -> NORMALIZED (field renaming, e.g. volume_traded -> volume; joins
         against the instrument archive for contract identity -- a new,
         separate representation, never an edit of the raw record)
      -> VALIDATED (Brief 18-style integrity checks, applied to the
         normalized layer, never silently rewriting the raw layer)
      -> RESEARCH (whatever downstream analysis needs, built entirely
         from the validated layer)

Never: read a raw tick file, modify its content, save the modified
version back over it. A future reconnect/backfill (4A-2) must write a
NEW record/segment for the reconnected period, never retroactively edit
an already-written raw record. A future validator (4A-3) must keep its
findings/corrections in a separate layer that references raw records by
(timestamp, instrument_token), never overwrite them. This is why
`run_capture_session` below opens its output file in append ("a") mode
only, never "w" or "r+" -- verified live and covered by a permanent
regression test, `test_a_second_real_capture_run_never_touches_the_
first_runs_already_written_bytes` in tests/test_option_tick_capture.py.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

from config import IST, Settings
from data.websocket import WebsocketHealth
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

CAPTURE_DIR = Path("data/private/option_tick_capture")

# Real close-event signature confirmed live (2026-09-06) against a real,
# intentionally invalid access token -- Kite's own auth rejection at the
# WebSocket-upgrade layer, not a documented API code, just what the
# server actually sends.
AUTH_FAILURE_SIGNATURE = "403"

# The real, live NIFTY 50 index instrument_token (confirmed live via
# kite.quote(["NSE:NIFTY 50"]) on 2026-09-06) -- stable across sessions,
# unlike option tokens which change every expiry.
NIFTY_INDEX_INSTRUMENT_TOKEN = 256265

# Part B #1: a real, bounded, ATM-centered strike window. 10 strikes
# either side of ATM (21 strikes total x2 for CE/PE = 42 real option
# contracts + 1 real index token = 43 total real WebSocket subscriptions)
# -- confirmed live today against the real, current nearest weekly NIFTY
# expiry: strike spacing is a uniform real 50 points across all 87 real
# strikes offered, so 10 strikes either side covers a real +/-500 point
# (~2.1% of a ~23,900 real spot) band, where NIFTY weekly option
# liquidity concentrates, without subscribing to the full real chain.
STRIKES_EITHER_SIDE = 10

# Part B #2: re-center once the real new ATM strike has drifted more
# than half the tracked window's radius from the window's current
# center. Gives the window real hysteresis (does not re-center on every
# single 50-point tick) while guaranteeing at least half the window's
# real strikes remain valid buffer on both sides of the new ATM before a
# re-center is needed.
RECENTER_THRESHOLD_STRIKES = STRIKES_EITHER_SIDE // 2

DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
CAPTURED = "CAPTURED"
RECONNECT_FAILED = "RECONNECT_FAILED"


@dataclass(frozen=True)
class ContractUniverse:
    expiry: date
    center_strike: float
    strike_interval: float
    strikes: tuple[float, ...]
    tokens_by_strike_type: dict[tuple[float, str], int]
    index_instrument_token: int

    def all_tokens(self) -> list[int]:
        return [self.index_instrument_token, *self.tokens_by_strike_type.values()]


def _row_expiry(row: dict) -> date:
    value = row["expiry"]
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _real_strike_interval(strikes: list[float]) -> float:
    """The real, uniform spacing between consecutive real strikes --
    never assumed to be a fixed number. Confirmed live today it is 50
    for NIFTY's nearest real weekly expiry, but this reads it from
    whatever real strikes are actually present rather than hardcoding
    that."""
    ordered = sorted(strikes)
    diffs = {round(b - a, 2) for a, b in pairwise(ordered)}
    if len(diffs) != 1:
        raise ValueError(f"real strikes are not uniformly spaced: found spacings {sorted(diffs)}")
    return next(iter(diffs))


def build_universe(
    instruments: list[dict],
    spot_price: float,
    expiry: date,
    index_instrument_token: int = NIFTY_INDEX_INSTRUMENT_TOKEN,
    strikes_either_side: int = STRIKES_EITHER_SIDE,
) -> ContractUniverse:
    """Part B: a real, bounded, ATM-centered universe -- `strikes_either_
    side` real strikes on each side of the real ATM strike, for the
    given real `expiry` only. Never the full real chain. `instruments`
    is a real (or real-shaped) raw NFO instrument list, e.g. from
    data/instrument_archive.py's own real archived dump."""
    matching = [
        row
        for row in instruments
        if row.get("name") == "NIFTY" and row.get("segment") == "NFO-OPT" and _row_expiry(row) == expiry
    ]
    if not matching:
        raise ValueError(f"no real NIFTY NFO-OPT instruments found for expiry {expiry}")

    available_strikes = sorted({float(row["strike"]) for row in matching})
    interval = _real_strike_interval(available_strikes)
    atm_strike = min(available_strikes, key=lambda k: abs(k - spot_price))
    atm_index = available_strikes.index(atm_strike)
    window = available_strikes[max(0, atm_index - strikes_either_side) : atm_index + strikes_either_side + 1]
    window_set = set(window)

    tokens = {
        (float(row["strike"]), row["instrument_type"]): int(row["instrument_token"])
        for row in matching
        if float(row["strike"]) in window_set
    }

    return ContractUniverse(
        expiry=expiry,
        center_strike=atm_strike,
        strike_interval=interval,
        strikes=tuple(window),
        tokens_by_strike_type=tokens,
        index_instrument_token=index_instrument_token,
    )


def should_recenter(
    universe: ContractUniverse, new_spot_price: float, threshold_strikes: int = RECENTER_THRESHOLD_STRIKES
) -> bool:
    """Part B #2: true once `new_spot_price` has drifted more than
    `threshold_strikes` real strikes away from the universe's current
    center strike. Uses real spot price directly (not a strike lookup
    restricted to the current window) so a move large enough to carry
    the true ATM entirely outside the tracked window is still detected
    correctly, rather than silently clamped to the window's edge."""
    threshold_points = threshold_strikes * universe.strike_interval
    return abs(new_spot_price - universe.center_strike) >= threshold_points


@dataclass(frozen=True)
class GapRecord:
    """Part B #2: an honest, explicit record of a real gap -- never
    implied only by a file boundary, and never filled/interpolated."""

    gap_start: str  # real ISO timestamp of the last tick before disconnect
    gap_end: str  # real ISO timestamp of the first tick after reconnect
    duration_seconds: float
    segment_before: str
    segment_after: str


def _gap_manifest_path(capture_dir: Path, day: date) -> Path:
    return capture_dir / f"capture_gaps_{day.isoformat()}.json"


def _record_gap(capture_dir: Path, day: date, gap: GapRecord) -> None:
    path = _gap_manifest_path(capture_dir, day)
    existing = read_capture_gaps(capture_dir, day)
    existing.append(
        {
            "gap_start": gap.gap_start,
            "gap_end": gap.gap_end,
            "duration_seconds": gap.duration_seconds,
            "segment_before": gap.segment_before,
            "segment_after": gap.segment_after,
        }
    )
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def read_capture_gaps(capture_dir: Path, day: date) -> list[dict]:
    """The real, queryable gap record for a given real day -- never just
    implied by which segment files happen to exist."""
    path = _gap_manifest_path(capture_dir, day)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("option_tick_capture_gap_manifest_corrupted path=%s", path)
        return []


def _parse_tick_timestamp(tick: dict) -> datetime | None:
    """The real per-tick Kite timestamp (`exchange_timestamp`), parsed
    for real duration math and real ordering checks. Real Kite format
    confirmed live: "YYYY-MM-DD HH:MM:SS", no sub-second precision, IST
    implied (never a UTC assumption -- this project never guesses a
    broker timestamp's timezone). Returns None, never a guess, when the
    real tick lacks this field (e.g. a malformed/unexpected record)."""
    value = tick.get("exchange_timestamp")
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


@dataclass(frozen=True)
class CaptureSessionResult:
    status: str  # CAPTURED, DATA_UNAVAILABLE, or RECONNECT_FAILED
    path: Path | None  # the first/primary real segment, for backward compatibility
    tick_count: int  # real ticks across every real segment this session wrote
    reason: str
    segments: tuple[Path, ...] = ()
    gaps: tuple[GapRecord, ...] = ()
    auth_failure_suspected: bool = False
    out_of_order_count: int = 0


def _notify_capture_unavailable(settings: Settings, reason: str) -> None:
    """Auth-lifecycle handling, matching the pattern already built for
    instrument archiving (data/instrument_archive.py) -- reuses the same
    Discord "system" channel / Telegram wiring verbatim."""
    message = f"Option tick capture unavailable: {reason} -- check Kite session/credentials"
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url,
            webhooks_by_category=webhooks_by_category_from_settings(settings),
        )
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord.send_message("WARNING", message, "system")
        telegram.send_message("WARNING", message)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break the capture session.
        logger.warning("option_tick_capture_notify_failed error=%s: %s", type(exc).__name__, exc)


def _notify_capture_failure(settings: Settings, reason: str, auth_failure_suspected: bool) -> None:
    """Part A #3: fail-closed notification once bounded reconnection
    genuinely gives up -- reuses the same Discord "system" channel /
    Telegram wiring verbatim. `auth_failure_suspected` is a real,
    evidence-based diagnostic (the real last-seen close reason matched
    Kite's real auth-rejection signature, AUTH_FAILURE_SIGNATURE) --
    never changes the reconnect/give-up mechanics, only the honesty of
    what the alert says happened."""
    severity = "CRITICAL" if auth_failure_suspected else "WARNING"
    prefix = (
        "Option tick capture stopped -- likely an auth/session issue (token may need refreshing)"
        if auth_failure_suspected
        else "Option tick capture stopped after reconnection failed"
    )
    message = f"{prefix}: {reason}"
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url,
            webhooks_by_category=webhooks_by_category_from_settings(settings),
        )
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord.send_message(severity, message, "system")
        telegram.send_message(severity, message)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break the capture session.
        logger.warning("option_tick_capture_notify_failed error=%s: %s", type(exc).__name__, exc)


class _CaptureState:
    """Real, mutable, per-session state shared across the WebSocket
    callbacks below -- kept in one place instead of a pile of
    `nonlocal` declarations, since Phase 4A-2 needs several: the
    current real segment file, `WebsocketHealth` (Part A #1, reused
    unmodified), the pending real gap being assembled across a
    disconnect/reconnect, and the real give-up signal."""

    def __init__(self, capture_dir: Path, day: date, universe: ContractUniverse) -> None:
        self.capture_dir = capture_dir
        self.day = day
        self.universe = universe
        self.health = WebsocketHealth()
        self.tick_count = 0
        self.out_of_order_count = 0
        self.last_timestamp_by_token: dict[int, datetime] = {}
        self.gaps: list[GapRecord] = []
        self.last_close_reason = ""
        self.give_up = threading.Event()
        self.give_up_reason = ""
        self.auth_failure_suspected = False
        self._awaiting_reconnect_tick = False
        self._pending_gap_start: datetime | None = None
        self._pending_gap_segment_before = ""
        self.path = capture_dir / f"nifty_option_ticks_{day.isoformat()}.jsonl"
        self.handle = self.path.open("a", encoding="utf-8")
        self.segments: list[Path] = [self.path]

    def start_new_segment(self) -> Path:
        """Part B #1: a real, distinct new segment file on reconnect --
        never appended into or overwritten, the pre-disconnect segment's
        handle is closed (not touched again) and a brand new file is
        opened for the next real segment number."""
        self.handle.close()
        segment_number = len(self.segments) + 1
        new_path = self.capture_dir / f"nifty_option_ticks_{self.day.isoformat()}_seg{segment_number}.jsonl"
        self.handle = new_path.open("a", encoding="utf-8")
        self.segments.append(new_path)
        return new_path

    def on_real_disconnect(self) -> None:
        self._pending_gap_start = self.health.last_tick_at
        self._pending_gap_segment_before = self.segments[-1].name
        self.health.on_disconnect()

    def on_real_reconnect_established(self) -> None:
        """Called from `on_connect` when it fires again after a real
        disconnect (never on the session's first, initial connect)."""
        new_path = self.start_new_segment()
        self._awaiting_reconnect_tick = True
        logger.warning("option_tick_capture_reconnected new_segment=%s", new_path)

    def write_tick(self, tick: dict, received_at: datetime) -> None:
        token = tick.get("instrument_token")
        timestamp = _parse_tick_timestamp(tick)
        out_of_order = False
        if (
            timestamp is not None
            and token in self.last_timestamp_by_token
            and timestamp < self.last_timestamp_by_token[token]
        ):
            out_of_order = True
            self.out_of_order_count += 1
            logger.warning(
                "option_tick_capture_out_of_order token=%s previous=%s current=%s",
                token,
                self.last_timestamp_by_token[token].isoformat(),
                timestamp.isoformat(),
            )
        if timestamp is not None:
            self.last_timestamp_by_token[token] = timestamp
            self.health.on_tick(timestamp)
        record: dict = {"received_at": received_at.isoformat(), "tick": tick}
        if out_of_order:
            # Part B #3: recorded and flagged, never silently reordered
            # or dropped -- the raw `tick` value itself is untouched,
            # exactly as Brief 20's immutability rule requires; only
            # this envelope-level metadata (alongside the pre-existing
            # `received_at`) carries the real finding.
            record["out_of_order"] = True
        self.handle.write(json.dumps(record, default=str) + "\n")
        self.handle.flush()
        self.tick_count += 1
        if self._awaiting_reconnect_tick:
            self._finalize_pending_gap(timestamp or received_at)

    def _finalize_pending_gap(self, gap_end: datetime) -> None:
        gap_start = self._pending_gap_start
        gap = GapRecord(
            gap_start=gap_start.isoformat() if gap_start else "",
            gap_end=gap_end.isoformat(),
            duration_seconds=(gap_end - gap_start).total_seconds() if gap_start else 0.0,
            segment_before=self._pending_gap_segment_before,
            segment_after=self.segments[-1].name,
        )
        self.gaps.append(gap)
        _record_gap(self.capture_dir, self.day, gap)
        self._awaiting_reconnect_tick = False
        self._pending_gap_start = None

    def note_close_reason(self, reason: object) -> None:
        self.last_close_reason = str(reason)

    def give_up_now(self) -> None:
        reason = self.last_close_reason or "unknown"
        self.auth_failure_suspected = AUTH_FAILURE_SIGNATURE in reason
        self.give_up_reason = reason
        logger.error(
            "option_tick_capture_reconnect_exhausted reason=%s auth_failure_suspected=%s",
            reason,
            self.auth_failure_suspected,
        )
        self.give_up.set()

    def close(self) -> None:
        try:
            self.handle.close()
        except OSError:
            pass


def run_capture_session(
    settings: Settings,
    universe: ContractUniverse,
    duration_seconds: float,
    capture_dir: Path = CAPTURE_DIR,
    today: date | None = None,
    kite_ticker_factory: Callable[[], object] | None = None,
) -> CaptureSessionResult:
    """Part C (Phase 4A-1) + Phase 4A-2 resilience: real tick capture
    with real reconnection, a real new segment per reconnect (Brief 20's
    immutability rule: never edits/appends into an already-written
    segment), real gap recording, and a real fail-closed give-up path
    (bounded retries exhausted, or a real mid-session auth expiry --
    both surfaced the same way, see the module docstring) with a real
    Discord/Telegram alert. Every stored record is the real tick exactly
    as `KiteTicker` delivered it, plus real envelope metadata
    (`received_at`, and `out_of_order` when real ordering is violated)
    -- the raw `tick` value itself is never modified.
    """
    if not (settings.kite_api_key and settings.kite_access_token):
        reason = "no_kite_credentials_configured"
        logger.warning("option_tick_capture_unavailable reason=%s", reason)
        _notify_capture_unavailable(settings, reason)
        return CaptureSessionResult(DATA_UNAVAILABLE, None, 0, reason)

    today = today or datetime.now(IST).date()
    capture_dir.mkdir(parents=True, exist_ok=True)

    if kite_ticker_factory is None:
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            reason = "kiteconnect_not_installed"
            logger.warning("option_tick_capture_unavailable reason=%s", reason)
            _notify_capture_unavailable(settings, reason)
            return CaptureSessionResult(DATA_UNAVAILABLE, None, 0, reason)
        # Phase 4A-2 Part A #2: reuses KiteTicker's own real, already-
        # proven auto-reconnect (exponential backoff up to a real
        # library-default 60s max delay) -- only reconnect_max_tries is
        # tightened, from the library's own default of 50 down to this
        # project's existing settings.max_consecutive_tick_failures
        # (already used for an analogous bounded-retry purpose in
        # Orchestrator.run_supervised), so a genuinely unrecoverable
        # failure gives up in a bounded, real amount of time.
        max_tries = settings.max_consecutive_tick_failures
        kite_ticker_factory = lambda: KiteTicker(
            settings.kite_api_key, settings.kite_access_token, reconnect=True, reconnect_max_tries=max_tries
        )

    kws = kite_ticker_factory()
    state = _CaptureState(capture_dir, today, universe)

    def on_connect(ws, response):
        # A reconnect, not the session's initial connect, iff a real
        # disconnect (state.health.on_disconnect(), via on_close below)
        # already happened at least once before this real connect fired.
        was_disconnected = state.health.reconnects > 0 and not state.health.connected
        state.health.on_connect()
        if was_disconnected:
            state.on_real_reconnect_established()
        tokens = state.universe.all_tokens()
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_ticks(ws, ticks):
        received_at = datetime.now(IST)
        for tick in ticks:
            state.write_tick(tick, received_at)

    def on_close(ws, code, reason):
        logger.warning("option_tick_capture_socket_closed code=%s reason=%s", code, reason)
        state.note_close_reason(reason)
        if state.health.connected:
            state.on_real_disconnect()

    def on_error(ws, code, reason):
        logger.warning("option_tick_capture_socket_error code=%s reason=%s", code, reason)
        state.note_close_reason(reason)

    def on_reconnect(ws, attempts_count):
        logger.warning("option_tick_capture_reconnect_attempt attempt=%d", attempts_count)

    def on_noreconnect(ws):
        state.give_up_now()

    kws.on_connect = on_connect
    kws.on_ticks = on_ticks
    kws.on_close = on_close
    kws.on_error = on_error
    kws.on_reconnect = on_reconnect
    kws.on_noreconnect = on_noreconnect
    kws.connect(threaded=True)
    gave_up = state.give_up.wait(timeout=duration_seconds)
    kws.close()
    state.close()

    if gave_up:
        reason = f"reconnection exhausted -- real last close/error reason: {state.give_up_reason}"
        logger.error("option_tick_capture_gave_up reason=%s", reason)
        _notify_capture_failure(settings, reason, state.auth_failure_suspected)
        return CaptureSessionResult(
            RECONNECT_FAILED,
            state.segments[0],
            state.tick_count,
            reason,
            tuple(state.segments),
            tuple(state.gaps),
            state.auth_failure_suspected,
            state.out_of_order_count,
        )

    logger.info(
        "option_tick_capture_complete segments=%s tick_count=%d gaps=%d out_of_order=%d",
        [p.name for p in state.segments],
        state.tick_count,
        len(state.gaps),
        state.out_of_order_count,
    )
    return CaptureSessionResult(
        CAPTURED,
        state.segments[0],
        state.tick_count,
        "",
        tuple(state.segments),
        tuple(state.gaps),
        False,
        state.out_of_order_count,
    )
