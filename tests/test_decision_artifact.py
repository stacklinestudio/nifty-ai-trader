"""Phase 2 Piece 8, Requirements 2 and 6: evidence/decision_artifact.py."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime

import pytest

from config import IST
from evidence.decision_artifact import (
    DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE,
    DECISION_ARTIFACT_MEMORY_TYPE,
    build_decision_artifact,
    build_decision_artifact_correction,
    record_decision_artifact,
    record_decision_artifact_correction,
)
from learning.memory import MemoryStore

NOW = datetime(2026, 9, 10, 9, 20, tzinfo=IST)


def test_build_decision_artifact_captures_the_real_facts_a_cycle_already_computed():
    artifact = build_decision_artifact(
        now=NOW,
        strategy_version="v2",
        agent_output_artifact_ids={"technical": "art-1", "risk": "art-2"},
        decision_ledger_id="CAND-20260910-092000-001",
        candidate_id="real-uuid-1",
        direction="CALL",
        confidence=82.0,
        score_attribution={"score": 61.4},
        consensus="BULLISH",
        conflicting_evidence=False,
        validation_decision="APPROVE",
        validation_reasons=("fresh market data",),
        validation_confidence=90.0,
        risk_approved=True,
        risk_reasons=(),
    )

    assert artifact.session_id == "2026-09-10"
    assert artifact.decision_ledger_id == "CAND-20260910-092000-001"
    assert artifact.candidate_id == "real-uuid-1"
    assert artifact.direction == "CALL"
    assert artifact.confidence == 82.0
    assert artifact.agent_output_artifact_ids == {"technical": "art-1", "risk": "art-2"}
    assert artifact.consensus == "BULLISH"
    assert artifact.validation_decision == "APPROVE"
    assert artifact.risk_approved is True


def test_supervisor_result_is_always_honestly_none_at_build_time():
    """Requirement 2/9: TradeSupervisorAgent only evaluates an already-
    open position, a later, separate concern -- never fabricated here."""
    artifact = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})

    assert artifact.supervisor_result is None


def test_ai_hypothesis_fields_are_none_at_initial_build_time():
    """A hypothesis is only proposed after a trade closes -- strictly
    later than decision time; see the correction test below for how it
    is genuinely linked in later, without rewriting this artifact."""
    artifact = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})

    assert artifact.ai_hypothesis_reference is None
    assert artifact.ai_evidence_id is None


def test_a_no_candidate_cycle_honestly_reports_absent_candidate_fields():
    artifact = build_decision_artifact(
        now=NOW,
        strategy_version="v2",
        agent_output_artifact_ids={"technical": "art-1"},
        decision_ledger_id="CAND-1",
        validation_decision="REJECT",
        validation_reasons=("No candidate passed the evidence-consensus stage.",),
        risk_approved=None,  # risk never ran
    )

    assert artifact.candidate_id is None
    assert artifact.direction is None
    assert artifact.risk_approved is None
    assert artifact.validation_decision == "REJECT"


def test_two_artifacts_built_from_identical_inputs_get_distinct_ids():
    a = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})
    b = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})

    assert a.artifact_id != b.artifact_id


def test_record_decision_artifact_round_trips_through_memory_store(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    artifact = build_decision_artifact(
        now=NOW, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id="CAND-1"
    )

    memory_id = record_decision_artifact(store, artifact, NOW)

    stored = store.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=5)
    assert len(stored) == 1
    assert stored[0]["memory_id"] == memory_id
    assert stored[0]["payload"]["decision_ledger_id"] == "CAND-1"


def test_a_decision_artifact_cannot_be_mutated_in_place():
    """Requirement 6: frozen at the dataclass level -- the first, cheap
    layer of the immutability guarantee."""
    artifact = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})

    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.direction = "PUT"  # type: ignore[misc]


def test_a_correction_never_rewrites_the_original_artifacts_stored_bytes(tmp_path):
    """Requirement 6's real proof: record an artifact, read its raw
    stored row, record a correction referencing it, then read the SAME
    original row again -- byte-identical, because MemoryStore has no
    UPDATE method at all (learning/memory.py) and this module never
    calls anything but `append`."""
    db_path = tmp_path / "learning.db"
    store = MemoryStore(db_path)
    artifact = build_decision_artifact(
        now=NOW, strategy_version="v2", agent_output_artifact_ids={}, decision_ledger_id="CAND-1"
    )
    memory_id = record_decision_artifact(store, artifact, NOW)

    with sqlite3.connect(db_path) as conn:
        (before,) = conn.execute(
            "SELECT payload FROM learning_memory WHERE memory_id = ?", (memory_id,)
        ).fetchone()

    correction = build_decision_artifact_correction(
        artifact.artifact_id,
        "AI hypothesis proposed after trade close",
        {"ai_hypothesis_reference": "EXP-1", "ai_evidence_id": "AIE-1"},
        NOW,
    )
    record_decision_artifact_correction(store, correction, NOW)

    with sqlite3.connect(db_path) as conn:
        (after,) = conn.execute(
            "SELECT payload FROM learning_memory WHERE memory_id = ?", (memory_id,)
        ).fetchone()
    assert after == before


def test_correction_is_a_separate_real_record_referencing_the_original(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    artifact = build_decision_artifact(now=NOW, strategy_version="v2", agent_output_artifact_ids={})
    record_decision_artifact(store, artifact, NOW)

    correction = build_decision_artifact_correction(
        artifact.artifact_id, "AI hypothesis proposed after trade close", {"ai_hypothesis_reference": "EXP-1"}, NOW
    )
    record_decision_artifact_correction(store, correction, NOW)

    stored_corrections = store.recent(memory_type=DECISION_ARTIFACT_CORRECTION_MEMORY_TYPE, limit=5)
    assert len(stored_corrections) == 1
    assert stored_corrections[0]["payload"]["original_artifact_id"] == artifact.artifact_id
    assert stored_corrections[0]["payload"]["reason"] == "AI hypothesis proposed after trade close"
    assert stored_corrections[0]["payload"]["fields"] == {"ai_hypothesis_reference": "EXP-1"}
    # The original decision_artifact record is untouched -- still exactly one, unmodified.
    stored_artifacts = store.recent(memory_type=DECISION_ARTIFACT_MEMORY_TYPE, limit=5)
    assert len(stored_artifacts) == 1
    assert stored_artifacts[0]["payload"]["ai_hypothesis_reference"] is None
