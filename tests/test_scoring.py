"""Opportunity-score tests, after the convergence onto the horizon kernel.

Two scoring engines used to be served to the same screen and disagreed on 89% of
symbols (AUDIT_TECHNIQUE.md §4). `score_opportunity` is now a projection of
`horizon_strategy` — one source of truth — and the tests that matter most here are
the ones that keep it that way:

  * `classify_label` uses the CIO's own thresholds, asserted against the CIO's
    module constants rather than restated;
  * an absent component is declared missing, never substituted with a neutral 50 —
    the substitution is what made the old engine unable to tell two days of history
    from three years, and what dragged every score to the middle.
"""

from __future__ import annotations

import pytest

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.scoring import (
    ScoreResult,
    classify_label,
    score_opportunity,
)


def _metric(**overrides) -> MetricSet:
    base = {
        "stock_id": 1,
        "symbol": "TGC",
        "company_name": "TGCC",
        "sector": "Construction",
        "price": 700,
        "daily_variation": 2,
        "volume": 100000,
        "momentum_1d": 1,
        "momentum_5d": 4,
        "momentum_30d": 12,
        "momentum_90d": 20,
        "ma20": 680,
        "ma50": 650,
        "ma200": 600,
        "volatility_30d": 22,
        "volume_anomaly": 2.5,
        "relative_performance_30d": 5,
        "drawdown_from_recent_high": -4,
        "support": 690,
        "resistance": 730,
        "support_distance": 1.45,
        "resistance_distance": -4.1,
        "week52_high": 730,
        "week52_low": 500,
        "week52_high_proximity": -4.1,
        "week52_low_proximity": 40,
        "sector_strength": 8,
    }
    base.update(overrides)
    return MetricSet(**base)


def _score(**kw) -> ScoreResult:
    defaults = {
        "symbol": "X",
        "buy_score": 50.0,
        "watch_score": 50.0,
        "avoid_score": 10.0,
        "reasons": [],
        "risks": [],
        "components": {},
        "confidence": 80.0,
    }
    defaults.update(kw)
    return ScoreResult(**defaults)


# --------------------------------------------------------------------------- #
# The contract every consumer reads.                                           #
# --------------------------------------------------------------------------- #


def test_scores_are_bounded_and_explained():
    score = score_opportunity(_metric(), NewsContext(count=2, avg_impact=0.5), history_days=300)
    assert 0 <= score.buy_score <= 100
    assert 0 <= score.watch_score <= 100
    assert 0 <= score.avoid_score <= 100
    assert 0 <= score.confidence <= 100
    assert score.reasons
    assert score.risks


def test_the_score_now_carries_how_much_data_backs_it():
    """The point of the convergence: the old engine had no notion of confidence."""
    thin = score_opportunity(_metric(), NewsContext(), history_days=2)
    deep = score_opportunity(_metric(), NewsContext(count=3, avg_impact=0.1), history_days=400)
    assert deep.confidence > thin.confidence


def test_an_absent_component_is_declared_missing_not_invented():
    """The old engine replaced a missing component with a hardcoded 50, which is a
    claim about data it does not have."""
    sparse = _metric(volume_anomaly=None, week52_high_proximity=None, support_distance=None)
    score = score_opportunity(sparse, NewsContext(), history_days=5)

    assert "volume" not in score.components
    assert "cassure" not in score.components
    assert "support" not in score.components
    assert "actualites" not in score.components
    assert len(score.missing) >= 4
    assert score.coverage < 1.0


def test_thin_data_shrinks_the_score_toward_neutral():
    """A strong score built on one available component would be fake certainty."""
    full = score_opportunity(_metric(), NewsContext(count=2, avg_impact=0.0), history_days=400)
    sparse = score_opportunity(
        _metric(volume_anomaly=None, week52_high_proximity=None, support_distance=None),
        NewsContext(),
        history_days=400,
    )
    assert abs(sparse.buy_score - 50) < abs(full.buy_score - 50)


