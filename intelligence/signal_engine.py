from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from intelligence.market_regime import Regime


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    direction: str
    confidence: float
    features: dict[str, float | str]
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.direction in {"CALL", "PUT"} and self.confidence > 0


class SignalEngine:
    def __init__(self, threshold: float = 75.0) -> None:
        self.threshold = threshold

    def evaluate(
        self,
        timestamp: datetime,
        regime: Regime,
        technical: float,
        opening: float,
        volume: float = 0,
        option: float = 0,
        global_score: float = 0,
        news: float = 0,
        risk_penalty: float = 0,
        override_direction: str | None = None,
    ) -> Signal:
        """override_direction (Brief 7): a caller that has already
        determined a real direction independent of `regime` -- e.g. a
        range-favored setup detector (execution/live_context.py's
        VWAP_REJECTION/SUPPORT_RESISTANCE_REACTION), which by design finds
        its own real signal specifically in RANGE/UNCERTAIN regimes, where
        `regime` alone implies no direction at all -- can supply it here
        instead of `regime` deciding. The confidence formula below is
        completely unchanged either way; only which direction that
        confidence attaches to. Omit (default None) for identical
        behavior to every existing caller.
        """
        if override_direction in {"CALL", "PUT"}:
            direction = override_direction
        else:
            direction = (
                "CALL"
                if regime in {Regime.TREND_UP, Regime.GAP_UP}
                else "PUT"
                if regime in {Regime.TREND_DOWN, Regime.GAP_DOWN}
                else "NO_TRADE"
            )
        confidence = max(
            0.0,
            min(
                100.0,
                technical * 0.35
                + opening * 0.25
                + volume * 0.15
                + option * 0.10
                + (global_score + 100) * 0.05
                + (news + 100) * 0.025
                - risk_penalty * 0.125,
            ),
        )
        risks = []
        if regime == Regime.HIGH_VOLATILITY:
            # A real whipsaw-risk flag regardless of direction source --
            # matches strategy/regime_selector.py's own "high volatility
            # expansion increases whipsaw risk" reasoning, which applies
            # broadly, not just to trend-derived directions.
            risks.append("high volatility regime")
        if regime in {Regime.UNCERTAIN, Regime.RANGE} and override_direction is None:
            # Only a real risk when there's no independent direction to
            # rely on -- an override means a setup already found one
            # despite the regime classifier itself being non-directional
            # here, which is exactly the case range-favored setups exist
            # for, not something to veto by construction.
            risks.append("uncertain regime")
        if confidence < self.threshold:
            risks.append("below configured confidence threshold")
        if risks:
            direction = "NO_TRADE"
        return Signal(
            timestamp,
            direction,
            confidence,
            {
                "regime": str(regime),
                "technical": technical,
                "opening": opening,
                "volume": volume,
                "option": option,
                "global": global_score,
                "news": news,
                "risk_penalty": risk_penalty,
            },
            ["multi-factor opening assessment"],
            risks,
        )
