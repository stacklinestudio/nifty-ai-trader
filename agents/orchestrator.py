"""Chief coordinator for the V2 paper-only, event-driven agent workflow."""

from __future__ import annotations

import time as time_module
from collections.abc import Callable
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
    TradeSupervisorAgent,
)
from ai.provider import build_ai_provider
from ai.refresh_cache import RefreshingAIRouter
from ai.router import AIRouter
from config import IST, Settings
from events.bus import EventBus
from events.contracts import Event, EventType
from execution.paper_broker import PaperBroker
from execution.position_persistence import position_state_from_dict, position_state_to_dict
from execution.position_supervisor import PositionState, TickResult
from execution.position_supervisor import tick as supervise_tick
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.obsidian import ObsidianExporter, render_decision_note
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from monitoring.live_status_server import dashboard_url, kite_chart_url, live_status_url
from monitoring.logger import configure_logger
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from storage.database import Database
from storage.models import SignalRecord

logger = configure_logger(__name__)

_EXIT_REASON_TO_EVENT = {
    "TAKE_PROFIT": EventType.TAKE_PROFIT,
    "STOP_LOSS": EventType.STOP_LOSS,
    "THESIS_INVALIDATED": EventType.THESIS_INVALIDATED,
    "FORCED_EXIT": EventType.FORCED_EXIT,
}


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
    # Brief 20 (Obsidian knowledge layer): the same real score_attribution
    # dict already persisted via database.save_signal below -- exposed here
    # too so open_position can carry it into PositionState without a
    # separate DB read/join. None whenever supplied_context never went
    # through the live-context pipeline (see the save_signal guard above).
    score_attribution: dict[str, Any] | None = None
    # Final Brief follow-up: the same real instrument_token _on_risk_
    # decision uses for the notification's Kite chart link, exposed here
    # too so open_position can carry it into PositionState the same way
    # score_attribution above is carried through -- letting the
    # dashboard's own current-position card build the identical real
    # kite_chart_url() for an open position, not just the notification.
    instrument_token: int | None = None


