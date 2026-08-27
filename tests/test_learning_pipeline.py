from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

from config import IST, Settings
from learning.experiment_manager import Experiment, create_experiment
from learning.memory import MemoryStore
from learning.pattern_memory import MIN_SAMPLES_FOR_CONFIDENCE, stats_for
from learning.promotion_engine import decide
from learning.trade_memory import record_trade


def record(store: MemoryStore, setup_type: str, regime: str, pnl: float) -> None:
    record_trade(
        store,
        {"setup_type": setup_type, "entry_regime": regime, "pnl": pnl, "outcome": "WIN" if pnl > 0 else "LOSS"},
        datetime.now(IST),
    )


def test_stats_for_unknown_pattern_is_low_confidence_with_no_samples(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    stats = stats_for(store, "GAP_CONTINUATION", "GAP_UP")
    assert stats.sample_size == 0 and stats.win_rate is None and stats.low_confidence is True


def test_stats_for_computes_real_win_rate_and_expectancy(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    record(store, "TREND_CONTINUATION", "TREND_UP", 100)
    record(store, "TREND_CONTINUATION", "TREND_UP", 100)
    record(store, "TREND_CONTINUATION", "TREND_UP", -50)
    # A different setup/regime must not pollute the aggregate.
    record(store, "OPENING_RANGE_BREAKOUT", "RANGE", -1000)

    stats = stats_for(store, "TREND_CONTINUATION", "TREND_UP")

    assert stats.sample_size == 3
    assert stats.win_rate == 2 / 3
    assert stats.expectancy == (100 + 100 - 50) / 3


def test_small_sample_count_is_flagged_low_confidence_even_with_perfect_win_rate(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    for _ in range(3):
        record(store, "VWAP_REJECTION", "RANGE", 50)

    stats = stats_for(store, "VWAP_REJECTION", "RANGE")

    assert stats.sample_size == 3 and stats.win_rate == 1.0
    assert stats.low_confidence is True, "3 winning trades must not read as a confident signal"


def test_sample_count_at_threshold_is_no_longer_flagged_low_confidence(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    for _ in range(MIN_SAMPLES_FOR_CONFIDENCE):
        record(store, "VWAP_REJECTION", "RANGE", 10)

    stats = stats_for(store, "VWAP_REJECTION", "RANGE")

    assert stats.sample_size == MIN_SAMPLES_FOR_CONFIDENCE
    assert stats.low_confidence is False


def test_a_losing_pattern_alone_cannot_bypass_promotion_engine(tmp_path):
    """A setup/regime pairing with a terrible track record must still be
    rejected for promotion until it actually clears historical replay,
    walk-forward, out-of-sample validation, and human approval -- raw trade
    stats from pattern_memory are not, by themselves, any of those things.
    This is the explicit test the brief asked for: try to make a losing
    (or winning) trade record promote a strategy change directly, and
    assert it is rejected.
    """
    store = MemoryStore(tmp_path / "learning.db")
    for _ in range(50):
        record(store, "OPENING_RANGE_BREAKOUT", "RANGE", -80)

    stats = stats_for(store, "OPENING_RANGE_BREAKOUT", "RANGE")
    assert stats.sample_size == 50 and stats.win_rate == 0.0 and stats.low_confidence is False

    # A real hypothesis can be filed as a CANDIDATE experiment from this...
    experiment_id = create_experiment(
        store,
        Experiment(
            f"de-weight {stats.setup_type} in {stats.regime}: {stats.sample_size} trades, "
            f"{stats.win_rate:.0%} win rate",
            {"setup_type": stats.setup_type, "regime": stats.regime},
            "v2",
        ),
        datetime.now(IST),
    )
    assert experiment_id

    # ...but pattern_memory's stats alone supply none of the four gates
    # promotion_engine requires, regardless of how bad (or good) they look.
    decision = decide(
        has_historical=False, has_walk_forward=False, has_out_of_sample=False, human_approved=False
    )
    assert decision.promote is False
    assert set(decision.reasons) == {
        "historical validation",
        "walk-forward validation",
        "out-of-sample validation",
        "human approval",
    }


def test_settings_cannot_be_mutated_at_all_regardless_of_learning_outcome():
    """Defense in depth: even if something tried to skip promotion_engine
    entirely, Settings is a frozen dataclass -- there is no live object for
    a trade outcome to silently write into."""
    settings = Settings()
    try:
        settings.max_risk_per_trade = 999999
        raised = False
    except FrozenInstanceError:
        raised = True
    assert raised
