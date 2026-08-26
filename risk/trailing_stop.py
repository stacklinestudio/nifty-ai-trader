"""Deterministic trailing-stop arithmetic. Never LLM-judged: an agent may
recommend tightening/loosening in an extreme regime shift, but the actual
stop price is always computed here, in plain Python, and only ever moves in
the trader's favor.
"""

from __future__ import annotations


def update_stop(
    entry: float, initial_stop: float, current_stop: float, ltp: float, trail_pct: float = 0.15
) -> float:
    """Ratchets the stop to breakeven at 1R, then partial profit at 1.5R,
    then trails behind LTP by a fixed percentage of premium.

    trail_pct is a percent of current premium rather than an ATR multiple:
    option-premium ATR isn't reliably available at this layer (ATR elsewhere
    in this codebase is computed on the underlying/spot, not the option),
    and premium moves are proportionally larger than the underlying's, so a
    percent-of-premium trail scales naturally as the position runs. 15% is a
    moderate default — tight enough to lock in gains, loose enough to
    survive normal intraday premium noise — and is configurable via
    Settings.trail_percent, not hardcoded for all callers.
    """
    initial_risk = entry - initial_stop
    if initial_risk <= 0:
        return current_stop
    unrealized_gain = ltp - entry
    stop = current_stop
    if unrealized_gain >= initial_risk:
        stop = max(stop, entry)
    if unrealized_gain >= 1.5 * initial_risk:
        stop = max(stop, entry + 0.5 * initial_risk)
    return max(stop, ltp - ltp * trail_pct)