def _selected_option_instrument_token(context: dict[str, Any]) -> int | None:
    """The real instrument_token of whatever option OptionsAgent most
    recently selected for this cycle (`context["selected_option"]`,
    Brief 5's option_selector.py), or None when no real option was ever
    selected -- e.g. the no-candidate/backtest paths, which never
    populate this key. Never fabricated."""
    selected_option = context.get("selected_option")
    return selected_option.quote.instrument.instrument_token if selected_option else None


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
        ai_router: AIRouter | None = None,
        dry_run: bool = False,
    ) -> None:
        """dry_run (Brief 10): the safe, explicit replacement for manually
        zeroing every Settings notification/vault field one at a time in a
        one-off investigation script -- a real incident this session, where
        an unpatched `Orchestrator(Settings())` picked up this session's
        real Discord/Telegram credentials from .env.local and sent a
        synthetic test cycle as real notifications. dry_run=True builds
        real, genuinely unconfigured (no-op-by-construction) Telegram/
        Discord/Obsidian regardless of what settings carries -- one flag,
        not eight fields to remember. Never touches ai_router/AI provider
        selection (a separate, already-explicit mechanism via
        settings.ai_provider) -- a script that wants real AI output but no
        real notifications is exactly Brief 10 Part A's use case.
        """
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.database.initialize()
        self.bus = EventBus(self.database.save_event)
        self.paper_broker = paper_broker or PaperBroker(
            settings.tick_size, settings.entry_slippage_ticks, settings.exit_slippage_ticks
        )
        self.limits = DailyLimits(settings.max_trades_per_day, settings.max_daily_loss)
        # Brief 8 Part C: real AI enrichment, strictly synthesis/narrative
        # -- see GlobalResearchAgent/PostTradeAgent's own docstrings for
        # exactly which fields this can and cannot touch. Defaults to
        # UnavailableProvider (build_ai_provider) whenever
        # settings.ai_provider isn't explicitly "anthropic" with a real
        # key configured -- identical behavior to every prior brief.
        # Injectable (like database/paper_broker above) so
        # tests/test_ai_safety.py can prove an adversarial provider
        # cannot change a single real trade decision.
        self.ai_router = ai_router or AIRouter(build_ai_provider(settings))
        # Brief 10: GlobalResearchAgent's synthesis (and, separately,
        # data/rss_news.py's news classification via main.py's
        # context_provider -- see orchestrator.synthesis_ai_router's use
        # there) only actually calls the real provider once per
        # ai_synthesis_refresh_seconds, not once per real entry scan --
        # global market conditions/news don't meaningfully change minute to
        # minute the way price-based setup detection correctly does. Wraps
        # whatever self.ai_router ends up being (default-built or injected,
        # e.g. tests/test_ai_safety.py's adversarial provider), so this is
        # additive, not a behavior change for any test that calls run_cycle
        # once. PostTradeAgent below deliberately keeps the raw,
        # un-throttled self.ai_router -- each closed trade's own real facts
        # are genuinely different from the last one's, so reusing a cached
        # explanation across trades would be wrong, not just wasteful.
        self.synthesis_ai_router = RefreshingAIRouter(
            self.ai_router.provider, settings.ai_synthesis_refresh_seconds
        )
        self.research_agents = [
            GlobalResearchAgent(self.synthesis_ai_router),
            IndiaMarketAgent(),
            NewsAgent(),
            TechnicalAgent(),
            VolatilityAgent(),
            BreadthAgent(),
            SignalHunterAgent(),
        ]
        self.options_agent = OptionsAgent()
        self.trade_builder = TradeBuilderAgent(
            RiskManager(settings.max_risk_per_trade, settings.max_position_value),
            settings.signal_threshold,
        )
        self.risk_agent = RiskAgent(settings, self.limits)
        self.execution_agent = ExecutionAgent(self.paper_broker, settings)
        self.validator = IndependentTradeValidator()
        self.memory = MemoryStore(settings.database_path)
        self.post_trade_agent = PostTradeAgent(self.memory, self.ai_router)
        self.trade_supervisor_agent = TradeSupervisorAgent()
        if dry_run:
            self.telegram = TelegramNotifier()
            self.discord = DiscordNotifier("")
            self.obsidian = ObsidianExporter("")
        else:
            self.telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
            self.discord = DiscordNotifier(
                settings.discord_webhook_url,
                webhooks_by_category=webhooks_by_category_from_settings(settings),
            )
            self.obsidian = ObsidianExporter(settings.obsidian_vault_path)
        self._state: _CycleState | None = None
        # (direction, setup_type, entry_regime) of every stop-out closed
        # today, for the re-entry re-validation gate below. Cleared only by
        # constructing a new Orchestrator (i.e. a new day, per the
        # recommended cron/systemd-relaunch deployment) -- there is no
        # separate "new day" reset call, since a process is expected to be
        # scoped to one trading day.
        self._stopped_out_today: list[tuple[str, str, str | None]] = []

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
        event = Event(kind, agent, datetime.now(IST), output_summary=output, confidence=confidence)
        self.bus.publish(event)
        try:
            self.discord.send_event(event)
        except Exception as exc:  # noqa: BLE001 - a notification bug must never break the trading loop.
            logger.warning("discord_event_dispatch_failed event_type=%s error=%s", kind, exc)
        # Separate try/except from Discord's above, deliberately -- a
        # Discord failure must not prevent Telegram from being attempted,
        # and vice versa. Previously self.telegram was only ever reached
        # from the crash-recovery CRITICAL path (see recover_open_
        # positions below); a normal research/signal/entry/exit cycle
        # never notified Telegram at all, only Discord.
        try:
            self.telegram.send_event(event)
        except Exception as exc:  # noqa: BLE001 - a notification bug must never break the trading loop.
            logger.warning("telegram_event_dispatch_failed event_type=%s error=%s", kind, exc)

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
        # Brief 12 Part A: persist this cycle's real score attribution
        # (execution/live_context.py::_add_candidate sets this key
        # unconditionally, before its own confidence-threshold check) --
        # every real evaluation, not just ones that become a trade.
        # live_context.py stays I/O-free by its own design; this is the
        # one real place that both sees supplied_context and already owns
        # a Database connection. Absent for a supplied_context that never
        # went through the live-context pipeline (e.g. most tests'
        # hand-built dicts) -- nothing to persist, not an error.
        attribution = state.context.get("score_attribution")
        if attribution is not None:
            try:
                self.database.save_signal(
                    SignalRecord(
                        timestamp=datetime.fromisoformat(attribution["now"]),
                        direction=attribution["direction"],
                        confidence=attribution["confidence"],
                        features=attribution,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a persistence bug must never break the trading loop.
                logger.warning("score_attribution_persist_failed error=%s", exc)
        self._event(EventType.SYSTEM_STARTED, {"trading_mode": self.settings.trading_mode})
        self._event(EventType.MARKET_PREP_STARTED, {"workflow": "research"})

        # signal_hunter runs after its siblings, not alongside them, so its
        # regime-aware confidence weighting (strategy/regime_selector.py) can
        # see their real output instead of requiring the caller to duplicate
        # india_market/volatility/breadth's own findings by hand.
        context_agents = [a for a in self.research_agents if a.name != "signal_hunter"]
        signal_hunter = next(a for a in self.research_agents if a.name == "signal_hunter")
        state.results = {agent.name: agent.run(state.context) for agent in context_agents}
        signal_context = {
            **state.context,
            "market_regime": state.results["india_market"].data.get("market_regime"),
            "volatility_regime": state.results["volatility"].data.get("volatility_regime"),
            "breadth_participation": state.results["breadth"].data.get("participation"),
            "news_direction": state.results["news"].data.get("direction"),
            "news_confidence": state.results["news"].confidence,
        }
        # Persisted onto state.context (not just the local signal_context) so
        # _on_trade_proposed's re-entry check can see the regime this
        # candidate was found in.
        state.context["market_regime"] = signal_context["market_regime"]
        state.results[signal_hunter.name] = signal_hunter.run(signal_context)
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
            attribution,
            _selected_option_instrument_token(state.context),
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
            self._blocked_reentry(state.thesis, state.context.get("market_regime")),
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
            instrument_token = _selected_option_instrument_token(state.context)
            paper_fill_summary = {
                "order_id": order["order_id"],
                "fill_price": order["fill_price"],
                # Follow-up to Brief 25/Final Brief: the dashboard is now
                # the fuller, better view (10 real sections vs. one
                # position card), so it -- not /live -- is the primary
                # link a person actually clicks from a notification.
                # /live stays real and reachable for anything that still
                # wants just the position card. Same real machine local
                # network address for both (never a public URL);
                # real_local_ip() itself never raises (falls back to
                # 127.0.0.1 on any real socket error).
                "dashboard_url": dashboard_url(self.settings),
                "live_status_url": live_status_url(self.settings),
            }
            # Final Brief Part B: the real Kite web chart for this exact
            # traded contract -- NFO is the real, only exchange option
            # contracts trade on in this project. None (never a
            # fabricated URL) when `selected_option`/its real
            # instrument_token isn't available -- e.g. the demo/backtest
            # paths, which never populate context["selected_option"].
            # Requires the person's OWN browser to already have an
            # active kite.zerodha.com login session, entirely separate
            # from and outside the bot's own API access token.
            chart_url = kite_chart_url("NFO", state.thesis.symbol, instrument_token)
            if chart_url:
                paper_fill_summary["kite_chart_url"] = chart_url
            self._event(
                EventType.PAPER_FILL,
                paper_fill_summary,
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

    def open_position(
        self, cycle: CycleResult, now: datetime | None = None
    ) -> PositionState | None:
        """Builds trackable state for a just-filled entry order and persists
        it immediately (storage.database's open_positions table) so a
        process restart can find and resume it — see recover_open_positions.
        Returns None when the cycle produced no fill — there is nothing to
        supervise or persist."""
        if not cycle.order or cycle.thesis is None:
            return None
        india_market = cycle.agent_results.get("india_market")
        volatility = cycle.agent_results.get("volatility")
        state = PositionState.opening(
            cycle.thesis,
            now or datetime.now(IST),
            india_market.data.get("market_regime") if india_market else None,
            volatility.data.get("volatility_regime") if volatility else None,
            cycle.consensus,
            self._agent_directions(cycle.agent_results),
            cycle.order["order_id"],
            cycle.score_attribution,
            cycle.validation.reasons,
            cycle.instrument_token,
        )
        self.database.save_open_position(
            state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state)
        )
        return state

    def recover_open_positions(self) -> list[PositionState]:
        """Reconstructs any position left open by a prior process (crash,
        restart, kill) from storage.database's open_positions table. A row
        that fails to reconstruct is NOT silently dropped or assumed fine —
        it is escalated via a CRITICAL notification and left in the table
        for a human to check the actual paper/broker state by hand.
        """
        recovered: list[PositionState] = []
        for row in self.database.open_positions():
            try:
                recovered.append(position_state_from_dict(row["state"]))
            except (KeyError, ValueError, TypeError) as exc:
                message = (
                    f"Could not reconstruct open position order_id={row['order_id']} "
                    f"opened_at={row['opened_at']}: {type(exc).__name__}: {exc}. "
                    "This position is NOT being resumed automatically -- check the "
                    "actual paper broker/database state by hand."
                )
                logger.error("open_position_recovery_failed %s", message)
                self.telegram.send_message("CRITICAL", message)
                self.discord.send_message("CRITICAL", message)
                self._event(
                    EventType.SYSTEM_ERROR,
                    {"reason": "open_position_recovery_failed", "order_id": row["order_id"]},
                )
        return recovered

    @staticmethod
    def _agent_directions(results: dict[str, AgentResult]) -> dict[str, str]:
        """Which research agents agreed/disagreed on direction at entry —
        recorded for the learning loop (Part C), not used to gate anything
        here; consensus/conflict already did that during run_cycle."""
        directions = {}
        for name in ("global_research", "india_market", "technical"):
            agent_result = results.get(name)
            if agent_result:
                directions[name] = (
                    agent_result.data.get("direction")
                    or agent_result.data.get("market_direction")
                    or agent_result.data.get("global_direction")
                    or "UNKNOWN"
                )
        return directions

    def _blocked_reentry(self, thesis: TradeThesis | None, current_regime: str | None) -> bool:
        """True when this candidate repeats a same-day stop-out: same
        direction, same setup type, same regime it was stopped out in. A
        different setup type or a regime change is enough to prove this
        isn't just the same broken thesis re-firing (Brief 3, Part B item 4,
        user decision)."""
        if thesis is None:
            return False
        candidate = thesis.candidate
        return any(
            direction == candidate.direction
            and setup_type == candidate.setup_type
            and entry_regime == current_regime
            for direction, setup_type, entry_regime in self._stopped_out_today
        )

    def supervise_once(
        self,
        state: PositionState,
        ltp: float | None,
        now: datetime,
        current_regime: str | None = None,
        current_volatility_regime: str | None = None,
    ) -> TickResult:
        """One deterministic tick: updates the trailing stop and MAE/MFE,
        decides HOLD/EXIT via position_supervisor.tick, and on EXIT actually
        closes the position — a real paper SELL, the matching audit event,
        and review_trade with real outcome facts. Side-effecting by design;
        run_supervised (or a test) supplies ltp/now for each call.
        """
        regime_context = {
            "entry_regime": state.entry_regime,
            "current_regime": current_regime,
            "entry_volatility_regime": state.entry_volatility_regime,
            "current_volatility_regime": current_volatility_regime,
        }
        result = supervise_tick(
            state,
            ltp,
            now,
            self.settings.forced_exit_time,
            self.settings.stale_data_seconds,
            self.trade_supervisor_agent,
            regime_context,
            self.settings.trail_percent,
        )
        if ltp is not None and state.entry_order_id:
            # Brief 25: previously save_open_position was only ever
            # called once, at open_position() time -- the persisted row
            # went stale immediately (real crash recovery still worked,
            # since it only needs entry/stop/target, but nothing could
            # read a real, current LTP/trailed-stop from storage). Kept
            # fresh on every real observed tick so the live status page
            # (and any future reader) sees genuinely current state, not
            # a snapshot from whenever the position opened. Harmless on
            # an exit tick too -- _close_position's own close_open_
            # position() call below deletes the row moments later.
            self.database.save_open_position(
                state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state)
            )
        if result.notify_stale:
            logger.warning("stale_price_during_open_position symbol=%s", state.thesis.symbol)
            self._event(
                EventType.SYSTEM_ERROR,
                {"reason": "stale_price_during_open_position", "symbol": state.thesis.symbol},
            )
        if result.should_exit:
            self._close_position(state, result, now)
        return result

    def _close_position(self, state: PositionState, result: TickResult, now: datetime) -> None:
        order = self.paper_broker.place_order(
            state.thesis.symbol, "SELL", state.thesis.quantity, result.exit_price, now, result.reason
        )
        pnl = (order["fill_price"] - state.thesis.entry) * state.thesis.quantity - order[
            "estimated_costs"
        ]
        self.limits.register_close(pnl)
        if state.entry_order_id:
            self.database.close_open_position(state.entry_order_id)
        if result.reason == "STOP_LOSS":
            candidate = state.thesis.candidate
            self._stopped_out_today.append(
                (candidate.direction, candidate.setup_type, state.entry_regime)
            )
        hold_seconds = (now - state.opened_at).total_seconds()
        self._event(
            _EXIT_REASON_TO_EVENT.get(result.reason, EventType.FORCED_EXIT),
            {
                "order_id": order["order_id"],
                "reason": result.reason,
                "entry": state.thesis.entry,
                "exit": order["fill_price"],
                "pnl": pnl,
                "mae": state.mae,
                "mfe": state.mfe,
                "hold_seconds": hold_seconds,
            },
            100,
            "execution",
        )
        self.review_trade(
            {
                "outcome": "WIN" if pnl > 0 else "LOSS",
                "pnl": pnl,
                "mae": state.mae,
                "mfe": state.mfe,
                "setup_type": state.thesis.candidate.setup_type,
                "exit_reason": result.reason,
                "hold_seconds": hold_seconds,
                "entry_regime": state.entry_regime,
                "entry_volatility_regime": state.entry_volatility_regime,
                "entry_consensus": state.entry_consensus,
                "agent_agreement": state.entry_agent_directions,
                "confidence": state.thesis.confidence,
                "stop_was_trailed": state.current_stop != state.thesis.stop,
            }
        )
        # Real per-trade journal entry, written as the day happens -- was
        # previously only reachable via the standalone `export-obsidian`
        # CLI command (a single placeholder "Daily Research" note on
        # manual request), never during a normal live day. Brief 20:
        # reorganized into 06-Trades/YYYY/YYYY-MM-DD/, and enriched with
        # the real score_attribution 7-component breakdown and real
        # validator reasoning carried on `state` since open_position()
        # (see PositionState.entry_score_attribution/entry_validation_
        # reasons) -- falls back to a minimal real attribution dict for a
        # cycle that never went through the live-context pipeline (most
        # tests' hand-built contexts), never a fabricated one.
        # ObsidianExporter.export_markdown() already fails closed on
        # OSError internally (returns None, no vault configured or a real
        # write failure); this broader except also catches anything else
        # a bad value could raise, matching the same non-fatal try/except
        # pattern Discord/Telegram already use in _event() -- a vault
        # write failure must never break the trading loop.
        try:
            attribution = state.entry_score_attribution or {
                "setup_type": state.thesis.candidate.setup_type,
                "direction": state.thesis.candidate.direction,
                "regime": state.entry_regime,
                "confidence": state.thesis.confidence,
                "now": now.isoformat(),
            }
            outcome = {
                "symbol": state.thesis.symbol,
                "entry": state.thesis.entry,
                "exit": order["fill_price"],
                "quantity": state.thesis.quantity,
                "pnl": pnl,
                "exit_reason": result.reason,
                "mae": state.mae,
                "mfe": state.mfe,
                "hold_seconds": hold_seconds,
                "stop_was_trailed": state.current_stop != state.thesis.stop,
            }
            self.obsidian.export_markdown(
                f"06-Trades/{now.year}/{now.date().isoformat()}",
                f"{state.thesis.symbol}-{order['order_id']}",
                render_decision_note(attribution, validation_reasons=state.entry_validation_reasons, outcome=outcome),
            )
        except Exception as exc:  # noqa: BLE001 - a vault write failure must never break the trading loop.
            logger.warning("obsidian_trade_journal_export_failed order_id=%s error=%s", order["order_id"], exc)

    def run_supervised(
        self,
        state: PositionState,
        quote_source: Callable[[], float | None],
        poll_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        regime_source: Callable[[], tuple[str | None, str | None]] | None = None,
        max_consecutive_failures: int | None = None,
    ) -> TickResult:
        """Real-time polling loop for live/paper use: blocks until the
        position closes (target, stop, invalidation, the 15:15 forced exit,
        or repeated data-fetch failure). All HOLD/EXIT decision logic lives
        in supervise_once/tick — this function wires real time and IO to it,
        plus the bounded-retry failure handling below, since an unhandled
        exception here would silently stop monitoring a real open position.
        Deliberately the one piece of this feature not covered by a
        real-time test; tests drive supervise_once/tick directly with
        synthetic clocks instead, and test this method's retry/give-up
        behavior with an injected failing quote_source.
        """
        poll_seconds = (
            poll_seconds if poll_seconds is not None else self.settings.supervision_poll_seconds
        )
        max_consecutive_failures = (
            max_consecutive_failures
            if max_consecutive_failures is not None
            else self.settings.max_consecutive_tick_failures
        )
        clock = clock or (lambda: datetime.now(IST))
        sleeper = sleeper or time_module.sleep
        consecutive_failures = 0
        while True:
            try:
                now = clock()
                ltp = quote_source()
                current_regime, current_volatility_regime = (
                    regime_source() if regime_source else (None, None)
                )
                result = self.supervise_once(
                    state, ltp, now, current_regime, current_volatility_regime
                )
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001 - outermost guard for a live position monitor.
                consecutive_failures += 1
                logger.error(
                    "supervision_tick_failed attempt=%d/%d error=%s: %s",
                    consecutive_failures,
                    max_consecutive_failures,
                    type(exc).__name__,
                    exc,
                )
                self._event(
                    EventType.SYSTEM_ERROR,
                    {
                        "reason": "supervision_tick_failed",
                        "attempt": consecutive_failures,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "supervision_giving_up_forcing_exit consecutive_failures=%d symbol=%s",
                        consecutive_failures,
                        state.thesis.symbol,
                    )
                    forced = TickResult(
                        True, "FORCED_EXIT_DATA_FAILURE", state.last_valid_ltp
                    )
                    self._close_position(state, forced, state.last_quote_at)
                    return forced
                sleeper(poll_seconds)
                continue
            if result.should_exit:
                return result
            sleeper(poll_seconds)
