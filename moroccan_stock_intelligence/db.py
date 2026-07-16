from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import Base

LOG = logging.getLogger(__name__)


def ensure_sqlite_parent(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.replace("sqlite:///", "", 1))
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    ensure_sqlite_parent(url)
    # timeout lets a writer wait for a concurrent lock instead of failing instantly
    # (scheduled jobs and the manual /api/run-now trigger can overlap on SQLite).
    connect_args = (
        {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    )
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


BASELINE_REVISION = "50c59b463e1e"


def init_db(engine: Engine) -> None:
    """Create any missing tables, and tell Alembic where this database stands.

    `create_all` stays: it is what boots a fresh database, and it is idempotent.
    What it cannot do is CHANGE an existing table — it creates what is absent and
    silently ignores everything else — which is why altering or dropping anything
    needs a migration (see AUDIT_TECHNIQUE.md §10, and `migrations/`).

    So the two have to agree on a starting point. Without a stamp, Alembic thinks
    an existing database is empty and tries to replay the baseline against tables
    that already exist. The rule is:

      * fresh database — `create_all` just built the current schema, so stamp HEAD:
        there is nothing older to migrate;
      * pre-Alembic database (the deployed one) — stamp the BASELINE, so pending
        migrations still run. Stamping it HEAD would silently skip them, and the
        orphan `signals` table would live forever;
      * already stamped — leave it alone, migrations own it now.

    This never RUNS a migration. Auto-migrating on boot would mean a bad migration
    takes the app down on deploy, with no chance to take a backup first. Applying
    them is an explicit `cli migrate`.
    """
    inspector = inspect(engine)
    tables_before = set(inspector.get_table_names())
    pre_existing = bool(tables_before - {"alembic_version"})

    Base.metadata.create_all(engine)

    if "alembic_version" in tables_before:
        with engine.connect() as connection:
            stamped = connection.execute(text("SELECT version_num FROM alembic_version")).first()
        if stamped:
            return

    revision = BASELINE_REVISION if pre_existing else _head_revision()
    if revision is None:
        return
    _stamp(engine, revision)
    LOG.info(
        "alembic_stamped revision=%s reason=%s",
        revision,
        "pre_alembic_database" if pre_existing else "fresh_database",
    )


def _head_revision() -> str | None:
    """The newest revision on disk, or None when migrations are not available."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic is a runtime dependency
        return None
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def _stamp(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"), {"rev": revision}
        )


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
