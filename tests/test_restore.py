"""A backup nobody has ever restored is an assumption, not a backup.

The audit (AUDIT_2026-07-18.md §15) found the snapshot path verified and the
RESTORE path never exercised at all. That asymmetry is the dangerous one: you
discover it at the moment you can least afford to.

The tests below drive the full loop — populate, snapshot, destroy, restore,
compare row by row — and pin the ordering guarantees that make a restore safe to
run under pressure:

  * a corrupt archive is detected BEFORE the live database is touched
  * the database being replaced is copied aside first
  * the swap is atomic, so there is no window where the file is half-written
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, Favorite, News, Price, Stock
from moroccan_stock_intelligence.services.backup import (
    latest_archive,
    render_restore,
    restore_backup,
    run_backup,
    sqlite_path,
)


@pytest.fixture
def populated(tmp_path) -> Path:
    """A database with a known, checkable shape."""
    db = tmp_path / "market.db"
    engine = create_engine(f"sqlite:///{db.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        for index, symbol in enumerate(("ATW", "IAM", "BCP"), start=1):
            session.add(Stock(id=index, symbol=symbol, company_name=f"{symbol} SA"))
            for day in range(25):
                session.add(
                    Price(
                        stock_id=index,
                        observed_at=datetime.now(UTC) - timedelta(days=day),
                        current_price=100.0 + day,
                        source="test",
                    )
                )
        session.add(Favorite(stock_id=1, symbol="ATW", note="cœur de portefeuille"))
        session.add(
            News(
                stock_id=1,
                title="ATW : Détachement du dividende",
                url="https://example.test/1.pdf",
                source="test",
                event_type="ex_dividend",
            )
        )
        session.commit()
    engine.dispose()
    return db


def counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def snapshot(populated: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    result = run_backup(
        database_url=f"sqlite:///{populated.as_posix()}",
        backup_dir=backup_dir,
        keep=5,
    )
    assert result.ok, result.error
    assert result.path is not None
    return result.path


# --------------------------------------------------------------------------- #
# The full loop                                                                #
# --------------------------------------------------------------------------- #


def test_a_destroyed_database_is_restored_exactly(populated, tmp_path):
    """Populate, snapshot, destroy, restore — and compare every table."""
    before = counts(populated)
    assert before["prices"] == 75

    archive = snapshot(populated, tmp_path / "backups")

    populated.unlink()
    assert not populated.exists(), "precondition: the database is genuinely gone"

    result = restore_backup(archive, target=populated)

    assert result.ok, result.error
    assert populated.exists()
    assert counts(populated) == before, "the restored database must match row for row"


def test_the_restored_data_is_readable_through_the_orm(populated, tmp_path):
    """Row counts alone would pass on a file that SQLite can open but the app cannot."""
    archive = snapshot(populated, tmp_path / "backups")
    populated.unlink()
    assert restore_backup(archive, target=populated).ok

    engine = create_engine(f"sqlite:///{populated.as_posix()}", future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as session:
            assert session.scalar(select(func.count()).select_from(Stock)) == 3
            favorite = session.scalar(select(Favorite))
            assert favorite.symbol == "ATW"
            assert favorite.note == "cœur de portefeuille", "non-ASCII survived the round trip"
            assert session.scalar(select(News)).event_type == "ex_dividend"
    finally:
        engine.dispose()


def test_restoring_over_a_live_database_replaces_it(populated, tmp_path):
    archive = snapshot(populated, tmp_path / "backups")
    original = counts(populated)

    # Diverge the live database from the snapshot.
    conn = sqlite3.connect(populated)
    conn.execute("DELETE FROM prices")
    conn.commit()
    conn.close()
    assert counts(populated)["prices"] == 0

    assert restore_backup(archive, target=populated).ok
    assert counts(populated) == original


# --------------------------------------------------------------------------- #
# Safety ordering — verify before destroying                                   #
# --------------------------------------------------------------------------- #


def test_a_corrupt_archive_leaves_the_live_database_untouched(populated, tmp_path):
    """The property that matters most: a bad archive must be found BEFORE the swap."""
    corrupt = tmp_path / "corrupt.db.gz"
    with gzip.open(corrupt, "wb") as handle:
        handle.write(b"this is not a SQLite database")

    before = counts(populated)
    result = restore_backup(corrupt, target=populated)

    assert not result.ok
    assert "integrity" in (result.error or "").lower()
    assert counts(populated) == before, "the live database was modified by a failed restore"


def test_a_truncated_archive_is_refused(populated, tmp_path):
    archive = snapshot(populated, tmp_path / "backups")
    truncated = tmp_path / "truncated.db.gz"
    truncated.write_bytes(archive.read_bytes()[: len(archive.read_bytes()) // 2])

    before = counts(populated)
    assert not restore_backup(truncated, target=populated).ok
    assert counts(populated) == before


def test_a_missing_archive_is_refused_cleanly(populated, tmp_path):
    result = restore_backup(tmp_path / "nope.db.gz", target=populated)
    assert not result.ok
    assert "not found" in (result.error or "")


def test_a_safety_copy_of_the_replaced_database_is_kept(populated, tmp_path):
    """A restore is itself a change that can be regretted."""
    archive = snapshot(populated, tmp_path / "backups")
    conn = sqlite3.connect(populated)
    conn.execute("DELETE FROM prices")
    conn.commit()
    conn.close()

    result = restore_backup(archive, target=populated)

    assert result.ok
    assert result.safety_copy is not None
    kept = Path(result.safety_copy)
    assert kept.exists()
    # The copy holds the state we replaced, not the state we restored.
    assert counts(kept)["prices"] == 0
    assert counts(populated)["prices"] == 75


def test_the_safety_copy_can_be_declined(populated, tmp_path):
    archive = snapshot(populated, tmp_path / "backups")
    result = restore_backup(archive, target=populated, safety_copy=False)
    assert result.ok
    assert result.safety_copy is None


def test_no_staging_file_survives_a_successful_restore(populated, tmp_path):
    archive = snapshot(populated, tmp_path / "backups")
    assert restore_backup(archive, target=populated).ok
    leftovers = list(populated.parent.glob("*.restore-staging"))
    assert leftovers == [], f"staging files left behind: {leftovers}"


def test_no_staging_file_survives_a_failed_restore(populated, tmp_path):
    corrupt = tmp_path / "corrupt.db.gz"
    with gzip.open(corrupt, "wb") as handle:
        handle.write(b"nope")
    restore_backup(corrupt, target=populated)
    assert list(populated.parent.glob("*.restore-staging")) == []


# --------------------------------------------------------------------------- #
# Reporting and helpers                                                        #
# --------------------------------------------------------------------------- #


def test_the_result_reports_what_was_restored(populated, tmp_path):
    archive = snapshot(populated, tmp_path / "backups")
    result = restore_backup(archive, target=populated)
    assert result.tables >= 3
    assert result.rows["prices"] == 75
    rendered = render_restore(result)
    assert "réussie" in rendered.lower()
    assert "75" in rendered or "prices" in rendered


def test_a_failure_renders_as_a_failure(populated, tmp_path):
    corrupt = tmp_path / "bad.db.gz"
    with gzip.open(corrupt, "wb") as handle:
        handle.write(b"nope")
    assert "ÉCHEC" in render_restore(restore_backup(corrupt, target=populated))


def test_latest_archive_picks_the_newest(tmp_path, populated):
    backups = tmp_path / "backups"
    backups.mkdir()
    for stamp in ("20260101T000000Z", "20260715T000000Z", "20260301T000000Z"):
        (backups / f"market-{stamp}.db.gz").write_bytes(b"x")
    assert latest_archive(backups).name == "market-20260715T000000Z.db.gz"


def test_latest_archive_on_an_empty_directory_is_none(tmp_path):
    (tmp_path / "empty").mkdir()
    assert latest_archive(tmp_path / "empty") is None
    assert latest_archive(tmp_path / "does-not-exist") is None


def test_a_non_sqlite_target_is_refused(monkeypatch, tmp_path):
    """On PostgreSQL this is a pg_restore operation; quietly doing nothing would
    be worse than refusing."""
    from moroccan_stock_intelligence.services import backup as backup_module

    monkeypatch.setattr(backup_module, "sqlite_path", lambda *_a, **_k: None)
    result = backup_module.restore_backup(tmp_path / "any.db.gz")
    assert not result.ok
    assert "pg_restore" in (result.error or "")


def test_sqlite_path_ignores_other_backends():
    assert sqlite_path("postgresql://localhost/market") is None
    assert sqlite_path("sqlite:///:memory:") is None
    assert sqlite_path("sqlite:///data/market.db") == Path("data/market.db")
