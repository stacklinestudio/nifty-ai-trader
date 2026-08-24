"""Only validated, structured facts can leave an AI provider boundary."""

from __future__ import annotations

from typing import Protocol

from ai.schemas import AIAnalysis


class AIProvider(Protocol):
    def analyze(self, task: str, facts: dict) -> AIAnalysis: ...


class UnavailableProvider:
    """Safe default that makes unavailable AI explicit rather than fabricating insight."""

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            "AI provider is not configured; deterministic workflow only.",
            0,
            risks=("AI unavailable",),
            source_facts={"task": task, "fact_count": len(facts)},
        )
