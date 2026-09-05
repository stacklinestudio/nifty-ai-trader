"""Runs the exact same once-per-trading-day entry pipeline main.py's
run_scheduled_day uses live -- the same Orchestrator.run_cycle, the same
execution.live_context.assemble_context, the same signal/risk/validation
pipeline, the same Settings.signal_threshold -- over a historical window
of real Kite data, instead of one live day.

Not a faster or different cadence, and not a lowered signal_threshold: if
this finds few or zero trades, that's the same honest reason the live
system would show (execution/live_context.py::KNOWN_GAPS), not a
backtest-specific difference. As of Brief 8, real historical global-
market data can be supplied via global_context_by_day (real day-over-day
percent change, no look-ahead); option buildup still needs a previous
option-chain snapshot the caller must supply (option_quotes_by_day), and
news has no historical archive to draw from at all (free RSS feeds have
no queryable history) -- both stay an honest, explicit gap rather than a
worked-around one. A caller wanting to see what *would* fire without
these gaps should construct a second Orchestrator with a deliberately
different Settings.signal_threshold and run this same function again --
never silently inside here.

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
from datetime import date, timedelta

import pandas as pd

from agents.orchestrator import CycleResult, Orchestrator
from config import IST, Settings
from data.global_market import ContextValue
from data.option_chain import OptionQuote
from execution.live_context import TECHNICAL_FEATURE_WINDOW_DAYS, assemble_context
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
    """Brief 13: `prior` is bounded to the same real
    TECHNICAL_FEATURE_WINDOW_DAYS execution/live_context.py::
    build_live_context actually fetches from Kite for the live path --
    a live process can never see more real history than that anyway, and
    empirically (a real 12-month dataset, 15 sample points) an unbounded
    `prior` produced byte-identical EMA/RSI/ATR to this bounded one every
    time, so the previous unbounded version was pure wasted computation,
    not extra correctness, and -- before this fix -- a real, if
    practically harmless, divergence between what this backtest replayed
    and what the live path could ever actually compute.
    """
    window_start = pd.Timestamp(trading_day, tz=IST) - timedelta(days=TECHNICAL_FEATURE_WINDOW_DAYS)
    prior = all_candles[(all_candles.index.date < trading_day) & (all_candles.index >= window_start)]
    todays = all_candles[all_candles.index.date == trading_day]
    return pd.concat([prior, todays.iloc[:cutoff_bars]])


def run_daily_backtest(
    settings: Settings,
    all_candles: pd.DataFrame,
    option_quotes_by_day: dict[date, list[OptionQuote]] | None = None,
    global_context_by_day: dict[date, list[ContextValue]] | None = None,
) -> DailyBacktestReport:
    """A fresh Orchestrator is constructed per simulated day (DailyLimits,
    paper_broker, and the same-day re-entry gate all reset), but every
    Orchestrator points at the same settings.database_path -- matching the
    real "fresh process each morning" deployment (execution/scheduler.py's
    own documented recommendation) while still letting learning.memory
    accumulate across the whole backtest, which is what the regime/setup
    win-rate breakdown afterward needs.

    global_context_by_day (Brief 8 Part D): real historical global-market
    data for each real trading day, e.g. from
    data/global_market.py::fetch_global_history -- a genuinely real
    per-day series (unlike option_quotes_by_day, which has no historical
    archive to draw from), so this is threaded straight through per day,
    no carry-forward needed. Defaults to None/{} -- a day with no entry
    reads as [] (unavailable), exactly like every other missing-data case
    in this codebase, never fabricated. There is no equivalent
    news_items_by_day: free RSS feeds have no queryable historical
    archive, so news stays genuinely unavailable for every backtest day
    -- an honest, stated gap, not silently worked around.
    """
    option_quotes_by_day = option_quotes_by_day or {}
    global_context_by_day = global_context_by_day or {}
    trading_days = sorted({ts.date() for ts in all_candles.index})
    days: list[BacktestDay] = []
    # Mirrors the live path's storage.database.Database.latest_option_chain_
    # snapshot semantics (Brief 5 Part B): "the most recently fetched real
    # chain," not strictly "yesterday's" -- if a day in option_quotes_by_day
    # has no entry, the last real one seen so far in this sequential pass
    # still serves as the comparison baseline, same as a live gap day
    # wouldn't erase what was last persisted. No SQLite persistence is
    # needed here specifically because the whole day-indexed dict is
    # already resident in memory for the entire backtest window -- unlike
    # live's fresh-process-per-day deployment, there's no process boundary
    # to carry state across.
    last_option_snapshot: list[OptionQuote] = []

    for trading_day in trading_days:
        # Read and (if real) record this day's chain before any skip check
        # below -- a day skipped for insufficient_prior_history/today_bars
        # (every backtest's first day, always) still really has its own
        # option chain in option_quotes_by_day if the caller supplied one;
        # losing that would mean day 1's real data could never become day
        # 2's real baseline, defeating the point of this sequential pass.
        todays_option_quotes = option_quotes_by_day.get(trading_day, [])
        if todays_option_quotes:
            previous_option_quotes_for_today = last_option_snapshot
            last_option_snapshot = todays_option_quotes
        else:
            previous_option_quotes_for_today = last_option_snapshot

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
        context = assemble_context(
            as_of,
            todays_option_quotes,
            spot,
            decision_time,
            True,
            settings,
            previous_option_quotes_for_today,
            global_context_by_day.get(trading_day, []),
        )
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
