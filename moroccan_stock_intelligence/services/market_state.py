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

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import load_price_frame
from moroccan_stock_intelligence.services.analytics import MetricSet, compute_metrics
from moroccan_stock_intelligence.services.news_context import build_news_contexts
from moroccan_stock_intelligence.services.scoring import ScoreResult, score_opportunity


def compute_state(session: Session) -> tuple[list[MetricSet], dict[str, ScoreResult]]:
    """Every tracked symbol's metrics and opportunity score, news included.

    One query pass for prices, one for news; the news aggregate is built once for
    the whole market rather than per symbol.
    """
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
    return metrics, scores


def _sentiment_of(news: dict, symbol: str) -> float:
    context = news.get(symbol)
    if context is None or context.avg_impact is None:
        return 0.0
    return context.avg_impact
