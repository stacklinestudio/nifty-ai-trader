"""Brief 14 Phase 2a: real Expected Value (EV) in R multiples, tiered
and labeled. MEASUREMENT ONLY -- proves the arithmetic is exactly right
(not just "it runs"), that 1R is read live from Settings (never
hardcoded), and that missing real data honestly reports
INSUFFICIENT_DATA rather than a fabricated number.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config import IST, Settings
from learning.memory import MemoryStore
from research.counterfactual import CounterfactualRecord
from research.expected_value import (
    AVG_LOSS_R,
    COUNTERFACTUAL_SOURCE,
    INSUFFICIENT_DATA,
    MIN_COUNTERFACTUAL_SAMPLES,
    REAL_TRADE_SOURCE,
    REWARD_RISK_RATIO,
    compute_ev,
    real_slippage,
    real_transaction_costs,
)


def _counterfactual_record(setup_type: str, regime: str, profitable: bool) -> CounterfactualRecord:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    return CounterfactualRecord(
        timestamp=now,
        setup_type=setup_type,
        direction="CALL",
        rejection_reason="confidence_gated",
        entry=100.0,
        stop=95.0,
        target=110.0,
        exit_price=110.0 if profitable else 95.0,
        exit_reason="TAKE_PROFIT" if profitable else "STOP_LOSS",
        exit_time=now + timedelta(minutes=10),
        profitable=profitable,
        regime=regime,
    )


def test_ev_arithmetic_is_exact_for_a_known_counterfactual_scenario(tmp_path):
    """Verifies the arithmetic, not just that it runs: a controlled
    win_rate with known REWARD_RISK_RATIO/AVG_LOSS_R and known real
    costs/slippage must produce the exact EV(R) the formula predicts."""
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")

    # Exactly MIN_COUNTERFACTUAL_SAMPLES records, exactly 60% profitable
    # -- a controlled, known win_rate.
    n = MIN_COUNTERFACTUAL_SAMPLES
    n_wins = int(n * 0.6)
    records = [_counterfactual_record("TREND_CONTINUATION", "TREND_UP", True) for _ in range(n_wins)]
    records += [_counterfactual_record("TREND_CONTINUATION", "TREND_UP", False) for _ in range(n - n_wins)]

    estimate = compute_ev("TREND_CONTINUATION", "TREND_UP", settings, memory, records)

    assert estimate.ev_source == COUNTERFACTUAL_SOURCE
    assert estimate.sample_size == n
    win_rate = n_wins / n
    assert estimate.win_rate == win_rate
    assert estimate.avg_win_r == REWARD_RISK_RATIO
    assert estimate.avg_loss_r == AVG_LOSS_R

    one_r = settings.max_risk_per_trade
    expected_costs_r = real_transaction_costs() / one_r
    expected_slippage_r = real_slippage(settings) / one_r
    expected_ev_r = (
        win_rate * REWARD_RISK_RATIO - (1 - win_rate) * AVG_LOSS_R - expected_costs_r - expected_slippage_r
    )
    assert estimate.costs_r == expected_costs_r
    assert estimate.slippage_r == expected_slippage_r
    assert estimate.ev_r == expected_ev_r
    assert estimate.one_r_rupees == one_r


def test_no_real_or_counterfactual_data_reports_insufficient_data_not_fabricated(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")

    # A setup_type/regime combination with zero real trades and zero real
    # counterfactual records.
    estimate = compute_ev("VWAP_REJECTION", "UNCERTAIN", settings, memory, [])

    assert estimate.ev_source == INSUFFICIENT_DATA
    assert estimate.ev_r is None
    assert estimate.win_rate is None
    assert estimate.sample_size == 0


def test_below_minimum_counterfactual_sample_size_also_reports_insufficient_data(tmp_path):
    """A few real counterfactual records exist, but fewer than
    MIN_COUNTERFACTUAL_SAMPLES -- still honestly INSUFFICIENT_DATA, not a
    number computed from too small a real sample to trust."""
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")
    records = [_counterfactual_record("OPENING_RANGE_BREAKOUT", "TREND_UP", True) for _ in range(3)]

    estimate = compute_ev("OPENING_RANGE_BREAKOUT", "TREND_UP", settings, memory, records)

    assert estimate.ev_source == INSUFFICIENT_DATA
    assert estimate.ev_r is None
    assert estimate.sample_size == 3  # real count reported, even though not enough to trust


def test_1r_is_read_live_from_settings_not_hardcoded(tmp_path):
    """Changes max_risk_per_trade and confirms the EV calculation's
    R-conversion changes accordingly -- 1R must never be a hardcoded 600."""
    memory = MemoryStore(tmp_path / "paper.db")
    n = MIN_COUNTERFACTUAL_SAMPLES
    records = [_counterfactual_record("VWAP_BREAKOUT", "TREND_DOWN", True) for _ in range(n)]

    settings_600 = Settings(database_path=tmp_path / "paper.db", max_risk_per_trade=600.0)
    settings_1200 = Settings(database_path=tmp_path / "paper.db", max_risk_per_trade=1200.0)

    estimate_600 = compute_ev("VWAP_BREAKOUT", "TREND_DOWN", settings_600, memory, records)
    estimate_1200 = compute_ev("VWAP_BREAKOUT", "TREND_DOWN", settings_1200, memory, records)

    assert estimate_600.one_r_rupees == 600.0
    assert estimate_1200.one_r_rupees == 1200.0
    # Real costs/slippage are the same absolute ₹ amount either way, so
    # halving 1R must exactly double their R-denominated size.
    assert estimate_1200.costs_r == estimate_600.costs_r / 2
    assert estimate_1200.slippage_r == estimate_600.slippage_r / 2
    # AvgWin(R)/AvgLoss(R) are structural (fixed real ratios), unaffected
    # by 1R -- only the real costs/slippage terms scale.
    assert estimate_1200.avg_win_r == estimate_600.avg_win_r
    assert estimate_1200.ev_r != estimate_600.ev_r


def test_real_trade_data_tier_activates_automatically_once_enough_real_trades_exist(tmp_path):
    """Tier 1 must be checked first and used automatically, with no
    further code changes, once real trades accumulate -- proven here by
    actually writing real trade records via MemoryStore and confirming
    compute_ev picks them up in preference to a real counterfactual
    sample that would otherwise apply."""
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")
    now = datetime(2026, 9, 1, tzinfo=IST)

    for i in range(25):  # comfortably above MIN_SAMPLES_FOR_CONFIDENCE=20
        pnl = 900.0 if i % 2 == 0 else -600.0  # a real, known mixed outcome
        memory.append(
            "trade",
            {"setup_type": "MOMENTUM_CONTINUATION", "entry_regime": "TREND_UP", "pnl": pnl},
            now + timedelta(days=i),
        )

    # A real counterfactual sample for the SAME combination -- tier 1
    # must win even though tier 2 also has enough real samples.
    counterfactual_records = [
        _counterfactual_record("MOMENTUM_CONTINUATION", "TREND_UP", True)
        for _ in range(MIN_COUNTERFACTUAL_SAMPLES)
    ]

    estimate = compute_ev("MOMENTUM_CONTINUATION", "TREND_UP", settings, memory, counterfactual_records)

    assert estimate.ev_source == REAL_TRADE_SOURCE
    assert estimate.sample_size == 25
    expected_expectancy = (13 * 900.0 + 12 * -600.0) / 25
    assert estimate.ev_r == expected_expectancy / settings.max_risk_per_trade


def test_real_transaction_costs_use_the_cited_real_fee_schedule():
    """Real, current NSE/Zerodha fee schedule (cited in
    research/expected_value.py, fetched live from zerodha.com/charges/,
    2026-09): brokerage Rs20/order (both legs), STT 0.15% sell-side,
    NSE transaction charges 0.03553% both legs, SEBI Rs10/crore both
    legs, GST 18% on (brokerage+transaction+SEBI), stamp duty 0.003%
    buy-side only. Verifies the exact arithmetic for a known
    entry/exit/quantity, not just that it returns a positive number."""
    entry, exit_price, quantity = 100.0, 100.0, 65
    buy_turnover = sell_turnover = entry * quantity  # 6500.0

    brokerage = 40.0
    stt = 0.0015 * sell_turnover
    exchange = 0.0003553 * (buy_turnover + sell_turnover)
    sebi = (buy_turnover + sell_turnover) * (10.0 / 1e7)
    gst = 0.18 * (brokerage + exchange + sebi)
    stamp = 0.00003 * buy_turnover
    expected = brokerage + stt + exchange + sebi + gst + stamp

    assert real_transaction_costs(entry, exit_price, quantity) == expected


def test_real_slippage_matches_paper_brokers_own_real_formula():
    """execution/paper_broker.py::PaperBroker's real adverse-fill formula
    is adverse = slippage_ticks * tick_size, applied on entry and exit --
    real_slippage must reuse this exactly, not a new estimate."""
    settings = Settings(entry_slippage_ticks=2, exit_slippage_ticks=3, tick_size=0.1)
    quantity = 65

    expected = (2 * 0.1 + 3 * 0.1) * quantity
    assert real_slippage(settings, quantity) == expected
