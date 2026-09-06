"""Serializes/reconstructs PositionState so an open position survives a
process restart. An unmonitored open position is a real safety issue, not
just a data-completeness gap -- this exists specifically so crash recovery
doesn't have to guess at entry/stop/target from partial audit-log summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.contracts import TradeCandidate, TradeThesis
from execution.position_supervisor import PositionState


def thesis_to_dict(thesis: TradeThesis) -> dict[str, Any]:
    candidate = thesis.candidate
    return {
        "candidate": {
            "direction": candidate.direction,
            "setup_type": candidate.setup_type,
            "underlying": candidate.underlying,
            "confidence": candidate.confidence,
            "evidence": list(candidate.evidence),
            "invalidations": list(candidate.invalidations),
            "entry_zone": list(candidate.entry_zone),
            "stop_zone": list(candidate.stop_zone),
            "target_zone": list(candidate.target_zone),
            "candidate_id": candidate.candidate_id,
        },
        "symbol": thesis.symbol,
        "entry": thesis.entry,
        "stop": thesis.stop,
        "target": thesis.target,
        "quantity": thesis.quantity,
        "estimated_risk": thesis.estimated_risk,
        "confidence": thesis.confidence,
        "evidence": list(thesis.evidence),
        "invalidations": list(thesis.invalidations),
    }


def thesis_from_dict(data: dict[str, Any]) -> TradeThesis:
    c = data["candidate"]
    candidate = TradeCandidate(
        c["direction"],
        c["setup_type"],
        c["underlying"],
        c["confidence"],
        tuple(c["evidence"]),
        tuple(c["invalidations"]),
        tuple(c["entry_zone"]),
        tuple(c["stop_zone"]),
        tuple(c["target_zone"]),
        c["candidate_id"],
    )
    return TradeThesis(
        candidate,
        data["symbol"],
        data["entry"],
        data["stop"],
        data["target"],
        data["quantity"],
        data["estimated_risk"],
        data["confidence"],
        tuple(data["evidence"]),
        tuple(data["invalidations"]),
    )


def position_state_to_dict(state: PositionState) -> dict[str, Any]:
    return {
        "thesis": thesis_to_dict(state.thesis),
        "opened_at": state.opened_at.isoformat(),
        "current_stop": state.current_stop,
        "last_valid_ltp": state.last_valid_ltp,
        "last_quote_at": state.last_quote_at.isoformat(),
        "mae": state.mae,
        "mfe": state.mfe,
        "entry_regime": state.entry_regime,
        "entry_volatility_regime": state.entry_volatility_regime,
        "entry_consensus": state.entry_consensus,
        "entry_agent_directions": state.entry_agent_directions,
        "entry_order_id": state.entry_order_id,
        "entry_score_attribution": state.entry_score_attribution,
        "entry_validation_reasons": list(state.entry_validation_reasons),
        "entry_instrument_token": state.entry_instrument_token,
    }


def position_state_from_dict(data: dict[str, Any]) -> PositionState:
    return PositionState(
        thesis_from_dict(data["thesis"]),
        datetime.fromisoformat(data["opened_at"]),
        data["current_stop"],
        data["last_valid_ltp"],
        datetime.fromisoformat(data["last_quote_at"]),
        data["mae"],
        data["mfe"],
        data["entry_regime"],
        data["entry_volatility_regime"],
        data["entry_consensus"],
        data["entry_agent_directions"],
        data["entry_order_id"],
        # .get() with a real, honest default: a position persisted before
        # Brief 20 added these two fields has neither in its stored JSON --
        # crash recovery must not fail on an older real record.
        data.get("entry_score_attribution"),
        tuple(data.get("entry_validation_reasons", ())),
        data.get("entry_instrument_token"),
    )
