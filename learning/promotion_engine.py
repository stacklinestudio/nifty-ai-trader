from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reasons: tuple[str, ...]


def decide(
    has_historical: bool, has_walk_forward: bool, has_out_of_sample: bool, human_approved: bool
) -> PromotionDecision:
    missing = [
        name
        for name, value in (
            ("historical validation", has_historical),
            ("walk-forward validation", has_walk_forward),
            ("out-of-sample validation", has_out_of_sample),
            ("human approval", human_approved),
        )
        if not value
    ]
    return PromotionDecision(
        not missing, tuple(missing or ["Candidate may be promoted by explicit process."])
    )
