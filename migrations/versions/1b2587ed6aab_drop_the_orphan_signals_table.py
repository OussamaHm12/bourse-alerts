"""drop the orphan signals table

Revision ID: 1b2587ed6aab
Revises: 50c59b463e1e
Create Date: 2026-07-16

`signals` was written on every analysis run — both digests plus three intraday
passes — by `generate_alerts`, which detected price crashes, volume spikes,
breakouts, support tests and high scores. Nothing ever read it: the digests query
neither it nor `alerts`, `dispatch_unsent_alerts` was reachable only from a
`send-alerts` command no job triggers, and its one reader was the Streamlit
dashboard, which was never deployed. Per-symbol notification has been thesis-based
since Phase 9 and is owned by `research/notifications`.

The writer and the model were removed in 468d8cd. The TABLE survived, because
`create_all` creates but never drops — which is the whole reason this migration
exists rather than a comment saying "it will sort itself out".

Guarded with a table-exists check: a database created after 468d8cd never had the
table, and this must be a no-op there rather than an error. That includes every
test database and every fresh deploy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b2587ed6aab"
down_revision: str | Sequence[str] | None = "50c59b463e1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_signals() -> bool:
    return "signals" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_signals():
        return
    op.drop_index("ix_signals_generated_at", table_name="signals")
    op.drop_index("ix_signals_signal_type", table_name="signals")
    op.drop_index("ix_signals_stock_id", table_name="signals")
    op.drop_table("signals")


def downgrade() -> None:
    """Recreate the table exactly as it was, so a rollback restores the schema.

    The ROWS are gone — a drop is a drop. That is acceptable here and nowhere else:
    nothing read this table, so nothing can miss its contents. Anything that carried
    real data would need its rows staged elsewhere before being dropped, not a
    downgrade that only rebuilds the shape.
    """
    if _has_signals():
        return
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_stock_id", "signals", ["stock_id"], unique=False)
    op.create_index("ix_signals_signal_type", "signals", ["signal_type"], unique=False)
    op.create_index("ix_signals_generated_at", "signals", ["generated_at"], unique=False)
