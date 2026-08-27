from __future__ import annotations

from datetime import date, datetime

from config import IST
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from intelligence.oi_buildup import detect_buildup


def quote(strike: float, option_type: str, oi: int) -> OptionQuote:
    instrument = OptionInstrument("NIFTY24CE", strike, date(2026, 9, 3), option_type, 75)
    return OptionQuote(instrument, 100, datetime.now(IST), open_interest=oi)


def test_no_prior_snapshot_is_unavailable_not_fabricated():
    result = detect_buildup([quote(22000, "CE", 1000)], [])
    assert result.bias == "UNAVAILABLE"


def test_call_side_buildup_detected():
    previous = [quote(22000, "CE", 1000), quote(22000, "PE", 1000)]
    current = [quote(22000, "CE", 5000), quote(22000, "PE", 1100)]
    result = detect_buildup(current, previous)
    assert result.bias == "CALL_BUILDUP"
    assert result.call_oi_change == 4000
    assert result.put_oi_change == 100


def test_put_side_buildup_detected():
    previous = [quote(22000, "CE", 1000), quote(22000, "PE", 1000)]
    current = [quote(22000, "CE", 1050), quote(22000, "PE", 6000)]
    result = detect_buildup(current, previous)
    assert result.bias == "PUT_BUILDUP"


def test_roughly_balanced_change_is_not_called_a_bias():
    # call change 300, put change 250: ratio 1.2 < the 1.5 significance bar.
    previous = [quote(22000, "CE", 1000), quote(22000, "PE", 1000)]
    current = [quote(22000, "CE", 1300), quote(22000, "PE", 1250)]
    result = detect_buildup(current, previous)
    assert result.bias == "BALANCED"


def test_aggregates_across_multiple_strikes():
    previous = [
        quote(21900, "CE", 500),
        quote(22000, "CE", 1000),
        quote(22100, "CE", 500),
        quote(22000, "PE", 1000),
    ]
    current = [
        quote(21900, "CE", 2500),
        quote(22000, "CE", 3000),
        quote(22100, "CE", 2500),
        quote(22000, "PE", 1050),
    ]
    result = detect_buildup(current, previous)
    assert result.call_oi_change == (2000 + 2000 + 2000)
    assert result.bias == "CALL_BUILDUP"


def test_net_unwinding_on_both_sides_is_balanced_not_a_bias():
    previous = [quote(22000, "CE", 5000), quote(22000, "PE", 5000)]
    current = [quote(22000, "CE", 3000), quote(22000, "PE", 3000)]
    result = detect_buildup(current, previous)
    assert result.bias == "BALANCED"
    assert result.call_oi_change < 0 and result.put_oi_change < 0


def test_options_agent_reports_buildup_without_it_changing_selection():
    from agents.contracts import TradeCandidate
    from agents.trading_agents import OptionsAgent

    candidate = TradeCandidate(
        "CALL", "OPENING_STRUCTURE", "NIFTY", 88.0, ("evidence",), (), (0.0, 0.0), (0.0, 0.0),
        (0.0, 0.0),
    )
    current = [
        OptionQuote(
            OptionInstrument("NIFTY24CE", 22000, date(2026, 9, 3), "CE", 75),
            100,
            datetime.now(IST),
            99.5,
            100.5,
            1000,
            open_interest=5000,
        )
    ]
    previous = [quote(22000, "CE", 1000)]

    review = OptionsAgent().run(
        {
            "candidate": candidate,
            "option_quotes": current,
            "previous_option_quotes": previous,
            "spot": 22000,
            "max_position_value": 7500,
        }
    )

    assert review.data["oi_buildup_bias"] == "CALL_BUILDUP"
    assert len(review.data["ranked"]) == 1  # selection still happened normally
