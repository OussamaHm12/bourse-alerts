from __future__ import annotations

import argparse
import logging
import os

from sqlalchemy import select

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.logging_config import configure_logging
from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import save_notification, store_news
from moroccan_stock_intelligence.services.alerts import build_daily_summary
from moroccan_stock_intelligence.services.alerts import dispatch_urgent_holding_alerts
from moroccan_stock_intelligence.services.collector import (
    collect_market_snapshots,
    persist_snapshots,
)
from moroccan_stock_intelligence.services.backup import render_result, run_backup
from moroccan_stock_intelligence.services.digest import (
    build_digest,
    build_intraday_update,
    build_push_payload,
    html_to_text,
)
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
from moroccan_stock_intelligence.services.push import send_push_to_all

LOG = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Moroccan Stock Intelligence Platform")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init-db")
    subparsers.add_parser("collect")
    subparsers.add_parser("analyze")
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
    migrate_parser = subparsers.add_parser(
        "migrate", help="apply pending Alembic migrations (take a backup first)"
    )
    migrate_parser.add_argument(
        "--to", default="head", help="target revision (default: head; e.g. -1 to roll back one)"
    )
    migrate_parser.add_argument(
        "--sql", action="store_true", help="print the SQL instead of running it"
    )
    subparsers.add_parser("migrate-status", help="show the current and pending revisions")
    copy_parser = subparsers.add_parser(
        "copy-database",
        help="copy every table to another backend (SQLite -> PostgreSQL). Read-only on the source.",
    )
    copy_parser.add_argument("--to", required=True, help="target URL, e.g. postgresql+psycopg://...")
    copy_parser.add_argument(
        "--from", dest="source", default=None, help="source URL (default: DATABASE_URL)"
    )
    backup_parser = subparsers.add_parser(
        "backup",
        help="snapshot the database, verify it, compress it, and rotate old copies",
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
    restore_parser = subparsers.add_parser(
        "restore-backup", help="replace the live database with a snapshot"
    )
    restore_parser.add_argument(
        "archive",
        nargs="?",
        help="path to a .db.gz snapshot; omit to use the newest in BACKUP_DIR",
    )
    restore_parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt (for scripted recovery)",
    )
    restore_parser.add_argument(
        "--no-safety-copy",
        action="store_true",
        help="do not keep a copy of the database being replaced (not recommended)",
    )
    health_parser = subparsers.add_parser(
        "data-health", help="which feeds are populated, fresh, and which analysts are blind"
    )
    health_parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of the table"
    )
    backtest_parser = subparsers.add_parser(
        "backtest", help="walk-forward validation of the scoring engine (audit §22 q4)"
    )
    backtest_parser.add_argument("--start", default=None, help="YYYY-MM-DD, default = earliest data")
    backtest_parser.add_argument("--end", default=None, help="YYYY-MM-DD, default = latest data")
    backtest_parser.add_argument(
        "--horizons", default="short,medium,long", help="comma-separated: short,medium,long"
    )
    backtest_parser.add_argument("--fees", type=float, default=None, help="round-trip fee rate, default settings.trading_fee_rate")
    backtest_parser.add_argument(
        "--step", type=int, default=5, help="simulate every Nth séance (5=weekly, 1=exhaustive)"
    )
    backtest_parser.add_argument(
        "--min-history-days", type=int, default=60, help="skip a symbol/date with less collected history than this"
    )
    backtest_parser.add_argument("--output", default=None, help="write the JSON report to this path")
    backtest_parser.add_argument(
        "--markdown", default=None, help="also write a human-readable Markdown report to this path"
    )
    backtest_parser.add_argument(
        "--ablation", action="store_true", help="also run the per-component ablation study (slow: N+1 backtests)"
    )
    backtest_parser.add_argument(
        "--benchmark", default="proxy", choices=["proxy"],
        help="'proxy' is the only benchmark available: no official MASI feed is collected (audit §4)",
    )
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
            run_backup_command(keep=args.keep)
        elif command == "migrate":
            run_migrate(target=args.to, sql_only=args.sql)
        elif command == "migrate-status":
            run_migrate_status()
        elif command == "copy-database":
            run_copy_database(source=args.source, target=args.to)
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
        elif command == "restore-backup":
            run_restore_command(
                archive=args.archive,
                assume_yes=args.yes,
                safety_copy=not args.no_safety_copy,
            )
        elif command == "data-health":
            import json as _json

            from moroccan_stock_intelligence.services import data_health

            report = data_health.check(session)
            print(
                _json.dumps(report.as_dict(), indent=2, ensure_ascii=False)
                if args.json
                else data_health.render(report)
            )
            # Non-zero when a feed is empty or stale, so this can be a cron guard
            # or a deploy check rather than something someone has to remember to read.
            if not report.healthy:
                raise SystemExit(1)
        elif command == "backtest":
            run_backtest_command(
                session,
                start=args.start,
                end=args.end,
                horizons=args.horizons,
                fees=args.fees,
                step=args.step,
                min_history_days=args.min_history_days,
                output=args.output,
                markdown=args.markdown,
                ablation=args.ablation,
            )
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


