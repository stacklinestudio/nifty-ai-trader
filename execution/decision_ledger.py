"""Phase 1: Decision Ledger + Market State Snapshot.

Pure data/persistence addition -- zero changes to any agent, risk, EV,
or execution DECISION logic. Every function here is either read-only
(re-evaluating the same real, already-tested setup detectors in
execution/live_context.py without short-circuiting, purely to record
what was seen) or a pure serialization/ID-generation helper. Nothing
here is imported by, or can influence, SignalEngine, RiskAgent,
TradeBuilderAgent, ExecutionAgent, or any EV/counterfactual module --
confirmed by grep: no module outside this file and its own tests
imports anything from here.

Real setups evaluated this cycle vs. the ONE that wins:
execution/live_context.py::_select_setup already tries setups in a
fixed, documented order and returns the first eligible one that fires
(see its own docstring) -- by design, since SignalEngine evaluates
exactly one setup per real cycle. That real decision is left completely
untouched here. `evaluate_all_setups` below calls the SAME real,
already-tested detector functions (_vwap_breakout_setup,
_vwap_rejection_setup, _momentum_continuation_setup,
_trend_continuation_setup, _support_resistance_reaction_setup, plus
ORB's breakout_direction/opening_range), imported directly from
live_context.py rather than reimplemented, but does not short-circuit
-- it records every real detector's real output for this cycle, tagged
with whether it was time-eligible and whether it's the real winner
_select_setup already, independently, chose. `winning_setup_type` is
always exactly whatever _select_setup returned to _add_candidate; this
module only cross-references it, never re-decides it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from data.global_market import ContextValue
from data.news import NewsItem
from data.option_chain import OptionQuote, quotes_to_json
from execution.live_context import (
    OPENING_RANGE_MINUTES,
    _momentum_continuation_setup,
    _setup_eligible_now,
    _support_resistance_reaction_setup,
    _trend_continuation_setup,
    _vwap_breakout_setup,
    _vwap_rejection_setup,
)
from intelligence.market_regime import Regime
from strategy.orb import breakout_direction, opening_range


@dataclass(frozen=True)
class SetupEvaluation:
    setup_type: str
    eligible: bool
    direction: str | None
    score: float
    evidence: str
    is_winner: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_type": self.setup_type,
            "eligible": self.eligible,
            "direction": self.direction,
            "score": self.score,
            "evidence": self.evidence,
            "is_winner": self.is_winner,
        }


@dataclass(frozen=True)
class MarketStateSnapshot:
    candidate_id: str
    timestamp: str  # ISO 8601
    spot: float
    regime: str
    trend_direction: str | None
    ema_fast: float
    ema_slow: float
    vwap: float
    atr: float
    momentum: float
    volatility_ratio: float
    gap_pct: float
    option_quotes_json: str
    previous_option_quotes_count: int
    global_context: tuple[dict[str, Any], ...]
    news_items: tuple[dict[str, Any], ...]
    setups_evaluated: tuple[SetupEvaluation, ...]
    winning_setup_type: str | None
    winning_direction: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "timestamp": self.timestamp,
            "spot": self.spot,
            "regime": self.regime,
            "trend_direction": self.trend_direction,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "vwap": self.vwap,
            "atr": self.atr,
            "momentum": self.momentum,
            "volatility_ratio": self.volatility_ratio,
            "gap_pct": self.gap_pct,
            "option_quotes_json": self.option_quotes_json,
            "previous_option_quotes_count": self.previous_option_quotes_count,
            "global_context": list(self.global_context),
            "news_items": list(self.news_items),
            "setups_evaluated": [s.to_dict() for s in self.setups_evaluated],
            "winning_setup_type": self.winning_setup_type,
            "winning_direction": self.winning_direction,
        }


def generate_candidate_id(now: datetime, seq: int) -> str:
    """CAND-<date>-<time>-<seq>, e.g. CAND-20260910-093015-001. `seq` is
    caller-supplied -- the real caller (agents/orchestrator.py) sources it
    from Database.next_decision_ledger_sequence, which is DB-backed (durable
    across a process restart within the same real calendar day), not an
    in-memory counter. This function itself does no I/O -- pure string
    formatting -- so the exact same code path is reusable for historical
    replay/verification without needing a live database."""
    return f"CAND-{now:%Y%m%d}-{now:%H%M%S}-{seq:03d}"


def evaluate_all_setups(
    candles: pd.DataFrame,
    todays: pd.DataFrame,
    features: dict[str, float],
    today: date,
    now: datetime,
    session_open: datetime,
    regime: Regime,
    trend_direction: str | None,
    winning_setup_type: str | None,
) -> tuple[SetupEvaluation, ...]:
    """Real detector outputs for every setup execution/live_context.py::
    _select_setup could ever try for this real regime -- never short-
    circuits, unlike _select_setup itself (whose short-circuit IS the
    real trading decision and is left completely untouched here, called
    separately by the caller). Returns an empty tuple for HIGH_VOLATILITY/
    LOW_VOLATILITY regimes -- the honest, correct mirror of the real,
    documented fact that _select_setup tries nothing at all for those
    regimes either, not a bug in this function.
    """
    results: list[SetupEvaluation] = []

    if trend_direction is not None:
        orb_eligible = _setup_eligible_now("OPENING_RANGE_BREAKOUT", now, session_open)
        if orb_eligible:
            orb_direction = breakout_direction(todays, OPENING_RANGE_MINUTES)
            high, low = opening_range(todays, OPENING_RANGE_MINUTES)
            orb_score = (
                80.0
                if orb_direction == trend_direction
                else 20.0
                if orb_direction != "NO_TRADE"
                else 50.0
            )
            results.append(
                SetupEvaluation(
                    "OPENING_RANGE_BREAKOUT",
                    True,
                    trend_direction,
                    orb_score,
                    f"opening range {low:.2f}-{high:.2f}, ORB read={orb_direction}",
                    winning_setup_type == "OPENING_RANGE_BREAKOUT",
                )
            )
        else:
            results.append(
                SetupEvaluation("OPENING_RANGE_BREAKOUT", False, None, 0.0, "outside open window", False)
            )

        for setup_type, detect in (
            ("TREND_CONTINUATION", lambda: _trend_continuation_setup(candles)),
            ("MOMENTUM_CONTINUATION", lambda: _momentum_continuation_setup(features)),
            ("VWAP_BREAKOUT", lambda: _vwap_breakout_setup(features)),
        ):
            eligible = _setup_eligible_now(setup_type, now, session_open)
            if not eligible:
                results.append(SetupEvaluation(setup_type, False, None, 0.0, "outside open window", False))
                continue
            direction, score, evidence = detect()
            results.append(
                SetupEvaluation(
                    setup_type,
                    True,
                    direction,
                    score,
                    evidence,
                    winning_setup_type == setup_type and direction == trend_direction,
                )
            )
        return tuple(results)

    if regime in {Regime.RANGE, Regime.UNCERTAIN}:
        latest_bar = todays.iloc[-1]
        for setup_type, detect in (
            ("VWAP_REJECTION", lambda: _vwap_rejection_setup(latest_bar, features)),
            ("SUPPORT_RESISTANCE_REACTION", lambda: _support_resistance_reaction_setup(candles, today, features)),
        ):
            eligible = _setup_eligible_now(setup_type, now, session_open)
            if not eligible:
                results.append(SetupEvaluation(setup_type, False, None, 0.0, "outside open window", False))
                continue
            direction, score, evidence = detect()
            results.append(
                SetupEvaluation(
                    setup_type,
                    True,
                    direction,
                    score,
                    evidence,
                    winning_setup_type == setup_type and direction is not None,
                )
            )
        return tuple(results)

    return ()


def build_market_state_snapshot(
    candidate_id: str,
    now: datetime,
    spot: float,
    features: dict[str, float],
    regime: Regime,
    trend_direction: str | None,
    gap_pct: float,
    option_quotes: list[OptionQuote],
    previous_option_quotes: list[OptionQuote],
    global_context: list[ContextValue],
    news_items: list[NewsItem],
    setups_evaluated: tuple[SetupEvaluation, ...],
    winning_setup_type: str | None,
    winning_direction: str | None,
) -> MarketStateSnapshot:
    """Assembles the immutable real market-state snapshot a decision links
    to. Every field here is read from data the caller already computed
    (features/regime/option_quotes/global_context/news_items) or from
    `evaluate_all_setups` above -- this function does no I/O and makes no
    decision of its own."""
    volatility_ratio = features["atr"] / features["close"] if features["close"] else 0.0
    return MarketStateSnapshot(
        candidate_id=candidate_id,
        timestamp=now.isoformat(),
        spot=spot,
        regime=regime.value,
        trend_direction=trend_direction,
        ema_fast=features["ema_fast"],
        ema_slow=features["ema_slow"],
        vwap=features["vwap"],
        atr=features["atr"],
        momentum=features["momentum"],
        volatility_ratio=volatility_ratio,
        gap_pct=gap_pct,
        option_quotes_json=quotes_to_json(option_quotes),
        previous_option_quotes_count=len(previous_option_quotes),
        global_context=tuple(
            {
                "name": c.name,
                "value": c.value,
                "timestamp": c.timestamp.isoformat() if c.timestamp else None,
                "source": c.source,
                "available": c.available,
                "error": c.error,
            }
            for c in global_context
        ),
        news_items=tuple(
            {
                "timestamp": n.timestamp.isoformat(),
                "headline": n.headline,
                "source": n.source,
                "relevance": n.relevance,
                "sentiment": n.sentiment,
                "confidence": n.confidence,
            }
            for n in news_items
        ),
        setups_evaluated=setups_evaluated,
        winning_setup_type=winning_setup_type,
        winning_direction=winning_direction,
    )
