"""Central, safety-first configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    trading_mode: str = os.getenv("TRADING_MODE", "paper").lower()
    live_trading_enabled: bool = _bool("LIVE_TRADING_ENABLED")
    kill_switch: bool = _bool("KILL_SWITCH")
    capital: float = float(os.getenv("CAPITAL", "10000"))
    # Raised from 200/5000 (2026-08-27, user decision): at lot size 75 and the
    # risk manager's 8%-of-premium stop floor, 200/5000 produced NO_TRADE for
    # any option above ~33 premium -- unusable for real NIFTY premiums
    # (commonly 50-300+). 600/7500 supports one lot at ~100 premium.
    max_risk_per_trade: float = float(os.getenv("MAX_RISK_PER_TRADE", "600"))
    # NOTE: max_daily_loss (400) is now LESS than a single trade's own risk
    # budget (600). This is harmless today only because max_trades_per_day=1
    # means DailyLimits.can_open() never blocks a second trade anyway -- it
    # is not a real daily ceiling under the new per-trade risk number. If
    # max_trades_per_day is ever raised, max_daily_loss must be revisited
    # first (explicitly, per the same rule as the numbers above), or the
    # first trade's own loss could already exceed what "daily loss limit"
    # implies.
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "400"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "1"))
    max_position_value: float = float(os.getenv("MAX_POSITION_VALUE", "7500"))
    signal_threshold: float = float(os.getenv("SIGNAL_THRESHOLD", "75"))
    forced_exit_time: time = field(
        default_factory=lambda: time.fromisoformat(os.getenv("FORCED_EXIT_TIME", "15:15"))
    )
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    stale_data_seconds: int = int(os.getenv("STALE_DATA_SECONDS", "60"))
    entry_slippage_ticks: int = int(os.getenv("ENTRY_SLIPPAGE_TICKS", "1"))
    exit_slippage_ticks: int = int(os.getenv("EXIT_SLIPPAGE_TICKS", "1"))
    tick_size: float = float(os.getenv("TICK_SIZE", "0.05"))
    simulated_latency_ms: int = int(os.getenv("SIMULATED_LATENCY_MS", "150"))
    database_path: Path = Path(os.getenv("DATABASE_PATH", "nifty_ai_trader.db"))
    kite_api_key: str = os.getenv("KITE_API_KEY", "")
    kite_api_secret: str = os.getenv("KITE_API_SECRET", "")
    kite_access_token: str = os.getenv("KITE_ACCESS_TOKEN", "")
    ai_provider: str = os.getenv("AI_PROVIDER", "unavailable")
    trail_percent: float = float(os.getenv("TRAIL_PERCENT", "0.15"))
    supervision_poll_seconds: float = float(os.getenv("SUPERVISION_POLL_SECONDS", "10"))
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    obsidian_vault_path: str = os.getenv("OBSIDIAN_VAULT_PATH", "")

    def validate(self) -> None:
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be paper or live")
        if self.trading_mode == "live" and not self.live_trading_enabled:
            raise ValueError("Live mode requires LIVE_TRADING_ENABLED=true")
        if self.capital <= 0 or self.max_risk_per_trade <= 0:
            raise ValueError("capital and risk limits must be positive")
        if self.max_trades_per_day != 1:
            raise ValueError(
                "This strategy is intentionally restricted to exactly one daily trade maximum"
            )

    @property
    def live_execution_allowed(self) -> bool:
        return self.trading_mode == "live" and self.live_trading_enabled and not self.kill_switch
