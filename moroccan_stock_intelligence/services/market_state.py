"""Metrics + opportunity scores for the whole market: the one place they are built.

Two problems this module resolves.

**A dependency inversion.** `compute_state` used to live in `services/views.py`,
so `investment_analysis` and `research/context` — both calculation layers — had
to import a *view* layer to obtain market state. `scoring.py`'s own comment
already argued the opposite ("none of them should import a view layer"); the code
just did not honour it. Market state is not a view concern: it is what the views,
the CLI and the research engine each need *before* they can present anything.

**A dead 10% weight.** `score_opportunity` reserves 10% of `buy_score` for news
sentiment plus a malus in `avoid_score`, but no production caller ever passed the
argument — `views.compute_state`, `cli.run_analysis` and the Streamlit dashboard
all called `score_opportunity(metric)`, so the component sat pinned at a constant
50 and a tenth of the score users actually look at was inert. Only the test suite
passed it, which is exactly why it went unnoticed. News is wired in here, once,
where every caller picks it up.

A note on what this can and cannot do. `NewsContext.avg_impact` is a mean in
[-1, +1], and `score_opportunity` maps it through `clamp(50 + s*25)`, so news can
move the component within [25, 75] — damped on purpose. What this engine still
cannot express is the difference between "no news" and "neutral news": both feed
0.0. The horizon engine handles that properly (a missing component lowers
coverage, which shrinks the score and the confidence); this one has no coverage
notion at all. That gap is stated rather than papered over, and it is one of the
inputs to the engine A/B decision.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import News, Price
from moroccan_stock_intelligence.repository import load_price_frame
from moroccan_stock_intelligence.services.analytics import MetricSet, compute_metrics
from moroccan_stock_intelligence.services.news_context import build_news_contexts
from moroccan_stock_intelligence.services.scoring import ScoreResult, score_opportunity

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Fingerprint:
    """Identifies the INPUTS. Same fingerprint ⇒ same output, by construction.

    Not a timestamp-and-hope TTL: this is derived from the data itself, so the
    cache cannot serve a stale answer, and cannot expire one that is still valid.

    * `max_price_id` rather than `MAX(observed_at)` — the history backfill inserts
      *old* séances, so the newest observation would not move and a TTL keyed on it
      would miss three years of new data. Any INSERT bumps the primary key. It is
      also an index lookup (0.09 ms) where `COUNT(*)` is a full scan (2.7 ms).
    * the news triple catches UPDATEs, not just inserts: `reclassify-news --apply`
      rewrites impact_score in place without inserting anything, so max(id) alone
      would keep serving scores built on the pre-backfill classification. The table
      is small, so summing it costs 0.15 ms.
    """

    max_price_id: int | None
    max_news_id: int | None
    news_count: int
    news_impact_sum: float | None


_lock = threading.Lock()
_cached: tuple[_Fingerprint, list[MetricSet], dict[str, ScoreResult]] | None = None


def _fingerprint(session: Session) -> _Fingerprint:
    max_price_id = session.scalar(select(func.max(Price.id)))
    news_max, news_count, news_sum = session.execute(
        select(func.max(News.id), func.count(News.id), func.sum(News.impact_score))
    ).one()
    return _Fingerprint(
        max_price_id=max_price_id,
        max_news_id=news_max,
        news_count=news_count or 0,
        news_impact_sum=float(news_sum) if news_sum is not None else None,
    )


def invalidate() -> None:
    """Drop the cache. For tests and for any in-process mutation of the inputs."""
    global _cached
    with _lock:
        _cached = None


def compute_state(session: Session) -> tuple[list[MetricSet], dict[str, ScoreResult]]:
    """Every tracked symbol's metrics and opportunity score, news included.

    Cached on the fingerprint of its inputs. Six endpoints call this, each on every
    request, and the answer only changes when a collection writes rows — which a
    900 s cooldown already bounds. Measured at production volume (80 symbols × 738
    séances): 1 100 ms per call, ~55 MB of transient allocation, and opening the app
    across its tabs cost ~11.5 s of which ~10 s was the same computation nine times.
    The result itself is 23 KB, so caching it is essentially free.

    Deliberately NOT a TTL: a fingerprint cannot serve data that changed, and cannot
    discard data that did not.
    """
    global _cached
    fingerprint = _fingerprint(session)

    with _lock:
        if _cached is not None and _cached[0] == fingerprint:
            return _cached[1], _cached[2]

    # Computed outside the lock: it is a pure function of the fingerprint, so a
    # concurrent duplicate is wasted work, never a wrong answer — and holding the
    # lock for a second would stall every other request behind it.
    metrics, scores = _compute(session)

    with _lock:
        _cached = (fingerprint, metrics, scores)
    return metrics, scores


def _compute(session: Session) -> tuple[list[MetricSet], dict[str, ScoreResult]]:
    metrics = compute_metrics(load_price_frame(session))
    news = build_news_contexts(session)
    scores = {
        metric.symbol: score_opportunity(
            metric,
            # A symbol with no collected news scores 0.0 — neutral. See the module
            # docstring: this engine cannot distinguish absent from neutral.
            news_sentiment_score=_sentiment_of(news, metric.symbol),
        )
        for metric in metrics
    }
    LOG.info("market_state_computed symbols=%s", len(metrics))
    return metrics, scores


def _sentiment_of(news: dict, symbol: str) -> float:
    context = news.get(symbol)
    if context is None or context.avg_impact is None:
        return 0.0
    return context.avg_impact
