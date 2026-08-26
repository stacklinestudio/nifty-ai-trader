"""Chief coordinator for the V2 paper-only, event-driven agent workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agents.contracts import AgentResult, Decision, TradeThesis, Validation
from agents.research_agents import (
    BreadthAgent,
    GlobalResearchAgent,
    IndiaMarketAgent,
    NewsAgent,
    SignalHunterAgent,
    TechnicalAgent,
    VolatilityAgent,
)
from agents.trade_validator import IndependentTradeValidator
from agents.trading_agents import (
    ExecutionAgent,
    OptionsAgent,
    PostTradeAgent,
    RiskAgent,
    TradeBuilderAgent,
)
from config import IST, Settings
from events.bus import EventBus
from events.contracts import Event, EventType
from execution.paper_broker import PaperBroker
from learning.memory import MemoryStore
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from storage.database import Database


@dataclass(frozen=True)
class CycleResult:
    timestamp: datetime
    agent_results: dict[str, AgentResult]
    consensus: str
    conflicting_evidence: bool
    thesis: TradeThesis | None
    validation: Validation
    risk_approved: bool
    order: dict[str, Any] | None


class Orchestrator:
    """Agents communicate through events; risk remains a deterministic final veto."""

    def __init__(
        self,
        settings: Settings,
        database: Database | None = None,
        paper_broker: PaperBroker | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.database.initialize()
        self.bus = EventBus(self.database.save_event)
        self.paper_broker = paper_broker or PaperBroker(
            settings.tick_size, settings.entry_slippage_ticks, settings.exit_slippage_ticks
        )
        self.limits = DailyLimits(settings.max_trades_per_day, settings.max_daily_loss)
        self.research_agents = [
            GlobalResearchAgent(),
            IndiaMarketAgent(),
            NewsAgent(),
            TechnicalAgent(),
            VolatilityAgent(),
            BreadthAgent(),
            SignalHunterAgent(),
        ]
        self.options_agent = OptionsAgent()
        self.trade_builder = TradeBuilderAgent(
            RiskManager(settings.max_risk_per_trade, settings.max_position_value)
        )
        self.risk_agent = RiskAgent(settings, self.limits)
        self.execution_agent = ExecutionAgent(self.paper_broker, settings)
        self.validator = IndependentTradeValidator()
        self.memory = MemoryStore(settings.database_path)
        self.post_trade_agent = PostTradeAgent(self.memory)

    def _event(
        self,
        kind: EventType,
        output: dict[str, Any],
        confidence: float | None = None,
        agent: str = "orchestrator",
    ) -> None:
        self.bus.publish(
            Event(kind, agent, datetime.now(IST), output_summary=output, confidence=confidence)
        )

    @staticmethod
    def _consensus(results: dict[str, AgentResult]) -> tuple[str, bool]:
        opinions = [
            results[name].data.get("direction")
            or results[name].data.get("market_direction")
            or results[name].data.get("global_direction")
            for name in ("global_research", "india_market", "technical")
            if name in results
        ]
        bullish = sum(item == "BULLISH" for item in opinions)
        bearish = sum(item == "BEARISH" for item in opinions)
        conflicting = bullish > 0 and bearish > 0
        if conflicting:
            return "CONFLICTED", True
        if bullish >= 2:
            return "BULLISH", False
        if bearish >= 2:
            return "BEARISH", False
        return "UNCERTAIN", False

    def run_cycle(self, supplied_context: dict[str, Any] | None = None) -> CycleResult:
        self.settings.validate()
        context = dict(supplied_context or {})
        self._event(EventType.SYSTEM_STARTED, {"trading_mode": self.settings.trading_mode})
        self._event(EventType.MARKET_PREP_STARTED, {"workflow": "research"})
        results = {agent.name: agent.run(context) for agent in self.research_agents}
        consensus, conflict = self._consensus(results)
        self._event(
            EventType.MARKET_RESEARCH_COMPLETE, {"consensus": consensus, "conflict": conflict}
        )
        candidates = results["signal_hunter"].data.get("candidates", []) if not conflict else []
        if not candidates:
            validation = Validation(
                Decision.REJECT, ("No candidate passed the evidence-consensus stage.",), 0
            )
            self._event(
                EventType.TRADE_VALIDATED,
                {"decision": validation.decision.value, "reasons": validation.reasons},
            )
            self._event(EventType.RISK_REJECTED, {"reasons": ["no candidate"]})
            return CycleResult(
                datetime.now(IST), results, consensus, conflict, None, validation, False, None
            )
        candidate = candidates[0]
        self._event(
            EventType.SIGNAL_CREATED,
            {"candidate_id": candidate.candidate_id},
            candidate.confidence,
            "signal_hunter",
        )
        options = self.options_agent.run(
            {
                **context,
                "candidate": candidate,
                "max_position_value": self.settings.max_position_value,
            }
        )
        results[options.agent] = options
        ranked = options.data.get("ranked", [])
        builder = self.trade_builder.run(
            {**context, "candidate": candidate, "selected_option": ranked[0] if ranked else None}
        )
        results[builder.agent] = builder
        thesis = builder.data.get("thesis")
        self._event(
            EventType.TRADE_PROPOSED,
            {"candidate_id": candidate.candidate_id, "built": thesis is not None},
            builder.confidence,
            builder.agent,
        )
        validation = self.validator.validate(
            thesis,
            getattr(ranked[0].quote, "spread", None) if ranked else None,
            bool(context.get("market_data_fresh", False)),
            conflict,
        )
        self._event(
            EventType.TRADE_VALIDATED,
            {"decision": validation.decision.value, "reasons": validation.reasons},
            validation.confidence,
            "validator",
        )
        risk = self.risk_agent.run(
            {
                "thesis": thesis,
                "validation": validation,
                "market_data_fresh": context.get("market_data_fresh", False),
                "market_open": context.get("market_open", False),
            }
        )
        results[risk.agent] = risk
        approved = bool(risk.data.get("approved", False))
        self._event(
            EventType.RISK_APPROVED if approved else EventType.RISK_REJECTED,
            {"reasons": risk.data.get("reasons", [])},
            risk.confidence,
            risk.agent,
        )
        execution = self.execution_agent.run({"thesis": thesis, "risk_approved": approved})
        results[execution.agent] = execution
        order = execution.data.get("order")
        if order:
            self.limits.register_open()
            self._event(
                EventType.PAPER_ORDER_SENT, {"order_id": order["order_id"]}, 100, execution.agent
            )
            self._event(
                EventType.PAPER_FILL,
                {"order_id": order["order_id"], "fill_price": order["fill_price"]},
                100,
                execution.agent,
            )
        return CycleResult(
            datetime.now(IST), results, consensus, conflict, thesis, validation, approved, order
        )

    def review_trade(self, outcome_facts: dict[str, Any]) -> AgentResult:
        """Runs the post-trade review once a trade has closed.

        Recording facts and proposing a hypothesis happens inside
        PostTradeAgent; this method's only job is to make that reachable and
        auditable. It never touches live risk/strategy parameters — promotion
        of a hypothesis still requires learning.promotion_engine.decide() to
        see historical, walk-forward, and out-of-sample evidence plus human
        approval.
        """
        review = self.post_trade_agent.run(outcome_facts)
        self._event(
            EventType.TRADE_COMPLETED,
            {"outcome": outcome_facts.get("outcome", "NO_TRADE")},
            review.confidence,
            review.agent,
        )
        hypothesis = review.data.get("review", {}).get("learning_hypothesis")
        if hypothesis and hypothesis != "None without closed trade facts":
            self._event(
                EventType.LEARNING_CREATED, {"hypothesis": hypothesis}, review.confidence, review.agent
            )
        return review
