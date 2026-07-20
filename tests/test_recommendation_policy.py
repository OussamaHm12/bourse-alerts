"""The one recommendation rule — asserted at every boundary.

The rule used to exist three times (`cio._recommend`, `investment_analysis._recommend`,
`scoring.classify_label`) with the constants copied by hand. They agreed by
coincidence; the audit (AUDIT_2026-07-18.md §6) flagged that a single edit would
have made the Opportunités tab and the research report disagree about the same
stock, silently.

A boundary matrix is the right shape of test for a threshold ladder: off-by-one at
a comparison operator is the failure mode, and `>=` vs `>` is invisible to review.
"""

from __future__ import annotations

import pytest

from moroccan_stock_intelligence.services.recommendation_policy import (
    HOLDER,
    MARKET,
    NO_POSITION,
    THRESHOLDS,
    PositionState,
    decide,
)

# Comfortably below every risk gate, so score/confidence decide alone.
CALM = 10.0


def market(score: float, *, confidence: float = 80.0, risk: float = CALM, avoid=None) -> str:
    return decide(
        score=score, risk=risk, confidence=confidence, avoid_score=avoid, position=NO_POSITION
    ).recommendation


def holder(*, risk: float = CALM, advice: str = "HOLD", pl: float | None = 5.0) -> str:
    return decide(
        score=60.0,
        risk=risk,
        confidence=80.0,
        position=PositionState(held=True, advice=advice, net_pl_pct=pl, take_profit_pct=15.0),
    ).recommendation


# --------------------------------------------------------------------------- #
# Score ladder — every boundary, from both sides                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (44.0, "AVOID"),
        (44.9, "AVOID"),
        (45.0, "WATCH"),  # weak_score is inclusive-above
        (54.0, "WATCH"),
        (54.9, "WATCH"),
        (55.0, "WATCH"),
        (69.0, "WATCH"),
        (69.9, "WATCH"),
        (70.0, "STRONG_OPPORTUNITY"),  # strong_score is inclusive
        (100.0, "STRONG_OPPORTUNITY"),
    ],
)
def test_the_score_ladder(score, expected):
    assert market(score) == expected


@pytest.mark.parametrize(("confidence", "expected"), [(49.0, "WATCH"), (49.9, "WATCH"), (50.0, "STRONG_OPPORTUNITY")])
def test_confidence_is_a_veto_on_a_strong_score(confidence, expected):
    """A high score on thin data is a guess, not an opportunity."""
    assert market(90.0, confidence=confidence) == expected


def test_a_strong_score_blocked_by_confidence_says_why():
    decision = decide(
        score=90.0, risk=CALM, confidence=30.0, avoid_score=None, position=NO_POSITION
    )
    assert decision.recommendation == "WATCH"
    assert "confiance" in decision.rationale.lower()
    assert any("confiance" in rule for rule in decision.triggered_rules)


# --------------------------------------------------------------------------- #
# Risk gates come first                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("risk", "expected"), [(64.0, "WATCH"), (64.9, "WATCH"), (65.0, "RISKY")])
def test_the_risky_gate(risk, expected):
    assert market(60.0, risk=risk) == expected


def test_a_strong_score_survives_the_risky_gate():
    """risky_risk only fires when the score does NOT clear strong_score."""
    assert market(70.0, risk=90.0, avoid=10.0) == "STRONG_OPPORTUNITY"


@pytest.mark.parametrize(("avoid", "expected"), [(59.0, "WATCH"), (59.9, "WATCH"), (60.0, "AVOID")])
def test_the_avoid_score_gate(avoid, expected):
    assert market(60.0, avoid=avoid) == expected


def test_risk_is_evaluated_before_opportunity():
    """The conservative ordering: a dangerous reading is never overridden by a score."""
    assert market(69.0, risk=80.0) == "RISKY"


# --------------------------------------------------------------------------- #
# Holder view                                                                  #
# --------------------------------------------------------------------------- #


def test_a_held_position_with_no_sell_signal_is_hold():
    assert holder() == "HOLD"


@pytest.mark.parametrize(("risk", "expected"), [(69.0, "HOLD"), (69.9, "HOLD"), (70.0, "RISKY")])
def test_the_holding_risk_gate(risk, expected):
    assert holder(risk=risk) == expected


@pytest.mark.parametrize(
    ("pl", "expected"),
    [(14.0, "RISKY"), (14.9, "RISKY"), (15.0, "TAKE_PROFIT"), (40.0, "TAKE_PROFIT")],
)
def test_a_sell_signal_becomes_take_profit_only_when_in_profit(pl, expected):
    assert holder(advice="SELL", pl=pl) == expected


def test_a_sell_signal_with_unknown_pl_is_risky_not_take_profit():
    """Never claim a profit we cannot measure."""
    assert holder(advice="SELL", pl=None) == "RISKY"


def test_an_unpriced_holding_is_not_treated_as_held():
    """We cannot advise on a position we cannot value."""
    from moroccan_stock_intelligence.services.recommendation_policy import position_from_holding

    class Unpriced:
        current_price = None
        advice = "SELL"
        net_pl_pct = None

    assert position_from_holding(Unpriced(), 15.0) == NO_POSITION
    assert position_from_holding(None, 15.0) == NO_POSITION


# --------------------------------------------------------------------------- #
# Perspective — the audit's user-visible contradiction                         #
# --------------------------------------------------------------------------- #


def test_the_same_numbers_give_different_verbs_to_a_holder_and_a_non_holder():
    """This is the "ACHETER vs Conserver" case. Both are right; the difference is
    the question being asked, and it is now an explicit field rather than an
    accident of which screen you are on."""
    numbers = {"score": 74.0, "risk": 45.0, "confidence": 60.0}
    as_market = decide(**numbers, position=NO_POSITION)
    as_holder = decide(
        **numbers, position=PositionState(held=True, advice="HOLD", net_pl_pct=8.0)
    )

    assert as_market.recommendation == "STRONG_OPPORTUNITY"
    assert as_market.perspective == MARKET
    assert as_holder.recommendation == "HOLD"
    assert as_holder.perspective == HOLDER
    assert as_holder.is_holder_view


def test_every_decision_carries_its_evidence():
    decision = decide(score=75.0, risk=CALM, confidence=80.0, position=NO_POSITION)
    assert decision.triggered_rules
    assert decision.rationale
    assert decision.label
    assert decision.policy_version


def test_thresholds_are_immutable():
    """A caller must not be able to redefine policy for everyone else."""
    with pytest.raises(Exception):
        THRESHOLDS.strong_score = 1.0


# --------------------------------------------------------------------------- #
# Agreement between the call sites                                             #
# --------------------------------------------------------------------------- #


def test_the_cio_and_the_analysis_screen_reach_the_same_verdict():
    """Same inputs must give the same code, whichever module asked."""
    from moroccan_stock_intelligence.services.investment_analysis import _decide as analysis_decide

    for score in (40.0, 50.0, 60.0, 75.0, 90.0):
        for risk in (10.0, 50.0, 70.0):
            direct = decide(
                score=score, risk=risk, confidence=80.0, avoid_score=risk, position=NO_POSITION
            )
            via_analysis = analysis_decide(score, 80.0, risk, risk, None)
            assert direct.recommendation == via_analysis.recommendation
