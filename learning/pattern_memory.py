"""Read-only aggregation over real recorded trades: "of setup type X in
regime Y, what's the realized win rate/expectancy so far, and on how many
samples." This never writes anything and never affects live parameters --
it only answers a question. Any proposed strategy change based on what it
finds still has to go through learning.promotion_engine.decide(), which
requires historical replay, walk-forward, out-of-sample evidence, and human
approval -- a losing streak here cannot, by itself, promote or demote
anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from learning.memory import MemoryStore

MIN_SAMPLES_FOR_CONFIDENCE = 20
_ALL_TRADES_LIMIT = 100_000


@dataclass(frozen=True)
class PatternStats:
    setup_type: str
    regime: str
    sample_size: int
    win_rate: float | None
    expectancy: float | None
    low_confidence: bool


def stats_for(store: MemoryStore, setup_type: str, regime: str) -> PatternStats:
    """low_confidence is True whenever sample_size < MIN_SAMPLES_FOR_CONFIDENCE
    (including zero samples) -- callers must not treat a handful of trades as
    authoritative, and promotion_engine.decide() requires far more than a
    win-rate number regardless of this flag.
    """
    trades = [
        entry["payload"]
        for entry in store.recent(memory_type="trade", limit=_ALL_TRADES_LIMIT)
        if entry["payload"].get("setup_type") == setup_type
        and entry["payload"].get("entry_regime") == regime
        and entry["payload"].get("pnl") is not None
    ]
    sample_size = len(trades)
    if sample_size == 0:
        return PatternStats(setup_type, regime, 0, None, None, True)
    wins = sum(1 for trade in trades if trade["pnl"] > 0)
    win_rate = wins / sample_size
    expectancy = sum(trade["pnl"] for trade in trades) / sample_size
    return PatternStats(
        setup_type,
        regime,
        sample_size,
        win_rate,
        expectancy,
        sample_size < MIN_SAMPLES_FOR_CONFIDENCE,
    )