def test_news_reaches_the_score():
    neutral = score_opportunity(_metric(), NewsContext(count=1, avg_impact=0.0), history_days=300)
    bad = score_opportunity(_metric(), NewsContext(count=1, avg_impact=-0.85), history_days=300)
    assert bad.buy_score < neutral.buy_score
    assert bad.avoid_score >= neutral.avoid_score


# --------------------------------------------------------------------------- #
# The anti-contradiction guarantee.                                            #
# --------------------------------------------------------------------------- #


def test_the_label_thresholds_are_the_cios_own():
    """THE test of the convergence.

    The Opportunités tab (this label) and the report (the CIO's verdict) must never
    disagree about the same stock. They agreed on nothing before: 71 of 80 symbols
    diverged. Asserted against the CIO's constants rather than restated, so drifting
    one apart from the other fails here.
    """
    from moroccan_stock_intelligence.services.analysts import cio
    from moroccan_stock_intelligence.services import scoring

    assert scoring.STRONG_SCORE == 70.0
    assert scoring.STRONG_CONFIDENCE == 50.0
    assert scoring.WATCH_SCORE == 55.0
    assert scoring.WEAK_SCORE == 45.0
    # Previously this test read the CIO's source and checked the same literals
    # appeared there — the best you can do when a rule is duplicated. The rule is
    # not duplicated any more, so the property can be asserted directly: both sides
    # are views onto the ONE policy object, and cannot hold different numbers.
    from moroccan_stock_intelligence.services import recommendation_policy

    assert scoring.STRONG_SCORE is recommendation_policy.THRESHOLDS.strong_score
    assert scoring.STRONG_CONFIDENCE is recommendation_policy.THRESHOLDS.strong_confidence
    assert scoring.WATCH_SCORE is recommendation_policy.THRESHOLDS.watch_score
    assert scoring.WEAK_SCORE is recommendation_policy.THRESHOLDS.weak_score
    assert scoring.AVOID_RISK is recommendation_policy.THRESHOLDS.avoid_risk
    assert scoring.RISKY_RISK is recommendation_policy.THRESHOLDS.risky_risk


def test_the_cio_and_the_tab_call_the_same_policy():
    """The structural guarantee that replaced the source-inspection check above.

    If someone reintroduces a local `_recommend` in either module, this fails.
    """
    import inspect

    from moroccan_stock_intelligence.services import recommendation_policy, scoring
    from moroccan_stock_intelligence.services.analysts import cio

    assert not hasattr(cio, "_recommend"), "the CIO must not hold its own copy of the rule"
    assert "decide_recommendation" in inspect.getsource(cio._decide)
    assert "decide(" in inspect.getsource(scoring.classify_label)
    assert recommendation_policy.POLICY_VERSION


def test_score_opportunity_and_the_kernel_agree_by_construction():
    """Not "the same answer" — literally the same computation."""
    from moroccan_stock_intelligence.services.horizon_strategy import assess_short

    metric, news = _metric(), NewsContext(count=2, avg_impact=0.3)
    assert score_opportunity(metric, news, 300).buy_score == round(
        assess_short(metric, news).score, 2
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"buy_score": 75.0, "confidence": 60.0}, "ACHETER"),
        # Same score, but we barely know anything: not an opportunity, a guess.
        ({"buy_score": 75.0, "confidence": 30.0}, "SURVEILLER"),
        ({"buy_score": 60.0}, "SURVEILLER"),
        ({"buy_score": 50.0}, "NEUTRE"),
        ({"buy_score": 40.0}, "ÉVITER"),
        ({"buy_score": 55.0, "avoid_score": 62.0}, "ÉVITER"),
        ({"buy_score": 60.0, "avoid_score": 70.0}, "ÉVITER"),
    ],
)
def test_classify_label(kwargs, expected):
    assert classify_label(_score(**kwargs)) == expected


def test_a_high_score_with_no_confidence_is_not_a_buy():
    """Confidence is a veto, not decoration: the CIO applies exactly this rule."""
    assert classify_label(_score(buy_score=90.0, confidence=49.0)) != "ACHETER"
    assert classify_label(_score(buy_score=90.0, confidence=50.0)) == "ACHETER"


def test_no_score_is_neutral():
    assert classify_label(None) == "NEUTRE"
