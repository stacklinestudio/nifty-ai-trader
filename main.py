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
import threading
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
from data.instrument_archive import real_archive_status, real_gap_check_status, run_daily_archive
from data.market_data import KiteMarketData, validate_quote
from data.rss_news import fetch_recent_news
from demo.demo_trade import run_demo_trade
from events.contracts import Event, EventType
from execution.live_context import build_live_context
from execution.process_lock import AlreadyRunningError, ProcessLock
from execution.scheduler import resume_open_positions, run_trading_day
from integrations.discord import CATEGORIES, DiscordNotifier, webhooks_by_category_from_settings
from integrations.obsidian import ObsidianExporter
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from monitoring.health import check_health, system_health
from monitoring.live_status_server import (
    build_live_status_server,
    build_mock_demo_position,
    dashboard_url,
    kite_chart_url,
    live_status_url,
    run_live_status_server_in_background,
)
from monitoring.logger import configure_logger
from monitoring.system_health_gate import run_system_health_gate
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


def _render_daily_report(day: datetime.date, summary: dict) -> str:
    """Part A's 08-Reports: the same real summary dict run_scheduled_day
    has always produced, now rendered as a readable note instead of a
    flat bullet dump."""
    lines = [f"# Daily Report — {day.isoformat()}", ""]
    lines += [f"- **{key}**: {value}" for key, value in summary.items()]
    return "\n".join(lines) + "\n"


def sync_obsidian_knowledge_layer(settings: Settings) -> None:
    """Brief 20 Parts A/C: keeps the vault's structural sections (real
    regime/setup vocabulary, real current risk config, real data-quality
    status, real research findings, real pattern-memory stats, and a
    fresh copy of docs/*.md) up to date. Called from the real daily
    scheduled path (above) so none of this can silently go stale, and
    from `main.py export-obsidian` for a manual/on-demand refresh. Fails
    closed exactly like every other Obsidian export -- a vault write
    failure here must never break the trading loop or the CLI command.
    """
    exporter = ObsidianExporter(settings.obsidian_vault_path)
    exporter.export_market_knowledge()
    exporter.export_risk_config(settings)
    archive_status, archive_detail = real_archive_status()
    exporter.export_data_quality(
        archive_status,
        archive_detail,
        real_gap_check_status(),
        [
            (
                "REST kite.quote() and KiteTicker (MODE_FULL) never carry tradingsymbol, "
                "expiry, strike, option_type, or the underlying NIFTY price inline -- both "
                "require a separate join/subscription (confirmed live, 2026-09-06)."
            ),
            (
                "KiteTicker MODE_QUOTE/MODE_LTP omit market depth entirely; MODE_FULL is "
                "required for real depth/OI capture (confirmed from the installed "
                "kiteconnect library's own real parsing source)."
            ),
            (
                "Real bid/ask is 5-level market depth (depth.buy/depth.sell, each with "
                "price+quantity+orders), not a flat bid/ask pair."
            ),
            (
                "Live tick frequency, live bid/ask movement, and real-time OI update "
                "cadence remain unverified as of Brief 19 -- structure confirmed only, "
                "pending the next real trading session."
            ),
        ],
    )
    exporter.export_research_summary()
    exporter.export_learning(MemoryStore(settings.database_path))
    exporter.sync_system_docs()


