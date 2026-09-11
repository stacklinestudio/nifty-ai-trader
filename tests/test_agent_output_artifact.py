"""Phase 2 Piece 8, Requirement 3: evidence/agent_output_artifact.py."""

from __future__ import annotations

from datetime import datetime

from agents.contracts import AgentResult
from config import IST
from evidence.agent_output_artifact import (
    AGENT_OUTPUT_MEMORY_TYPE,
    build_agent_output_artifact,
    build_agent_output_artifacts,
    record_agent_output_artifact,
    record_agent_output_artifacts,
)
from learning.memory import MemoryStore


def _result(agent: str, error: str | None = None) -> AgentResult:
    return AgentResult(
        agent,
        datetime(2026, 9, 10, 9, 20, tzinfo=IST),
        73.5,
        evidence=("real evidence line",),
        data={"direction": "BULLISH", "real_field": 42},
        error=error,
        duration_ms=12.5,
    )


def test_build_agent_output_artifact_carries_the_real_agent_result_verbatim():
    artifact = build_agent_output_artifact(_result("technical"), "CAND-20260910-092000-001", "v2")

    assert artifact.agent_name == "technical"
    assert artifact.correlation_id == "CAND-20260910-092000-001"
    assert artifact.agent_version == "v2"
    assert artifact.output == {"direction": "BULLISH", "real_field": 42}
    assert artifact.evidence == ("real evidence line",)
    assert artifact.confidence == 73.5
    assert artifact.available is True
    assert artifact.error is None
    assert artifact.duration_ms == 12.5
    assert artifact.timestamp == "2026-09-10T09:20:00+05:30"


def test_build_agent_output_artifact_never_fabricates_a_correlation_id_when_none_is_known():
    artifact = build_agent_output_artifact(_result("news"), None, "v2")

    assert artifact.correlation_id is None


def test_a_real_agent_failure_is_carried_through_honestly_not_masked():
    """Requirement 11: agent failures must be represented as evidence,
    never converted into a fabricated successful output."""
    failed = AgentResult(
        "volatility", datetime(2026, 9, 10, 9, 20, tzinfo=IST), 0.0, data={}, error="real timeout: no response"
    )

    artifact = build_agent_output_artifact(failed, None, "v2")

    assert artifact.available is False
    assert artifact.error == "real timeout: no response"


def test_build_agent_output_artifacts_produces_exactly_one_per_real_agent_that_ran():
    results = {"technical": _result("technical"), "news": _result("news")}

    artifacts = build_agent_output_artifacts(results, "CAND-1", "v2")

    assert {a.agent_name for a in artifacts} == {"technical", "news"}
    assert all(a.correlation_id == "CAND-1" for a in artifacts)


def test_an_agent_that_never_ran_this_cycle_produces_no_artifact():
    """Requirement 3/9: absence by omission, never a fabricated
    placeholder for an agent that simply never ran (e.g. RiskAgent on a
    no-candidate cycle)."""
    results = {"technical": _result("technical")}

    artifacts = build_agent_output_artifacts(results, None, "v2")

    assert {a.agent_name for a in artifacts} == {"technical"}


def test_record_agent_output_artifact_round_trips_through_memory_store(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    artifact = build_agent_output_artifact(_result("technical"), "CAND-1", "v2")

    memory_id = record_agent_output_artifact(store, artifact, datetime(2026, 9, 10, 9, 21, tzinfo=IST))

    stored = store.recent(memory_type=AGENT_OUTPUT_MEMORY_TYPE, limit=5)
    assert len(stored) == 1
    assert stored[0]["memory_id"] == memory_id
    assert stored[0]["payload"]["artifact_id"] == artifact.artifact_id
    assert stored[0]["payload"]["output"] == {"direction": "BULLISH", "real_field": 42}


def test_record_agent_output_artifacts_returns_a_real_agent_name_to_artifact_id_map(tmp_path):
    """The map's values must be the artifact's own `artifact_id` (the id
    space stored inside each record's payload and what evidence/
    reconstruction.py matches against), not MemoryStore's separate,
    internal `memory_id`."""
    store = MemoryStore(tmp_path / "learning.db")
    artifacts = build_agent_output_artifacts(
        {"technical": _result("technical"), "news": _result("news")}, "CAND-1", "v2"
    )

    ids = record_agent_output_artifacts(store, artifacts, datetime(2026, 9, 10, 9, 21, tzinfo=IST))

    assert set(ids.keys()) == {"technical", "news"}
    stored_artifact_ids = {
        e["payload"]["artifact_id"] for e in store.recent(memory_type=AGENT_OUTPUT_MEMORY_TYPE, limit=5)
    }
    assert set(ids.values()) == stored_artifact_ids
    assert ids["technical"] != ids["news"]
