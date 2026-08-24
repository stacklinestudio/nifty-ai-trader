"""Explicit messages exchanged among V2 agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class AgentResult:
    agent: str
    timestamp: datetime
    confidence: float
    evidence: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    sources: tuple[str, ...] = ()
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def available(self) -> bool:
        return self.error is None

    def serializable(self) -> dict[str, Any]:
        output = asdict(self)
        output["timestamp"] = self.timestamp.isoformat()
        return output


@dataclass(frozen=True)
class TradeCandidate:
    direction: str
    setup_type: str
    underlying: str
    confidence: float
    evidence: tuple[str, ...]
    invalidations: tuple[str, ...]
    entry_zone: tuple[float, float]
    stop_zone: tuple[float, float]
    target_zone: tuple[float, float]
    candidate_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class TradeThesis:
    candidate: TradeCandidate
    symbol: str
    entry: float
    stop: float
    target: float
    quantity: int
    estimated_risk: float
    confidence: float
    evidence: tuple[str, ...]
    invalidations: tuple[str, ...]


@dataclass(frozen=True)
class Validation:
    decision: Decision
    reasons: tuple[str, ...]
    confidence: float
