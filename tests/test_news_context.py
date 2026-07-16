"""Tests for the single canonical news aggregation.

This logic existed twice (`investment_analysis.build_news_contexts` and
`research/context._build_news`) and had already drifted on the window constants.
Neither copy was directly tested. These tests pin the aggregate both scoring
engines now read, so the two cannot silently disagree about the same stock again.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, News, Stock
from moroccan_stock_intelligence.services.news_context import (
    FRESH_HOURS,
    NEWS_WINDOW_DAYS,
    build_news_contexts,
    build_news_views,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.add(Stock(id=1, symbol="ATW", company_name="ATTIJARIWAFA BANK"))
        s.add(Stock(id=2, symbol="IAM", company_name="MAROC TELECOM"))
        s.commit()
        yield s
    engine.dispose()


_seq = itertools.count()


def _news(session, **kw):
    """Seed one notice. `url` is unique per call — the table enforces it."""
    defaults = {
        "stock_id": 1,
        "title": "ATW : un avis",
        "source": "Casablanca Bourse Avis",
        "collected_at": NOW - timedelta(days=1),
        "event_type": "announcement",
        "sentiment": "neutral",
        "impact_score": 0.0,
    }
    defaults.update(kw)
    defaults.setdefault("url", f"https://www.casablanca-bourse.com/fr/avis/{next(_seq)}.pdf")
    session.add(News(**defaults))
    session.commit()


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #


def test_counts_and_averages_per_symbol(session):
    _news(session, impact_score=0.4, sentiment="positive")
    _news(session, impact_score=-0.8, sentiment="negative")
    _news(session, stock_id=2, impact_score=0.2, sentiment="positive")

    contexts = build_news_contexts(session, now=NOW)

    assert contexts["ATW"].count == 2
    assert contexts["ATW"].avg_impact == pytest.approx(-0.2)
    assert contexts["ATW"].positive == 1
    assert contexts["ATW"].negative == 1
    assert contexts["IAM"].count == 1


def test_missing_impacts_do_not_become_zero(session):
    """A NULL impact must be absent from the mean, not counted as neutral —
    averaging it in would drag a real signal toward 0."""
    _news(session, impact_score=-0.8, sentiment="negative")
    _news(session, impact_score=None, sentiment=None)

    contexts = build_news_contexts(session, now=NOW)
    assert contexts["ATW"].count == 2
    assert contexts["ATW"].avg_impact == pytest.approx(-0.8)


def test_no_impacts_at_all_yields_none_not_zero(session):
    _news(session, impact_score=None, sentiment=None)
    assert build_news_contexts(session, now=NOW)["ATW"].avg_impact is None


def test_unlinked_notices_are_dropped(session):
    """A notice about an index or a regulation is not evidence about an issuer."""
    _news(session, stock_id=None, title="Réglementation", impact_score=-0.9)
    assert build_news_contexts(session, now=NOW) == {}


def test_window_excludes_old_notices(session):
    _news(session, collected_at=NOW - timedelta(days=NEWS_WINDOW_DAYS + 1), impact_score=-0.9)
    assert build_news_contexts(session, now=NOW) == {}


def test_window_includes_notices_inside_it(session):
    _news(session, collected_at=NOW - timedelta(days=NEWS_WINDOW_DAYS - 1), impact_score=-0.9)
    assert build_news_contexts(session, now=NOW)["ATW"].count == 1


# --------------------------------------------------------------------------- #
# The flags the horizon engine reads                                           #
# --------------------------------------------------------------------------- #


def test_fresh_negative_only_fires_inside_the_fresh_window(session):
    _news(
        session,
        sentiment="negative",
        impact_score=-0.8,
        collected_at=NOW - timedelta(hours=FRESH_HOURS + 1),
    )
    assert build_news_contexts(session, now=NOW)["ATW"].fresh_negative is False

    _news(
        session,
        sentiment="negative",
        impact_score=-0.8,
        collected_at=NOW - timedelta(hours=1),
        title="ATW : frais",
    )
    assert build_news_contexts(session, now=NOW)["ATW"].fresh_negative is True


def test_has_dividend_reads_the_family_not_the_raw_event_type(session):
    """The taxonomy is finer than `dividend` now. Comparing the raw value would
    silently never match, dropping the long-horizon `evenements` component from
    70 to 50 with no error anywhere."""
    _news(session, event_type="ex_dividend", sentiment="neutral", impact_score=0.0)
    context = build_news_contexts(session, now=NOW)["ATW"]
    assert context.has_dividend is True
    assert context.has_results is False


def test_has_results_fires_on_a_profit_warning(session):
    _news(session, event_type="profit_warning", sentiment="negative", impact_score=-0.85)
    assert build_news_contexts(session, now=NOW)["ATW"].has_results is True


def test_legacy_event_types_still_resolve(session):
    """Rows written before the classifier rewrite are still in the DB."""
    _news(session, event_type="dividend", sentiment="positive", impact_score=0.6)
    assert build_news_contexts(session, now=NOW)["ATW"].has_dividend is True


def test_latest_is_the_newest(session):
    _news(session, title="ATW : ancien", collected_at=NOW - timedelta(days=5))
    _news(session, title="ATW : récent", collected_at=NOW - timedelta(hours=2))
    assert build_news_contexts(session, now=NOW)["ATW"].latest_title == "ATW : récent"


# --------------------------------------------------------------------------- #
# Views + the two callers agreeing                                             #
# --------------------------------------------------------------------------- #


def test_views_and_contexts_come_from_the_same_pass(session):
    _news(session, impact_score=0.4, sentiment="positive")
    views, contexts = build_news_views(session, now=NOW)

    assert [v.title for v in views["ATW"]] == ["ATW : un avis"]
    assert contexts["ATW"].count == len(views["ATW"])


def test_build_news_contexts_matches_build_news_views(session):
    """The convenience wrapper must not diverge from the full builder — that is
    exactly how the two original copies drifted apart."""
    _news(session, impact_score=0.4, sentiment="positive")
    _news(session, stock_id=2, impact_score=-0.3, sentiment="negative")

    assert build_news_contexts(session, now=NOW) == build_news_views(session, now=NOW)[1]


def test_naive_timestamps_are_treated_as_utc(session):
    """SQLite hands back naive datetimes; comparing them to an aware cutoff
    would raise rather than filter."""
    _news(session, collected_at=datetime(2026, 7, 15, 12, 0))  # no tzinfo
    contexts = build_news_contexts(session, now=NOW)
    assert contexts["ATW"].count == 1
    assert contexts["ATW"].latest_at.tzinfo is not None


def test_both_engines_read_the_same_aggregate():
    """The research engine and the opportunity engine must share one builder.

    They used to have a copy each, already drifted on the window constants.
    """
    from moroccan_stock_intelligence.services import market_state
    from moroccan_stock_intelligence.services.research import context as research_context

    assert market_state.build_news_contexts.__module__ == (
        "moroccan_stock_intelligence.services.news_context"
    )
    assert research_context.build_news_views.__module__ == (
        "moroccan_stock_intelligence.services.news_context"
    )
