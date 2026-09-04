"""COUNTERFACTUAL -- INDEX-PRICE PROXY, NOT REAL OPTION P&L.

Brief 12 Part B: the real, honest way to test whether signal_threshold
is too strict, without lowering it in production. For a real candidate
that structurally fired but was rejected (confidence-gated, validator-
rejected, or risk-vetoed), this records the same real entry/stop/target
levels the live pipeline would have set, then walks forward through REAL
SUBSEQUENT index price to see what actually happened.

Real historical option premium data for this project's 42-day window is
confirmed genuinely unavailable (Brief 8-9's live investigation: Kite
purges expired contracts from its instrument list, no dump was saved
before they expired) -- so every entry/stop/target level here, and every
outcome, is on the real NIFTY INDEX, never a real or fabricated option
price. This is a research signal about whether the underlying's real
direction validated or invalidated a rejected setup -- never a trade
outcome, never P&L. CounterfactualRecord.label carries this exact
caveat as a real field (a read-only property, not something a caller can
override) on every single record; nothing here is wired into
learning.memory, DailyLimits, or any real trade record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from execution.live_context import OPENING_RANGE_MINUTES, _atr_zones
from strategy.orb import opening_range

COUNTERFACTUAL_LABEL = "COUNTERFACTUAL -- INDEX-PRICE PROXY, NOT REAL OPTION P&L"


@dataclass(frozen=True)
class CounterfactualRecord:
    timestamp: datetime
    setup_type: str
    direction: str
    rejection_reason: str
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str  # TAKE_PROFIT | STOP_LOSS | SESSION_END
    exit_time: datetime
    profitable: bool

    @property
    def label(self) -> str:
        """Always this exact string -- a read-only property, not a
        constructor field, so no caller can construct a record that omits
        or alters it."""
        return COUNTERFACTUAL_LABEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "timestamp": self.timestamp.isoformat(),
            "setup_type": self.setup_type,
            "direction": self.direction,
            "rejection_reason": self.rejection_reason,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "exit_time": self.exit_time.isoformat(),
            "profitable": self.profitable,
        }

    def describe(self) -> str:
        verdict = "PROFITABLE" if self.profitable else "NOT PROFITABLE"
        return (
            f"[{self.label}] {self.setup_type} {self.direction} rejected "
            f"({self.rejection_reason}) at {self.timestamp.isoformat()} -- "
            f"entry={self.entry:.2f} stop={self.stop:.2f} target={self.target:.2f} "
            f"-> {self.exit_reason} at {self.exit_price:.2f} ({verdict}, index-proxy only)"
        )


def _zones_for_rejected_candidate(
    setup_type: str, direction: str, todays: pd.DataFrame, features: dict[str, float]
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """The exact real zone-computation logic execution/live_context.py::
    _add_candidate already uses on its cleared-threshold path -- that path
    only ever runs it after the confidence gate, so a rejected candidate
    never had zones computed at all. Applying the same real functions
    (_atr_zones / opening_range) to a rejected candidate isn't new math,
    it's the same math this codebase already trusts, just also run here.
    """
    if setup_type == "OPENING_RANGE_BREAKOUT":
        high, low = opening_range(todays, OPENING_RANGE_MINUTES)
        spread = max(high - low, 0.01)
        if direction == "CALL":
            return (high, high + spread * 0.1), (low, low), (high + spread * 1.5, high + spread * 2.0)
        return (low - spread * 0.1, low), (high, high), (low - spread * 2.0, low - spread * 1.5)
    return _atr_zones(direction, features["close"], features["atr"])


def _walk_forward_index_price(
    direction: str, stop: float, target: float, subsequent: pd.DataFrame
) -> tuple[float, str, pd.Timestamp]:
    """Mirrors backtest/simulator.py::Simulator.exit_price's real,
    already-tested walking pattern and its conservative same-bar ordering
    (a bar that could satisfy both stop and target counts as the stop,
    never the more favorable outcome) -- generalized for direction, since
    Simulator.exit_price only handles a CALL-shaped trade (stop below
    entry, target above); a PUT-shaped rejected candidate has stop above
    entry and target below, and needs the mirrored comparison.
    """
    for timestamp, row in subsequent.iterrows():
        if direction == "CALL":
            if row.low <= stop:
                return stop, "STOP_LOSS", timestamp
            if row.high >= target:
                return target, "TAKE_PROFIT", timestamp
        else:
            if row.high >= stop:
                return stop, "STOP_LOSS", timestamp
            if row.low <= target:
                return target, "TAKE_PROFIT", timestamp
    last = subsequent.iloc[-1]
    return float(last.close), "SESSION_END", subsequent.index[-1]


def evaluate_counterfactual(
    setup_type: str,
    direction: str,
    rejection_reason: str,
    decision_time: datetime,
    todays: pd.DataFrame,
    remaining_today: pd.DataFrame,
    features: dict[str, float],
) -> CounterfactualRecord | None:
    """Real entry/stop/target from the same real zone logic, walked
    forward against REAL subsequent same-day index candles only -- no
    overnight hold (this system's own real forced-exit-by-day-end
    discipline, honored here rather than inventing a different rule).
    Returns None (not fabricated) when there is no real subsequent price
    left in the session to check.
    """
    if remaining_today.empty:
        return None
    entry_zone, stop_zone, target_zone = _zones_for_rejected_candidate(
        setup_type, direction, todays, features
    )
    entry = (entry_zone[0] + entry_zone[1]) / 2
    stop = (stop_zone[0] + stop_zone[1]) / 2
    target = (target_zone[0] + target_zone[1]) / 2
    exit_price, exit_reason, exit_time = _walk_forward_index_price(
        direction, stop, target, remaining_today
    )
    if exit_reason == "TAKE_PROFIT":
        profitable = True
    elif exit_reason == "STOP_LOSS":
        profitable = False
    else:  # SESSION_END -- real net index-point move, direction-aware
        net_move = (exit_price - entry) if direction == "CALL" else (entry - exit_price)
        profitable = net_move > 0
    return CounterfactualRecord(
        timestamp=decision_time,
        setup_type=setup_type,
        direction=direction,
        rejection_reason=rejection_reason,
        entry=entry,
        stop=stop,
        target=target,
        exit_price=exit_price,
        exit_reason=exit_reason,
        exit_time=exit_time.to_pydatetime() if hasattr(exit_time, "to_pydatetime") else exit_time,
        profitable=profitable,
    )
