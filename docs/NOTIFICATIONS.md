# Notifications

Telegram and Discord are optional failure-isolated integrations. They retry with bounded backoff and return `False` on failure; notification loss never enables or blocks a trade. Never include credentials in messages.

Telegram: configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

## Discord: 6 category channels

Discord notifications route to one of 6 category webhooks based on the event type, instead of a single channel. Configure any subset of these in `.env`:

| Category | Env var | Covers |
|---|---|---|
| `market_research` | `DISCORD_WEBHOOK_MARKET_RESEARCH` | Global/India/News/Technical/Volatility/Breadth agent output, regime detection, FII/DII flow, OI buildup evidence |
| `signals` | `DISCORD_WEBHOOK_SIGNALS` | `SIGNAL_CREATED`, candidate setups from `SignalHunterAgent` |
| `trades` | `DISCORD_WEBHOOK_TRADES` | `TRADE_PROPOSED`, `PAPER_ORDER_SENT`, `PAPER_FILL`, `TAKE_PROFIT`, `STOP_LOSS`, `THESIS_INVALIDATED`, `FORCED_EXIT`, `TRADE_COMPLETED` |
| `risk` | `DISCORD_WEBHOOK_RISK` | `RISK_APPROVED`, `RISK_REJECTED`, `TRADE_VALIDATED` (approve/reject/review reasons), daily limit / profit-target / kill-switch state |
| `system` | `DISCORD_WEBHOOK_SYSTEM` | `SYSTEM_STARTED`, `SYSTEM_ERROR`, health checks, crash recovery events, scheduler start/stop, connection loss/restore |
| `daily_report` | `DISCORD_WEBHOOK_DAILY_REPORT` | End-of-day summary, `LEARNING_CREATED`, promotion decisions |

The full category → `EventType` mapping lives in `integrations/discord.py::CATEGORY_BY_EVENT_TYPE`. A few of the scenarios above (connection loss/restore, daily summary, promotion decisions) don't have their own dedicated `EventType` yet — they currently surface via `SYSTEM_ERROR`/`LEARNING_CREATED`, which is the correct category regardless, just without finer sub-typing within it.

**Fallback behavior**: a category with no webhook configured falls back to the single `DISCORD_WEBHOOK_URL` if that's set, or is silently skipped (no notification sent, no error raised) if neither is configured. A send failure on one category never blocks sends to any other category — each is an independent, non-fatal attempt.

Existing single-webhook configuration (`DISCORD_WEBHOOK_URL` only, no per-category vars) keeps working exactly as before — every category just falls back to that one channel.

Test connectivity for every configured channel at once with `python main.py notifications`, which reports `discord_sent` (the legacy single-webhook send) plus `discord_by_category` (one test send per category).
