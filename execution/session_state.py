"""Phase 2 Piece 6: real, date-keyed persistence closing the two named,
real gaps this project's own audit already found:

1. risk/trade_limits.py::DailyLimits.trades / .realized_pnl are plain
   in-memory state, constructed fresh (0 / 0.0) every time an
   Orchestrator is built -- a real gap: a mid-day crash-restart
   currently permits exceeding max_trades_per_day and continuing to
   trade past max_daily_loss for the rest of that real day.
2. agents/orchestrator.py::Orchestrator._stopped_out_today is also a
   plain in-memory list, silently cleared on every restart -- a setup
   that stopped out earlier in the real day could re-enter after a
   restart without the re-entry guard (Orchestrator._blocked_reentry)
   catching it.

Reuses storage/database.py's existing `daily_metrics` table (see
Database.session_state/save_session_state's own docstrings for why --
schema-only, confirmed unused for its own semantics before this).

Date-keyed by design, matching the existing, documented "fresh process
each trading morning" deployment model (execution/scheduler.py's own
module docstring): a genuinely NEW real trading day (a different date
than whatever is persisted) correctly starts fresh at 0/0.0/[] -- that
reset is the CORRECT, intended behavior, not a bug. Only a SAME-day
restart must reconstruct the real accumulated state. This module never
decides "is today a new day" on its own wall-clock read -- every
function here takes `today`/`now` explicitly from its caller, so a
caller (a live process, or a test) controls what "today" means.

Fails closed on corrupted state, deliberately the OPPOSITE of the old
gap's default: if a real daily_metrics row exists for today but is
malformed (validate_session_state below), the safe assumption is NOT
"start fresh at 0 trades" -- that is exactly the unsafe default this
module exists to replace. A corrupted row means today's real trade
count/realized P&L cannot be safely determined, so the caller must
treat today's limits as already exhausted (see build_fail_closed_state)
rather than silently permitting new entries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from storage.database import Database

StoppedOutEntry = tuple[str, str, str | None]


@dataclass(frozen=True)
class SessionState:
    session_date: str  # real ISO date (YYYY-MM-DD), also the daily_metrics primary key
    trades: int
    realized_pnl: float
    stopped_out_today: tuple[StoppedOutEntry, ...]
    strategy_version: str
    last_processed_timestamp: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "trades": self.trades,
            "realized_pnl": self.realized_pnl,
            "stopped_out_today": [list(entry) for entry in self.stopped_out_today],
            "strategy_version": self.strategy_version,
            "last_processed_timestamp": self.last_processed_timestamp,
            "updated_at": self.updated_at,
        }


def fresh_session_state(today: date, strategy_version: str, now: datetime) -> SessionState:
    """A genuinely new real trading day -- 0 trades, 0.0 realized P&L, no
    stop-outs yet. This is the CORRECT default for a new day; it is only
    ever wrong to apply it to a day that already has real persisted
    state (see load_or_start_session_state below)."""
    return SessionState(today.isoformat(), 0, 0.0, (), strategy_version, None, now.isoformat())


def build_fail_closed_state(today: date, strategy_version: str, now: datetime) -> SessionState:
    """The safe default when today's real persisted state exists but is
    corrupted/unparseable -- trades is set to a real, large sentinel so
    DailyLimits.can_open() (trades < max_trades) is false regardless of
    the real configured max_trades_per_day, and realized_pnl is set to a
    real, large negative sentinel so the max_daily_loss check also
    fails. Blocks all NEW entries for the rest of this real day; it does
    NOT touch any already-open position (recover_open_positions is a
    separate, unaffected real code path)."""
    return SessionState(
        today.isoformat(),
        trades=10_000,
        realized_pnl=-1e12,
        stopped_out_today=(),
        strategy_version=strategy_version,
        last_processed_timestamp=None,
        updated_at=now.isoformat(),
    )


def validate_session_state(payload: dict[str, Any]) -> bool:
    """Real, explicit structural checks -- not just "did json.loads
    succeed." A malformed real daily_metrics row (a bad manual edit, a
    partial write, a future schema this version doesn't understand)
    must be caught here, not silently trusted."""
    try:
        if not isinstance(payload.get("session_date"), str):
            return False
        trades = payload.get("trades")
        if not isinstance(trades, int) or isinstance(trades, bool) or trades < 0:
            return False
        realized_pnl = payload.get("realized_pnl")
        if not isinstance(realized_pnl, (int, float)) or isinstance(realized_pnl, bool):
            return False
        if math.isnan(float(realized_pnl)) or math.isinf(float(realized_pnl)):
            return False
        stopped_out = payload.get("stopped_out_today")
        if not isinstance(stopped_out, list):
            return False
        for entry in stopped_out:
            if not isinstance(entry, list) or len(entry) != 3:
                return False
            direction, setup_type, entry_regime = entry
            if not isinstance(direction, str) or not isinstance(setup_type, str):
                return False
            if entry_regime is not None and not isinstance(entry_regime, str):
                return False
        return True
    except (TypeError, ValueError):
        return False


def parse_session_state(payload: dict[str, Any]) -> SessionState:
    """Caller must have already confirmed validate_session_state(payload)
    is True -- this does no validation of its own, matching the "one
    real place decides validity" principle."""
    return SessionState(
        session_date=payload["session_date"],
        trades=payload["trades"],
        realized_pnl=payload["realized_pnl"],
        stopped_out_today=tuple(tuple(entry) for entry in payload["stopped_out_today"]),
        strategy_version=payload.get("strategy_version", "v2"),
        last_processed_timestamp=payload.get("last_processed_timestamp"),
        updated_at=payload.get("updated_at", ""),
    )


def load_session_state(database: Database, today: date) -> dict[str, Any] | None:
    """Pure read -- the raw persisted payload (or None), never parsed or
    validated here, so a caller can distinguish "no row" from "a row
    that failed validation" (see RestartDiagnosis)."""
    return database.session_state(today.isoformat())


def save_session_state(database: Database, state: SessionState) -> None:
    database.save_session_state(state.session_date, state.to_dict())


class RestartDiagnosis(str, Enum):
    """Requirement 6's explicit process-level restart classification.
    Deliberately scoped to THIS module's own concern (does real
    session_date-keyed state / an open position already exist for
    today) -- WebSocket reconnect and market-data-gap detection are
    real, already-built, separate mechanisms at a different layer
    (data/option_tick_capture.py::KiteTicker's own real reconnect
    handling and GapRecord/capture_gaps_<date>.json -- confirmed real
    and tested by this project's own earlier audit), not rebuilt or
    redefined here."""

    FIRST_RUN_TODAY = "FIRST_RUN_TODAY"  # no real persisted session_state for today at all
    CLEAN_CONTINUATION = "CLEAN_CONTINUATION"  # real persisted state exists, no open position -- safe
    INTERRUPTED_WITH_OPEN_POSITION = "INTERRUPTED_WITH_OPEN_POSITION"  # a position was live when the prior process stopped
    CORRUPTED_STATE = "CORRUPTED_STATE"  # real persisted state exists but failed validation -- fail closed


def diagnose_restart(raw_payload: dict[str, Any] | None, open_position_count: int) -> RestartDiagnosis:
    if raw_payload is None:
        return RestartDiagnosis.FIRST_RUN_TODAY
    if not validate_session_state(raw_payload):
        return RestartDiagnosis.CORRUPTED_STATE
    if open_position_count > 0:
        return RestartDiagnosis.INTERRUPTED_WITH_OPEN_POSITION
    return RestartDiagnosis.CLEAN_CONTINUATION
