"""prediction semantics v2

Revision ID: 9f3c21d4a7e2
Revises: 1b2587ed6aab
Create Date: 2026-07-18

The learning engine recorded two things it should not have (AUDIT_2026-07-18.md §9):

  * WATCH and HOLD were mapped to a bullish "up" prediction. WATCH means "no
    direction dominates, wait for confirmation" and covers the whole 45-70 score
    band, i.e. most verdicts — so the engine mass-produced directional calls
    nobody made, and any hit rate computed from them would have measured how often
    the market rose rather than whether the platform was right.
  * `predicted_probability` was derived from `confidence`, which measures DATA
    COVERAGE (indicators available, history depth, signal agreement) and says
    nothing about whether a direction will occur.

Both are fixed in code. This migration adds the columns that let the fix apply
only to NEW rows.

WHY NOT REWRITE THE EXISTING ROWS

They are a faithful record of what the engine claimed at the time. Re-labelling a
stored WATCH from "up" to "flat" would invent a prediction that was never made,
and re-deriving its probability would need a signal strength that was never
stored. Rewriting history to make past performance look better-founded is the
opposite of what a calibration system is for.

So rows keep their semantics_version, and `load_evaluated_predictions` filters to
the current one. v1 rows stay queryable and simply do not contribute to statistics
computed under v2 rules.

Existing rows are backfilled to semantics_version=1 and claim_kind='direction',
which is exactly what they were written as. signal_strength and data_confidence
stay NULL for them: those values were never captured, and a plausible-looking
reconstruction would be fabricated data.

REVERSIBILITY

Downgrade drops the four columns. SQLite cannot DROP COLUMN before 3.35, so the
downgrade uses a batch operation, which rebuilds the table. Data in the dropped
columns is lost by definition; nothing outside the learning engine reads them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3c21d4a7e2"
down_revision: str | Sequence[str] | None = "1b2587ed6aab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "prediction_history"

def _new_columns() -> tuple[sa.Column, ...]:
    """Built fresh on each call.

    A Column object binds to the table it is added to, so reusing one across
    `add_column` and `batch_alter_table` raises. A factory is cheaper than
    reasoning about when a copy is safe.

    server_default is required on the NOT NULL columns: existing rows need a value
    at ALTER time. It is deliberately left in place rather than dropped in a second
    step — a default of 1 is the correct reading of any row written by code that
    predates this migration.
    """
    return (
        sa.Column("semantics_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claim_kind", sa.String(length=16), nullable=False, server_default="direction"),
        sa.Column("signal_strength", sa.Float(), nullable=True),
        sa.Column("data_confidence", sa.Float(), nullable=True),
    )


COLUMN_NAMES = ("semantics_version", "claim_kind", "signal_strength", "data_confidence")


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    """Idempotent: a database created by `create_all` after this revision already
    has the columns, and must see a no-op rather than a duplicate-column error.
    That includes every fresh deploy and every test database."""
    present = _existing_columns()
    if not present:
        return  # table absent entirely (a database predating the research tables)

    for column in _new_columns():
        if column.name not in present:
            op.add_column(TABLE, column)

    existing_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
    }
    for column_name in ("semantics_version", "claim_kind"):
        index_name = f"ix_prediction_history_{column_name}"
        if index_name not in existing_indexes:
            op.create_index(index_name, TABLE, [column_name], unique=False)


def downgrade() -> None:
    present = _existing_columns()
    if not present:
        return

    existing_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
    }
    for column_name in ("claim_kind", "semantics_version"):
        index_name = f"ix_prediction_history_{column_name}"
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE)

    # batch_alter_table rebuilds the table, which is how SQLite drops a column.
    with op.batch_alter_table(TABLE) as batch:
        for column_name in COLUMN_NAMES:
            if column_name in present:
                batch.drop_column(column_name)
