from __future__ import annotations

import json
import time
from collections.abc import Callable

import requests

from events.contracts import Event
from monitoring.logger import configure_logger

logger = configure_logger(__name__)


class TelegramNotifier:
    def __init__(
        self, token: str = "", chat_id: str = "", transport: Callable[..., object] | None = None
    ) -> None:
        self.token, self.chat_id, self.transport = token, chat_id, transport or requests.post

    def send_event(self, event: Event) -> bool:
        """Mirrors integrations/discord.py::DiscordNotifier.send_event's
        formatting -- the same real event stream, one chat (Telegram has
        no per-category routing concept the way Discord's 6 optional
        webhooks do; every event goes to the single configured chat).
        """
        title = event.event_type.value.replace("_", " ").title()
        description = json.dumps(event.output_summary, default=str) or "(no details)"
        return self.send_message("INFO", f"{title}\n{description}")

    def send_message(self, severity: str, message: str) -> bool:
        if not self.token or not self.chat_id:
            return False
        text = f"[{severity.upper()}] {message}"
        for attempt in range(3):
            try:
                response = self.transport(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text},
                    timeout=5,
                )
                if getattr(response, "ok", False):
                    return True
            except OSError as exc:
                # Non-fatal by design: notification failures never block trading.
                logger.warning("telegram_send_failed attempt=%d error=%s", attempt + 1, exc)
            time.sleep(0.1 * (2**attempt))
        return False
