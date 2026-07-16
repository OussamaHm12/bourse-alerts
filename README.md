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
- Sends two full Telegram digests per trading day, at 10:00 and 16:00 Morocco time:
  - your portfolio: current value, net profit/loss after fees, and a SELL/HOLD advice per position
  - a market recap: top movers, unusual volume, and the BUY-score opportunities (top pick detailed + Top 5 with score >= 60)
- Sends a lightweight intraday update every 2 hours during the session (12:00 and 14:00 Morocco):
  portfolio P/L, opportunities scoring >= 60, and the day's movers.
- Sends an immediate urgent alert only when a stock you actually own crashes -5% or more intraday.
- Tracks your real holdings (quantity + buy price) and tells you the net gain if you sell now.
- Provides a Streamlit dashboard.

Detected technical events (price crash, volume spike, breakout, support test, high opportunity
score) are still recorded in the database and surfaced inside the two daily digests, instead of
firing a separate notification each time.

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

3. Set the environment variables on the deployed service (Railway):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

   Set them in **exactly one** place. The deployed service is the only sender —
   see [Notifications: one source of truth](#notifications-one-source-of-truth).

4. Test manually:
   - Open the app and use the "run now" button, or
   - `python -m moroccan_stock_intelligence.cli daily-summary` against the same database.

## CLI

```bash
python -m moroccan_stock_intelligence.cli init-db
python -m moroccan_stock_intelligence.cli collect
python -m moroccan_stock_intelligence.cli analyze
python -m moroccan_stock_intelligence.cli morning-digest
python -m moroccan_stock_intelligence.cli afternoon-digest
python -m moroccan_stock_intelligence.cli watch-holdings
python -m moroccan_stock_intelligence.cli daily-summary
python -m moroccan_stock_intelligence.cli run-once
```

- `morning-digest` / `afternoon-digest`: collect, analyze, and send one consolidated Telegram
  digest (portfolio advice + market summary). These are the only two scheduled notifications.
- `watch-holdings`: collect and analyze, then send an urgent Telegram alert **only** if a stock
  you own crashed `URGENT_CRASH_PCT` or more today (deduplicated to once per symbol per day).
- `run-once`: collects prices, stores them, collects news, and computes signals. It no longer
  pushes Telegram messages, so it is safe for ad-hoc local runs.
- `send-alerts`: legacy per-event dispatch, kept for manual use.

## Notifications: one source of truth

**The deployed service is the only sender.** Its in-process scheduler owns every Telegram digest,
every push, and every alert. Nothing else may hold `TELEGRAM_BOT_TOKEN`.

This used to be split. A `.github/workflows/stock-alert.yml` cron also sent digests (10:00 / 16:00
Morocco) while the scheduler sent its own (09:00 / 17:00) — four digests a day. Worse than the
duplication: the workflow ran against a **different database**, a throwaway SQLite file restored from
the GitHub Actions cache. Its history depth had nothing to do with production's, so its momentum,
scores and confidences were structurally different, and the two channels could contradict each other
about the same stock on the same day. The workflow was removed for that reason — not for tidiness.

If you ever reintroduce a second scheduled runner, give it its own read-only job. Do not give it the
bot token.

Schedule (Africa/Casablanca, weekdays unless noted) — see [scheduler.py](moroccan_stock_intelligence/scheduler.py):

| Time | Job |
| --- | --- |
| 07:30 | `macro_collect` (Bank Al-Maghrib) |
| 09:00 | `morning_digest` — collect + news + analyze + Telegram + push |
| 11:00, 13:00, 15:00 | `intraday_update` — light refresh + crash safety net |
| 17:00 | `closing_digest` |
| 18:00 | `research_reports` — multi-analyst reports |
| 22:00 (daily) | `database_backup` — see [Backups](#backups) |
| 06:00 (daily) | `learning_cycle` — grade matured predictions, recalibrate |
| Sun 03:00 / 04:30 | `issuer_collect` / `knowledge_harvest` |

## Backups

The database is the only thing here that cannot be rebuilt. The upstream history endpoint re-serves
only a ~3-year rolling window, so anything older than that which is lost is lost permanently.

`database_backup` runs nightly at 22:00 — after the last writing job — and:

1. snapshots the file with SQLite's **online backup API** (never a file copy: the scheduler and the
   API share the database and a `cp` of a live file can capture a torn page);
2. verifies the copy with `PRAGMA integrity_check` — an unverified backup is not a backup;
3. gzips it (~9x on real data);
4. ships it to Telegram, off-host, using the credentials that already exist;
5. rotates local copies, keeping `BACKUP_KEEP` (default 7).

Local copies answer logical damage (a bad backfill, a hand-run `UPDATE`). The Telegram copy answers
losing the volume itself. Shipping is best-effort: if it fails, the verified local snapshot still
stands and you get a warning — but a local-only backup leaves the real risk uncovered, so do not
ignore that warning.

On demand, and **before any destructive operation**:

```bash
railway ssh
python -m moroccan_stock_intelligence.cli backup            # snapshot + ship
python -m moroccan_stock_intelligence.cli backup --no-ship  # snapshot only
```

Exits non-zero if the snapshot cannot be verified, so it is safe to use as a gate:
`cli backup && cli reclassify-news --apply`.

To restore: download the archive, `gunzip` it, and put it back at `data/market.db`.

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

## Mobile App (PWA)

A FastAPI server exposes a JSON API and serves an installable Progressive Web App
([webapp/](webapp/)) with **web-push notifications** and an **in-process scheduler**
(APScheduler, timezone `Africa/Casablanca`). One always-on process does everything: it collects,
analyzes, sends the 09:00 / 17:00 digests, the 11:00 / 13:00 / 15:00 intraday updates, and the
urgent holding alerts, and pushes them to your phone — at the exact time, reliably. It is the only
sender; see [Notifications: one source of truth](#notifications-one-source-of-truth).

Run locally:

```bash
python -m moroccan_stock_intelligence.cli gen-vapid   # once: copy the keys into .env
python -m moroccan_stock_intelligence.cli serve       # http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000`, tap **Activer** to allow notifications, then **Tester**.
On a phone, use the browser menu → *Add to Home screen* to install the app icon.

The PWA has four tabs — **Portefeuille**, **Marché**, **Opportunités**, **Actus** —
plus a per-stock detail sheet (price sparkline, score breakdown with reasons/risks,
technical indicators, linked news) and a manual "run now" button.

Endpoints:

- `GET /api/overview` — portfolio (value, net P/L, advice) + market summary
- `GET /api/stocks?sort=&sector=&q=` — full market table with scores and labels
- `GET /api/stock/{symbol}` — full detail: metrics, score breakdown, history, news
- `GET /api/opportunities?min_score=` — ranked BUY-score opportunities
- `GET /api/news`, `GET /api/sectors`
- `GET /api/health`, `GET /api/vapid-public-key`
- `POST /api/push/subscribe`, `POST /api/push/test`, `POST /api/run-now`

### Explainable investment analysis (Analyse IA)

Per-horizon, explainable analyses built from the internal data only (prices,
metrics, scores, news, portfolio). Missing metrics are reported, never guessed;
the wording stays probabilistic and every payload carries the disclaimer.

- `GET /api/analysis/{symbol}?horizon=short|medium|long` — full analysis: recommendation
  (Forte opportunité / À surveiller / Conserver / Prendre des bénéfices / Éviter / Risqué),
  confidence, risk, expected scenario, bullish/bearish arguments, summaries, portfolio
  impact, suggested action, watch-next list, and an `explainability` block
  (`data_used`, `positive_factors`, `negative_factors`, `missing_data`,
  `decision_reason`, `confidence_reason`, `risk_reason`).
- `GET /api/analysis/opportunities?horizon=&min_score=&limit=` — ranked opportunities per horizon
- `GET /api/analysis/portfolio` — per-holding analysis + positions needing attention
- `GET /api/analysis/market-summary` — market regime, breadth, top picks per horizon

Scoring (weighted mean of the AVAILABLE components only — see
[horizon_strategy.py](moroccan_stock_intelligence/services/horizon_strategy.py)):

- short  = 0.30 momentum(1j/5j) + 0.20 volume + 0.20 cassure + 0.15 support + 0.15 actus
- medium = 0.35 tendance(30/90j) + 0.25 moyennes mobiles + 0.15 secteur + 0.15 volatilité⁻¹ + 0.10 actus
- long   = 0.30 tendance longue + 0.30 stabilité + 0.20 structure 52s + 0.10 secteur + 0.10 événements
- le score est atténué vers 50 quand moins de 80 % des composantes sont disponibles
  (pas de fausse certitude construite sur un seul indicateur)
- confidence = 50·couverture + 30·min(historique/cible, 1) + 20·cohérence (cibles 30/90/250 j)

Intelligent notifications (web push + in-app inbox only, Telegram untouched):
held position with SELL advice or risk ≥ 70, fresh negative news on a holding,
or a new short-term opportunity (score ≥ 72, confidence ≥ 55, risk < 60).
Deduplicated once per symbol per day, max 3 per scheduled run.

### Deployment (for notifications on the go)

Web push and PWA install require **HTTPS** in production (`http://localhost` is exempt for dev).
Deploy the single `webapp` container on any always-on host that gives you HTTPS:

```bash
docker compose up -d webapp   # serves on :8000 behind your HTTPS reverse proxy
```

- **Managed (simplest)**: Railway / Fly.io / Render give an `https://…` URL out of the box —
  push the repo, set the env vars (`VAPID_*`, `TELEGRAM_*`, `PORTFOLIO_JSON`, `TIMEZONE`), done.
- **VPS / Raspberry Pi**: run the container and put **Caddy** in front for automatic Let's Encrypt
  HTTPS, or expose it through a **Cloudflare Tunnel**.

Keep `ENABLE_SCHEDULER=true` on exactly one instance so the digests fire once.

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

## Favorites (the watchlist)

Star any stock from the **Marché** tab (or from its detail sheet) and it becomes a
favorite, stored in the `favorites` table. Favorites are managed entirely from the app —
no file to edit, no redeploy.

A favorite is deliberately **not** a holding: it carries no quantity and no buy price,
so it has no P/L and never produces a SELL/HOLD advice. What it buys is *attention*:

- **Urgent crash alert.** A favorite falling `URGENT_CRASH_PCT` (-5% by default) intraday
  triggers an immediate Telegram alert, exactly like a held position — minus the P/L block.
- **Priority on thesis notifications.** Thesis-change pushes are capped at 3 per run
  (`MAX_PUSHES_PER_RUN`). Favorites are evaluated first, so a change on a stock you watch
  is never crowded out by one on a stock you have never looked at.
- **Its own digest section.** The 09:00/17:00 digests carry a `⭐ Mes favoris` block, and
  the intraday points carry a one-line recap plus a detail line for anything moving ≥5%.
- **Its own tab** in the app, ordered by opportunity score, best first. A favorite with no
  collected price sorts last rather than as a zero: a missing score is not a bad one.

The two lists are independent: holding a stock does not favorite it, and vice-versa. A
stock that is **both** held and favorited is alerted **once** — as a holding, which is the
richer message.

| Endpoint | Method | Effect |
| --- | --- | --- |
| `/api/favorites` | GET | Every favorite, evaluated and sorted |
| `/api/favorites/{symbol}` | POST | Star (idempotent) |
| `/api/favorites/{symbol}` | DELETE | Un-star (no-op if absent) |

> [config/watchlist.json](config/watchlist.json) is legacy: it now only filters the
> Streamlit dashboard's watchlist page and drives none of the alerts or digests.

## Portfolio Holdings (stocks you actually own)

Copy [config/portfolio.example.json](config/portfolio.example.json) to `config/portfolio.json` and
fill in each position. `config/portfolio.json` is gitignored so your buy prices stay private.

```json
{
  "fee_rate": 0.005,
  "holdings": [
    { "symbol": "ATW", "quantity": 10, "buy_price": 410.0 },
    { "symbol": "TGC", "quantity": 5, "buy_price": 700.0 }
  ]
}
```

- `quantity`: number of shares you hold.
- `buy_price`: average price you paid per share, in MAD.
- `fee_rate`: round-trip selling fee used for the net profit estimate (0.005 = 0.5%). Defaults to
  `TRADING_FEE_RATE`.

Do not commit this file (it is gitignored). On the deployed service, set `PORTFOLIO_JSON` to the same
JSON on a single line; it takes priority over the file, so your buy prices stay out of the repo.

### How the SELL / HOLD advice works

For each holding the digest combines technical signals with your profit:

- **SELL** if the stop-loss is hit (`net P/L <= STOP_LOSS_PCT`), the technical risk is high
  (`AVOID score >= SELL_AVOID_SCORE`), or you have a large gain while momentum weakens
  (`net P/L >= TAKE_PROFIT_PCT` and `momentum_30d <= WEAK_MOMENTUM_PCT`).
- **HOLD** otherwise.

The projected gain shown is **net of fees**: `current_price * quantity * (1 - fee_rate) - buy_price * quantity`.

All thresholds are environment variables (see [.env.example](.env.example)), so you can tune the
strategy without touching code. Long-window signals such as `momentum_30d` only become meaningful
once the database has collected enough history.

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

## Refresh on open

Launching the app re-collects the market, so you never look at yesterday's numbers.
The tabs keep showing the cached data while it runs (the header says "Mise à jour…"),
then all reload at once when the fresh prices land.

It is **silent by design**: it collects, persists and recomputes, but sends no Telegram
and no push. Reusing the digest job here would have notified you every single time you
opened the app. `/api/run-now` still exists for a digest on demand.

Two guards, because a collection writes ~80 new price rows (`observed_at` is the
collection instant):

- **Cooldown** — `APP_REFRESH_COOLDOWN_SECONDS` (default 900). Casablanca Bourse
  publishes with a stated ~15 min delay, so scraping faster returns data we already
  have. Inside the cooldown the server answers `fresh` and skips the fetch. The
  **Actualiser** button forces past it.
- **Single-flight** — two launches (or a launch landing on a scheduled job) never
  scrape concurrently. The slot is claimed *before* the endpoint responds, so an app
  polling milliseconds later cannot mistake "not started yet" for "already finished".
  A collection presumed dead after 5 min releases the slot rather than wedging the app.

| Endpoint | Method | Effect |
| --- | --- | --- |
| `/api/refresh` | POST | Collect unless fresh. `?force=true` ignores the cooldown. Returns `fresh` / `running` / `started` |
| `/api/refresh/status` | GET | Polled while a collection runs; carries `as_of` and `data_age_seconds` |
| `/api/run-now` | POST | Collect **and notify** (Telegram digest + push) |

## Operations Notes

- Backups are automatic (nightly 22:00, verified, shipped to Telegram). See [Backups](#backups).
  Take a manual one with `cli backup` before any destructive operation.
- Monitor the service logs for source parse warnings, and for `backup_job_failed` /
  `backup_not_shipped`.
- Prefer official Casablanca Bourse data when available.
- Treat public delayed quotes as intelligence inputs, not execution-grade data.
- Do not increase scraping frequency aggressively; the current 3-hour cadence is intentionally polite.
- Keep `HTTP_VERIFY_SSL=true` in production. Set `HTTP_ALLOW_INSECURE_SOURCE_RETRY=true` so a public market-data source with a broken certificate chain can be retried without disabling SSL globally.

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
