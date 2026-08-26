from risk.trailing_stop import update_stop


def test_stop_moves_to_breakeven_at_1r():
    # entry=100, initial_stop=90 -> initial_risk=10; +10 gain hits 1R exactly.
    # ltp=110 with the default 15% trail gives a looser trail candidate
    # (93.5) than breakeven (100), so breakeven is the binding constraint.
    stop = update_stop(100, 90, 90, 110)
    assert stop == 100


def test_stop_locks_partial_profit_at_1point5r():
    # +15 gain hits 1.5R; partial-lock candidate (105) beats the trail
    # candidate (115 - 15% = 97.75) here too.
    stop = update_stop(100, 90, 90, 115)
    assert stop == 105


def test_stop_trails_behind_ltp_beyond_partial_profit_lock():
    stop = update_stop(100, 90, 105, 200, trail_pct=0.15)
    assert stop == 200 - 200 * 0.15


def test_stop_never_loosens_on_adverse_price_tick():
    stop_at_high = update_stop(100, 90, 90, 150, trail_pct=0.15)
    stop_after_pullback = update_stop(100, 90, stop_at_high, 120, trail_pct=0.15)
    assert stop_after_pullback == stop_at_high


def test_invalid_initial_risk_returns_current_stop_unchanged():
    assert update_stop(100, 100, 100, 110) == 100
    assert update_stop(100, 110, 100, 110) == 100
