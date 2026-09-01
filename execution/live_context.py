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
doesn't yet. See KNOWN_GAPS below and the accompanying report for the
honest current state.

Brief 4 wired 4 of SignalEngine's 7 inputs that were previously hardcoded
to 0.0 -- volume (real candle volume, already flowing through this same
data, just never read), option (agents/trading_agents.py::OptionsAgent's
already-built OI-buildup detection, reused via intelligence/oi_buildup.py
directly since a candidate doesn't exist yet at this point in the
pipeline), global_score and news (GlobalResearchAgent/NewsAgent reused
directly for the same reason). technical/opening/risk_penalty were
already real before this brief. Each newly-wired input still degrades to
0.0 (logged, not fabricated) when its underlying real data is genuinely
unavailable for a given day -- see each _*_score function's own
docstring for exactly what "unavailable" means for that input.

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

from agents.research_agents import GlobalResearchAgent, NewsAgent
from config import IST, Settings
from data.calendar import NseCalendar
from data.historical import KiteHistoricalData
from data.instruments import OptionInstrument, download_kite_nifty_options
from data.market_data import KiteMarketData, parse_kite_timestamp, validate_quote
from data.option_chain import OptionQuote
from intelligence.market_regime import Regime, classify
from intelligence.oi_buildup import detect_buildup
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

# As of Brief 4, all 7 of SignalEngine.evaluate()'s inputs are wired to a
# real computation -- but that does not mean all 7 have real DATA on every
# day. Two independent gaps remain, both explicit/logged, never fabricated:
KNOWN_GAPS = (
    "option",  # _option_score requires a *previous* option-chain OI snapshot
    #             (intelligence/oi_buildup.py::detect_buildup needs two
    #             snapshots to detect a change). No live source persists a
    #             prior snapshot yet -- build_live_context always passes
    #             previous_option_quotes=[], so this stays 0.0/"UNAVAILABLE"
    #             until that's built. Wiring is real and tested; the specific
    #             data feed for "yesterday's closing OI" is not.
    "global_score",  # _global_score correctly computes from whatever
    #                   context["global_context"] holds, but
    #                   data/global_market.py::GlobalMarketProvider.snapshot()
    #                   has no live provider implementation, and
    #                   build_live_context always passes global_context=[] --
    #                   so this is real wiring over still-empty real data.
    "news",  # same shape as global_score: _news_score is real, but
    #           build_live_context always passes news_items=[] since no live
    #           news source is wired -- see NewsAgent's own docstring, which
    #           already handles real news correctly whenever it's supplied.
)


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


def _volume_score(candles: pd.DataFrame, today: date, opening_minutes: int) -> float:
    """Compares today's opening-range volume to the average opening-range
    volume over the prior trading days already present in `candles` --
    apples-to-apples (same time-of-day window each day), not "cumulative
    volume so far," which would trivially grow through the day regardless
    of real participation. 50.0 means "about average"; higher/lower is a
    real read on unusually high/low early participation. Returns 0.0 (not
    a guess) when there is no volume column or no full prior day to
    compare against -- e.g. the first day of a backtest window.
    """
    if "volume" not in candles.columns:
        return 0.0
    todays = candles[candles.index.date == today]
    prior_days = sorted({d for d in candles.index.date if d < today})
    if not prior_days:
        return 0.0
    todays_volume = float(todays.iloc[:opening_minutes]["volume"].sum())
    prior_volumes = [
        float(candles[candles.index.date == day].iloc[:opening_minutes]["volume"].sum())
        for day in prior_days
        if len(candles[candles.index.date == day]) >= opening_minutes
    ]
    if not prior_volumes:
        return 0.0
    avg_prior_volume = sum(prior_volumes) / len(prior_volumes)
    if avg_prior_volume <= 0:
        return 0.0
    ratio = todays_volume / avg_prior_volume
    return max(0.0, min(100.0, ratio * 50.0))


def _option_score(
    option_quotes: list[OptionQuote],
    previous_option_quotes: list[OptionQuote],
    regime_direction: str,
) -> tuple[float, str]:
    """Reuses agents/trading_agents.py::OptionsAgent's own OI-buildup
    detection (intelligence/oi_buildup.py::detect_buildup) directly --
    OptionsAgent itself can't be called with a candidate here, since no
    candidate exists yet at this point in the pipeline, but detect_buildup
    doesn't need one. Real OI-change data when a previous snapshot is
    available; explicit 0.0/"UNAVAILABLE" (not a guess) when it isn't --
    see KNOWN_GAPS for why no live "previous snapshot" source exists yet.
    """
    buildup = detect_buildup(option_quotes, previous_option_quotes)
    if buildup.bias == "UNAVAILABLE":
        return 0.0, buildup.reasons[0]
    if buildup.bias == "BALANCED":
        return 40.0, buildup.reasons[0]
    aligned = (buildup.bias == "CALL_BUILDUP" and regime_direction == "CALL") or (
        buildup.bias == "PUT_BUILDUP" and regime_direction == "PUT"
    )
    return (75.0 if aligned else 20.0), buildup.reasons[0]


def _alignment_score(direction: str, regime_direction: str, confidence: float) -> float:
    """Maps a BULLISH/BEARISH/NEUTRAL/UNKNOWN direction + confidence into
    the signed -100..100 value SignalEngine's global_score/news inputs
    expect (its formula does `(value + 100) * weight`, so positive means
    "supports this candidate's direction," negative means "contradicts
    it" -- the same alignment concept SignalHunterAgent's own separate
    news nudge already uses). NEUTRAL/UNKNOWN carries no directional
    information, so it contributes exactly 0.0, never a guessed lean.
    """
    if direction == "BULLISH":
        return confidence if regime_direction == "CALL" else -confidence
    if direction == "BEARISH":
        return confidence if regime_direction == "PUT" else -confidence
    return 0.0


