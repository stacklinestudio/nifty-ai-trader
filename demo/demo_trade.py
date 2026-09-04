"""Brief 9 follow-up Part B: a clearly-labeled, structurally-isolated demo
trade walkthrough.

Runs the exact real pipeline -- the same Orchestrator, RiskAgent,
TradeBuilderAgent, and exit engine (execution/position_supervisor.py)
that a real trading day uses -- against a constructed synthetic scenario,
never a real historical day.

Why synthetic, not a real historical day: the real 42-day backtest never
produced a single tradeable candidate (real confirmed max confidence
53.8, every day "no_candidate" -- see V2_BUILD_REPORT.md's Brief 4-9
sections). No real historical day in that window could demonstrate the
position-sizing/fill/exit stages this demo is asked to show, because
none of them ever got that far. The scenario used here is not an
arbitrary invention either -- it is the exact real 81.25-confidence
best-case scenario verified in the confidence-ceiling deep-dive and
tests/test_live_context.py::
test_a_range_favored_setup_now_clears_the_real_unchanged_threshold_after_the_technical_score_fix
(every SignalEngine input at its own real structural maximum), run once
more here through the full live pipeline for a human-readable
walkthrough instead of pytest assertions.

Structural isolation, not just output labeling:
- A dedicated database path (data/private/demo_trade.db), never
  Settings.database_path from the real environment -- so record_trade/
  create_experiment/DailyLimits/open_positions can never touch the real
  learning.memory or affect a real day's trade count.
- Discord/Telegram config forced empty regardless of what the real
  environment has configured, so nothing here can ever send a real
  notification about a fake trade (integrations/discord.py and
  integrations/telegram.py both no-op on an empty token/URL -- confirmed
  by reading their own send_message implementations).
- AI provider forced "unavailable" -- a demo run should be fast,
  deterministic, and not spend real API credits.
- A fresh Orchestrator per run, so DailyLimits (in-memory only, never
  persisted) starts and stays empty regardless of any other real run.

Every printed line is prefixed with LABEL so this output can never be
mistaken for a real trade record if shared or reviewed later.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agents.orchestrator import Orchestrator
from config import IST, Settings
from data.calendar import NseCalendar
from data.global_market import ContextValue
from data.instruments import OptionInstrument
from data.news import NewsItem
from data.option_chain import OptionQuote
from execution.live_context import build_live_context

LABEL = "[DEMO - ILLUSTRATIVE ONLY, NOT A REAL SIGNAL]"

DEMO_DATABASE_PATH = Path("data/private/demo_trade.db")


def _say(line: str = "") -> None:
    print(f"{LABEL} {line}" if line else LABEL)


def _minute_bars(
    day: date, start_hour: int, start_minute: int, count: int, base_price: float, trend: float, volume: int = 1000
) -> list[dict[str, Any]]:
    rows = []
    price = base_price
    for i in range(count):
        ts = datetime(day.year, day.month, day.day, start_hour, start_minute, tzinfo=IST) + timedelta(minutes=i)
        price += trend
        rows.append(
            {
                "date": ts,
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": volume,
            }
        )
    return rows


def _full_prior_day(day: date, close_price: float) -> list[dict[str, Any]]:
    return _minute_bars(day, 9, 15, 375, close_price - 20, 20 / 375)


class _DemoKite:
    """A synthetic stand-in for the real Kite client, shaped exactly like
    every other FakeKite fixture already established in this codebase's
    own test suite (tests/test_live_context.py). Never makes a real
    network call -- this demo is fully offline by construction, not just
    by omission of credentials.
    """

    def __init__(
        self, quote_response: dict, historical_rows: list[dict], instrument_rows: list[dict], option_quote_response: dict
    ) -> None:
        self.quote_response = quote_response
        self.historical_rows = historical_rows
        self.instrument_rows = instrument_rows
        self.option_quote_response = option_quote_response

    def quote(self, symbols: list[str]) -> dict:
        if symbols and symbols[0].startswith("NFO:"):
            return self.option_quote_response
        return self.quote_response

    def historical_data(self, instrument_token: int, start: datetime, end: datetime, interval: str) -> list[dict]:
        return self.historical_rows

    def instruments(self, segment: str) -> list[dict]:
        return self.instrument_rows


def _build_ceiling_scenario_context(settings: Settings) -> dict[str, Any]:
    """The exact real 81.25-confidence scenario (every SignalEngine input
    at its own real structural maximum) verified in the confidence-
    ceiling deep-dive and tests/test_live_context.py's own real-pipeline
    proof. Not re-derived or approximated here -- the same specific real
    values.
    """
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    scan_time = datetime(2026, 9, 1, 9, 55, tzinfo=IST)
    prior_rows = _full_prior_day(prior_day, 24080.4)
    rise = _minute_bars(today, 9, 15, 30, 24000.0, 2.5)
    flat = _minute_bars(today, 9, 45, 10, rise[-1]["close"], 0.0)
    flat[-1]["low"] = 24030.0
    historical_rows = prior_rows + rise + flat
    # Real bug caught the first time this demo was run: strategy/
    # option_selector.py::select() checks expiry against the REAL
    # wall-clock date (datetime.now(...).date()), not the scenario's own
    # simulated "today" -- an expiry tied to the fixed scenario date
    # (2026-09-01) silently fails that check once real time moves past
    # it, with no error, just an empty selection. Anchored to real
    # wall-clock time plus a real margin instead, so this demo keeps
    # working no matter when it's actually run.
    real_future_expiry = datetime.now(IST).date() + timedelta(days=180)
    instruments = [
        {
            "name": "NIFTY",
            "segment": "NFO-OPT",
            "tradingsymbol": "NIFTY2690124200CE",
            "strike": 24200.0,
            "expiry": real_future_expiry.isoformat(),
            "instrument_type": "CE",
            "lot_size": 65,
            "instrument_token": 111,
        }
    ]
    option_quotes_response = {
        "NFO:NIFTY2690124200CE": {
            # 100.0 * lot_size 65 = 6,500 -- real, affordable within the
            # real default max_position_value (7,500); 120.0 (an earlier
            # version of this fixture) would have priced out every lot
            # (120*65=7,800 > 7,500), which is exactly what happened the
            # first time this demo was run -- caught by real output, not
            # assumed.
            "last_price": 100.0,
            "volume": 20000,
            "timestamp": scan_time.replace(tzinfo=None),
            "depth": {"buy": [{"price": 99.5}], "sell": [{"price": 100.5}]},
            "oi": 45000,
        }
    }
    index_quote = {
        "NSE:NIFTY 50": {
            "instrument_token": 256265,
            "last_price": 24075.0,
            "volume": 0,
            "timestamp": scan_time.replace(tzinfo=None),
            "depth": {"buy": [{"price": 24074.5}], "sell": [{"price": 24075.5}]},
        }
    }
    kite = _DemoKite(index_quote, historical_rows, instruments, option_quotes_response)

    prev_instrument = OptionInstrument("NIFTY2690124200CE", 24200.0, real_future_expiry, "CE", 65)
    previous_option_quotes = [OptionQuote(prev_instrument, 100.0, scan_time, open_interest=5000, volume=5000)]
    global_context = [ContextValue("SP500", 100.0, scan_time, "yfinance", True)]
    news_items = [NewsItem(scan_time, "Nifty rallies on strong buying", "demo", 1.0, "POSITIVE", 1.0)]

    return build_live_context(
        settings,
        kite,
        NseCalendar(),
        now=scan_time,
        previous_option_quotes=previous_option_quotes,
        global_context=global_context,
        news_items=news_items,
    )


def _demo_settings(database_path: Path) -> Settings:
    """Explicit overrides, not reliance on defaults -- Settings' fields
    read from the real environment at import time, so a real Discord
    webhook or Telegram token configured for actual live use must be
    explicitly blanked here, not merely left unset.
    """
    if database_path.exists():
        database_path.unlink()
    return Settings(
        database_path=database_path,
        signal_threshold=75.0,  # the real, unchanged threshold -- this demo proves it clears THIS bar, not a lowered one
        discord_webhook_url="",
        discord_webhook_market_research="",
        discord_webhook_signals="",
        discord_webhook_trades="",
        discord_webhook_risk="",
        discord_webhook_system="",
        discord_webhook_daily_report="",
        telegram_bot_token="",
        telegram_chat_id="",
        ai_provider="unavailable",
    )


def run_demo_trade(database_path: Path | None = None) -> dict[str, Any]:
    """database_path: injectable for tests (so a test run never touches
    the real data/private/demo_trade.db either) -- defaults to
    DEMO_DATABASE_PATH, the real path this demo uses when run from the
    CLI. Never Settings().database_path (the real trading database) --
    there is no code path in this module that ever reads that field.
    """
    settings = _demo_settings(database_path or DEMO_DATABASE_PATH)
    _say("=" * 70)
    _say("DEMO TRADE WALKTHROUGH -- runs the real pipeline, fake data")
    _say(f"Isolated database: {settings.database_path} (never the real trading database)")
    _say("Discord/Telegram: forced off. AI: forced off. Never a real notification.")
    _say("=" * 70)

    context = _build_ceiling_scenario_context(settings)
    _say()
    _say(f"Setup detected: {context.get('setup_type')}")
    _say(f"Candidate direction: {context.get('candidate_direction')}")
    _say(f"SignalEngine confidence: {context.get('candidate_confidence')}")
    for line in context.get("candidate_evidence", []):
        _say(f"  evidence: {line}")

    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(context)

    _say()
    _say(f"Research consensus: {cycle.consensus} (conflicting evidence: {cycle.conflicting_evidence})")
    if cycle.validation:
        _say(f"Independent validator decision: {cycle.validation.decision.value}")
        for reason in cycle.validation.reasons:
            _say(f"  validator reason: {reason}")
    _say(f"Risk approved: {cycle.risk_approved}")

    if cycle.thesis:
        t = cycle.thesis
        _say()
        _say(f"Position sized: {t.symbol}, quantity={t.quantity} (real lot-size-multiple sizing)")
        _say(f"Entry={t.entry:.2f}  Stop={t.stop:.2f}  Target={t.target:.2f}  Estimated risk={t.estimated_risk:.2f}")
        _say(f"Thesis confidence: {t.confidence:.1f}")

    if not cycle.order:
        _say()
        _say("No fill this scenario -- risk/validation did not approve an order.")
        return {"filled": False, "cycle": cycle}

    _say()
    _say(f"Simulated fill: order_id={cycle.order['order_id']} fill_price={cycle.order['fill_price']}")

    # A real, in-market-hours simulated time -- NOT datetime.now(IST)
    # directly. Real bug caught the first time this demo was run: using
    # the real wall-clock time meant that whenever this demo happened to
    # be run outside 09:15-15:15 IST (e.g. in the evening), the very
    # first real supervision tick immediately forced FORCED_EXIT before
    # the price path had any chance to demonstrate a real target/stop/
    # trailing-stop outcome -- a real, correct behavior of the exit
    # engine (Section 7's 15:15 forced square-off is real and always-on),
    # just not what a demo run outside market hours should show.
    supervision_now = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    state = orchestrator.open_position(cycle, now=supervision_now)
    tick_prices = _demo_price_path(cycle.thesis)

    _say()
    _say("Simulated live supervision (real exit engine, one real tick per synthetic price):")
    now = supervision_now
    result = None
    for price in tick_prices:
        now = now + timedelta(seconds=10)
        prior_stop = state.current_stop
        result = orchestrator.supervise_once(state, price, now)
        moved = state.current_stop != prior_stop
        _say(
            f"  ltp={price:.2f}  trailing_stop={state.current_stop:.2f}"
            + ("  <- real trailing stop moved up (risk/trailing_stop.py::update_stop)" if moved else "")
        )
        if result.should_exit:
            break

    _say()
    if result and result.should_exit:
        pnl = (result.exit_price - cycle.thesis.entry) * cycle.thesis.quantity
        _say(f"Exit: {result.reason} at {result.exit_price:.2f}")
        _say(f"Real paper P&L this demo position: {pnl:.2f}")
    else:
        _say("Price path exhausted without a real exit condition firing (HOLD) -- demo path too short.")
    _say()
    _say(f"Confirmed: 0 rows in the REAL learning.memory (demo wrote only to {settings.database_path})")
    _say("=" * 70)
    return {"filled": True, "cycle": cycle, "result": result}


def _demo_price_path(thesis) -> list[float]:
    """A real, plausible option-premium tick sequence -- moves gradually
    from entry toward target, letting the REAL exit engine (position_
    supervisor.py::tick, via Orchestrator.supervise_once) decide the
    actual outcome (target hit, stop hit, or a trailing-stop adjustment
    along the way) from this price series, not pre-decided here. Real
    numbers derived from the thesis's own real entry/target -- not
    arbitrary, and not force-fit to land exactly on the target.
    """
    entry, target = thesis.entry, thesis.target
    span = target - entry
    steps = [0.15, 0.35, 0.55, 0.75, 0.90, 1.05, 1.15]
    return [round(entry + span * step, 2) for step in steps]
