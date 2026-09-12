"""Phase 2 Piece 10, Requirements 2-4: canonical, deterministic,
versioned market-regime detection.

Audit finding this builds on (see the Piece 10 completion report for
the full audit): `intelligence/market_regime.py::classify` already IS a
real, deterministic, pure regime classifier -- no I/O, no randomness,
no look-ahead of its own (it only reads whatever `features`/`gap_pct`
the CALLER already computed). It already has 8 real call sites live in
this system (`execution/live_context.py::_add_candidate`,
`agents/research_agents.py::IndiaMarketAgent`). This module deliberately
does NOT replace or duplicate that classifier -- `classify()` is reused
verbatim, unmodified. What did not exist before this piece: a
versioned, timestamp-bounded, evidence-carrying, stability-aware WRAPPER
around it that can be run standalone (over real historical candle data,
for the Requirement 14 historical report / Requirement 12 no-look-ahead
proof) independent of the live orchestrator's own already-existing
regime read.

Two honest, real gaps in the existing `Regime` enum/classifier, found
and documented here rather than silently worked around:
- `Regime.RANGE` is a real enum member `classify()` can never actually
  return (its body has no branch that produces it) -- confirmed by
  reading the whole 36-line file. This module does not fix that (it is
  live, already-tested classification logic outside this piece's
  scope); `detect_regime` below can therefore never report `RANGE`
  either, honestly, not a bug introduced here.
- `classify()` has no notion of "not enough real data yet" -- it always
  returns SOME `Regime` value, even from an all-zero/default `features`
  Series. `INSUFFICIENT_DATA` (this module's own addition, not a
  `Regime` enum member) is what this wrapper reports instead of ever
  calling `classify()` on a window that does not have enough genuine
  history behind it -- see MIN_BARS_FOR_REGIME below.

Reused verbatim from `execution/live_context.py` (matching the same,
already-established reuse pattern `execution/decision_ledger.py`'s own
docstring documents for its own detector reuse): `_technical_features`
(the exact real feature dict `classify()` is fed live) and `_gap_pct`
(the exact real today-vs-prior-close formula). No new feature-
computation logic is introduced -- this module only decides WHICH real
bars those functions run over and HOW MANY TIMES, for a genuine
stability read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd

from execution.live_context import _gap_pct, _technical_features
from intelligence.market_regime import Regime, classify
from intelligence.technicals import feature_frame

DETECTOR_VERSION = "regime_detection_v1"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# Matches execution/live_context.py::TECHNICAL_FEATURE_WINDOW_DAYS
# exactly -- the same real lookback the live system already uses for
# identical feature computation over the same real candle shape, not an
# independently-chosen number that could silently diverge from what the
# live system's own regime read already assumes.
REGIME_LOOKBACK_DAYS = 10

# intelligence/technicals.py::feature_frame needs a real, non-NaN
# ema_slow (ewm span=21 -- converges fast but is only genuinely
# "warmed up" past its own span) and a real, non-NaN atr/rsi (rolling
# window=14, hard NaN before the 14th real bar). 30 gives comfortable
# margin past both real warm-up requirements on the LATEST bar -- not a
# number tuned against any performance outcome, a warm-up floor derived
# directly from feature_frame's own two rolling/ewm window sizes.
MIN_BARS_FOR_REGIME = 30

# Requirement 4 (stability/flapping): how many of the most recent real
# bars must independently re-classify to the SAME regime as the latest
# bar for that regime to be reported `stable=True`. 20 is large enough
# to distinguish "genuinely settled" from "one bar flickered" while
# small enough that a real, fast intraday regime shift (e.g. a real
# breakout) still registers as stable within the same trading session,
# not only the next day.
STABILITY_LOOKBACK_BARS = 20

# The real, explicit, testable stability threshold (Requirement 4): at
# least 60% of the trailing STABILITY_LOOKBACK_BARS real bars must
# agree with the latest bar's own classification. Not derived from or
# optimized against any backtest/OOS performance data -- a plain
# majority-with-margin rule, chosen before any regime/strategy
# evaluation in this piece was run, exactly so it cannot be quietly
# fitted to this project's own historical window.
STABLE_AGREEMENT_THRESHOLD = 0.6


@dataclass(frozen=True)
class RegimeRecord:
    regime_id: str
    timestamp: str  # ISO 8601 -- the real "as of" point this record is valid for
    regime: str  # a real Regime.value, or INSUFFICIENT_DATA -- never anything else
    confidence: float | None  # real trailing-agreement percentage (0-100), or None when a stability read wasn't possible (INSUFFICIENT_DATA)
    input_metrics: dict[str, float]
    lookback_window_days: int
    lookback_window_bars: int
    data_start_timestamp: str | None
    data_end_timestamp: str | None
    detector_version: str
    reason: str
    stable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime_id": self.regime_id,
            "timestamp": self.timestamp,
            "regime": self.regime,
            "confidence": self.confidence,
            "input_metrics": dict(self.input_metrics),
            "lookback_window_days": self.lookback_window_days,
            "lookback_window_bars": self.lookback_window_bars,
            "data_start_timestamp": self.data_start_timestamp,
            "data_end_timestamp": self.data_end_timestamp,
            "detector_version": self.detector_version,
            "reason": self.reason,
            "stable": self.stable,
        }


def _insufficient_data_record(
    now: datetime, window_bars: int, lookback_days: int, window: pd.DataFrame
) -> RegimeRecord:
    return RegimeRecord(
        regime_id=str(uuid4()),
        timestamp=now.isoformat(),
        regime=INSUFFICIENT_DATA,
        confidence=None,
        input_metrics={},
        lookback_window_days=lookback_days,
        lookback_window_bars=window_bars,
        data_start_timestamp=window.index.min().isoformat() if window_bars else None,
        data_end_timestamp=window.index.max().isoformat() if window_bars else None,
        detector_version=DETECTOR_VERSION,
        reason=(
            f"only {window_bars} real bars available in the trailing {lookback_days} real days "
            f"as of {now.isoformat()} -- need at least {MIN_BARS_FOR_REGIME} for a real, "
            "non-NaN feature read. Honestly undetermined, not guessed."
        ),
        stable=False,
    )


def _explain(regime: Regime, features: dict[str, float], gap_pct: float) -> str:
    """A deterministic, plain-language account of which real branch of
    intelligence.market_regime.classify fired and why -- Requirement 2's
    'do not store unexplained black-box classifications'."""
    if regime is Regime.GAP_UP:
        return f"real gap_pct={gap_pct:.4f} >= 0.006 (today's real open vs. prior real close)."
    if regime is Regime.GAP_DOWN:
        return f"real gap_pct={gap_pct:.4f} <= -0.006 (today's real open vs. prior real close)."
    if regime is Regime.HIGH_VOLATILITY:
        ratio = features["atr"] / max(features["close"], 1)
        return f"real atr/close={ratio:.4f} > 0.008."
    if regime is Regime.TREND_UP:
        return (
            f"real ema_fast={features['ema_fast']:.2f} > ema_slow={features['ema_slow']:.2f} "
            f"and real momentum={features['momentum']:.4f} > 0."
        )
    if regime is Regime.TREND_DOWN:
        return (
            f"real ema_fast={features['ema_fast']:.2f} < ema_slow={features['ema_slow']:.2f} "
            f"and real momentum={features['momentum']:.4f} < 0."
        )
    return "no real gap/volatility/trend condition fired -- neither confirmed trend nor confirmed gap."


def _trailing_agreement(feature_rows: pd.DataFrame, gap_pct: float, final_regime: Regime) -> tuple[float, int]:
    """Re-classifies each of the trailing STABILITY_LOOKBACK_BARS real
    rows already inside the (already `<= as_of`-truncated) window using
    the SAME real classify() function, and reports what fraction agree
    with the latest bar's own regime. `gap_pct` is held constant across
    the tail deliberately -- it is a real, single-session value (today's
    open vs. yesterday's close) that classify() itself treats as
    constant for the whole session, not a per-bar quantity."""
    tail = feature_rows.iloc[-STABILITY_LOOKBACK_BARS:]
    classifications = [classify(row, gap_pct) for _, row in tail.iterrows()]
    agree = sum(1 for r in classifications if r == final_regime)
    return agree / len(classifications), len(classifications)


def detect_regime(
    candles: pd.DataFrame, as_of: datetime, lookback_days: int = REGIME_LOOKBACK_DAYS
) -> RegimeRecord:
    """The one real, deterministic entry point. No-look-ahead by
    construction (Requirement 12): the FIRST thing this function does
    is truncate `candles` to `index <= as_of` -- every subsequent
    computation (features, gap_pct, stability re-classification) only
    ever touches that truncated frame, so no later real bar can reach
    any branch below this line, structurally, not by caller discipline.
    """
    truncated = candles[candles.index <= as_of]
    window = truncated[truncated.index >= as_of - timedelta(days=lookback_days)]
    window_bars = len(window)
    if window_bars < MIN_BARS_FOR_REGIME:
        return _insufficient_data_record(as_of, window_bars, lookback_days, window)

    feature_rows = feature_frame(window)
    features = _technical_features(window)
    today = as_of.date()
    gap_pct = _gap_pct(window, today)
    regime = classify(pd.Series(features), gap_pct)
    agreement, n_evaluated = _trailing_agreement(feature_rows, gap_pct, regime)
    stable = n_evaluated >= STABILITY_LOOKBACK_BARS and agreement >= STABLE_AGREEMENT_THRESHOLD

    return RegimeRecord(
        regime_id=str(uuid4()),
        timestamp=as_of.isoformat(),
        regime=regime.value,
        confidence=round(agreement * 100, 2),
        input_metrics={**features, "gap_pct": gap_pct},
        lookback_window_days=lookback_days,
        lookback_window_bars=window_bars,
        data_start_timestamp=window.index.min().isoformat(),
        data_end_timestamp=window.index.max().isoformat(),
        detector_version=DETECTOR_VERSION,
        reason=_explain(regime, features, gap_pct),
        stable=stable,
    )
