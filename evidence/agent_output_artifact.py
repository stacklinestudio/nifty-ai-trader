"""Phase 2 Piece 8, Requirement 3: deterministic agent-output evidence.

Audit finding this closes: agents/orchestrator.py's per-cycle
`_CycleState.results: dict[str, AgentResult]` (the full output of every
agent that ran -- GlobalResearchAgent, IndiaMarketAgent, NewsAgent,
TechnicalAgent, VolatilityAgent, BreadthAgent, SignalHunterAgent,
OptionsAgent, TradeBuilderAgent, RiskAgent, ExecutionAgent) is exposed to
the caller once via `CycleResult.agent_results`, then discarded -- only a
narrow summary of it is ever persisted, via `Orchestrator._event()` into
`audit_events` (agent/event_type/input_summary/output_summary/confidence,
never the real structured `AgentResult.data`). This module captures the
real, already-computed `AgentResult` (agents/contracts.py) verbatim --
never re-running an agent, never inventing reasoning it did not produce,
never requiring chain-of-thought (AgentResult has no such field to begin
with).

`AgentResult.serializable()` (agents/contracts.py) already produces the
exact real dict this module persists -- no new serialization format was
invented; this module only adds an artifact id, a correlation id (the
real Phase 1 decision-ledger `CAND-...` candidate id, when known), and a
storage call.

Persisted via the existing `learning/memory.py::MemoryStore` -- the
project's one canonical append-only, immutable, JSON-payload evidence
store (no UPDATE method exists on it at all; already used for "trade",
"prediction_review", "experiment", "learning_event" records). A new SQL
table was deliberately NOT added: MemoryStore already IS the canonical
storage for exactly this shape of record (an immutable JSON payload
keyed by a fresh id, filterable by a `memory_type` string), so adding one
would be the duplicate storage Requirement 1 explicitly warns against.
New `memory_type` value: "agent_output".

Individual agents (agents/research_agents.py, agents/trading_agents.py)
expose no per-agent version field of their own -- confirmed by reading
every agent class in both modules. The only genuine "version" concept
that exists in this codebase is the whole strategy pipeline's
`strategy_version` (e.g. "v2", already threaded through TradeOutcomeRecord/
SessionState/decision-ledger persistence). `agent_version` below is
therefore that real, existing value, honestly representing "which
strategy pipeline version produced this output" -- never a fabricated
per-agent version number that does not exist anywhere in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from learning.memory import MemoryStore

if TYPE_CHECKING:
    # Deliberately import-guarded, not a top-level import: agents/__init__.py
    # eagerly imports Orchestrator, which imports THIS module (to build the
    # real per-cycle evidence -- see agents/orchestrator.py::
    # _record_decision_artifact) -- a top-level `from agents.contracts
    # import AgentResult` here would be a genuine circular import whenever
    # something imports evidence.agent_output_artifact before agents.
    # orchestrator has already fully loaded (confirmed while gathering this
    # piece's own real-data evidence: a standalone script importing
    # evidence.reconstruction first hit exactly this). AgentResult is only
    # ever used here as a type hint, and `from __future__ import
    # annotations` above already makes every annotation a lazily-evaluated
    # string, so this import never needs to run at all outside a type
    # checker.
    from agents.contracts import AgentResult

AGENT_OUTPUT_MEMORY_TYPE = "agent_output"


@dataclass(frozen=True)
class AgentOutputArtifact:
    artifact_id: str
    correlation_id: str | None  # decision-ledger candidate_id, when known
    agent_name: str
    agent_version: str  # the real strategy_version -- see module docstring
    timestamp: str  # ISO 8601, the real AgentResult.timestamp
    output: dict[str, Any]  # the real AgentResult.data, verbatim
    evidence: tuple[str, ...]  # the real AgentResult.evidence, verbatim
    confidence: float
    available: bool  # the real AgentResult.available (error is None)
    error: str | None
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "correlation_id": self.correlation_id,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "timestamp": self.timestamp,
            "output": self.output,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "available": self.available,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def build_agent_output_artifact(
    result: AgentResult, correlation_id: str | None, strategy_version: str
) -> AgentOutputArtifact:
    """Pure assembly -- no I/O, no re-running the agent. Every field is
    read straight off the real, already-produced `result`; nothing is
    inferred or fabricated."""
    return AgentOutputArtifact(
        artifact_id=str(uuid4()),
        correlation_id=correlation_id,
        agent_name=result.agent,
        agent_version=strategy_version,
        timestamp=result.timestamp.isoformat(),
        output=dict(result.data),
        evidence=result.evidence,
        confidence=result.confidence,
        available=result.available,
        error=result.error,
        duration_ms=result.duration_ms,
    )


def build_agent_output_artifacts(
    results: dict[str, AgentResult], correlation_id: str | None, strategy_version: str
) -> list[AgentOutputArtifact]:
    """One artifact per agent that genuinely ran this cycle. An agent
    that never ran (e.g. RiskAgent/ExecutionAgent on a no-candidate
    cycle) simply has no key in `results` and therefore produces no
    artifact here -- reported as absent by omission, never a fabricated
    placeholder. See evidence/reconstruction.py for how a reader
    distinguishes "ran, artifact exists" from "never ran, no artifact"."""
    return [
        build_agent_output_artifact(result, correlation_id, strategy_version)
        for result in results.values()
    ]


def record_agent_output_artifact(
    store: MemoryStore, artifact: AgentOutputArtifact, timestamp: datetime
) -> str:
    return store.append(AGENT_OUTPUT_MEMORY_TYPE, artifact.to_dict(), timestamp)


def record_agent_output_artifacts(
    store: MemoryStore, artifacts: list[AgentOutputArtifact], timestamp: datetime
) -> dict[str, str]:
    """Returns {agent_name: artifact_id} so a caller (the decision
    artifact builder) can carry references, not duplicate content.

    Deliberately the artifact's own `artifact_id` (embedded in its
    payload), NOT MemoryStore's separate `memory_id` returned by
    `store.append` -- evidence/reconstruction.py looks an agent-output
    record up by matching its stored `artifact_id` field, so this must
    return the same id space or a reader could never find it back."""
    for artifact in artifacts:
        record_agent_output_artifact(store, artifact, timestamp)
    return {artifact.agent_name: artifact.artifact_id for artifact in artifacts}
