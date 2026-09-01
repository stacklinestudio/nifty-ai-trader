"""Runs the exact same once-per-trading-day entry pipeline main.py's
run_scheduled_day uses live -- the same Orchestrator.run_cycle, the same
execution.live_context.assemble_context, the same signal/risk/validation
pipeline, the same Settings.signal_threshold -- over a historical window
of real Kite data, instead of one live day.

Not a faster or different cadence, and not a lowered signal_threshold: if
this finds few or zero trades, that's the same honest reason the live
system does (execution/live_context.py::KNOWN_GAPS -- SignalEngine gets
real data for only 2 of 7 inputs), not a backtest-specific difference. A
caller wanting to see what *would* fire without that gap should construct
a second Orchestrator with a deliberately different Settings.signal_threshold
and run this same function again -- never silently inside here.

No look-ahead: for each trading day, only candles strictly before that
day, plus that day's own first few bars (modeling a process that starts
at market open and needs a few minutes to see enough of today's session
to act -- the same minimum execution/live_context.py itself requires) are
ever visible. Nothing from later in the day or any future day reaches the
decision.

Runs against its own Orchestrator/database, entirely separate from the
live daily scheduler main.py run drives -- no change to that behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from agents.orchestrator import CycleResult, Orchestrator
from config import Settings
from data.option_chain import OptionQuote
from execution.live_context import assemble_context
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

# Matches execution/live_context.py's own OPENING_RANGE_MINUTES -- the
# minimum bars _add_candidate needs before it will ever look at a day.
OPENING_RANGE_MINUTES = 5
DECISION_CUTOFF_BARS = OPENING_RANGE_MINUTES + 1


@dataclass(frozen=True)
class BacktestDay:
    trading_day: date
    reason: str  # insufficient_prior_history | insufficient_today_bars | no_candidate | candidate_no_option | traded
    cycle: CycleResult | None = None
    candidate_formed: bool = False


@dataclass(frozen=True)
class DailyBacktestReport:
    days: list[BacktestDay] = field(default_factory=list)

    @property
    def candidates_formed(self) -> int:
        return sum(1 for d in self.days if d.candidate_formed)

    @property
    def trades_filled(self) -> int:
        return sum(1 for d in self.days if d.cycle and d.cycle.order)

    @property
    def trading_days_evaluated(self) -> int:
        return len(self.days)


def _decision_time_candles(
    all_candles: pd.DataFrame, trading_day: date, cutoff_bars: int
) -> pd.DataFrame:
    prior = all_candles[all_candles.index.date < trading_day]
    todays = all_candles[all_candles.index.date == trading_day]
    return pd.concat([prior, todays.iloc[:cutoff_bars]])


def run_daily_backtest(
    settings: Settings,
    all_candles: pd.DataFrame,
    option_quotes_by_day: dict[date, list[OptionQuote]] | None = None,
) -> DailyBacktestReport:
    """A fresh Orchestrator is constructed per simulated day (DailyLimits,
    paper_broker, and the same-day re-entry gate all reset), but every
    Orchestrator points at the same settings.database_path -- matching the
    real "fresh process each morning" deployment (execution/scheduler.py's
    own documented recommendation) while still letting learning.memory
    accumulate across the whole backtest, which is what the regime/setup
    win-rate breakdown afterward needs.
    """
    option_quotes_by_day = option_quotes_by_day or {}
    trading_days = sorted({ts.date() for ts in all_candles.index})
    days: list[BacktestDay] = []

    for trading_day in trading_days:
        prior = all_candles[all_candles.index.date < trading_day]
        if prior.empty:
            days.append(BacktestDay(trading_day, "insufficient_prior_history"))
            continue

        as_of = _decision_time_candles(all_candles, trading_day, DECISION_CUTOFF_BARS)
        todays_slice = as_of[as_of.index.date == trading_day]
        if len(todays_slice) <= OPENING_RANGE_MINUTES:
            days.append(BacktestDay(trading_day, "insufficient_today_bars"))
            continue

        spot = float(todays_slice.iloc[-1].close)
        decision_time = todays_slice.index[-1].to_pydatetime()
        option_quotes = option_quotes_by_day.get(trading_day, [])
        context = assemble_context(as_of, option_quotes, spot, decision_time, True, settings)
        candidate_formed = "candidate_direction" in context

        orchestrator = Orchestrator(settings)
        cycle = orchestrator.run_cycle(context)

        if cycle.order:
            reason = "traded"
        elif candidate_formed:
            reason = "candidate_no_option"
        else:
            reason = "no_candidate"
        days.append(BacktestDay(trading_day, reason, cycle, candidate_formed))
        logger.info(
            "daily_backtest_day trading_day=%s reason=%s candidate_formed=%s",
            trading_day,
            reason,
            candidate_formed,
        )

    return DailyBacktestReport(days)
