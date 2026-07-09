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

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import (
    load_history_depths,
    load_recent_news,
    load_symbol_history,
)
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
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
    as_of: datetime | None = None
    per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    dividend_yield: float | None = None
    roe: float | None = None
    roa: float | None = None
    net_margin: float | None = None
    revenue: float | None = None
    net_income: float | None = None
    debt_to_equity: float | None = None
    book_value: float | None = None
    source: str | None = None

    @property
    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (self.per, self.pbr, self.eps, self.dividend_yield, self.roe, self.revenue)
        )


@dataclass(frozen=True)
class CompanyProfile:
    description: str | None = None
    business_model: str | None = None
    management: str | None = None
    ownership: str | None = None
    updated_at: datetime | None = None
    source: str | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.description or self.business_model)


@dataclass(frozen=True)
class MacroSnapshot:
    as_of: datetime | None = None
    policy_rate: float | None = None
    inflation: float | None = None
    mad_usd: float | None = None
    mad_eur: float | None = None
    oil: float | None = None
    phosphate: float | None = None
    source: str | None = None

    @property
    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (self.policy_rate, self.inflation, self.mad_usd, self.oil, self.phosphate)
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
            has_dividend=any(i.event_type == "dividend" for i in items),
            has_results=any(i.event_type == "results" for i in items),
        )
    return grouped, contexts


# Phase 1b will replace these three stubs with real repository loaders once the
# fundamentals / company_profiles / macro_indicators tables exist. Returning
# empty now keeps Phase 1 non-breaking (no new tables, no migration) while the
# analysts already handle the populated case.
def _load_fundamentals(session: Session) -> dict[str, Fundamentals]:  # noqa: ARG001
    return {}


def _load_profiles(session: Session) -> dict[str, CompanyProfile]:  # noqa: ARG001
    return {}


def _load_macro(session: Session) -> MacroSnapshot | None:  # noqa: ARG001
    return None


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
