from __future__ import annotations

import time
from collections.abc import Callable

import requests


class DiscordNotifier:
    def __init__(
        self, webhook_url: str = "", transport: Callable[..., object] | None = None
    ) -> None:
        self.webhook_url, self.transport = webhook_url, transport or requests.post

    def send_message(self, severity: str, message: str) -> bool:
        return self.send_embed("NIFTY AI Trader", message, severity)

    def send_embed(self, title: str, description: str, severity: str = "INFO") -> bool:
        if not self.webhook_url:
            return False
        for attempt in range(3):
            try:
                response = self.transport(
                    self.webhook_url,
                    json={
                        "embeds": [
                            {"title": f"[{severity.upper()}] {title}", "description": description}
                        ]
                    },
                    timeout=5,
                )
                if getattr(response, "ok", False):
                    return True
            except Exception:  # noqa: BLE001 - notification failures are deliberately isolated.
                response = None
            time.sleep(0.1 * (2**attempt))
        return False
