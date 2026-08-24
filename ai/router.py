from __future__ import annotations

from ai.provider import AIProvider, UnavailableProvider
from ai.schemas import AIAnalysis


class AIRouter:
    def __init__(self, provider: AIProvider | None = None) -> None:
        self.provider = provider or UnavailableProvider()
        self._cache: dict[str, AIAnalysis] = {}

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        cache_key = f"{task}:{sorted(facts.items())!r}"
        if cache_key not in self._cache:
            analysis = self.provider.analyze(task, dict(facts))
            analysis.validate()
            self._cache[cache_key] = analysis
        return self._cache[cache_key]
