"""Append-only, serialisable system event schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(str, Enum):
    SYSTEM_STARTED = "SYSTEM_STARTED"
    AUTH_READY = "AUTH_READY"
    MARKET_PREP_STARTED = "MARKET_PREP_STARTED"
    MARKET_RESEARCH_COMPLETE = "MARKET_RESEARCH_COMPLETE"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    TRADE_PROPOSED = "TRADE_PROPOSED"
    TRADE_VALIDATED = "TRADE_VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    PAPER_ORDER_SENT = "PAPER_ORDER_SENT"
    PAPER_FILL = "PAPER_FILL"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    FORCED_EXIT = "FORCED_EXIT"
    TRADE_COMPLETED = "TRADE_COMPLETED"
    LEARNING_CREATED = "LEARNING_CREATED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True)
class Event:
    event_type: EventType
    agent: str
    timestamp: datetime
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    source: str = "system"
    strategy_version: str = "v2"
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def serializable(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["event_type"] = self.event_type.value
        return data
