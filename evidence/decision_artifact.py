"""Phase 2 Piece 8, Requirement 2: the canonical decision artifact.

One immutable record per real, evaluated trading cycle -- built once,
synchronously, from exactly what `agents/orchestrator.py::run_cycle` and
its subscriber chain (`_on_research_complete` -> `_on_signal_created` ->
`_on_trade_proposed` -> `_on_trade_validated` -> `_on_risk_decision`)
already computed for THAT cycle, never re-derived or looked up later --
see this module's own no-look-ahead tests for the concrete proof.

This is deliberately a REFERENCE layer, not a second copy of the
project's real sources of truth (Requirement 1's "do not create
duplicate storage"):

- market state: referenced by `decision_ledger_id` (the real Phase 1
  `CAND-...` id, primary key of the `decision_ledger` SQL table,
  execution/decision_ledger.py) -- never copied inline. A reader joins
  back via `storage/database.py::Database.decision_ledger_entry`.
- agent outputs: referenced by `agent_output_artifact_ids` (a
  {agent_name: artifact_id} map into MemoryStore's new "agent_output"
  records, evidence/agent_output_artifact.py) -- never the full
  `AgentResult.data` duplicated a second time here.
- score attribution: inlined (small, and, like `TradeOutcomeRecord.
  score_attribution` already does elsewhere in this codebase, there is
  no FK-able id on the `signals` SQL table to reference by instead).
- consensus / validation ("adversarial") / risk outcome: inlined --
  small, decision-critical facts with no separate canonical record of
  their own anywhere else in the codebase (confirmed by the Piece 8
  audit): this artifact IS their first persisted home.
- "supervisor" result: honestly absent (`None`) at decision-artifact
  time. `agents/trading_agents.py::TradeSupervisorAgent` only evaluates
  an already-OPEN position tick by tick (a later, separate concern from
  candidate evaluation) -- there is no supervisor evaluation to
  reference at decision time, so nothing is fabricated here to fill the
  field.
- AI hypothesis: `ai_hypothesis_reference`/`ai_evidence_id` are None at
  initial build time (a hypothesis is only proposed by
  `PostTradeAgent._propose_hypothesis`, AFTER a trade has closed --
  strictly later than candidate-decision time). When a trade tied to
  this artifact later closes and a hypothesis is genuinely proposed, a
  `DecisionArtifactCorrection` (below) links it in -- the ORIGINAL
  artifact is never rewritten. This is exactly Requirement 6's
  correction mechanism, not a workaround.

Persisted via `learning/memory.py::MemoryStore` (append-only, no UPDATE
method -- see evidence/agent_output_artifact.py's docstring for why no
new SQL table was added). New memory_types: "decision_artifact" and
"decision_artifact_correction".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from learning.memory import MemoryStore

DECISION_ARTIFACT_MEMORY_TYPE = "decision_artifact"
DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE = "decision_artifact_correction"


@dataclass(frozen=True)
class DecisionArtifact:
    artifact_id: str
    session_id: str | None  # the real SessionState.session_date (execution/session_state.py) -- see module docstring for why no separate "run id" concept was invented
    candidate_id: str | None  # the real TradeCandidate.candidate_id (agents/contracts.py), when a candidate was found
    decision_ledger_id: str | None  # the real Phase 1 CAND-... id -- FK into the decision_ledger table, never copied inline
    timestamp: str  # ISO 8601, when this artifact was built
    strategy_version: str
    direction: str | None
    confidence: float | None
    score_attribution: dict[str, Any] | None
    agent_output_artifact_ids: dict[str, str]  # {agent_name: evidence/agent_output_artifact.py artifact_id}
    consensus: str | None
    conflicting_evidence: bool
    validation_decision: str | None  # Decision enum value, e.g. "APPROVE"/"REJECT"/"REVIEW"
    validation_reasons: tuple[str, ...]
    validation_confidence: float | None
    risk_approved: bool | None  # None when risk was never reached (e.g. validation rejected first)
    risk_reasons: tuple[str, ...]
    supervisor_result: dict[str, Any] | None  # always None at build time -- see module docstring
    ai_hypothesis_reference: str | None  # an Experiment id (learning/hypothesis.py), set only via a later correction
    ai_evidence_id: str | None  # an evidence/ai_evidence.py AIEvidence id, set only via a later correction

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "session_id": self.session_id,
            "candidate_id": self.candidate_id,
            "decision_ledger_id": self.decision_ledger_id,
            "timestamp": self.timestamp,
            "strategy_version": self.strategy_version,
            "direction": self.direction,
            "confidence": self.confidence,
            "score_attribution": self.score_attribution,
            "agent_output_artifact_ids": dict(self.agent_output_artifact_ids),
            "consensus": self.consensus,
            "conflicting_evidence": self.conflicting_evidence,
            "validation_decision": self.validation_decision,
            "validation_reasons": list(self.validation_reasons),
            "validation_confidence": self.validation_confidence,
            "risk_approved": self.risk_approved,
            "risk_reasons": list(self.risk_reasons),
            "supervisor_result": self.supervisor_result,
            "ai_hypothesis_reference": self.ai_hypothesis_reference,
            "ai_evidence_id": self.ai_evidence_id,
        }


def build_decision_artifact(
    now: datetime,
    strategy_version: str,
    agent_output_artifact_ids: dict[str, str],
    decision_ledger_id: str | None = None,
    candidate_id: str | None = None,
    direction: str | None = None,
    confidence: float | None = None,
    score_attribution: dict[str, Any] | None = None,
    consensus: str | None = None,
    conflicting_evidence: bool = False,
    validation_decision: str | None = None,
    validation_reasons: tuple[str, ...] = (),
    validation_confidence: float | None = None,
    risk_approved: bool | None = None,
    risk_reasons: tuple[str, ...] = (),
) -> DecisionArtifact:
    """Pure assembly -- no I/O, no lookup of anything not explicitly
    passed in by the caller. This is the structural guarantee behind
    Requirement 7 (no look-ahead): the function has no mechanism to
    reach for any data source other than its own arguments, so it
    cannot pull in an observation the caller did not already have in
    hand at `now`."""
    return DecisionArtifact(
        artifact_id=str(uuid4()),
        session_id=now.date().isoformat(),
        candidate_id=candidate_id,
        decision_ledger_id=decision_ledger_id,
        timestamp=now.isoformat(),
        strategy_version=strategy_version,
        direction=direction,
        confidence=confidence,
        score_attribution=score_attribution,
        agent_output_artifact_ids=dict(agent_output_artifact_ids),
        consensus=consensus,
        conflicting_evidence=conflicting_evidence,
        validation_decision=validation_decision,
        validation_reasons=validation_reasons,
        validation_confidence=validation_confidence,
        risk_approved=risk_approved,
        risk_reasons=risk_reasons,
        supervisor_result=None,
        ai_hypothesis_reference=None,
        ai_evidence_id=None,
    )


def record_decision_artifact(store: MemoryStore, artifact: DecisionArtifact, timestamp: datetime) -> str:
    return store.append(DECISION_ARTIFACT_MEMORY_TYPE, artifact.to_dict(), timestamp)


@dataclass(frozen=True)
class DecisionArtifactCorrection:
    """Requirement 6: a correction never overwrites the original artifact
    -- it is a new, separate, immutable record that references it,
    carries only the fields being added/corrected, and states why."""

    correction_id: str
    original_artifact_id: str
    timestamp: str
    reason: str
    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "original_artifact_id": self.original_artifact_id,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "fields": self.fields,
        }


def build_decision_artifact_correction(
    original_artifact_id: str, reason: str, fields: dict[str, Any], now: datetime
) -> DecisionArtifactCorrection:
    return DecisionArtifactCorrection(
        correction_id=str(uuid4()),
        original_artifact_id=original_artifact_id,
        timestamp=now.isoformat(),
        reason=reason,
        fields=dict(fields),
    )


def record_decision_artifact_correction(
    store: MemoryStore, correction: DecisionArtifactCorrection, timestamp: datetime
) -> str:
    return store.append(DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE, correction.to_dict(), timestamp)
