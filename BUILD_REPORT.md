# Build Report — NIFTY AI Trader

## Status

Completed as a paper-first quantitative research platform. `TRADING_MODE=paper` is the default and live execution is hard-gated by a separate `LIVE_TRADING_ENABLED=true` setting. No live order was placed during build or validation.

## Architecture delivered

- Official Kite Connect request-token authentication helper, secure token storage, historical/instrument/quote adapters, and WebSocket health model.
- IST-aware candle/quote validation, optional global/news context interfaces, and conservative NSE calendar abstraction.
- Transparent technical features, regime detection, multi-factor signal scoring, opening-range strategy, and liquidity-aware option selection.
- Enforced ₹200 default per-trade risk budget, one-trade daily cap, maximum position value, daily-loss restriction, stale-data guard, kill switch, and forced 15:15 IST square-off logic.
- Deterministic paper broker with adverse slippage, costs, duplicate-order prevention, order/position inspection, and a live adapter that rejects disabled execution.
- No-look-ahead opening-range backtest engine, simulated exit/costs/slippage, core performance metrics, train/validation/out-of-sample walk-forward segmentation, SQLite audit schema, JSON/CSV reports, daily reports, and an optional static local dashboard.

## Validation run

Executed with Python 3.10 in `.venv`:

| Check | Result |
|---|---|
| Dependency installation | Passed |
| Import/compile check | Passed |
| `pytest -q` | **20 passed** |
| `ruff check .` | Passed |
| Configuration validation | Passed (paper mode) |
| Paper dry run | Passed; explicitly reported that no live order can be submitted |
| Paper-order lifecycle | Covered by automated test |
| Historical backtest | Not run: no user/broker-supplied historical data exists in `data/private/` |
| Walk-forward validation | Not run: requires the same unavailable historical data |
| Performance report/dashboard | Implemented but not generated because no historical trades were fabricated |

## Backtest results

No backtest results exist. The project deliberately does not manufacture candles, fills, trades, profit, or out-of-sample performance. Supply broker-authorized, timezone-aware IST OHLC data and run:

```powershell
.\.venv\Scripts\python.exe main.py backtest --data data/private/nifty_candles.csv
```

The command will generate a JSON report and attempt walk-forward validation when at least three trading days are present. Therefore no conclusion about profitability or out-of-sample survival can be made.

## Required configuration

Copy `.env.example` to `.env`. For Kite data access, set `KITE_API_KEY`, `KITE_API_SECRET`, and a supported-session access token. Credentials, tokens, private data, databases, logs, and generated reports are ignored by Git.

## Known limitations and pre-live work

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md). Before any contemplated live use: provide and validate authorised data access, maintain the official NSE holiday list, independently review strategy behavior across adequate out-of-sample data, reconcile paper and broker execution behavior, test operational monitoring, and obtain explicit human approval. This build must remain in paper mode until then.
