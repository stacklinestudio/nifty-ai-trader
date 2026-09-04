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
from data.global_market import YFinanceGlobalMarketProvider
from data.historical import validate_candles
from data.market_data import KiteMarketData, validate_quote
from data.rss_news import fetch_recent_news
from demo.demo_trade import run_demo_trade
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
    """Returns a real Kite-backed quote source for the given, fully
    exchange-prefixed symbol (e.g. "NSE:NIFTY 50" or "NFO:NIFTY2690124200CE")
    when a session is available (built fresh if `kite` isn't supplied),
    otherwise a source that always reports unavailable -- never fabricated.

    Callers supervising an open position must build this per-position, from
    that position's own instrument symbol -- see
    execution/scheduler.py::resume_open_positions/run_trading_day's
    quote_source_factory parameter and run_scheduled_day below. A single
    quote source fixed to one symbol (this function used to always be
    called with the literal index symbol "NIFTY" for supervision,
    regardless of what was actually held) was a real bug, not just a
    theoretical risk -- fixed by making the factory per-instrument.
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

    As of Brief 6, run_trading_day periodically re-scans for a new entry
    through the day (Settings.entry_scan_interval_seconds,
    Settings.entry_scan_cutoff_time) instead of evaluating exactly once
    near open -- so this can now open, supervise, and close more than one
    position in a single day, up to Settings.max_trades_per_day, pausing
    scanning while a position is open and resuming once it closes with
    capacity remaining. context_provider is called once per scan, not
    once per day -- each call also persists that scan's own option chain
    (see below), so the "previous snapshot" OI-buildup scoring compares
    against gets fresher through the day too, not just day over day.

    The live entry-context assembly pipeline (execution/live_context.py)
    is wired in here -- context_provider builds real spot/candles/
    technicals/option-chain context instead of an empty dict, when a Kite
    session is available. Position supervision (both a freshly-filled
    position and any resumed from a prior crash) uses a per-instrument
    quote source built from that specific position's own option symbol
    (quote_source_factory below), not a single quote source fixed to the
    index -- see execution/scheduler.py's docstring for why a fixed one
    was a real bug. Also persists this cycle's option chain
    (Database.save_option_chain_snapshot) and retrieves the prior one
    (latest_option_chain_snapshot) so option-based scoring has real
    day-over-day data to compare, since a fresh process each morning has
    no other memory of yesterday (Brief 5 Part B). As of Brief 8, real
    global-market data (YFinanceGlobalMarketProvider) and real news
    (data/rss_news.py -- real RSS feeds, classified by AI when configured
    or a real deterministic keyword fallback otherwise) are both wired
    into every scan's context here too -- `global_score`/`news` are no
    longer wired-but-empty; each independently fails closed to an empty
    list on its own real fetch failure, same as everything else. Known,
    still-open gaps (see execution/live_context.py::KNOWN_GAPS and the
    accompanying report for the full honest list):
    - `option`'s OI-buildup scoring stays at its honest "unavailable"
      floor until a second real snapshot has actually been persisted
      (i.e. from the second day this runs onward).
    - fetch_option_quotes' `oi` field mapping is unconfirmed against a
      real live option quote (only the index quote and instrument list
      were captured live).
    - Real AI enrichment (ai/provider.py::AnthropicProvider) is wired but
      currently blocked on the configured Anthropic account having no
      credit balance -- see V2_BUILD_REPORT.md's Brief 8 Part C.

    A real Obsidian "Daily Research" journal entry is written after the
    day completes (ObsidianExporter, fails closed on any vault write
    failure -- never blocks returning this function's result); each real
    trade close writes its own real "Trade Journal" entry independently
    (Orchestrator._close_position). No credentials configured still
    correctly produces "no_entry" (fails closed), same as before this
    pass.
    """
    database = Database(settings.database_path)
    database.initialize()
    orchestrator = Orchestrator(settings, database)
    calendar = NseCalendar()
    kite = build_kite_session(settings)

    def quote_source_factory(symbol: str):
        return build_live_quote_source(settings, f"NFO:{symbol}", kite)

    def clock() -> datetime.datetime:
        return datetime.datetime.now(IST)

    def context_provider() -> dict:
        if kite is None:
            return {}
        # The real prior-session option chain, if one has ever been
        # persisted -- Brief 5 Part B. Read here (not before, at function-
        # definition time) so it reflects whatever's actually in the
        # database at the moment this cycle runs, not whenever
        # run_scheduled_day started.
        previous_option_quotes = database.latest_option_chain_snapshot()
        # Real external data (Brief 8 Parts A/B) -- each independently
        # fails closed: a yfinance or RSS failure here means an empty
        # list, which GlobalResearchAgent/NewsAgent already correctly
        # read as "unavailable," never a crash and never a guess.
        try:
            global_context = YFinanceGlobalMarketProvider().snapshot()
        except Exception as exc:  # noqa: BLE001 - yfinance's own failure must not block the rest of this cycle.
            logger.warning("global_market_snapshot_failed error=%s", exc)
            global_context = []
        try:
            news_items = fetch_recent_news(orchestrator.ai_router)
        except Exception as exc:  # noqa: BLE001 - an RSS failure must not block the rest of this cycle.
            logger.warning("news_fetch_failed error=%s", exc)
            news_items = []
        context = build_live_context(
            settings,
            kite,
            calendar,
            clock(),
            previous_option_quotes=previous_option_quotes,
            global_context=global_context,
            news_items=news_items,
        )
        # Persist THIS cycle's chain so the next cycle (a fresh process,
        # per this deployment's own recommended "one process per trading
        # day" model) has a real previous snapshot to compare against --
        # save_option_chain_snapshot no-ops on an empty list, so a day
        # where no option chain was fetched never poisons tomorrow's read.
        database.save_option_chain_snapshot(clock(), context.get("option_quotes", []))
        return context

    resumed = resume_open_positions(orchestrator, quote_source_factory, clock, time.sleep)
    day = run_trading_day(
        orchestrator,
        calendar,
        context_provider=context_provider,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=time.sleep,
    )
    summary = {
        "resumed_positions": len(resumed),
        "day_ran": day.ran,
        "day_reason": day.reason,
        "scans": len(day.rounds),
        "trades_today": sum(1 for r in day.rounds if r.cycle.order),
        "order": day.cycle.order if day.cycle else None,
    }
    # Real daily research journal entry, written as the day happens --
    # was previously only reachable via the standalone `export-obsidian`
    # CLI command (a fixed placeholder note on manual request). Each real
    # trade close already writes its own real "Trade Journal" entry
    # independently (Orchestrator._close_position). Fails closed exactly
    # like every other integration here -- ObsidianExporter.export()
    # already returns None on no vault configured or a real OSError; this
    # broader except also covers anything else, and either way a vault
    # write failure must never prevent this function from returning its
    # real result.
    try:
        ObsidianExporter(settings.obsidian_vault_path).export(
            "Daily Research", clock().date().isoformat(), summary
        )
    except Exception as exc:  # noqa: BLE001 - a vault write failure must never break the trading loop.
        logger.warning("obsidian_daily_research_export_failed error=%s", exc)
    return summary


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
        "demo-trade",
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
    if args.command == "demo-trade":
        # Deliberately builds its own fully isolated Settings internally
        # (demo/demo_trade.py::_demo_settings) -- never touches the real
        # `settings` object built above, or anything it points at.
        run_demo_trade()
        return 0
    print(
        "Download instruments through an authenticated official Kite SDK session; no credentials were found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
