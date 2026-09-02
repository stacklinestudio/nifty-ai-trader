from __future__ import annotations

from datetime import datetime
from uuid import uuid4


class PaperBroker:
    """Deterministic, non-live broker with adverse fill slippage and duplicate protection."""

    def __init__(
        self,
        tick_size: float = 0.05,
        entry_slippage_ticks: int = 1,
        exit_slippage_ticks: int = 1,
        cost_rate: float = 0.0005,
    ) -> None:
        self.tick_size = tick_size
        self.entry_slippage_ticks = entry_slippage_ticks
        self.exit_slippage_ticks = exit_slippage_ticks
        self.cost_rate = cost_rate
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, int] = {}
        self._fingerprints: set[tuple] = set()

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        requested_price: float,
        timestamp: datetime,
        reason: str,
    ) -> dict:
        if quantity <= 0 or requested_price <= 0:
            raise ValueError("Invalid paper order")
        # Brief 6: fingerprinting on timestamp.date() (not the full
        # timestamp) meant a genuine SECOND same-symbol/side/quantity
        # trade later the same day -- exactly what periodic re-scanning
        # (execution/scheduler.py) and Brief 3's max_trades_per_day>1 both
        # intend to allow -- was silently rejected as a false "duplicate."
        # The full timestamp still catches a real accidental double-
        # submission at the same instant while correctly allowing two
        # distinct real orders placed at different times the same day.
        key = (symbol, side, quantity, timestamp)
        if key in self._fingerprints:
            raise ValueError("Duplicate order prevented")
        self._fingerprints.add(key)
        adverse = (
            self.entry_slippage_ticks * self.tick_size
            if side == "BUY"
            else -self.entry_slippage_ticks * self.tick_size
        )
        fill = round(requested_price + adverse, 2)
        oid = f"PAPER-{uuid4().hex[:12]}"
        order = {
            "order_id": oid,
            "timestamp": timestamp,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "requested_price": requested_price,
            "fill_price": fill,
            "status": "FILLED",
            "slippage": abs(fill - requested_price),
            "reason": reason,
            "estimated_costs": fill * quantity * self.cost_rate,
        }
        self.orders[oid] = order
        self.positions[symbol] = self.positions.get(symbol, 0) + (
            quantity if side == "BUY" else -quantity
        )
        return order

    def modify_order(self, order_id: str, price: float) -> dict:
        order = self.get_order(order_id)
        order["requested_price"] = price
        return order

    def cancel_order(self, order_id: str) -> dict:
        order = self.get_order(order_id)
        if order["status"] == "FILLED":
            raise ValueError("Filled paper order cannot be cancelled")
        order["status"] = "CANCELLED"
        return order

    def get_order(self, order_id: str) -> dict:
        if order_id not in self.orders:
            raise KeyError(order_id)
        return self.orders[order_id]

    def get_positions(self) -> list[dict]:
        return [{"symbol": s, "quantity": q} for s, q in self.positions.items() if q]

    def get_ltp(self, symbol: str) -> float:
        raise RuntimeError("Paper broker requires a market-data provider for LTP")
