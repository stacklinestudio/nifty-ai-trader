"""Timeout-bounded base class that turns agent errors into explicit results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from time import perf_counter
from typing import Any

from agents.contracts import AgentResult
from config import IST


class BaseAgent(ABC):
    name = "base"
    timeout_seconds = 3.0

    def run(self, context: dict[str, Any]) -> AgentResult:
        started = perf_counter()
        timestamp = datetime.now(IST)
        try:
            result = self.analyze(dict(context))
            duration = (perf_counter() - started) * 1000
            if duration > self.timeout_seconds * 1000:
                return AgentResult(
                    self.name, timestamp, 0, error="agent timeout", duration_ms=duration
                )
            return AgentResult(
                self.name,
                timestamp,
                max(0, min(100, result.confidence)),
                result.evidence,
                result.data,
                result.sources,
                result.error,
                duration,
            )
        except Exception as exc:  # noqa: BLE001 - the agent boundary must turn all failures into data.
            return AgentResult(
                self.name,
                timestamp,
                0,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(perf_counter() - started) * 1000,
            )

    @abstractmethod
    def analyze(self, context: dict[str, Any]) -> AgentResult:
        raise NotImplementedError
