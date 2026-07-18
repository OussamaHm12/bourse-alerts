"""What the platform records as a prediction must be what it actually claimed.

Two defects (AUDIT_2026-07-18.md §9), both of which would have corrupted the
learning engine's output *before* anyone could notice, because nothing matures for
weeks:

  * WATCH and HOLD were stored as bullish "up" bets. WATCH means "no direction
    dominates" and covers the whole 45-70 band — most verdicts — so the engine
    mass-produced calls nobody made. A hit rate over those measures how often the
    Casablanca market rose, not whether this platform is right.
  * `predicted_probability` came from `confidence`, which measures DATA COVERAGE.
    A stock with three complete years of history scored 0.82 regardless of how
    weak its setup was.

These tests pin the corrected semantics, and — as importantly — pin that the old
rows are neither rewritten nor mixed into the new statistics.
"""

from __future__ import annotations

import pytest

from moroccan_stock_intelligence.models import CURRENT_SEMANTICS_VERSION
from moroccan_stock_intelligence.services.research.store import (
    CLAIM_ACTION,
    CLAIM_DIRECTION,
    CLAIM_STABILITY,
    MAX_EDGE,
    _claim_for,
    _probability,
    _signal_strength,
)


# --------------------------------------------------------------------------- #
# The semantic table                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("recommendation", "direction", "kind"),
    [
        ("STRONG_OPPORTUNITY", "up", CLAIM_DIRECTION),
        ("WATCH", "flat", CLAIM_DIRECTION),
        ("HOLD", "flat", CLAIM_DIRECTION),
        ("AVOID", "down", CLAIM_DIRECTION),
        ("RISKY", None, CLAIM_STABILITY),
        ("TAKE_PROFIT", None, CLAIM_ACTION),
    ],
)
def test_each_verdict_claims_what_it_means(recommendation, direction, kind):
    claim = _claim_for(recommendation)
    assert claim.direction == direction
    assert claim.kind == kind


def test_watch_is_no_longer_a_bullish_bet():
    """The headline defect. WATCH says "wait for confirmation", not "it will rise"."""
    assert _claim_for("WATCH").direction != "up"


def test_hold_is_not_a_bullish_bet_either():
    assert _claim_for("HOLD").direction != "up"


def test_flat_is_still_a_falsifiable_claim():
    """WATCH is scored, not excluded: the evaluator's flat band makes "it will not
    move much" a real prediction that reality can contradict."""
    assert _claim_for("WATCH").kind == CLAIM_DIRECTION
    assert _claim_for("WATCH").direction == "flat"


def test_risk_and_action_verdicts_are_not_scored_as_directions():
    """RISKY asserts volatility; TAKE_PROFIT is an instruction about a position
    already in profit. Neither is falsified by a price moving the "wrong" way, and
    scoring them as "down" would have been the easy, wrong choice."""
    for recommendation in ("RISKY", "TAKE_PROFIT"):
        claim = _claim_for(recommendation)
        assert claim.direction is None
        assert claim.kind != CLAIM_DIRECTION


def test_an_unknown_verdict_defaults_to_a_non_committal_claim():
    assert _claim_for("SOMETHING_NEW").direction == "flat"


# --------------------------------------------------------------------------- #
# Probability is no longer a coverage metric in disguise                       #
# --------------------------------------------------------------------------- #


def test_signal_strength_measures_distance_from_no_opinion():
    assert _signal_strength(50.0) == 0.0
    assert _signal_strength(100.0) == 1.0
    assert _signal_strength(0.0) == 1.0
    assert _signal_strength(75.0) == pytest.approx(0.5)
    assert _signal_strength(None) == 0.0


def test_a_neutral_score_is_a_coin_flip_however_complete_the_data():
    """This is the fix. Perfect data about a stock with no setup is still no edge."""
    assert _probability(_signal_strength(50.0), 100.0) == pytest.approx(0.5)


def test_thin_data_pulls_the_probability_toward_a_coin_flip():
    strong = _signal_strength(90.0)
    assert _probability(strong, 100.0) > _probability(strong, 30.0)
    assert _probability(strong, 0.0) == pytest.approx(0.5)


def test_a_stronger_signal_claims_more_all_else_equal():
    assert _probability(_signal_strength(90.0), 80.0) > _probability(
        _signal_strength(60.0), 80.0
    )


def test_the_claimed_edge_is_bounded():
    """A single-market technical model with no validated edge must not assert more."""
    assert _probability(1.0, 100.0) == pytest.approx(0.5 + MAX_EDGE)
    assert MAX_EDGE <= 0.3


@pytest.mark.parametrize("confidence", [None, 0.0, 50.0, 100.0, 999.0, -20.0])
def test_the_probability_stays_a_probability(confidence):
    for strength in (0.0, 0.5, 1.0):
        assert 0.1 <= _probability(strength, confidence) <= 0.9


def test_the_old_derivation_would_have_disagreed():
    """Guards the regression: v1 read confidence directly, so a complete-data stock
    with a neutral setup claimed a large edge."""
    v1_probability = 0.5 + (95.0 - 50) / 125  # the old formula, confidence 95
    v2_probability = _probability(_signal_strength(50.0), 95.0)
    assert v1_probability > 0.8
    assert v2_probability == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Versioning — old rows are kept, not rewritten, and not mixed in              #
# --------------------------------------------------------------------------- #


def test_new_rows_carry_the_current_semantics_version():
    from moroccan_stock_intelligence.services.research import store

    assert store.SEMANTICS_VERSION == CURRENT_SEMANTICS_VERSION == 2


def test_only_current_semantics_and_directional_claims_are_scored(tmp_path):
    """v1 rows stay queryable and simply do not contribute to v2 statistics.

    Rewriting them would invent predictions that were never made; mixing them in
    would produce a statistic that means neither semantics.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from moroccan_stock_intelligence.models import Base, PredictionHistory, Stock
    from moroccan_stock_intelligence.repository import load_evaluated_predictions

    engine = create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    with sessionmaker(bind=engine, future=True)() as session:
        session.add(Stock(id=1, symbol="TST", company_name="Test"))

        def prediction(**kwargs):
            base = dict(
                report_id=1,
                stock_id=1,
                symbol="TST",
                analyst="cio",
                horizon="short",
                generated_at=now - timedelta(days=20),
                evaluate_at=now - timedelta(days=10),
                engine_version="2.0",
                predicted_direction="up",
                predicted_probability=0.6,
                evaluated_at=now,
                correct=1,
                brier_component=0.16,
                realized_direction="up",
            )
            base.update(kwargs)
            return PredictionHistory(**base)

        session.add(prediction(scenario="v1", semantics_version=1, claim_kind="direction"))
        session.add(prediction(scenario="v2", semantics_version=2, claim_kind="direction"))
        session.add(prediction(scenario="risk", semantics_version=2, claim_kind="stability",
                               predicted_direction=None))
        session.commit()

        scored = load_evaluated_predictions(session)
        scenarios = {row.scenario for row in scored}

    assert scenarios == {"v2"}, "only current-semantics directional claims are scored"
    engine.dispose()
