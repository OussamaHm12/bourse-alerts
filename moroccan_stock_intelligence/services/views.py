from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import News
from moroccan_stock_intelligence.repository import (
    load_favorite_symbols,
    load_recent_news,
    load_recent_notifications,
    load_symbol_history,
)
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.favorites import evaluate_favorites, sort_by_score
from moroccan_stock_intelligence.services.market_state import compute_state
from moroccan_stock_intelligence.services.portfolio import (
    HoldingEvaluation,
    Portfolio,
    evaluate_portfolio,
    load_portfolio,
)
from moroccan_stock_intelligence.services.scoring import ScoreResult, classify_label


# `compute_state` moved to `services/market_state`: calculation layers were
# importing it from here, i.e. depending on a view module to obtain market state.
# Re-exported (imported above) so existing `from ...views import compute_state`
# callers keep resolving.


def _holding_dict(evaluation: HoldingEvaluation) -> dict:
    return {
        "symbol": evaluation.symbol,
        "company_name": evaluation.company_name,
        "quantity": evaluation.quantity,
        "buy_price": evaluation.buy_price,
        "current_price": evaluation.current_price,
        "daily_variation": evaluation.daily_variation,
        "market_value": evaluation.market_value,
        "net_pl": evaluation.net_pl,
        "net_pl_pct": evaluation.net_pl_pct,
        "advice": evaluation.advice,
        "advice_reason": evaluation.advice_reason,
    }


def portfolio_payload(
    portfolio: Portfolio,
    metrics: list[MetricSet],
    scores: dict[str, ScoreResult],
) -> dict:
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    evaluations = evaluate_portfolio(portfolio, metrics_by_symbol, scores)
    priced = [e for e in evaluations if e.market_value is not None]
    total_value = sum(e.market_value for e in priced)
    total_cost = sum(e.cost_basis for e in priced)
    total_net = sum(e.net_pl for e in priced if e.net_pl is not None)
    total_pct = (total_net / total_cost * 100) if total_cost else None
    return {
        "fee_rate": portfolio.fee_rate,
        "total_value": total_value,
        "total_net_pl": total_net,
        "total_pl_pct": total_pct,
        "holdings": [_holding_dict(e) for e in evaluations],
        "sell_count": sum(1 for e in evaluations if e.advice == "SELL"),
    }


def market_payload(metrics: list[MetricSet], scores: dict[str, ScoreResult]) -> dict:
    movers = [m for m in metrics if m.daily_variation is not None]
    gainers = sorted(movers, key=lambda m: m.daily_variation or 0, reverse=True)[:5]
    losers = sorted(movers, key=lambda m: m.daily_variation or 0)[:5]
    opportunities = sorted(scores.values(), key=lambda s: s.buy_score, reverse=True)[:5]

    def mover(metric: MetricSet) -> dict:
        return {
            "symbol": metric.symbol,
            "company_name": metric.company_name,
            "price": metric.price,
            "daily_variation": metric.daily_variation,
        }

    return {
        "tracked": len({m.symbol for m in metrics}),
        "gainers": [mover(m) for m in gainers],
        "losers": [mover(m) for m in losers],
        "opportunities": [
            {"symbol": s.symbol, "buy_score": s.buy_score, "reasons": s.reasons[:2]}
            for s in opportunities
        ],
    }


def favorites_payload(session: Session) -> dict:
    """The watchlist tab: every favorite, best opportunity score first.

    No P/L and no SELL/HOLD advice — we hold none of these. `symbols` is what the app
    stars, so the star state comes from the same source of truth as the alerts.
    """
    metrics, scores = compute_state(session)
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}
    symbols = load_favorite_symbols(session)
    evaluations = sort_by_score(evaluate_favorites(symbols, metrics_by_symbol, scores))
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "count": len(evaluations),
        "symbols": symbols,
        "favorites": [
            {
                "symbol": e.symbol,
                "company_name": e.company_name,
                "sector": e.sector,
                "price": e.price,
                "daily_variation": e.daily_variation,
                "momentum_30d": e.momentum_30d,
                "volume_anomaly": e.volume_anomaly,
                "buy_score": e.buy_score,
                "avoid_score": e.avoid_score,
                "label": e.label,
                "headline": e.headline,
                "reasons": e.reasons[:3],
                "risks": e.risks[:2],
            }
            for e in evaluations
        ],
    }


def overview_payload(session: Session) -> dict:
    metrics, scores = compute_state(session)
    portfolio = load_portfolio()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "timezone": settings.timezone,
        "portfolio": portfolio_payload(portfolio, metrics, scores),
        "market": market_payload(metrics, scores),
    }


# --------------------------------------------------------------------------- #
# Enriched views: full market table, per-stock detail, opportunities, news.    #
# --------------------------------------------------------------------------- #

def _trend(metric: MetricSet) -> str:
    if metric.price is None or metric.ma50 is None:
        return "neutre"
    if metric.price >= metric.ma50 * 1.01:
        return "haussier"
    if metric.price <= metric.ma50 * 0.99:
        return "baissier"
    return "neutre"


def _stock_row(metric: MetricSet, score: ScoreResult | None, is_favorite: bool = False) -> dict:
    return {
        "symbol": metric.symbol,
        "company_name": metric.company_name,
        "sector": metric.sector,
        "price": metric.price,
        "daily_variation": metric.daily_variation,
        "volume": metric.volume,
        "volume_anomaly": metric.volume_anomaly,
        "momentum_30d": metric.momentum_30d,
        "buy_score": score.buy_score if score else None,
        "watch_score": score.watch_score if score else None,
        "avoid_score": score.avoid_score if score else None,
        "label": classify_label(score),
        "trend": _trend(metric),
        "is_favorite": is_favorite,
    }


