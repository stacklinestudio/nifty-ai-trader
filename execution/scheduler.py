"""Daily entrypoint control flow.

Recommended deployment: a fresh process each trading morning (cron/systemd),
not one process staying resident for days. A relaunched process is simpler
and more crash-tolerant -- restart recovery (Orchestrator.recover_open_positions)
already handles "a position was open when the process died," so there is no
correctness reason to keep one process alive across days, and every extra
day a process stays up is another day a slow memory leak or accumulated
state bug could affect a live position. run_trading_day below still supports
staying resident (call it in a loop) if there's a specific reason to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from agents.orchestrator import CycleResult, Orchestrator
from data.calendar import NseCalendar
from execution.position_supervisor import TickResult


@dataclass(frozen=True)
class DayResult:
    ran: bool
    reason: str
    cycle: CycleResult | None = None
    supervision: TickResult | None = None


def resume_open_positions(
    orchestrator: Orchestrator,
    quote_source: Callable[[], float | None],
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
    regime_source: Callable[[], tuple[str | None, str | None]] | None = None,
) -> list[TickResult]:
    """Recovers and resumes every position a prior process left open, before
    the caller considers any new entry for the day. Returns one TickResult
    per resumed position (a fresh crash-recovery pass on a later tick could
    surface more, but max_trades_per_day=1 makes more than one unlikely
    today)."""
    results = []
    for state in orchestrator.recover_open_positions():
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
    quote_source: Callable[[], float | None],
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
    poll_seconds_before_open: float = 30.0,
    regime_source: Callable[[], tuple[str | None, str | None]] | None = None,
    today: date | None = None,
) -> DayResult:
    """Runs one trading day end to end: skip if not a trading day, wait for
    market open, run one entry cycle, and supervise any resulting fill
    through to its own close. Does NOT check for a recovered position from a
    prior process -- call resume_open_positions first at process start, per
    Part A3, before this.
    """
    checked_date = today if today is not None else clock().date()
    if not calendar.is_trading_day(checked_date):
        return DayResult(False, "not_a_trading_day")

    while not calendar.is_market_open(clock()):
        sleeper(poll_seconds_before_open)

    cycle = orchestrator.run_cycle(context_provider())
    if not cycle.order:
        return DayResult(True, "no_entry", cycle)

    # Pass the same clock used for supervision so opened_at/last_quote_at
    # are consistent with the timestamps run_supervised will compare
    # against -- using two different time sources here would make the
    # staleness check meaningless.
    state = orchestrator.open_position(cycle, now=clock())
    supervision = orchestrator.run_supervised(
        state, quote_source, clock=clock, sleeper=sleeper, regime_source=regime_source
    )
    return DayResult(True, "closed", cycle, supervision)
