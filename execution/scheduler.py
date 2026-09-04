"""Daily entrypoint control flow.

Recommended deployment: a fresh process each trading morning (cron/systemd),
not one process staying resident for days. A relaunched process is simpler
and more crash-tolerant -- restart recovery (Orchestrator.recover_open_positions)
already handles "a position was open when the process died," so there is no
correctness reason to keep one process alive across days, and every extra
day a process stays up is another day a slow memory leak or accumulated
state bug could affect a live position. run_trading_day below still supports
staying resident (call it in a loop) if there's a specific reason to.

Brief 6: run_trading_day now periodically re-scans for a NEW entry through
the day instead of evaluating exactly once near open -- polling, not
Zerodha's WebSocket streaming API, deliberately, reusing the exact same,
already-proven KiteMarketData.get_quote() path (timezone bug and all,
already fixed and tested) rather than introducing a new persistent-
connection/reconnect-logic surface while this feature is first being
proven out.

Brief 9 tightened the interval from 240s to 60s (Settings.entry_scan_
interval_seconds) to reduce lag between a real setup starting to form and
the system noticing it. Real math, not an assertion -- one full scan
cycle (execution/live_context.py::build_live_context) makes exactly 4
real Kite API calls: 1 quote (index spot, KiteMarketData.get_quote), 1
historical (index candles, KiteHistoricalData.candles), 1 instruments
(the NFO dump, download_kite_nifty_options), 1 more quote (the option-
chain batch, fetch_option_quotes) -- confirmed by reading each call site,
not estimated. At a 60s interval:
- Quote-category: 2 calls/cycle / 60s = 0.033 req/sec sustained average
  vs Kite's documented 1 req/sec ceiling -- ~30x headroom. The two quote
  calls within one cycle are separated by the historical and instruments
  calls in between (each a real network round-trip for a non-trivial
  payload -- 10 days of minute candles, a 33k+-row instruments dump), so
  in practice they land seconds apart, not back-to-back -- a reasoned
  expectation from the real call sequence, not a guaranteed one given
  real-world network timing variance.
- Historical-category: 1 call/cycle / 60s = 0.0167 req/sec vs the
  documented 3 req/sec ceiling -- ~180x headroom.
- No documented daily cap exists, but for scale: a full scanning day
  (9:15 open to the 15:00 entry_scan_cutoff_time, ~345 minutes, worst
  case if no trade ever fires and scanning runs the whole window) is
  ~345 cycles -- ~1,380 real Kite calls total across the day, a small
  absolute number for a retail API client. Actual daily volume is
  usually lower: a fill pauses entry scanning entirely until that
  position closes (its own supervision polling is a separate, unchanged
  cadence -- Settings.supervision_poll_seconds).
- The instruments dump (NFO, ~33k rows) being re-fetched and re-parsed
  every single cycle is real, non-trivial CPU/bandwidth work, unchanged
  in kind by this brief (it happened every cycle at 240s too) but now
  4x more frequent -- a real secondary cost, not a documented rate-limit
  concern, worth stating plainly rather than only checking the formal
  limits.

Deliberately not sub-minute: nothing meaningful changes on these setups'
timescale below 1 minute, and it would add real API/CPU load for no
benefit. A future pass could reconsider WebSocket if scan frequency ever
needed to drop meaningfully below this -- not needed at this cadence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as time_of_day
from typing import Any

from agents.orchestrator import CycleResult, Orchestrator
from data.calendar import NseCalendar
from execution.position_supervisor import TickResult
from monitoring.logger import configure_logger

logger = configure_logger(__name__)


@dataclass(frozen=True)
class ScanRound:
    """One entry-scan cycle: always has a CycleResult (even when it
    produced no candidate/order), and a TickResult only when this round's
    cycle actually filled and was supervised to close."""

    cycle: CycleResult
    supervision: TickResult | None = None


@dataclass(frozen=True)
class DayResult:
    ran: bool
    reason: str
    rounds: list[ScanRound] = field(default_factory=list)

    @property
    def cycle(self) -> CycleResult | None:
        """The most recent round's cycle -- a day can now have several
        (Brief 6); callers that only care about one (existing callers,
        single-scan-day tests) get the last one, matching the pre-Brief-6
        single-cycle-per-day behavior exactly when only one round ran."""
        return self.rounds[-1].cycle if self.rounds else None

    @property
    def supervision(self) -> TickResult | None:
        return self.rounds[-1].supervision if self.rounds else None


def resume_open_positions(
    orchestrator: Orchestrator,
    quote_source_factory: Callable[[str], Callable[[], float | None]],
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
    regime_source: Callable[[], tuple[str | None, str | None]] | None = None,
) -> list[TickResult]:
    """Recovers and resumes every position a prior process left open, before
    the caller considers any new entry for the day. Returns one TickResult
    per resumed position (a fresh crash-recovery pass on a later tick could
    surface more, but max_trades_per_day=1 makes more than one unlikely
    today).

    quote_source_factory takes the position's own instrument symbol
    (state.thesis.symbol -- the actual option contract held, e.g.
    "NIFTY2690124200CE") and returns a quote source for THAT instrument.
    A single fixed quote_source here would supervise every recovered
    position -- regardless of what it actually holds -- against whatever
    one symbol the caller happened to build a quote source for; this was a
    real bug (fixed on the index symbol "NIFTY") until this signature
    change.
    """
    results = []
    for state in orchestrator.recover_open_positions():
        quote_source = quote_source_factory(state.thesis.symbol)
        results.append(
            orchestrator.run_supervised(
                state, quote_source, clock=clock, sleeper=sleeper, regime_source=regime_source
            )
        )
    return results


def run_trading_day(
    orchestrator: Orchestrator,
    calendar: NseCalendar,
    context_provider: Callable[[], dict[str, Any]],
    quote_source_factory: Callable[[str], Callable[[], float | None]],
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
    poll_seconds_before_open: float = 30.0,
    regime_source: Callable[[], tuple[str | None, str | None]] | None = None,
    today: date | None = None,
    entry_scan_interval_seconds: float | None = None,
    entry_scan_cutoff_time: time_of_day | None = None,
    max_consecutive_scan_failures: int | None = None,
) -> DayResult:
    """Runs one trading day end to end: skip if not a trading day, wait for
    market open, then periodically re-scan for a new entry (Brief 6) --
    scan, and if a candidate fills, hand off to the existing run_supervised
    position-monitoring loop and pause scanning until it closes, then
    resume scanning with whatever daily-trade capacity remains. Repeats
    until DailyLimits.can_open() goes false or entry_scan_cutoff_time is
    reached. Does NOT check for a recovered position from a prior process
    -- call resume_open_positions first at process start, per Part A3,
    before this.

    entry_scan_interval_seconds/entry_scan_cutoff_time/
    max_consecutive_scan_failures default from orchestrator.settings when
    not given explicitly (same pattern run_supervised uses for
    poll_seconds/max_consecutive_failures) -- read only after the
    not-a-trading-day check below, so orchestrator=None still works for
    that early-exit path exactly as before.

    quote_source_factory: see resume_open_positions -- the same
    per-instrument requirement applies to a freshly-filled position here,
    not just a recovered one. The instrument isn't known until after
    open_position() builds the thesis, so the quote source for supervision
    is built from that thesis's own symbol, not decided up front.
    """
    checked_date = today if today is not None else clock().date()
    if not calendar.is_trading_day(checked_date):
        return DayResult(False, "not_a_trading_day")

    while not calendar.is_market_open(clock()):
        sleeper(poll_seconds_before_open)

    scan_interval = (
        entry_scan_interval_seconds
        if entry_scan_interval_seconds is not None
        else orchestrator.settings.entry_scan_interval_seconds
    )
    cutoff_time = (
        entry_scan_cutoff_time
        if entry_scan_cutoff_time is not None
        else orchestrator.settings.entry_scan_cutoff_time
    )
    max_failures = (
        max_consecutive_scan_failures
        if max_consecutive_scan_failures is not None
        else orchestrator.settings.max_consecutive_tick_failures
    )

    rounds: list[ScanRound] = []
    consecutive_failures = 0
    reason = "scan_cutoff_reached"

    while True:
        # Checked first, every iteration -- not just once at day start
        # (Brief 6 Part B.3): once the daily trade/loss cap or profit-lock
        # target is hit, stop scanning entirely for the rest of the day,
        # not just skip one iteration and try again next interval.
        if not orchestrator.limits.can_open():
            reason = "daily_limit_reached"
            break

        now = clock()
        if now.time() >= cutoff_time:
            reason = "scan_cutoff_reached"
            break

        try:
            cycle = orchestrator.run_cycle(context_provider())
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - outermost guard for the entry scan loop; one bad scan must not kill the whole day.
            consecutive_failures += 1
            logger.error(
                "entry_scan_failed attempt=%d/%d error=%s: %s",
                consecutive_failures,
                max_failures,
                type(exc).__name__,
                exc,
            )
            if consecutive_failures >= max_failures:
                logger.error(
                    "entry_scan_giving_up_for_today consecutive_failures=%d", consecutive_failures
                )
                reason = "scan_repeated_failure"
                break
            sleeper(scan_interval)
            continue

        if not cycle.order:
            rounds.append(ScanRound(cycle))
            sleeper(scan_interval)
            continue

        # Pass the same clock used for supervision so opened_at/last_quote_at
        # are consistent with the timestamps run_supervised will compare
        # against -- using two different time sources here would make the
        # staleness check meaningless.
        state = orchestrator.open_position(cycle, now=clock())
        quote_source = quote_source_factory(state.thesis.symbol)
        # Scanning is paused for the whole duration of this call -- it
        # blocks until the position closes (target/stop/forced-exit/data-
        # failure), including through and past entry_scan_cutoff_time,
        # which only ever gates STARTING a new scan, never an already-open
        # position's own supervision.
        supervision = orchestrator.run_supervised(
            state, quote_source, clock=clock, sleeper=sleeper, regime_source=regime_source
        )
        rounds.append(ScanRound(cycle, supervision))
        # Loop back to the top: can_open()/cutoff are re-checked before any
        # further scanning resumes.

    return DayResult(True, reason, rounds)
