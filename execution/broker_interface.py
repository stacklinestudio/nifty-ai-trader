from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Broker(Protocol):
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        requested_price: float,
        timestamp: datetime,
        reason: str,
    ) -> dict: ...
    def modify_order(self, order_id: str, price: float) -> dict: ...
    def cancel_order(self, order_id: str) -> dict: ...
    def get_order(self, order_id: str) -> dict: ...
    def get_positions(self) -> list[dict]: ...
