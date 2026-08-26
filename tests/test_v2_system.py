from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from agents.contracts import AgentResult
from agents.orchestrator import Orchestrator
from agents.trading_agents import PostTradeAgent
from ai.router import AIRouter
from config import IST, Settings
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from events.bus import EventBus
from events.contracts import Event, EventType
from integrations.discord import DiscordNotifier
from integrations.obsidian import ObsidianExporter
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from storage.database import Database


@dataclass
class Response:
    ok: bool = True


def option_quote() -> OptionQuote:
    instrument = OptionInstrument(
        "NIFTY24CE",
        22000,
        datetime.now(IST).date() + timedelta(days=3),
        "CE",
        25,
    )
    return OptionQuote(instrument, 10, datetime.now(IST), 9.75, 10.25, 1000)


def candidate_context(fresh: bool = True) -> dict:
    return {
        "candidate_direction": "CALL",
        "candidate_confidence": 88,
        "entry_zone": (10.0, 10.5),
        "stop_zone": (8.0, 8.5),
        "target_zone": (13.0, 14.0),
        "option_quotes": [option_quote()],
        "spot": 22000,
        "option_atr": 1,
        "market_data_fresh": fresh,
        "market_open": fresh,
        "features": {"ema_fast": 2, "ema_slow": 1, "close": 2, "vwap": 1, "atr": 10},
    }


def test_agent_contract_has_structured_failure_state():
    result = AgentResult("unit", datetime.now(IST), 50, evidence=("fact",), data={"value": 1})
    assert result.available and result.serializable()["agent"] == "unit"


def test_event_bus_deduplicates_and_persists(tmp_path: Path):
    database = Database(tmp_path / "audit.db")
    database.initialize()
    bus = EventBus(database.save_event)
    event = Event(EventType.SYSTEM_STARTED, "test", datetime.now(IST))
    assert bus.publish(event)
    assert not bus.publish(event)
    assert len(database.events()) == 1


def test_orchestrator_fails_closed_without_market_data(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "paper.db")
    cycle = Orchestrator(settings).run_cycle()
    assert (
        not cycle.risk_approved
        and cycle.order is None
        and cycle.validation.decision.value == "REJECT"
    )


def test_independent_validation_and_risk_veto_stale_candidate(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "paper.db")
    cycle = Orchestrator(settings).run_cycle(candidate_context(fresh=False))
    assert cycle.thesis is not None
    assert not cycle.risk_approved and "market data is stale" in cycle.validation.reasons


def test_paper_execution_requires_and_receives_risk_approval(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "paper.db")
    cycle = Orchestrator(settings).run_cycle(candidate_context())
    assert settings.trading_mode == "paper"
    assert cycle.risk_approved and cycle.order and cycle.order["status"] == "FILLED"


def test_learning_memory_is_append_only(tmp_path: Path):
    store = MemoryStore(tmp_path / "learning.db")
    memory_id = store.append("trade", {"outcome": "NO_TRADE"}, datetime.now(IST))
    recent = store.recent()
    assert recent[0]["memory_id"] == memory_id and recent[0]["payload"]["outcome"] == "NO_TRADE"


def test_ai_router_returns_valid_unavailable_analysis():
    analysis = AIRouter().analyze("research", {"known": True})
    assert analysis.confidence == 0 and "AI unavailable" in analysis.risks


def test_notification_formatting_and_failures_do_not_raise():
    sent: list[tuple[str, dict]] = []

    def transport(url: str, **kwargs: dict) -> Response:
        sent.append((url, kwargs))
        return Response()

    assert TelegramNotifier("token", "chat", transport).send_message("trade", "approved")
    assert DiscordNotifier("https://example.invalid", transport).send_embed(
        "Trade", "approved", "TRADE"
    )
    assert "[TRADE] approved" in sent[0][1]["json"]["text"]
    assert not TelegramNotifier(
        "token", "chat", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    ).send_message("INFO", "failure")


def test_obsidian_export_is_optional_and_writes_markdown(tmp_path: Path):
    assert ObsidianExporter().export("Daily Research", "none", {}) is None
    path = ObsidianExporter(str(tmp_path)).export("Daily Research", "2025-01-01", {"mode": "paper"})
    assert path and path.exists() and "paper" in path.read_text()


def test_post_trade_agent_defers_without_closed_facts(tmp_path: Path):
    memory = MemoryStore(tmp_path / "learning.db")
    review = PostTradeAgent(memory).run({})
    assert review.data["review"]["learning_hypothesis"] == "None without closed trade facts"
    assert memory.recent() == []


def test_post_trade_agent_records_hypothesis_from_closed_trade_facts(tmp_path: Path):
    memory = MemoryStore(tmp_path / "learning.db")
    review = PostTradeAgent(memory).run(
        {"outcome": "LOSS", "pnl": -150, "setup_type": "OPENING_STRUCTURE", "exit_reason": "STOP_LOSS"}
    )
    assert "LOSS" in review.data["review"]["learning_hypothesis"]
    kinds = {entry["memory_type"] for entry in memory.recent(limit=10)}
    assert kinds == {"trade", "experiment"}


def test_orchestrator_review_trade_wires_learning_without_promoting(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    review = orchestrator.review_trade(
        {"outcome": "WIN", "pnl": 120, "setup_type": "OPENING_STRUCTURE", "exit_reason": "TAKE_PROFIT"}
    )
    assert review.data["review"]["outcome"] == "WIN"
    events = {event["event_type"] for event in orchestrator.database.events()}
    assert {"TRADE_COMPLETED", "LEARNING_CREATED"} <= events
    recent = orchestrator.memory.recent(memory_type="experiment", limit=5)
    assert recent and recent[0]["payload"]["status"] == "CANDIDATE"
