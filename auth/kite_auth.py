"""Official Kite Connect login flow; never automates browser credentials."""

from __future__ import annotations

from config import Settings


class KiteAuthenticator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def login_url(self) -> str:
        if not self.settings.kite_api_key:
            raise RuntimeError("KITE_API_KEY is required")
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise RuntimeError("Install kiteconnect to use broker authentication") from exc
        return KiteConnect(api_key=self.settings.kite_api_key).login_url()

    def exchange_request_token(self, request_token: str) -> str:
        if not (self.settings.kite_api_key and self.settings.kite_api_secret):
            raise RuntimeError("KITE_API_KEY and KITE_API_SECRET are required")
        from kiteconnect import KiteConnect

        session = KiteConnect(api_key=self.settings.kite_api_key).generate_session(
            request_token, api_secret=self.settings.kite_api_secret
        )
        return str(session["access_token"])
