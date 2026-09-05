"""Brief 15: EV diagnosis, not a decision. Proves the real decomposition
(Part A) is a real breakdown of the same number compute_ev() already
produces -- never a second, potentially-divergent calculation -- and
that the AvgWin sensitivity sweep (Part B) changes EV exactly as the
arithmetic predicts, monotonically.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config import IST, Settings
from learning.memory import MemoryStore
from research.counterfactual import CounterfactualRecord
from research.expected_value import (
    AVG_LOSS_R,
    MIN_COUNTERFACTUAL_SAMPLES,
    REWARD_RISK_RATIO,
    compute_ev,
    recompute_ev,
)


def _counterfactual_record(setup_type: str, regime: str, profitable: bool) -> CounterfactualRecord:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    return CounterfactualRecord(
        timestamp=now, setup_type=setup_type, direction="CALL", rejection_reason="confidence_gated",
        entry=100.0, stop=95.0, target=110.0,
        exit_price=110.0 if profitable else 95.0,
        exit_reason="TAKE_PROFIT" if profitable else "STOP_LOSS",
        exit_time=now + timedelta(minutes=10), profitable=profitable, regime=regime,
    )


def _known_records(n: int, win_rate: float) -> list[CounterfactualRecord]:
    n_wins = round(n * win_rate)
    records = [_counterfactual_record("TREND_CONTINUATION", "TREND_UP", True) for _ in range(n_wins)]
    records += [_counterfactual_record("TREND_CONTINUATION", "TREND_UP", False) for _ in range(n - n_wins)]
    return records


def test_decomposition_sums_exactly_to_the_same_real_ev_compute_ev_produces(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")
    records = _known_records(MIN_COUNTERFACTUAL_SAMPLES, win_rate=0.4)

    estimate = compute_ev("TREND_CONTINUATION", "TREND_UP", settings, memory, records)
    decomposition = estimate.decomposition()

    assert decomposition is not None
    # Real, exact equality -- not approx -- since both come from the same
    # real floats with no independent rounding introduced.
    assert decomposition.total == estimate.ev_r
    assert decomposition.win_contribution == pytest.approx(0.4 * REWARD_RISK_RATIO)
    assert decomposition.loss_contribution == pytest.approx(0.6 * AVG_LOSS_R)
    assert decomposition.costs == estimate.costs_r
    assert decomposition.slippage == estimate.slippage_r


def test_decomposition_is_none_for_insufficient_data(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")

    estimate = compute_ev("VWAP_REJECTION", "UNCERTAIN", settings, memory, [])

    assert estimate.decomposition() is None


def test_dominant_driver_correctly_identifies_the_largest_real_drag(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")
    # A very low win rate -- loss_contribution should dominate over the
    # comparatively tiny real costs/slippage (~0.12R combined).
    records = _known_records(MIN_COUNTERFACTUAL_SAMPLES, win_rate=0.1)

    estimate = compute_ev("TREND_CONTINUATION", "TREND_UP", settings, memory, records)
    decomposition = estimate.decomposition()

    assert decomposition.dominant_driver() == "loss_contribution"
    assert decomposition.loss_contribution > decomposition.costs
    assert decomposition.loss_contribution > decomposition.slippage


def test_recompute_ev_sensitivity_sweep_is_monotonic_and_arithmetically_exact():
    """Part B: EV must increase by exactly win_rate x delta for every
    unit increase in avg_win_r -- a real, exact, monotonic relationship,
    not just "the number went up."""
    win_rate = 0.35
    avg_loss_r = AVG_LOSS_R
    costs_r = 0.110
    slippage_r = 0.011

    avg_win_values = [1.0, 1.5, 2.0, 2.5]
    evs = [recompute_ev(win_rate, w, avg_loss_r, costs_r, slippage_r) for w in avg_win_values]

    # Strictly increasing -- a real, monotonic relationship.
    assert evs == sorted(evs)
    assert len(set(evs)) == len(evs)  # every step actually changed something real

    # Exact arithmetic: each step of +0.5 in avg_win_r must raise EV by
    # exactly win_rate * 0.5.
    for i in range(len(avg_win_values) - 1):
        delta_avg_win = avg_win_values[i + 1] - avg_win_values[i]
        expected_delta_ev = win_rate * delta_avg_win
        assert evs[i + 1] - evs[i] == pytest.approx(expected_delta_ev)

    # Independent, direct check against the raw formula for one point.
    assert recompute_ev(0.35, 2.0, 1.0, 0.110, 0.011) == pytest.approx(
        0.35 * 2.0 - 0.65 * 1.0 - 0.110 - 0.011
    )


def test_recompute_ev_matches_compute_ev_for_the_same_real_inputs(tmp_path):
    """The sweep must use the exact same formula compute_ev() itself
    calls for tier 2 -- proven by reproducing a real compute_ev() result
    via recompute_ev() using its own reported win_rate/costs/slippage."""
    settings = Settings(database_path=tmp_path / "paper.db")
    memory = MemoryStore(tmp_path / "paper.db")
    records = _known_records(MIN_COUNTERFACTUAL_SAMPLES, win_rate=0.45)

    estimate = compute_ev("TREND_CONTINUATION", "TREND_UP", settings, memory, records)
    reproduced = recompute_ev(
        estimate.win_rate, estimate.avg_win_r, estimate.avg_loss_r, estimate.costs_r, estimate.slippage_r
    )

    assert reproduced == estimate.ev_r
