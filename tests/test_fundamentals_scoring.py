"""Published fundamentals must actually move the long-horizon score.

`assess_long` used to append "Fondamentaux non collectés pour l'instant" to every
assessment unconditionally, and `LONG_WEIGHTS` had no fundamental term
(AUDIT_2026-07-18.md §7). The claim had been false since the Phase 1b issuer
collector shipped, and the consequence was that a stock at PER 45 scored exactly
like one at PER 8 on the one horizon where valuation matters most.
"""

from __future__ import annotations

import pytest

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import (
    FUNDAMENTAL_WEIGHTS,
    LONG_WEIGHTS,
    NewsContext,
    assess_long,
    score_fundamentals,
)
from moroccan_stock_intelligence.services.research.context import Fundamentals


def metric(**overrides) -> MetricSet:
    base = dict(
        stock_id=1,
        symbol="ATW",
        company_name="ATTIJARIWAFA BANK",
        sector="Banques",
        price=400.0,
        daily_variation=0.4,
        volume=1_000_000.0,
        momentum_1d=0.1,
        momentum_5d=0.8,
        momentum_30d=2.0,
        momentum_90d=5.0,
        ma20=395.0,
        ma50=390.0,
        ma200=380.0,
        volatility_30d=18.0,
        volume_anomaly=1.0,
        relative_performance_30d=0.5,
        drawdown_from_recent_high=-2.0,
        support=380.0,
        resistance=420.0,
        support_distance=5.0,
        resistance_distance=-4.8,
        week52_high=420.0,
        week52_low=350.0,
        week52_high_proximity=-4.8,
        week52_low_proximity=14.0,
        sector_strength=1.5,
    )
    base.update(overrides)
    return MetricSet(**base)


def long_score(fundamentals) -> float:
    return assess_long(metric(), NewsContext(count=1), 400, fundamentals).score


# --------------------------------------------------------------------------- #
# The weights                                                                  #
# --------------------------------------------------------------------------- #


def test_the_long_horizon_has_a_fundamental_term():
    assert "fondamentaux" in LONG_WEIGHTS
    assert LONG_WEIGHTS["fondamentaux"] > 0


def test_long_weights_still_sum_to_one():
    assert sum(LONG_WEIGHTS.values()) == pytest.approx(1.0)


def test_fundamental_sub_weights_sum_to_one():
    assert sum(FUNDAMENTAL_WEIGHTS.values()) == pytest.approx(1.0)


def test_fundamentals_cannot_outvote_price_behaviour():
    """Six published ratios must not outweigh three years of price action."""
    assert LONG_WEIGHTS["fondamentaux"] < (
        LONG_WEIGHTS["tendance_longue"] + LONG_WEIGHTS["stabilite"]
    )


# --------------------------------------------------------------------------- #
# The headline consequence                                                     #
# --------------------------------------------------------------------------- #


def test_a_cheap_stock_scores_better_than_an_expensive_one():
    """Before this change the two were identical."""
    cheap = long_score(Fundamentals(fiscal_year=2025, per=8.0, roe=15.0, pbr=1.0))
    rich = long_score(Fundamentals(fiscal_year=2025, per=45.0, roe=15.0, pbr=5.0))
    assert cheap > rich


def test_a_profitable_company_scores_better_than_an_unprofitable_one():
    strong = long_score(Fundamentals(fiscal_year=2025, per=15.0, roe=22.0))
    weak = long_score(Fundamentals(fiscal_year=2025, per=15.0, roe=2.0))
    assert strong > weak


def test_a_dividend_payer_scores_better_all_else_equal():
    payer = long_score(Fundamentals(fiscal_year=2025, per=15.0, dividend_yield=6.0))
    none_paid = long_score(Fundamentals(fiscal_year=2025, per=15.0, dividend_yield=0.0))
    assert payer > none_paid


# --------------------------------------------------------------------------- #
# The false claim is gone                                                      #
# --------------------------------------------------------------------------- #