def _start_live_status_server_in_background_safely(settings: Settings) -> None:
    """Real, shared startup for the live-status/dashboard server, used
    both by `run_scheduled_day` (its own direct entry point, e.g.
    `python main.py run`) and by `start_day` (called first, before
    anything else -- see that function's own docstring for why).

    Initializes the database here (not just relies on a caller having
    already done so) so the dashboard's own real reads
    (`open_positions`, `events`, etc.) never hit a missing-table error
    if this is genuinely the first real thing to touch the database
    this run. `CREATE TABLE IF NOT EXISTS` makes this idempotent with
    any other real `database.initialize()` call elsewhere in the same
    process.

    A real bind failure (e.g. the configured port already in use) must
    never prevent the rest of a real day from proceeding.
    """
    try:
        database = Database(settings.database_path)
        database.initialize()
        run_live_status_server_in_background(database, settings, settings.live_status_port)
    except OSError as exc:
        logger.warning("live_status_server_start_failed error=%s", exc)


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
    # Brief 25/Final Brief: the real, local, read-only live status page +
    # Command Center dashboard -- started once per real day, reads the
    # same real open_positions table this Orchestrator maintains (opens
    # its own real, separate Database(settings.database_path)
    # connection, not this one, so it never needs to share an in-memory
    # object with the trading loop). Kept here (not just in start_day)
    # for `python main.py run`'s own direct call to this function.
    _start_live_status_server_in_background_safely(settings)
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
            # synthesis_ai_router (Brief 10), not the raw ai_router -- news
            # classification is the other real per-scan AI call site this
            # session's cost projection flagged; throttled to
            # ai_synthesis_refresh_seconds same as GlobalResearchAgent's.
            news_items = fetch_recent_news(orchestrator.synthesis_ai_router)
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
        ObsidianExporter(settings.obsidian_vault_path).export_markdown(
            "08-Reports", clock().date().isoformat(), _render_daily_report(clock().date(), summary)
        )
        sync_obsidian_knowledge_layer(settings)
    except Exception as exc:  # noqa: BLE001 - a vault write failure must never break the trading loop.
        logger.warning("obsidian_daily_research_export_failed error=%s", exc)
    return summary


def _notify_gate_result(settings: Settings, gate) -> None:
    """Brief 24: the real System Health Gate result, printed above and
    also sent as a real Discord "system" channel + Telegram notification
    -- regardless of verdict -- so an unattended headless run still
    surfaces it, not just a console nobody is watching."""
    severity = "INFO" if gate.verdict == "READY" else "WARNING"
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url,
            webhooks_by_category=webhooks_by_category_from_settings(settings),
        )
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord.send_message(severity, gate.describe(), "system")
        telegram.send_message(severity, gate.describe())
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break start-day.
        logger.warning("start_day_gate_notify_failed error=%s", exc)


