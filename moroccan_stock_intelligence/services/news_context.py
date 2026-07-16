"""The one place recent news is aggregated per symbol.

This existed twice: `investment_analysis.build_news_contexts` and
`research/context._build_news` computed the same thing from the same rows, and
had already drifted — one read module constants for the window, the other
hard-coded 30 days and 24 hours as defaults. Nothing linked them, so changing
"what counts as recent" in one place would have left the two scoring engines
silently disagreeing about the same stock's news. That is a correctness risk,
not a tidiness one, and it grows the moment `compute_state` starts reading news
too.

`NewsContext` (the aggregate the scoring kernel consumes) lives in
`horizon_strategy`, which is deliberately pure — no session, no I/O — so the
builder cannot live there. It lives here instead, next to the view model it
produces, and both callers import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import load_recent_news
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.news_classifier import event_family

# How far back a notice still counts, and how fresh "fresh" is. Single source of
# truth: every consumer reads these, so the two engines cannot drift apart again.
NEWS_WINDOW_DAYS = 30
FRESH_HOURS = 24

# The news table is small and this filters in Python on purpose: pushing the
# window into SQL would need an index per consumer for no measurable gain at
# this size (see AUDIT_TECHNIQUE.md §13 — revisit if the table grows).
NEWS_FETCH_LIMIT = 300


@dataclass(frozen=True)
class NewsView:
    """One notice, decoupled from the ORM row so analysts stay session-free."""

    title: str
    url: str
    source: str
    published_at: datetime | None
    collected_at: datetime | None
    event_type: str | None
    sentiment: str | None
    impact_score: float | None


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; the rest of the platform is UTC-aware."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _aggregate(items: list[NewsView], fresh_cutoff: datetime) -> NewsContext:
    impacts = [item.impact_score for item in items if item.impact_score is not None]
    latest = items[0]  # load_recent_news returns newest first
    return NewsContext(
        count=len(items),
        avg_impact=(sum(impacts) / len(impacts)) if impacts else None,
        positive=sum(1 for item in items if item.sentiment == "positive"),
        negative=sum(1 for item in items if item.sentiment == "negative"),
        latest_title=latest.title,
        latest_at=latest.collected_at,
        fresh_negative=any(
            item.sentiment == "negative"
            and item.collected_at is not None
            and item.collected_at >= fresh_cutoff
            for item in items
        ),
        # Family, not raw event_type: the taxonomy is finer than `dividend` /
        # `results` now, and comparing the raw value would silently never match.
        has_dividend=any(event_family(item.event_type) == "dividend" for item in items),
        has_results=any(event_family(item.event_type) == "results" for item in items),
    )


def build_news_views(
    session: Session,
    *,
    days: int = NEWS_WINDOW_DAYS,
    fresh_hours: int = FRESH_HOURS,
    now: datetime | None = None,
) -> tuple[dict[str, list[NewsView]], dict[str, NewsContext]]:
    """Recent notices per symbol, plus the aggregate the scoring kernel reads.

    Unlinked notices (no `stock_id`) are dropped: a market-level notice about an
    index or a regulation is not evidence about any issuer.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    fresh_cutoff = now - timedelta(hours=fresh_hours)

    grouped: dict[str, list[NewsView]] = {}
    for news, symbol in load_recent_news(session, limit=NEWS_FETCH_LIMIT):
        if symbol is None:
            continue
        when = aware(news.collected_at)
        if when is not None and when < cutoff:
            continue
        grouped.setdefault(symbol, []).append(
            NewsView(
                title=news.title,
                url=news.url,
                source=news.source,
                published_at=aware(news.published_at),
                collected_at=when,
                event_type=news.event_type,
                sentiment=news.sentiment,
                impact_score=news.impact_score,
            )
        )

    contexts = {symbol: _aggregate(items, fresh_cutoff) for symbol, items in grouped.items()}
    return grouped, contexts


def build_news_contexts(
    session: Session,
    *,
    days: int = NEWS_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, NewsContext]:
    """Just the aggregates, for callers that do not need the per-notice views."""
    return build_news_views(session, days=days, now=now)[1]
