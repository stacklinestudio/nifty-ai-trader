"""Candidate option analysis, thesis building, adversarial validation, and supervision."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from agents.contracts import AgentResult, Decision, TradeCandidate, TradeThesis, Validation
from config import IST, Settings
from data.option_chain import OptionQuote
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from strategy.option_selector import OptionSelector


def result(name: str, confidence: float, evidence: tuple[str, ...], **data: Any) -> AgentResult:
    return AgentResult(name, datetime.now(IST), confidence, evidence, data)


class OptionsAgent(BaseAgent):
    name = "options"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        candidate: TradeCandidate | None = context.get("candidate")
        options: list[OptionQuote] = context.get("option_quotes", [])
        if not candidate or not options:
            return result(self.name, 0, ("Candidate option quotes unavailable.",), ranked=[])
        selected = OptionSelector().select(
            options,
            candidate.direction,
            float(context.get("spot", 0)),
            float(context.get("max_position_value", 5000)),
        )
        if selected is None:
            return result(
                self.name, 0, ("No liquid, affordable option contract met constraints.",), ranked=[]
            )
        return result(
            self.name,
            min(100, max(0, selected.score + 90)),
            ("Liquidity, spread, expiry, and affordability ranked.",),
            ranked=[selected],
        )


class TradeBuilderAgent(BaseAgent):
    name = "trade_builder"

    def __init__(self, risk: RiskManager) -> None:
        self.risk = risk

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        candidate: TradeCandidate | None = context.get("candidate")
        selected = context.get("selected_option")
        if not candidate or not selected:
            return result(
                self.name,
                0,
                ("Cannot build thesis without candidate and option selection.",),
                thesis=None,
            )
        quote = selected.quote
        plan = self.risk.plan_long_option(
            quote.ltp,
            max(float(context.get("option_atr", quote.ltp * 0.08)), quote.ltp * 0.02),
            quote.instrument.lot_size,
        )
        if plan is None:
            return result(
                self.name, 0, ("Risk manager could not form a safe position plan.",), thesis=None
            )
        thesis = TradeThesis(
            candidate,
            quote.instrument.symbol,
            plan.entry,
            plan.stop,
            plan.target,
            plan.quantity,
            plan.estimated_risk,
            min(candidate.confidence, 95),
            candidate.evidence + ("Risk-based entry, stop, target, and size computed.",),
            candidate.invalidations,
        )
        return result(self.name, thesis.confidence, thesis.evidence, thesis=thesis)


class RiskAgent(BaseAgent):
    name = "risk"

    def __init__(self, settings: Settings, limits: DailyLimits) -> None:
        self.settings, self.limits = settings, limits

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        thesis: TradeThesis | None = context.get("thesis")
        validation: Validation | None = context.get("validation")
        health_ok = bool(context.get("market_data_fresh", False)) and bool(
            context.get("market_open", False)
        )
        reasons: list[str] = []
        if self.settings.kill_switch:
            reasons.append("emergency kill switch enabled")
        if not thesis:
            reasons.append("missing trade thesis")
        elif thesis.estimated_risk > self.settings.max_risk_per_trade:
            reasons.append("trade risk exceeds configured maximum")
        elif thesis.quantity <= 0:
            reasons.append("invalid quantity")
        if validation and validation.decision is not Decision.APPROVE:
            reasons.append("independent validation did not approve")
        if not self.limits.can_open():
            reasons.append("daily trade or loss limit reached")
        if not health_ok:
            reasons.append("market data is stale/unavailable or market is closed")
        approved = not reasons
        return result(
            self.name,
            100 if approved else 0,
            ("Deterministic risk veto evaluated.",),
            approved=approved,
            reasons=reasons,
        )


class TradeSupervisorAgent(BaseAgent):
    name = "trade_supervisor"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        thesis: TradeThesis | None = context.get("thesis")
        ltp = context.get("ltp")
        if thesis is None or ltp is None:
            return result(
                self.name,
                0,
                ("Position or live price unavailable; no recommendation.",),
                thesis_state="UNKNOWN",
                recommendation="HOLD",
            )
        state = (
            "STRENGTHENING"
            if ltp >= thesis.entry
            else "WEAKENING"
            if ltp > thesis.stop
            else "INVALIDATED"
        )
        recommendation = "EXIT" if state == "INVALIDATED" else "HOLD"
        return result(
            self.name,
            70,
            ("Original thesis and current LTP compared.",),
            thesis_state=state,
            recommendation=recommendation,
        )


class ExecutionAgent(BaseAgent):
    name = "execution"

    def __init__(self, paper_broker: Any, settings: Settings) -> None:
        self.paper_broker, self.settings = paper_broker, settings

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        thesis: TradeThesis | None = context.get("thesis")
        risk_approved = bool(context.get("risk_approved", False))
        if self.settings.trading_mode != "paper":
            return result(self.name, 0, ("Execution agent refuses non-paper mode.",), order=None)
        if not thesis or not risk_approved:
            return result(
                self.name,
                0,
                ("Paper order blocked until deterministic risk approval.",),
                order=None,
            )
        order = self.paper_broker.place_order(
            thesis.symbol,
            "BUY",
            thesis.quantity,
            thesis.entry,
            datetime.now(IST),
            "V2 agent workflow approved",
        )
        return result(self.name, 100, ("Paper order submitted after risk approval.",), order=order)


class PostTradeAgent(BaseAgent):
    name = "post_trade"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        return result(
            self.name,
            0,
            ("Post-trade review is fact-grounded and deferred until trade closure.",),
            review={
                "outcome": context.get("outcome", "NO_TRADE"),
                "learning_hypothesis": "None without closed trade facts",
            },
        )
