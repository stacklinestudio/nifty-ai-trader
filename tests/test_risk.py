from risk.position_sizer import position_size
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits


def test_position_size_obeys_risk_and_lot():
    assert position_size(100, 90, 200, 25, 5000) == 0
    assert position_size(20, 18, 200, 25, 5000) == 100


def test_risk_plan_never_exceeds_budget():
    plan = RiskManager(200, 5000).plan_long_option(20, 2, 25)
    assert plan and plan.estimated_risk <= 200 and plan.quantity % 25 == 0


def test_daily_limit_allows_exactly_one_trade():
    limits = DailyLimits(1, 400)
    assert limits.can_open()
    limits.register_open()
    assert not limits.can_open()


def test_daily_loss_blocks_trade():
    limits = DailyLimits(1, 200)
    limits.realized_pnl = -200
    assert not limits.can_open()
