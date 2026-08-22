from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from data.market_data import Quote, validate_quote
from data.websocket import WebsocketHealth

IST = ZoneInfo("Asia/Kolkata")


def test_stale_quote_rejected():
    now = datetime.now(IST)
    with pytest.raises(ValueError, match="Stale"):
        validate_quote(Quote("NIFTY", 1, now - timedelta(minutes=2), "test"), now, 60)


def test_websocket_recovery_and_stale_state():
    ws = WebsocketHealth()
    now = datetime.now(IST)
    ws.on_connect()
    ws.on_tick(now)
    assert ws.safe_for_trading(now, 60)
    ws.on_disconnect()
    assert not ws.safe_for_trading(now, 60) and ws.reconnects == 1
