"""Deterministic structured research agents. External adapters supply their context."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from agents.contracts import AgentResult, TradeCandidate
from ai.prompts import GLOBAL_SYNTHESIS
from ai.router import AIRouter
from config import IST
from data.news import aggregate_sentiment
from intelligence.market_regime import Regime, classify
from monitoring.logger import configure_logger
from strategy.regime_selector import weight_for

logger = configure_logger(__name__)


def _result(agent: str, confidence: float, evidence: tuple[str, ...], **data: Any) -> AgentResult:
    return AgentResult(agent, datetime.now(IST), confidence, evidence, data)


class GlobalResearchAgent(BaseAgent):
    """global_direction/confidence below are computed by ONE deterministic
    formula (the real average of context["global_context"]'s numeric
    values) and NOTHING else changes them -- ai_commentary (Brief 8 Part
    C) is a purely additional, informational field, generated from the
    SAME real facts, read by no other agent, RiskAgent, sizing, or order
    logic anywhere in this codebase (see
    tests/test_ai_safety.py::test_adversarial_ai_output_never_changes_
    the_deterministic_signal for the adversarial proof). AI here
    supplements the quantitative read; it never replaces or adjusts it.
    """

    name = "global_research"
    # Real synchronous AI HTTP calls can take longer than BaseAgent's
    # default 3s budget; this only affects how long the OPTIONAL ai_
    # commentary enrichment is allowed to take before BaseAgent.run()'s
    # own post-hoc check would flag the whole result as timed-out -- the
    # deterministic computation above is unaffected either way, and the
    # ai_commentary try/except below already has its own tighter, real
    # HTTP-level bound (ai/provider.py::ANTHROPIC_REQUEST_TIMEOUT_SECONDS).
    timeout_seconds = 20.0

    def __init__(self, ai_router: AIRouter | None = None) -> None:
        self.ai_router = ai_router or AIRouter()

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
        global_direction = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
        confidence = min(80, abs(score))

        ai_commentary = self._synthesize(available)

        return _result(
            self.name,
            confidence,
            ("Available global context evaluated.",),
            global_direction=global_direction,
            data_freshness="PROVIDED",
            risk_factors=[],
            ai_commentary=ai_commentary,
        )

    def _synthesize(self, available: list[Any]) -> str | None:
        """Real AI synthesis over the same real facts already used above
        -- never invents new data, never influences global_direction/
        confidence (already computed before this is even called). Any
        failure here (network, parsing, no provider configured) is
        caught LOCALLY and returns None -- it must never escape to
        BaseAgent.run()'s outer try/except, which would discard the
        already-correct deterministic result above along with it.
        """
        try:
            facts = {v.name: v.value for v in available}
            analysis = self.ai_router.analyze(GLOBAL_SYNTHESIS, facts)
            return analysis.summary or None
        except Exception as exc:  # noqa: BLE001 - AI enrichment is optional; its failure must never affect the real deterministic result above.
            logger.warning("global_research_ai_synthesis_failed error=%s", exc)
            return None


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
    """Classifies verified news by real aggregate sentiment
    (data.news.aggregate_sentiment) instead of a placeholder. Confidence is
    capped at 40 -- well below what other research agents can reach -- so
    SignalHunterAgent's news-alignment nudge (strategy/regime_selector.py's
    sibling concept, applied in SignalHunterAgent.analyze) can only ever be
    a small adjustment, never enough by itself to flip an otherwise-failing
    candidate to approved. Matches Section 7's "must not trade on a single
    headline" principle."""

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
                direction="UNKNOWN",
                sentiment_score=0.0,
            )
        sentiment_score = aggregate_sentiment(items)
        direction = (
            "BULLISH" if sentiment_score > 0.1 else "BEARISH" if sentiment_score < -0.1 else "NEUTRAL"
        )
        confidence = min(40.0, abs(sentiment_score) * 100)
        evidence_note = (
            f"{len(items)} verified news item(s); aggregate sentiment "
            f"{sentiment_score:.2f} ({direction})."
        )
        return _result(
            self.name,
            confidence,
            (evidence_note,),
            classification=direction,
            market_impact="CONTEXT_ONLY",
            freshness="PROVIDED",
            direction=direction,
            sentiment_score=sentiment_score,
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
    """Builds the deterministic candidate the upstream market state implies,
    then applies a regime-aware confidence weight (strategy/regime_selector.py)
    -- a scaling of that same candidate's confidence, never a hard filter, and
    always logged with which regime/breadth evidence drove it and by how much,
    so the learning loop can later evaluate whether regime-matching actually
    correlated with better outcomes."""

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
        setup_type = context.get("setup_type", "OPENING_STRUCTURE")
        weight = weight_for(
            setup_type,
            context.get("market_regime", "UNCERTAIN"),
            context.get("volatility_regime", "NORMAL"),
            context.get("breadth_participation"),
        )
        weighted_confidence = min(100.0, max(0.0, confidence * weight.multiplier))
        invalidations = ("Loss of entry zone",)
        if weight.widen_invalidation:
            invalidations = invalidations + (
                "Invalidation widened: high-volatility whipsaw risk for this setup type",
            )
        evidence = tuple(context.get("candidate_evidence", ["deterministic setup"])) + weight.reasons

        # News-alignment nudge: NewsAgent's own confidence is capped at 40
        # (see its docstring), and this nudge is itself capped at +/-5% of
        # confidence -- deliberately too small to flip an otherwise-failing
        # candidate to approved on a single headline (Section 7 principle),
        # while still letting aligned/contradicting news shade confidence.
        news_direction = context.get("news_direction", "UNKNOWN")
        news_confidence = float(context.get("news_confidence", 0) or 0)
        if news_direction in {"BULLISH", "BEARISH"} and news_confidence > 0:
            aligned = (news_direction == "BULLISH" and direction == "CALL") or (
                news_direction == "BEARISH" and direction == "PUT"
            )
            nudge = min(news_confidence, 40.0) / 40.0 * 0.05
            weighted_confidence = min(
                100.0,
                max(0.0, weighted_confidence * (1 + nudge if aligned else 1 - nudge)),
            )
            news_note = (
                f"news sentiment {'supports' if aligned else 'contradicts'} direction "
                f"(news confidence {news_confidence:.0f}, capped nudge)."
            )
            evidence = evidence + (news_note,)
        candidate = TradeCandidate(
            direction,
            setup_type,
            "NIFTY",
            weighted_confidence,
            evidence,
            invalidations,
            tuple(context.get("entry_zone", (0.0, 0.0))),
            tuple(context.get("stop_zone", (0.0, 0.0))),
            tuple(context.get("target_zone", (0.0, 0.0))),
        )
        return _result(
            self.name,
            weighted_confidence,
            candidate.evidence,
            candidates=[candidate],
            regime_weight_multiplier=weight.multiplier,
            regime_weight_reasons=weight.reasons,
        )
