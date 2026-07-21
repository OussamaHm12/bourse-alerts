"""Backup tests — no network, real SQLite files on tmp_path.

Guards the properties that make a backup worth having:
  * the snapshot is complete and byte-faithful (row counts and content match);
  * it is taken safely WHILE another connection holds an open write transaction —
    the reason we use SQLite's online backup API instead of copying the file;
  * every archive is verified, and actually restorable (we decompress and reopen);
  * rotation keeps N and touches only our own filenames;
  * the run is local-only — no network call — and says so in its output;
  * a non-SQLite URL is skipped honestly rather than half-handled.
"""

from __future__ import annotations

import gzip
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moroccan_stock_intelligence.config import settings as real_settings
from moroccan_stock_intelligence.services import backup as bk


def _make_db(path: Path, rows: int = 50) -> None:
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE prices (id INTEGER PRIMARY KEY, symbol TEXT, price REAL)")
        conn.executemany(
            "INSERT INTO prices (symbol, price) VALUES (?, ?)",
            [(f"SYM{i:03d}", i * 1.5) for i in range(rows)],
        )
    conn.close()


@pytest.fixture
def source_db(tmp_path):
    db = tmp_path / "market.db"
    _make_db(db)
    return db


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Pin the settings object so a developer's own .env cannot steer these tests.

    `Settings` is a frozen dataclass, so the whole object is swapped (the repo's
    existing convention — see test_refresh / test_favorites).
    """
    monkeypatch.setattr(bk, "settings", replace(real_settings))


# --------------------------------------------------------------------------- #
# URL handling                                                                 #
# --------------------------------------------------------------------------- #


def test_sqlite_path_extracts_the_file():
    assert bk.sqlite_path("sqlite:///data/market.db") == Path("data/market.db")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:pw@host/db",
        "postgresql://user:pw@host/db",
        "sqlite:///:memory:",
        "sqlite:///",
    ],
)
def test_non_file_backends_have_no_path(url):
    assert bk.sqlite_path(url) is None


def test_postgres_url_is_skipped_honestly(backup_dir):
    """A Postgres backup is pg_dump, not a file snapshot. Say so; do not pretend."""
    result = bk.run_backup(
        database_url="postgresql://u:p@h/db", backup_dir=backup_dir
    )
    assert result.ok is False
    assert result.skipped_reason is not None
    assert "pg_dump" in result.skipped_reason
    assert not backup_dir.exists()  # nothing was created


def test_missing_source_is_skipped_not_crashed(tmp_path, backup_dir):
    result = bk.run_backup(
        database_url=f"sqlite:///{tmp_path / 'absent.db'}", backup_dir=backup_dir
    )
    assert result.ok is False
    assert "introuvable" in result.skipped_reason


# --------------------------------------------------------------------------- #
# The snapshot itself                                                          #
# --------------------------------------------------------------------------- #


def test_backup_produces_a_verified_archive(source_db, backup_dir):
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    assert result.ok is True
    assert result.integrity_ok is True
    assert result.path.exists()
    assert result.path.name.startswith("market-")
    assert result.path.name.endswith(".db.gz")
    assert result.size_bytes > 0


def test_archive_is_actually_restorable(source_db, backup_dir, tmp_path):
    """A backup nobody has restored is an assumption. Decompress it and read it back."""
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)

    restored = tmp_path / "restored.db"
    assert bk.restore_readable(result.path, restored) is True

    conn = sqlite3.connect(restored)
    try:
        assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 50
        assert conn.execute("SELECT symbol FROM prices WHERE id = 1").fetchone()[0] == "SYM000"
    finally:
        conn.close()


def test_restored_content_matches_the_source_exactly(source_db, backup_dir, tmp_path):
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    restored = tmp_path / "restored.db"
    bk.restore_readable(result.path, restored)

    def dump(db):
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT id, symbol, price FROM prices ORDER BY id").fetchall()
        finally:
            conn.close()

    assert dump(restored) == dump(source_db)


def test_snapshot_is_safe_while_a_writer_holds_an_open_transaction(source_db, backup_dir, tmp_path):
    """The whole reason we use the online backup API instead of copying the file.

    A writer is mid-transaction with uncommitted rows. `cp` here could capture a
    torn page; the backup API must yield a valid database holding the last
    COMMITTED state — the uncommitted rows are correctly absent, not corrupt.
    """
    writer = sqlite3.connect(source_db)
    writer.execute("BEGIN IMMEDIATE")
    writer.executemany(
        "INSERT INTO prices (symbol, price) VALUES (?, ?)",
        [(f"UNCOMMITTED{i}", 999.0) for i in range(200)],
    )
    try:
        result = bk.run_backup(
            database_url=f"sqlite:///{source_db}", backup_dir=backup_dir
        )
    finally:
        writer.rollback()
        writer.close()

    assert result.ok is True, "the snapshot must succeed despite a live writer"

    restored = tmp_path / "restored.db"
    assert bk.restore_readable(result.path, restored) is True
    conn = sqlite3.connect(restored)
    try:
        assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 50
        uncommitted = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE symbol LIKE 'UNCOMMITTED%'"
        ).fetchone()[0]
        assert uncommitted == 0
    finally:
        conn.close()


def test_intermediate_plain_copy_is_not_left_behind(source_db, backup_dir):
    """Only the compressed archive survives — an uncompressed twin would double the volume."""
    bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    assert list(backup_dir.glob("*.db")) == []
    assert len(list(backup_dir.glob("*.db.gz"))) == 1


def test_archive_is_really_gzip(source_db, backup_dir):
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    with gzip.open(result.path, "rb") as handle:
        assert handle.read(16).startswith(b"SQLite format 3")


def test_corrupt_source_fails_loudly_and_produces_no_archive(tmp_path, backup_dir):
    """A damaged source is the case a backup tool most needs to name clearly.

    It must not crash, must not emit an archive that only looks valid, and must
    say the SOURCE is the problem — not the backup.
    """
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is definitely not a sqlite database" * 40)

    result = bk.run_backup(database_url=f"sqlite:///{broken}", backup_dir=backup_dir)

    assert result.ok is False
    assert result.error is not None
    assert "corrompue" in result.error
    assert result.skipped_reason is None, "this is a failure, not a skip"
    assert list(backup_dir.glob("*.gz")) == []
    assert list(backup_dir.glob("*.db")) == [], "no half-written copy left behind"
    assert "ÉCHEC" in bk.render_result(result)


# --------------------------------------------------------------------------- #
# Rotation                                                                     #
# --------------------------------------------------------------------------- #


def test_rotation_keeps_the_newest_n(source_db, backup_dir):
    for hour in range(5):
        bk.run_backup(
            database_url=f"sqlite:///{source_db}",
            backup_dir=backup_dir,
            keep=3,
            now=datetime(2026, 7, 10, hour, tzinfo=UTC),
        )
    remaining = sorted(p.name for p in backup_dir.glob("market-*.db.gz"))
    assert len(remaining) == 3
    assert remaining == [
        "market-20260710T020000Z.db.gz",
        "market-20260710T030000Z.db.gz",
        "market-20260710T040000Z.db.gz",
    ]


def test_rotation_only_touches_our_own_files(source_db, backup_dir):
    backup_dir.mkdir(parents=True)
    bystander = backup_dir / "important-notes.txt"
    bystander.write_text("do not delete me")
    other_db = backup_dir / "someone-elses.db.gz"
    other_db.write_bytes(b"not ours")

    for hour in range(4):
        bk.run_backup(
            database_url=f"sqlite:///{source_db}",
            backup_dir=backup_dir,
            keep=1,
            now=datetime(2026, 7, 10, hour, tzinfo=UTC),
        )

    assert bystander.exists()
    assert other_db.exists()
    assert len(list(backup_dir.glob("market-*.db.gz"))) == 1


def test_keep_zero_disables_rotation(source_db, backup_dir):
    for hour in range(3):
        bk.run_backup(
            database_url=f"sqlite:///{source_db}",
            backup_dir=backup_dir,
            keep=0,
            now=datetime(2026, 7, 10, hour, tzinfo=UTC),
        )
    assert len(list(backup_dir.glob("market-*.db.gz"))) == 3


# --------------------------------------------------------------------------- #
# The backup is local-only, and says so.                                       #
# --------------------------------------------------------------------------- #


def test_backup_makes_no_network_call(source_db, backup_dir, monkeypatch):
    """There is no off-host copy any more, so nothing here may touch the network.

    Asserted rather than assumed: shipping was removed along with Telegram, and a
    reintroduced upload that silently failed would be worse than no upload at all.
    """
    import requests

    for verb in ("post", "get", "put"):
        monkeypatch.setattr(
            requests, verb, lambda *a, **k: pytest.fail("backup must not use the network")
        )
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    assert result.ok is True


def test_render_warns_that_the_copy_is_local_only(source_db, backup_dir):
    """The residual risk is stated on every successful run, not buried in a docstring.

    Losing the volume loses these backups with it, and the history API cannot
    re-serve seances older than ~3 years — so this warning is the only thing
    between the owner and a silent, permanent loss.
    """
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    text = bk.render_result(result)
    assert "Copie locale uniquement" in text
    assert "3 ans" in text


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #


def test_render_reports_success(source_db, backup_dir):
    result = bk.run_backup(database_url=f"sqlite:///{source_db}", backup_dir=backup_dir)
    text = bk.render_result(result)
    assert "Sauvegarde terminée" in text
    assert "Intégrité   : ok" in text


def test_render_reports_a_skip(backup_dir):
    result = bk.run_backup(database_url="postgresql://u:p@h/db", backup_dir=backup_dir)
    assert "ignorée" in bk.render_result(result)


def test_backup_is_repeatable(source_db, backup_dir):
    """Two runs a second apart must both stand — no clobbering, no interference."""
    first = bk.run_backup(
        database_url=f"sqlite:///{source_db}",
        backup_dir=backup_dir,
        now=datetime(2026, 7, 10, 22, 0, 0, tzinfo=UTC),
    )
    second = bk.run_backup(
        database_url=f"sqlite:///{source_db}",
        backup_dir=backup_dir,
        now=datetime(2026, 7, 10, 22, 0, 1, tzinfo=UTC),
    )
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()
    assert first.ok and second.ok
