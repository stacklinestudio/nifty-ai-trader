from datetime import datetime, timedelta

from agents.contracts import TradeCandidate
from agents.trading_agents import TradeBuilderAgent
from config import IST
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from risk.confidence_scaling import scale_quantity
from risk.risk_manager import RiskManager
from strategy.option_selector import SelectedOption


def candidate(confidence: float) -> TradeCandidate:
    return TradeCandidate(
        "CALL", "OPENING_STRUCTURE", "NIFTY", confidence, ("evidence",), (), (10.0, 10.5),
        (8.0, 8.5), (13.0, 14.0),
    )


def selected_option() -> SelectedOption:
    instrument = OptionInstrument(
        "NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 25
    )
    quote = OptionQuote(instrument, 10, datetime.now(IST), 9.75, 10.25, 1000)
    return SelectedOption(quote, 200, 1.0)


def test_high_confidence_gets_full_approved_size():
    assert scale_quantity(200, 95.0, 25) == 200
    assert scale_quantity(200, 99.0, 25) == 200


def test_low_confidence_sizes_down_to_one_lot_not_zero():
    assert scale_quantity(200, 75.0, 25) == 25
    assert scale_quantity(200, 40.0, 25) == 25


def test_mid_confidence_scales_between_one_lot_and_max():
    scaled = scale_quantity(200, 85.0, 25)  # midpoint of 75-95 range
    assert 25 < scaled < 200
    assert scaled % 25 == 0


def test_never_exceeds_the_already_approved_max():
    for confidence in (0, 50, 75, 85, 95, 100):
        assert scale_quantity(200, confidence, 25) <= 200


def test_returns_zero_when_less_than_one_lot_was_ever_affordable():
    # max_quantity below a single lot means position_size() already said no
    # trade -- confidence scaling cannot manufacture a lot out of nothing.
    assert scale_quantity(10, 99.0, 25) == 0


def test_single_lot_max_quantity_is_unaffected_by_confidence():
    assert scale_quantity(25, 40.0, 25) == 25
    assert scale_quantity(25, 99.0, 25) == 25


def test_trade_builder_gives_high_confidence_candidate_full_approved_size():
    builder = TradeBuilderAgent(RiskManager(200, 5000), low_confidence=75.0, high_confidence=95.0)
    thesis = builder.run(
        {"candidate": candidate(97.0), "selected_option": selected_option(), "option_atr": 1}
    ).data["thesis"]
    assert thesis.quantity == 200


def test_trade_builder_scales_down_lower_confidence_candidate_without_zeroing_it():
    builder = TradeBuilderAgent(RiskManager(200, 5000), low_confidence=75.0, high_confidence=95.0)
    thesis = builder.run(
        {"candidate": candidate(75.0), "selected_option": selected_option(), "option_atr": 1}
    ).data["thesis"]
    assert 0 < thesis.quantity < 200
    assert thesis.quantity % 25 == 0


def test_trade_builder_never_exceeds_max_risk_regardless_of_confidence():
    risk = RiskManager(200, 5000)
    builder = TradeBuilderAgent(risk, low_confidence=75.0, high_confidence=95.0)
    for confidence in (10.0, 50.0, 75.0, 85.0, 95.0, 100.0):
        thesis = builder.run(
            {"candidate": candidate(confidence), "selected_option": selected_option(), "option_atr": 1}
        ).data["thesis"]
        assert thesis.estimated_risk <= 200
