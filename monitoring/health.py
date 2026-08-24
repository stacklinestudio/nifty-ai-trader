from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config import Settings
from data.websocket import WebsocketHealth


@dataclass(frozen=True)
class HealthReport:
    safe_for_new_trades: bool
    reasons: list[str]


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: str
    detail: str = ""


def check_health(
    settings: Settings, websocket: WebsocketHealth | None, now: datetime
) -> HealthReport:
    reasons = []
    if settings.kill_switch:
        reasons.append("emergency kill switch is enabled")
    if settings.trading_mode != "paper" and not settings.live_execution_allowed:
        reasons.append("live execution safety guard is not satisfied")
    if websocket and not websocket.safe_for_trading(now, settings.stale_data_seconds):
        reasons.append("websocket unavailable or stale")
    return HealthReport(not reasons, reasons)


def system_health(
    settings: Settings,
    database_available: bool,
    websocket: WebsocketHealth | None,
    now: datetime,
) -> list[ComponentHealth]:
    """Component statuses are observability only; unavailable data stays a trade veto."""
    return [
        ComponentHealth("database", "HEALTHY" if database_available else "FAILED"),
        ComponentHealth(
            "market_data",
            "HEALTHY"
            if websocket and websocket.safe_for_trading(now, settings.stale_data_seconds)
            else "DEGRADED",
            "live feed unavailable or stale",
        ),
        ComponentHealth(
            "kite",
            "HEALTHY" if settings.kite_access_token else "DEGRADED",
            "access token not configured",
        ),
        ComponentHealth(
            "ai_provider", "DEGRADED" if settings.ai_provider == "unavailable" else "HEALTHY"
        ),
        ComponentHealth(
            "telegram",
            "HEALTHY" if settings.telegram_bot_token and settings.telegram_chat_id else "DEGRADED",
            "not configured",
        ),
        ComponentHealth(
            "discord", "HEALTHY" if settings.discord_webhook_url else "DEGRADED", "not configured"
        ),
        ComponentHealth(
            "obsidian", "HEALTHY" if settings.obsidian_vault_path else "DEGRADED", "not configured"
        ),
        ComponentHealth("scheduler", "HEALTHY", "external scheduler not required for manual CLI"),
    ]
