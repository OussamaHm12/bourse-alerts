from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.alerts import dispatch_urgent_holding_alerts
from moroccan_stock_intelligence.services.collector import (
    collect_market_snapshots,
    persist_snapshots,
)
from moroccan_stock_intelligence.services.digest import (
    build_digest,
    build_intraday_update,
    build_push_payload,
)
from moroccan_stock_intelligence.services.portfolio import evaluate_portfolio, load_portfolio
from moroccan_stock_intelligence.services.push import send_push_to_all
from moroccan_stock_intelligence.services.telegram import send_telegram_message

LOG = logging.getLogger(__name__)


def _digest_job(session_factory, period_label: str) -> None:  # noqa: ANN001
    from moroccan_stock_intelligence.cli import run_analysis, run_news

    with session_factory() as session:
        try:
            persist_snapshots(session, collect_market_snapshots())
            run_news(session)
            result = run_analysis(session)
            metrics, scores = result["metrics"], result["scores"]
            portfolio = load_portfolio()
            metrics_by_symbol = {metric.symbol: metric for metric in metrics}
            holdings = evaluate_portfolio(portfolio, metrics_by_symbol, scores)

            message = build_digest(period_label, metrics, scores, holdings, portfolio)
            send_telegram_message(message, parse_mode="HTML")
            title, body = build_push_payload(period_label, holdings)
            send_push_to_all(session, title, body, "/")
            LOG.info("digest_job_done period=%s holdings=%s", period_label, len(holdings))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("digest_job_failed period=%s", period_label)


def _intraday_job(session_factory, period_label: str) -> None:  # noqa: ANN001
    """Every 2h during the session: a lightweight update plus the crash safety net."""
    from moroccan_stock_intelligence.cli import run_analysis

    with session_factory() as session:
        try:
            persist_snapshots(session, collect_market_snapshots())
            result = run_analysis(session)
            metrics, scores = result["metrics"], result["scores"]
            portfolio = load_portfolio()
            metrics_by_symbol = {metric.symbol: metric for metric in metrics}
            holdings = evaluate_portfolio(portfolio, metrics_by_symbol, scores)

            # Safety net: immediate alert if a held position is crashing intraday.
            dispatch_urgent_holding_alerts(session, portfolio, metrics, scores)

            message = build_intraday_update(period_label, metrics, scores, holdings, portfolio)
            send_telegram_message(message, parse_mode="HTML")
            title, body = build_push_payload(period_label, holdings)
            send_push_to_all(session, title, body, "/")
            LOG.info("intraday_job_done period=%s holdings=%s", period_label, len(holdings))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("intraday_job_failed period=%s", period_label)


def build_scheduler(session_factory) -> BackgroundScheduler:  # noqa: ANN001
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        _digest_job,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=settings.timezone),
        args=[session_factory, "Matin (10:00)"],
        id="morning_digest",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _digest_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=settings.timezone),
        args=[session_factory, "Clôture (16:00)"],
        id="afternoon_digest",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _intraday_job,
        CronTrigger(day_of_week="mon-fri", hour="12,14", minute=0, timezone=settings.timezone),
        args=[session_factory, "Point intraday"],
        id="intraday_update",
        misfire_grace_time=600,
        replace_existing=True,
    )
    return scheduler
