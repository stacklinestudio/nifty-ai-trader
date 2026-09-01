"""Assembles the real context Orchestrator.run_cycle's research agents
expect, from live Kite data -- the piece main.py's own run_scheduled_day
docstring flagged as missing (context_provider=dict meant every real
trading day produced "no_entry" by construction, regardless of real
market conditions).

Reuses the pieces already proven working against real data on
2026-08-31/09-01: KiteMarketData.get_quote (naive-timestamp fix,
data/market_data.py), download_kite_nifty_options (1594 real instruments
parsed correctly), KiteHistoricalData.candles (real 09:15-15:29 IST minute
bars). Direction/confidence reuse intelligence/market_regime.py's classify
(same regime IndiaMarketAgent derives its own read from) and
intelligence/signal_engine.py's SignalEngine (already-tested multi-factor
formula, not a new one invented here) -- fed real sub-scores where a real
source exists, and explicit 0 (documented, not fabricated) where it
doesn't yet: volume, options-flow, global market, news. See
KNOWN_GAPS below and the accompanying report for the honest current state.

Fail-closed by construction, not by new logic: if the spot quote is
missing/stale, market_data_fresh stays False, which the existing
RiskAgent/IndependentTradeValidator checks already refuse to trade on. If
today's session doesn't yet have enough bars to form an opening range,
candidate_direction is simply never set, which SignalHunterAgent already
treats as "no candidate produced." Nothing new was added to make this
safe -- the safety was already there; this only decides whether to feed
it real inputs or leave them absent.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from config import IST, Settings
from data.calendar import NseCalendar
from data.historical import KiteHistoricalData
from data.instruments import OptionInstrument, download_kite_nifty_options
from data.market_data import KiteMarketData, parse_kite_timestamp, validate_quote
from data.option_chain import OptionQuote
from intelligence.market_regime import Regime, classify
from intelligence.signal_engine import SignalEngine
from intelligence.technicals import feature_frame
from monitoring.logger import configure_logger
from strategy.orb import breakout_direction, opening_range

logger = configure_logger(__name__)

# Confirmed live against the real Kite API (2026-08-31/09-01, raw
# kite.quote(["NSE:NIFTY 50"]) response): NSE:NIFTY 50's instrument_token.
# A stable, well-known constant for this specific index, not a guess.
NIFTY_INDEX_TOKEN = 256265
NIFTY_INDEX_SYMBOL = "NSE:NIFTY 50"

OPENING_RANGE_MINUTES = 5
OPTION_STRIKE_WINDOW = 500.0  # points either side of spot

# Sub-scores fed into SignalEngine.evaluate() with no real live source yet.
# Documented here, not silently defaulted, so a future reader (or a later
# session) can see exactly what's still missing rather than needing to
# re-derive it -- see the accompanying report for the same list.
KNOWN_GAPS = (
    "volume",  # no intraday volume-profile signal computed yet
    "option",  # OI buildup (intelligence/oi_buildup.py) is wired as informational evidence
    #            only (agents/trading_agents.py::OptionsAgent), not yet a SignalEngine input
    "global_score",  # data/global_market.py::GlobalMarketProvider.snapshot() has no live
    #                   provider implementation; FII/DII (data/fii_dii.py) has no live fetcher
    "news",  # NewsAgent already handles real news directly when news_items is populated;
    #           this context builder doesn't have a live news source to populate it with
)

# IMPORTANT, verified by test (tests/test_live_context.py): with only
# "technical" and "opening" backed by real data and the four KNOWN_GAPS
# above fixed at 0, SignalEngine.evaluate()'s formula caps achievable
# confidence at roughly 54-59 (technical maxes at 75, opening at 100,
# weighted 0.35/0.25, plus a ~7.5 baseline from the zeroed global/news
# terms) -- this can NEVER cross the default signal_threshold (75), no
# matter how clean the real breakout is. This is not a bug in the wiring;
# SignalEngine's formula was designed assuming more live sub-signals than
# are currently connected. Until more of KNOWN_GAPS is wired to real data,
# or signal_threshold is deliberately lowered (a risk-relevant decision
# needing explicit sign-off, not a silent change made here), candidates
# will structurally almost never form even on a genuine breakout.


def _select_option_universe(
    instruments: list[OptionInstrument], spot: float, today: date
) -> list[OptionInstrument]:
    valid_expiries = sorted({i.expiry for i in instruments if i.expiry >= today})
    if not valid_expiries:
        return []
    nearest_expiry = valid_expiries[0]
    return [
        i
        for i in instruments
        if i.expiry == nearest_expiry and abs(i.strike - spot) <= OPTION_STRIKE_WINDOW
    ]


def fetch_option_quotes(
    kite: object, instruments: list[OptionInstrument]
) -> list[OptionQuote]:
    """Batches instruments into one kite.quote() call, parsing the response
    the same way KiteMarketData.get_quote does for the index (shared
    parse_kite_timestamp). NOT yet verified against a real live option
    quote this session -- only the index quote and the instrument list
    were (market was closed / token unavailable when this was written).
    The `oi` field name for open interest matches documented Kite Connect
    behavior but is unconfirmed against a real option response; flagged in
    the accompanying report, not silently assumed correct.
    """
    if not instruments:
        return []
    by_symbol = {f"NFO:{i.symbol}": i for i in instruments}
    raw = kite.quote(list(by_symbol.keys()))
    quotes = []
    for symbol, payload in raw.items():
        instrument = by_symbol.get(symbol)
        if instrument is None:
            continue
        try:
            timestamp = parse_kite_timestamp(payload)
        except ValueError as exc:
            logger.warning("option_quote_bad_timestamp symbol=%s error=%s", symbol, exc)
            continue
        depth = payload.get("depth", {})
        buys, sells = depth.get("buy", []), depth.get("sell", [])
        quotes.append(
            OptionQuote(
                instrument=instrument,
                ltp=float(payload.get("last_price", 0)),
                timestamp=timestamp,
                bid=float(buys[0]["price"]) if buys else None,
                ask=float(sells[0]["price"]) if sells else None,
                volume=payload.get("volume"),
                open_interest=payload.get("oi"),
            )
        )
    return quotes


def _technical_features(candles: pd.DataFrame) -> dict[str, float]:
    latest = feature_frame(candles).iloc[-1]

    def _num(name: str) -> float:
        value = latest.get(name)
        return float(value) if pd.notna(value) else 0.0

    return {
        "ema_fast": _num("ema_fast"),
        "ema_slow": _num("ema_slow"),
        "close": _num("close"),
        "vwap": _num("vwap"),
        "atr": _num("atr"),
        "momentum": _num("momentum"),
    }


def _gap_pct(candles: pd.DataFrame, today: date) -> float:
    todays = candles[candles.index.date == today]
    prior = candles[candles.index.date < today]
    if todays.empty or prior.empty:
        return 0.0
    prior_close = float(prior.iloc[-1].close)
    if prior_close <= 0:
        return 0.0
    return (float(todays.iloc[0].open) - prior_close) / prior_close


def _add_candidate(
    context: dict[str, Any],
    candles: pd.DataFrame,
    features: dict[str, float],
    signal_threshold: float,
) -> None:
    today = candles.index[-1].date()
    todays = candles[candles.index.date == today]
    if len(todays) <= OPENING_RANGE_MINUTES:
        return  # not enough of today's session yet -- correctly no candidate, not a guess

    regime = classify(features, context.get("gap_pct", 0.0))
    orb_direction = breakout_direction(todays, OPENING_RANGE_MINUTES)
    high, low = opening_range(todays, OPENING_RANGE_MINUTES)

    regime_direction = (
        "CALL"
        if regime in {Regime.TREND_UP, Regime.GAP_UP}
        else "PUT"
        if regime in {Regime.TREND_DOWN, Regime.GAP_DOWN}
        else None
    )
    if regime_direction is None:
        return  # UNCERTAIN/RANGE/HIGH_VOLATILITY/LOW_VOLATILITY regime -- no directional bias to trade

    # opening score: reward a real ORB breakout confirming the same
    # direction the regime implies, penalize a contradicting breakout,
    # neutral if the opening range hasn't broken either way yet.
    opening_score = (
        80.0
        if orb_direction == regime_direction
        else 20.0
        if orb_direction != "NO_TRADE"
        else 50.0
    )
    # Same bullish/bearish read TechnicalAgent computes itself -- reused,
    # not reinvented, for the "technical" sub-score.
    bullish = features["ema_fast"] > features["ema_slow"] and features["close"] > features["vwap"]
    technical_score = 75.0 if (bullish and regime_direction == "CALL") or (
        not bullish and regime_direction == "PUT"
    ) else 45.0
    volatility_ratio = features["atr"] / features["close"] if features["close"] else 0.0
    risk_penalty = 25.0 if volatility_ratio > 0.008 else 0.0

    signal = SignalEngine(threshold=signal_threshold).evaluate(
        timestamp=datetime.now(IST),
        regime=regime,
        technical=technical_score,
        opening=opening_score,
        volume=0.0,
        option=0.0,
        global_score=0.0,
        news=0.0,
        risk_penalty=risk_penalty,
    )
    if signal.direction not in {"CALL", "PUT"}:
        # SignalEngine itself vetoed -- most likely below signal_threshold,
        # since only technical+opening are backed by real data right now
        # (volume/option/global/news are 0, a known gap -- see KNOWN_GAPS).
        # Logged, not silent: this is a real computed signal that didn't
        # clear the bar, not "nothing happened."
        logger.info(
            "live_context_signal_below_threshold regime=%s confidence=%.1f threshold=%.1f",
            regime.value,
            signal.confidence,
            signal_threshold,
        )
        return

    spread = max(high - low, 0.01)
    if signal.direction == "CALL":
        entry_zone, stop_zone, target_zone = (high, high + spread * 0.1), (low, low), (
            high + spread * 1.5,
            high + spread * 2.0,
        )
    else:
        entry_zone, stop_zone, target_zone = (low - spread * 0.1, low), (high, high), (
            low - spread * 2.0,
            low - spread * 1.5,
        )

    context["candidate_direction"] = signal.direction
    context["candidate_confidence"] = signal.confidence
    context["setup_type"] = "OPENING_RANGE_BREAKOUT"
    context["entry_zone"] = entry_zone
    context["stop_zone"] = stop_zone
    context["target_zone"] = target_zone
    context["candidate_evidence"] = [
        f"regime={regime.value} implies {regime_direction}",
        f"opening range {low:.2f}-{high:.2f}, ORB read={orb_direction}",
        f"SignalEngine confidence={signal.confidence:.1f} (threshold {signal_threshold})",
    ]


def assemble_context(
    candles: pd.DataFrame,
    option_quotes: list[OptionQuote],
    spot: float,
    now: datetime,
    market_open: bool,
    settings: Settings,
) -> dict[str, Any]:
    """Pure context assembly from already-fetched data -- no I/O, no Kite
    calls. Both build_live_context (fetches live) and
    backtest/daily_backtest.py (fetches historical) call this, so entry
    logic is identical between live and backtest -- not reimplemented
    twice, and not a place a backtest-only shortcut could quietly diverge
    from what actually runs live.
    """
    context: dict[str, Any] = {
        "market_open": market_open,
        "market_data_fresh": True,
        "global_context": [],  # no live provider wired -- GlobalResearchAgent fails closed on this
        "news_items": [],  # no live news source wired -- NewsAgent fails closed on this
        "spot": spot,
        "max_position_value": settings.max_position_value,
        "option_quotes": option_quotes,
    }
    if candles.empty:
        return context
    features = _technical_features(candles)
    context["features"] = features
    context["atr"] = features["atr"]
    context["gap_pct"] = _gap_pct(candles, now.date())
    _add_candidate(context, candles, features, settings.signal_threshold)
    return context


def build_live_context(
    settings: Settings, kite: object, calendar: NseCalendar, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(IST)
    market_open = calendar.is_market_open(now)
    not_fresh: dict[str, Any] = {
        "market_open": market_open,
        "market_data_fresh": False,
        "global_context": [],
        "news_items": [],
    }

    try:
        quote = KiteMarketData(kite).get_quote(NIFTY_INDEX_SYMBOL)
        validate_quote(quote, now, settings.stale_data_seconds)
    except Exception as exc:  # noqa: BLE001 - any failure here means "no fresh spot data," fail closed below.
        logger.warning("live_context_spot_quote_unavailable error=%s", exc)
        return not_fresh

    try:
        candles = KiteHistoricalData(kite).candles(
            NIFTY_INDEX_TOKEN, now - timedelta(days=10), now, interval="minute"
        )
    except Exception as exc:  # noqa: BLE001 - no candles means no technical/ORB read; still return what we have.
        logger.warning("live_context_candles_unavailable error=%s", exc)
        candles = pd.DataFrame()

    try:
        instruments = download_kite_nifty_options(kite)
    except Exception as exc:  # noqa: BLE001 - no option chain means OptionsAgent correctly finds nothing tradeable.
        logger.warning("live_context_instruments_unavailable error=%s", exc)
        instruments = []
    universe = _select_option_universe(instruments, quote.ltp, now.date())
    option_quotes = fetch_option_quotes(kite, universe) if universe else []

    return assemble_context(candles, option_quotes, quote.ltp, now, market_open, settings)
