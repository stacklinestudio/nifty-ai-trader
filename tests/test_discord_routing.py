from __future__ import annotations

from datetime import datetime

from config import IST, Settings
from events.contracts import Event, EventType
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings


def recording_transport(calls: list[tuple[str, dict]]):
    class Response:
        ok = True

    def transport(url: str, **kwargs) -> Response:
        calls.append((url, kwargs))
        return Response()

    return transport


def event(event_type: EventType, output: dict | None = None) -> Event:
    return Event(event_type, "test", datetime.now(IST), output_summary=output or {})


def test_each_category_resolves_to_its_own_configured_webhook():
    calls: list[tuple[str, dict]] = []
    webhooks = {
        "market_research": "https://discord.test/market_research",
        "signals": "https://discord.test/signals",
        "trades": "https://discord.test/trades",
        "risk": "https://discord.test/risk",
        "system": "https://discord.test/system",
        "daily_report": "https://discord.test/daily_report",
    }
    notifier = DiscordNotifier(transport=recording_transport(calls), webhooks_by_category=webhooks)

    cases = [
        (EventType.MARKET_RESEARCH_COMPLETE, "market_research"),
        (EventType.SIGNAL_CREATED, "signals"),
        (EventType.PAPER_FILL, "trades"),
        (EventType.TAKE_PROFIT, "trades"),
        (EventType.RISK_APPROVED, "risk"),
        (EventType.TRADE_VALIDATED, "risk"),
        (EventType.SYSTEM_ERROR, "system"),
        (EventType.LEARNING_CREATED, "daily_report"),
    ]
    for event_type, expected_category in cases:
        calls.clear()
        assert notifier.send_event(event(event_type))
        assert calls[0][0] == webhooks[expected_category], event_type


def test_unconfigured_category_falls_back_to_default_webhook():
    calls: list[tuple[str, dict]] = []
    notifier = DiscordNotifier(
        webhook_url="https://discord.test/default",
        transport=recording_transport(calls),
        webhooks_by_category={"trades": "https://discord.test/trades"},  # "risk" left unset
    )

    assert notifier.send_event(event(EventType.RISK_APPROVED))
    assert calls[0][0] == "https://discord.test/default"


def test_unconfigured_category_with_no_default_is_silently_skipped_not_an_error():
    calls: list[tuple[str, dict]] = []
    notifier = DiscordNotifier(transport=recording_transport(calls))  # nothing configured at all

    result = notifier.send_event(event(EventType.RISK_APPROVED))

    assert result is False
    assert calls == []


def test_backward_compatible_single_webhook_constructor_still_works():
    calls: list[tuple[str, dict]] = []
    notifier = DiscordNotifier("https://discord.test/legacy", recording_transport(calls))

    assert notifier.send_embed("Trade", "approved", "TRADE")
    assert calls[0][0] == "https://discord.test/legacy"


def test_failure_on_one_channel_does_not_block_notifications_to_others():
    calls: list[tuple[str, dict]] = []

    class OkResponse:
        ok = True

    def flaky_transport(url: str, **kwargs):
        if "risk" in url:
            raise OSError("simulated outage on the risk channel")
        calls.append((url, kwargs))
        return OkResponse()

    notifier = DiscordNotifier(
        transport=flaky_transport,
        webhooks_by_category={
            "risk": "https://discord.test/risk",
            "trades": "https://discord.test/trades",
        },
    )

    risk_result = notifier.send_event(event(EventType.RISK_REJECTED))
    trades_result = notifier.send_event(event(EventType.PAPER_FILL))

    assert risk_result is False  # the outage channel failed...
    assert trades_result is True  # ...but the next call to a different channel still worked
    assert calls and calls[0][0] == "https://discord.test/trades"


def test_webhooks_by_category_from_settings_reads_all_six_fields():
    # Settings' os.getenv(...) defaults are evaluated once at class
    # definition (module import) time, not per-instance -- so this
    # constructs Settings directly with explicit field values rather than
    # monkeypatching env vars, which would have no effect on an
    # already-imported class's field defaults.
    settings = Settings(
        discord_webhook_market_research="https://discord.test/mr",
        discord_webhook_signals="https://discord.test/sig",
        discord_webhook_trades="https://discord.test/tr",
        discord_webhook_risk="https://discord.test/risk",
        discord_webhook_system="https://discord.test/sys",
        discord_webhook_daily_report="https://discord.test/daily",
    )

    webhooks = webhooks_by_category_from_settings(settings)

    assert webhooks == {
        "market_research": "https://discord.test/mr",
        "signals": "https://discord.test/sig",
        "trades": "https://discord.test/tr",
        "risk": "https://discord.test/risk",
        "system": "https://discord.test/sys",
        "daily_report": "https://discord.test/daily",
    }
