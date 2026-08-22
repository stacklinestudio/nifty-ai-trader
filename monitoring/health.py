from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from config import Settings
from data.websocket import WebsocketHealth


@dataclass(frozen=True)
class HealthReport:
    safe_for_new_trades: bool
    reasons: list[str]


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
