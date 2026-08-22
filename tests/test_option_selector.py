from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from strategy.option_selector import OptionSelector


def quote(kind="CE", spread=0.5, volume=1000):
    item = OptionInstrument(
        "NIFTY",
        22000,
        datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=3),
        kind,
        25,
    )
    return OptionQuote(
        item, 10, datetime.now(ZoneInfo("Asia/Kolkata")), 9.75, 9.75 + spread, volume
    )


def test_option_selector_respects_position_value():
    selected = OptionSelector().select([quote()], "CALL", 22000, 1000)
    assert selected and selected.quantity == 100


def test_option_selector_rejects_wide_spread():
    assert OptionSelector().select([quote(spread=5)], "CALL", 22000, 1000) is None
