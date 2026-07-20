"""Backups of the live database — the only irreversible risk this project carries.

Everything the platform knows lives in one SQLite file on one Railway volume:
three years of séances, every generated report, every prediction, the whole
learning history. The upstream source only re-serves a ~3-year rolling window
(see `collectors/history.py`), so anything older that is lost is lost *for good* —
no re-collection can rebuild it. Nothing else in the codebase fails that way.

Two tiers, because they defend against different accidents:

  * **local rotated copies** (on the volume) — cheap, fast to restore, and the
    answer to logical damage: a bad backfill, a hand-run UPDATE, a botched
    schema change. Useless if the volume itself goes.
  * **off-host copy** (Telegram) — the answer to losing the volume. Telegram is
    not a clever choice, it is the *available* one: the bot token and chat id
    already exist, the owner already reads that channel, and it adds no
    infrastructure and no cost — which is the standing constraint in
    ARCHITECTURE_AI_ANALYST.md §2.8 ("single-user, single-container, cheap").

Three details that are not incidental:

1. **The snapshot uses SQLite's online backup API, never a file copy.** The
   scheduler and the API share this database and their jobs can overlap, so a
   `cp` of a live file can capture a torn page and produce a backup that only
   *looks* like one. `Connection.backup()` is safe against concurrent writers by
   construction.
2. **Every snapshot is verified before it counts.** `PRAGMA integrity_check`
   runs against the copy, not the source. An unverified backup is a belief, not
   a backup.
3. **Shipping is best-effort; the local copy is not.** If Telegram is down, over
   quota, or the file is too big, the local snapshot still stands and the run
   still reports success — with the reason recorded. Losing today's backup
   because an upload failed would be the opposite of the point.

PostgreSQL is out of scope here on purpose: its backup is `pg_dump`, not a file
snapshot, and production is SQLite today (HANDOVER.md §1). A non-SQLite URL is
skipped honestly rather than half-handled.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.telegram import send_telegram_document

LOG = logging.getLogger(__name__)

# Backups are named so rotation can glob exactly its own files and never touch
# anything else that happens to live in the backup directory.
PREFIX = "market-"
SUFFIX = ".db.gz"
_GLOB = f"{PREFIX}*{SUFFIX}"

SQLITE_URL_PREFIX = "sqlite:///"


@dataclass(frozen=True)
class BackupResult:
    """What actually happened. `ok` is the single question a caller should ask.

    `skipped_reason` and `error` are kept apart because they mean opposite things:
    skipped = we did not try (not SQLite, no file yet), which is normal;
    error = we tried and it failed, which is an incident.
    """

    path: Path | None = None
    size_bytes: int = 0
    integrity_ok: bool = False
    shipped: bool = False
    ship_error: str | None = None
    rotated: tuple[str, ...] = ()
    skipped_reason: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when a verified local snapshot exists. Shipping is not part of this.

        Deliberate: an off-host copy is the goal, but a verified local snapshot
        that could not be uploaded is still a backup. Failing the whole run on a
        Telegram hiccup would throw away the artefact we just built.
        """
        return self.path is not None and self.integrity_ok and self.error is None

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


