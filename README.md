# Moroccan Stock Intelligence Platform

Production-oriented Python platform for collecting Casablanca Stock Exchange market snapshots, storing history, computing opportunity signals and multi-analyst investment reports, sending Telegram digests and web-push alerts, and serving an installable PWA.

This project is for market intelligence and notifications only. It does not place trades, route orders, or provide investment advice.

### What this is, precisely

A **deterministic quantitative engine**, a **rule-based multi-agent expert system**,
and **statistical calibration**. In that order of importance.

It is *not* self-learning AI, and the code says so where it would be tempting to
claim otherwise (`services/research/learning.py`: "Deliberately NOT machine
learning"). There is no trained model and no learned inference anywhere. The ten
"analysts" are Python functions. An optional LLM can re-word a finished report and
is disabled by default; it cannot change a number, a score or a recommendation,
and the SDK is not even installed.

The single-owner design is deliberate, not an unfinished feature: the portfolio,
favorites and push subscriptions all assume one person, and multi-user support
would be a schema change rather than a flag.

## What It Does

- Discovers listed equities from the official Casablanca Bourse actions page.
- Stores every collected snapshot indefinitely in SQL tables.
- Uses SQLite locally and any SQLAlchemy-supported PostgreSQL URL in production.
- Computes momentum, moving averages, volatility, volume anomalies, relative performance, support/resistance distance, drawdowns, and 52-week proximity. A windowed indicator is reported only when it has the history its name implies — a MA200 needs 200 séances, not "whatever exists" — so a missing one lowers coverage instead of quietly asserting a number.
- Scores opportunities from 0 to 100 with component explanations, a coverage figure and a confidence.
- Collects official Casablanca Bourse announcements and links them to known symbols when possible.
- **Requires a password.** Every route except the healthcheck and the login endpoints needs a session (`AUTH_PASSWORD`). See [Authentication](#authentication).
- Sends two full Telegram digests per trading day, at **09:00 and 17:00** Morocco time:
  - your portfolio: current value, net profit/loss after **both** commissions (buy and sell), and a SELL/HOLD advice per position
  - a market recap: top movers, unusual volume, and the BUY-score opportunities (top pick detailed + Top 5 with score >= 60)
- Sends a lightweight intraday update during the session (**11:00, 13:00 and 15:00** Morocco):
  portfolio P/L, opportunities scoring >= 60, and the day's movers.
- Sends an immediate urgent alert only when a stock you actually own crashes -5% or more intraday.
- Tracks your real holdings (quantity + buy price) and tells you the net gain if you sell now.
- Serves an installable PWA with web push (see [Mobile App](#mobile-app-pwa)).

Per-symbol notification is **thesis-based**, not event-based: you are told when the *conclusion*
about a stock changes, not every time a price moves. A stock can move 4% in silence because the
thesis survived; a flat stock can push because new evidence broke the case.

Public Moroccan market data may be delayed, unavailable outside market hours, or inconsistent across providers. Casablanca Bourse states on its website that indices are real-time and prices are delayed by 15 minutes.

## Architecture

One container: FastAPI serves the JSON API and the compiled PWA, and runs the scheduler in-process.
The scheduler collects, analyzes, reports and notifies; the API serves **stored** reports rather than
recomputing per request.

```text
.
├── moroccan_stock_intelligence/
│   ├── api.py                     # 25 JSON routes + static PWA mount
│   ├── cli.py                     # container entrypoint; every job is a subcommand
│   ├── scheduler.py               # APScheduler: collect / report / learn / back up
│   ├── config.py  db.py  models.py  repository.py  schemas.py
│   ├── scrapers/                  # casablanca (primary), bmce + cdg (fallbacks)
│   └── services/
│       ├── collector.py           # scraper cascade
│       ├── collectors/            # macro (BKAM), issuers, 3-year history backfill
│       ├── analytics.py           # MetricSet: momentum, MAs, volatility, 52w…
│       ├── market_state.py        # metrics + opportunity scores (the one builder)
│       ├── scoring.py             # buy / watch / avoid
│       ├── horizon_strategy.py    # short / medium / long + risk + confidence
│       ├── news.py                # collect the official notices
│       ├── news_classifier.py     # event-driven classification of those notices
│       ├── news_context.py        # the one recent-news aggregate both engines read
│       ├── news_backfill.py       # re-derive stored notices (idempotent)
│       ├── analysts/              # 8 analysts + risk_manager + cio
│       ├── research/              # orchestrator, debate, scenarios, learning,
│       │                          #   knowledge, thesis store, notifications
│       ├── synthesis/             # deterministic template + optional Claude
│       ├── portfolio.py  favorites.py
│       ├── backup.py              # nightly verified snapshot, shipped off-host
│       ├── digest.py  alerts.py  push.py  telegram.py  refresh.py  views.py
│       └── investment_analysis.py # explainable composition for /api/analysis/*
├── webapp_flutter/                # the compiled PWA that ships (source: flutter_app/)
├── tests/
├── Dockerfile  docker-compose.yml  requirements.txt  pyproject.toml
└── stock_alert.py                 # backward-compatible wrapper around the CLI
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
MIN_OPPORTUNITY_SCORE=80
```

Copy `.env.example` to `.env` for local Docker or shell use.

## Authentication

The platform holds real holdings, buy prices and P/L. **Every route is private by
default** — an allowlist in [services/auth.py](moroccan_stock_intelligence/services/auth.py)
names the only four exceptions (`/api/health`, `/api/auth/login`, `/api/auth/logout`,
`/api/auth/status` + its `/session` alias). A route added next month is private
without anyone remembering to protect it; you have to *un*-protect it on purpose.

```bash
AUTH_PASSWORD=une-phrase-de-passe-longue   # >= 12 characters, no default
AUTH_SESSION_DAYS=30
AUTH_COOKIE_SECURE=true                    # false only for local http testing
AUTH_MAX_ATTEMPTS=5
AUTH_LOCKOUT_SECONDS=300
```

**There is no default password, and that is deliberate.** With `AUTH_PASSWORD`
unset or shorter than 12 characters, protected routes answer **503**, not 200 — an
auth layer that disables itself on a missing environment variable would silently
reintroduce the exact problem it exists to fix. `/api/health` keeps answering so
the platform can still see the container.

- The session is a signed, stateless cookie (`HttpOnly`, `Secure`, `SameSite=Strict`).
  No session table, no Redis.
- The signing key is derived from `AUTH_PASSWORD`, so **changing it invalidates
  every live session** — rotation that left old cookies working would not be
  rotation.
- The cookie is `HttpOnly`, so the app's own JavaScript cannot read it either.
  Nothing secret is ever compiled into the frontend bundle.

Rate limiting sits in front of the expensive routes (`/api/refresh`,
`/api/run-now`, `/api/push/test`, `/api/report/{symbol}?fresh=true`). It is a
fixed-window counter held in memory — per container, lost on restart. Both limits
are real and deliberate: this is a cost guard, and the security boundary is the
session cookie, not the counter.

## Backtest

Answers the only question that matters about a scoring engine: *do the scores
predict anything?*

```bash
python -m moroccan_stock_intelligence.cli backtest \
  --horizons short,medium \
  --step 10 \
  --output reports/backtest-2026-07.json
```

It walks forward through the collected history: pick a past date, rebuild the
metrics from **only** the rows visible on that date, score, record the
recommendation, then measure what actually happened 10/60/180 séances later
against an equal-weighted benchmark.

**What it found** on 53 symbols × 741 séances (`reports/backtest-2026-07.json`):

| Horizon | Score band | N | Mean return (net) | vs benchmark |
|---|---|---:|---:|---:|
| medium | 0-45 | 1247 | +2.75% | +0.29% |
| medium | 45-55 | 488 | +2.87% | +0.49% |
| medium | 55-70 | 770 | +5.30% | +2.74% |
| medium | **70-100** | 763 | **+11.63%** | **+8.52%** |
| short | 70-100 | 45 | +4.35% | +3.66% |

The medium horizon is monotonic across bands and its top band's confidence
interval is disjoint from the neutral band's. The short horizon is monotonic too,
but its top band has 45 observations and an interval spanning zero — **nothing is
established there.**

The ablation (`reports/ablation-2026-07.json`) is more useful still:

| Variant | Spread 70+ vs 45-55 | Δ vs reference |
|---|---:|---:|
| reference | 6.13 | — |
| without `tendance` | −0.13 | **−6.26** |
| without `secteur` | 3.43 | −2.70 |
| without `volatilite` | 4.81 | −1.32 |
| without `actualites` | 6.25 | +0.12 |
| without `moyennes_mobiles` | 12.03 | **+5.90** |

Read plainly: **trend carries essentially all of the signal**, news contributes
nothing measurable, and the moving-average component — 25% of the medium score —
appears to be working *against* it.

**Do not over-read any of this.** Forward windows overlap, so the observations are
correlated and the reported intervals are optimistic; the sample covers roughly
three years of a broadly rising market, so a high-scoring band may be capturing
beta rather than skill; the benchmark is the equal-weighted proxy, not a real
MASI; and news and fundamentals are excluded entirely because neither carries a
usable publication date, which means **this validates the technical core only**.
The module states all of this in its own output.

## Data health

```bash
python -m moroccan_stock_intelligence.cli data-health   # exits 1 if a feed is empty or stale
```

Reports rows, freshness and coverage per feed, judged against each feed's own
cadence — and names the analysts that are running blind rather than leaving you to
infer it from a report full of "données non collectées". Also served, privately,
at `GET /api/admin/system-status`.

This exists because a scraper that returns 200 with an empty list looks exactly
like one that works.

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
- `send-alerts`: manual retry for alerts whose Telegram send failed (they stay `sent=0`). Nothing
  schedules it; live alerts send immediately and mark themselves sent.

Diagnostics and maintenance:

```bash
python -m moroccan_stock_intelligence.cli data-health          # which feeds are blind or stale
python -m moroccan_stock_intelligence.cli backtest --help      # walk-forward validation
python -m moroccan_stock_intelligence.cli backup               # snapshot + ship off-host
python -m moroccan_stock_intelligence.cli restore-backup       # recover from a snapshot
python -m moroccan_stock_intelligence.cli migrate --to head    # apply pending migrations
python -m moroccan_stock_intelligence.cli migrate-status
python -m moroccan_stock_intelligence.cli reclassify-news      # dry run by default
python -m moroccan_stock_intelligence.cli backfill-history     # seed ~3y, self-healing
```

`data-health` and `backup` both exit non-zero on failure, so either can gate a
deployment or a destructive operation.

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

### Restoring

```bash
python -m moroccan_stock_intelligence.cli restore-backup                     # newest snapshot
python -m moroccan_stock_intelligence.cli restore-backup path/to/snap.db.gz
python -m moroccan_stock_intelligence.cli restore-backup --yes               # scripted recovery
```

The order of operations is the point, and it is what the tests in
[tests/test_restore.py](tests/test_restore.py) pin:

1. decompress to a staging file;
2. **integrity-check that copy** — a corrupt archive is discovered while the live
   database is still the live database;
3. copy the current database aside, timestamped (a restore is itself a change you
   can regret);
4. `os.replace` — atomic, so there is no instant where the database is missing or
   half-written.

Restoring by hand still works (`gunzip` the archive over `data/market.db`), but it
skips every one of those steps.

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

## Building the frontend

**The bundle is compiled from source by the Docker build.** `webapp_flutter/` is
what the server mounts; `flutter_app/lib/` is the source. Those two drifting apart
is how you ship a backend that requires a login alongside a frontend that has no
login screen — so the image now builds the bundle in a stage of its own, and the
committed copy is a fallback that CI keeps honest.

To rebuild it locally (needed whenever you change `flutter_app/lib/` and want the
committed bundle to match):

```bash
scripts/flutter-docker.sh analyze --no-fatal-infos
scripts/flutter-docker.sh test
scripts/flutter-docker.sh build web --release

rm -rf webapp_flutter && cp -r flutter_app/build/web webapp_flutter
scripts/verify-bundle.sh          # rebuilds and compares hashes
```

No local Flutter SDK is required — the script runs `ghcr.io/cirruslabs/flutter:stable`.

**Behind a TLS-inspecting proxy** (the maintainer's network re-signs certificates),
containers cannot reach pub.dev or PyPI. Export the corporate CA once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/export-corporate-ca.ps1
```

`scripts/flutter-docker.sh` picks it up from `/c/tmp/ca/corporate-ca.crt`
automatically. `docker build` reads it from `ci/certs/`:

```bash
cp /c/tmp/ca/corporate-ca.crt ci/certs/     # gitignored; see ci/certs/README.md
docker build -t bourse .
```

A build secret would be the better mechanism, and was used first — but Railway's
builder supports only `type=cache` mounts and rejects the Dockerfile outright. The
consequence is worth knowing: a **locally**-built image now contains the
certificate, where a secret-mounted one did not. Railway builds from git and
`*.crt` is gitignored, so the deployed image never does.

Certificate verification is never disabled. On a machine without such a proxy the
directory is empty and everything works unchanged.

## Docker

Collector:

```bash
docker compose run --rm collector
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

> Favorites replaced the old `config/watchlist.json`, which was removed along with the Streamlit
> dashboard — it was that page's only consumer and drove none of the alerts or digests.

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

> The weighting listed here previously (25% momentum / 20% volume / 20% valuation
> / …) described a second, independent engine that no longer exists. It was merged
> into the horizon kernel in `6a208b2` after a comparison found the two disagreed
> on 71 of 80 symbols.

There is **one** scoring kernel, [horizon_strategy.py](moroccan_stock_intelligence/services/horizon_strategy.py),
producing three scores per stock. `buy_score` on the Opportunités tab is the
short-horizon score; `avoid_score` is the risk score.

| Horizon | Weights |
|---|---|
| **short** (days–2 weeks) | 30% momentum (1j/5j) · 20% volume · 20% breakout · 15% support · 15% news |
| **medium** (1–3 months) | 35% trend (30j/90j) · 25% moving averages · 15% sector · 15% inverse volatility · 10% news |
| **long** (6 months+) | 25% long trend · 20% stability · 15% 52-week structure · 10% sector · 10% events · **20% fundamentals** |

Three properties matter more than the weights:

- **Only available components are scored.** A missing metric lowers *coverage* —
  it is never replaced by a neutral 50.
- **Low coverage shrinks the score toward neutral**: `50 + (score−50) × min(1, coverage/0.8)`.
  It is structurally impossible to display a strong conviction built on thin data.
- **Confidence measures data, not correctness**: 50% coverage + 30% history depth
  + 20% signal agreement, capped at 35 when coverage is under half. It is *not* a
  probability that the call is right, and since the learning-semantics fix it is
  no longer treated as one.

One recommendation policy decides every verdict
([recommendation_policy.py](moroccan_stock_intelligence/services/recommendation_policy.py)),
so the Opportunités tab and the research report cannot disagree. Where they *look*
like they disagree — "Acheter" on one screen, "Conserver" on another — it is
because they answer different questions, and the API now returns the `perspective`
field saying which.

**Empirically**, the medium-horizon score ranks stocks in a way that correlated
with subsequent returns on the available history, the short horizon does not, and
the moving-average component may be counter-productive. See [Backtest](#backtest)
— including its limitations, which are substantial.

Windowed indicators are reported only with the history their name implies, so on a
newly listed symbol MA200 and the 52-week range are *absent* rather than
approximated from six weeks of data.

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

Frontend (no local Flutter SDK required):

```bash
scripts/flutter-docker.sh analyze --no-fatal-infos
scripts/flutter-docker.sh test
```

**644 Python tests + 23 Flutter widget tests, 79% line coverage** (measured with
`pytest-cov`, not estimated — and not the same thing as a pass rate).

Testing strategy:

- Parser tests for Moroccan number formats and Casablanca Bourse table extraction.
- Scoring tests for bounded outputs and explanations.
- **Boundary matrices** wherever a threshold ladder decides something
  (44/45, 54/55, 69/70, confidence 49/50, risk 64/65): off-by-one at a comparison
  operator is that code's failure mode and is invisible to review.
- **Anti-leakage tests** for the backtest. Two symbols with identical pasts and
  different futures must score identically before the divergence *and* differently
  after it — a leak detector that cannot see the signal it hunts for detects
  nothing.
- **Recovery tests** that destroy a database and restore it, comparing row by row.
  A backup nobody has restored is an assumption.
- The suite blocks outbound network calls (`conftest.no_outbound_network`); an
  earlier version scraped casablanca-bourse.com for real on every run.
- Add regression fixtures whenever a source changes HTML shape.
- Add integration tests using saved HTML fixtures before relying on new data sources.

Not covered, stated plainly: `synthesis/claude.py` (0% — the LLM path is disabled
and untested), `collectors/issuers.py` and `collectors/company.py` (0% — they feed
two analysts and would fail silently on a site redesign).

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

Shipped since this list was written (kept visible so the roadmap is not read as a
list of things that are still missing):

- ~~Alembic migrations.~~ — `migrations/`, applied with `cli migrate`.
- ~~Backtesting module for signal quality.~~ — `cli backtest`, see [Backtest](#backtest).
- ~~Fundamentals and valuation ratios where public data is available.~~ — the six
  published ratios are collected and now carry 20% of the long-horizon score.
- ~~Role-based dashboard auth.~~ — single-owner password auth; role-based access
  would need a real user model (see below).

Still open, in rough order of value:

- **Sector-relative fundamentals.** The valuation bands are absolute today. A PER
  of 20 means different things for a bank and a telecom, and doing it properly
  needs the cross-section computed in `gather()` rather than per symbol.
- **PDF text extraction for official notices.** Only the notice *title* is read;
  the linked PDF is never downloaded. The backtest's ablation suggests news
  currently contributes nothing measurable, so this is the most likely way to
  make it contribute something.
- **A real MASI/MSI20 feed.** The index is an equal-weighted proxy of tracked
  constituents, which over-weights small caps against a real capitalisation-weighted
  index. Labelled as a proxy everywhere it appears.
- **Re-examine the moving-average component.** The ablation found that removing it
  *improved* the medium-horizon spread by 5.9 points on the available history —
  see [Backtest](#backtest). One sample, overlapping windows, one market regime,
  so this is a lead to investigate rather than a conclusion.
- PostgreSQL production profile with `psycopg` (the path is proven by test).
- More news sources and RSS/media adapters.
- Telegram command bot for on-demand stock lookup.
- Multi-user support — a genuine schema change, not a feature flag: portfolio,
  favorites and push subscriptions all assume a single owner.
