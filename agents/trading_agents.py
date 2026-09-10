"""Candidate option analysis, thesis building, adversarial validation, and supervision."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from agents.base import BaseAgent
from agents.contracts import AgentResult, Decision, TradeCandidate, TradeThesis, Validation
from ai.prompts import POST_TRADE_EXPLANATION, POST_TRADE_HYPOTHESIS, POST_TRADE_LESSON
from ai.router import AIRouter
from config import IST, Settings
from data.option_chain import OptionQuote
from intelligence.oi_buildup import detect_buildup
from learning.experiment_manager import Experiment, create_experiment
from learning.hypothesis import HypothesisCondition, evaluate_hypothesis, parse_hypothesis_condition
from learning.learning_event import build_learning_event, record_learning_event
from learning.memory import MemoryStore
from learning.prediction_review import compute_prediction_error, record_prediction_review
from learning.trade_memory import record_trade
from learning.trade_outcome import build_trade_outcome_record
from monitoring.logger import configure_logger
from risk.confidence_scaling import scale_quantity
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from strategy.option_selector import OptionSelector

logger = configure_logger(__name__)


def result(name: str, confidence: float, evidence: tuple[str, ...], **data: Any) -> AgentResult:
    return AgentResult(name, datetime.now(IST), confidence, evidence, data)


class OptionsAgent(BaseAgent):
    """Ranks/selects the tradeable option contract, and separately reports
    OI buildup/unwinding by strike (intelligence/oi_buildup.py) as
    informational evidence -- the closest honest public-data equivalent to
    "watching where large participants are positioning." This never gates
    selection or overrides the adversarial validator/risk veto on its own;
    it is recorded for audit and for the learning loop to later evaluate
    whether it actually correlated with anything."""

    name = "options"

    def analyze(self, context: dict[str, Any]) -> AgentResult:
        candidate: TradeCandidate | None = context.get("candidate")
        options: list[OptionQuote] = context.get("option_quotes", [])
        buildup = detect_buildup(options, context.get("previous_option_quotes", []))
        if not candidate or not options:
            return result(
                self.name,
                0,
                ("Candidate option quotes unavailable.",),
                ranked=[],
                oi_buildup_bias=buildup.bias,
                oi_buildup_reasons=buildup.reasons,
            )
        selected = OptionSelector().select(
            options,
            candidate.direction,
            float(context.get("spot", 0)),
            float(context.get("max_position_value", 5000)),
        )
        if selected is None:
            return result(
                self.name,
                0,
                ("No liquid, affordable option contract met constraints.",),
                ranked=[],
                oi_buildup_bias=buildup.bias,
                oi_buildup_reasons=buildup.reasons,
            )
        return result(
            self.name,
            min(100, max(0, selected.score + 90)),
            ("Liquidity, spread, expiry, and affordability ranked.", *buildup.reasons),
            ranked=[selected],
            oi_buildup_bias=buildup.bias,
            oi_buildup_reasons=buildup.reasons,
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

    Runs strictly AFTER a trade has already closed (agents/orchestrator.py
    ::_close_position calls review_trade only once the paper SELL order
    has already filled) -- record_trade/create_experiment above use only
    the real deterministic outcome facts already established by then.
    ai_explanation (Brief 8 Part C) is generated from those SAME
    already-recorded facts, strictly for human review of the trading log;
    it is appended to this method's own return value only, after
    record_trade/create_experiment have already run on the deterministic
    facts alone -- it cannot retroactively affect a trade that has
    already closed, and no other code anywhere reads it.
    """

    name = "post_trade"
    timeout_seconds = 20.0  # see GlobalResearchAgent's identical comment on why

    def __init__(self, memory: MemoryStore, ai_router: AIRouter | None = None) -> None:
        self.memory = memory
        self.ai_router = ai_router or AIRouter()

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
        review_context_facts = context.get("trade_review_context")

        # Phase 2 Piece 5, Requirement 2: when the real decision-ledger/
        # candidate/execution context is available, record_trade is called
        # with the canonical TradeOutcomeRecord -- a strict superset of the
        # older inline dict (learning/trade_outcome.py::TradeOutcomeRecord.
        # to_dict() keeps the exact "pnl"/"setup_type"/"entry_regime" keys
        # learning/pattern_memory.py::stats_for already depends on) --
        # instead of the thinner ad hoc dict. Falls back to that original
        # minimal dict, completely unchanged, when no real context is
        # available (every existing test's hand-built outcome_facts) --
        # fully backward compatible.
        outcome_record = None
        if review_context_facts is not None:
            outcome_record = build_trade_outcome_record(review_context_facts)
            record_trade(self.memory, outcome_record.to_dict(), datetime.now(IST))
        else:
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
        ai_explanation = self._explain(outcome, pnl, setup_type, exit_reason, mae, mfe, hold_seconds)

        # Phase 2 Pieces 2-3, extended by Piece 5: real historical context
        # -> AI hypothesis -> deterministic evaluation, prediction-error ->
        # AI lesson, and (Piece 5, Requirement 4) the canonical learning
        # event -- generated unconditionally for both winning and losing
        # trades, no branch on outcome sign anywhere below. Purely
        # additive to the deterministic recording above (already run,
        # unchanged): absent (None-valued, never fabricated) whenever
        # context["trade_review_context"] wasn't supplied.
        ai_hypothesis_condition: HypothesisCondition | None = None
        ai_hypothesis_evaluation_dict: dict[str, Any] | None = None
        ai_lesson: str | None = None
        if review_context_facts is not None:
            ai_hypothesis_condition = self._propose_hypothesis(review_context_facts)
            ai_hypothesis_experiment_id: str | None = None
            if ai_hypothesis_condition is not None:
                evaluation = evaluate_hypothesis(ai_hypothesis_condition, self.memory, datetime.now(IST))
                ai_hypothesis_evaluation_dict = evaluation.to_dict()
                ai_hypothesis_experiment_id = create_experiment(
                    self.memory,
                    Experiment(
                        f"AI hypothesis: {ai_hypothesis_condition.metric} on "
                        f"{ai_hypothesis_condition.setup_type}/{ai_hypothesis_condition.regime} "
                        f"{ai_hypothesis_condition.operator} {ai_hypothesis_condition.threshold}",
                        {
                            "source": "ai_proposed",
                            "hypothesis_condition": ai_hypothesis_condition.to_dict(),
                            "evaluated_at_creation": ai_hypothesis_evaluation_dict,
                            "outcome_id": outcome_record.outcome_id if outcome_record else None,
                        },
                        "v2",
                    ),
                    datetime.now(IST),
                )
                # Requirement 2's "original AI prediction/hypothesis
                # reference": the already-persisted "trade" record can't
                # be retroactively updated (MemoryStore is deliberately
                # append-only, no UPDATE method exists at all -- see its
                # own docstring), so the reference is carried on the
                # richer, synthesized learning-event snapshot below
                # instead, built moments later in this same real flow.
                if outcome_record is not None:
                    outcome_record = dataclasses.replace(
                        outcome_record, ai_hypothesis_reference=ai_hypothesis_experiment_id
                    )

            prediction_error = compute_prediction_error(review_context_facts)
            ai_lesson = self._generate_lesson(prediction_error)
            record_prediction_review(self.memory, prediction_error, ai_lesson, datetime.now(IST))

            if outcome_record is not None:
                learning_event = build_learning_event(outcome_record, prediction_error, datetime.now(IST))
                record_learning_event(self.memory, learning_event, datetime.now(IST))

        return result(
            self.name,
            60,
            ("Closed-trade facts recorded; hypothesis is a candidate only, not a promotion.",),
            review={"outcome": outcome, "pnl": pnl, "mae": mae, "mfe": mfe, "learning_hypothesis": hypothesis},
            ai_explanation=ai_explanation,
            ai_hypothesis_condition=ai_hypothesis_condition.to_dict() if ai_hypothesis_condition else None,
            ai_hypothesis_evaluation=ai_hypothesis_evaluation_dict,
            ai_lesson=ai_lesson,
            outcome_id=outcome_record.outcome_id if outcome_record else None,
        )

    def _explain(
        self,
        outcome: str,
        pnl: float,
        setup_type: str,
        exit_reason: str,
        mae: float,
        mfe: float,
        hold_seconds: float | None,
    ) -> str | None:
        """Real, plain-language explanation of an already-closed trade,
        for human review only -- see this class's own docstring for why
        that's structurally guaranteed, not just a convention. Any
        failure (no provider configured, network, parsing) is caught
        LOCALLY and returns None, same reasoning as GlobalResearchAgent's
        identical pattern: must never escape to BaseAgent.run()'s outer
        try/except and discard the already-recorded real trade facts.
        """
        try:
            facts = {
                "outcome": outcome,
                "pnl": pnl,
                "setup_type": setup_type,
                "exit_reason": exit_reason,
                "mae": mae,
                "mfe": mfe,
                "hold_seconds": hold_seconds,
            }
            analysis = self.ai_router.analyze(POST_TRADE_EXPLANATION, facts)
            return analysis.summary or None
        except Exception as exc:  # noqa: BLE001 - AI enrichment is optional; failure must not affect the already-recorded real trade facts.
            logger.warning("post_trade_ai_explanation_failed error=%s", exc)
            return None

    def _propose_hypothesis(self, review_context_facts: dict[str, Any]) -> HypothesisCondition | None:
        """Phase 2 Piece 2: asks the real AI provider for exactly one
        falsifiable hypothesis, given the real trade + real prior pattern
        stats already assembled by learning/trade_review_context.py.
        Strictly parsed/validated by learning.hypothesis::
        parse_hypothesis_condition -- a malformed or empty AI response
        becomes None here, never a guessed condition. Any failure (no
        provider configured, network, parsing) is caught locally, same
        reasoning as _explain above: must never escape and discard the
        already-recorded deterministic trade facts."""
        try:
            analysis = self.ai_router.analyze(POST_TRADE_HYPOTHESIS, review_context_facts)
            structured = analysis.source_facts.get("structured")
            return parse_hypothesis_condition(structured)
        except Exception as exc:  # noqa: BLE001 - AI enrichment is optional; failure must not affect the already-recorded real trade facts.
            logger.warning("post_trade_ai_hypothesis_failed error=%s", exc)
            return None

    def _generate_lesson(self, prediction_error: dict[str, Any]) -> str | None:
        """Phase 2 Piece 3: asks the real AI provider to narrate a lesson
        from the already-computed, real prediction_error facts (learning.
        prediction_review::compute_prediction_error) -- the AI never
        computes the error itself, only interprets numbers already true.
        Same local-failure-never-escapes reasoning as _explain/
        _propose_hypothesis above."""
        try:
            analysis = self.ai_router.analyze(POST_TRADE_LESSON, {"prediction_error": prediction_error})
            return analysis.summary or None
        except Exception as exc:  # noqa: BLE001 - AI enrichment is optional; failure must not affect the already-recorded real trade facts.
            logger.warning("post_trade_ai_lesson_failed error=%s", exc)
            return None
