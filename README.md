# NIFTY AI Trader

NIFTY AI Trader is a safety-first quantitative research, historical-backtesting, and live-market **paper-trading** system for NIFTY 50 options using Zerodha Kite Connect. V2 extends it into a multi-agent trading intelligence system: structured research agents, event-driven orchestration, independent validation, deterministic risk veto, learning memory, and optional notification/journal exports. It is a research platform, not a promise of profitability.

## Safety

`TRADING_MODE=paper` is the default. The application never silently enables live trading; the `KiteExecutor` rejects orders unless both `TRADING_MODE=live` and `LIVE_TRADING_ENABLED=true` are explicitly set. The strategy permits at most one trade per day, applies a ₹200 default per-trade risk budget, validates stale data, respects a kill switch, and supports a 15:15 IST forced exit.

## Architecture

- `data/`: broker and public-context adapters, instrument/candle validation, WebSocket health.
- `intelligence/` and `strategy/`: transparent features, regime classification, opening-range and signal logic.
- `risk/` and `execution/`: hard risk limits, option selection, simulated paper orders, guarded live adapter.
- `backtest/`: same opening logic, adverse slippage/costs, metrics, and walk-forward evaluation.
- `storage/` and `monitoring/`: SQLite audit trail, structured logging, health/performance exports.
- `dashboard.py`: a local, static results dashboard generated from an exported trades CSV.
- `agents/`, `events/`, `ai/`, and `learning/`: V2 contracts, orchestration, audit trail, provider abstraction, and validation-gated research memory.
- `integrations/`: optional Telegram, Discord, and Obsidian adapters that cannot affect trade safety.

## Install

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
pytest -q
```

Never commit `.env`, tokens, credentials, or private market data. Configure Kite credentials in `.env`; use the official request-token login URL from `auth.kite_auth.KiteAuthenticator`, then store the resulting access token outside version control.

## Usage

Historical CSV must contain timezone-aware IST `timestamp`, `open`, `high`, `low`, `close` (and optionally `volume`) columns. It is user/broker supplied—this project does not invent data.

```powershell
python main.py health
python main.py backtest --data data/private/nifty_candles.csv
python main.py paper
python main.py instruments
python main.py agents
python main.py events
python main.py notifications
python main.py export-obsidian
python dashboard.py reports/generated/trades.csv
```

Backtests write JSON including gross/net P&L, slippage, costs, drawdown, Sharpe/Sortino where meaningful, and a separated train/validation/out-of-sample walk-forward result when sufficient dates exist. Generated reports and private data are ignored by Git.

## Data and deployment

Use only legally available, documented data sources and the official Kite APIs. For a paper session, authenticate/validate before market open, prime live data before 09:15 IST, and let the system return `NO_TRADE` when data or confidence is insufficient. Run the process under an OS service only after validating time zone, exchange holiday calendar, connectivity, kill switch, and reporting.

## Limitations

Read [docs/LIMITATIONS.md](docs/LIMITATIONS.md) before use. Paper trading differs from real execution, free data is limited/delayed, and no strategy profitability is guaranteed.
