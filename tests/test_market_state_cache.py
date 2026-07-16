"""Cache-correctness tests for compute_state.

A cache that serves a stale score is worse than no cache: the wrongness is
invisible and outlives the data that caused it. So these tests are about
invalidation, and specifically about the two ways this data changes that a naive
"newest timestamp" key would miss entirely:

  * the history backfill inserts *old* séances — MAX(observed_at) does not move;
  * `reclassify-news --apply` UPDATEs impact_score in place — MAX(news.id) does
    not move.

Measured justification for the cache existing at all (80 symbols × 738 séances):
compute_state was 1 100 ms per call on 9 endpoints, ~11.5 s to open the app across
its tabs. The result is 23 KB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, News, Price, Stock
from moroccan_stock_intelligence.services import market_state


@pytest.fixture(autouse=True)
def _clear_cache():
    market_state.invalidate()
    yield
    market_state.invalidate()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.add(Stock(id=1, symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        start = datetime.now(UTC) - timedelta(days=40)
        for day in range(40):
            s.add(
                Price(
                    stock_id=1,
                    observed_at=start + timedelta(days=day),
                    current_price=100.0 + day,
                    daily_variation=1.0,
                    volume=1_000_000.0,
                    source="test",
                )
            )
        s.commit()
        yield s
    engine.dispose()


def _count_computations(monkeypatch) -> list[int]:
    """Count real computations, as opposed to cache hits."""
    calls: list[int] = []
    real = market_state._compute

    def counting(session):
        calls.append(1)
        return real(session)

    monkeypatch.setattr(market_state, "_compute", counting)
    return calls


# --------------------------------------------------------------------------- #
# It caches at all.                                                            #
# --------------------------------------------------------------------------- #


def test_unchanged_inputs_are_computed_once(session, monkeypatch):
    calls = _count_computations(monkeypatch)

    for _ in range(5):
        market_state.compute_state(session)

    assert len(calls) == 1, "five identical requests must cost one computation"


def test_a_cache_hit_returns_the_same_values(session):
    _, first = market_state.compute_state(session)
    _, second = market_state.compute_state(session)
    assert first["ATW"].buy_score == second["ATW"].buy_score
    assert first["ATW"].components == second["ATW"].components


def test_invalidate_forces_a_recomputation(session, monkeypatch):
    calls = _count_computations(monkeypatch)
    market_state.compute_state(session)
    market_state.invalidate()
    market_state.compute_state(session)
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# It invalidates when — and only when — the inputs change.                     #
# --------------------------------------------------------------------------- #


def test_a_new_price_invalidates(session, monkeypatch):
    calls = _count_computations(monkeypatch)
    market_state.compute_state(session)

    session.add(
        Price(
            stock_id=1,
            observed_at=datetime.now(UTC),
            current_price=200.0,
            daily_variation=5.0,
            volume=2_000_000.0,
            source="test",
        )
    )
    session.commit()
    _, scores = market_state.compute_state(session)

    assert len(calls) == 2
    assert scores["ATW"].components["momentum_court"] != 50.0


def test_a_history_backfill_invalidates_even_though_it_inserts_OLD_rows(session, monkeypatch):
    """The trap a MAX(observed_at) key would fall into.

    `backfill-history` seeds up to three years of séances that are all older than
    the newest one already stored, so the newest observation does not move. Keyed
    on it, the cache would serve pre-backfill scores forever — with no error and no
    way to notice.
    """
    calls = _count_computations(monkeypatch)
    metrics_before, _ = market_state.compute_state(session)

    newest_before = session.scalar(select(Price.observed_at).order_by(Price.observed_at.desc()))
    old_start = datetime.now(UTC) - timedelta(days=400)
    for day in range(300):
        session.add(
            Price(
                stock_id=1,
                observed_at=old_start + timedelta(days=day),
                current_price=50.0 + day * 0.1,
                daily_variation=0.1,
                volume=500_000.0,
                source="history",
            )
        )
    session.commit()

    newest_after = session.scalar(select(Price.observed_at).order_by(Price.observed_at.desc()))
    assert newest_after == newest_before, "precondition: the backfill added no NEWER séance"

    metrics_after, _ = market_state.compute_state(session)
    assert len(calls) == 2, "the cache must not miss three years of new history"
    # Asserted on the metrics, not the short-horizon components: old séances do not
    # move momentum 1-5d or the 90-day support, and correctly so. What they do move
    # is the long structure — which is exactly the data the backfill exists to seed.
    assert metrics_after[0].week52_low < metrics_before[0].week52_low


def test_reclassifying_news_invalidates_even_though_it_only_UPDATEs(session, monkeypatch):
    """The other trap: `reclassify-news --apply` rewrites impact_score in place.

    No row is inserted, so MAX(news.id) is unchanged. Keyed on that alone, the
    cache would keep scoring with the pre-backfill classification — which is
    exactly the bug the reclassification exists to remove.
    """
    session.add(
        News(
            stock_id=1,
            title="ATW : Détachement du dividende",
            url="https://x/1.pdf",
            source="Casablanca Bourse Avis",
            collected_at=datetime.now(UTC),
            event_type="dividend",
            sentiment="positive",
            impact_score=0.6,  # the old keyword model's verdict
        )
    )
    session.commit()

    calls = _count_computations(monkeypatch)
    _, before = market_state.compute_state(session)
    assert before["ATW"].components["actualites"] == 71.0

    row = session.scalars(select(News)).one()
    row.event_type = "ex_dividend"
    row.sentiment = "neutral"
    row.impact_score = 0.0  # what the event-driven classifier says
    session.commit()

    _, after = market_state.compute_state(session)
    assert len(calls) == 2, "an in-place reclassification must invalidate the cache"
    assert after["ATW"].components["actualites"] == 50.0


def test_new_news_invalidates(session, monkeypatch):
    calls = _count_computations(monkeypatch)
    market_state.compute_state(session)

    session.add(
        News(
            stock_id=1,
            title="ATW : Profit warning",
            url="https://x/w.pdf",
            source="Casablanca Bourse Avis",
            collected_at=datetime.now(UTC),
            event_type="profit_warning",
            sentiment="negative",
            impact_score=-0.85,
        )
    )
    session.commit()
    _, scores = market_state.compute_state(session)

    assert len(calls) == 2
    assert scores["ATW"].components["actualites"] < 50.0


def test_deleting_news_invalidates(session, monkeypatch):
    session.add(
        News(
            stock_id=1,
            title="ATW : Profit warning",
            url="https://x/w.pdf",
            source="Casablanca Bourse Avis",
            collected_at=datetime.now(UTC),
            event_type="profit_warning",
            sentiment="negative",
            impact_score=-0.85,
        )
    )
    session.commit()
    calls = _count_computations(monkeypatch)
    market_state.compute_state(session)

    session.query(News).delete()
    session.commit()
    _, scores = market_state.compute_state(session)

    assert len(calls) == 2
    assert "actualites" not in scores["ATW"].components


# --------------------------------------------------------------------------- #
# Shape of the fingerprint itself.                                            #
# --------------------------------------------------------------------------- #


def test_an_empty_database_is_fingerprintable(session):
    session.query(Price).delete()
    session.commit()
    metrics, scores = market_state.compute_state(session)
    assert metrics == []
    assert scores == {}


def test_the_fingerprint_is_cheap_relative_to_the_work_it_avoids(session):
    """It runs on every request, so it must not become the new cost."""
    import time

    market_state.compute_state(session)  # warm

    t0 = time.perf_counter()
    for _ in range(50):
        market_state.compute_state(session)
    per_call_ms = (time.perf_counter() - t0) / 50 * 1000

    assert per_call_ms < 20, f"a cache hit costs {per_call_ms:.1f} ms — too close to computing"
