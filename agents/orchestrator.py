"""Chief coordinator for the V2 paper-only, event-driven agent workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class _CycleState:
    """Scratch space bus subscribers fill in as each stage's event arrives.

    This — not a hand-threaded local variable — is how one stage's output
    reaches the next stage's handler: run_cycle populates it as far as
    research goes, then publish() synchronously runs the whole subscriber
    chain (each handler publishes the next event before returning) before
    control comes back to run_cycle to read the final state.
    """

    context: dict[str, Any]
    results: dict[str, AgentResult] = field(default_factory=dict)
    consensus: str = "UNCERTAIN"
    conflict: bool = False
    thesis: TradeThesis | None = None
    validation: Validation | None = None
    risk_approved: bool = False
    order: dict[str, Any] | None = None


class Orchestrator:
    """Each stage publishes an event on completion; the next stage is a bus
    subscriber reacting to that event, not a direct call from the previous
    stage. Risk remains a deterministic final veto."""

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
        self._state: _CycleState | None = None

        self.bus.subscribe(EventType.MARKET_RESEARCH_COMPLETE, self._on_research_complete)
        self.bus.subscribe(EventType.SIGNAL_CREATED, self._on_signal_created)
        self.bus.subscribe(EventType.TRADE_PROPOSED, self._on_trade_proposed)
        self.bus.subscribe(EventType.TRADE_VALIDATED, self._on_trade_validated)
        self.bus.subscribe(EventType.RISK_APPROVED, self._on_risk_decision)
        self.bus.subscribe(EventType.RISK_REJECTED, self._on_risk_decision)

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
        state = _CycleState(context=dict(supplied_context or {}))
        self._state = state
        self._event(EventType.SYSTEM_STARTED, {"trading_mode": self.settings.trading_mode})
        self._event(EventType.MARKET_PREP_STARTED, {"workflow": "research"})

        state.results = {agent.name: agent.run(state.context) for agent in self.research_agents}
        state.consensus, state.conflict = self._consensus(state.results)
        # Publishing this event synchronously runs the entire subscriber chain
        # below (research -> signal -> options/build -> validate -> risk ->
        # execute) before this call returns; by the next line, `state` holds
        # the cycle's final outcome.
        self._event(
            EventType.MARKET_RESEARCH_COMPLETE, {"consensus": state.consensus, "conflict": state.conflict}
        )
        assert state.validation is not None  # always set by _on_research_complete's chain
        return CycleResult(
            datetime.now(IST),
            state.results,
            state.consensus,
            state.conflict,
            state.thesis,
            state.validation,
            state.risk_approved,
            state.order,
        )

    def _on_research_complete(self, event: Event) -> None:
        state = self._state
        candidates = (
            state.results["signal_hunter"].data.get("candidates", []) if not state.conflict else []
        )
        if not candidates:
            state.validation = Validation(
                Decision.REJECT, ("No candidate passed the evidence-consensus stage.",), 0
            )
            self._event(
                EventType.TRADE_VALIDATED,
                {"decision": state.validation.decision.value, "reasons": state.validation.reasons},
            )
            self._event(EventType.RISK_REJECTED, {"reasons": ["no candidate"]})
            return
        candidate = candidates[0]
        state.context["candidate"] = candidate
        self._event(
            EventType.SIGNAL_CREATED,
            {"candidate_id": candidate.candidate_id},
            candidate.confidence,
            "signal_hunter",
        )

    def _on_signal_created(self, event: Event) -> None:
        state = self._state
        candidate = state.context["candidate"]
        options = self.options_agent.run(
            {**state.context, "max_position_value": self.settings.max_position_value}
        )
        state.results[options.agent] = options
        ranked = options.data.get("ranked", [])
        state.context["ranked"] = ranked
        state.context["selected_option"] = ranked[0] if ranked else None
        builder = self.trade_builder.run(state.context)
        state.results[builder.agent] = builder
        state.thesis = builder.data.get("thesis")
        self._event(
            EventType.TRADE_PROPOSED,
            {"candidate_id": candidate.candidate_id, "built": state.thesis is not None},
            builder.confidence,
            builder.agent,
        )

    def _on_trade_proposed(self, event: Event) -> None:
        state = self._state
        ranked = state.context.get("ranked", [])
        validation = self.validator.validate(
            state.thesis,
            getattr(ranked[0].quote, "spread", None) if ranked else None,
            bool(state.context.get("market_data_fresh", False)),
            state.conflict,
        )
        state.validation = validation
        self._event(
            EventType.TRADE_VALIDATED,
            {"decision": validation.decision.value, "reasons": validation.reasons},
            validation.confidence,
            "validator",
        )

    def _on_trade_validated(self, event: Event) -> None:
        state = self._state
        if state.thesis is None:
            # TRADE_VALIDATED also fires from _on_research_complete's early
            # no-candidate branch (for the audit trail); there's nothing for
            # risk to evaluate yet, and RISK_REJECTED is already published
            # directly by that branch.
            return
        risk = self.risk_agent.run(
            {
                "thesis": state.thesis,
                "validation": state.validation,
                "market_data_fresh": state.context.get("market_data_fresh", False),
                "market_open": state.context.get("market_open", False),
            }
        )
        state.results[risk.agent] = risk
        state.risk_approved = bool(risk.data.get("approved", False))
        self._event(
            EventType.RISK_APPROVED if state.risk_approved else EventType.RISK_REJECTED,
            {"reasons": risk.data.get("reasons", [])},
            risk.confidence,
            risk.agent,
        )

    def _on_risk_decision(self, event: Event) -> None:
        state = self._state
        if state.thesis is None:
            # The "no candidate" path publishes RISK_REJECTED directly without
            # a thesis ever having been built; there is nothing to execute.
            return
        execution = self.execution_agent.run(
            {"thesis": state.thesis, "risk_approved": state.risk_approved}
        )
        state.results[execution.agent] = execution
        order = execution.data.get("order")
        state.order = order
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
