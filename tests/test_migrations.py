"""Migration tests.

The schema was created by `create_all` and had no migration history at all
(AUDIT_TECHNIQUE.md §7, §10): `create_all` creates what is missing and silently
ignores everything else, so it can never alter or drop. The first column change
would have had to be done by hand, in production, with no rollback.

What is guarded here:

  * upgrade and downgrade both run, on a copy of a REAL pre-Alembic database —
    a fresh database would not exercise the interesting case;
  * data survives — this is the whole point, and the audit's top risk;
  * the drop of the orphan `signals` table is a no-op where the table was never
    created, so a fresh deploy and every test database are unaffected;
  * `init_db` stamps a pre-Alembic database at the BASELINE, not at head. Stamping
    head would mark pending migrations as already applied, and the orphan table
    would live forever with nothing reporting a problem.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from moroccan_stock_intelligence.db import BASELINE_REVISION, get_engine, init_db

# Downgrade targets are named explicitly rather than as "-1": relative steps
# silently retarget whenever a migration is added, so these tests would start
# asserting the wrong revision's effect while still passing on the day it lands.

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_config(tmp_path, monkeypatch):
    """Alembic pointed at a throwaway database.

    env.py reads settings.database_url, and Settings is frozen with defaults
    evaluated at class creation — so the module object is swapped, the same way the
    rest of the suite does it.
    """

    def _for(db_path: Path) -> Config:
        from dataclasses import replace

        from moroccan_stock_intelligence.config import settings as real

        url = f"sqlite:///{db_path.as_posix()}"
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(
            "moroccan_stock_intelligence.config.settings", replace(real, database_url=url)
        )
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", url)
        return config

    return _for


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()


def _pre_alembic_db(path: Path) -> None:
    """A database as it exists in production today: created by create_all, with the
    orphan `signals` table still in it, and no alembic_version."""
    engine = get_engine(f"sqlite:///{path.as_posix()}")
    from moroccan_stock_intelligence.models import Base

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE signals ("
                " id INTEGER NOT NULL PRIMARY KEY,"
                " stock_id INTEGER NOT NULL,"
                " generated_at DATETIME NOT NULL,"
                " signal_type VARCHAR(64) NOT NULL,"
                " score FLOAT,"
                " severity VARCHAR(32) NOT NULL,"
                " explanation TEXT NOT NULL,"
                " metrics_json TEXT,"
                " FOREIGN KEY(stock_id) REFERENCES stocks (id))"
            )
        )
        connection.execute(text("CREATE INDEX ix_signals_stock_id ON signals (stock_id)"))
        connection.execute(text("CREATE INDEX ix_signals_signal_type ON signals (signal_type)"))
        connection.execute(text("CREATE INDEX ix_signals_generated_at ON signals (generated_at)"))
        connection.execute(
            text("INSERT INTO stocks (symbol, company_name) VALUES ('ATW', 'ATTIJARIWAFA')")
        )
        connection.execute(
            text(
                "INSERT INTO signals (stock_id, generated_at, signal_type, severity, explanation)"
                " VALUES (1, '2026-01-01', 'price_crash', 'warning', 'x')"
            )
        )
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


# --------------------------------------------------------------------------- #
# The upgrade path, on a copy of a real pre-Alembic database.                  #
# --------------------------------------------------------------------------- #


def test_upgrade_drops_the_orphan_table_and_keeps_the_data(tmp_path, alembic_config):
    db = tmp_path / "prod.db"
    _pre_alembic_db(db)
    config = alembic_config(db)

    assert "signals" in _tables(db)

    command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")

    assert "signals" not in _tables(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
    finally:
        conn.close()


def test_downgrade_restores_the_schema(tmp_path, alembic_config):
    db = tmp_path / "prod.db"
    _pre_alembic_db(db)
    config = alembic_config(db)
    command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")
    assert "signals" not in _tables(db)

    command.downgrade(config, BASELINE_REVISION)

    assert "signals" in _tables(db), "downgrade must restore the shape"
    conn = sqlite3.connect(db)
    try:
        # The rows are gone: a drop is a drop. Acceptable ONLY because nothing read
        # this table — anything carrying real data would need its rows staged first.
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
    finally:
        conn.close()


def test_the_cycle_is_repeatable(tmp_path, alembic_config):
    db = tmp_path / "prod.db"
    _pre_alembic_db(db)
    config = alembic_config(db)
    command.stamp(config, BASELINE_REVISION)

    for _ in range(3):
        command.upgrade(config, "head")
        assert "signals" not in _tables(db)
        command.downgrade(config, BASELINE_REVISION)
        assert "signals" in _tables(db)

    command.upgrade(config, "head")
    assert "signals" not in _tables(db)


def test_dropping_signals_is_a_no_op_where_it_never_existed(tmp_path, alembic_config):
    """Fresh deploys and every test database never had the table."""
    db = tmp_path / "fresh.db"
    config = alembic_config(db)

    command.upgrade(config, "head")  # builds everything from the baseline

    tables = _tables(db)
    assert "signals" not in tables
    assert "prices" in tables
    assert "news" in tables


def test_the_baseline_builds_the_whole_schema(tmp_path, alembic_config):
    db = tmp_path / "fresh.db"
    config = alembic_config(db)
    command.upgrade(config, "head")

    from moroccan_stock_intelligence.models import Base

    assert set(Base.metadata.tables) <= _tables(db)


def test_a_migrated_database_matches_the_models(tmp_path, alembic_config):
    """The baseline must not drift from models.py: autogenerate on a migrated
    database has to find nothing left to do."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from moroccan_stock_intelligence.models import Base

    db = tmp_path / "fresh.db"
    config = alembic_config(db)
    command.upgrade(config, "head")

    engine = get_engine(f"sqlite:///{db.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], f"the schema and the models have drifted: {diff}"


# --------------------------------------------------------------------------- #
# init_db has to agree with Alembic about where a database stands.            #
# --------------------------------------------------------------------------- #


def test_init_db_stamps_a_pre_alembic_database_at_the_BASELINE(tmp_path):
    """Not at head — that would mark the pending drop as already applied, and the
    orphan table would survive forever with nothing reporting a problem."""
    db = tmp_path / "prod.db"
    _pre_alembic_db(db)

    engine = get_engine(f"sqlite:///{db.as_posix()}")
    try:
        init_db(engine)
        with engine.connect() as connection:
            stamped = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    assert stamped == BASELINE_REVISION
    assert "signals" in _tables(db), "init_db must not migrate; that is `cli migrate`"


def test_init_db_stamps_a_fresh_database_at_head(tmp_path):
    """create_all just built the current schema, so there is nothing older to run."""
    db = tmp_path / "fresh.db"
    engine = get_engine(f"sqlite:///{db.as_posix()}")
    try:
        init_db(engine)
        with engine.connect() as connection:
            stamped = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    assert stamped is not None
    assert stamped != BASELINE_REVISION, "a fresh database has no pending migration"


def test_init_db_leaves_an_already_stamped_database_alone(tmp_path):
    db = tmp_path / "prod.db"
    _pre_alembic_db(db)
    engine = get_engine(f"sqlite:///{db.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('deadbeef')")
            )
        init_db(engine)
        with engine.connect() as connection:
            stamped = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    assert stamped == "deadbeef", "migrations own the stamp once it exists"


def test_init_db_is_still_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    engine = get_engine(f"sqlite:///{db.as_posix()}")
    try:
        init_db(engine)
        init_db(engine)  # must not raise
        assert "prices" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
