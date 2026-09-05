"""Brief 19 (Phase 4A-1): field discovery + minimal single-session raw
tick capture. First of several sequenced pieces toward a real option-
price archive -- deliberately does NOT build reconnect resilience, gap
detection, or integrity validation (those are Phase 4A-2/4A-3/4A-4,
separate, future, sequenced pieces; see V2_BUILD_REPORT.md's Brief 19
section for what each will need to address).

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
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

from config import IST, Settings
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

CAPTURE_DIR = Path("data/private/option_tick_capture")

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
class CaptureSessionResult:
    status: str  # CAPTURED or DATA_UNAVAILABLE
    path: Path | None
    tick_count: int
    reason: str


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


def run_capture_session(
    settings: Settings,
    universe: ContractUniverse,
    duration_seconds: float,
    capture_dir: Path = CAPTURE_DIR,
    today: date | None = None,
    kite_ticker_factory: Callable[[], object] | None = None,
) -> CaptureSessionResult:
    """Part C: minimal, single-session RAW tick capture. Deliberately no
    reconnect resilience and no gap detection (Phase 4A-2, separate,
    future, and only scoped after this brief's real findings are seen)
    -- if the real WebSocket session drops mid-capture, this simply
    stops capturing for the rest of the real session; it does not
    attempt to reconnect. Every stored record is the real tick exactly
    as `KiteTicker` delivered it, plus a real `received_at` timestamp --
    no field is ever added, renamed, or fabricated, matching Part A's
    real finding that several assumed fields (tradingsymbol, expiry,
    strike, option_type, underlying price) simply are not present.
    """
    if not (settings.kite_api_key and settings.kite_access_token):
        reason = "no_kite_credentials_configured"
        logger.warning("option_tick_capture_unavailable reason=%s", reason)
        _notify_capture_unavailable(settings, reason)
        return CaptureSessionResult(DATA_UNAVAILABLE, None, 0, reason)

    today = today or datetime.now(IST).date()
    capture_dir.mkdir(parents=True, exist_ok=True)
    path = capture_dir / f"nifty_option_ticks_{today.isoformat()}.jsonl"

    if kite_ticker_factory is None:
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            reason = "kiteconnect_not_installed"
            logger.warning("option_tick_capture_unavailable reason=%s", reason)
            _notify_capture_unavailable(settings, reason)
            return CaptureSessionResult(DATA_UNAVAILABLE, None, 0, reason)
        kite_ticker_factory = lambda: KiteTicker(settings.kite_api_key, settings.kite_access_token)

    kws = kite_ticker_factory()
    tick_count = 0

    with path.open("a", encoding="utf-8") as handle:

        def on_connect(ws, response):
            tokens = universe.all_tokens()
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

        def on_ticks(ws, ticks):
            nonlocal tick_count
            received_at = datetime.now(IST).isoformat()
            for tick in ticks:
                handle.write(json.dumps({"received_at": received_at, "tick": tick}, default=str) + "\n")
                tick_count += 1
            handle.flush()

        def on_close(ws, code, reason):
            logger.warning("option_tick_capture_socket_closed code=%s reason=%s", code, reason)

        def on_error(ws, code, reason):
            logger.warning("option_tick_capture_socket_error code=%s reason=%s", code, reason)

        kws.on_connect = on_connect
        kws.on_ticks = on_ticks
        kws.on_close = on_close
        kws.on_error = on_error
        kws.connect(threaded=True)
        time.sleep(duration_seconds)
        kws.close()

    logger.info("option_tick_capture_complete path=%s tick_count=%d", path, tick_count)
    return CaptureSessionResult(CAPTURED, path, tick_count, "")
