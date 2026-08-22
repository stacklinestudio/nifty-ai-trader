from __future__ import annotations


def position_size(
    entry: float, stop: float, risk_budget: float, lot_size: int, max_position_value: float
) -> int:
    per_unit = abs(entry - stop)
    if entry <= 0 or per_unit <= 0 or lot_size <= 0:
        return 0
    by_risk = int(risk_budget // per_unit)
    by_value = int(max_position_value // entry)
    return (min(by_risk, by_value) // lot_size) * lot_size
