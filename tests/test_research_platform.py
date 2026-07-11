"""Tests for the research platform: cache, memory, learning, knowledge, debate,
scenarios, and the LLM anti-hallucination gate.

The invariants under test are the ones the whole design rests on:
  * a report is served from the store and is reproducible;
  * the thesis hash tracks the DECISION, not the prose;
  * predictions are graded only once they mature — never guessed;
  * confidence is NOT recalibrated until enough evidence exists;
  * knowledge is de-duplicated;
  * scenario probabilities sum to 1 and shrink toward uniform when unsure;
  * a narrative containing a number the report never stated is REJECTED.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.db import get_engine, get_session_factory, init_db
from moroccan_stock_intelligence.models import Price, Stock
from moroccan_stock_intelligence.repository import (
    load_analyst_performance,
    load_thesis_changes,
    upsert_knowledge_fact,
)
from moroccan_stock_intelligence.services.research import learning
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Statement,
    thesis_hash,
)
from moroccan_stock_intelligence.services.research.debate import build_debate
from moroccan_stock_intelligence.services.research.knowledge import fact_hash, harvest_all
from moroccan_stock_intelligence.services.research.orchestrator import (
    ENGINE_VERSION,
    analyze_report,
    generate_report,
)
from moroccan_stock_intelligence.services.research.scenarios import build_all_scenarios
from moroccan_stock_intelligence.services.research.store import persist_report
from moroccan_stock_intelligence.services.synthesis.base import validate_narrative
from moroccan_stock_intelligence.services.synthesis.template import TemplateSynthesizer


@pytest.fixture
def session(tmp_path):
    engine = get_engine(f"sqlite:///{(tmp_path / 'platform.db').as_posix()}")
    init_db(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        _seed_prices(s)
        yield s
    engine.dispose()


def _seed_prices(session) -> None:
    """One stock with enough daily history for the analysts to have a view."""
    stock = Stock(symbol="TST", company_name="Test SA", sector="Banques")
    session.add(stock)
    session.flush()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    for day in range(120):
        price *= 1.002 if day % 3 else 0.999
        session.add(
            Price(
                stock_id=stock.id,
                observed_at=base + timedelta(days=day),
                current_price=round(price, 2),
                daily_variation=0.2,
                volume=10_000,
                source="test",
            )
        )
    session.commit()


# --------------------------------------------------------------------------- #
# Phase 2 — research DB, cache, reproducibility                                #
# --------------------------------------------------------------------------- #

def test_report_is_persisted_and_served_from_cache(session):
    first = analyze_report(session, "TST", "short")
    assert first is not None
    assert first["cached"] is False

    second = analyze_report(session, "TST", "short")
    assert second["cached"] is True
    # Served verbatim from the store: same thesis, same engine — reproducible.
    assert second["thesis_hash"] == first["thesis_hash"]
    assert second["engine_version"] == ENGINE_VERSION


def test_fresh_bypasses_the_cache(session):
    analyze_report(session, "TST", "short")
    forced = analyze_report(session, "TST", "short", fresh=True)
    assert forced["cached"] is False


def test_cache_is_invalidated_by_engine_version(session):
    analyze_report(session, "TST", "short")
    # A report produced by different logic must never be served as if current.
    from moroccan_stock_intelligence.repository import load_cached_report

    assert load_cached_report(session, "TST", "short", "different-version", 99999) is None


def test_thesis_hash_tracks_the_decision_not_the_prose(session):
    report = generate_report(session, "TST", "short", persist=False)
    from dataclasses import replace

    reworded = replace(report, cio=replace(report.cio, executive_summary="Texte différent."))
    assert thesis_hash(reworded) == thesis_hash(report)

    verdicts = dict(report.cio.verdicts)
    verdicts["short"] = replace(verdicts["short"], recommendation="AVOID")
    flipped = replace(report, cio=replace(report.cio, verdicts=verdicts))
    assert thesis_hash(flipped) != thesis_hash(report)


# --------------------------------------------------------------------------- #
# Phase 5 — investment memory                                                  #
# --------------------------------------------------------------------------- #

def test_thesis_change_is_recorded_with_a_reason(session):
    from dataclasses import replace

    report = generate_report(session, "TST", "short")  # first report: no change
    assert not load_thesis_changes(session, "TST")

    verdicts = dict(report.cio.verdicts)
    for horizon in verdicts:
        verdicts[horizon] = replace(verdicts[horizon], recommendation="AVOID",
                                    recommendation_label="Éviter")
    flipped = replace(report, cio=replace(report.cio, verdicts=verdicts))
    persist_report(session, flipped)

    changes = load_thesis_changes(session, "TST")
    assert changes, "a flipped recommendation must be recorded"
    change = changes[0]
    assert change.to_recommendation == "AVOID"
    assert change.reason  # WHY it changed, not just that it did
    assert json.loads(change.new_evidence_json) is not None


def test_unchanged_thesis_records_nothing(session):
    generate_report(session, "TST", "short")
    generate_report(session, "TST", "short")  # identical thesis
    assert not load_thesis_changes(session, "TST"), "an unchanged thesis is not a change"


# --------------------------------------------------------------------------- #
# Phase 3 — learning engine                                                    #
# --------------------------------------------------------------------------- #

def test_predictions_are_recorded_for_cio_and_analysts(session):
    from moroccan_stock_intelligence.models import PredictionHistory
    from sqlalchemy import select

    generate_report(session, "TST", "short")
    rows = session.scalars(select(PredictionHistory)).all()
    assert rows
    analysts = {row.analyst for row in rows}
    assert "cio" in analysts, "the CIO's call must be falsifiable"
    assert analysts - {"cio"}, "individual analysts must be falsifiable too"
    # Nothing is graded before it matures.
    assert all(row.evaluated_at is None for row in rows)
    assert all(row.correct is None for row in rows)


def test_immature_predictions_are_not_graded(session):
    generate_report(session, "TST", "short")
    assert learning.evaluate_due_predictions(session) == 0, "nothing has matured yet"


def test_confidence_is_not_recalibrated_below_the_sample_threshold(session):
    from moroccan_stock_intelligence.models import PredictionHistory
    from sqlalchemy import select

    generate_report(session, "TST", "short")
    # Force-mature every prediction and grade it.
    for row in session.scalars(select(PredictionHistory)).all():
        row.evaluate_at = datetime.now(UTC) - timedelta(days=1)
    session.commit()
    learning.run_learning_cycle(session)

    performance = load_analyst_performance(session)
    for (_analyst, _horizon), stats in performance.items():
        if stats["sample_size"] < settings.min_calibration_samples:
            assert stats["confidence_multiplier"] == 1.0, (
                "a handful of outcomes must NOT move an analyst's weighting"
            )


def test_brier_score_rewards_a_correct_confident_call():
    from moroccan_stock_intelligence.models import PredictionHistory

    def row(direction, realized, probability):
        r = PredictionHistory(
            report_id=1, stock_id=1, symbol="TST", analyst="technical", horizon="short",
            scenario="direction", generated_at=datetime.now(UTC),
            evaluate_at=datetime.now(UTC), engine_version="2.0",
            predicted_direction=direction, predicted_probability=probability,
        )
        r.realized_direction = realized
        r.correct = int(direction == realized)
        r.brier_component = (probability - (1.0 if direction == realized else 0.0)) ** 2
        return r

    confident_right = learning._stats([row("up", "up", 0.9) for _ in range(5)])
    confident_wrong = learning._stats([row("up", "down", 0.9) for _ in range(5)])
    assert confident_right["brier_score"] < confident_wrong["brier_score"]
    assert confident_right["hit_rate"] == 1.0
    assert confident_wrong["hit_rate"] == 0.0


# --------------------------------------------------------------------------- #
# Phase 4 — knowledge base                                                     #
# --------------------------------------------------------------------------- #

def test_knowledge_is_deduplicated(session):
    stock = session.query(Stock).first()
    digest = fact_hash("identity", "Objet social", "Banque")
    _, created_first = upsert_knowledge_fact(
        session, stock.id, "identity", "Objet social", "Banque", digest
    )
    _, created_again = upsert_knowledge_fact(
        session, stock.id, "identity", "Objet social", "Banque", digest
    )
    session.commit()
    assert created_first is True
    assert created_again is False, "the same fact must not be stored twice"


def test_harvest_is_idempotent(session):
    first = harvest_all(session)
    second = harvest_all(session)
    assert second == 0, f"re-harvesting learned {second} 'new' facts (first run: {first})"


# --------------------------------------------------------------------------- #
# Phase 6 — debate                                                             #
# --------------------------------------------------------------------------- #

def _analyst(name, lean, confidence, bullish):
    statement = Statement(text=f"{name} claim", polarity="bullish" if bullish else "bearish",
                          weight=0.8)
    return AnalystReport(
        analyst=name, version="1.0", confidence=confidence,
        strengths=[statement] if bullish else [],
        weaknesses=[] if bullish else [statement],
        horizon_signals=[HorizonSignal("short", lean)],
    )


def test_debate_only_fires_on_a_real_clash():
    agree = {"technical": _analyst("technical", 70, 80, True),
             "news": _analyst("news", 68, 80, True)}
    assert build_debate(agree) == [], "agreement is not a debate"

    clash = {"technical": _analyst("technical", 75, 90, True),
             "news": _analyst("news", 25, 40, False)}
    exchanges = build_debate(clash)
    assert len(exchanges) == 1
    assert exchanges[0].winner == "technical"  # higher conviction AND confidence
    assert exchanges[0].resolution


def test_learned_reliability_can_flip_the_debate():
    clash = {"technical": _analyst("technical", 75, 70, True),
             "news": _analyst("news", 25, 70, False)}
    # With no track record the bull wins on equal footing…
    baseline = build_debate(clash)[0]
    # …but an analyst proven unreliable loses influence.
    weighted = build_debate(clash, {"technical": 0.6, "news": 1.4})[0]
    assert baseline.winner != weighted.winner


# --------------------------------------------------------------------------- #
# Phase 7 — scenarios                                                          #
# --------------------------------------------------------------------------- #

def test_scenario_probabilities_sum_to_one(session):
    report = generate_report(session, "TST", "medium", persist=False)
    for horizon, scenarios in report.scenarios_by_horizon.items():
        total = scenarios.best.probability + scenarios.base.probability + scenarios.worst.probability
        assert abs(total - 1.0) <= 0.02, f"{horizon} probabilities sum to {total}"
        assert scenarios.best.assumptions and scenarios.worst.assumptions


def test_low_confidence_shrinks_toward_uniform(session):
    from moroccan_stock_intelligence.services.research.context import build_context, build_market_context, gather
    from moroccan_stock_intelligence.services.analysts import risk_manager

    gathered = gather(session)
    market = build_market_context(gathered)
    ctx = build_context(session, "TST", gathered, market)
    risk = risk_manager.assess(ctx, {})

    confident = build_all_scenarios(ctx, {"short": 85.0}, {"short": 95.0}, risk)["short"]
    unsure = build_all_scenarios(ctx, {"short": 85.0}, {"short": 0.0}, risk)["short"]
    # With no confidence, the honest answer is "I don't know" -> near 1/3 each.
    assert abs(unsure.best.probability - 0.33) < 0.05
    assert confident.best.probability > unsure.best.probability


# --------------------------------------------------------------------------- #
# Phase 10 — the anti-hallucination gate                                       #
# --------------------------------------------------------------------------- #

def test_template_synthesizer_needs_no_llm(session):
    report = generate_report(session, "TST", "short", persist=False)
    narrative = TemplateSynthesizer().render(report)
    assert "## Résumé" in narrative
    assert report.disclaimer in narrative
    valid, problems = validate_narrative(narrative, report)
    assert valid, f"our own renderer must pass its own validator: {problems}"


def test_validator_rejects_an_invented_number(session):
    report = generate_report(session, "TST", "short", persist=False)
    fabricated = (
        "Le chiffre d'affaires atteint 47231.55 MAD, en hausse de 8213.77 face à "
        "un bénéfice de 9182.44 et une dette de 6621.99."
    )
    valid, problems = validate_narrative(fabricated, report)
    assert not valid, "numbers absent from the report must be rejected"
    assert problems


def test_validator_rejects_empty_narrative(session):
    report = generate_report(session, "TST", "short", persist=False)
    assert validate_narrative("", report)[0] is False
