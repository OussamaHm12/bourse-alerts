# Moroccan Stock Intelligence Platform

Production-oriented Python platform for collecting Casablanca Stock Exchange market snapshots, storing history, computing opportunity signals, sending Telegram alerts, and exploring the market in Streamlit.

This project is for market intelligence and notifications only. It does not place trades, route orders, or provide investment advice.

## What It Does

- Discovers listed equities from the official Casablanca Bourse actions page.
- Stores every collected snapshot indefinitely in SQL tables.
- Uses SQLite locally and any SQLAlchemy-supported PostgreSQL URL in production.
- Computes momentum, moving averages, volatility, volume anomalies, relative performance, support/resistance distance, drawdowns, and 52-week proximity.
- Scores opportunities from 0 to 100 with component explanations.
- Collects official Casablanca Bourse announcements and links them to known symbols when possible.
- Sends Telegram alerts for meaningful events:
  - price crash of -5% or more
  - volume spike above 2x recent average
  - breakout or near 52-week high
  - support test
  - opportunity score above `MIN_OPPORTUNITY_SCORE`
- Sends a daily market summary.
- Provides a Streamlit dashboard.

Public Moroccan market data may be delayed, unavailable outside market hours, or inconsistent across providers. Casablanca Bourse states on its website that indices are real-time and prices are delayed by 15 minutes.

## Architecture

```text
.
├── moroccan_stock_intelligence/
│   ├── cli.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── repository.py
│   ├── schemas.py
│   ├── scrapers/
│   │   ├── casablanca.py
│   │   ├── bmce.py
│   │   └── cdg.py
│   └── services/
│       ├── alerts.py
│       ├── analytics.py
│       ├── collector.py
│       ├── news.py
│       ├── portfolio.py
│       ├── scoring.py
│       └── telegram.py
├── dashboard/app.py
├── config/watchlist.json
├── tests/
├── .github/workflows/stock-alert.yml
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
└── stock_alert.py
```

`stock_alert.py` remains as a backward-compatible wrapper around the new CLI.

## Data Sources

Primary:

- Casablanca Bourse actions page: `https://www.casablanca-bourse.com/fr/live-market/marche-actions-groupement`

Fallback:

- BMCE Capital Bourse list pages
- CDG Capital Bourse public pages

News:

- Casablanca Bourse official notices: `https://www.casablanca-bourse.com/fr/avis`

The scrapers use browser-like headers, retries, timeouts, source isolation, and structured logs. If one source fails, the collector tries the next source.

## Database Schema

Tables are defined in [models.py](moroccan_stock_intelligence/models.py):

- `stocks`: symbol, company name, sector, source metadata.
- `prices`: every market snapshot with price, variation, volume, traded quantity, market cap, highs/lows, raw payload.
- `signals`: analytics events and score explanations.
- `alerts`: de-duplicated alert events and Telegram delivery state.
- `news`: official announcements, event type, sentiment, impact score, optional linked stock.

Default local database:

```text
sqlite:///data/market.db
```

PostgreSQL migration path:

```text
DATABASE_URL=postgresql+psycopg://market:market@localhost:5432/market
```

`psycopg[binary]` is included in `requirements.txt` for this path.

## Setup

```bash
python -m pip install -r requirements.txt
python -m moroccan_stock_intelligence.cli init-db
python -m moroccan_stock_intelligence.cli run-once
```