_SORT_KEYS = {
    "score": (lambda r: r["buy_score"] if r["buy_score"] is not None else -1, True),
    "variation": (lambda r: r["daily_variation"] if r["daily_variation"] is not None else -999, True),
    "volume": (lambda r: r["volume_anomaly"] if r["volume_anomaly"] is not None else -1, True),
    "name": (lambda r: r["symbol"], False),
}


def stocks_payload(
    session: Session,
    sort: str = "score",
    sector: str | None = None,
    query: str | None = None,
) -> dict:
    metrics, scores = compute_state(session)
    favorites = set(load_favorite_symbols(session))
    rows = [_stock_row(m, scores.get(m.symbol), m.symbol in favorites) for m in metrics]
    if sector:
        rows = [r for r in rows if (r["sector"] or "").lower() == sector.lower()]
    if query:
        needle = query.lower()
        rows = [
            r
            for r in rows
            if needle in r["symbol"].lower() or needle in (r["company_name"] or "").lower()
        ]
    key_fn, reverse = _SORT_KEYS.get(sort, _SORT_KEYS["score"])
    rows.sort(key=key_fn, reverse=reverse)
    sectors = sorted({m.sector for m in metrics if m.sector})
    return {"count": len(rows), "sectors": sectors, "stocks": rows}


def _news_item(news: News, symbol: str | None) -> dict:
    return {
        "title": news.title,
        "url": news.url,
        "source": news.source,
        "published_at": news.published_at.isoformat() if news.published_at else None,
        "event_type": news.event_type,
        "sentiment": news.sentiment,
        "impact_score": news.impact_score,
        "symbol": symbol,
    }


def stock_detail_payload(session: Session, symbol: str) -> dict | None:
    metrics, scores = compute_state(session)
    metric = next((m for m in metrics if m.symbol.upper() == symbol.upper()), None)
    if metric is None:
        return None
    score = scores.get(metric.symbol)
    history = [{"t": t.isoformat(), "p": p} for t, p in load_symbol_history(session, symbol)]
    news = [_news_item(n, s) for n, s in load_recent_news(session, limit=10, symbol=symbol)]
    return {
        "symbol": metric.symbol,
        "company_name": metric.company_name,
        "sector": metric.sector,
        "is_favorite": metric.symbol in set(load_favorite_symbols(session)),
        "price": metric.price,
        "daily_variation": metric.daily_variation,
        "volume": metric.volume,
        "volume_anomaly": metric.volume_anomaly,
        "trend": _trend(metric),
        "momentum": {
            "d1": metric.momentum_1d,
            "d5": metric.momentum_5d,
            "d30": metric.momentum_30d,
            "d90": metric.momentum_90d,
        },
        "moving_averages": {"ma20": metric.ma20, "ma50": metric.ma50, "ma200": metric.ma200},
        "volatility_30d": metric.volatility_30d,
        "relative_performance_30d": metric.relative_performance_30d,
        "drawdown_from_recent_high": metric.drawdown_from_recent_high,
        "support": metric.support,
        "resistance": metric.resistance,
        "support_distance": metric.support_distance,
        "resistance_distance": metric.resistance_distance,
        "week52_high": metric.week52_high,
        "week52_low": metric.week52_low,
        "week52_high_proximity": metric.week52_high_proximity,
        "week52_low_proximity": metric.week52_low_proximity,
        "sector_strength": metric.sector_strength,
        "score": {
            "buy": score.buy_score,
            "watch": score.watch_score,
            "avoid": score.avoid_score,
            "label": classify_label(score),
            "components": score.components,
            "reasons": score.reasons,
            "risks": score.risks,
        }
        if score
        else None,
        "history": history,
        "news": news,
    }


def opportunities_payload(session: Session, min_score: float = 50.0) -> dict:
    metrics, scores = compute_state(session)
    by_symbol = {m.symbol: m for m in metrics}
    ranked = sorted(scores.values(), key=lambda s: s.buy_score, reverse=True)
    items = []
    for score in ranked:
        if score.buy_score < min_score:
            continue
        metric = by_symbol.get(score.symbol)
        items.append(
            {
                "symbol": score.symbol,
                "company_name": metric.company_name if metric else score.symbol,
                "price": metric.price if metric else None,
                "daily_variation": metric.daily_variation if metric else None,
                "buy_score": score.buy_score,
                "avoid_score": score.avoid_score,
                "label": classify_label(score),
                "reasons": score.reasons,
                "components": score.components,
                "momentum_30d": metric.momentum_30d if metric else None,
            }
        )
    return {"min_score": min_score, "count": len(items), "opportunities": items}


def news_payload(session: Session, limit: int = 30) -> dict:
    return {"news": [_news_item(n, s) for n, s in load_recent_news(session, limit=limit)]}


def notifications_payload(session: Session, limit: int = 50) -> dict:
    return {
        "notifications": [
            {
                "id": n.id,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
            }
            for n in load_recent_notifications(session, limit=limit)
        ]
    }


def sectors_payload(session: Session) -> dict:
    metrics, _ = compute_state(session)
    agg: dict[str, dict] = {}
    for metric in metrics:
        if not metric.sector:
            continue
        bucket = agg.setdefault(metric.sector, {"momenta": [], "count": 0})
        bucket["count"] += 1
        if metric.momentum_30d is not None:
            bucket["momenta"].append(metric.momentum_30d)
    sectors = [
        {
            "sector": name,
            "avg_momentum_30d": (sum(d["momenta"]) / len(d["momenta"])) if d["momenta"] else None,
            "count": d["count"],
        }
        for name, d in agg.items()
    ]
    sectors.sort(
        key=lambda s: (s["avg_momentum_30d"] is not None, s["avg_momentum_30d"] or 0),
        reverse=True,
    )
    return {"sectors": sectors}
