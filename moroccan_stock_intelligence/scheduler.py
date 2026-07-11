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
from moroccan_stock_intelligence.services.investment_analysis import (
    dispatch_analysis_notifications,
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
            try:  # intelligent analysis alerts are best-effort; never break the digest
                dispatch_analysis_notifications(session, metrics, scores, portfolio)
            except Exception:  # noqa: BLE001
                LOG.exception("analysis_notifications_failed period=%s", period_label)
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
            try:  # intelligent analysis alerts are best-effort; never break the update
                dispatch_analysis_notifications(session, metrics, scores, portfolio)
            except Exception:  # noqa: BLE001
                LOG.exception("analysis_notifications_failed period=%s", period_label)
            LOG.info("intraday_job_done period=%s holdings=%s", period_label, len(holdings))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("intraday_job_failed period=%s", period_label)


def _macro_job(session_factory) -> None:  # noqa: ANN001
    """Daily Bank Al-Maghrib collection. One page, no symbol loop, off the hot path."""
    from moroccan_stock_intelligence.services.collectors.macro import collect_macro

    with session_factory() as session:
        try:
            stored = collect_macro(session)
            LOG.info("macro_job_done new_observations=%s", stored)
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("macro_job_failed")


def _issuer_job(session_factory) -> None:  # noqa: ANN001
    """Weekly issuer collection.

    The profile and the six ratios live on the SAME page, so both feeds are
    refreshed from a single fetch per issuer. Splitting them across different
    cadences would double the requests for no benefit. Ratios only change once a
    year, so weekly is already generous.
    """
    from moroccan_stock_intelligence.services.collectors.issuers import collect_issuers

    with session_factory() as session:
        try:
            tally = collect_issuers(session)
            LOG.info("issuer_job_done %s", tally)
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("issuer_job_failed")


def _feeds_bootstrap_job(session_factory) -> None:  # noqa: ANN001
    """Seed the Phase 1b feeds once if they are empty (fresh deploy / new volume).

    Without this, a new database would leave the macro / company / fundamental
    analysts reporting "unavailable" until the next weekly slot. Macro is cheap
    (one page); the issuer sweep is ~80 polite sequential fetches, so it only runs
    when the table is genuinely empty.
    """
    from moroccan_stock_intelligence.models import Fundamental, MacroIndicator
    from moroccan_stock_intelligence.services.collectors.issuers import collect_issuers
    from moroccan_stock_intelligence.services.collectors.macro import collect_macro

    with session_factory() as session:
        try:
            if not session.scalar(select(func.count()).select_from(MacroIndicator)):
                collect_macro(session)
        except Exception:  # noqa: BLE001
            LOG.exception("feeds_bootstrap_macro_failed")
        try:
            if not session.scalar(select(func.count()).select_from(Fundamental)):
                collect_issuers(session)
        except Exception:  # noqa: BLE001
            LOG.exception("feeds_bootstrap_issuers_failed")


def _research_job(session_factory) -> None:  # noqa: ANN001
    """Generate + store a report for every stock, then notify on thesis changes only.

    This is the expensive path, deliberately OFF the request path: the API then
    serves stored reports. Notifications here are thesis-based, not event-based —
    a stock that moved 4% with an unchanged thesis produces nothing.
    """
    from moroccan_stock_intelligence.services.research.notifications import (
        dispatch_thesis_notifications,
    )
    from moroccan_stock_intelligence.services.research.orchestrator import generate_all

    with session_factory() as session:
        try:
            generated = generate_all(session, horizon="short")
            sent = dispatch_thesis_notifications(session, generated)
            LOG.info("research_job_done reports=%s notifications=%s", len(generated), sent)
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("research_job_failed")


def _learning_job(session_factory) -> None:  # noqa: ANN001
    """Grade matured predictions and recalibrate analyst confidence.

    Statistical only (Brier + Bayesian shrinkage) — no ML. An analyst below the
    sample threshold keeps a 1.0 multiplier, so the system never pretends to have
    learned something it hasn't.
    """
    from moroccan_stock_intelligence.services.research.learning import run_learning_cycle

    with session_factory() as session:
        try:
            LOG.info("learning_job_done %s", run_learning_cycle(session))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("learning_job_failed")


def _knowledge_job(session_factory) -> None:  # noqa: ANN001
    """Accumulate de-duplicated company knowledge from the collected feeds."""
    from moroccan_stock_intelligence.services.research.knowledge import harvest_all

    with session_factory() as session:
        try:
            LOG.info("knowledge_job_done new_facts=%s", harvest_all(session))
        except Exception:  # noqa: BLE001 - a scheduled job must never crash the scheduler.
            LOG.exception("knowledge_job_failed")


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

    # --- Phase 1b feeds: deliberately off the report hot path. ---
    # One-off seed shortly after boot, only if the feed tables are empty.
    scheduler.add_job(
        _feeds_bootstrap_job,
        "date",
        run_date=datetime.now(ZoneInfo(settings.timezone)) + timedelta(seconds=90),
        args=[session_factory],
        id="feeds_bootstrap",
        replace_existing=True,
    )
    # Macro: daily before the open (BAM refreshes FX daily, the policy rate rarely).
    scheduler.add_job(
        _macro_job,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=settings.timezone),
        args=[session_factory],
        id="macro_collect",
        misfire_grace_time=3600,
        replace_existing=True,
    )
    # Issuers: weekly, off-market (Sunday 03:00). Ratios change once a year.
    scheduler.add_job(
        _issuer_job,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=settings.timezone),
        args=[session_factory],
        id="issuer_collect",
        misfire_grace_time=7200,
        replace_existing=True,
    )

    # --- Research platform (Phases 2-9): all off the request hot path. ---
    # Reports: after the close, once the day's prices are in. The API then serves
    # these stored reports instead of recomputing per request.
    scheduler.add_job(
        _research_job,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=settings.timezone),
        args=[session_factory],
        id="research_reports",
        misfire_grace_time=7200,
        replace_existing=True,
    )
    # Learning: grade whatever matured today, then recalibrate. Cheap; daily.
    scheduler.add_job(
        _learning_job,
        CronTrigger(hour=6, minute=0, timezone=settings.timezone),
        args=[session_factory],
        id="learning_cycle",
        misfire_grace_time=7200,
        replace_existing=True,
    )
    # Knowledge: after the weekly issuer sweep, so it harvests fresh data.
    scheduler.add_job(
        _knowledge_job,
        CronTrigger(day_of_week="sun", hour=4, minute=30, timezone=settings.timezone),
        args=[session_factory],
        id="knowledge_harvest",
        misfire_grace_time=7200,
        replace_existing=True,
    )
    return scheduler


def run_update_now(session_factory, label: str = "Mise à jour manuelle") -> None:  # noqa: ANN001
    """On-demand collect + analyze + notify, triggered by the app's manual button.

    Reuses the full digest path so a manual run behaves like a scheduled one
    (works any day, including weekends) and pushes the result to subscribers.
    """
    _digest_job(session_factory, label)
