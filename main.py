"""CLI entry point. V2 remains deterministic, auditable, and paper-only by default."""

from __future__ import annotations

from dotenv import load_dotenv

# Must run before `from config import ...` below: Settings' fields default
# via os.getenv(...) evaluated when the config module is first imported,
# not when Settings() is instantiated -- loading here, before any other
# local import, is the only placement that actually works.
load_dotenv(".env.local")  # real local credentials take precedence...
load_dotenv(".env")  # ...falling back to the non-secret template/defaults

import argparse
import datetime
import json
import time
from pathlib import Path

import pandas as pd

from agents.orchestrator import Orchestrator
from backtest.engine import BacktestEngine
from backtest.report import write_backtest_report
from backtest.simulator import Simulator
from backtest.walk_forward import walk_forward
from config import IST, Settings
from data.calendar import NseCalendar
from data.historical import validate_candles
from data.market_data import KiteMarketData, validate_quote
from execution.live_context import build_live_context
from execution.process_lock import AlreadyRunningError, ProcessLock
from execution.scheduler import resume_open_positions, run_trading_day
from integrations.discord import CATEGORIES, DiscordNotifier, webhooks_by_category_from_settings
from integrations.obsidian import ObsidianExporter
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from monitoring.health import check_health, system_health
from monitoring.logger import configure_logger
from risk.risk_manager import RiskManager
from storage.database import Database

logger = configure_logger(__name__)


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


def build_kite_session(settings: Settings) -> object | None:
    """Returns an authenticated KiteConnect session when credentials and the
    kiteconnect package are both available, else None -- callers must treat
    None as "no live data," never fabricate a session. Shared by
    context_provider and quote_source in run_scheduled_day so a real day
    uses one session, not two independently-constructed ones."""
    if not (settings.kite_api_key and settings.kite_access_token):
        return None
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        logger.warning("kiteconnect_not_installed; live data unavailable")
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(settings.kite_access_token)
    return kite


def build_live_quote_source(settings: Settings, symbol: str, kite: object | None = None):
    """Returns a real Kite-backed quote source when a session is available
    (built fresh if `kite` isn't supplied), otherwise a source that always
    reports unavailable -- never fabricated.

    KNOWN GAP, not fixed by this call: run_scheduled_day always builds this
    for the literal symbol "NIFTY" (the index), including for supervising
    an already-open or resumed position -- but a real open position's
    instrument is an OPTION contract (e.g. "NFO:NIFTY2690124200CE"), not
    the index. Supervision would be checking the wrong symbol's price.
    Flagged in run_scheduled_day's docstring and the accompanying report;
    not fixed in this pass since it needs execution/scheduler.py's
    quote_source to become per-position rather than fixed at day start,
    a separate change from the entry-context assembly this pass built.
    """
    kite = kite if kite is not None else build_kite_session(settings)
    if kite is None:
        return lambda: None
    market_data = KiteMarketData(kite)

    def live_quote() -> float | None:
        try:
            quote = market_data.get_quote(symbol)
            validate_quote(quote, datetime.datetime.now(IST), settings.stale_data_seconds)
            return quote.ltp
        except Exception as exc:  # noqa: BLE001 - turns any transport/staleness failure into
            # "no data" for run_supervised's own retry/force-exit handling; never crashes here.
            logger.warning("quote_fetch_failed symbol=%s error=%s", symbol, exc)
            return None

    return live_quote


def run_scheduled_day(settings: Settings) -> dict:
    """Entry point for unattended daily operation (Brief 3, Part A1).

    Recommended deployment: a fresh process each trading morning via
    cron/systemd (see execution/scheduler.py's docstring for why), not one
    process staying resident for days -- this function runs exactly one
    trading day and returns.

    The live entry-context assembly pipeline (execution/live_context.py)
    is now wired in here -- context_provider builds real spot/candles/
    technicals/option-chain context instead of an empty dict, when a Kite
    session is available. Known, still-open gaps (see
    execution/live_context.py::KNOWN_GAPS and the accompanying report for
    the full honest list):
    - SignalEngine's confidence formula is fed only 2 of 7 real sub-signals
      right now, capping achievable confidence below the default
      signal_threshold -- candidates will rarely form until more of
      KNOWN_GAPS is wired to real data, or signal_threshold is
      deliberately reconsidered (not silently changed here).
    - build_live_quote_source is still called with the literal symbol
      "NIFTY" for supervising any open/resumed position, not that
      position's actual option instrument -- see its own docstring.
    - fetch_option_quotes' `oi` field mapping is unconfirmed against a
      real live option quote (only the index quote and instrument list
      were captured live).
    No credentials configured still correctly produces "no_entry" (fails
    closed), same as before this pass.
    """
    database = Database(settings.database_path)
    database.initialize()
    orchestrator = Orchestrator(settings, database)
    calendar = NseCalendar()
    kite = build_kite_session(settings)
    quote_source = build_live_quote_source(settings, "NIFTY", kite)

    def clock() -> datetime.datetime:
        return datetime.datetime.now(IST)

    def context_provider() -> dict:
        if kite is None:
            return {}
        return build_live_context(settings, kite, calendar, clock())

    resumed = resume_open_positions(orchestrator, quote_source, clock, time.sleep)
    day = run_trading_day(
        orchestrator,
        calendar,
        context_provider=context_provider,
        quote_source=quote_source,
        clock=clock,
        sleeper=time.sleep,
    )
    return {
        "resumed_positions": len(resumed),
        "day_ran": day.ran,
        "day_reason": day.reason,
        "order": day.cycle.order if day.cycle else None,
    }


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
        "run",
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
        discord = DiscordNotifier(
            settings.discord_webhook_url,
            webhooks_by_category=webhooks_by_category_from_settings(settings),
        )
        print(
            json.dumps(
                {
                    "telegram_sent": telegram.send_message(
                        "INFO", "V2 notification test; no trade."
                    ),
                    "discord_sent": discord.send_message("INFO", "V2 notification test; no trade."),
                    "discord_by_category": {
                        category: discord.send_message(
                            "INFO", f"V2 notification test ({category} channel); no trade.", category
                        )
                        for category in CATEGORIES
                    },
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
    if args.command == "run":
        lock = ProcessLock(Path(f"{settings.database_path}.lock"))
        try:
            lock.acquire()
        except AlreadyRunningError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        try:
            print(json.dumps(run_scheduled_day(settings), indent=2, default=str))
            return 0
        finally:
            lock.release()
    print(
        "Download instruments through an authenticated official Kite SDK session; no credentials were found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
