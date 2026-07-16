"""Copy the whole database from one backend to another (SQLite → PostgreSQL).

Production runs SQLite on a Railway volume. PostgreSQL is supported by the config
and the driver is installed, but has never been used (HANDOVER.md §1 lists it under
"not done"). This is the tool that makes the switch provable rather than hopeful.

Why not `pg_dump`/`.dump`: SQLite's dump is SQLite SQL. Its types, its AUTOINCREMENT
and its date handling do not survive contact with Postgres. Going through the ORM
metadata means every column lands with the type the models declare, on either
backend, and the same code works for a Postgres → SQLite rollback.

Three properties this must have, because losing this database is the one
irreversible risk in the project (the history endpoint only re-serves ~3 rolling
years, so what is lost past that is lost for good):

1. **Read-only on the source.** The source is never opened for writing. If anything
   fails, production is untouched and the answer is "do nothing".
2. **Verified, not assumed.** Row counts are compared per table AFTER the copy, and
   a mismatch is an error, not a warning. A migration that reports success without
   checking is how data goes missing quietly.
3. **Refuses a non-empty target, with no override.** Primary keys are copied
   explicitly, so a populated target does not produce duplicates — it produces an
   IntegrityError halfway through, leaving a partial copy. An "allow anyway" flag
   was written and removed: its only reachable outcome was that crash, which makes
   it a trap rather than an option. Empty the target first; it is one command and
   it is explicit.

Sequence reset is deliberate: Postgres tracks identity columns with a sequence, and
copying explicit ids leaves it at 1, so the next INSERT collides with an existing
row. SQLite has no such notion, which is exactly why this is easy to forget and
only fails later, on the first write.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine

from moroccan_stock_intelligence.models import Base

LOG = logging.getLogger(__name__)

# Big tables are streamed rather than materialised: `prices` is ~59k rows today and
# only grows.
BATCH_ROWS = 2_000


@dataclass
class TableResult:
    table: str
    source_rows: int = 0
    copied_rows: int = 0
    target_rows: int = 0

    @property
    def ok(self) -> bool:
        return self.source_rows == self.target_rows


@dataclass
class MigrationResult:
    tables: list[TableResult] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.tables) and all(t.ok for t in self.tables)

    @property
    def total_rows(self) -> int:
        return sum(t.copied_rows for t in self.tables)


def _row_count(engine: Engine, table) -> int:  # noqa: ANN001
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


def _reset_sequences(engine: Engine) -> None:
    """Point each identity sequence past the ids we just copied.

    Only PostgreSQL needs this, and only after an explicit-id insert: the sequence
    still sits at 1, so the next INSERT collides with row 1. SQLite has no sequence,
    which is why this is easy to miss — it fails on the first write after the switch,
    not during the migration.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            column = pk[0].name
            connection.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{column}'), "
                    f"COALESCE((SELECT MAX({column}) FROM {table.name}), 0) + 1, false)"
                )
            )
    LOG.info("sequences_reset backend=postgresql")


def migrate_database(
    source_url: str,
    target_url: str,
    *,
    batch_rows: int = BATCH_ROWS,
) -> MigrationResult:
    """Copy every table from `source_url` to `target_url`, then verify the counts."""
    source = create_engine(source_url, future=True)
    target = create_engine(target_url, future=True)
    result = MigrationResult()
    try:
        Base.metadata.create_all(target)

        existing = {
            table.name: _row_count(target, table)
            for table in Base.metadata.sorted_tables
            if table.name in set(inspect(target).get_table_names())
        }
        populated = {name: n for name, n in existing.items() if n}
        if populated:
            result.error = (
                f"La cible contient déjà des données ({populated}). "
                "Les clés primaires sont copiées telles quelles : la copie échouerait "
                "à mi-parcours sur une violation d'unicité, en laissant une base "
                "partielle. Vider la cible d'abord."
            )
            return result

        # sorted_tables is dependency-ordered, so a foreign key never points at a
        # row that has not been inserted yet.
        for table in Base.metadata.sorted_tables:
            table_result = TableResult(table=table.name)
            table_result.source_rows = _row_count(source, table)

            with source.connect() as read, target.begin() as write:
                cursor = read.execution_options(stream_results=True).execute(select(table))
                while True:
                    rows = cursor.fetchmany(batch_rows)
                    if not rows:
                        break
                    write.execute(table.insert(), [dict(row._mapping) for row in rows])
                    table_result.copied_rows += len(rows)

            table_result.target_rows = _row_count(target, table)
            result.tables.append(table_result)
            LOG.info(
                "table_migrated table=%s rows=%s verified=%s",
                table.name,
                table_result.copied_rows,
                table_result.ok,
            )

        mismatched = [t for t in result.tables if not t.ok]
        if mismatched:
            result.error = "Écart de comptage après copie : " + ", ".join(
                f"{t.table} ({t.source_rows} → {t.target_rows})" for t in mismatched
            )
            return result

        _reset_sequences(target)
    # Broad by design: any failure is reported through the result, so the caller
    # decides. A half-migrated database that raised a traceback and said nothing
    # else would be the worst outcome available.
    except Exception as exc:  # noqa: BLE001
        LOG.exception("migration_failed")
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        source.dispose()
        target.dispose()
    return result


def render_migration(result: MigrationResult) -> str:
    lines = ["", "  Migration de base de données", "  " + "─" * 60]
    for table in sorted(result.tables, key=lambda t: -t.copied_rows):
        mark = "ok" if table.ok else "ÉCART"
        lines.append(f"    {table.table:24s} {table.copied_rows:>7} lignes   {mark}")
    lines.append("  " + "─" * 60)
    lines.append(f"    Total : {result.total_rows} lignes sur {len(result.tables)} tables")
    if result.error:
        lines += ["", f"  ÉCHEC — {result.error}", "  La source n'a pas été modifiée.", ""]
    else:
        lines += [
            "",
            "  Comptages vérifiés table par table. La source est intacte.",
            "  Bascule : changer DATABASE_URL, redéployer, puis vérifier /api/health.",
            "  Retour arrière : remettre l'ancien DATABASE_URL — la source n'a pas bougé.",
            "",
        ]
    return "\n".join(lines)