def _alembic_config():
    from pathlib import Path

    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def run_migrate(*, target: str, sql_only: bool) -> None:
    """Apply migrations. Explicit on purpose — never on boot.

    Auto-migrating at startup would mean a bad migration takes the app down on
    deploy, with no opportunity to take a backup first. `cli backup && cli migrate`
    is the intended sequence, and `backup` exits non-zero when the snapshot cannot
    be verified, so it works as a gate.
    """
    from alembic import command

    LOG.info("migrate_start target=%s database=%s", target, settings.database_url.split("@")[-1])
    try:
        if target.startswith("-"):
            command.downgrade(_alembic_config(), target, sql=sql_only)
        else:
            command.upgrade(_alembic_config(), target, sql=sql_only)
    except Exception as exc:
        LOG.exception("migrate_failed target=%s", target)
        raise SystemExit(1) from exc
    LOG.info("migrate_done target=%s", target)


def run_migrate_status() -> None:
    from alembic import command

    config = _alembic_config()
    print("\n  Révision appliquée :")
    command.current(config, verbose=True)
    print("\n  Révisions disponibles (la plus récente en premier) :")
    command.history(config, verbose=False)


def run_copy_database(*, source: str | None, target: str) -> None:
    """Copy the database to another backend. Read-only on the source.

    Exits non-zero on any count mismatch: a migration that reports success without
    verifying is how data goes missing quietly.
    """
    from moroccan_stock_intelligence.services.db_migrate import (
        migrate_database,
        render_migration,
    )

    result = migrate_database(source or settings.database_url, target)
    print(render_migration(result))
    if not result.ok:
        raise SystemExit(1)


def run_backup_command(*, keep: int | None) -> None:
    """Snapshot the database. Exits non-zero if the snapshot is not verifiable.

    A failed integrity check exits 1 on purpose: this command is meant to be the
    gate in front of any destructive operation, and a gate that always opens is
    not a gate.
    """
    result = run_backup(keep=keep)
    print(render_result(result))
    if result.skipped_reason or not result.ok:
        raise SystemExit(1)


