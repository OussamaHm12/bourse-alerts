from __future__ import annotations

import argparse
import logging
import os

from sqlalchemy import select

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import store_news
from moroccan_stock_intelligence.services.alerts import (
    build_daily_summary,
    dispatch_unsent_alerts,
    generate_alerts,
)
from moroccan_stock_intelligence.services.alerts import dispatch_urgent_holding_alerts
from moroccan_stock_intelligence.services.collector import (
    collect_market_snapshots,
    persist_snapshots,
)
from moroccan_stock_intelligence.services.backup import render_result, run_backup
from moroccan_stock_intelligence.services.digest import build_digest, build_intraday_update
from moroccan_stock_intelligence.services.market_state import compute_state
from moroccan_stock_intelligence.services.news import collect_news
from moroccan_stock_intelligence.services.news_backfill import (
    BATCH_SIZE as NEWS_BATCH_SIZE,
)
from moroccan_stock_intelligence.services.news_backfill import (
    reclassify_news,
    render_report,
)
from moroccan_stock_intelligence.services.portfolio import evaluate_portfolio, load_portfolio
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
    subparsers.add_parser("intraday-update")
    subparsers.add_parser("watch-holdings")
    subparsers.add_parser("run-once")
    subparsers.add_parser("gen-vapid")
    subparsers.add_parser("collect-macro")
    issuers_parser = subparsers.add_parser("collect-issuers")
    issuers_parser.add_argument(
        "--symbols", nargs="*", help="limit to these symbols (default: every stock)"
    )
    history_parser = subparsers.add_parser(
        "backfill-history",
        help="seed up to ~3 years of daily history from the instrument_history endpoint",
    )
    history_parser.add_argument(
        "--symbols", nargs="*", help="limit to these symbols (default: every stock)"
    )
    history_parser.add_argument(
        "--limit", type=int, default=None, help="cap séances fetched per symbol (default: all)"
    )
    backup_parser = subparsers.add_parser(
        "backup",
        help="snapshot the database, verify it, compress it, and ship it off-host",
    )
    backup_parser.add_argument(
        "--no-ship", action="store_true", help="keep the snapshot local (skip Telegram)"
    )
    backup_parser.add_argument(
        "--keep", type=int, default=None, help="how many snapshots to retain (default: BACKUP_KEEP)"
    )
    reclassify_parser = subparsers.add_parser(
        "reclassify-news",
        help="re-derive event_type/sentiment/impact on stored notices (dry-run by default)",
    )
    # Mutually exclusive so the intent is unambiguous: writing is never a default,
    # and `--dry-run --apply` is rejected rather than silently resolved.
    reclassify_mode = reclassify_parser.add_mutually_exclusive_group()
    reclassify_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing (default)",
    )
    reclassify_mode.add_argument(
        "--apply", action="store_true", help="write the new classification to the database"
    )
    reclassify_parser.add_argument(
        "--batch-size", type=int, default=NEWS_BATCH_SIZE, help="rows per committed batch"
    )
    reports_parser = subparsers.add_parser("generate-reports")
    reports_parser.add_argument("--symbols", nargs="*")
    reports_parser.add_argument("--horizon", default="short", choices=["short", "medium", "long"])
    subparsers.add_parser("learn")
    subparsers.add_parser("harvest-knowledge")
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    # Managed hosts (Railway, Render, Fly) inject the public port via $PORT.
    serve_parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args(argv)

    if args.command == "gen-vapid":
        run_gen_vapid()
        return
    if args.command == "serve":
        run_serve(args.host, args.port)
        return

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
            run_digest(session, "Matin (10:00)")
        elif command == "afternoon-digest":
            run_digest(session, "Clôture (16:00)")
        elif command == "intraday-update":
            run_intraday_update(session, "Point intraday")
        elif command == "watch-holdings":
            run_watch_holdings(session)
        elif command == "run-once":
            snapshots = collect_market_snapshots()
            persist_snapshots(session, snapshots)
            run_news(session)
            run_analysis(session)
            LOG.info("run_complete")
        elif command == "collect-macro":
            from moroccan_stock_intelligence.services.collectors.macro import collect_macro

            LOG.info("macro_collected new_observations=%s", collect_macro(session))
        elif command == "collect-issuers":
            from moroccan_stock_intelligence.services.collectors.issuers import collect_issuers

            LOG.info("issuers_collected %s", collect_issuers(session, symbols=args.symbols))
        elif command == "backfill-history":
            from moroccan_stock_intelligence.services.collectors.history import backfill_history

            tally = backfill_history(session, symbols=args.symbols, limit=args.limit)
            LOG.info("history_backfilled %s", tally)
        elif command == "backup":
            run_backup_command(ship=not args.no_ship, keep=args.keep)
        elif command == "reclassify-news":
            run_reclassify_news(session, apply=args.apply, batch_size=args.batch_size)
        elif command == "generate-reports":
            from moroccan_stock_intelligence.services.research.notifications import (
                dispatch_thesis_notifications,
            )
            from moroccan_stock_intelligence.services.research.orchestrator import generate_all

            generated = generate_all(session, horizon=args.horizon, symbols=args.symbols)
            sent = dispatch_thesis_notifications(session, generated)
            LOG.info("reports_generated count=%s notifications=%s", len(generated), sent)
        elif command == "learn":
            from moroccan_stock_intelligence.services.research.learning import run_learning_cycle

            LOG.info("learning_cycle %s", run_learning_cycle(session))
        elif command == "harvest-knowledge":
            from moroccan_stock_intelligence.services.research.knowledge import harvest_all

            LOG.info("knowledge_harvested new_facts=%s", harvest_all(session))
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


