"""Deterministic structured research agents. External adapters supply their context."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from agents.contracts import AgentResult, TradeCandidate
from config import IST
from intelligence.market_regime import Regime, classify


def _result(agent: str, confidence: float, evidence: tuple[str, ...], **data: Any) -> AgentResult:
    return AgentResult(agent, datetime.now(IST), confidence, evidence, data)


class GlobalResearchAgent(BaseAgent):
    name = "global_research"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        values = context.get("global_context", [])
        available = [value for value in values if getattr(value, "available", False)]
        if not available:
            return _result(
                self.name,
                0,
                ("Global data unavailable; no value invented.",),
                global_direction="UNKNOWN",
                data_freshness="UNAVAILABLE",
                risk_factors=["missing global context"],
            )
        score = sum(float(getattr(value, "value", 0) or 0) for value in available) / len(available)
        return _result(
            self.name,
            min(80, abs(score)),
            ("Available global context evaluated.",),
            global_direction="BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL",
            data_freshness="PROVIDED",
            risk_factors=[],
        )


class IndiaMarketAgent(BaseAgent):
    name = "india_market"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        features = context.get("features")
        if features is None:
            return _result(
                self.name,
                0,
                ("NIFTY feature state unavailable.",),
                market_direction="UNKNOWN",
                market_regime="UNCERTAIN",
                key_levels=[],
                risk_flags=["missing market data"],
            )
        regime = classify(features, float(context.get("gap_pct", 0)))
        direction = (
            "BULLISH"
            if regime in {Regime.TREND_UP, Regime.GAP_UP}
            else "BEARISH"
            if regime in {Regime.TREND_DOWN, Regime.GAP_DOWN}
            else "NEUTRAL"
        )
        return _result(
            self.name,
            70 if direction != "NEUTRAL" else 35,
            (f"Regime: {regime.value}",),
            market_direction=direction,
            market_regime=regime.value,
            key_levels=context.get("key_levels", []),
            risk_flags=[],
        )


class NewsAgent(BaseAgent):
    name = "news"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        items = context.get("news_items", [])
        if not items:
            return _result(
                self.name,
                0,
                ("No verified news items available.",),
                classification="UNKNOWN",
                market_impact="UNKNOWN",
                freshness="UNAVAILABLE",
            )
        impact = sum(
            getattr(item, "confidence", 0) * getattr(item, "relevance", 0) for item in items
        ) / len(items)
        return _result(
            self.name,
            min(70, impact * 100),
            (f"{len(items)} verified news item(s) considered.",),
            classification="MIXED",
            market_impact="CONTEXT_ONLY",
            freshness="PROVIDED",
        )


class TechnicalAgent(BaseAgent):
    name = "technical"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        features = context.get("features")
        if features is None:
            return _result(
                self.name, 0, ("Technical features unavailable.",), direction="UNKNOWN", score=0
            )
        bullish = features.get("ema_fast", 0) > features.get("ema_slow", 0) and features.get(
            "close", 0
        ) > features.get("vwap", 0)
        return _result(
            self.name,
            75 if bullish else 45,
            ("EMA/VWAP feature assessment.",),
            direction="BULLISH" if bullish else "BEARISH",
            score=75 if bullish else 45,
            atr=float(features.get("atr", 0)),
        )


class VolatilityAgent(BaseAgent):
    name = "volatility"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        atr, price = float(context.get("atr", 0)), float(context.get("spot", 0))
        ratio = atr / price if price else 0
        regime = "HIGH" if ratio > 0.008 else "LOW" if ratio < 0.002 else "NORMAL"
        return _result(
            self.name,
            70 if price else 0,
            ("ATR-based volatility assessment.",),
            volatility_regime=regime,
            volatility_score=ratio * 10000,
            risk_flags=["high opening volatility"] if regime == "HIGH" else [],
        )


class BreadthAgent(BaseAgent):
    name = "breadth"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        advances, declines = context.get("advances"), context.get("declines")
        if advances is None or declines is None:
            return _result(self.name, 0, ("Breadth unavailable.",), participation="UNKNOWN")
        participation = (
            "BROAD"
            if advances > declines * 1.5
            else "NARROW"
            if declines > advances * 1.5
            else "MIXED"
        )
        return _result(
            self.name,
            65,
            ("Advance/decline breadth assessed.",),
            participation=participation,
            advances=advances,
            declines=declines,
        )


class SignalHunterAgent(BaseAgent):
    name = "signal_hunter"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        direction, confidence = (
            context.get("candidate_direction"),
            float(context.get("candidate_confidence", 0)),
        )
        if direction not in {"CALL", "PUT"} or confidence <= 0:
            return _result(
                self.name,
                0,
                ("No deterministic candidate supplied by market state.",),
                candidates=[],
            )
        candidate = TradeCandidate(
            direction,
            context.get("setup_type", "OPENING_STRUCTURE"),
            "NIFTY",
            confidence,
            tuple(context.get("candidate_evidence", ["deterministic setup"])),
            ("Loss of entry zone",),
            tuple(context.get("entry_zone", (0.0, 0.0))),
            tuple(context.get("stop_zone", (0.0, 0.0))),
            tuple(context.get("target_zone", (0.0, 0.0))),
        )
        return _result(self.name, confidence, candidate.evidence, candidates=[candidate])
