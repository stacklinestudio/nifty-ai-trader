"""Command-line entry point. Defaults remain non-live and fail closed."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.report import write_backtest_report
from backtest.simulator import Simulator
from backtest.walk_forward import walk_forward
from config import Settings
from data.historical import validate_candles
from monitoring.health import check_health
from risk.risk_manager import RiskManager


def engine(settings: Settings) -> BacktestEngine:
    return BacktestEngine(
        RiskManager(settings.max_risk_per_trade, settings.max_position_value),
        Simulator(settings.tick_size, settings.entry_slippage_ticks),
    )


def load(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
    frame.index = pd.DatetimeIndex(frame.index)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("Asia/Kolkata")
    return validate_candles(frame)


def main() -> int:
    parser = argparse.ArgumentParser(description="NIFTY AI Trader (paper-only by default)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("backtest", "report"):
        p = sub.add_parser(name)
        p.add_argument("--data", required=True)
        p.add_argument("--output", default="reports/generated/backtest.json")
    sub.add_parser("health")
    sub.add_parser("paper")
    sub.add_parser("instruments")
    args = parser.parse_args()
    settings = Settings()
    settings.validate()
    if args.command in {"backtest", "report"}:
        result = engine(settings).run(load(args.data))
        walk = None
        try:
            walk = walk_forward(engine(settings), load(args.data))
        except ValueError:
            pass
        print(write_backtest_report(result, Path(args.output), walk))
        return 0
    if args.command == "health":
        import datetime

        print(check_health(settings, None, datetime.datetime.now(tz=__import__("config").IST)))
        return 0
    if args.command == "paper":
        print(
            "PAPER mode ready. No live order can be submitted. Configure supported Kite credentials and live market-data feed."
        )
        return 0
    print(
        "Download instruments through an authenticated official Kite SDK session; no credentials were found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
