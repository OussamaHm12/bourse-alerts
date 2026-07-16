"""ResearchContext — the shared read-model (Phase 1, PRIORITY 1).

Every analyst reads a single immutable :class:`ResearchContext`: the "one hour of
research materials" gathered once per run so no analyst re-queries the database and
market-wide aggregates are computed a single time (this also removes the current
recompute-everything-per-request cost).

The new data feeds (fundamentals, company profiles, macro) are wired here as
optional fields. Their collectors land in Phase 1b; until a feed is populated the
field is ``None`` and the owning analyst reports the data as unavailable — it never
fabricates numbers (locked decision #2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import (
    load_company_profiles,
    load_fundamentals,
    load_history_depths,
    load_latest_macro,
    load_recent_news,
    load_symbol_history,
)
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.collectors import DERIVED_SOURCE
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.news_classifier import event_family
from moroccan_stock_intelligence.services.portfolio import (
    HoldingEvaluation,
    Portfolio,
    evaluate_portfolio,
    load_portfolio,
)
from moroccan_stock_intelligence.services.scoring import ScoreResult
from moroccan_stock_intelligence.services.views import compute_state

NEWS_WINDOW_DAYS = 30
FRESH_HOURS = 24
HISTORY_POINTS = 365


# --------------------------------------------------------------------------- #
# Lightweight, detached views of the raw rows (kept serialisable, no ORM)      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NewsView:
    title: str
    url: str
    source: str
    published_at: datetime | None
    collected_at: datetime | None
    event_type: str | None
    sentiment: str | None
    impact_score: float | None


# --------------------------------------------------------------------------- #
# New data feeds (Phase 1b collectors populate these; None-safe until then)    #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Fundamentals:
    """The six ratios the Casablanca Bourse issuer page actually publishes.

    Revenue, net income, margins, ROA, debt/equity and book value are NOT
    published in machine-readable form (validated 2026-07-09), so they are
    absent here rather than present-and-always-None. `fundamental.py` names them
    in `missing_data` instead.

    `per_is_derived` marks a PER computed as price / BPA because the published
    cell was "-". Such a value must be presented as inference, never as fact.
    """

    fiscal_year: int | None = None
    eps: float | None = None  # BPA, MAD
    roe: float | None = None  # %
    payout: float | None = None  # %
    dividend_yield: float | None = None  # %
    per: float | None = None
    pbr: float | None = None
    per_is_derived: bool = False
    source: str | None = None
    source_url: str | None = None

    @property
    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (self.eps, self.roe, self.payout, self.dividend_yield, self.per, self.pbr)
        )


@dataclass(frozen=True)
class CompanyProfile:
    """Issuer identity. `description` is the published "Objet social".

    No business-model field is published, and the `Dirigeants` table layout is
    unconfirmed, so `business_model` and `management` stay None rather than being
    synthesised.
    """

    company_name: str | None = None
    description: str | None = None
    business_model: str | None = None
    siege_social: str | None = None
    commissaire_aux_comptes: str | None = None
    date_constitution: str | None = None
    date_introduction: str | None = None
    duree_exercice_social: str | None = None
    ownership: list[dict] | None = None  # [{"holder": .., "pct": ..}, ..]
    management: list[dict] | None = None
    updated_at: datetime | None = None
    source: str | None = None
    source_url: str | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.description or self.business_model or self.ownership)


@dataclass(frozen=True)
class MacroSnapshot:
    """Bank Al-Maghrib series. Oil and phosphate are not published by BAM and are
    permanently None — `macro.py` reports them as missing, never as zero."""

    as_of: datetime | None = None
    policy_rate: float | None = None  # %
    interbank_rate: float | None = None  # % (TMP)
    inflation: float | None = None  # %
    inflation_underlying: float | None = None  # %
    mad_eur: float | None = None
    mad_usd: float | None = None
    oil: float | None = None
    phosphate: float | None = None
    source: str | None = None

    @property
    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (
                self.policy_rate,
                self.interbank_rate,
                self.inflation,
                self.mad_eur,
                self.mad_usd,
            )
        )


# --------------------------------------------------------------------------- #
# Market-wide context (computed once per run, shared by every symbol)          #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MarketContext:
    as_of: datetime
    tracked: int
    regime: str  # haussier | baissier | neutre | indéterminé
    breadth_above_ma50_pct: float | None
    advancers: int
    decliners: int
    avg_momentum_30d: float | None
    # Equal-weighted index proxy until a real MASI/MSI20 feed exists (INFERENCE).
    msi20_proxy: dict[str, float | None]  # {"5d": .., "30d": ..}
    sector_strength: dict[str, float]  # sector -> mean 30d momentum
    sector_rank: dict[str, int]  # sector -> 1 = strongest
    macro: MacroSnapshot | None


# --------------------------------------------------------------------------- #
# The read-model                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResearchContext:
    symbol: str
    company_name: str
    sector: str | None
    as_of: datetime
    metric: MetricSet
    history_days: int
    price_history: list[tuple[datetime, float]]
    news: NewsContext
    news_items: list[NewsView]
    holding: HoldingEvaluation | None
    portfolio: Portfolio
    fundamentals: Fundamentals | None
    company_profile: CompanyProfile | None
    market: MarketContext


@dataclass(frozen=True)
class GatheredState:
    """Everything loaded once, reused across all symbols in a run."""

    metrics: list[MetricSet]
    metrics_by_symbol: dict[str, MetricSet]
    scores: dict[str, ScoreResult]
    holdings: dict[str, HoldingEvaluation]
    depths: dict[str, int]
    news_by_symbol: dict[str, list[NewsView]]
    news_contexts: dict[str, NewsContext]
    portfolio: Portfolio
    fundamentals: dict[str, Fundamentals]
    profiles: dict[str, CompanyProfile]
    macro: MacroSnapshot | None


# --------------------------------------------------------------------------- #
# Builders                                                                      #
# --------------------------------------------------------------------------- #

def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _build_news(session: Session) -> tuple[dict[str, list[NewsView]], dict[str, NewsContext]]:
    """Aggregate recent linked news per symbol (small table: filter in Python)."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=NEWS_WINDOW_DAYS)
    fresh_cutoff = now - timedelta(hours=FRESH_HOURS)
    grouped: dict[str, list[NewsView]] = {}
    for news, symbol in load_recent_news(session, limit=300):
        if symbol is None:
            continue
        when = _aware(news.collected_at)
        if when is not None and when < cutoff:
            continue
        grouped.setdefault(symbol, []).append(
            NewsView(
                title=news.title,
                url=news.url,
                source=news.source,
                published_at=_aware(news.published_at),
                collected_at=when,
                event_type=news.event_type,
                sentiment=news.sentiment,
                impact_score=news.impact_score,
            )
        )

    contexts: dict[str, NewsContext] = {}
    for symbol, items in grouped.items():
        impacts = [i.impact_score for i in items if i.impact_score is not None]
        latest = items[0]  # load_recent_news returns newest first
        contexts[symbol] = NewsContext(
            count=len(items),
            avg_impact=(sum(impacts) / len(impacts)) if impacts else None,
            positive=sum(1 for i in items if i.sentiment == "positive"),
            negative=sum(1 for i in items if i.sentiment == "negative"),
            latest_title=latest.title,
            latest_at=latest.collected_at,
            fresh_negative=any(
                i.sentiment == "negative" and i.collected_at is not None
                and i.collected_at >= fresh_cutoff
                for i in items
            ),
            has_dividend=any(event_family(i.event_type) == "dividend" for i in items),
            has_results=any(event_family(i.event_type) == "results" for i in items),
        )
    return grouped, contexts


