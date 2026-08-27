"""Confidence-scaled position sizing, strictly within an already-approved
risk envelope.

This never changes what "approved" means: max_quantity is whatever
position_size()/RiskManager already computed under MAX_RISK_PER_TRADE and
MAX_POSITION_VALUE, and this function only decides how much of that
already-approved size to take. It cannot move the ceiling, and it cannot
bypass the risk agent's veto -- both still apply exactly as before, to
whatever quantity this returns.
"""

from __future__ import annotations


def scale_quantity(
    max_quantity: int,
    confidence: float,
    lot_size: int,
    low_confidence: float = 75.0,
    high_confidence: float = 95.0,
) -> int:
    """Linearly scales lots from 1 (at low_confidence or below) up to the
    full max_quantity (at high_confidence or above). Never returns more than
    max_quantity, never fewer than one lot as long as one lot is affordable
    (max_quantity >= lot_size) -- "size down, not to zero," per spec.
    """
    if max_quantity < lot_size or lot_size <= 0:
        return 0
    max_lots = max_quantity // lot_size
    if confidence >= high_confidence:
        lots = max_lots
    elif confidence <= low_confidence:
        lots = 1
    else:
        fraction = (confidence - low_confidence) / (high_confidence - low_confidence)
        lots = 1 + round(fraction * (max_lots - 1))
    return max(1, min(max_lots, lots)) * lot_size
