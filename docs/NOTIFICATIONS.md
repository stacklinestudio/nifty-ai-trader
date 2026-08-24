# Notifications

Telegram and Discord are optional failure-isolated integrations. Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, or `DISCORD_WEBHOOK_URL` in `.env`. They retry with bounded backoff and return `False` on failure; notification loss never enables or blocks a trade. Never include credentials in messages.