# --------------------------------------------------------------------------- #
# Phase 1b feeds. A feed with no collected rows yields no entry, so the owning  #
# analyst emits its honest "unavailable" report. Nothing defaults to 0.        #
# --------------------------------------------------------------------------- #

# BKAM series name -> MacroSnapshot field.
_MACRO_FIELDS = {
    "policy_rate": "policy_rate",
    "interbank_money_market": "interbank_rate",
    "inflation_rate": "inflation",
    "inflation_underlying_rate": "inflation_underlying",
    "eur": "mad_eur",
    "usd": "mad_usd",
}


def _json_or_none(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) and value else None


def _load_fundamentals(session: Session) -> dict[str, Fundamentals]:
    """Latest fiscal year per symbol. An official value always beats a derived one:
    a derived PER is used only when the published cell was missing."""
    out: dict[str, Fundamentals] = {}
    for symbol, rows in load_fundamentals(session).items():
        if not rows:
            continue
        latest_year = rows[0]["fiscal_year"]  # loader sorts fiscal_year DESC
        for_year = [r for r in rows if r["fiscal_year"] == latest_year]
        official = next((r for r in for_year if r["source"] != DERIVED_SOURCE), None)
        derived = next((r for r in for_year if r["source"] == DERIVED_SOURCE), None)
        anchor = official or derived
        if anchor is None:
            continue

        per = official["per"] if official else None
        per_is_derived = False
        if per is None and derived is not None and derived["per"] is not None:
            per = derived["per"]
            per_is_derived = True

        out[symbol] = Fundamentals(
            fiscal_year=latest_year,
            eps=official["eps"] if official else None,
            roe=official["roe_pct"] if official else None,
            payout=official["payout_pct"] if official else None,
            dividend_yield=official["dividend_yield_pct"] if official else None,
            pbr=official["pbr"] if official else None,
            per=per,
            per_is_derived=per_is_derived,
            source=anchor["source"],
            source_url=anchor["source_url"],
        )
    return out


def _load_profiles(session: Session) -> dict[str, CompanyProfile]:
    return {
        symbol: CompanyProfile(
            company_name=row["company_name"],
            description=row["description"],
            business_model=row["business_model"],
            siege_social=row["siege_social"],
            commissaire_aux_comptes=row["commissaire_aux_comptes"],
            date_constitution=row["date_constitution"],
            date_introduction=row["date_introduction"],
            duree_exercice_social=row["duree_exercice_social"],
            ownership=_json_or_none(row["ownership_json"]),
            management=_json_or_none(row["management_json"]),
            updated_at=_aware(row["updated_at"]),
            source=row["source"],
            source_url=row["source_url"],
        )
        for symbol, row in load_company_profiles(session).items()
    }


