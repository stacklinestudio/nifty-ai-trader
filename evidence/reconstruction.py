"""Phase 2 Piece 8, Requirements 5 and 9: deterministic correlation and
end-to-end reconstruction.

`reconstruct_decision_trace` answers, for one real Phase 1 decision-
ledger candidate id (`CAND-YYYYMMDD-HHMMSS-NNN`), "what did the system
know at that exact moment, what did each agent produce, what did Claude
reason/propose, what deterministic gates decided, what was executed, and
what happened afterward?" -- by reading back real, already-persisted
records across the existing, real chain of ids this project already
uses (see learning/trade_outcome.py's own module docstring for the same
chain, confirmed again by the Piece 8 audit):

    decision_ledger (CAND-...)
      -> decision_artifact (memory_type "decision_artifact", references
         the same CAND-... id)
        -> agent_output artifacts (memory_type "agent_output", referenced
           by the decision artifact's own agent_output_artifact_ids map)
        -> decision_artifact_correction records (memory_type
           "decision_artifact_correction"), each possibly carrying an
           ai_evidence_id -> ai_evidence (memory_type "ai_evidence")
      -> trade outcome (memory_type "trade", TradeOutcomeRecord.
         decision_ledger_candidate_id == the same CAND-... id)
        -> learning_event (memory_type "learning_event",
           LearningEvent.outcome_id == TradeOutcomeRecord.outcome_id)

Every stage that genuinely has no record yet (never ran, or the trade
has not closed) is reported as the literal `{"status": "ABSENT"}` --
never synthesized, never silently omitted from the returned dict's keys
(Requirement 9's explicit instruction).

Pure read-only function: no memory_type gains a new query method on
`learning/memory.py::MemoryStore` itself (its existing `recent()` is
already sufficient, scanned here rather than adding a new indexed
lookup this project's real, still-small data volumes do not need); no
write of any kind happens inside this module. Calling this function
twice against the same underlying store/database returns byte-for-byte
identical dicts -- there is no live-clock read, no randomness, and no
"latest wins" logic anywhere in it (see tests/test_evidence_
reconstruction.py's own repeated-call proof).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evidence.agent_output_artifact import AGENT_OUTPUT_MEMORY_TYPE
from evidence.ai_evidence import AI_EVIDENCE_MEMORY_TYPE
from evidence.decision_artifact import (
    DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE,
    DECISION_ARTIFACT_MEMORY_TYPE,
)
from learning.memory import MemoryStore
from storage.database import Database

TRADE_MEMORY_TYPE = "trade"
LEARNING_EVENT_MEMORY_TYPE = "learning_event"

# This project's real data volumes (paper trading, two live sessions
# captured so far) are small enough that a full type-scoped scan is the
# honest, simplest correct implementation -- see module docstring.
_SCAN_LIMIT = 100_000

ABSENT: dict[str, str] = {"status": "ABSENT"}


def _find_one(
    store: MemoryStore, memory_type: str, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any] | None:
    for entry in store.recent(memory_type=memory_type, limit=_SCAN_LIMIT):
        if predicate(entry["payload"]):
            return entry["payload"]
    return None


def _find_all(
    store: MemoryStore, memory_type: str, predicate: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    return [
        entry["payload"]
        for entry in store.recent(memory_type=memory_type, limit=_SCAN_LIMIT)
        if predicate(entry["payload"])
    ]


def reconstruct_decision_trace(
    database: Database, store: MemoryStore, decision_ledger_candidate_id: str
) -> dict[str, Any]:
    market_state = database.decision_ledger_entry(decision_ledger_candidate_id)

    decision_artifact = _find_one(
        store,
        DECISION_ARTIFACT_MEMORY_TYPE,
        lambda p: p.get("decision_ledger_id") == decision_ledger_candidate_id,
    )

    agent_outputs: dict[str, Any] = {}
    corrections: list[dict[str, Any]] = []
    ai_hypothesis_evidence: dict[str, Any] = dict(ABSENT)
    risk_decision: dict[str, Any] = dict(ABSENT)

    if decision_artifact is not None:
        for agent_name, artifact_id in decision_artifact.get("agent_output_artifact_ids", {}).items():
            output = _find_one(
                store, AGENT_OUTPUT_MEMORY_TYPE, lambda p, aid=artifact_id: p.get("artifact_id") == aid
            )
            agent_outputs[agent_name] = output if output is not None else dict(ABSENT)

        corrections = _find_all(
            store,
            DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE,
            lambda p: p.get("original_artifact_id") == decision_artifact["artifact_id"],
        )
        for correction in corrections:
            ai_evidence_id = correction.get("fields", {}).get("ai_evidence_id")
            if ai_evidence_id:
                evidence = _find_one(
                    store, AI_EVIDENCE_MEMORY_TYPE, lambda p, eid=ai_evidence_id: p.get("ai_evidence_id") == eid
                )
                if evidence is not None:
                    ai_hypothesis_evidence = evidence

        risk_decision = {
            "risk_approved": decision_artifact.get("risk_approved"),
            "risk_reasons": decision_artifact.get("risk_reasons"),
        }

    trade_outcome = _find_one(
        store, TRADE_MEMORY_TYPE, lambda p: p.get("decision_ledger_candidate_id") == decision_ledger_candidate_id
    )

    execution_fill: dict[str, Any] = dict(ABSENT)
    learning_event: dict[str, Any] = dict(ABSENT)
    if trade_outcome is not None:
        execution_fill = {
            "entry_timestamp": trade_outcome.get("entry_timestamp"),
            "entry_price": trade_outcome.get("entry_price"),
            "exit_timestamp": trade_outcome.get("exit_timestamp"),
            "exit_price": trade_outcome.get("exit_price"),
        }
        outcome_id = trade_outcome.get("outcome_id")
        found_event = _find_one(store, LEARNING_EVENT_MEMORY_TYPE, lambda p: p.get("outcome_id") == outcome_id)
        if found_event is not None:
            learning_event = found_event

    return {
        "decision_ledger_candidate_id": decision_ledger_candidate_id,
        "market_state": market_state if market_state is not None else dict(ABSENT),
        "decision_artifact": decision_artifact if decision_artifact is not None else dict(ABSENT),
        "agent_outputs": agent_outputs,
        "corrections": corrections,
        "ai_hypothesis_evidence": ai_hypothesis_evidence,
        "risk_decision": risk_decision,
        "execution_fill": execution_fill,
        "trade_outcome": trade_outcome if trade_outcome is not None else dict(ABSENT),
        "learning_event": learning_event,
    }
