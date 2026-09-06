from __future__ import annotations

from datetime import datetime

from config import IST
from events.contracts import Event, EventType
from integrations.telegram import TelegramNotifier


def recording_transport(calls: list[tuple[str, dict]]):
    class Response:
        ok = True

    def transport(url: str, **kwargs) -> Response:
        calls.append((url, kwargs))
        return Response()

    return transport


def event(event_type: EventType, output: dict | None = None) -> Event:
    return Event(event_type, "test", datetime.now(IST), output_summary=output or {})


def test_paper_fill_notification_clearly_labels_both_real_links():
    calls: list[tuple[str, dict]] = []
    notifier = TelegramNotifier("token", "chat-1", transport=recording_transport(calls))

    notifier.send_event(
        event(
            EventType.PAPER_FILL,
            {
                "order_id": "o1",
                "live_status_url": "http://192.168.1.10:8765/live",
                "kite_chart_url": "https://kite.zerodha.com/chart/ext/tvc/NFO/NIFTY24CE/17512194",
            },
        )
    )

    text = calls[0][1]["json"]["text"]
    assert "Our dashboard: http://192.168.1.10:8765/live" in text
    assert "Kite chart: https://kite.zerodha.com/chart/ext/tvc/NFO/NIFTY24CE/17512194" in text


def test_non_paper_fill_events_never_get_a_links_line():
    calls: list[tuple[str, dict]] = []
    notifier = TelegramNotifier("token", "chat-1", transport=recording_transport(calls))

    notifier.send_event(event(EventType.RISK_APPROVED, {"reasons": []}))

    text = calls[0][1]["json"]["text"]
    assert "Our dashboard" not in text
    assert "Kite chart" not in text
