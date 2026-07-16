"""Thesis-notification tests — 75 statements at 0% coverage (AUDIT_TECHNIQUE.md §12).

This module decides when the owner's phone buzzes. Untested, a bug here is either
spam (and the owner mutes the app, losing every future alert) or silence (and the
platform's whole point evaporates). Both fail quietly.

The rule under test is thesis-based, not event-based: notify when the *conclusion*
changes, not when a price moves. So these tests are mostly about what must stay
SILENT.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import AnalysisReport, Base, Favorite, Stock
from moroccan_stock_intelligence.services.research import notifications as notif
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    CIOReport,
    HorizonVerdict,
    InvestmentReport,
    RiskReport,
    Scenario,
    Statement,
)


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


def _verdict(horizon="short", rec="WATCH", confidence=60.0):
    return HorizonVerdict(
        horizon=horizon,
        recommendation=rec,
        recommendation_label="À surveiller",
        score=55.0,
        confidence=confidence,
        rationale="—",
    )


def _scenario(name="Scénario central"):
    return Scenario(name=name, probability=0.5, confidence=50.0, rationale="—")


def _risk(risk=30.0):
    return RiskReport(
        overall_risk=risk,
        confidence=50.0,
        dimensions={"technique": risk},
        worst_case=_scenario("Scénario défavorable"),
        base_case=_scenario(),
        best_case=_scenario("Scénario favorable"),
    )


def _report(symbol="ATW", rec="WATCH", confidence=60.0, risk=30.0, analysts=None):
    return InvestmentReport(
        symbol=symbol,
        company_name="ATTIJARIWAFA BANK",
        sector="Banques",
        as_of=datetime.now(UTC),
        horizon_focus="short",
        cio=CIOReport(
            symbol=symbol,
            verdicts={"short": _verdict("short", rec, confidence)},
            executive_summary="—",
            final_verdict="—",
        ),
        risk=_risk(risk),
        analysts=analysts or {},
        scenarios=[],
        narrative=None,
        engine_version="2.0",
        disclaimer="—",
    )


def _store_previous(session, *, symbol="ATW", rec="WATCH", confidence=60.0, risk=30.0) -> int:
    """The report the new one is compared against."""
    row = AnalysisReport(
        stock_id=1 if symbol == "ATW" else 2,
        symbol=symbol,
        generated_at=datetime.now(UTC) - timedelta(days=1),
        horizon_focus="short",
        engine_version="2.0",
        thesis_hash="old",
        recommendation_short=rec,
        confidence_short=confidence,
        risk_score=risk,
        report_json="{}",
    )
    session.add(row)
    session.commit()
    return row.id


def _current_id(session, symbol="ATW") -> int:
    """A row id strictly after the previous one, standing in for the report just stored."""
    row = AnalysisReport(
        stock_id=1 if symbol == "ATW" else 2,
        symbol=symbol,
        generated_at=datetime.now(UTC),
        horizon_focus="short",
        engine_version="2.0",
        thesis_hash="new",
        report_json="{}",
    )
    session.add(row)
    session.commit()
    return row.id


# --------------------------------------------------------------------------- #
# Silence is the default.                                                      #
# --------------------------------------------------------------------------- #


def test_the_first_ever_report_says_nothing(session):
    """Nothing changed, because there is nothing to change from."""
    events = notif.evaluate_report(session, _report(), _current_id(session))
    assert events == []


def test_an_unchanged_thesis_says_nothing(session):
    _store_previous(session, rec="WATCH", confidence=60.0, risk=30.0)
    events = notif.evaluate_report(
        session, _report(rec="WATCH", confidence=60.0, risk=30.0), _current_id(session)
    )
    assert events == []


def test_a_small_confidence_wobble_is_noise_not_news(session):
    """Below CONFIDENCE_DROP the owner must not be told: it is not information."""
    _store_previous(session, confidence=60.0)
    events = notif.evaluate_report(
        session, _report(confidence=60.0 - notif.CONFIDENCE_DROP + 1), _current_id(session)
    )
    assert events == []


def test_a_small_risk_rise_is_noise_not_news(session):
    _store_previous(session, risk=30.0)
    events = notif.evaluate_report(
        session, _report(risk=30.0 + notif.RISK_RISE - 1), _current_id(session)
    )
    assert events == []


# --------------------------------------------------------------------------- #
# The four genuine triggers.                                                   #
# --------------------------------------------------------------------------- #


def test_a_flipped_recommendation_notifies(session):
    _store_previous(session, rec="WATCH")
    events = notif.evaluate_report(session, _report(rec="AVOID"), _current_id(session))

    assert len(events) == 1
    suffix, title, body = events[0]
    assert suffix.startswith("thesis-short-")
    assert "Thèse modifiée" in title
    assert "ATW" in title


def test_a_material_confidence_drop_notifies(session):
    _store_previous(session, confidence=70.0)
    events = notif.evaluate_report(
        session, _report(confidence=70.0 - notif.CONFIDENCE_DROP), _current_id(session)
    )

    assert len(events) == 1
    assert events[0][0].startswith("confidence-short-")
    assert "Confiance en baisse" in events[0][1]


def test_a_material_risk_rise_notifies(session):
    _store_previous(session, risk=20.0)
    events = notif.evaluate_report(
        session, _report(risk=20.0 + notif.RISK_RISE), _current_id(session)
    )

    assert len(events) == 1
    assert events[0][0].startswith("risk-")
    assert "Risque en hausse" in events[0][1]


def test_a_flip_suppresses_the_confidence_message(session):
    """A flip already says everything; also sending "confidence dropped" is noise."""
    _store_previous(session, rec="WATCH", confidence=80.0)
    events = notif.evaluate_report(
        session, _report(rec="AVOID", confidence=40.0), _current_id(session)
    )

    suffixes = [suffix for suffix, _, _ in events]
    assert any(s.startswith("thesis-") for s in suffixes)
    assert not any(s.startswith("confidence-") for s in suffixes)


def test_fresh_adverse_news_on_a_held_position_notifies(session):
    held = AnalystReport(
        analyst="portfolio",
        version="1.0",
        observations=[Statement(text="ATW pèse 40% du portefeuille", kind="fact")],
    )
    news = AnalystReport(
        analyst="news",
        version="1.0",
        risk_flags=[
            Statement(text="Actualité négative fraîche (< 24 h) : thèse à revérifier.", kind="fact")
        ],
    )
    _store_previous(session)
    events = notif.evaluate_report(
        session,
        _report(analysts={"portfolio": held, "news": news}),
        _current_id(session),
    )

    assert [e[0] for e in events if e[0].startswith("news-")]
    assert "Actualité contraire" in [e[1] for e in events if e[0].startswith("news-")][0]


def test_fresh_adverse_news_on_a_stock_we_do_not_hold_stays_silent(session):
    """Only a held position is "under attack" — otherwise it is just news."""
    news = AnalystReport(
        analyst="news",
        version="1.0",
        risk_flags=[Statement(text="Actualité négative fraîche (< 24 h).", kind="fact")],
    )
    _store_previous(session)
    events = notif.evaluate_report(session, _report(analysts={"news": news}), _current_id(session))
    assert [e for e in events if e[0].startswith("news-")] == []


# --------------------------------------------------------------------------- #
# The cap, and who gets the slots.                                             #
# --------------------------------------------------------------------------- #


def test_favorites_are_ordered_before_everything_else(session):
    """MAX_PUSHES_PER_RUN caps the run, and `generated` arrives alphabetically.

    Without this ordering the 3 slots go to whichever symbols sort first, so a
    thesis change on a watched stock is crowded out by one on a stock the owner
    has never looked at.
    """
    session.add(Favorite(stock_id=2, symbol="IAM"))
    session.commit()

    generated = [(_report(symbol="ATW"), 1), (_report(symbol="IAM"), 2)]
    ordered = notif._by_attention(session, generated)

    assert [report.symbol for report, _ in ordered] == ["IAM", "ATW"]


def test_ordering_is_stable_within_each_group(session):
    generated = [(_report(symbol="ATW"), 1), (_report(symbol="IAM"), 2)]
    assert [r.symbol for r, _ in notif._by_attention(session, generated)] == ["ATW", "IAM"]


def test_dispatch_is_capped_per_run(session, monkeypatch):
    """A report cycle that flipped many theses must not fire one push per flip."""
    pushes: list[str] = []
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.push.send_push_to_all",
        lambda s, t, b, u: pushes.append(t),
    )
    monkeypatch.setattr(notif, "save_notification", lambda *a, **k: None)

    # Each event needs its own key, or create_alert_once dedups them and the cap
    # is never what stops the run.
    counter = itertools.count()
    monkeypatch.setattr(
        notif,
        "evaluate_report",
        lambda s, report, rid: [(f"x-{next(counter)}", f"T {report.symbol}", "body")],
    )

    generated = [(_report(symbol="ATW"), 1), (_report(symbol="IAM"), 2)] * 5
    sent = notif.dispatch_thesis_notifications(session, generated)

    assert sent == notif.MAX_PUSHES_PER_RUN
    assert len(pushes) == notif.MAX_PUSHES_PER_RUN


def test_the_same_change_is_not_repeated_the_same_day(session, monkeypatch):
    """Deduplicated through the alerts table, so a re-run does not re-notify."""
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.push.send_push_to_all", lambda s, t, b, u: 1
    )
    monkeypatch.setattr(notif, "save_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        notif, "evaluate_report", lambda s, report, rid: [("thesis-short-day", "T", "body")]
    )

    first = notif.dispatch_thesis_notifications(session, [(_report(), 1)])
    second = notif.dispatch_thesis_notifications(session, [(_report(), 1)])

    assert first == 1
    assert second == 0, "the same thesis change must not be re-sent the same day"


def test_one_failing_symbol_does_not_sink_the_run(session, monkeypatch):
    """A report cycle notifies for many symbols; one bad row must not silence the rest."""
    pushes: list[str] = []
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.push.send_push_to_all",
        lambda s, t, b, u: pushes.append(t),
    )
    monkeypatch.setattr(notif, "save_notification", lambda *a, **k: None)

    def flaky(session_, report, report_id):
        if report.symbol == "ATW":
            raise RuntimeError("boom")
        return [("x", f"T {report.symbol}", "body")]

    monkeypatch.setattr(notif, "evaluate_report", flaky)

    sent = notif.dispatch_thesis_notifications(
        session, [(_report(symbol="ATW"), 1), (_report(symbol="IAM"), 2)]
    )

    assert sent == 1
    assert pushes == ["T IAM"]


def test_a_notification_is_persisted_for_the_in_app_inbox(session, monkeypatch):
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.push.send_push_to_all", lambda s, t, b, u: 1
    )
    monkeypatch.setattr(
        notif, "evaluate_report", lambda s, report, rid: [("k", "Titre", "Corps")]
    )

    notif.dispatch_thesis_notifications(session, [(_report(), 1)])

    from moroccan_stock_intelligence.models import Notification

    rows = session.scalars(select(Notification)).all()
    assert [(r.kind, r.title) for r in rows] == [("analysis", "Titre")]
