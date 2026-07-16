"""Reclassification backfill tests — no network, no live DB.

Guards the contracts that make it safe to run against the Railway Postgres:
  * dry-run is the default and writes NOTHING;
  * --apply rewrites only the three derived columns;
  * the title/url/source/dates/stock_id are never touched;
  * the run is idempotent (a second pass finds nothing to do);
  * a failure mid-run rolls back instead of leaving a half-written batch;
  * rows already agreeing with the classifier are reported as unchanged.

The seeded rows reproduce the exact values the old keyword model wrote into the
production DB (an ex-dividend detachment at +0.6, a dilutive capital increase
at +0.6), so the tests pin the real migration, not a synthetic one.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, News, Stock
from moroccan_stock_intelligence.services.news_backfill import (
    reclassify_news,
    render_report,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.add(Stock(symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        s.commit()
        yield s
    engine.dispose()


def _seed(session, rows: list[tuple[str, str, str, float]]) -> None:
    """Seed (title, event_type, sentiment, impact) rows as the OLD model wrote them."""
    for index, (title, event_type, sentiment, impact) in enumerate(rows):
        session.add(
            News(
                stock_id=1,
                title=title,
                url=f"https://www.casablanca-bourse.com/fr/avis/{index}.pdf",
                source="Casablanca Bourse Avis",
                published_at=datetime(2026, 7, 10, tzinfo=UTC),
                event_type=event_type,
                sentiment=sentiment,
                impact_score=impact,
            )
        )
    session.commit()


# The two rows that actually exist in production, with their wrong verdicts.
_LEGACY_ROWS = [
    ("ATW : Détachement du dividende", "dividend", "positive", 0.6000000000000001),
    ("CDM : Augmentation de capital en numéraire", "capital_action", "positive", 0.6000000000000001),
]


# --------------------------------------------------------------------------- #
# Dry-run: the default, and it must not write.                                 #
# --------------------------------------------------------------------------- #


def test_dry_run_is_the_default(session):
    _seed(session, _LEGACY_ROWS)
    report = reclassify_news(session)  # no apply= passed
    assert report.applied is False
    assert report.batches_committed == 0


def test_dry_run_writes_nothing(session):
    _seed(session, _LEGACY_ROWS)
    reclassify_news(session, apply=False)

    session.expire_all()  # force a re-read from the DB, not the identity map
    rows = session.scalars(select(News).order_by(News.id)).all()
    assert [r.sentiment for r in rows] == ["positive", "positive"]
    assert [r.event_type for r in rows] == ["dividend", "capital_action"]
    assert all(r.impact_score == pytest.approx(0.6) for r in rows)


def test_dry_run_reports_what_would_change(session):
    _seed(session, _LEGACY_ROWS)
    report = reclassify_news(session, apply=False)

    assert report.scanned == 2
    assert report.changed == 2
    assert report.unchanged == 0

    detachment = report.changes[0]
    assert detachment.old_event_type == "dividend"
    assert detachment.new_event_type == "ex_dividend"
    assert detachment.old_sentiment == "positive"
    assert detachment.new_sentiment == "neutral"
    assert detachment.old_impact == pytest.approx(0.6)
    assert detachment.new_impact == 0.0


def test_dry_run_aggregates_by_event_type_and_sentiment(session):
    _seed(session, _LEGACY_ROWS)
    report = reclassify_news(session, apply=False)

    assert report.before_events == {"dividend": 1, "capital_action": 1}
    assert report.after_events == {"ex_dividend": 1, "capital_increase_cash": 1}
    assert report.before_sentiments == {"positive": 2}
    assert report.after_sentiments == {"neutral": 1, "negative": 1}


# --------------------------------------------------------------------------- #
# Apply: writes the derived columns and nothing else.                          #
# --------------------------------------------------------------------------- #


def test_apply_writes_the_new_classification(session):
    _seed(session, _LEGACY_ROWS)
    report = reclassify_news(session, apply=True)
    assert report.applied is True
    assert report.changed == 2
    assert report.batches_committed == 1

    session.expire_all()
    rows = session.scalars(select(News).order_by(News.id)).all()
    assert rows[0].event_type == "ex_dividend"
    assert rows[0].sentiment == "neutral"
    assert rows[0].impact_score == 0.0
    assert rows[1].event_type == "capital_increase_cash"
    assert rows[1].sentiment == "negative"
    assert rows[1].impact_score == pytest.approx(-0.35)


def test_apply_never_touches_content_columns(session):
    """The classifier derives FROM the title — re-deriving must not rewrite it."""
    _seed(session, _LEGACY_ROWS)
    before = [
        (r.id, r.title, r.url, r.source, r.published_at, r.collected_at, r.stock_id)
        for r in session.scalars(select(News).order_by(News.id)).all()
    ]

    reclassify_news(session, apply=True)

    session.expire_all()
    after = [
        (r.id, r.title, r.url, r.source, r.published_at, r.collected_at, r.stock_id)
        for r in session.scalars(select(News).order_by(News.id)).all()
    ]
    assert before == after


def test_apply_does_not_add_or_delete_rows(session):
    _seed(session, _LEGACY_ROWS)
    reclassify_news(session, apply=True)
    session.expire_all()
    assert len(session.scalars(select(News)).all()) == 2


# --------------------------------------------------------------------------- #
# Idempotence.                                                                 #
# --------------------------------------------------------------------------- #


def test_second_run_changes_nothing(session):
    _seed(session, _LEGACY_ROWS)
    first = reclassify_news(session, apply=True)
    assert first.changed == 2

    second = reclassify_news(session, apply=True)
    assert second.scanned == 2
    assert second.changed == 0
    assert second.batches_committed == 0  # nothing touched -> no commit issued


def test_repeated_applies_converge_to_the_same_values(session):
    _seed(session, _LEGACY_ROWS)
    reclassify_news(session, apply=True)
    session.expire_all()
    once = [(r.event_type, r.sentiment, r.impact_score) for r in session.scalars(select(News)).all()]

    for _ in range(3):
        reclassify_news(session, apply=True)
    session.expire_all()
    thrice = [
        (r.event_type, r.sentiment, r.impact_score) for r in session.scalars(select(News)).all()
    ]
    assert once == thrice


def test_dry_run_after_apply_reports_a_clean_base(session):
    _seed(session, _LEGACY_ROWS)
    reclassify_news(session, apply=True)
    report = reclassify_news(session, apply=False)
    assert report.changed == 0
    assert "Aucun changement" in render_report(report)


# --------------------------------------------------------------------------- #
# Rows already correct.                                                        #
# --------------------------------------------------------------------------- #


def test_already_correct_rows_are_left_alone(session):
    _seed(
        session,
        [
            ("ATW : Détachement du dividende", "ex_dividend", "neutral", 0.0),
            ("XX : Profit warning sur le résultat annuel", "profit_warning", "negative", -0.85),
        ],
    )
    report = reclassify_news(session, apply=True)
    assert report.scanned == 2
    assert report.changed == 0
    assert report.unchanged == 2
    assert report.batches_committed == 0


def test_a_mixed_base_only_rewrites_the_stale_rows(session):
    _seed(
        session,
        [
            ("ATW : Détachement du dividende", "ex_dividend", "neutral", 0.0),  # correct
            ("CDM : Augmentation de capital en numéraire", "capital_action", "positive", 0.6),  # stale
        ],
    )
    report = reclassify_news(session, apply=True)
    assert report.scanned == 2
    assert report.changed == 1
    assert report.changes[0].title.startswith("CDM")


def test_null_derived_columns_are_backfilled(session):
    """Columns are nullable; a NULL impact must be treated as a difference, not skipped."""
    session.add(
        News(
            stock_id=1,
            title="ATW : Détachement du dividende",
            url="https://www.casablanca-bourse.com/fr/avis/null.pdf",
            source="Casablanca Bourse Avis",
            event_type=None,
            sentiment=None,
            impact_score=None,
        )
    )
    session.commit()

    report = reclassify_news(session, apply=True)
    assert report.changed == 1
    session.expire_all()
    row = session.scalars(select(News)).one()
    assert row.event_type == "ex_dividend"
    assert row.impact_score == 0.0


# --------------------------------------------------------------------------- #
# Batching and failure handling.                                               #
# --------------------------------------------------------------------------- #


def test_batching_commits_per_batch(session):
    _seed(session, _LEGACY_ROWS * 3)  # 6 rows, distinct urls via the seed index
    report = reclassify_news(session, apply=True, batch_size=2)
    assert report.scanned == 6
    assert report.changed == 6
    assert report.batches_committed == 3


def test_batch_walk_visits_every_row_exactly_once(session):
    _seed(session, _LEGACY_ROWS * 5)  # 10 rows
    report = reclassify_news(session, apply=False, batch_size=3)
    assert report.scanned == 10
    assert len({change.news_id for change in report.changes}) == 10


def test_failure_rolls_back_and_raises(session, monkeypatch):
    _seed(session, _LEGACY_ROWS)

    def boom(_title):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.news_backfill.classify", boom
    )
    with pytest.raises(RuntimeError, match="classifier exploded"):
        reclassify_news(session, apply=True)

    session.expire_all()
    rows = session.scalars(select(News).order_by(News.id)).all()
    assert [r.sentiment for r in rows] == ["positive", "positive"]  # untouched


def test_failure_mid_batch_leaves_the_in_flight_batch_unwritten(session, monkeypatch):
    """Row 1 classifies, row 2 explodes: the uncommitted batch must not land."""
    _seed(session, _LEGACY_ROWS)
    real = __import__(
        "moroccan_stock_intelligence.services.news_classifier", fromlist=["classify"]
    ).classify
    calls = {"n": 0}

    def flaky(title):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("boom on the second row")
        return real(title)

    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.news_backfill.classify", flaky
    )
    with pytest.raises(RuntimeError):
        reclassify_news(session, apply=True, batch_size=10)

    session.expire_all()
    rows = session.scalars(select(News).order_by(News.id)).all()
    assert rows[0].event_type == "dividend"  # the first row's edit was rolled back too
    assert rows[1].event_type == "capital_action"


def test_empty_table_is_a_no_op(session):
    report = reclassify_news(session, apply=True)
    assert report.scanned == 0
    assert report.changed == 0
    assert report.batches_committed == 0
    assert "Aucun changement" in render_report(report)


# --------------------------------------------------------------------------- #
# Report rendering.                                                            #
# --------------------------------------------------------------------------- #


def test_render_marks_dry_run_and_invites_apply(session):
    _seed(session, _LEGACY_ROWS)
    text = render_report(reclassify_news(session, apply=False))
    assert "DRY-RUN" in text
    assert "aucune écriture" in text.lower()
    assert "--apply" in text
    assert "ex_dividend" in text


def test_render_marks_a_real_run(session):
    _seed(session, _LEGACY_ROWS)
    text = render_report(reclassify_news(session, apply=True))
    assert "APPLIQUÉ" in text
    assert "--apply" not in text  # no invitation once it is done


def test_render_truncates_a_long_change_list(session):
    _seed(session, _LEGACY_ROWS * 30)  # 60 rows
    text = render_report(reclassify_news(session, apply=False), max_rows=5)
    assert "et 55 autre(s)" in text
