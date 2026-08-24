from __future__ import annotations

from datetime import datetime

from learning.memory import MemoryStore


def record_trade(store: MemoryStore, facts: dict, timestamp: datetime) -> str:
    return store.append("trade", facts, timestamp)
