from __future__ import annotations

from dataclasses import dataclass

from risk.position_sizer import position_size


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop: float
    target: float
    quantity: int
    estimated_risk: float


class RiskManager:
    """max_risk/max_position_value are fixed at construction from
    Settings.max_risk_per_trade/max_position_value and never change during
    the day -- sizing has no path to the day's realized P&L (DailyLimits is
    a separate object RiskAgent consults independently) or to any notion of
    "current balance." Every trade of the day is sized off the same fixed
    base, deliberately, so a profitable trade earlier in the day cannot
    compound into a larger size for a later one -- pinned by
    tests/test_multi_trade_sizing.py::test_second_trade_sizing_is_unaffected_by_first_trades_profit.
    """

    def __init__(
        self,
        max_risk: float,
        max_position_value: float,
        reward_multiple: float = 1.5,
        stop_atr_multiple: float = 1.0,
    ) -> None:
        self.max_risk = max_risk
        self.max_position_value = max_position_value
        self.reward_multiple = reward_multiple
        self.stop_atr_multiple = stop_atr_multiple

    def plan_long_option(self, entry: float, atr: float, lot_size: int) -> TradePlan | None:
        stop = max(0.05, entry - max(atr * self.stop_atr_multiple, entry * 0.08))
        qty = position_size(entry, stop, self.max_risk, lot_size, self.max_position_value)
        risk = (entry - stop) * qty
        return (
            TradePlan(entry, stop, entry + (entry - stop) * self.reward_multiple, qty, risk)
            if qty and risk <= self.max_risk
            else None
        )

    def validate(self, plan: TradePlan, spread: float | None, max_spread: float = 2.0) -> None:
        if plan.estimated_risk > self.max_risk or plan.quantity <= 0:
            raise ValueError("Risk budget exceeded")
        if spread is not None and spread > max_spread:
            raise ValueError("Illiquid option spread")