Windows PowerShell:

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m moroccan_stock_intelligence.cli init-db
py -3 -m moroccan_stock_intelligence.cli run-once
```

Environment variables:

```text
TELEGRAM_BOT_TOKEN=123456789:replace_with_your_bot_token
TELEGRAM_CHAT_ID=123456789
DATABASE_URL=sqlite:///data/market.db
HTTP_TIMEOUT_SECONDS=20
HTTP_RETRIES=3
HTTP_VERIFY_SSL=true
HTTP_ALLOW_INSECURE_SOURCE_RETRY=false
LOG_LEVEL=INFO
WATCHLIST_FILE=config/watchlist.json
MIN_OPPORTUNITY_SCORE=80
```

Copy `.env.example` to `.env` for local Docker or shell use.

## Telegram Setup

1. Create a bot:
   - Open Telegram and message `@BotFather`.
   - Run `/newbot`.
   - Copy the bot token.

2. Get your chat ID:
   - Send a message to the bot.
   - Open:

```text
https://api.telegram.org/bot<TOKEN>/getUpdates
```

   - Copy `chat.id`.

3. Add GitHub Secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

4. Test manually:
   - Open GitHub `Actions`.
   - Run `Moroccan Stock Intelligence`.
   - Choose `run-once` or `daily-summary`.

## CLI

```bash
python -m moroccan_stock_intelligence.cli init-db
python -m moroccan_stock_intelligence.cli collect
python -m moroccan_stock_intelligence.cli analyze
python -m moroccan_stock_intelligence.cli send-alerts
python -m moroccan_stock_intelligence.cli daily-summary
python -m moroccan_stock_intelligence.cli run-once
```

`run-once` collects prices, stores them, collects news, computes signals, creates alerts, and dispatches unsent Telegram messages.

## GitHub Actions

[stock-alert.yml](.github/workflows/stock-alert.yml) runs:

- every 3 hours: `run-once`
- weekday daily summary at `18:30 UTC`: `daily-summary`
- manual `workflow_dispatch` with mode selection

The workflow restores and saves `data/market.db` using GitHub Actions cache and uploads the database as an artifact.

## Dashboard

Run locally:

```bash
streamlit run dashboard/app.py
```

Pages:

- Market Overview
- Stock Explorer
- Top Opportunities
- Signals
- Historical Charts
- News Feed
- Portfolio Watchlist

## Docker

Collector:

```bash
docker compose run --rm collector
```

Dashboard:

```bash
docker compose up dashboard
```

Then open:

```text
http://localhost:8501
```

Optional PostgreSQL profile:

```bash
docker compose --profile postgres up postgres
```

## Portfolio Watchlist

Edit [config/watchlist.json](config/watchlist.json):

```json
{
  "symbols": ["TGC", "AKT", "ATW", "CIH", "MSA", "HPS", "MNG"]
}
```

The dashboard watchlist page tracks performance, opportunity scores, and alert context for these symbols.

## Scoring Model

BUY score weighting:

- 25% momentum
- 20% volume anomaly
- 20% valuation opportunity
- 15% support proximity
- 10% sector strength
- 10% recent news sentiment

The platform also emits WATCH and AVOID scores. Every score includes reasons, risks, and component values.

Early runs have limited history, so long-window metrics such as MA200 and 52-week high/low become more meaningful as the database grows.

## Testing

```bash
pytest
ruff check .
ruff format --check .
```

Install pre-commit hooks:

```bash
pre-commit install
```

Testing strategy:

- Parser tests for Moroccan number formats and Casablanca Bourse table extraction.
- Scoring tests for bounded outputs and explanations.
- Add regression fixtures whenever a source changes HTML shape.
- Add integration tests using saved HTML fixtures before relying on new data sources.

## Operations Notes

- Keep `data/market.db` backed up if running outside GitHub Actions.
- Monitor workflow logs for source parse warnings.
- Prefer official Casablanca Bourse data when available.
- Treat public delayed quotes as intelligence inputs, not execution-grade data.
- Do not increase scraping frequency aggressively; the current 3-hour cadence is intentionally polite.
- Keep `HTTP_VERIFY_SSL=true` in production. GitHub Actions enables `HTTP_ALLOW_INSECURE_SOURCE_RETRY=true` so a public market-data source with a broken certificate chain can be retried without disabling SSL globally.

## Future Roadmap

- Alembic migrations.
- PostgreSQL production profile with `psycopg`.
- More news sources and RSS/media adapters.
- PDF text extraction for official notices.
- Sector benchmark indices.
- Fundamentals and valuation ratios where public data is available.
- Backtesting module for signal quality.
- Telegram command bot for on-demand stock lookup.
- Role-based dashboard auth for hosted deployment.
