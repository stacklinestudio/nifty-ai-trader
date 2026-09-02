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

Brief 5 made two of those four inputs receive real DATA, not just real
wiring: `volume` now prefers real option-contract volume
(_combined_volume_score/_option_volume_score) over index candle volume,
which is structurally always 0 for NIFTY 50 on Kite (an index carries no
traded volume of its own); `option`'s OI-buildup detection becomes real
once a previous option-chain snapshot exists, which main.py's live path
now persists and retrieves via storage.database.Database.save_option_
chain_snapshot/latest_option_chain_snapshot (reusing the schema's
pre-existing, previously-unused `snapshots` table) -- the mechanism a
fresh-process-per-day live deployment needs to carry state across days.
`global_score`/`news` remain real wiring over still-empty real data --
Brief 5 researched, but deliberately did not pick or wire, a live
external source for either (see the accompanying report's Part C).

Brief 7 built real detection for 5 setup types designed in the original
spec but never implemented beyond OPENING_RANGE_BREAKOUT: VWAP_BREAKOUT,
VWAP_REJECTION, MOMENTUM_CONTINUATION, TREND_CONTINUATION, SUPPORT_
RESISTANCE_REACTION (see each _*_setup function). _select_setup wires 3
of them (the trend-favored ones) into real candidate formation alongside
OPENING_RANGE_BREAKOUT; VWAP_REJECTION/SUPPORT_RESISTANCE_REACTION are
real and independently tested but NOT wired in -- SignalEngine.evaluate()
itself hardcodes `direction` from `regime` (trend/gap regimes only), so a
range-favored setup's own real direction can never reach a candidate
without changing that shared, already-tested core piece too. See
_select_setup's docstring and the accompanying report.

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

# Brief 6 Part A: how long after the real session open an open-window
# setup stays eligible -- distinct from OPENING_RANGE_MINUTES above, which
# is how many bars *define* the opening range itself, not how long a
# breakout of it stays a "fresh" signal. "Roughly the first 15-30 minutes"
# per the brief; 30 chosen as the more permissive end so a real breakout
# forming late in a slow opening range isn't cut off prematurely.
OPEN_WINDOW_MINUTES = 30

# Confirmed against strategy/regime_selector.py's own setup-type strings --
# the only other place in this codebase that references setup_type by
# name. NOT SignalHunterAgent: it has no setup-detection logic of its own,
# it only *weights* whatever setup_type execution/live_context.py already
# put in the candidate. As of Brief 6, `_add_candidate` below is still the
# ONLY code that ever actually produces a setup_type, and it only ever
# produces "OPENING_RANGE_BREAKOUT" -- the other 8 strings below are
# recognized by regime_selector.py's weighting table but have no detection
# logic anywhere in this codebase yet. Gating them here is forward
# scaffolding for whichever gets implemented next, not evidence they're
# active today.
OPEN_WINDOW_SETUPS = frozenset(
    {
        "OPENING_RANGE_BREAKOUT",
        "OPENING_RANGE_REJECTION",
        "GAP_CONTINUATION",
        "GAP_REVERSAL",
    }
)
# Brief 6's own suggested "valid all day" list also named "volatility
# expansion" as a setup type; no such setup_type string exists anywhere in
# this codebase -- strategy/regime_selector.py instead has a *volatility_
# regime* value "HIGH" it calls "high volatility expansion" internally, a
# different concept (a market-state read, not a setup type). Not included
# here since it isn't a real setup_type; VWAP_BREAKOUT/VWAP_REJECTION/
# MOMENTUM_CONTINUATION/TREND_CONTINUATION/SUPPORT_RESISTANCE_REACTION are
# real regime_selector.py setup-type strings and are listed.
ALL_DAY_SETUPS = frozenset(
    {
        "VWAP_BREAKOUT",
        "VWAP_REJECTION",
        "MOMENTUM_CONTINUATION",
        "TREND_CONTINUATION",
        "SUPPORT_RESISTANCE_REACTION",
    }
)

# As of Brief 4, all 7 of SignalEngine.evaluate()'s inputs are wired to a
# real computation -- but that does not mean all 7 have real DATA on every
# day. As of Brief 5, `volume` and `option` also have a real live DATA path
# (see module docstring); `global_score`/`news` remain wiring over
# still-empty real data, both explicit/logged, never fabricated:
KNOWN_GAPS = (
    "option",  # _option_score is real whenever a previous option-chain
    #             snapshot exists -- main.py's live path now persists and
    #             retrieves one via storage.database.Database (Brief 5 Part
    #             B). Still correctly 0.0/"UNAVAILABLE" the first time this
    #             feature ever runs (no prior snapshot to retrieve yet), and
    #             in backtest/daily_backtest.py unless real per-day option
    #             chain data has actually been fetched for that window
    #             (option_quotes_by_day) -- neither is a bug, both are the
    #             same honest "no real data yet" state this system always
    #             surfaces rather than fabricating.
    "global_score",  # _global_score correctly computes from whatever
    #                   context["global_context"] holds, but
    #                   data/global_market.py::GlobalMarketProvider.snapshot()
    #                   has no live provider implementation, and
    #                   build_live_context always passes global_context=[] --
    #                   so this is real wiring over still-empty real data.
    #                   Brief 5 researched real provider options (see the
    #                   accompanying report's Part C) but deliberately did
    #                   not pick one or wire it in.
    "news",  # same shape as global_score: _news_score is real, but
    #           build_live_context always passes news_items=[] since no live
    #           news source is wired -- see NewsAgent's own docstring, which
    #           already handles real news correctly whenever it's supplied.
    #           Same Brief 5 Part C research-not-wired treatment as
    #           global_score.
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


def _option_volume_score(
    option_quotes: list[OptionQuote], previous_option_quotes: list[OptionQuote]
) -> float:
    """Real option-contract volume (OptionQuote.volume, already fetched
    live in fetch_option_quotes) compared to the same real total from the
    prior real snapshot (see Part B: persisted via
    storage.database.Database.save_option_chain_snapshot), using the same
    ratio*50-capped-at-100 shape as _volume_score -- "about average"
    participation reads 50, real above/below-average reads higher/lower.
    Not a like-for-like strike match (today's near-ATM universe shifts
    with spot) -- an aggregate real-participation proxy across the fetched
    near-ATM/near-week universe on both sides, same spirit as _volume_score
    comparing aggregate opening-range volume day over day rather than
    matching individual candles.

    Returns 0.0 -- not a fabricated "average" -- when there's no current
    quote, no previous snapshot, or every volume field on one side is
    genuinely null (not yet confirmed against a real live option response,
    per last week's honest disclosure): a null volume field is
    "unavailable," never silently treated as zero participation.
    """
    if not option_quotes or not previous_option_quotes:
        return 0.0
    today_values = [q.volume for q in option_quotes if q.volume is not None]
    prior_values = [q.volume for q in previous_option_quotes if q.volume is not None]
    if not today_values or not prior_values:
        return 0.0
    prior_total = float(sum(prior_values))
    if prior_total <= 0:
        return 0.0
    ratio = float(sum(today_values)) / prior_total
    return max(0.0, min(100.0, ratio * 50.0))


def _combined_volume_score(
    candles: pd.DataFrame,
    today: date,
    opening_minutes: int,
    option_quotes: list[OptionQuote],
    previous_option_quotes: list[OptionQuote],
) -> tuple[float, str]:
    """Prefers real option-contract volume over index candle volume as
    SignalEngine's `volume` input: NIFTY 50 index candle volume from Kite
    is structurally always 0 (confirmed against the real 42-day captured
    dataset and the real live index quote fixtures elsewhere in this
    codebase -- an index has no traded volume of its own, only its
    constituents and derivatives do), so _volume_score alone can never
    move on real NIFTY index data. Real option-contract volume is real
    participation data for the actual tradeable instrument. Falls back to
    the index-candle score only when no option-volume comparison is yet
    possible (no current option quotes, or no previous snapshot yet --
    e.g. the very first day this feature runs) -- kept as the general
    mechanism rather than deleted, since it is real and correct for any
    future instrument whose own candle volume isn't structurally zero.
    """
    option_score = _option_volume_score(option_quotes, previous_option_quotes)
    if option_quotes and previous_option_quotes:
        return option_score, "option_contract_volume"
    index_score = _volume_score(candles, today, opening_minutes)
    return index_score, "index_candle_volume(no_prior_option_snapshot)"


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


def _setup_eligible_now(setup_type: str, now: datetime, session_open: datetime) -> bool:
    """Open-window setups (OPEN_WINDOW_SETUPS) are only eligible within
    OPEN_WINDOW_MINUTES of the real session open -- Brief 6's periodic
    re-scanning means this can now be checked hours after open, and
    without this gate a stale opening range would keep re-reporting the
    same old structure as if it were a fresh breakout every scan. All-day
    setups, and any setup_type not recognized in either set, are eligible
    on every scan -- the safer default for an unrecognized name is NOT to
    silently block it.
    """
    if setup_type not in OPEN_WINDOW_SETUPS:
        return True
    return now <= session_open + timedelta(minutes=OPEN_WINDOW_MINUTES)


def _clamp_score(raw: float) -> float:
    return max(20.0, min(80.0, raw))


def _vwap_breakout_setup(features: dict[str, float]) -> tuple[str | None, float, str]:
    """Real detection: price on the far side of the real, session-anchored
    VWAP (intelligence/technicals.py -- fixed this brief to never silently
    degrade to a vacuous 0.0) with momentum confirming the same direction
    -- a genuine breakout away from the session's own volume-weighted (or,
    when real volume is genuinely zero, typical-price) center, not a
    guessed threshold. Score reflects real distance from vwap in ATR
    units -- how decisive the break is -- the same 20-80 clamp every
    setup here uses.
    """
    close, vwap, momentum, atr = (
        features["close"],
        features["vwap"],
        features["momentum"],
        features["atr"],
    )
    if close <= 0 or vwap <= 0 or atr <= 0:
        return None, 0.0, "insufficient data for a VWAP read"
    distance_atr = abs(close - vwap) / atr
    if close > vwap and momentum > 0:
        return (
            "CALL",
            _clamp_score(40.0 + distance_atr * 20.0),
            f"close {close:.2f} above session vwap {vwap:.2f} ({distance_atr:.2f} ATR), momentum positive",
        )
    if close < vwap and momentum < 0:
        return (
            "PUT",
            _clamp_score(40.0 + distance_atr * 20.0),
            f"close {close:.2f} below session vwap {vwap:.2f} ({distance_atr:.2f} ATR), momentum negative",
        )
    return None, 0.0, "no VWAP breakout structure present"


def _vwap_rejection_setup(
    latest_bar: pd.Series, features: dict[str, float]
) -> tuple[str | None, float, str]:
    """Real detection: this bar's real high/low crossed the session VWAP
    but closed back on the other side -- a genuine intrabar rejection, not
    a guessed pattern. Mean-reversion, the opposite read of
    _vwap_breakout_setup on the exact same real level. NOT currently wired
    into _select_setup below -- see that function's docstring for why.
    """
    vwap, atr = features["vwap"], features["atr"]
    high, low, close = float(latest_bar.high), float(latest_bar.low), float(latest_bar.close)
    if vwap <= 0 or atr <= 0:
        return None, 0.0, "insufficient data for a VWAP read"
    if high > vwap and close < vwap:
        wick_atr = (high - vwap) / atr
        return (
            "PUT",
            _clamp_score(40.0 + wick_atr * 20.0),
            (f"high {high:.2f} pierced session vwap {vwap:.2f} but closed back below at {close:.2f} "
            f"({wick_atr:.2f} ATR wick)"),
        )
    if low < vwap and close > vwap:
        wick_atr = (vwap - low) / atr
        return (
            "CALL",
            _clamp_score(40.0 + wick_atr * 20.0),
            (f"low {low:.2f} pierced session vwap {vwap:.2f} but closed back above at {close:.2f} "
            f"({wick_atr:.2f} ATR wick)"),
        )
    return None, 0.0, "no VWAP rejection structure present"


def _momentum_continuation_setup(features: dict[str, float]) -> tuple[str | None, float, str]:
    """Real detection: 5-bar momentum (intelligence/technicals.py's
    momentum = close.pct_change(5)) outpacing the session's own real
    ATR-normalized typical move, with EMA trend alignment confirming --
    a real volatility-relative threshold, not an arbitrary fixed one, so
    what counts as "significant" adapts to how volatile this real session
    actually is.
    """
    close, momentum, atr, ema_fast, ema_slow = (
        features["close"],
        features["momentum"],
        features["atr"],
        features["ema_fast"],
        features["ema_slow"],
    )
    if close <= 0 or atr <= 0:
        return None, 0.0, "insufficient data for a momentum read"
    threshold = atr / close
    if momentum > threshold and ema_fast > ema_slow:
        ratio = momentum / threshold
        return (
            "CALL",
            _clamp_score(40.0 + (ratio - 1.0) * 20.0),
            (f"5-bar momentum {momentum:.4f} exceeds ATR-relative threshold {threshold:.4f} "
            f"({ratio:.2f}x), ema_fast>ema_slow"),
        )
    if momentum < -threshold and ema_fast < ema_slow:
        ratio = abs(momentum) / threshold
        return (
            "PUT",
            _clamp_score(40.0 + (ratio - 1.0) * 20.0),
            (f"5-bar momentum {momentum:.4f} exceeds ATR-relative threshold -{threshold:.4f} "
            f"({ratio:.2f}x), ema_fast<ema_slow"),
        )
    return None, 0.0, "no significant momentum continuation present"


def _trend_continuation_setup(candles: pd.DataFrame, lookback: int = 10) -> tuple[str | None, float, str]:
    """Real detection: EMA fast/slow ordering has held for the last
    `lookback` real bars, not just the current one -- a sustained trend,
    distinct from _momentum_continuation_setup's "fresh acceleration"
    read. Needs the full feature history, not just the latest bar's
    values, so recomputes feature_frame locally over the same real
    candles _add_candidate already has (a small, bounded recomputation
    over already-fetched data, not a new data source).
    """
    feats = feature_frame(candles)
    if len(feats) < lookback:
        return None, 0.0, "insufficient history for a trend-persistence read"
    recent = feats.iloc[-lookback:]
    atr = float(recent["atr"].iloc[-1])
    if atr <= 0 or pd.isna(atr):
        return None, 0.0, "insufficient data for a trend-persistence read"
    if (recent["ema_fast"] > recent["ema_slow"]).all():
        spread = float(recent["ema_fast"].iloc[-1] - recent["ema_slow"].iloc[-1])
        return (
            "CALL",
            _clamp_score(40.0 + (spread / atr) * 20.0),
            (f"ema_fast>ema_slow held for the last {lookback} real bars, spread {spread:.2f} "
            f"({spread / atr:.2f} ATR)"),
        )
    if (recent["ema_slow"] > recent["ema_fast"]).all():
        spread = float(recent["ema_slow"].iloc[-1] - recent["ema_fast"].iloc[-1])
        return (
            "PUT",
            _clamp_score(40.0 + (spread / atr) * 20.0),
            (f"ema_slow>ema_fast held for the last {lookback} real bars, spread {spread:.2f} "
            f"({spread / atr:.2f} ATR)"),
        )
    return None, 0.0, "trend not sustained across the full lookback window"


def _support_resistance_reaction_setup(
    candles: pd.DataFrame, today: date, features: dict[str, float]
) -> tuple[str | None, float, str]:
    """Real detection: the prior real trading day's own high/low as the
    support/resistance level -- a real, already-observed price level, not
    a fabricated one -- with the latest real bar wicking through it and
    closing back on the origin side, the same rejection mechanics as
    _vwap_rejection_setup applied to a different real reference level.
    NOT currently wired into _select_setup below -- see that function's
    docstring for why.
    """
    prior = candles[candles.index.date < today]
    if prior.empty:
        return None, 0.0, "no prior real trading day to derive a level from"
    prior_day = max({d for d in prior.index.date})
    prior_bars = prior[prior.index.date == prior_day]
    resistance = float(prior_bars.high.max())
    support = float(prior_bars.low.min())
    atr = features["atr"]
    if atr <= 0:
        return None, 0.0, "insufficient data for a support/resistance read"
    latest = candles.iloc[-1]
    high, low, close = float(latest.high), float(latest.low), float(latest.close)
    if high >= resistance and close < resistance:
        wick_atr = (high - resistance) / atr
        return (
            "PUT",
            _clamp_score(40.0 + wick_atr * 20.0),
            (f"high {high:.2f} reached prior-day resistance {resistance:.2f} but closed back below "
            f"at {close:.2f} ({wick_atr:.2f} ATR wick)"),
        )
    if low <= support and close > support:
        wick_atr = (support - low) / atr
        return (
            "CALL",
            _clamp_score(40.0 + wick_atr * 20.0),
            (f"low {low:.2f} reached prior-day support {support:.2f} but closed back above at "
            f"{close:.2f} ({wick_atr:.2f} ATR wick)"),
        )
    return None, 0.0, "no support/resistance rejection structure present"


def _atr_zones(
    direction: str, close: float, atr: float
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Entry/stop/target zones anchored on real ATR -- the same
    0.1/1.0/1.5/2.0-spread-multiple proportions the opening-range-based
    zones below already use for OPENING_RANGE_BREAKOUT, applied to a
    different real "how much room this setup needs" measure, since these
    setups have no opening range of their own.
    """
    spread = max(atr, 0.01)
    if direction == "CALL":
        return (
            (close, close + spread * 0.1),
            (close - spread * 1.0, close - spread * 1.0),
            (close + spread * 1.5, close + spread * 2.0),
        )
    return (
        (close - spread * 0.1, close),
        (close + spread * 1.0, close + spread * 1.0),
        (close - spread * 2.0, close - spread * 1.5),
    )


def _select_setup(
    candles: pd.DataFrame,
    todays: pd.DataFrame,
    features: dict[str, float],
    now: datetime,
    session_open: datetime,
    trend_direction: str | None,
) -> tuple[str, str, float, str] | None:
    """Tries real setup detectors in a fixed, documented order and returns
    the first one that's time-eligible (_setup_eligible_now) and produces
    a real direction -- never more than one setup_type per scan, matching
    SignalEngine's existing single-candidate-per-cycle design.

    Only tries trend-favored setups (OPENING_RANGE_BREAKOUT, TREND_
    CONTINUATION, MOMENTUM_CONTINUATION, VWAP_BREAKOUT), each requiring
    its own real direction to AGREE with trend_direction to be selected --
    same spirit as the existing technical_score's "does the broader trend
    confirm this" read, just applied per-setup instead of only once.
    OPENING_RANGE_BREAKOUT is tried first and, exactly as before this
    change, always forms a candidate in trend_direction when time-eligible
    regardless of its own agreement (SignalEngine's confidence weighting,
    not a hard gate, is what penalizes a mismatch) -- unchanged behavior,
    preserved for the one setup that already worked this way.

    range-favored setups (VWAP_REJECTION, SUPPORT_RESISTANCE_REACTION,
    real and independently tested -- see their own functions) are
    deliberately NOT tried here: intelligence/signal_engine.py::
    SignalEngine.evaluate()'s own `direction` is hardcoded from `regime`
    (TREND_UP/GAP_UP -> CALL, TREND_DOWN/GAP_DOWN -> PUT, everything else
    -> NO_TRADE, including RANGE/UNCERTAIN -- the exact regime these two
    setups need to matter). That is a real architectural constraint in
    shared, already-tested core infrastructure, not a data gap this
    function can route around without changing SignalEngine itself --
    surfaced plainly in the accompanying report rather than silently
    worked around.
    """
    if trend_direction is None:
        return None

    if _setup_eligible_now("OPENING_RANGE_BREAKOUT", now, session_open):
        orb_direction = breakout_direction(todays, OPENING_RANGE_MINUTES)
        high, low = opening_range(todays, OPENING_RANGE_MINUTES)
        opening_score = (
            80.0
            if orb_direction == trend_direction
            else 20.0
            if orb_direction != "NO_TRADE"
            else 50.0
        )
        evidence = f"opening range {low:.2f}-{high:.2f}, ORB read={orb_direction}"
        return "OPENING_RANGE_BREAKOUT", trend_direction, opening_score, evidence

    for setup_type, detect in (
        ("TREND_CONTINUATION", lambda: _trend_continuation_setup(candles)),
        ("MOMENTUM_CONTINUATION", lambda: _momentum_continuation_setup(features)),
        ("VWAP_BREAKOUT", lambda: _vwap_breakout_setup(features)),
    ):
        if not _setup_eligible_now(setup_type, now, session_open):
            continue
        direction, score, evidence = detect()
        if direction == trend_direction:
            return setup_type, direction, score, evidence

    return None


def _add_candidate(
    context: dict[str, Any],
    candles: pd.DataFrame,
    features: dict[str, float],
    signal_threshold: float,
    option_quotes: list[OptionQuote],
    previous_option_quotes: list[OptionQuote],
    now: datetime,
) -> None:
    today = candles.index[-1].date()
    todays = candles[candles.index.date == today]
    if len(todays) <= OPENING_RANGE_MINUTES:
        return  # not enough of today's session yet -- correctly no candidate, not a guess

    regime = classify(features, context.get("gap_pct", 0.0))
    trend_direction = (
        "CALL"
        if regime in {Regime.TREND_UP, Regime.GAP_UP}
        else "PUT"
        if regime in {Regime.TREND_DOWN, Regime.GAP_DOWN}
        else None
    )
    session_open = todays.index[0].to_pydatetime()

    setup = _select_setup(candles, todays, features, now, session_open, trend_direction)
    if setup is None:
        # Either no directional (trend/gap) regime this scan (RANGE/
        # UNCERTAIN/HIGH_VOLATILITY/LOW_VOLATILITY), or every trend-favored
        # setup was either outside its own time window or found no real
        # structure agreeing with the regime's implied direction -- see
        # _select_setup's own docstring for exactly which setups were
        # even eligible to be tried this scan.
        logger.info(
            "live_context_no_eligible_setup regime=%s trend_direction=%s now=%s",
            regime.value,
            trend_direction,
            now.isoformat(),
        )
        return
    setup_type, direction, setup_score, setup_evidence = setup

    # Same bullish/bearish read TechnicalAgent computes itself -- reused,
    # not reinvented, for the "technical" sub-score. Keyed on the regime's
    # own implied direction (trend_direction), not the specific setup's --
    # this is a broader "does the wider trend confirm" read, independent
    # of which setup fired.
    bullish = features["ema_fast"] > features["ema_slow"] and features["close"] > features["vwap"]
    technical_score = 75.0 if (bullish and trend_direction == "CALL") or (
        not bullish and trend_direction == "PUT"
    ) else 45.0
    volatility_ratio = features["atr"] / features["close"] if features["close"] else 0.0
    risk_penalty = 25.0 if volatility_ratio > 0.008 else 0.0

    volume_score, volume_reason = _combined_volume_score(
        candles, today, OPENING_RANGE_MINUTES, option_quotes, previous_option_quotes
    )
    option_score, option_reason = _option_score(option_quotes, previous_option_quotes, direction)
    global_score, global_direction = _global_score(context, direction)
    news_score, news_direction = _news_score(context, direction)

    signal = SignalEngine(threshold=signal_threshold).evaluate(
        # The real `now` this scan is evaluating at, not a fresh wall-clock
        # read -- matters once assemble_context can be called repeatedly
        # through the day (Brief 6 Part B) or from a backtest's simulated
        # clock; a second, independent time source here would silently
        # diverge from both.
        timestamp=now,
        regime=regime,
        technical=technical_score,
        opening=setup_score,
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
            "live_context_signal_below_threshold setup_type=%s regime=%s confidence=%.1f threshold=%.1f "
            "volume=%.1f(%s) option=%.1f(%s) global=%.1f(%s) news=%.1f(%s)",
            setup_type,
            regime.value,
            signal.confidence,
            signal_threshold,
            volume_score,
            volume_reason,
            option_score,
            option_reason,
            global_score,
            global_direction,
            news_score,
            news_direction,
        )
        return

    if setup_type == "OPENING_RANGE_BREAKOUT":
        high, low = opening_range(todays, OPENING_RANGE_MINUTES)
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
    else:
        entry_zone, stop_zone, target_zone = _atr_zones(
            signal.direction, features["close"], features["atr"]
        )

    context["candidate_direction"] = signal.direction
    context["candidate_confidence"] = signal.confidence
    context["setup_type"] = setup_type
    context["entry_zone"] = entry_zone
    context["stop_zone"] = stop_zone
    context["target_zone"] = target_zone
    context["candidate_evidence"] = [
        f"regime={regime.value} implies {trend_direction}",
        f"setup={setup_type}: {setup_evidence}",
        (
            f"volume={volume_score:.1f} ({volume_reason}), option={option_score:.1f} ({option_reason}), "
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

    previous_option_quotes defaults to [] (not fabricated) when a caller
    doesn't have one -- _option_score/_option_volume_score correctly read
    this as "unavailable" rather than a real absence of buildup/volume
    change. As of Brief 5, build_live_context's own live callers (main.py)
    supply a real one, retrieved from storage.database.Database's
    persisted snapshot (Part B); daily_backtest.py supplies the prior
    trading day's option_quotes_by_day entry when one exists.
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
        context, candles, features, settings.signal_threshold, option_quotes, previous_option_quotes, now
    )
    return context


def build_live_context(
    settings: Settings,
    kite: object,
    calendar: NseCalendar,
    now: datetime | None = None,
    previous_option_quotes: list[OptionQuote] | None = None,
) -> dict[str, Any]:
    """previous_option_quotes: the real prior-session option chain, when
    the caller has one (main.py retrieves it from
    storage.database.Database.latest_option_chain_snapshot before calling
    this -- Brief 5 Part B). Defaults to None/[] so this function stays
    usable and correctly fail-closed on its own, e.g. in tests that don't
    care about OI-buildup scoring.
    """
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

    return assemble_context(
        candles, option_quotes, quote.ltp, now, market_open, settings, previous_option_quotes
    )
