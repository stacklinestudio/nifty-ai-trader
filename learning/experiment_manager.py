from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from learning.memory import MemoryStore


@dataclass(frozen=True)
class Experiment:
    hypothesis: str
    parameters: dict
    strategy_version: str
    status: str = "CANDIDATE"


def create_experiment(store: MemoryStore, experiment: Experiment, timestamp: datetime) -> str:
    return store.append(
        "experiment",
        {
            "hypothesis": experiment.hypothesis,
            "parameters": experiment.parameters,
            "strategy_version": experiment.strategy_version,
            "status": experiment.status,
            "requires": ["historical", "walk-forward", "out-of-sample"],
        },
        timestamp,
    )