def test_collected_fundamentals_are_not_reported_as_missing():
    assessment = assess_long(
        metric(), NewsContext(count=1), 400, Fundamentals(fiscal_year=2025, per=12.0, roe=15.0, pbr=1.2, dividend_yield=4.0)
    )
    joined = " ".join(assessment.missing).lower()
    assert "non collectés" not in joined
    assert assessment.components["fondamentaux"] is not None


def test_absent_fundamentals_are_declared_and_lower_coverage():
    with_data = assess_long(
        metric(), NewsContext(count=1), 400,
        Fundamentals(fiscal_year=2025, per=12.0, roe=15.0, pbr=1.2, dividend_yield=4.0),
    )
    without = assess_long(metric(), NewsContext(count=1), 400, None)
    assert without.components["fondamentaux"] is None
    assert without.coverage < with_data.coverage
    assert any("non collectés" in note for note in without.missing)


def test_the_default_argument_keeps_old_callers_working():
    assert assess_long(metric(), NewsContext(count=1), 400).components["fondamentaux"] is None


# --------------------------------------------------------------------------- #
# score_fundamentals itself                                                    #
# --------------------------------------------------------------------------- #


def test_no_data_yields_none_not_a_neutral_fifty():
    score, _, _, missing = score_fundamentals(None)
    assert score is None
    assert missing


def test_an_empty_fundamentals_object_yields_none():
    score, _, _, _ = score_fundamentals(Fundamentals(fiscal_year=2025))
    assert score is None


@pytest.mark.parametrize("per", [-5.0, 0.0])
def test_a_negative_or_zero_per_is_unmeasurable_not_cheap(per):
    """A negative multiple is meaningless; scoring it as "very cheap" would invert
    the signal for exactly the companies that just lost money."""
    score, _, _, missing = score_fundamentals(Fundamentals(fiscal_year=2025, per=per))
    assert score is None
    assert any("négatif" in note or "non publié" in note for note in missing)


def test_extreme_values_are_winsorised():
    """PER 300 and PER 60 are both simply "expensive"; letting the first dominate a
    weighted mean would be a data artefact driving a recommendation."""
    absurd, _, _, _ = score_fundamentals(Fundamentals(fiscal_year=2025, per=300.0, roe=15.0))
    merely_rich, _, _, _ = score_fundamentals(Fundamentals(fiscal_year=2025, per=60.0, roe=15.0))
    assert absurd == merely_rich


def test_partial_data_is_shrunk_toward_neutral():
    """One sub-component out of three must not speak as loudly as a complete set."""
    complete, _, _, _ = score_fundamentals(
        Fundamentals(fiscal_year=2025, per=6.0, pbr=0.5, roe=25.0, dividend_yield=9.0)
    )
    partial, _, _, _ = score_fundamentals(Fundamentals(fiscal_year=2025, roe=25.0))
    assert complete > partial


def test_a_derived_per_is_flagged_as_an_inference():
    _, _, _, missing = score_fundamentals(
        Fundamentals(fiscal_year=2025, per=14.0, per_is_derived=True)
    )
    assert any("inférence" in note.lower() for note in missing)


def test_strengths_and_weaknesses_are_explained_in_words():
    _, positives, negatives, _ = score_fundamentals(
        Fundamentals(fiscal_year=2025, per=8.0, roe=22.0, dividend_yield=6.0)
    )
    assert positives
    assert not negatives

    _, _, bad, _ = score_fundamentals(Fundamentals(fiscal_year=2025, per=40.0, roe=1.0))
    assert bad


def test_the_score_stays_inside_its_scale():
    for fundamentals in (
        Fundamentals(fiscal_year=2025, per=1.0, pbr=0.1, roe=99.0, dividend_yield=50.0),
        Fundamentals(fiscal_year=2025, per=999.0, pbr=99.0, roe=0.1, dividend_yield=0.0),
    ):
        score, _, _, _ = score_fundamentals(fundamentals)
        assert 0.0 <= score <= 100.0
