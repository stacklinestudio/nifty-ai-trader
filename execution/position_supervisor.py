"""Per-tick exit decision for one open paper position.

Deterministic trailing-stop math (risk/trailing_stop.py) and the deterministic
15:15 IST forced-exit check happen here in plain Python -- never delegated to
an agent. TradeSupervisorAgent supplies the target/stop/thesis-invalidated
read on top of that. Nothing in this module sleeps or performs real IO; the
orchestrator's real-time loop supplies ltp/now each tick and performs the
actual broker/notification side effects on an EXIT decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from typing import Any

from agents.contracts import TradeThesis
from agents.trading_agents import TradeSupervisorAgent
from risk.trailing_stop import update_stop

_RECOMMENDATION_TO_REASON = {
    "EXIT_TARGET": "TAKE_PROFIT",
    "EXIT_STOP": "STOP_LOSS",
    "EXIT_INVALIDATED": "THESIS_INVALIDATED",
}


@dataclass
class PositionState:
    """Mutable per-tick state for one open position: current trailed stop
    plus max adverse/favorable excursion (MAE/MFE), tracked in premium
    points from entry."""

    thesis: TradeThesis
    opened_at: datetime
    current_stop: float
    last_valid_ltp: float
    last_quote_at: datetime
    mae: float = 0.0
    mfe: float = 0.0
    entry_regime: str | None = None
    entry_volatility_regime: str | None = None
    entry_consensus: str | None = None
    entry_agent_directions: dict[str, str] | None = None
    entry_order_id: str | None = None
    # Brief 20 (Obsidian knowledge layer): the real score_attribution dict
    # and real validator reasoning from the exact same run_cycle() call
    # that produced this trade -- carried through unmodified so a later
    # Trade Journal export can present them without a fuzzy, separately-
    # computed timestamp join against the signals/audit_events tables.
    # Not used by any decision logic; purely for later real reporting.
    entry_score_attribution: dict[str, Any] | None = None
    entry_validation_reasons: tuple[str, ...] = ()
    # Final Brief follow-up: the real instrument_token known at entry
    # time (state.context["selected_option"], same real source
    # agents/orchestrator.py::_on_risk_decision already uses for the
    # notification's Kite chart link) -- carried through so the
    # dashboard's own current-position card can build the identical
    # real kite_chart_url() for an open position, not just the outbound
    # notification. Not used by any decision logic; purely for later
    # real reporting, same as entry_score_attribution above. None
    # whenever no real option was ever selected (e.g. no candidate).
    entry_instrument_token: int | None = None

    @classmethod
    def opening(
        cls,
        thesis: TradeThesis,
        opened_at: datetime,
        entry_regime: str | None = None,
        entry_volatility_regime: str | None = None,
        entry_consensus: str | None = None,
        entry_agent_directions: dict[str, str] | None = None,
        entry_order_id: str | None = None,
        entry_score_attribution: dict[str, Any] | None = None,
        entry_validation_reasons: tuple[str, ...] = (),
        entry_instrument_token: int | None = None,
    ) -> PositionState:
        return cls(
            thesis, opened_at, thesis.stop, thesis.entry, opened_at, 0.0, 0.0,
            entry_regime, entry_volatility_regime, entry_consensus, entry_agent_directions,
            entry_order_id, entry_score_attribution, entry_validation_reasons, entry_instrument_token,
        )

    def observe(self, ltp: float, now: datetime, trail_pct: float) -> None:
        gain = ltp - self.thesis.entry
        self.mfe = max(self.mfe, gain)
        self.mae = max(self.mae, -gain)
        self.current_stop = update_stop(
            self.thesis.entry, self.thesis.stop, self.current_stop, ltp, trail_pct
        )
        self.last_valid_ltp = ltp
        self.last_quote_at = now


@dataclass(frozen=True)
class TickResult:
    should_exit: bool
    reason: str | None = None
    exit_price: float | None = None
    notify_stale: bool = False


def tick(
    state: PositionState,
    ltp: float | None,
    now: datetime,
    forced_exit_time: dt_time,
    stale_data_seconds: int,
    trade_supervisor_agent: TradeSupervisorAgent,
    regime_context: dict[str, Any] | None = None,
    trail_pct: float = 0.15,
) -> TickResult:
    if ltp is not None:
        state.observe(ltp, now, trail_pct)

    # Forced exit is a deterministic time check independent of price, P&L,
    # or any agent's recommendation -- it is evaluated first and cannot be
    # deferred by staleness or a "STRENGTHENING" read.
    if now.timetz().replace(tzinfo=None) >= forced_exit_time:
        return TickResult(True, "FORCED_EXIT", state.last_valid_ltp)

    if (now - state.last_quote_at).total_seconds() > stale_data_seconds:
        # Fail closed: do not guess a price. Hold at the last-known state
        # and let the caller notify, rather than silently trusting stale data.
        return TickResult(False, notify_stale=True)

    supervision = trade_supervisor_agent.run(
        {
            "thesis": state.thesis,
            "ltp": state.last_valid_ltp,
            "current_stop": state.current_stop,
            **(regime_context or {}),
        }
    )
    reason = _RECOMMENDATION_TO_REASON.get(supervision.data.get("recommendation"))
    return TickResult(True, reason, state.last_valid_ltp) if reason else TickResult(False)
