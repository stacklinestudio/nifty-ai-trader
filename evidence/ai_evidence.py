"""Phase 2 Piece 8, Requirement 4: AI/Claude call evidence.

Audit finding this closes: no existing call site of ai/router.py::
AIRouter.analyze (agents/research_agents.py:97, agents/trading_agents.py:
465/482/497, learning/ai_learning_context.py:67, data/rss_news.py:126)
captures which provider/model actually answered, or when --
`AIAnalysis.source_facts` (ai/schemas.py) only ever holds
{"task", "fact_count", "structured"} (ai/provider.py:102-106). This
module wraps a real `AIRouter.analyze` call and returns BOTH the real,
unchanged `AIAnalysis` the caller already acts on, and a separate,
immutable `AIEvidence` record describing the call itself -- provider,
model, task, timestamp, and (when applicable) which hypothesis/prediction
it relates to.

The project's AI provider stays exactly what it already is -- Anthropic
via ai/provider.py::AnthropicProvider (model resolved from
`settings.ai_model`, real default "claude-haiku-4-5-20251001",
config.py:67) or the safe `UnavailableProvider` fallback -- this module
introduces no new provider and never replaces either.

`advisory` is always True: nothing in this module (or anywhere it is
called from) can place an order, change a risk limit, bypass validation,
bypass promotion_engine, mutate strategy code, or overwrite a
deterministic agent's decision. This wrapper only ever runs the SAME real
`router.analyze(task, facts)` call a site already makes and records what
happened around it; it never substitutes its own judgment for the
provider's real response, and never fabricates one when the provider
fails or is unavailable -- see `build_ai_evidence` below for exactly how
each real failure mode is represented, never as a fabricated success.

Persisted via the same `learning/memory.py::MemoryStore` append-only
store used throughout this project (see evidence/agent_output_artifact.py
's own docstring for why no new SQL table was added). New `memory_type`
value: "ai_evidence".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from ai.router import AIRouter
from ai.schemas import AIAnalysis
from learning.memory import MemoryStore

AI_EVIDENCE_MEMORY_TYPE = "ai_evidence"
UNAVAILABLE_PROVIDER_NAME = "UnavailableProvider"


@dataclass(frozen=True)
class AIEvidence:
    ai_evidence_id: str
    correlation_id: str | None
    provider: str  # real provider class name, e.g. "AnthropicProvider"
    model: str | None  # real settings.ai_model when provider is Anthropic, else None
    task: str
    timestamp: str  # ISO 8601, when this call was made
    hypothesis_id: str | None
    prediction: dict[str, Any] | None
    structured_output: dict[str, Any] | None
    summary: str | None
    confidence: float | None
    advisory: bool  # always True -- see module docstring
    available: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ai_evidence_id": self.ai_evidence_id,
            "correlation_id": self.correlation_id,
            "provider": self.provider,
            "model": self.model,
            "task": self.task,
            "timestamp": self.timestamp,
            "hypothesis_id": self.hypothesis_id,
            "prediction": self.prediction,
            "structured_output": self.structured_output,
            "summary": self.summary,
            "confidence": self.confidence,
            "advisory": self.advisory,
            "available": self.available,
            "error": self.error,
        }


def build_ai_evidence(
    router: AIRouter,
    task: str,
    facts: dict[str, Any],
    now: datetime,
    correlation_id: str | None = None,
    hypothesis_id: str | None = None,
    prediction: dict[str, Any] | None = None,
) -> tuple[AIAnalysis | None, AIEvidence]:
    """Calls the real `router.analyze(task, facts)` exactly once and
    reports what genuinely happened:

    - AnthropicProvider configured and the call succeeds: (analysis,
      evidence) with available=True, error=None, and the real summary/
      confidence/structured output the provider returned.
    - UnavailableProvider (not configured -- the real, safe default):
      `router.analyze` itself never raises for this provider (see its own
      "AI unavailable" AIAnalysis), but this is still genuinely
      unavailable AI, so `available=False` with an explicit, real
      `error` message is recorded rather than treating the canned
      fallback text as a real answer.
    - A configured provider whose real call raises (network, parsing,
      timeout -- ai/provider.py's own documented failure modes):
      analysis is None, available=False, error=the real
      "{ExceptionType}: {message}". Never a fabricated/placeholder
      analysis returned in its place -- callers must handle None exactly
      like every existing AI call site's own try/except already does.
    """
    provider = router.provider
    provider_name = type(provider).__name__
    model = getattr(provider, "model", None)
    is_configured = provider_name != UNAVAILABLE_PROVIDER_NAME

    try:
        analysis = router.analyze(task, facts)
    except Exception as exc:  # noqa: BLE001 - the real failure is recorded as evidence, never swallowed silently.
        evidence = AIEvidence(
            ai_evidence_id=str(uuid4()),
            correlation_id=correlation_id,
            provider=provider_name,
            model=model,
            task=task,
            timestamp=now.isoformat(),
            hypothesis_id=hypothesis_id,
            prediction=prediction,
            structured_output=None,
            summary=None,
            confidence=None,
            advisory=True,
            available=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None, evidence

    structured = None
    if analysis.source_facts:
        candidate = analysis.source_facts.get("structured")
        structured = candidate if isinstance(candidate, dict) else None

    evidence = AIEvidence(
        ai_evidence_id=str(uuid4()),
        correlation_id=correlation_id,
        provider=provider_name,
        model=model,
        task=task,
        timestamp=now.isoformat(),
        hypothesis_id=hypothesis_id,
        prediction=prediction,
        structured_output=structured,
        summary=analysis.summary,
        confidence=analysis.confidence,
        advisory=True,
        available=is_configured,
        error=None if is_configured else "AI provider not configured (UnavailableProvider fallback)",
    )
    return analysis, evidence


def record_ai_evidence(store: MemoryStore, evidence: AIEvidence, timestamp: datetime) -> str:
    return store.append(AI_EVIDENCE_MEMORY_TYPE, evidence.to_dict(), timestamp)
