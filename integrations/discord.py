from __future__ import annotations

import json
import time
from collections.abc import Callable

import requests

from config import Settings
from events.contracts import Event, EventType
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

# Which of the 6 Discord channels each event type belongs to. Some
# scenarios the categories are meant to cover (e.g. "connection loss",
# "daily summary", "promotion decisions") don't have their own EventType
# yet -- they currently surface via SYSTEM_ERROR/LEARNING_CREATED, which is
# the correct category regardless, just without finer sub-typing. Anything
# not listed here falls back to "system" rather than being silently dropped.
CATEGORY_BY_EVENT_TYPE: dict[EventType, str] = {
    EventType.SYSTEM_STARTED: "system",
    EventType.AUTH_READY: "system",
    EventType.MARKET_PREP_STARTED: "market_research",
    EventType.MARKET_RESEARCH_COMPLETE: "market_research",
    EventType.SIGNAL_CREATED: "signals",
    EventType.TRADE_PROPOSED: "trades",
    EventType.TRADE_VALIDATED: "risk",
    EventType.RISK_APPROVED: "risk",
    EventType.RISK_REJECTED: "risk",
    EventType.PAPER_ORDER_SENT: "trades",
    EventType.PAPER_FILL: "trades",
    EventType.STOP_LOSS: "trades",
    EventType.TAKE_PROFIT: "trades",
    EventType.THESIS_INVALIDATED: "trades",
    EventType.FORCED_EXIT: "trades",
    EventType.TRADE_COMPLETED: "trades",
    EventType.LEARNING_CREATED: "daily_report",
    EventType.SYSTEM_ERROR: "system",
}

CATEGORIES = (
    "market_research",
    "signals",
    "trades",
    "risk",
    "system",
    "daily_report",
)


def _links_line(event: Event) -> str:
    """Final Brief Part B: PAPER_FILL's output_summary already carries the
    real dashboard/Kite chart URLs as plain dict values (readable, but
    buried inside the JSON blob above); this appends one clearly labeled
    extra line so a real person opening the notification doesn't have to
    parse JSON to find them. A no-op string for every other event type,
    or when neither real link is present on this one."""
    if event.event_type != EventType.PAPER_FILL:
        return ""
    dashboard_link = event.output_summary.get("live_status_url")
    kite_link = event.output_summary.get("kite_chart_url")
    parts = []
    if dashboard_link:
        parts.append(f"Our dashboard: {dashboard_link}")
    if kite_link:
        parts.append(f"Kite chart: {kite_link}")
    return ("\n" + " | ".join(parts)) if parts else ""


class DiscordNotifier:
    """Routes to one of 6 category webhooks when configured
    (webhooks_by_category), falling back to a single webhook_url per
    category that isn't configured -- and silently no-ops (returns False,
    never raises) for a category with no webhook at all, same principle as
    every other notification path in this codebase: a missing channel
    never blocks trading.
    """

    def __init__(
        self,
        webhook_url: str = "",
        transport: Callable[..., object] | None = None,
        webhooks_by_category: dict[str, str] | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.transport = transport or requests.post
        self.webhooks_by_category = webhooks_by_category or {}

    def _resolve_url(self, category: str | None) -> str:
        if category:
            configured = self.webhooks_by_category.get(category)
            if configured:
                return configured
        return self.webhook_url

    def send_message(self, severity: str, message: str, category: str | None = None) -> bool:
        return self.send_embed("NIFTY AI Trader", message, severity, category)

    def send_embed(
        self, title: str, description: str, severity: str = "INFO", category: str | None = None
    ) -> bool:
        url = self._resolve_url(category)
        if not url:
            return False
        for attempt in range(3):
            try:
                response = self.transport(
                    url,
                    json={
                        "embeds": [
                            {"title": f"[{severity.upper()}] {title}", "description": description}
                        ]
                    },
                    timeout=5,
                )
                if getattr(response, "ok", False):
                    return True
            except OSError as exc:
                # Non-fatal by design: notification failures never block trading.
                logger.warning(
                    "discord_send_failed category=%s attempt=%d error=%s",
                    category,
                    attempt + 1,
                    exc,
                )
            time.sleep(0.1 * (2**attempt))
        return False

    def send_event(self, event: Event) -> bool:
        """Routes an audit-trail Event to its category's channel. Formatting
        only; the event has already been persisted by the audit sink
        regardless of whether this send succeeds."""
        category = CATEGORY_BY_EVENT_TYPE.get(event.event_type, "system")
        title = event.event_type.value.replace("_", " ").title()
        description = json.dumps(event.output_summary, default=str) or "(no details)"
        description += _links_line(event)
        return self.send_embed(title, description, "INFO", category)


def webhooks_by_category_from_settings(settings: Settings) -> dict[str, str]:
    """Builds the category->URL map from Settings' 6 optional
    DISCORD_WEBHOOK_* fields. A blank field means that category falls back
    to DISCORD_WEBHOOK_URL (handled by DiscordNotifier._resolve_url), or is
    skipped entirely if that isn't set either."""
    return {
        "market_research": settings.discord_webhook_market_research,
        "signals": settings.discord_webhook_signals,
        "trades": settings.discord_webhook_trades,
        "risk": settings.discord_webhook_risk,
        "system": settings.discord_webhook_system,
        "daily_report": settings.discord_webhook_daily_report,
    }
