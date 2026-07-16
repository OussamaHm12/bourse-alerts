"""Cross-backend copy tests.

These run SQLite → SQLite, which exercises everything except the dialect itself:
the ordering, the batching, the emptiness guard and the verification. The dialect
half — types, sequences, the app actually running on the result — cannot be proven
by a unit test, so it was proven against a real PostgreSQL 16 instead; the numbers
are in AUDIT_TECHNIQUE.md and the commit message. Both halves matter, and neither
substitutes for the other.

What is guarded here is the property the whole thing rests on: **the source is never
written to**. Losing this database is the one irreversible risk in the project, so a
failed migration must leave production exactly as it was.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, News, Notification, Price, Stock
from moroccan_stock_intelligence.services.db_migrate import migrate_database, render_migration


def _seed(url: str, *, stocks: int = 3, days: int = 5) -> None:
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        for i in range(stocks):
            s.add(Stock(id=i + 1, symbol=f"S{i}", company_name=f"Soc {i}", sector="Banques"))
        s.flush()
        start = datetime.now(UTC) - timedelta(days=days)
        for i in range(stocks):
            for d in range(days):
                s.add(
                    Price(
                        stock_id=i + 1,
                        observed_at=start + timedelta(days=d),
                        current_price=100.0 + d,
                        daily_variation=0.5,
                        volume=1000.0,
                        source="test",
                    )
                )
        s.add(
            News(
                stock_id=1,
                title="S0 : Détachement du dividende",
                url="https://x/1.pdf",
                source="Avis",
                event_type="ex_dividend",
                sentiment="neutral",
                impact_score=0.0,
            )
        )
        s.add(Notification(kind="digest", title="T", body="B"))
        s.commit()
    engine.dispose()


def _count(url: str, table) -> int:  # noqa: ANN001
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as c:
            return c.execute(select(func.count()).select_from(table)).scalar_one()
    finally:
        engine.dispose()


@pytest.fixture
def source_url(tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'src.db').as_posix()}"
    _seed(url)
    return url


@pytest.fixture
def target_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'dst.db').as_posix()}"


# --------------------------------------------------------------------------- #
# The copy                                                                     #
# --------------------------------------------------------------------------- #


def test_every_row_is_copied_and_verified(source_url, target_url):
    result = migrate_database(source_url, target_url)

    assert result.ok
    assert result.error is None
    assert {t.table for t in result.tables} == set(Base.metadata.tables)
    assert _count(target_url, Price.__table__) == 15
    assert _count(target_url, Stock.__table__) == 3
    assert _count(target_url, News.__table__) == 1


def test_content_survives_not_just_counts(source_url, target_url):
    migrate_database(source_url, target_url)

    engine = create_engine(target_url, future=True)
    try:
        with engine.connect() as c:
            row = c.execute(select(News.__table__)).one()
    finally:
        engine.dispose()

    assert row.title == "S0 : Détachement du dividende"
    assert row.event_type == "ex_dividend"
    assert row.impact_score == 0.0


def test_the_source_is_never_written_to(source_url, target_url):
    """The property everything else rests on: a failed migration must leave
    production exactly as it was."""
    before = {
        table.name: _count(source_url, table) for table in Base.metadata.sorted_tables
    }
    migrate_database(source_url, target_url)
    after = {table.name: _count(source_url, table) for table in Base.metadata.sorted_tables}
    assert before == after


def test_a_failure_leaves_the_source_intact(source_url):
    before = _count(source_url, Price.__table__)
    result = migrate_database(source_url, "postgresql+psycopg://nobody@127.0.0.1:1/nope")
    assert not result.ok
    assert result.error
    assert _count(source_url, Price.__table__) == before


# --------------------------------------------------------------------------- #
# The guard against duplicates                                                 #
# --------------------------------------------------------------------------- #


def test_a_non_empty_target_is_refused_before_anything_is_written(source_url, target_url):
    """Primary keys are copied as-is, so a populated target does not silently
    duplicate — it raises halfway through and leaves a partial database. Caught up
    front instead.

    An "allow anyway" flag existed briefly. This test is what removed it: its only
    reachable outcome was that crash, which makes it a trap, not an option.
    """
    _seed(target_url, stocks=1, days=1)
    before = _count(target_url, Price.__table__)

    result = migrate_database(source_url, target_url)

    assert not result.ok
    assert "Vider la cible" in result.error
    assert "ÉCHEC" in render_migration(result)
    assert _count(target_url, Price.__table__) == before, "nothing was written"


def test_an_empty_source_is_not_an_error(tmp_path):
    src = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    dst = f"sqlite:///{(tmp_path / 'out.db').as_posix()}"
    create_engine(src, future=True)
    Base.metadata.create_all(create_engine(src, future=True))

    result = migrate_database(src, dst)

    assert result.ok
    assert result.total_rows == 0


# --------------------------------------------------------------------------- #
# Batching                                                                     #
# --------------------------------------------------------------------------- #


def test_batching_copies_everything(source_url, target_url):
    """`prices` is ~59k rows in production and streamed, so the batch boundary must
    not drop or duplicate a row."""
    result = migrate_database(source_url, target_url, batch_rows=2)
    assert result.ok
    assert _count(target_url, Price.__table__) == 15


def test_render_lists_the_biggest_tables_first(source_url, target_url):
    text = render_migration(migrate_database(source_url, target_url))
    assert "prices" in text
    assert "Comptages vérifiés" in text
    assert "Retour arrière" in text