def _start_option_tick_capture_in_background(settings: Settings) -> threading.Thread:
    """Brief 24 step 3: real option tick capture must run CONCURRENTLY
    with the real trading scheduler (step 4), not block before it --
    both cover the same real trading session. Launched in a daemon
    background thread; `run_capture_session` itself already fails closed
    and sends its own real notifications internally (Brief 19/22), so
    this function's own try/except only needs to cover real setup
    failures (fetching the real spot/instrument list to build today's
    real universe), a distinct, separate failure mode.
    """
    from data.option_tick_capture import build_universe, run_capture_session

    kite = build_kite_session(settings)
    if kite is None:
        raise RuntimeError("no real Kite session available for option tick capture")
    spot = kite.quote(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    instruments = kite.instruments("NFO")
    nifty_options = [r for r in instruments if r.get("name") == "NIFTY" and r.get("segment") == "NFO-OPT"]
    if not nifty_options:
        raise RuntimeError("no real NIFTY NFO-OPT instruments found -- cannot build today's real universe")

    def _row_expiry(row: dict) -> datetime.date:
        value = row["expiry"]
        return value if isinstance(value, datetime.date) else datetime.date.fromisoformat(str(value)[:10])

    nearest_expiry = min(_row_expiry(row) for row in nifty_options)
    universe = build_universe(instruments, spot, nearest_expiry)

    calendar = NseCalendar()
    now = datetime.datetime.now(IST)
    close_at = datetime.datetime.combine(now.date(), calendar.close_time, tzinfo=IST)
    duration_seconds = max(0.0, (close_at - now).total_seconds())

    thread = threading.Thread(
        target=run_capture_session, args=(settings, universe, duration_seconds), daemon=True
    )
    thread.start()
    return thread


def demo_live_link(settings: Settings, database: Database | None = None) -> dict:
    """`python main.py demo-live-link`: writes a clearly-labeled,
    structurally-separate DEMO position state the live status page can
    render, and sends a real Discord/Telegram message with the real,
    working link -- the same way `python main.py notifications` already
    test-sends, so delivery and rendering can be verified end to end
    without waiting for (or risking) a real trade.

    Structural isolation, matching the pattern already used for the
    AI-judgment experiment (`learning/experiment_manager.py`'s distinct
    `memory_type="experiment"` tag, never conflated with a real trade
    record) and demo-trade (`demo/demo_trade.py`'s wholly separate
    database): the mock state is written via `Database.save_demo_
    position`, a real, separate SQLite table (`demo_live_position`)
    `recover_open_positions` and every real position-supervision code
    path never reads from -- never `open_positions`, never
    `save_open_position`. `monitoring.live_status_server.current_
    position_view` always checks for a real open position FIRST; demo
    data can only ever appear in place of "no open position," never
    mask or be confused with a real one.

    `database` is injectable purely for tests (an isolated tmp_path
    database) -- production callers never pass it; the real Discord/
    Telegram sends always use `settings` as configured, unlike demo-
    trade, which deliberately forces notifications off. This command's
    entire point is a REAL, working notification.
    """
    database = database or Database(settings.database_path)
    database.initialize()
    now = datetime.datetime.now(IST)
    mock_view = build_mock_demo_position(now)
    database.save_demo_position(mock_view, now)

    dashboard_link = dashboard_url(settings)
    live_link = live_status_url(settings)
    # Follow-up to the real bug report: the real PAPER_FILL path also
    # includes a real Kite chart link (agents/orchestrator.py::_on_risk_
    # decision). This exercises the exact same kite_chart_url() call
    # against the mock position's own (obviously fake) instrument data,
    # so this command visibly proves that real behavior too -- not just
    # the dashboard link.
    chart_link = kite_chart_url("NFO", mock_view["symbol"], mock_view.get("instrument_token"))
    # The exact same real formatting the real PAPER_FILL entry
    # notification uses (agents/orchestrator.py::_on_risk_decision) --
    # a real Event run through the same real send_event() Discord/
    # Telegram already use for every other real event, not a
    # separately hand-rolled message string.
    event = Event(
        EventType.PAPER_FILL,
        "demo_live_link",
        now,
        output_summary={
            "order_id": "DEMO-ORDER",
            "fill_price": mock_view["entry"],
            "dashboard_url": dashboard_link,
            "live_status_url": live_link,
            "kite_chart_url": chart_link,
            "note": "DEMO DATA, NOT A REAL TRADE -- sent by python main.py demo-live-link",
        },
        confidence=100,
    )
    discord = DiscordNotifier(
        settings.discord_webhook_url, webhooks_by_category=webhooks_by_category_from_settings(settings)
    )
    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    discord_sent = discord.send_event(event)
    telegram_sent = telegram.send_event(event)
    return {
        "mock_view": mock_view,
        "dashboard_url": dashboard_link,
        "live_status_url": live_link,
        "kite_chart_url": chart_link,
        "discord_sent": discord_sent,
        "telegram_sent": telegram_sent,
    }


def start_day(
    settings: Settings,
    gate=None,
    archive_runner=None,
    capture_starter=None,
    scheduler_runner=None,
    dashboard_starter=None,
    calendar: NseCalendar | None = None,
    today: datetime.date | None = None,
) -> dict:
    """`python main.py start-day`: (0) start the real Command Center
    dashboard, (1) System Health Gate, (2) instrument archiving, (3)
    start real option tick capture in the background, (4) start the
    real main trading scheduler in the foreground (blocking, same as
    `python main.py run`).

    Real bug report, confirmed by direct inspection: the dashboard used
    to only start inside step 4 (`run_scheduled_day`) -- meaning a real
    kite_connection failure in step 1 (which stops this whole sequence
    immediately, see below) meant the dashboard NEVER started at all,
    and even on a real success path it wasn't reachable until after
    steps 1-3 had already run. Fixed: the dashboard now starts first,
    unconditionally, before the health gate's own real Kite login
    attempt -- reachable from the very start of this real sequence,
    showing an honest "no open position"/"no candidate yet" (or an
    honest BLOCKED gate) rather than being gated behind a successful
    Kite connection or a trade ever entering. `scheduler_runner`'s
    default (`run_scheduled_day`) also starts the dashboard itself (for
    its own standalone/`run` entry point) -- when both run in the same
    real process, the second real bind attempt harmlessly no-ops (the
    port is already in use; caught, logged, never fatal), so no double
    dashboard is required to avoid duplicating this call.

    Real finding from actually running this end to end (not assumed):
    on a real non-trading day, `run_scheduled_day` returns almost
    instantly (`run_trading_day`'s own real not-a-trading-day
    short-circuit), so this function would otherwise return right after
    it -- silently killing the real background capture thread (a daemon
    thread) mid-way through its real, multi-hour `duration_seconds`
    wait, before it ever got to do anything. Fixed two ways: (a) capture
    is not even started on a real non-trading day (nothing would stream
    anyway -- Brief 19's own finding), and (b) on a real trading day,
    this function joins the real capture thread before returning, so it
    is never silently killed once the scheduler naturally finishes
    around the same real close time capture's own duration targets.

    Explicit decision, not assumed: a real `BLOCKED` gate verdict does
    NOT stop this sequence -- it is printed and notified prominently
    (`_notify_gate_result`, regardless of outcome) and the sequence
    continues, matching Brief 23's own stated plan to observe the real
    gate across a few real days before deciding whether it should hard-
    block. The ONE absolute exception: a real, failed `kite_connection`
    check stops everything immediately -- nothing downstream (real
    instruments, real capture, real live scheduler data) can
    meaningfully run without it.

    Steps 2-4 are independent: a real failure in one is caught, reported
    clearly in the returned result (and printed), and does not prevent
    the remaining steps from being attempted. `gate`/`archive_runner`/
    `capture_starter`/`scheduler_runner`/`dashboard_starter` are
    injectable (default to the real functions) purely so this can be
    tested deterministically without live network calls -- production
    callers never pass them.
    """
    archive_runner = archive_runner or run_daily_archive
    capture_starter = capture_starter or _start_option_tick_capture_in_background
    scheduler_runner = scheduler_runner or run_scheduled_day
    dashboard_starter = dashboard_starter or _start_live_status_server_in_background_safely
    calendar = calendar or NseCalendar()
    today = today or datetime.datetime.now(IST).date()

    # Step 0, first, unconditionally -- see the docstring above.
    dashboard_starter(settings)

    result: dict = {"gate": None, "stopped_after_gate": False, "archive": None, "capture": None, "scheduler": None}

    if gate is None:
        database = Database(settings.database_path)
        database.initialize()
        gate = run_system_health_gate(settings, database)
    print(gate.describe())
    _notify_gate_result(settings, gate)
    result["gate"] = {"verdict": gate.verdict, "blocking_reasons": gate.blocking_reasons}

    kite_check = next((c for c in gate.checks if c.name == "kite_connection"), None)
    if kite_check is not None and kite_check.status != "OK":
        print(f"start-day STOPPED: real kite_connection check failed ({kite_check.detail}) -- nothing else can meaningfully run.")
        result["stopped_after_gate"] = True
        return result
    if gate.verdict == "BLOCKED":
        print(
            "start-day CONTINUING despite a BLOCKED health gate -- an explicit decision for now, "
            "observing the real gate before deciding whether it should hard-block (Brief 23)."
        )

    try:
        path = archive_runner(settings)
        result["archive"] = {"status": "OK" if path else "FAILED", "detail": str(path) if path else "no real archive produced"}
    except Exception as exc:  # noqa: BLE001 - one step's real failure must never prevent the others from being attempted.
        result["archive"] = {"status": "FAILED", "detail": f"{type(exc).__name__}: {exc}"}
    print(f"instrument archiving: {result['archive']['status']} -- {result['archive']['detail']}")

    capture_thread = None
    if not calendar.is_trading_day(today):
        result["capture"] = {"status": "SKIPPED", "detail": f"{today.isoformat()} is not a real NSE trading day"}
    else:
        try:
            capture_thread = capture_starter(settings)
            result["capture"] = {
                "status": "STARTED", "detail": "running in the background for the rest of the real session"
            }
        except Exception as exc:  # noqa: BLE001 - one step's real failure must never prevent the others from being attempted.
            result["capture"] = {"status": "FAILED", "detail": f"{type(exc).__name__}: {exc}"}
    print(f"option tick capture: {result['capture']['status']} -- {result['capture']['detail']}")

    try:
        day_summary = scheduler_runner(settings)
        result["scheduler"] = {"status": "OK", "detail": day_summary}
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed silently -- but this is the last real step either way.
        result["scheduler"] = {"status": "FAILED", "detail": f"{type(exc).__name__}: {exc}"}
    print(f"trading scheduler: {result['scheduler']['status']}")

    if capture_thread is not None:
        # Never let the real background capture thread be silently
        # killed by this function returning first -- see the docstring's
        # real finding above.
        capture_thread.join()

    return result


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
        "health-gate",
        "paper",
        "instruments",
        "agents",
        "memory",
        "events",
        "notifications",
        "export-obsidian",
        "run",
        "start-day",
        "live-status",
        "demo-live-link",
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
    if args.command == "health-gate":
        # Brief 23: a reporting tool only -- does NOT block this or any
        # other command. Whether to wire it as an actual pre-flight gate
        # is a separate, future decision.
        database = Database(settings.database_path)
        database.initialize()
        report = run_system_health_gate(settings, database)
        print(report.describe())
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
        # Brief 20: manual/on-demand trigger for the full real structural
        # export (market knowledge, current risk config, real data-
        # quality status, real research findings, real pattern-memory
        # stats, a fresh docs/ sync) -- the same real sync the daily
        # scheduled path already runs automatically.
        exporter = ObsidianExporter(settings.obsidian_vault_path)
        if exporter.root is None:
            print("Obsidian vault not configured or unavailable.")
            return 0
        report_path = exporter.export_markdown(
            "08-Reports",
            datetime.datetime.now(IST).date().isoformat(),
            "# Daily Report\n\n- **mode**: paper\n- **note**: No fabricated market data.\n",
        )
        sync_obsidian_knowledge_layer(settings)
        print(report_path)
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
    if args.command == "start-day":
        # Everything after the real Kite login, in one shot -- same
        # single-instance guard as "run", since this also ends up
        # calling run_scheduled_day.
        lock = ProcessLock(Path(f"{settings.database_path}.lock"))
        try:
            lock.acquire()
        except AlreadyRunningError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        try:
            result = start_day(settings)
            if result.get("stopped_after_gate"):
                # Real bug report, the same class as demo-live-link's
                # dead-on-arrival link: start_day's own dashboard starts
                # as its very first step (before the health gate),
                # specifically so it stays reachable through a real Kite
                # login failure -- but this CLI handler used to just
                # `return 0` right after, which exits the real OS
                # process and kills that server's daemon thread along
                # with it, undoing the entire point of starting it
                # first. Confirmed via a real subprocess run: the
                # process genuinely exited (code 0) within seconds of
                # printing "STOPPED". Fixed by blocking here instead of
                # returning -- the real server thread (already running,
                # started inside start_day itself) is never touched
                # again; this only keeps the enclosing process alive so
                # it isn't torn down.
                print(
                    "start-day stopped early after a real health-gate failure -- the real "
                    f"dashboard stays up: {dashboard_url(settings)} "
                    f"(live position page: {live_status_url(settings)}). Press Ctrl+C to stop."
                )
                # Real usability issue: a bare threading.Event().wait()
                # with no timeout blocks on a real OS-level wait that
                # Windows does not reliably interrupt for a real Ctrl+C
                # -- the interpreter only gets a chance to notice a
                # pending KeyboardInterrupt when it returns to running
                # Python bytecode, which an indefinite wait may not do
                # for a long time (if ever) on Windows. Looping on a
                # short-timeout wait instead -- the same "wake up
                # periodically" shape server.serve_forever() already
                # uses internally (its own selector.select(poll_interval)
                # loop, which is why live-status/demo-live-link's
                # Ctrl+C handling was never affected by this) -- returns
                # control to the interpreter every real second, so a
                # real Ctrl+C is caught promptly and reliably.
                stop_event = threading.Event()
                try:
                    while not stop_event.wait(timeout=1):
                        pass
                except KeyboardInterrupt:
                    pass
            return 0
        finally:
            lock.release()
    if args.command == "live-status":
        # Brief 25: manual/dev entry point -- runs the real, local,
        # read-only live position status page in the foreground
        # (blocking). The real daily path (run_scheduled_day above)
        # already starts this automatically in the background; this
        # command exists for standalone use (e.g. checking the page
        # without a full real trading day running).
        database = Database(settings.database_path)
        database.initialize()
        url = live_status_url(settings)
        print(f"Live position status page: {url}")
        print(f"Command Center dashboard: {dashboard_url(settings)}")
        print("Local network only -- never exposed beyond it. Ctrl+C to stop.")
        server = build_live_status_server(database, settings, settings.live_status_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0
    if args.command == "demo-live-link":
        # Brief 27 fix: a real bug report confirmed demo_live_link()
        # alone never started a server -- the real notification's link
        # was dead the instant the command returned (ERR_CONNECTION_
        # REFUSED immediately, not "after a few minutes"), unless some
        # OTHER process happened to already be serving that port. This
        # command is explicitly a standalone way to test the page
        # without running the whole day (per the real bug report's own
        # instruction) -- it now says so and actually stays up:
        # writes the clearly-labeled DEMO position (never the real
        # open_positions table), sends the real Discord/Telegram
        # notification, then blocks in the foreground serving the real
        # page (Ctrl+C to stop), exactly like `live-status`.
        database = Database(settings.database_path)
        database.initialize()
        result = demo_live_link(settings, database=database)
        print(f"Demo position written (DEMO DATA, not a real trade): {result['mock_view']['symbol']}")
        print(f"Command Center dashboard: {result['dashboard_url']}")
        print(f"Live status page: {result['live_status_url']}")
        print(f"Kite chart (demo, not a real instrument): {result['kite_chart_url']}")
        print(f"Discord sent: {result['discord_sent']}  Telegram sent: {result['telegram_sent']}")
        print("(If neither channel is configured, this correctly reports False -- same as `notifications`.)")
        print(
            "Standalone demo mode: this command does NOT start the real trading day -- "
            "the server below is what keeps the link above alive. Press Ctrl+C to stop "
            "(the demo state stays in the database until then, or until a real trade opens)."
        )
        server = build_live_status_server(database, settings, settings.live_status_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0
    if args.command == "demo-trade":
        # Deliberately builds its own fully isolated Settings internally
        # (demo/demo_trade.py::_demo_settings) -- never touches the real
        # `settings` object built above, or anything it points at.
        run_demo_trade()
        return 0
    if args.command == "instruments":
        # Brief 13 Part 2: real, daily-scheduled NFO instrument archiving
        # (data/instrument_archive.py) -- previously this subcommand was
        # registered but never implemented, always falling through to the
        # generic "no credentials" message below regardless of the real
        # reason. Fails closed the same honest way run_daily_archive
        # itself does: no valid session today (missing credentials, or a
        # real API failure -- most often an expired access token) prints
        # the same message a genuinely-unconfigured environment would,
        # never a crash, never a fabricated archive file. Brief 18: a real
        # session can also succeed and still return None -- the file got
        # written but failed post-write content validation -- so this
        # message no longer claims "no valid session" as the only real
        # explanation; the real reason either way is in the Discord
        # "system" channel notification run_daily_archive already sent.
        path = run_daily_archive(settings)
        if path:
            print(f"Archived real NFO instruments to {path}")
        else:
            print(
                "No trustworthy real NFO instrument archive was produced for today -- either no "
                "valid Kite session exists (missing credentials, or today's access token has "
                "expired/not yet been refreshed), or a real session succeeded but the written file "
                "failed post-write validation. See the Discord \"system\" channel notification for "
                "the specific real reason."
            )
        return 0
    print(
        "Download instruments through an authenticated official Kite SDK session; no credentials were found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
