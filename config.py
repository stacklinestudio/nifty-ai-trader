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
    # Raised from 400 alongside max_trades_per_day (below), 2026-08-27, user
    # decision (Brief 3, Part B): at 3 trades/day and 600 risk/trade, worst
    # case if all 3 lose is 1800 (18% of 10k capital). 1200 is a real,
    # meaningful backstop -- not just "3 x max_risk_per_trade" with no
    # cushion -- because it trips after 2 full losses (realized_pnl <=
    # -1200), blocking the 3rd attempt before it's tried, rather than only
    # ever mattering after all trades are already exhausted.
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "1200"))
    # Raised from 1, 2026-08-27, user decision (Brief 3, Part B). Presented
    # tradeoff: multiple trades/day are same-underlying, same-session
    # correlated risk, not diversified bets. Paired with (a) fixed-base
    # sizing -- every trade sizes off this same Settings object, never a
    # running/current balance, see risk/risk_manager.py's docstring -- and
    # (b) a same-direction-re-entry-after-a-stop-out re-validation gate
    # (agents/trade_validator.py) so a broken thesis can't just re-fire
    # immediately with the same setup in the same regime.
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
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
    # Brief 8 Part C: stays "unavailable" until explicitly flipped by hand
    # in .env.local on a fresh run -- never flipped programmatically here,
    # and never while a live run is in progress (user's own explicit
    # instruction).
    ai_provider: str = os.getenv("AI_PROVIDER", "unavailable")
    ai_model: str = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    trail_percent: float = float(os.getenv("TRAIL_PERCENT", "0.15"))
    supervision_poll_seconds: float = float(os.getenv("SUPERVISION_POLL_SECONDS", "10"))
    max_consecutive_tick_failures: int = int(os.getenv("MAX_CONSECUTIVE_TICK_FAILURES", "5"))
    # Brief 6: periodic entry re-scanning. 240s (4 min) sits in the
    # brief's own suggested 3-5 minute range -- comfortably inside Kite's
    # documented 1 quote req/sec, 3 historical req/sec limits (enormous
    # headroom at this cadence) and matches the timescale these setups
    # actually develop on; deliberately not sub-minute (see
    # execution/scheduler.py's run_trading_day docstring for the
    # polling-vs-WebSocket tradeoff this pairs with).
    entry_scan_interval_seconds: float = float(os.getenv("ENTRY_SCAN_INTERVAL_SECONDS", "240"))
    # A full 15 minutes before forced_exit_time (15:15 default) so a fresh
    # position never opens with no realistic time to develop before
    # mandatory square-off. Only gates STARTING a new scan/entry -- an
    # already-open position at this time is unaffected and still
    # supervised through to forced_exit_time via run_supervised.
    entry_scan_cutoff_time: time = field(
        default_factory=lambda: time.fromisoformat(os.getenv("ENTRY_SCAN_CUTOFF_TIME", "15:00"))
    )
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    # Per-category Discord channels; each optional, falling back to
    # DISCORD_WEBHOOK_URL when unset (see integrations/discord.py).
    discord_webhook_market_research: str = os.getenv("DISCORD_WEBHOOK_MARKET_RESEARCH", "")
    discord_webhook_signals: str = os.getenv("DISCORD_WEBHOOK_SIGNALS", "")
    discord_webhook_trades: str = os.getenv("DISCORD_WEBHOOK_TRADES", "")
    discord_webhook_risk: str = os.getenv("DISCORD_WEBHOOK_RISK", "")
    discord_webhook_system: str = os.getenv("DISCORD_WEBHOOK_SYSTEM", "")
    discord_webhook_daily_report: str = os.getenv("DISCORD_WEBHOOK_DAILY_REPORT", "")
    obsidian_vault_path: str = os.getenv("OBSIDIAN_VAULT_PATH", "")

    def validate(self) -> None:
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be paper or live")
        if self.trading_mode == "live" and not self.live_trading_enabled:
            raise ValueError("Live mode requires LIVE_TRADING_ENABLED=true")
        if self.capital <= 0 or self.max_risk_per_trade <= 0:
            raise ValueError("capital and risk limits must be positive")
        # Was a hard "must be exactly 1" guard (original spec, Section 15).
        # Raised, 2026-08-27, user decision (Brief 3, Part B) to 3, paired
        # with max_daily_loss=1200, fixed-base sizing, and the re-entry
        # re-validation gate -- see config field comments above. The bound
        # (not a fixed "must be exactly 3") keeps this a real guard against
        # an unconsidered jump (e.g. an env var typo setting this to 50)
        # rather than reopening it to any value; 4 was the highest option
        # actually presented and considered in that decision.
        if not 1 <= self.max_trades_per_day <= 4:
            raise ValueError("max_trades_per_day must be between 1 and 4")
        if self.entry_scan_cutoff_time >= self.forced_exit_time:
            raise ValueError("entry_scan_cutoff_time must be before forced_exit_time")

    @property
    def live_execution_allowed(self) -> bool:
        return self.trading_mode == "live" and self.live_trading_enabled and not self.kill_switch
