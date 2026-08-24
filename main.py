"""CLI entry point. V2 remains deterministic, auditable, and paper-only by default."""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import pandas as pd

from agents.orchestrator import Orchestrator
from backtest.engine import BacktestEngine
from backtest.report import write_backtest_report
from backtest.simulator import Simulator
from backtest.walk_forward import walk_forward
from config import IST, Settings
from data.historical import validate_candles
from integrations.discord import DiscordNotifier
from integrations.obsidian import ObsidianExporter
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from monitoring.health import check_health, system_health
from risk.risk_manager import RiskManager
from storage.database import Database


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


def paper_dry_run(settings: Settings) -> dict:
    """Runs the full V2 workflow with empty facts; it must result in NO TRADE."""
    result = Orchestrator(settings).run_cycle({"market_data_fresh": False, "market_open": False})
    return {
        "paper_only": settings.trading_mode == "paper",
        "consensus": result.consensus,
        "risk_approved": result.risk_approved,
        "order": result.order,
        "agent_count": len(result.agent_results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="NIFTY AI Trader V2 (paper-only by default)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("backtest", "report"):
        command = sub.add_parser(name)
        command.add_argument("--data", required=True)
        command.add_argument("--output", default="reports/generated/backtest.json")
    for name in (
        "health",
        "paper",
        "instruments",
        "agents",
        "memory",
        "events",
        "notifications",
        "export-obsidian",
    ):
        sub.add_parser(name)
    args = parser.parse_args()
    settings = Settings()
    settings.validate()
    if args.command in {"backtest", "report"}:
        candles = load(args.data)
        result = engine(settings).run(candles)
        try:
            walk = walk_forward(engine(settings), candles)
        except ValueError:
            walk = None
        print(write_backtest_report(result, Path(args.output), walk))
        return 0
    if args.command == "health":
        database = Database(settings.database_path)
        database.initialize()
        now = datetime.datetime.now(IST)
        print(check_health(settings, None, now))
        print(*system_health(settings, True, None, now), sep="\n")
        return 0
    if args.command in {"paper", "agents"}:
        print(json.dumps(paper_dry_run(settings), indent=2, default=str))
        return 0
    if args.command == "memory":
        print(json.dumps(MemoryStore(settings.database_path).recent(), indent=2))
        return 0
    if args.command == "events":
        database = Database(settings.database_path)
        database.initialize()
        print(json.dumps(database.events(), indent=2))
        return 0
    if args.command == "notifications":
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord = DiscordNotifier(settings.discord_webhook_url)
        print(
            json.dumps(
                {
                    "telegram_sent": telegram.send_message(
                        "INFO", "V2 notification test; no trade."
                    ),
                    "discord_sent": discord.send_message("INFO", "V2 notification test; no trade."),
                }
            )
        )
        return 0
    if args.command == "export-obsidian":
        path = ObsidianExporter(settings.obsidian_vault_path).export(
            "Daily Research",
            datetime.datetime.now(IST).date().isoformat(),
            {"mode": "paper", "note": "No fabricated market data."},
        )
        print(path or "Obsidian vault not configured or unavailable.")
        return 0
    print(
        "Download instruments through an authenticated official Kite SDK session; no credentials were found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
