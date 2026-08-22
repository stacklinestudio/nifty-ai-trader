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
    ) -> Signal:
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
        risks = ["uncertain regime"] if regime in {Regime.UNCERTAIN, Regime.HIGH_VOLATILITY} else []
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
