from risk.position_sizer import position_size
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits


def test_position_size_obeys_risk_and_lot():
    # Realistic NIFTY premium (~100) and lot size (75) at the current caps
    # (600 risk / 7500 value): a 10-point stop leaves only 60 units of risk
    # budget per lot-worth of risk, which floors to 0 lots (60 < 75).
    assert position_size(100, 90, 600, 75, 7500) == 0
    # An 8-point stop (matching RiskManager's 8%-of-premium floor) leaves
    # exactly one lot's worth of both risk and value budget.
    assert position_size(100, 92, 600, 75, 7500) == 75


def test_risk_plan_never_exceeds_budget():
    plan = RiskManager(600, 7500).plan_long_option(100, 8, 75)
    assert plan and plan.estimated_risk <= 600 and plan.quantity % 75 == 0


def test_daily_limit_allows_exactly_one_trade():
    limits = DailyLimits(1, 400)
    assert limits.can_open()
    limits.register_open()
    assert not limits.can_open()


def test_daily_loss_blocks_trade():
    limits = DailyLimits(1, 200)
    limits.realized_pnl = -200
    assert not limits.can_open()
