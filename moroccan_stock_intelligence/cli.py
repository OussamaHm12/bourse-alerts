from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import load_price_frame, store_news
from moroccan_stock_intelligence.services.alerts import (
    build_daily_summary,
    dispatch_unsent_alerts,
    generate_alerts,
)
from moroccan_stock_intelligence.services.alerts import dispatch_urgent_holding_alerts
from moroccan_stock_intelligence.services.analytics import compute_metrics
from moroccan_stock_intelligence.services.collector import (
    collect_market_snapshots,
    persist_snapshots,
)
from moroccan_stock_intelligence.services.digest import build_digest
from moroccan_stock_intelligence.services.news import collect_news
from moroccan_stock_intelligence.services.portfolio import evaluate_portfolio, load_portfolio
from moroccan_stock_intelligence.services.scoring import score_opportunity
from moroccan_stock_intelligence.services.telegram import send_telegram_message

LOG = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Moroccan Stock Intelligence Platform")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init-db")
    subparsers.add_parser("collect")
    subparsers.add_parser("analyze")
    subparsers.add_parser("send-alerts")
    subparsers.add_parser("daily-summary")
    subparsers.add_parser("morning-digest")
    subparsers.add_parser("afternoon-digest")
    subparsers.add_parser("watch-holdings")
    subparsers.add_parser("run-once")
    args = parser.parse_args(argv)

    configure_logging(settings.log_level)
    engine = get_engine()
    init_db(engine)
    SessionFactory = get_session_factory(engine)
    command = args.command or "run-once"

    with SessionFactory() as session:
        if command == "init-db":
            LOG.info("database_initialized")
        elif command == "collect":
            snapshots = collect_market_snapshots()
            persist_snapshots(session, snapshots)
        elif command == "analyze":
            run_analysis(session)
        elif command == "send-alerts":
            sent = dispatch_unsent_alerts(session)
            LOG.info("alerts_dispatched count=%s", sent)
        elif command == "daily-summary":
            run_daily_summary(session)
        elif command == "morning-digest":
            run_digest(session, "Matin (10:07)")
        elif command == "afternoon-digest":
            run_digest(session, "Après-midi (15:07)")
        elif command == "watch-holdings":
            run_watch_holdings(session)
        elif command == "run-once":
            snapshots = collect_market_snapshots()
            persist_snapshots(session, snapshots)
            run_news(session)
            run_analysis(session)
            LOG.info("run_complete")
        else:
            parser.error(f"unknown command: {command}")


def run_news(session) -> None:  # noqa: ANN001
    stocks = session.scalars(select(Stock)).all()
    symbol_to_name = {stock.symbol: stock.company_name for stock in stocks}
    symbol_to_id = {stock.symbol: stock.id for stock in stocks}
    news_items = collect_news(symbol_to_name)
    for item in news_items:
        store_news(session, item, symbol_to_id.get(item.company_symbol or ""))
    session.commit()
    LOG.info("news_stored count=%s", len(news_items))


def run_analysis(session) -> dict[str, object]:  # noqa: ANN001
    frame = load_price_frame(session)
    metrics = compute_metrics(frame)
    scores = {metric.symbol: score_opportunity(metric) for metric in metrics}
    alerts = generate_alerts(session, metrics, scores)
    LOG.info("analysis_complete metrics=%s alerts_created=%s", len(metrics), len(alerts))
    return {"metrics": metrics, "scores": scores}


def run_daily_summary(session) -> None:  # noqa: ANN001
    result = run_analysis(session)
    message = build_daily_summary(result["metrics"], result["scores"])  # type: ignore[arg-type]
    send_telegram_message(message)
    LOG.info("daily_summary_complete")


def run_digest(session, period_label: str) -> None:  # noqa: ANN001
    snapshots = collect_market_snapshots()
    persist_snapshots(session, snapshots)
    run_news(session)
    result = run_analysis(session)
    metrics = result["metrics"]
    scores = result["scores"]
    portfolio = load_portfolio()
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}  # type: ignore[union-attr]
    holdings = evaluate_portfolio(portfolio, metrics_by_symbol, scores)  # type: ignore[arg-type]
    message = build_digest(period_label, metrics, scores, holdings, portfolio)  # type: ignore[arg-type]
    send_telegram_message(message, parse_mode="HTML")
    LOG.info("digest_sent period=%s holdings=%s", period_label, len(holdings))


def run_watch_holdings(session) -> None:  # noqa: ANN001
    snapshots = collect_market_snapshots()
    persist_snapshots(session, snapshots)
    result = run_analysis(session)
    portfolio = load_portfolio()
    sent = dispatch_urgent_holding_alerts(
        session, portfolio, result["metrics"], result["scores"]  # type: ignore[arg-type]
    )
    LOG.info("watch_holdings_complete urgent_sent=%s", sent)


if __name__ == "__main__":
    main()
