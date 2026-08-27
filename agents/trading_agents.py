"""Candidate option analysis, thesis building, adversarial validation, and supervision."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from agents.contracts import AgentResult, Decision, TradeCandidate, TradeThesis, Validation
from config import IST, Settings
from data.option_chain import OptionQuote
from learning.experiment_manager import Experiment, create_experiment
from learning.memory import MemoryStore
from learning.trade_memory import record_trade
from risk.confidence_scaling import scale_quantity
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
    """Builds the thesis's entry/stop/target/size from the risk-approved
    plan, then scales the quantity within that same approved envelope by
    confidence (risk/confidence_scaling.py) -- this changes how much of the
    already-approved size to take, never the ceiling itself, and never
    bypasses RiskAgent's veto downstream."""

    name = "trade_builder"

    def __init__(
        self, risk: RiskManager, low_confidence: float = 75.0, high_confidence: float = 95.0
    ) -> None:
        self.risk = risk
        self.low_confidence = low_confidence
        self.high_confidence = high_confidence

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
        scaled_quantity = scale_quantity(
            plan.quantity,
            candidate.confidence,
            quote.instrument.lot_size,
            self.low_confidence,
            self.high_confidence,
        )
        scaled_risk = (plan.entry - plan.stop) * scaled_quantity
        sizing_note = (
            f"Risk-based entry/stop/target computed; sized to {scaled_quantity} of "
            f"{plan.quantity} max affordable at confidence {candidate.confidence:.0f}."
        )
        thesis = TradeThesis(
            candidate,
            quote.instrument.symbol,
            plan.entry,
            plan.stop,
            plan.target,
            scaled_quantity,
            scaled_risk,
            min(candidate.confidence, 95),
            candidate.evidence + (sizing_note,),
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
    """Supervises one open position per poll tick.

    Distinguishes three separate exit signals because they carry different
    learning meaning: EXIT_TARGET/EXIT_STOP are price-level events (the
    trade played out as planned, win or lose), while EXIT_INVALIDATED means
    the thesis itself broke — a regime flip or a volatility spike after
    entry — independent of where price happens to be. The stop level this
    checks against is whatever the caller currently has (context["current_stop"]),
    not the original thesis.stop, since trailing-stop math (risk/trailing_stop.py)
    may have already moved it.
    """

    name = "trade_supervisor"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        thesis: TradeThesis | None = context.get("thesis")
        ltp = context.get("ltp")
        current_stop = context.get("current_stop")
        if thesis is None or ltp is None or current_stop is None:
            return result(
                self.name,
                0,
                ("Position, live price, or current stop unavailable; no recommendation.",),
                thesis_state="UNKNOWN",
                recommendation="HOLD",
            )
        entry_regime = context.get("entry_regime")
        current_regime = context.get("current_regime")
        entry_volatility = context.get("entry_volatility_regime")
        current_volatility = context.get("current_volatility_regime")
        regime_flip = bool(entry_regime and current_regime and entry_regime != current_regime)
        volatility_spike = current_volatility == "HIGH" and entry_volatility != "HIGH"
        if regime_flip or volatility_spike:
            reasons = (
                *(
                    (f"regime flipped from {entry_regime} to {current_regime}",)
                    if regime_flip
                    else ()
                ),
                *(("volatility expanded to HIGH after entry",) if volatility_spike else ()),
            )
            return result(
                self.name, 80, reasons, thesis_state="INVALIDATED", recommendation="EXIT_INVALIDATED"
            )
        if ltp >= thesis.target:
            return result(
                self.name,
                90,
                (f"LTP {ltp} reached target {thesis.target}.",),
                thesis_state="TARGET_HIT",
                recommendation="EXIT_TARGET",
            )
        if ltp <= current_stop:
            return result(
                self.name,
                90,
                (f"LTP {ltp} reached current stop {current_stop}.",),
                thesis_state="STOPPED",
                recommendation="EXIT_STOP",
            )
        state = "STRENGTHENING" if ltp >= thesis.entry else "WEAKENING"
        return result(
            self.name,
            55,
            ("Original thesis and current LTP compared.",),
            thesis_state=state,
            recommendation="HOLD",
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
    """Reviews a closed trade's own facts and records a candidate hypothesis.

    This never modifies live risk/strategy parameters — it only appends to
    MemoryStore and creates a CANDIDATE Experiment. Promotion still requires
    learning.promotion_engine.decide() to see historical, walk-forward, and
    out-of-sample evidence plus explicit human approval.
    """

    name = "post_trade"

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        outcome = context.get("outcome", "NO_TRADE")
        pnl = context.get("pnl")
        if outcome == "NO_TRADE" or pnl is None:
            return result(
                self.name,
                0,
                ("Post-trade review is fact-grounded and deferred until trade closure.",),
                review={"outcome": outcome, "learning_hypothesis": "None without closed trade facts"},
            )
        setup_type = context.get("setup_type", "unspecified setup")
        exit_reason = context.get("exit_reason", "unspecified exit")
        mae = context.get("mae", 0.0)
        mfe = context.get("mfe", 0.0)
        entry_regime = context.get("entry_regime")
        hold_seconds = context.get("hold_seconds")
        hypothesis = f"{setup_type} exiting via {exit_reason} produced {outcome} (pnl={pnl})."
        record_trade(
            self.memory,
            {
                "outcome": outcome,
                "pnl": pnl,
                "setup_type": setup_type,
                "exit_reason": exit_reason,
                "mae": mae,
                "mfe": mfe,
                "entry_regime": entry_regime,
                "entry_volatility_regime": context.get("entry_volatility_regime"),
                "entry_consensus": context.get("entry_consensus"),
                "agent_agreement": context.get("agent_agreement"),
                "confidence": context.get("confidence"),
                "stop_was_trailed": context.get("stop_was_trailed"),
                "hold_seconds": hold_seconds,
            },
            datetime.now(IST),
        )
        create_experiment(
            self.memory,
            Experiment(hypothesis, {"setup_type": setup_type, "entry_regime": entry_regime}, "v2"),
            datetime.now(IST),
        )
        return result(
            self.name,
            60,
            ("Closed-trade facts recorded; hypothesis is a candidate only, not a promotion.",),
            review={"outcome": outcome, "pnl": pnl, "mae": mae, "mfe": mfe, "learning_hypothesis": hypothesis},
        )
