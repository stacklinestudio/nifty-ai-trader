"""Phase 2 Piece 4: date-based train/validation/out-of-sample split for
the real agent/orchestrator-based daily replay (backtest/daily_backtest.py
::run_daily_backtest).

This is a real, honest sub-step this phase's own brief called out
explicitly: backtest/walk_forward.py already does a 60/20/20 date split,
but it calls BacktestEngine.run() (the older, ORB-only engine) directly
and cannot be reused unmodified against DailyBacktestReport's different
shape -- Phase 1's own audit flagged the agent-based backtest path as
having no real walk-forward wiring of its own. This adapts the SAME real
60/20/20 date-split logic, not a different one, to that path.

Runs the full, unsliced real replay ONCE (so every day -- including
out-of-sample ones -- gets the same real prior-history window a live
process would genuinely have had; slicing the INPUT candles per split
would incorrectly starve later-window days of real history they should
see), then partitions the real per-day results by date range.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.daily_backtest import DailyBacktestReport, run_daily_backtest
from config import Settings


@dataclass(frozen=True)
class DailyWalkForwardResult:
    train: DailyBacktestReport
    validation: DailyBacktestReport
    out_of_sample: DailyBacktestReport


def run_daily_backtest_walk_forward(
    settings: Settings,
    all_candles: pd.DataFrame,
    train_ratio: float = 0.6,
    validation_ratio: float = 0.2,
) -> DailyWalkForwardResult:
    dates = sorted({ts.date() for ts in all_candles.index})
    if len(dates) < 3:
        raise ValueError("Walk-forward requires at least three real trading days")
    first = max(1, int(len(dates) * train_ratio))
    second = max(first + 1, int(len(dates) * (train_ratio + validation_ratio)))
    train_dates = set(dates[:first])
    validation_dates = set(dates[first:second])
    out_of_sample_dates = set(dates[second:])

    full_report = run_daily_backtest(settings, all_candles)

    def part(dates_set: set) -> DailyBacktestReport:
        return DailyBacktestReport([d for d in full_report.days if d.trading_day in dates_set])

    return DailyWalkForwardResult(part(train_dates), part(validation_dates), part(out_of_sample_dates))
