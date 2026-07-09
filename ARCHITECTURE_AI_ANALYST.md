# Architecture — AI Investment Analyst (Casablanca Stock Exchange)

> **Status: DESIGN — awaiting validation. No implementation until this is approved.**
> Audience: the engineer/AI who will build it. Read `HANDOVER.md` first (current system),
> then this (target system). This document *extends* the live platform — it does not rewrite it.

---

## 0. Decisions locked (from the owner)

| # | Decision | Choice | Consequence for this design |
|---|----------|--------|------------------------------|
| 1 | LLM final-report layer | **Pluggable, deferred-on by flag** | Analysts *always* emit structured JSON. A `Synthesizer` interface has a deterministic template default (offline, free) and an optional `ClaudeSynthesizer` that activates only when `ANTHROPIC_API_KEY` is set. The LLM only *synthesizes* the JSON; it may never introduce a fact. |
| 2 | Data sources for data-less analysts | **Build the new collectors now** | New collectors land this program: `fundamentals`, `macro` (Bank Al-Maghrib / HCP), `company_profiles`. Until a feed is populated the relevant analyst outputs `data unavailable` honestly — it never fabricates numbers. |
| 3 | First implementation slice | **Analyst architecture first** | Phase 1 order is fixed: **(1) ResearchContext → (2) Analyst JSON contracts → (3) Orchestrator → (4) the 10 analysts → (5) Risk Manager → (6) CIO.** Research DB, Learning engine, Flutter terminal, and LLM synthesis are *later* phases. |

---

## 1. What we are building (in one paragraph)

We convert the current single scoring engine into a **team of independent analysts** coordinated by
a **Chief Investment Officer**. Each analyst has exactly one responsibility, reads a shared,
pre-assembled `ResearchContext` (the "one hour of research"), and returns a **structured JSON report**
of observations, strengths, weaknesses, confidence, and missing information. **No analyst is allowed to
recommend.** The CIO consumes every report, surfaces contradictions, reasons in probabilities across
three horizons, and writes the only recommendation — as a full investment thesis, with the evidence,
the counter-case, the confidence, the conditions that would invalidate it, and what to watch next.
Everything is reproducible and, later, graded against reality so the system calibrates itself.

---

## 2. Non-negotiable principles (enforced by the architecture, not by good intentions)

1. **Never fabricate.** If a metric is absent it goes into `missing_data`; it is never replaced by a
   guess. (v1 already does this in `horizon_strategy.py` — we keep the discipline.)
2. **Structured JSON first.** Every analyst returns a typed, serialisable report. The LLM sees only
   that JSON and may not add facts. This is a hard contract, not a convention (§8 guardrails).
3. **Only the CIO recommends.** The `AnalystReport` schema has *no* recommendation field. Only
   `CIOReport` carries a verdict. It is structurally impossible for an analyst to "decide".
4. **Probabilities, not predictions.** We never predict a price. We estimate scenario probabilities,
   each with its own confidence.
5. **Everything is explainable.** Every conclusion carries: evidence used, positive evidence, negative
   evidence, missing evidence, confidence, reasoning, counter-arguments, invalidation conditions,
   events to watch — and cites which module produced it.
6. **Facts vs inference vs opinion are labelled.** Each statement is tagged `fact | inference | opinion`
   so the reader (and the LLM) can never blur them.
7. **Extend, don't rewrite.** `analytics.py`, `scoring.py`, `horizon_strategy.py`, `portfolio.py`,
   `news.py`, the scrapers, the scheduler, the DB, and the existing `/api/analysis/*` endpoints stay.
   The proven per-horizon scoring math is *reused* inside the new analysts, not thrown away.
8. **Single-user, single-container, cheap.** SQLite + one Railway process stays the default. No new
   hard external dependency is added (the LLM is opt-in; collectors degrade gracefully).

---

## 3. Vision → module map (the 10 agents become 10 modules)