def _load_macro(session: Session) -> MacroSnapshot | None:
    latest = load_latest_macro(session)
    if not latest:
        return None
    values: dict[str, float] = {}
    stamps: list[datetime] = []
    source: str | None = None
    for indicator, row in latest.items():
        field = _MACRO_FIELDS.get(indicator)
        if field is None:
            continue  # unknown series: ignored, never guessed into a field
        values[field] = row["value"]
        when = _aware(row["as_of"])
        if when is not None:
            stamps.append(when)
        source = source or row["source"]
    if not values:
        return None
    # oil / phosphate stay absent: BAM does not publish them.
    return MacroSnapshot(as_of=max(stamps) if stamps else None, source=source, **values)


def gather(session: Session) -> GatheredState:
    """Load everything once. The single DB-heavy step of a run."""
    metrics, scores = compute_state(session)
    portfolio = load_portfolio()
    metrics_by_symbol = {m.symbol: m for m in metrics}
    holdings = {
        e.symbol: e for e in evaluate_portfolio(portfolio, metrics_by_symbol, scores)
    }
    depths = load_history_depths(session)
    news_by_symbol, news_contexts = _build_news(session)
    return GatheredState(
        metrics=metrics,
        metrics_by_symbol=metrics_by_symbol,
        scores=scores,
        holdings=holdings,
        depths=depths,
        news_by_symbol=news_by_symbol,
        news_contexts=news_contexts,
        portfolio=portfolio,
        fundamentals=_load_fundamentals(session),
        profiles=_load_profiles(session),
        macro=_load_macro(session),
    )


def build_market_context(gathered: GatheredState) -> MarketContext:
    metrics = gathered.metrics
    with_ma = [m for m in metrics if m.price is not None and m.ma50 is not None]
    above = sum(1 for m in with_ma if m.price > m.ma50)
    breadth = round(above / len(with_ma) * 100, 1) if with_ma else None

    variations = [m.daily_variation for m in metrics if m.daily_variation is not None]
    advancers = sum(1 for v in variations if v > 0)
    decliners = sum(1 for v in variations if v < 0)

    momenta_30 = [m.momentum_30d for m in metrics if m.momentum_30d is not None]
    momenta_5 = [m.momentum_5d for m in metrics if m.momentum_5d is not None]
    avg_30 = round(sum(momenta_30) / len(momenta_30), 2) if momenta_30 else None
    avg_5 = round(sum(momenta_5) / len(momenta_5), 2) if momenta_5 else None

    if avg_30 is None or breadth is None or len(momenta_30) < max(3, len(metrics) // 5):
        regime = "indéterminé"
    elif breadth >= 60 and avg_30 > 1:
        regime = "haussier"
    elif breadth <= 40 and avg_30 < -1:
        regime = "baissier"
    else:
        regime = "neutre"

    sector_strength: dict[str, float] = {}
    for m in metrics:
        if m.sector and m.sector_strength is not None:
            sector_strength.setdefault(m.sector, m.sector_strength)
    sector_rank = {
        sector: rank
        for rank, (sector, _) in enumerate(
            sorted(sector_strength.items(), key=lambda kv: kv[1], reverse=True), start=1
        )
    }

    return MarketContext(
        as_of=datetime.now(UTC),
        tracked=len({m.symbol for m in metrics}),
        regime=regime,
        breadth_above_ma50_pct=breadth,
        advancers=advancers,
        decliners=decliners,
        avg_momentum_30d=avg_30,
        msi20_proxy={"5d": avg_5, "30d": avg_30},
        sector_strength=sector_strength,
        sector_rank=sector_rank,
        macro=gathered.macro,
    )


def build_context(
    session: Session,
    symbol: str,
    gathered: GatheredState,
    market: MarketContext,
) -> ResearchContext | None:
    metric = gathered.metrics_by_symbol.get(symbol.upper())
    if metric is None:
        metric = next(
            (m for m in gathered.metrics if m.symbol.upper() == symbol.upper()), None
        )
    if metric is None:
        return None
    history = load_symbol_history(session, metric.symbol, limit=HISTORY_POINTS)
    return ResearchContext(
        symbol=metric.symbol,
        company_name=metric.company_name,
        sector=metric.sector,
        as_of=datetime.now(UTC),
        metric=metric,
        history_days=gathered.depths.get(metric.symbol, 0),
        price_history=history,
        news=gathered.news_contexts.get(metric.symbol, NewsContext()),
        news_items=gathered.news_by_symbol.get(metric.symbol, []),
        holding=gathered.holdings.get(metric.symbol),
        portfolio=gathered.portfolio,
        fundamentals=gathered.fundamentals.get(metric.symbol),
        company_profile=gathered.profiles.get(metric.symbol),
        market=market,
    )