def run_restore_command(
    *, archive: str | None, assume_yes: bool, safety_copy: bool
) -> None:
    """Replace the live database with a snapshot, after confirming.

    Interactive by default. This is the one command in the CLI that destroys
    data, and the audit's point (§15) was that the restore path had never been
    exercised at all — so it should be hard to run by accident and easy to run
    correctly under pressure.

    `--yes` exists for scripted recovery, where a prompt would hang a runbook.
    """
    from pathlib import Path

    from moroccan_stock_intelligence.services.backup import (
        latest_archive,
        render_restore,
        restore_backup,
        sqlite_path,
    )

    source = Path(archive) if archive else latest_archive()
    if source is None:
        raise SystemExit(
            "no snapshot found — pass a path, or check BACKUP_DIR "
            f"({settings.backup_dir})"
        )

    target = sqlite_path()
    if target is None:
        raise SystemExit(
            "DATABASE_URL is not SQLite; restoring a PostgreSQL database is a "
            "pg_restore operation, not this command"
        )

    if not assume_yes:
        print(f"Restauration de : {source}")
        print(f"Vers            : {target}")
        if target.exists():
            size_mb = target.stat().st_size / 1_048_576
            print(f"\nLa base actuelle ({size_mb:.1f} Mo) sera REMPLACÉE.")
            if safety_copy:
                print("Une copie horodatée sera conservée à côté avant remplacement.")
            else:
                print("AUCUNE copie de sécurité ne sera conservée (--no-safety-copy).")
        if input("\nTaper 'oui' pour confirmer : ").strip().lower() != "oui":
            raise SystemExit("Restauration annulée.")

    result = restore_backup(source, safety_copy=safety_copy)
    print(render_restore(result))
    if not result.ok:
        raise SystemExit(1)


def run_backtest_command(  # noqa: ANN001, PLR0913
    session,
    *,
    start: str | None,
    end: str | None,
    horizons: str,
    fees: float | None,
    step: int,
    min_history_days: int,
    output: str | None,
    markdown: str | None,
    ablation: bool,
) -> None:
    """Walk forward through the collected history and report what the scores were worth.

    Prints the Markdown report to stdout so a run is readable without opening a
    file, and writes the JSON only when asked. The JSON is the artefact worth
    keeping — it carries every group statistic, not just the ones the summary
    prints.
    """
    import json
    from datetime import datetime
    from pathlib import Path

    from moroccan_stock_intelligence.services.backtest import (
        BacktestConfig,
        run_ablation,
        run_backtest,
        to_markdown,
    )

    def _parse(value: str | None) -> datetime | None:
        return datetime.strptime(value, "%Y-%m-%d") if value else None

    requested = tuple(h.strip() for h in horizons.split(",") if h.strip())
    unknown = [h for h in requested if h not in {"short", "medium", "long"}]
    if unknown:
        raise SystemExit(f"unknown horizon(s): {', '.join(unknown)}")

    config = BacktestConfig(
        start=_parse(start),
        end=_parse(end),
        horizons=requested,
        fee_rate=settings.trading_fee_rate if fees is None else fees,
        step=step,
        min_history_days=min_history_days,
    )

    result = run_backtest(session, config)
    if ablation:
        # Reported alongside rather than merged in: the ablation re-runs the whole
        # simulation per component, so its numbers are only comparable to each
        # other and to the reference it carries.
        result["ablation"] = run_ablation(session, config)

    print(to_markdown(result))

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        LOG.info("backtest_json_written path=%s", path)
    if markdown:
        path = Path(markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_markdown(result), encoding="utf-8")
        LOG.info("backtest_markdown_written path=%s", path)

    # Deliberately exits 0 even when the verdict is "no measurable edge". That is a
    # finding, not a failure — a non-zero exit would make an honest negative result
    # look like a broken run.


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
    metrics, scores = compute_state(session)
    LOG.info("analysis_complete metrics=%s", len(metrics))
    return {"metrics": metrics, "scores": scores}


def run_daily_summary(session) -> None:  # noqa: ANN001
    result = run_analysis(session)
    message = build_daily_summary(result["metrics"], result["scores"])  # type: ignore[arg-type]
    save_notification(session, "digest", "Résumé quotidien", message)
    session.commit()
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
    save_notification(session, "digest", period_label, html_to_text(message))
    title, body = build_push_payload(period_label, holdings)
    send_push_to_all(session, title, body, "/")
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
    save_notification(session, "intraday", period_label, html_to_text(message))
    title, body = build_push_payload(period_label, holdings)
    send_push_to_all(session, title, body, "/")
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