| Agent (owner's spec) | Module | Scope | Data it needs | Available today? |
|---|---|---|---|---|
| 1 Technical Analyst | `services/analysts/technical.py` | per-symbol | prices, MAs, RSI/MACD/Bollinger, support/resistance, volume, patterns | **Yes** (from `MetricSet`; add RSI/MACD/Bollinger/candlesticks) |
| 2 Market Structure | `services/analysts/market_structure.py` | per-symbol | market breadth, MSI20 proxy, sector rotation, relative strength, liquidity | **Yes** (compute a market/sector read-model) |
| 3 Company Analyst | `services/analysts/company.py` | per-symbol | business model, products, governance, ownership, capital actions | **Partial** → new `company_profiles` collector; honest-unavailable until populated |
| 4 Fundamental Analyst | `services/analysts/fundamental.py` | per-symbol | revenue, EPS, margins, debt, ROE, PER, PBR, yield | **New** → `fundamentals` collector; honest-unavailable until populated |
| 5 News Analyst | `services/analysts/news_analyst.py` | per-symbol | full news history, clustering, priced-in reasoning | **Yes** (extend existing `news` table + `NewsContext`) |
| 6 Historical Behaviour | `services/analysts/historical_behaviour.py` | per-symbol | event studies: behaviour after earnings/crashes/volume spikes | **Yes** (from `prices` + `signals` history) |
| 7 Macroeconomic | `services/analysts/macro.py` | market-wide (applied per-symbol via sector) | policy rate, inflation, FX, oil, phosphate | **New** → `macro` collector (Bank Al-Maghrib / HCP) |
| 8 Portfolio Analyst | `services/analysts/portfolio_analyst.py` | **portfolio-level** | holdings, concentration, correlation, drawdown, cash | **Yes** (extend `portfolio.py`) |
| 9 Risk Manager | `services/analysts/risk_manager.py` | aggregator | all analyst reports + metrics | **Yes** (generalise `compute_risk`) |
| 10 Chief Investment Officer | `services/analysts/cio.py` | aggregator (**only recommender**) | all reports + risk | **Yes** (generalise `_recommend` + `compose_analysis`) |

> Two **scopes** exist: *per-symbol* analysts (1–7, 9) run once per stock; the *portfolio-level*
> analyst (8) runs once over all holdings; the CIO runs per-symbol but is *handed* the portfolio
> analyst's output so it can weigh "you are already overexposed to banks".

---

## 4. The reasoning engine

### 4.0 Directory layout (new + changed)

```
moroccan_stock_intelligence/
├── services/
│   ├── analytics.py            (kept; add RSI/MACD/Bollinger to MetricSet)
│   ├── scoring.py              (kept; legacy 3-way score still powers /api/stocks)
│   ├── horizon_strategy.py     (kept; its _aggregate/confidence math becomes the CIO's kernel)
│   ├── investment_analysis.py  (kept as a THIN ADAPTER over the new engine for back-compat)
│   ├── portfolio.py            (extended: concentration/correlation helpers)
│   ├── news.py                 (extended: clustering key)
│   ├── research/
│   │   ├── context.py          # (1) ResearchContext + build_context()/build_market_context()
│   │   ├── contracts.py        # (2) AnalystReport, Statement, HorizonSignal, RiskReport, CIOReport, InvestmentReport
│   │   └── orchestrator.py     # (3) run analysts → risk → CIO, fault-isolated, registry
│   ├── analysts/               # (4)(5)(6) one file per analyst
│   │   ├── base.py             # Analyst protocol + register()
│   │   ├── technical.py
│   │   ├── market_structure.py
│   │   ├── company.py
│   │   ├── fundamental.py
│   │   ├── news_analyst.py
│   │   ├── historical_behaviour.py
│   │   ├── macro.py
│   │   ├── portfolio_analyst.py
│   │   ├── risk_manager.py
│   │   └── cio.py
│   ├── collectors/             # new data (Decision 2)
│   │   ├── fundamentals.py
│   │   ├── macro.py
│   │   └── company.py
│   └── synthesis/              # later phase (Decision 1)
│       ├── base.py             # Synthesizer protocol
│       ├── template.py         # deterministic default (offline, free)
│       └── claude.py           # optional, activates only if ANTHROPIC_API_KEY set
```

### 4.1 `ResearchContext` — the shared read-model (PRIORITY 1)

The single immutable bundle every analyst reads. Built **once per symbol per run** so no analyst
re-queries the DB and market aggregates are computed once (this also kills the current
"recompute-everything-per-request" performance debt). Frozen dataclass; trivially serialisable for
the research DB later.

```python
@dataclass(frozen=True)
class MarketContext:
    """Computed once per run, shared by every symbol's context."""
    as_of: datetime
    tracked: int
    regime: str                       # haussier | baissier | neutre | indéterminé
    breadth_above_ma50_pct: float | None
    advancers: int
    decliners: int
    avg_momentum_30d: float | None
    msi20_proxy_momentum: dict[str, float | None]   # {"5d":..,"30d":..} — index proxy vs which we measure relative strength
    sector_strength: dict[str, float]               # sector -> mean 30d momentum
    sector_rank: dict[str, int]                     # sector -> rank (rotation)
    macro: "MacroSnapshot | None"                   # None until the macro collector runs

@dataclass(frozen=True)
class ResearchContext:
    # identity
    symbol: str
    company_name: str
    sector: str | None
    as_of: datetime
    # market intelligence (existing engines, reused verbatim)
    metric: MetricSet                 # analytics.py output (price, momenta, MAs, vol, support…)
    history_days: int                 # depth of collected history (drives confidence caps)
    price_history: list[tuple[datetime, float]]   # for event studies / charts
    # news
    news: NewsContext                 # aggregated (existing dataclass, extended)
    news_items: list[NewsItem]        # raw, for clustering + timeline
    # portfolio
    holding: HoldingEvaluation | None # this stock's position, if held
    portfolio: Portfolio              # whole portfolio (for the portfolio analyst / CIO)
    # NEW feeds (Decision 2) — None-safe; analysts degrade honestly
    fundamentals: Fundamentals | None
    company_profile: CompanyProfile | None
    # market-wide
    market: MarketContext
```

**Builders** (`research/context.py`):
- `build_market_context(session) -> MarketContext` — one pass over all metrics (regime, breadth,
  MSI20 proxy, sector strength/rank, attaches the latest `MacroSnapshot`).
- `build_context(session, symbol, market, gathered) -> ResearchContext` — assembles one stock's
  bundle from already-loaded state (no per-symbol DB round-trips).
- `gather(session)` (evolves the existing `_gather`) — loads metrics, scores, holdings, depths,
  news, fundamentals, profiles **once**, returns them for reuse across all symbols.

> **Why first:** every later piece (contracts, orchestrator, analysts, research DB, LLM) consumes a
> `ResearchContext`. Nailing this schema first means the rest is plumbing.

### 4.2 Analyst JSON contracts (PRIORITY 2)

`research/contracts.py`. The heart of the "structured JSON, LLM only synthesizes" rule.

```python
Kind = Literal["fact", "inference", "opinion"]        # every statement is labelled
Polarity = Literal["bullish", "bearish", "neutral"]

@dataclass(frozen=True)
class Statement:
    text: str                       # French, human-readable
    kind: Kind                      # fact | inference | opinion
    polarity: Polarity
    weight: float                   # 0..1 how much this analyst leans on it
    evidence: dict                  # the raw numbers behind it (e.g. {"momentum_30d": -6.2})

@dataclass(frozen=True)
class HorizonSignal:
    """An analyst's numeric contribution to one horizon, per component.
    Fed to the CIO's aggregation kernel (reuses horizon_strategy._aggregate)."""
    horizon: Literal["short", "medium", "long"]
    components: dict[str, float | None]     # e.g. {"momentum_court": 71.0, "volume": None}
    weights: dict[str, float]

@dataclass(frozen=True)
class Scenario:
    name: str                       # "poursuite de la tendance" / "correction" / "range" …
    probability: float              # 0..1
    confidence: float               # 0..100 — how sure we are of THAT probability
    rationale: str

@dataclass(frozen=True)
class AnalystReport:
    analyst: str                    # "technical" | "news" | …  (module id)
    version: str                    # "1.0" — bump when logic changes (research DB reproducibility)
    scope: Literal["symbol", "portfolio", "market"]
    observations: list[Statement]   # neutral facts
    strengths: list[Statement]      # bullish factors
    weaknesses: list[Statement]     # bearish factors
    horizon_signals: list[HorizonSignal]   # numeric, per horizon (may be empty)
    scenarios: list[Scenario]       # where this analyst can estimate; else []
    risk_flags: list[Statement]     # things the risk manager should weigh
    confidence: float               # 0..100 (coverage + data depth + internal agreement)
    data_used: list[str]
    missing_data: list[str]
    notes: list[str]
    # NOTE: there is deliberately NO `recommendation` field.
```

```python
@dataclass(frozen=True)
class RiskReport:              # Risk Manager (Agent 9) output
    overall_risk: float                 # 0..100 (higher = riskier)
    confidence: float
    dimensions: dict[str, float]        # technical/fundamental/liquidity/event/valuation/portfolio
    worst_case: Scenario
    base_case: Scenario
    best_case: Scenario
    drivers: list[Statement]
    missing_data: list[str]

@dataclass(frozen=True)
class HorizonVerdict:
    horizon: str
    recommendation: str                 # STRONG_OPPORTUNITY | WATCH | HOLD | TAKE_PROFIT | AVOID | RISKY
    score: float                        # aggregated 0..100
    confidence: float
    rationale: str
    invalidation: list[str]             # what would change this opinion
    watch_next: list[str]

@dataclass(frozen=True)
class CIOReport:              # Agent 10 — the ONLY recommender
    symbol: str
    verdicts: dict[str, HorizonVerdict]  # short/medium/long — may differ
    contradictions: list[str]            # e.g. "Technique haussière vs actus baissières"
    bull_case: list[Statement]
    bear_case: list[Statement]
    executive_summary: str
    final_verdict: str

@dataclass(frozen=True)
class InvestmentReport:      # what the API returns / research DB stores
    symbol: str
    company_name: str
    as_of: datetime
    horizon_focus: str
    cio: CIOReport
    risk: RiskReport
    analysts: dict[str, AnalystReport]   # every analyst's raw JSON (explainability drill-down)
    scenarios: list[Scenario]            # consolidated
    narrative: str | None                # filled by the Synthesizer (template or Claude); None = not yet
    engine_version: str
    disclaimer: str
```

The API payload is `InvestmentReport` → `asdict()`. Backward compatibility: the current
`/api/analysis/{symbol}` fields (`recommendation`, `confidence`, `bullish`, `bearish`,
`explainability`, …) are re-derived from `CIOReport`/`RiskReport` by the adapter so the live Flutter
tab keeps working through the refactor.

### 4.3 Orchestrator (PRIORITY 3)

`research/orchestrator.py`. Turns a context into an `InvestmentReport`.

```python
def analyze(session, symbol, horizon="short") -> InvestmentReport | None:
    gathered = gather(session)                       # load-once
    market   = build_market_context(session, gathered)
    ctx      = build_context(session, symbol, market, gathered)
    if ctx is None: return None
    reports  = run_symbol_analysts(ctx)              # agents 1-7 (fault-isolated)
    reports["portfolio"] = portfolio_report_for(ctx, gathered)   # agent 8 (portfolio scope)
    risk     = risk_manager.assess(ctx, reports)     # agent 9
    cio      = cio.decide(ctx, reports, risk, horizon)   # agent 10 — only recommender
    return assemble_report(ctx, reports, risk, cio, horizon)
```

Rules:
- **Fault isolation.** Each analyst runs inside try/except (mirrors the "jobs never raise"
  convention). A failure yields a *degraded* `AnalystReport` (`confidence=0`, a `notes` entry naming
  the error, everything else empty) — the report is still produced. One broken analyst never sinks
  the ship.
- **Registry.** `analysts/base.py` exposes `@register("technical")`; the orchestrator iterates the
  registry. Adding/removing an analyst is one decorator. An ML model later is *just another
  registered analyst* — same `analyze(ctx) -> AnalystReport` signature.
- **Determinism & order.** Analysts are pure functions of `ctx`; execution order is fixed for
  reproducibility. They are independent → trivially parallelisable later (thread pool), but start
  **sequential** (~80 symbols, SQLite).
- **Aggregation kernel reuse.** The CIO feeds every analyst's `HorizonSignal.components/weights` into
  the *existing, tested* `horizon_strategy._aggregate` + `compute_confidence` — the coverage-shrink,
  neutral-fallback, and confidence-cap logic is not rewritten.

### 4.4 The ten analysts (responsibility · inputs · output · availability)

Each returns an `AnalystReport`. Below, "signals" = the `HorizonSignal` components it contributes.

**1 · Technical** (`technical.py`) — *have data.*
Reads `ctx.metric` + `ctx.price_history`. Emits: momentum (1/5/30/90), MA structure (20/50/200),
volatility, volume anomaly/confirmation, support/resistance, 52-week structure, breakout quality &
false-breakout check, **plus new** RSI, MACD, Bollinger position, and basic candlestick/pattern flags
(added to `MetricSet`). Signals feed short & medium & long. Scenarios: continuation vs correction vs
range with probabilities. This is where most of today's `horizon_strategy` component code moves.

**2 · Market Structure** (`market_structure.py`) — *have data.*
Reads `ctx.market`. Relative strength vs the **MSI20 proxy** (index proxy = cap-weighted mean of
tracked constituents until a real index feed exists — declared as `inference`), sector rotation
(`sector_rank`), correlation/relative performance, liquidity (volume vs market). Determines
out/under-performance vs sector. Signals: sector + relative-strength components for medium/long.

**3 · Company** (`company.py`) — *new data; honest-unavailable until populated.*
Reads `ctx.company_profile`. Business model, products, competitors, governance/ownership changes,
capital increases, dividend policy. When `company_profile is None` → one `missing_data` entry
("Profil société non collecté"), `confidence≈0`, no fabricated text.

**4 · Fundamental** (`fundamental.py`) — *new data; honest-unavailable until populated.*
Reads `ctx.fundamentals`. Revenue, EPS, margins, cash flow, debt, ROE/ROA, PER, PBR, yield, EV, book
value, growth. Missing → `"Fondamentaux non collectés"` in `missing_data`. **Never invents numbers.**
Signals feed medium/long valuation components when present.

**5 · News** (`news_analyst.py`) — *have data.*
Reads `ctx.news` + `ctx.news_items` (full history, not just today). Per item: polarity, importance,
urgency, affected horizon, expected impact, **priced-in?** (did the stock already move?), **ignored?**.
**Clusters** similar items (dedup reasoning) via a normalised clustering key. Cross-references history
("similar news historically moved this stock ±x%"). Signals: `actualites` for all horizons.

**6 · Historical Behaviour** (`historical_behaviour.py`) — *have data.*
Event studies over `ctx.price_history` + `signals` table: distribution of forward returns after
earnings / capital actions / large drops / volume spikes / dividend announcements. Answers "does
history suggest recovery or continuation?" as **probabilities with confidence**, never certainty.
Confidence scales with the number of past occurrences (few events → low confidence, stated plainly).

**7 · Macro** (`macro.py`) — *new data; honest-unavailable until populated.*
Reads `ctx.market.macro`. Policy rate, inflation, FX, oil, phosphate, reforms. Maps macro → sector
sensitivity (e.g. rate cut → banks/real-estate). Market-scope; applied per-symbol via sector. Missing
feed → honest-unavailable.

**8 · Portfolio** (`portfolio_analyst.py`) — *have data; PORTFOLIO scope.*
Reads `ctx.portfolio` + all holdings' metrics. Sector concentration, position sizing/overweight,
correlation & diversification, cash allocation, aggregate drawdown exposure. Suggests
reduce/increase/hold/diversify/wait **as observations for the CIO** (still not a per-stock order).

**9 · Risk Manager** (`risk_manager.py`) → `RiskReport`. See §4.5.

**10 · CIO** (`cio.py`) → `CIOReport`. See §4.6.

### 4.5 Risk Manager (Agent 9, PRIORITY 5)

Aggregator, not a data reader. Consumes `ctx` + all `AnalystReport`s. Generalises the existing
`compute_risk`. Produces `RiskReport`: an overall 0–100 risk, per-dimension breakdown (technical,
fundamental, liquidity, event, valuation, portfolio), a **base/best/worst-case scenario** each with
probability & confidence, the drivers (as labelled `Statement`s), and its own `missing_data`. It
harvests every analyst's `risk_flags`, so a bearish news item or a fundamental red flag automatically
raises risk — the wiring is the contract, not ad-hoc code.

### 4.6 Chief Investment Officer (Agent 10, PRIORITY 6) — the only recommender

`cio.py`. Consumes every `AnalystReport` + `RiskReport`. Steps:

1. **Aggregate per horizon.** Collect all analysts' `HorizonSignal`s, merge components, run the
   *existing* `horizon_strategy._aggregate` + `compute_confidence` kernel → a score & confidence per
   horizon. (Reuse, don't rewrite.)
2. **Detect contradictions.** Compare polarities across analysts (e.g. Technical bullish + News
   bearish + Fundamental neutral) → explicit `contradictions[]` strings.
3. **Decide per horizon.** Generalise `_recommend` into `HorizonVerdict` per short/medium/long — the
   recommendation **may differ by horizon** (e.g. short *Avoid*, long *Strong opportunity*), each
   with its rationale, invalidation conditions, and watch-next list.
4. **Write the thesis.** Assemble Executive Summary, Bull Case, Bear Case (each a list of labelled
   `Statement`s drawn from analysts, *cited by module*), and a Final Verdict — explaining *why* the
   verdict holds despite the contradictions.

The CIO's textual assembly is initially the deterministic `TemplateSynthesizer` (today's French
composition, upgraded). When the LLM is enabled it hands the **structured `CIOReport` + all analyst
JSON** to `ClaudeSynthesizer` for the prose — Claude may reword and connect, never add a fact (§8).

---

## 5. Data & collection layer (Decision 2 — build the collectors)

New collectors under `services/collectors/`, each isolated (a failure never blocks price collection),
each writing to its own table, each on its own cadence.

| Collector | Source(s) | Cadence | Writes | Analyst it feeds |
|---|---|---|---|---|
| `fundamentals.py` | Casablanca Bourse company sheets / broker research pages | **weekly** (fundamentals move slowly) | `fundamentals` | Fundamental (4) |
| `macro.py` | Bank Al-Maghrib (policy rate, FX), HCP (inflation), commodity feeds (oil, phosphate) | **daily/weekly** | `macro_indicators` | Macro (7) |
| `company.py` | Casablanca Bourse issuer pages / official profiles | **monthly** or on-change | `company_profiles` | Company (3) |

Design rules mirror the existing scraper base: browser-like headers, tenacity retries, the opt-in
insecure-SSL path, `raw_payload` stored for audit, idempotent upserts. **A collector that fails or
returns nothing leaves the feed empty → the analyst says "unavailable".** No placeholder numbers ever
reach the report. New scheduler jobs (weekday-aware) trigger them; they are *not* on the hot path of a
report request.

---

## 6. Database changes

`create_all` **creates new tables** idempotently at startup — so every table below lands with zero
migration risk *because they are all new tables* (the no-Alembic constraint only bites when ALTERing
existing tables, which we avoid). We add RSI/MACD/Bollinger to the computed `MetricSet` **in memory**
(no `prices` schema change). If a future change must ALTER an existing table, that is the moment to
introduce a minimal migration step — flagged, not silently assumed.

### New now (Phase 1b, with the collectors)
- **`fundamentals`** — `id, stock_id FK, as_of, per, pbr, eps, dividend_yield, roe, roa, net_margin,
  revenue, net_income, debt_to_equity, book_value, source, raw_payload, collected_at`. Unique
  `(stock_id, as_of, source)`.
- **`macro_indicators`** — `id, as_of, indicator (policy_rate|inflation|mad_usd|mad_eur|oil|phosphate),
  value, unit, source, collected_at`. Unique `(indicator, as_of, source)`.
- **`company_profiles`** — `id, stock_id FK, description, business_model, sector_detail, management,
  ownership, updated_at, source, raw_payload`. Unique `(stock_id)`.

### New later (Phase 2 — Research DB) and (Phase 3 — Learning)
- **`analysis_reports`** — `id, stock_id FK, generated_at, horizon_focus, engine_version,
  report_json (full InvestmentReport), narrative, cio_verdict_short/medium/long, confidence,
  risk_score`. Makes every report **reproducible** and doubles as the API cache.
- **`predictions`** — `id, report_id FK, stock_id, horizon, generated_at, evaluate_at, scenario,
  predicted_prob, predicted_direction, price_at_prediction`.
- **`prediction_outcomes`** — `id, prediction_id FK, evaluated_at, realized_return, realized_direction,
  hit (bool), brier_component`.
- **`analyst_performance`** — `id, analyst, horizon, window, sample_size, hit_rate, brier_score,
  calibration, updated_at`. Drives the learning engine's confidence calibration.

---

## 7. API surface

**Kept (back-compat, adapter-fed):** `/api/analysis/{symbol}`, `/api/analysis/market-summary`,
`/api/analysis/portfolio`, `/api/analysis/opportunities`. These keep their current shapes so the live
Flutter "Analyse IA" tab keeps working during the refactor.

**New (Phase 1 → later):**
| Endpoint | Returns | Phase |
|---|---|---|
| `GET /api/report/{symbol}?horizon=` | full `InvestmentReport` (all sections, all analysts) | 1 |
| `GET /api/report/{symbol}/analysts` | raw per-analyst JSON (explainability drill-down) | 1 |
| `GET /api/market/mood` | regime, breadth, advancers/decliners, MSI20 proxy, mood gauge | later |
| `GET /api/market/heatmap` | sector strength/rank grid | later |
| `GET /api/portfolio/health` | concentration, correlation, diversification, cash, risk radar | later |
| `GET /api/risks/top` / `GET /api/opportunities/top` | ranked highest-risk / best-opportunity across horizons | later |
| `GET /api/reports/history/{symbol}` | past reports (research DB) + recommendation timeline | Phase 2 |
| `GET /api/performance` | analyst accuracy / calibration (learning engine) | Phase 3 |
| `GET /api/report/{symbol}/narrative` | LLM-synthesized prose (or template) | Phase 4 |

All read endpoints stay `GET`, plain dicts, no auth (single-user model unchanged). Reports are served
from the research DB when present (fast) and recomputed on schedule — removing the current
per-request recompute cost.

---

## 8. Synthesizer / LLM integration (Decision 1 — pluggable, deferred-on by flag)

`services/synthesis/`. The analysts + CIO already produced *all facts as structured JSON*; the
Synthesizer only turns that JSON into prose.

```python
class Synthesizer(Protocol):
    def render(self, report: InvestmentReport) -> str: ...   # returns narrative markdown/French

# default — always available, offline, free, deterministic, reproducible
class TemplateSynthesizer:  ...

# optional — activates ONLY if settings.llm_provider == "anthropic" and ANTHROPIC_API_KEY is set
class ClaudeSynthesizer:
    model = settings.llm_model   # default "claude-opus-4-8"; "claude-haiku-4-5" for cost
```

**Guardrails (the "never invent" contract):**
- Claude receives **only** the serialized `InvestmentReport` JSON — never raw web text.
- System prompt: *"You may rephrase, connect, and explain the findings below. You may NOT introduce
  any number, fact, event, or entity not present in the JSON. Every claim must cite the analyst
  module (`cio`, `technical`, `news`…). Preserve `fact/inference/opinion` labels. Express uncertainty;
  never assert certainty."*
- Output is **validated**: numbers in the prose must appear in the JSON; on any mismatch or API error
  we **fall back to `TemplateSynthesizer`**. The report is never blocked on the LLM.
- Cost containment: synthesis runs **on report generation (scheduled) or on demand**, not per request;
  the narrative is stored in `analysis_reports.narrative` and cached. Model, provider, and
  on/off are env-driven (`LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`). Railway egress reaches the
  API fine — the corporate-TLS problem only ever affected *local Flutter builds*, not server runtime.

Future ML models plug in at the **analyst** layer (a model is a registered analyst emitting
`AnalystReport`), not here — keeping synthesis purely presentational.

---

## 9. Research Database + Learning Engine (later — Phases 2 & 3)

- **Research DB (Phase 2).** Every generated `InvestmentReport` is persisted (`analysis_reports`),
  making analyses reproducible and enabling a **recommendation timeline** per stock. The store also
  serves as the API cache.
- **Learning Engine (Phase 3).** A daily job compares each matured `prediction` to reality
  (`prediction_outcomes`), computes hit-rate and **Brier score** per analyst per horizon
  (`analyst_performance`), and **Bayesian-updates** each analyst's confidence weighting — analysts
  that prove reliable gain influence in the CIO aggregation; unreliable ones lose it. **No ML until
  enough labelled history exists** (the spec's rule); we start with statistical evaluation + Bayesian
  calibration, and the analyst-registry design lets ML models drop in later behind the same contract.

---

## 10. Flutter — AI investment terminal (later — Phase 4)

The single-file `main.dart` pattern stays (no state library). New/upgraded surfaces, all fed by the
new APIs, all with explainability + confidence + probabilistic language:

| Screen / widget | Source | Notes |
|---|---|---|
| AI Market Overview + **Market Mood** gauge | `/api/market/mood` | regime + breadth + advancers/decliners |
| **Sector Heatmap** | `/api/market/heatmap` | strength/rank grid, rotation arrows |
| **Portfolio Health** + **Risk Radar** | `/api/portfolio/health` | concentration/correlation/cash; radar of risk dimensions |
| Best Opportunities / Highest Risks | `/api/opportunities/top`, `/api/risks/top` | per horizon |
| **AI Report** view | `/api/report/{symbol}` | full thesis: Exec Summary, **Bull vs Bear** columns, Technical/Fundamental/News/Sector/Macro/Historical sections, Portfolio Impact |
| **Scenario Analysis** | report `scenarios` | probability gauges + confidence |
| **Timeline** | `/api/reports/history/{symbol}` | events + recommendation changes over time |
| Explainability cards / Confidence gauges | every payload's `explainability`/`confidence` | reused across screens |
| Recommendation tracking | `/api/performance` | how past calls actually did |

Rebuild discipline unchanged (the committed `webapp_flutter/` build — see HANDOVER §14; do not forget
the copy step or prod won't change).

---

## 11. Notifications (thesis-change semantics)

Extend the existing `dispatch_analysis_notifications` (already deduped, capped 3/run, push+inbox,
Telegram untouched to avoid the double-Telegram trap). Once the research DB exists, "notify" means
**"the stored thesis changed"**: horizon recommendation flipped, confidence dropped on new conflicting
info, news invalidated yesterday's thesis, breakout confirmed, portfolio risk rose, sector rotation
detected, dividend improved the long-term case. **Notify only on a thesis change — never on noise.**

---

## 12. End-to-end data flow

```
                  ┌── scrapers (prices) ──┐   ┌── news scraper ──┐
 COLLECT (cron) ──┤   fundamentals (wk)   ├──►│  macro (daily)   │──► SQLite
                  └── company (monthly) ──┘   └──────────────────┘
                                   │
 ANALYZE (cron / on-demand)        ▼
   gather(session)  ── load-once ─► build_market_context ─► for each symbol: build ResearchContext
                                                                     │
                                                 ┌───────────────────┴─── Orchestrator ───────────────┐
                                                 │  agents 1-7 (fault-isolated) → AnalystReport JSON   │
                                                 │  agent 8 portfolio (portfolio scope)                │
                                                 │  agent 9 Risk Manager → RiskReport                  │
                                                 │  agent 10 CIO → CIOReport (only recommender)        │
                                                 └───────────────────┬─────────────────────────────────┘
                                                                     ▼
                                             InvestmentReport (structured JSON)
                                          ┌──────────────┼───────────────┬─────────────────┐
                                          ▼              ▼               ▼                 ▼
                                  Synthesizer      research DB      thesis-change      API /api/report/*
                                (template|Claude)  (persist+cache)   notifications      → Flutter terminal
                                                        │
                                                        ▼  (daily)
                                              Learning engine: predictions vs outcomes
                                              → Brier/hit-rate → Bayesian confidence calibration
```

---

## 13. Scalability & performance

- **Load-once read-model** removes today's per-request recompute (each `/api/analysis/*` currently
  rebuilds metrics from the full price frame). Market aggregates computed once per run.
- **Reports served from the research DB** (materialised) — requests become table reads.
- **Analysts are independent pure functions** → parallelisable (thread pool) and, if ever needed,
  splittable into services. Start sequential (~80 symbols, SQLite single-writer).
- **LLM off the hot path** — synthesis at generation time, cached; bounded cost.
- **Unbounded `prices` growth** remains the known long-term issue (HANDOVER §12) → prune/aggregate or
  move to Postgres when history warrants; the read-model localises the fix.
- **Scheduler stays single-instance** (HANDOVER constraint); nothing here adds a second writer.

---

## 14. Implementation plan (phased; Phase 1 is your locked order)

**Phase 1 — Analyst architecture (do this first, in this order):**
1. `research/context.py` — `ResearchContext` + `MarketContext` + builders (`gather` evolves `_gather`).
2. `research/contracts.py` — `Statement`, `HorizonSignal`, `Scenario`, `AnalystReport`, `RiskReport`,
   `HorizonVerdict`, `CIOReport`, `InvestmentReport`.
3. `research/orchestrator.py` — registry, fault-isolated run, `assemble_report`.
4. `analysts/*` — the 10 analysts (start with the *have-data* ones: technical, market_structure,
   news, historical_behaviour, portfolio; company/fundamental/macro emit honest-unavailable).
5. `analysts/risk_manager.py`.
6. `analysts/cio.py` (reusing `horizon_strategy` kernel) + wire `/api/report/{symbol}` and re-point
   `investment_analysis.py` as a thin back-compat adapter.
   *Gate:* live `/api/analysis/*` + Flutter tab still work; every analyst emits valid JSON; CIO is the
   only recommender.

**Phase 1b — Collectors (in parallel, right after the skeleton):** `fundamentals`, `macro`,
`company` + their tables + scheduler jobs → the data-less analysts light up.

**Phase 2 — Research DB:** `analysis_reports` + persistence + report-from-store + history endpoints.

**Phase 3 — Learning engine:** `predictions`/`prediction_outcomes`/`analyst_performance` + daily
evaluation + Bayesian calibration.

**Phase 4 — LLM synthesis + Flutter AI terminal:** `synthesis/` (template then Claude behind flag) and
the new Flutter screens.

---

## 15. Risks & open questions to settle during validation

1. **MSI20 proxy.** No real index feed today → Market Structure uses a cap-weighted proxy, declared as
   `inference`. Acceptable, or should we source the official MASI/MSI20 first? (Recommend: proxy now,
   real feed as a later collector.)
2. **Fundamentals source reliability.** Casablanca Bourse company sheets are scrape-fragile. Confirm
   the source before Phase 1b, and accept `unavailable` gaps as normal.
3. **Report generation cost when analysts multiply.** ~80 symbols × 10 analysts per run — start
   sequential, measure, parallelise only if the scheduled run exceeds budget.
4. **`engine_version` / analyst `version` bumps.** Needed for research-DB reproducibility and to avoid
   comparing outcomes across changed logic — confirm the versioning discipline.
5. **LLM budget & model.** Default `claude-opus-4-8` for depth vs `claude-haiku-4-5` for cost — pick
   per run when Phase 4 lands.

---

**Validation checklist for the owner:** (a) module map §3, (b) `ResearchContext` schema §4.1,
(c) the `AnalystReport`/`CIOReport` contracts §4.2, (d) the collectors & new tables §5–6, (e) the
phase order §14. Approve or redirect these and implementation begins with `research/context.py`.