def run_backup_command(*, ship: bool, keep: int | None) -> None:
    """Snapshot the database. Exits non-zero if the snapshot is not verifiable.

    A failed integrity check exits 1 on purpose: this command is meant to be the
    gate in front of any destructive operation, and a gate that always opens is
    not a gate.
    """
    result = run_backup(ship=ship, keep=keep)
    print(render_result(result))
    if result.skipped_reason or not result.ok:
        raise SystemExit(1)


def run_reclassify_news(session, *, apply: bool, batch_size: int) -> None:  # noqa: ANN001
    """Re-derive the stored notices' classification. Exits non-zero on failure.

    The report goes to stdout rather than the log: it is the deliverable of a dry
    run, not a trace of it.
    """
    try:
        report = reclassify_news(session, apply=apply, batch_size=batch_size)
    except Exception as exc:
        # reclassify_news has already rolled back and logged the traceback; all this
        # layer owes the caller is a non-zero exit.
        raise SystemExit(1) from exc
    print(render_report(report))


def run_analysis(session) -> dict[str, object]:  # noqa: ANN001
    # Third copy of the same three lines until now — and the one that silently
    # dropped news, so the digests scored differently from the reports.
    metrics, scores = compute_state(session)
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


def run_intraday_update(session, period_label: str) -> None:  # noqa: ANN001
    """Lightweight intraday point (every 2h during the session) + crash safety net."""
    snapshots = collect_market_snapshots()
    persist_snapshots(session, snapshots)
    result = run_analysis(session)
    metrics = result["metrics"]
    scores = result["scores"]
    portfolio = load_portfolio()
    metrics_by_symbol = {metric.symbol: metric for metric in metrics}  # type: ignore[union-attr]
    holdings = evaluate_portfolio(portfolio, metrics_by_symbol, scores)  # type: ignore[arg-type]
    dispatch_urgent_holding_alerts(session, portfolio, metrics, scores)  # type: ignore[arg-type]
    message = build_intraday_update(period_label, metrics, scores, holdings, portfolio)  # type: ignore[arg-type]
    send_telegram_message(message, parse_mode="HTML")
    LOG.info("intraday_update_sent period=%s holdings=%s", period_label, len(holdings))


def run_watch_holdings(session) -> None:  # noqa: ANN001
    snapshots = collect_market_snapshots()
    persist_snapshots(session, snapshots)
    result = run_analysis(session)
    portfolio = load_portfolio()
    sent = dispatch_urgent_holding_alerts(
        session, portfolio, result["metrics"], result["scores"]  # type: ignore[arg-type]
    )
    LOG.info("watch_holdings_complete urgent_sent=%s", sent)


def run_gen_vapid() -> None:
    from moroccan_stock_intelligence.services.push import generate_vapid_keys

    public, private = generate_vapid_keys()
    print("Add these to your .env (keep the private key secret):\n")
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_SUBJECT=mailto:you@example.com")


def run_serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("moroccan_stock_intelligence.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
