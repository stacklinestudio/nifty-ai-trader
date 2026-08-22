from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from config import Settings
from execution.kite_executor import KiteExecutor
from execution.order_manager import exit_reason
from execution.paper_broker import PaperBroker
from storage.models import Trade

IST = ZoneInfo("Asia/Kolkata")


def test_paper_order_lifecycle_and_slippage():
    broker = PaperBroker()
    order = broker.place_order("NIFTYCE", "BUY", 25, 10, datetime.now(IST), "test")
    assert (
        order["status"] == "FILLED"
        and order["fill_price"] > 10
        and broker.get_positions()[0]["quantity"] == 25
    )
    assert broker.get_order(order["order_id"])["order_id"] == order["order_id"]


def test_duplicate_order_prevented():
    broker = PaperBroker()
    now = datetime.now(IST)
    broker.place_order("NIFTYCE", "BUY", 25, 10, now, "test")
    with pytest.raises(ValueError, match="Duplicate"):
        broker.place_order("NIFTYCE", "BUY", 25, 10, now, "test")


def test_live_executor_is_gated():
    with pytest.raises(PermissionError):
        KiteExecutor(Settings(), object()).place_order()


def test_forced_square_off():
    trade = Trade("NIFTYCE", "BUY", 25, 10, 8, 14, datetime.now(IST))
    timestamp = datetime.now(IST).replace(hour=15, minute=15)
    assert exit_reason(trade, 10, timestamp, time(15, 15)) == "FORCED_SQUARE_OFF"
