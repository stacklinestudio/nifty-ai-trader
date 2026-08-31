from __future__ import annotations

from datetime import datetime

from config import IST, Settings
from monitoring.health import system_health


def component(settings: Settings, name: str):
    return next(c for c in system_health(settings, True, None, datetime.now(IST)) if c.name == name)


def test_discord_degraded_when_nothing_configured():
    result = component(Settings(), "discord")
    assert result.status == "DEGRADED"


def test_discord_healthy_via_legacy_single_webhook_only():
    settings = Settings(discord_webhook_url="https://discord.test/legacy")
    result = component(settings, "discord")
    assert result.status == "HEALTHY"
    assert "default webhook only" in result.detail


def test_discord_healthy_via_category_webhooks_without_legacy_url():
    # This is the exact scenario the previous check got wrong: a setup using
    # only the 6 per-category channels, with discord_webhook_url left blank.
    settings = Settings(
        discord_webhook_market_research="https://discord.test/mr",
        discord_webhook_signals="https://discord.test/sig",
        discord_webhook_trades="https://discord.test/tr",
    )
    result = component(settings, "discord")
    assert result.status == "HEALTHY"
    assert result.detail == "3/6 category channels configured"


def test_discord_reports_zero_of_six_distinctly_from_fully_unconfigured():
    settings = Settings(discord_webhook_url="https://discord.test/legacy")
    result = component(settings, "discord")
    # Legacy-only configuration should not be mislabeled as N/6 category
    # channels when zero of the six are actually set.
    assert "0/6" not in result.detail
