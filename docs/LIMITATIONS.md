# Limitations and safety boundaries

- Kite authentication follows its supported request-token flow. This project does not automate Zerodha credentials or browser login.
- Kite access, historical candles, option-chain fields, and live quotes require a valid Kite Connect subscription, permissions, and documented API use. No external market values are fabricated when unavailable.
- Public/free data may be delayed, incomplete, rate-limited, or unavailable. It is treated as optional context, never as guaranteed real-time data.
- Paper fills model configurable adverse slippage and estimated costs but cannot reproduce queue position, rejected orders, partial fills, taxes, or live liquidity perfectly.
- Opening-market conditions can be volatile; historical backtests do not guarantee out-of-sample or live performance.
- The default mode is paper. The live adapter rejects all orders unless `TRADING_MODE=live` and `LIVE_TRADING_ENABLED=true` are explicitly set. Human review is required before live trading.
- The supplied exchange-calendar interface is intentionally conservative; maintain NSE holiday data before unattended scheduling.
