from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.repository import load_price_frame
from moroccan_stock_intelligence.services.analytics import MetricSet, compute_metrics
from moroccan_stock_intelligence.services.portfolio import (
    HoldingEvaluation,
    Portfolio,
    evaluate_portfolio,
    load_portfolio,
)
from moroccan_stock_intelligence.services.scoring import ScoreResult, score_opportunity


def compute_state(session: Session) -> tuple[list[MetricSet], dict[str, ScoreResult]]:
    metrics = compute_metrics(load_price_frame(session))
    scores = {metric.symbol: score_opportunity(metric) for metric in metrics}
    return metrics, scores


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


def overview_payload(session: Session) -> dict:
    metrics, scores = compute_state(session)
    portfolio = load_portfolio()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "timezone": settings.timezone,
        "portfolio": portfolio_payload(portfolio, metrics, scores),
        "market": market_payload(metrics, scores),
    }
