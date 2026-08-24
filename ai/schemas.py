"""Strict schema for AI hypotheses; never executable instructions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AIAnalysis:
    summary: str
    confidence: float
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    source_facts: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.summary, str) or len(self.summary) > 5000:
            raise ValueError("Invalid AI summary")
        if not 0 <= self.confidence <= 100:
            raise ValueError("AI confidence must be within 0-100")
        if any(not isinstance(item, str) for item in self.evidence + self.risks):
            raise ValueError("AI evidence and risks must be text")
