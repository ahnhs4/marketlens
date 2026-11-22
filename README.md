# MarketLens — US/KR equity research terminal (MVP)

Market → Macro regime → Sectors → Stocks → Technicals → Buy/Sell signal → 3Y historical validation → Risk, in one app.

## Architecture
Next.js 16 (TS, Tailwind v4, shadcn/ui, lightweight-charts v5) → `/api/*` proxied → FastAPI (Python 3.12)
→ services (cache: Redis or in-memory, single-flight) → SQLAlchemy (SQLite dev / PostgreSQL prod, Alembic migrations)
→ providers (Yahoo Finance, FRED, Google News RSS, SEC EDGAR, KRX KIND, Finnhub*, Anthropic*). APScheduler runs
universe scans (S&P 500, Nasdaq-100 approx., KOSPI 100, KOSDAQ 50), alerts, fundamentals backfill. *optional keys.

## Run locally
Node 24 and Python 3.12 are bundled in `.tools/` (no system install needed).
```
scripts/setup.sh          # once: Python venv + npm packages, creates .env from .env.example
scripts/dev-backend.sh    # terminal 1 -> http://127.0.0.1:8000 (migrations run on start)
scripts/dev-frontend.sh   # terminal 2 -> http://localhost:3000
```
Optional keys go in `.env` (see `.env.example`). The first universe scan takes ~3 minutes after the backend starts.
Tests: `cd backend && .venv/bin/python -m pytest -m "not network"` (offline) or `pytest` (includes live-API tests).

## Methodology (summary)
- Technical score = 50 + Σ wᵢ(componentᵢ − 50): trend, momentum, RSI, volume, breakout, S/R, volatility, pattern.
  Signal thresholds BUY ≥ 62, STRONG BUY ≥ 78, SELL ≤ 40, STRONG SELL ≤ 28, plus trend gates; Risk-Off raises BUY by 6.
- Regime = 50 + 50(0.35 risk + 0.20 liquidity + 0.20 policy + 0.15 growth − 0.10 inflation) from FRED + market data.
- Backtests: signal at close t, fill at open t+1, costs per side, dividend-adjusted; walk-forward train/validation/OOS.
- Historical analogs: robust-z feature distance, de-clustered matches, 20-day embargo, realised outcomes only, Wilson CI.

## Known limitations
ISM PMIs and put/call ratios are licensed (proxies shown). Nasdaq-100 membership is approximated. KRX quotes are
20-min delayed via Yahoo. Without AI_API_KEY news analysis is rule-based and labeled as such.