def sqlite_path(database_url: str | None = None) -> Path | None:
    """The on-disk file behind a SQLite URL, or None for any other backend.

    NOTE the URL is relative (`sqlite:///data/market.db`), so this resolves
    against the CWD: `/app/data/market.db` in the container, `./data/market.db`
    on a laptop. That ambiguity is exactly why a backup must run *inside* the
    container (see AUDIT_TECHNIQUE.md §10).
    """
    url = database_url or settings.database_url
    if not url.startswith(SQLITE_URL_PREFIX):
        return None
    raw = url[len(SQLITE_URL_PREFIX) :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def _snapshot(source_db: Path, target_db: Path) -> None:
    """Consistent copy via SQLite's online backup API.

    Reads the source read-only and lets SQLite serialise against any writer that
    happens to be mid-transaction. An uncommitted transaction is simply not in
    the copy — which is correct, not a loss.
    """
    source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(target_db)
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _integrity_ok(db: Path) -> bool:
    """Run SQLite's own integrity check against the COPY, not the source."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _compress(plain: Path, archive: Path) -> None:
    with plain.open("rb") as src, gzip.open(archive, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)


def rotate(backup_dir: Path, keep: int) -> tuple[str, ...]:
    """Delete all but the `keep` newest snapshots. Globs only our own names."""
    if keep < 1:
        return ()
    archives = sorted(backup_dir.glob(_GLOB), key=lambda p: p.name, reverse=True)
    removed: list[str] = []
    for stale in archives[keep:]:
        try:
            stale.unlink()
            removed.append(stale.name)
        except OSError as exc:  # a rotation failure must never fail the backup
            LOG.warning("backup_rotate_failed file=%s error=%s", stale.name, exc)
    return tuple(removed)


def restore_readable(archive: Path, target: Path) -> bool:
    """Decompress a snapshot and confirm it opens and passes integrity check.

    Used by the tests to prove an archive is restorable — a backup nobody has
    ever restored is an assumption.
    """
    with gzip.open(archive, "rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return _integrity_ok(target)


@dataclass(frozen=True)
class RestoreResult:
    ok: bool
    archive: str
    target: str
    safety_copy: str | None = None
    tables: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _table_counts(db: Path) -> dict[str, int]:
    """Row count per table, used to prove a restore actually carried the data."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        conn.close()


def restore_backup(
    archive: Path,
    *,
    target: Path | None = None,
    safety_copy: bool = True,
) -> RestoreResult:
    """Replace the live database with a snapshot.

    THE ORDER HERE IS THE WHOLE POINT
    ---------------------------------
    The audit noted the backup was verified but the RESTORE never was
    (AUDIT_2026-07-18.md §15) — an untested restore path is an assumption, and
    it is the assumption you discover is wrong at the worst possible moment.

    Every step happens before anything destructive:

      1. decompress to a temporary file
      2. integrity-check the DECOMPRESSED COPY — a corrupt archive must be
         discovered while the live database is still the live database
      3. copy the current database aside (a restore is itself a change that can
         be regretted, and "I restored the wrong snapshot" needs an undo)
      4. only then, atomically replace

    `os.replace` is atomic within a filesystem, so there is no instant where the
    database file is absent or half-written.

    Refuses to touch a non-SQLite target: on PostgreSQL this operation belongs to
    `pg_restore`, and quietly doing nothing would be worse than refusing.
    """
    destination = target or sqlite_path()
    if destination is None:
        return RestoreResult(
            ok=False,
            archive=str(archive),
            target="",
            error="DATABASE_URL is not SQLite — restore is a pg_restore operation there",
        )
    if not archive.exists():
        return RestoreResult(
            ok=False, archive=str(archive), target=str(destination), error="archive not found"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_suffix(f"{destination.suffix}.restore-staging")
    kept: Path | None = None

    try:
        # 1 + 2 — verify before the live database is at any risk.
        with gzip.open(archive, "rb") as src, staged.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        if not _integrity_ok(staged):
            staged.unlink(missing_ok=True)
            return RestoreResult(
                ok=False,
                archive=str(archive),
                target=str(destination),
                error="the archive failed its integrity check — the live database was NOT touched",
            )

        counts = _table_counts(staged)

        # 3 — an undo for the restore itself.
        if safety_copy and destination.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            kept = destination.with_name(f"{destination.name}.pre-restore-{stamp}")
            shutil.copy2(destination, kept)
            LOG.info("restore_safety_copy path=%s", kept)

        # 4 — atomic swap.
        os.replace(staged, destination)
        LOG.info(
            "restore_done archive=%s target=%s tables=%s rows=%s",
            archive.name,
            destination,
            len(counts),
            sum(counts.values()),
        )
        return RestoreResult(
            ok=True,
            archive=str(archive),
            target=str(destination),
            safety_copy=str(kept) if kept else None,
            tables=len(counts),
            rows=counts,
        )
    # EOFError is listed explicitly because it is NOT an OSError: gzip raises it
    # when a stream ends before its end-of-stream marker, which is exactly what a
    # half-uploaded or interrupted archive looks like. Without it a truncated
    # backup crashed the command instead of failing cleanly — and a crash
    # mid-restore is the worst possible moment to lose the error handling.
    # (gzip.BadGzipFile is not listed: it subclasses OSError, already covered.)
    except (OSError, EOFError, sqlite3.Error) as exc:
        staged.unlink(missing_ok=True)
        LOG.exception("restore_failed archive=%s", archive)
        return RestoreResult(
            ok=False,
            archive=str(archive),
            target=str(destination),
            safety_copy=str(kept) if kept else None,
            error=f"{type(exc).__name__}: {exc}",
        )


def latest_archive(backup_dir: Path | None = None) -> Path | None:
    """The newest snapshot. Names are timestamp-sorted, so lexical order is time order."""
    directory = backup_dir or Path(settings.backup_dir)
    if not directory.exists():
        return None
    archives = sorted(directory.glob(_GLOB), reverse=True)
    return archives[0] if archives else None


def render_restore(result: RestoreResult) -> str:
    if not result.ok:
        return f"ÉCHEC de la restauration — {result.error}"
    lines = [
        "Restauration réussie.",
        f"  archive : {result.archive}",
        f"  base    : {result.target}",
        f"  tables  : {result.tables}",
        f"  lignes  : {sum(result.rows.values())}",
    ]
    if result.safety_copy:
        lines.append(f"  copie de sécurité (avant restauration) : {result.safety_copy}")
    # Per-table counts, biggest first. A single total tells you the restore ran;
    # the breakdown is what lets you say "yes, that is my database" — which is the
    # question you are actually asking after a recovery.
    populated_tables = sorted(
        ((name, count) for name, count in result.rows.items() if count),
        key=lambda item: item[1],
        reverse=True,
    )
    if populated_tables:
        lines.append("  détail  :")
        lines.extend(f"    {name:<22} {count:>8}" for name, count in populated_tables)
    return "\n".join(lines)


def run_backup(
    *,
    database_url: str | None = None,
    backup_dir: Path | None = None,
    keep: int | None = None,
    ship: bool | None = None,
    max_upload_mb: float | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Snapshot → verify → compress → ship (best-effort) → rotate.

    Never raises for an expected condition (missing DB, wrong backend, upload
    refused): those return a `BackupResult` carrying the reason, so the caller
    decides. A genuine bug still propagates.
    """
    backup_dir = backup_dir or Path(settings.backup_dir)
    keep = settings.backup_keep if keep is None else keep
    ship = settings.backup_to_telegram if ship is None else ship
    max_upload_mb = settings.backup_max_upload_mb if max_upload_mb is None else max_upload_mb
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")

    source = sqlite_path(database_url)
    if source is None:
        reason = "La base n'est pas SQLite : sauvegarde par pg_dump, hors périmètre de ce job."
        LOG.warning("backup_skipped reason=not_sqlite url_backend=%s", (database_url or settings.database_url).split(":")[0])
        return BackupResult(skipped_reason=reason)
    if not source.exists():
        reason = f"Base introuvable à {source} : rien à sauvegarder."
        LOG.warning("backup_skipped reason=source_missing path=%s", source)
        return BackupResult(skipped_reason=reason)

    backup_dir.mkdir(parents=True, exist_ok=True)
    plain = backup_dir / f"{PREFIX}{stamp}.db"
    archive = backup_dir / f"{PREFIX}{stamp}{SUFFIX}"

    try:
        try:
            _snapshot(source, plain)
        except sqlite3.DatabaseError as exc:
            # The source itself is unreadable or damaged. This is the single most
            # important thing a backup tool can say out loud, so it is an error
            # with a plain message — not a traceback and not a silent skip.
            LOG.error("backup_source_unreadable path=%s error=%s", source, exc)
            return BackupResult(
                error=(
                    f"Base source illisible ou corrompue ({exc}). "
                    "Aucune sauvegarde produite — la base elle-même est en cause."
                )
            )
        if not _integrity_ok(plain):
            LOG.error("backup_integrity_failed path=%s", plain)
            return BackupResult(
                error=(
                    "Le contrôle d'intégrité de la copie a échoué : la base source "
                    "est probablement endommagée."
                )
            )
        _compress(plain, archive)
    finally:
        plain.unlink(missing_ok=True)

    size = archive.stat().st_size
    result = BackupResult(path=archive, size_bytes=size, integrity_ok=True)

    shipped, ship_error = False, None
    if ship:
        shipped, ship_error = _ship(archive, size, max_upload_mb, stamp)

    rotated = rotate(backup_dir, keep)
    LOG.info(
        "backup_done path=%s size_kb=%s integrity=ok shipped=%s rotated=%s",
        archive.name,
        size // 1024,
        shipped,
        len(rotated),
    )
    return BackupResult(
        path=archive,
        size_bytes=size,
        integrity_ok=True,
        shipped=shipped,
        ship_error=ship_error,
        rotated=rotated,
    )


def _ship(archive: Path, size: int, max_upload_mb: float, stamp: str) -> tuple[bool, str | None]:
    """Push the snapshot off-host. Returns (shipped, error) — never raises."""
    size_mb = size / (1024 * 1024)
    if size_mb > max_upload_mb:
        # Telegram refuses bot uploads above ~50 MB. Say so plainly rather than
        # letting the API return an opaque error every night from now on.
        msg = (
            f"Archive de {size_mb:.1f} Mo au-dessus de la limite d'envoi ({max_upload_mb:.0f} Mo) : "
            "copie hors-hôte non envoyée. La sauvegarde locale est intacte."
        )
        LOG.warning("backup_ship_skipped reason=too_large size_mb=%.1f", size_mb)
        return False, msg
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        msg = "Identifiants Telegram absents : copie hors-hôte non envoyée."
        LOG.warning("backup_ship_skipped reason=no_credentials")
        return False, msg

    caption = f"Sauvegarde base — {stamp} ({size_mb:.1f} Mo)"
    try:
        send_telegram_document(archive, caption=caption)
    except Exception as exc:  # noqa: BLE001 — shipping must never sink the backup
        LOG.warning("backup_ship_failed error=%s", exc)
        return False, f"Envoi hors-hôte échoué : {exc}"
    return True, None


def render_result(result: BackupResult) -> str:
    """Human-readable outcome for the CLI."""
    if result.skipped_reason:
        return f"\n  Sauvegarde ignorée — {result.skipped_reason}\n"
    if result.error:
        return f"\n  ÉCHEC — {result.error}\n"
    lines = [
        "",
        "  Sauvegarde terminée",
        "  " + "─" * 58,
        f"    Archive        : {result.path}",
        f"    Taille         : {result.size_mb:.2f} Mo",
        "    Intégrité      : ok (PRAGMA integrity_check sur la copie)",
        f"    Copie hors-hôte: {'envoyée sur Telegram' if result.shipped else 'non envoyée'}",
    ]
    if result.ship_error:
        lines.append(f"                     → {result.ship_error}")
    if result.rotated:
        lines.append(f"    Rotation       : {len(result.rotated)} ancienne(s) archive(s) supprimée(s)")
    lines.append("")
    return "\n".join(lines)