def _global_score(context: dict[str, Any], regime_direction: str) -> tuple[float, str]:
    """Reuses GlobalResearchAgent directly rather than reimplementing its
    averaging logic. Real whenever context["global_context"] holds real
    ContextValue entries; 0.0/"UNKNOWN" (GlobalResearchAgent's own,
    unmodified fail-closed behavior) when it's empty -- currently always,
    since no live global-market provider is wired (KNOWN_GAPS)."""
    review = GlobalResearchAgent().run({"global_context": context.get("global_context", [])})
    direction = review.data.get("global_direction", "UNKNOWN")
    return _alignment_score(direction, regime_direction, review.confidence), direction


def _news_score(context: dict[str, Any], regime_direction: str) -> tuple[float, str]:
    """Reuses NewsAgent directly -- the same real sentiment classification
    Brief 3 Part C already fixed (data.news.aggregate_sentiment), not a
    second implementation. Real whenever context["news_items"] holds real
    NewsItem entries; 0.0/"UNKNOWN" (NewsAgent's own fail-closed behavior)
    when it's empty -- currently always, since no live news source is
    wired here (KNOWN_GAPS). This is separate from SignalHunterAgent's own
    ±5%-capped news nudge, which only ever applies after a candidate
    already exists; this is what lets news evidence affect whether one
    forms in the first place.
    """
    review = NewsAgent().run({"news_items": context.get("news_items", [])})
    direction = review.data.get("direction", "UNKNOWN")
    return _alignment_score(direction, regime_direction, review.confidence), direction


def _add_candidate(
    context: dict[str, Any],
    candles: pd.DataFrame,
    features: dict[str, float],
    signal_threshold: float,
    option_quotes: list[OptionQuote],
    previous_option_quotes: list[OptionQuote],
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

    volume_score = _volume_score(candles, today, OPENING_RANGE_MINUTES)
    option_score, option_reason = _option_score(option_quotes, previous_option_quotes, regime_direction)
    global_score, global_direction = _global_score(context, regime_direction)
    news_score, news_direction = _news_score(context, regime_direction)

    signal = SignalEngine(threshold=signal_threshold).evaluate(
        timestamp=datetime.now(IST),
        regime=regime,
        technical=technical_score,
        opening=opening_score,
        volume=volume_score,
        option=option_score,
        global_score=global_score,
        news=news_score,
        risk_penalty=risk_penalty,
    )
    if signal.direction not in {"CALL", "PUT"}:
        # SignalEngine itself vetoed -- a real computed signal that didn't
        # clear signal_threshold, not "nothing happened." All 7 inputs are
        # real computations as of Brief 4, though option/global/news may
        # still be 0.0 on days their underlying real data is unavailable
        # (see KNOWN_GAPS) -- logged explicitly so that's distinguishable
        # from "computed and turned out low."
        logger.info(
            "live_context_signal_below_threshold regime=%s confidence=%.1f threshold=%.1f "
            "volume=%.1f option=%.1f(%s) global=%.1f(%s) news=%.1f(%s)",
            regime.value,
            signal.confidence,
            signal_threshold,
            volume_score,
            option_score,
            option_reason,
            global_score,
            global_direction,
            news_score,
            news_direction,
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
        (
            f"volume={volume_score:.1f}, option={option_score:.1f} ({option_reason}), "
            f"global={global_score:.1f} ({global_direction}), news={news_score:.1f} ({news_direction})"
        ),
        f"SignalEngine confidence={signal.confidence:.1f} (threshold {signal_threshold})",
    ]


def assemble_context(
    candles: pd.DataFrame,
    option_quotes: list[OptionQuote],
    spot: float,
    now: datetime,
    market_open: bool,
    settings: Settings,
    previous_option_quotes: list[OptionQuote] | None = None,
) -> dict[str, Any]:
    """Pure context assembly from already-fetched data -- no I/O, no Kite
    calls. Both build_live_context (fetches live) and
    backtest/daily_backtest.py (fetches historical) call this, so entry
    logic is identical between live and backtest -- not reimplemented
    twice, and not a place a backtest-only shortcut could quietly diverge
    from what actually runs live.

    previous_option_quotes defaults to [] (not fabricated) -- no live
    source persists a prior option-chain snapshot yet (KNOWN_GAPS), so
    _option_score correctly reads this as "unavailable" rather than a
    real absence of buildup. A caller that does have a real prior
    snapshot (e.g. a future backtest fed real day-over-day option data)
    can pass it here and OI-buildup scoring becomes real for that day.
    """
    previous_option_quotes = previous_option_quotes or []
    context: dict[str, Any] = {
        "market_open": market_open,
        "market_data_fresh": True,
        "global_context": [],  # no live provider wired -- GlobalResearchAgent fails closed on this
        "news_items": [],  # no live news source wired -- NewsAgent fails closed on this
        "spot": spot,
        "max_position_value": settings.max_position_value,
        "option_quotes": option_quotes,
        "previous_option_quotes": previous_option_quotes,
    }
    if candles.empty:
        return context
    features = _technical_features(candles)
    context["features"] = features
    context["atr"] = features["atr"]
    context["gap_pct"] = _gap_pct(candles, now.date())
    _add_candidate(
        context, candles, features, settings.signal_threshold, option_quotes, previous_option_quotes
    )
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
