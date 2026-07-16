"""Market state tests — the news wiring, and the layering that made it possible.

`score_opportunity` reserved 10% of buy_score for news, but no production caller
ever passed the argument, so the component sat pinned at 50 and a tenth of the
user-facing score was inert (AUDIT_TECHNIQUE.md §5). Only the unit test of the
scoring function itself passed it — which is precisely why nobody noticed. These
tests exercise the wiring through the real entry point, so the gap cannot reopen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, News, Price, Stock
from moroccan_stock_intelligence.services.market_state import compute_state


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.add(Stock(id=1, symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        # Enough séances for the metrics to be computable at all.
        start = datetime.now(UTC) - timedelta(days=40)
        for day in range(40):
            s.add(
                Price(
                    stock_id=1,
                    observed_at=start + timedelta(days=day),
                    current_price=100.0 + day * 0.2,
                    daily_variation=0.2,
                    volume=1_000_000.0,
                    source="test",
                )
            )
        s.commit()
        yield s
    engine.dispose()


def _add_news(session, *, sentiment: str, impact: float, event_type: str, url: str) -> None:
    session.add(
        News(
            stock_id=1,
            title=f"ATW : {event_type}",
            url=url,
            source="Casablanca Bourse Avis",
            collected_at=datetime.now(UTC),
            event_type=event_type,
            sentiment=sentiment,
            impact_score=impact,
        )
    )
    session.commit()


# --------------------------------------------------------------------------- #
# The wiring itself.                                                           #
# --------------------------------------------------------------------------- #


def test_news_reaches_the_opportunity_score(session):
    """The regression that mattered: news used to be a constant 50, never read.

    Note what "no news" now looks like: the component is ABSENT and declared in
    `missing`, not silently set to a neutral 50. The old engine could not tell
    silence from a neutral notice; this one refuses to pretend.
    """
    _, before = compute_state(session)
    assert "actualites" not in before["ATW"].components
    assert any("actualité" in m.lower() for m in before["ATW"].missing)

    _add_news(session, sentiment="negative", impact=-0.85, event_type="profit_warning", url="u1")
    _, after = compute_state(session)

    assert after["ATW"].components["actualites"] < 50.0, (
        "a profit warning must move the news component — this was the dead 10% weight"
    )
    assert after["ATW"].buy_score < before["ATW"].buy_score


def test_positive_news_lifts_the_score(session):
    _, before = compute_state(session)
    _add_news(session, sentiment="positive", impact=0.5, event_type="share_buyback", url="u1")
    _, after = compute_state(session)

    assert after["ATW"].components["actualites"] > 50.0
    assert after["ATW"].buy_score > before["ATW"].buy_score


def test_strongly_negative_news_raises_the_avoid_score(session):
    """The avoid_score malus (news < -0.5) was dead too, not just the buy weight."""
    _, before = compute_state(session)
    _add_news(session, sentiment="negative", impact=-0.85, event_type="profit_warning", url="u1")
    _, after = compute_state(session)

    assert after["ATW"].avoid_score > before["ATW"].avoid_score


def test_mechanical_news_contributes_no_direction(session):
    """An ex-dividend detachment is arithmetic, not information: impact 0.0.

    End-to-end proof that the classifier fix and the wiring agree — the old keyword
    model scored this +0.6 and would have LIFTED the score here.

    The score still moves a hair, and that is correct rather than a leak: the
    component goes from absent to present-at-neutral, so coverage rises and the
    kernel shrinks the score toward neutral a little less. Having a procedural
    notice really is more information than having none.
    """
    _, before = compute_state(session)
    _add_news(session, sentiment="neutral", impact=0.0, event_type="ex_dividend", url="u1")
    _, after = compute_state(session)

    assert after["ATW"].components["actualites"] == 50.0, "no direction"
    assert after["ATW"].coverage > before["ATW"].coverage, "but more coverage"
    assert abs(after["ATW"].buy_score - before["ATW"].buy_score) < 1.0


def test_the_average_is_what_reaches_the_score(session):
    """Several notices in the window are averaged, not summed — one bad headline
    must not dominate a month of filings."""
    _add_news(session, sentiment="negative", impact=-0.8, event_type="profit_warning", url="u1")
    _add_news(session, sentiment="positive", impact=0.4, event_type="share_buyback", url="u2")
    _, scores = compute_state(session)

    # mean(-0.8, 0.4) = -0.2 -> clamp(50 + (-0.2 * 35)) = 43
    assert scores["ATW"].components["actualites"] == 43.0


def test_a_symbol_with_no_news_is_declared_missing_not_penalised(session):
    """Absent news must not read as bad news — nor be invented as neutral."""
    session.add(Stock(id=2, symbol="IAM", company_name="MAROC TELECOM", sector="Télécoms"))
    start = datetime.now(UTC) - timedelta(days=40)
    for day in range(40):
        session.add(
            Price(
                stock_id=2,
                observed_at=start + timedelta(days=day),
                current_price=90.0,
                daily_variation=0.0,
                volume=500_000.0,
                source="test",
            )
        )
    session.commit()
    _add_news(session, sentiment="negative", impact=-0.85, event_type="profit_warning", url="u1")

    _, scores = compute_state(session)
    assert "actualites" not in scores["IAM"].components
    assert scores["ATW"].components["actualites"] < 50.0


def test_unlinked_news_reaches_nobody(session):
    """A market-level notice (no stock_id) is not evidence about any issuer."""
    session.add(
        News(
            stock_id=None,
            title="Réglementation du marché à terme",
            url="u-market",
            source="Casablanca Bourse Avis",
            collected_at=datetime.now(UTC),
            event_type="market_notice",
            sentiment="negative",
            impact_score=-0.9,
        )
    )
    session.commit()

    _, scores = compute_state(session)
    assert "actualites" not in scores["ATW"].components


def test_news_outside_the_window_is_ignored(session):
    _add_news(session, sentiment="negative", impact=-0.85, event_type="profit_warning", url="u1")
    stale = session.query(News).one()
    stale.collected_at = datetime.now(UTC) - timedelta(days=45)
    session.commit()

    _, scores = compute_state(session)
    assert "actualites" not in scores["ATW"].components


# --------------------------------------------------------------------------- #
# Shape and layering.                                                          #
# --------------------------------------------------------------------------- #


def test_compute_state_returns_metrics_and_scores_per_symbol(session):
    metrics, scores = compute_state(session)
    assert [m.symbol for m in metrics] == ["ATW"]
    assert set(scores) == {"ATW"}
    assert 0 <= scores["ATW"].buy_score <= 100


def test_empty_market_is_not_an_error(session):
    session.query(Price).delete()
    session.commit()
    metrics, scores = compute_state(session)
    assert metrics == []
    assert scores == {}


def test_calculation_layers_do_not_import_the_view_layer():
    """compute_state lived in views.py, so calculation modules imported a view
    module to obtain market state. scoring.py's own comment argued against that
    ("none of them should import a view layer"); now the code agrees."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "moroccan_stock_intelligence"
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "views.py"
        and path.name != "api.py"  # the API is a presentation layer: views is its business
        and "services.views" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"{offenders} import the view layer to obtain market state"
