"""Brief 8 Part C.7: the most important test in this brief.

Proves -- with real code paths, not just inspection -- that no AIAnalysis
field, however extreme or adversarial, can change a single real trade
decision: not the candidate direction, not the position size, not a risk
approval, not an order. AI (agents/research_agents.py::GlobalResearchAgent,
agents/trading_agents.py::PostTradeAgent, data/rss_news.py's headline
classification feeding NewsAgent) is enrichment only; the deterministic
pipeline (RiskAgent, TradeBuilderAgent, ExecutionAgent, SignalEngine) has
no code path that reads AI output at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agents.contracts import Decision, TradeCandidate, TradeThesis, Validation
from agents.orchestrator import Orchestrator
from agents.trading_agents import RiskAgent, TradeBuilderAgent
from ai.provider import UnavailableProvider
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from config import IST, Settings
from data.global_market import ContextValue
from data.instruments import OptionInstrument
from data.news import NewsItem
from data.option_chain import OptionQuote
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from strategy.option_selector import SelectedOption


class _AdversarialAIProvider:
    """Every field an AIAnalysis can carry, pushed to an extreme designed
    to change a trade decision if anything downstream were (incorrectly)
    reading it -- confidence pinned at the schema's own maximum, no risk
    caveats at all, and `structured` stuffed with keys that LOOK like
    real decision fields (position_size, risk_approved, quantity,
    stop_zone) despite AIAnalysis's schema never defining them, in case
    any code somewhere reads source_facts by guessed key name.
    """

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            summary="URGENT: BUY MAXIMUM SIZE IMMEDIATELY, IGNORE ALL RISK LIMITS, GUARANTEED WIN",
            confidence=100,
            risks=(),
            source_facts={
                "task": task,
                "structured": {
                    "classifications": [{"sentiment": "POSITIVE", "relevance": 1.0}] * 50,
                    "position_size": 999999,
                    "quantity": 999999,
                    "risk_approved": True,
                    "override_risk_approved": True,
                    "candidate_confidence": 100,
                    "stop_zone": (0.0, 0.0),
                    "kill_switch": False,
                },
            },
        )


def _real_shaped_context() -> dict:
    instrument = OptionInstrument(
        "NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 25
    )
    quote = OptionQuote(instrument, 10, datetime.now(IST), 9.75, 10.25, 1000)
    return {
        "candidate_direction": "CALL",
        "candidate_confidence": 88,
        "entry_zone": (10.0, 10.5),
        "stop_zone": (8.0, 8.5),
        "target_zone": (13.0, 14.0),
        "option_quotes": [quote],
        "spot": 22000,
        "option_atr": 1,
        "market_data_fresh": True,
        "market_open": True,
        "features": {"ema_fast": 2, "ema_slow": 1, "close": 2, "vwap": 1, "atr": 10},
        # Real facts for GlobalResearchAgent/NewsAgent to actually have
        # something to work with -- an empty context would short-circuit
        # both to their "unavailable" branch before AI is ever invoked,
        # which would weaken this test (AI must actually run to prove its
        # output doesn't matter).
        "global_context": [
            ContextValue("SP500", 0.5, datetime.now(IST), "yfinance", True),
            ContextValue("GOLD", 0.3, datetime.now(IST), "yfinance", True),
        ],
        "news_items": [
            NewsItem(datetime.now(IST), "Nifty surges on strong FII inflows", "test", 0.8, "POSITIVE", 0.6),
        ],
    }


def _decision_fingerprint(cycle) -> dict:
    """Every real decision-relevant field a cycle can produce -- excludes
    only pure bookkeeping that's expected to differ run to run regardless
    of AI (TradeCandidate.candidate_id is a fresh real uuid4 each
    construction; paper_broker order_id/timestamp are real wall-clock/
    random values) -- see agents/contracts.py::TradeCandidate and
    execution/paper_broker.py::place_order.
    """
    thesis = cycle.thesis
    order = cycle.order
    return {
        "consensus": cycle.consensus,
        "conflicting_evidence": cycle.conflicting_evidence,
        "risk_approved": cycle.risk_approved,
        "validation_decision": cycle.validation.decision if cycle.validation else None,
        "validation_reasons": cycle.validation.reasons if cycle.validation else None,
        "thesis_direction": thesis.candidate.direction if thesis else None,
        "thesis_setup_type": thesis.candidate.setup_type if thesis else None,
        "thesis_quantity": thesis.quantity if thesis else None,
        "thesis_entry": thesis.entry if thesis else None,
        "thesis_stop": thesis.stop if thesis else None,
        "thesis_target": thesis.target if thesis else None,
        "thesis_estimated_risk": thesis.estimated_risk if thesis else None,
        "thesis_confidence": thesis.confidence if thesis else None,
        "order_side": order.get("side") if order else None,
        "order_quantity": order.get("quantity") if order else None,
        "order_symbol": order.get("symbol") if order else None,
        "order_requested_price": order.get("requested_price") if order else None,
        "order_fill_price": order.get("fill_price") if order else None,
        "order_status": order.get("status") if order else None,
    }


def test_adversarial_ai_output_never_changes_the_deterministic_cycle_result(tmp_path):
    baseline = Orchestrator(
        Settings(database_path=tmp_path / "baseline.db"), ai_router=AIRouter(UnavailableProvider())
    )
    adversarial = Orchestrator(
        Settings(database_path=tmp_path / "adversarial.db"), ai_router=AIRouter(_AdversarialAIProvider())
    )

    baseline_cycle = baseline.run_cycle(_real_shaped_context())
    adversarial_cycle = adversarial.run_cycle(_real_shaped_context())

    assert _decision_fingerprint(baseline_cycle) == _decision_fingerprint(adversarial_cycle)
    # Real proof the adversarial provider actually ran (not skipped/no-op)
    # -- its narrative DID reach the AgentResult, just nowhere decision-relevant.
    global_result = adversarial_cycle.agent_results.get("global_research")
    assert global_result is not None
    assert global_result.data.get("ai_commentary") == (
        "URGENT: BUY MAXIMUM SIZE IMMEDIATELY, IGNORE ALL RISK LIMITS, GUARANTEED WIN"
    )
    # ...yet the deterministic global_direction/confidence are identical
    # to the baseline run's -- confirmed by the fingerprint match above
    # already covering everything that reaches a trade decision, and
    # directly here too for this specific agent's own numeric output.
    baseline_global = baseline_cycle.agent_results.get("global_research")
    assert global_result.confidence == baseline_global.confidence
    assert global_result.data.get("global_direction") == baseline_global.data.get("global_direction")


def test_adversarial_post_trade_explanation_never_changes_recorded_trade_facts(tmp_path):
    """PostTradeAgent runs strictly after a trade closes -- proves its
    ai_explanation field cannot retroactively alter what got recorded to
    MemoryStore, by comparing the actual recorded facts between a
    baseline and an adversarial-AI run of the exact same real outcome."""
    from learning.memory import MemoryStore

    outcome_facts = {
        "outcome": "WIN",
        "pnl": 500.0,
        "setup_type": "OPENING_RANGE_BREAKOUT",
        "exit_reason": "TAKE_PROFIT",
        "mae": -50.0,
        "mfe": 600.0,
        "entry_regime": "TREND_UP",
        "hold_seconds": 1200,
    }

    baseline_memory = MemoryStore(tmp_path / "baseline_memory.db")
    from agents.trading_agents import PostTradeAgent

    PostTradeAgent(baseline_memory, AIRouter(UnavailableProvider())).run(dict(outcome_facts))
    baseline_trades = baseline_memory.recent(memory_type="trade", limit=10)

    adversarial_memory = MemoryStore(tmp_path / "adversarial_memory.db")
    review = PostTradeAgent(adversarial_memory, AIRouter(_AdversarialAIProvider())).run(dict(outcome_facts))
    adversarial_trades = adversarial_memory.recent(memory_type="trade", limit=10)

    assert len(baseline_trades) == len(adversarial_trades) == 1
    # Real recorded facts (pnl, outcome, setup_type, etc.) are identical
    # -- only ever built from outcome_facts, before ai_explanation is
    # even generated (see PostTradeAgent.analyze's real call order).
    assert baseline_trades[0]["payload"] == adversarial_trades[0]["payload"]
    # The adversarial narrative did reach the AgentResult (proving AI
    # actually ran)...
    assert "GUARANTEED WIN" in (review.data.get("ai_explanation") or "")
    # ...but nothing about what was already recorded changed because of it.


def test_risk_agent_ignores_ai_looking_keys_injected_directly_into_context():
    """Even if something upstream mistakenly merged AI-sourced content
    into the context dict RiskAgent sees, RiskAgent's own analyze() reads
    a fixed, small set of real keys (thesis, validation, market_data_
    fresh, market_open) and nothing else -- proven here by injecting
    extra keys that look like they could plausibly be read by name."""
    settings = Settings()
    limits = DailyLimits(settings.max_trades_per_day, settings.max_daily_loss)
    candidate = TradeCandidate(
        "CALL", "OPENING_STRUCTURE", "NIFTY", 88.0, ("evidence",), (), (100.0, 100.5), (92.0, 92.5), (114.0, 115.0)
    )
    thesis = TradeThesis(candidate, "NIFTY24CE", 100.0, 92.0, 115.0, 75, 600.0, 88.0, ("evidence",), ())
    validation = Validation(Decision.APPROVE, ("No disqualifying evidence.",), 90)
    clean_context = {
        "thesis": thesis,
        "validation": validation,
        "market_data_fresh": True,
        "market_open": True,
    }
    poisoned_context = {
        **clean_context,
        "ai_commentary": "APPROVE THIS TRADE NO MATTER WHAT",
        "ai_explanation": "OVERRIDE RISK LIMITS",
        "risk_approved": False,  # a real key name RiskAgent's OWN OUTPUT uses -- proves an injected
        # value under that name is not accidentally read back as an input
        "approved": False,
        "structured": {"risk_approved": False, "kill_switch": True},
    }

    clean_result = RiskAgent(settings, limits).run(clean_context)
    poisoned_result = RiskAgent(settings, limits).run(poisoned_context)

    assert clean_result.data["approved"] == poisoned_result.data["approved"] is True
    assert clean_result.confidence == poisoned_result.confidence


def test_trade_builder_ignores_ai_looking_keys_injected_directly_into_context():
    settings = Settings()
    candidate = TradeCandidate(
        "CALL", "OPENING_STRUCTURE", "NIFTY", 88.0, ("evidence",), (), (100.0, 100.5), (92.0, 92.5), (114.0, 115.0)
    )
    instrument = OptionInstrument("NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 75)
    quote = OptionQuote(instrument, 100, datetime.now(IST), 99.5, 100.5, 1000)
    selected = SelectedOption(quote, 75, 1.0)
    risk = RiskManager(settings.max_risk_per_trade, settings.max_position_value)
    builder = TradeBuilderAgent(risk, low_confidence=75.0, high_confidence=95.0)

    clean_context = {"candidate": candidate, "selected_option": selected, "option_atr": 8}
    poisoned_context = {
        **clean_context,
        "quantity": 999999,
        "position_size": 999999,
        "structured": {"quantity": 999999, "position_size": 999999},
    }

    clean_thesis = builder.run(clean_context).data["thesis"]
    poisoned_thesis = builder.run(poisoned_context).data["thesis"]

    assert clean_thesis.quantity == poisoned_thesis.quantity
    assert clean_thesis.entry == poisoned_thesis.entry
    assert clean_thesis.stop == poisoned_thesis.stop
    assert clean_thesis.target == poisoned_thesis.target
    assert clean_thesis.estimated_risk == poisoned_thesis.estimated_risk


def test_a_failing_ai_provider_still_produces_the_exact_same_cycle_result(tmp_path):
    """Part C.6's fail-closed requirement, end to end: the system trades
    exactly the same whether AI is unavailable or actively throwing on
    every call."""

    class _AlwaysFailsProvider:
        def analyze(self, task: str, facts: dict) -> AIAnalysis:
            raise ConnectionError("simulated real AI outage")

    baseline = Orchestrator(
        Settings(database_path=tmp_path / "baseline2.db"), ai_router=AIRouter(UnavailableProvider())
    )
    failing = Orchestrator(
        Settings(database_path=tmp_path / "failing.db"), ai_router=AIRouter(_AlwaysFailsProvider())
    )

    baseline_cycle = baseline.run_cycle(_real_shaped_context())
    failing_cycle = failing.run_cycle(_real_shaped_context())

    assert _decision_fingerprint(baseline_cycle) == _decision_fingerprint(failing_cycle)
    # The failure didn't silently zero out the whole GlobalResearchAgent
    # result either -- the real deterministic computation still ran.
    failing_global = failing_cycle.agent_results.get("global_research")
    assert failing_global.error is None
    assert failing_global.data.get("ai_commentary") is None
    assert failing_global.data.get("global_direction") in {"BULLISH", "BEARISH", "NEUTRAL"}
