from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import Price
from moroccan_stock_intelligence.services.alerts import dispatch_urgent_holding_alerts
from moroccan_stock_intelligence.services.collector import (
    collect_market_snapshots,
    persist_snapshots,
)
from moroccan_stock_intelligence.repository import save_notification
from moroccan_stock_intelligence.services.digest import (
    build_digest,
    build_intraday_update,
    build_push_payload,
    html_to_text,
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
            save_notification(session, "digest", period_label, html_to_text(message))
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
            save_notification(session, "intraday", period_label, html_to_text(message))
            send_telegram_message(message, parse_mode="HTML")
            title, body = build_push_payload(period_label, holdings)
            send_push_to_all(session, title, body, "/")
            LOG.info("intraday_job_done period=%s holdings=%s", period_label, len(holdings))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("intraday_job_failed period=%s", period_label)


def _bootstrap_job(session_factory) -> None:  # noqa: ANN001
    """Seed the DB once at startup if it has no price history yet.

    A fresh deploy or a newly mounted volume starts with an empty database, so the
    web app would show nothing until the next scheduled collection (which is
    Mon-Fri only). This one-off job populates prices + signals right after boot so
    the app has data immediately, without waiting for a market-hours slot.
    """
    from moroccan_stock_intelligence.cli import run_analysis, run_news

    with session_factory() as session:
        try:
            existing = session.scalar(select(func.count()).select_from(Price))
            if existing:
                LOG.info("bootstrap_skipped existing_prices=%s", existing)
                return
            persist_snapshots(session, collect_market_snapshots())
            run_analysis(session)
            try:  # news is best-effort; never let it block the price seeding
                run_news(session)
            except Exception:  # noqa: BLE001
                LOG.exception("bootstrap_news_failed")
            LOG.info("bootstrap_done prices_seeded=true")
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("bootstrap_job_failed")


def build_scheduler(session_factory) -> BackgroundScheduler:  # noqa: ANN001
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        _bootstrap_job,
        "date",
        run_date=datetime.now(ZoneInfo(settings.timezone)) + timedelta(seconds=8),
        args=[session_factory],
        id="bootstrap",
        replace_existing=True,
    )
    # Every 2h on weekdays (09:00 -> 17:00). Full digests bookend the day at open
    # and close; lighter intraday updates fill the slots in between.
    scheduler.add_job(
        _digest_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=settings.timezone),
        args=[session_factory, "Ouverture (09:00)"],
        id="morning_digest",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _digest_job,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=settings.timezone),
        args=[session_factory, "Clôture (17:00)"],
        id="closing_digest",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        _intraday_job,
        CronTrigger(
            day_of_week="mon-fri", hour="11,13,15", minute=0, timezone=settings.timezone
        ),
        args=[session_factory, "Point intraday"],
        id="intraday_update",
        misfire_grace_time=1800,
        replace_existing=True,
    )
    return scheduler


def run_update_now(session_factory, label: str = "Mise à jour manuelle") -> None:  # noqa: ANN001
    """On-demand collect + analyze + notify, triggered by the app's manual button.

    Reuses the full digest path so a manual run behaves like a scheduled one
    (works any day, including weekends) and pushes the result to subscribers.
    """
    _digest_job(session_factory, label)
