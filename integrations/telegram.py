from __future__ import annotations

import time
from collections.abc import Callable

import requests


class TelegramNotifier:
    def __init__(
        self, token: str = "", chat_id: str = "", transport: Callable[..., object] | None = None
    ) -> None:
        self.token, self.chat_id, self.transport = token, chat_id, transport or requests.post

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
            except Exception:  # noqa: BLE001 - notification failures are deliberately isolated.
                response = None
            time.sleep(0.1 * (2**attempt))
        return False
