from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import (
    LONG_WEIGHTS,
    MEDIUM_WEIGHTS,
    SHORT_WEIGHTS,
    NewsContext,
    assess_all,
    assess_long,
    assess_medium,
    assess_short,
    compute_confidence,
    compute_risk,
)


def _metric(**kwargs):
    base = dict(
        stock_id=1,
        symbol="ATW",
        company_name="Attijariwafa",
        sector="Banques",
        price=415.0,
        daily_variation=1.0,
        volume=1000.0,
        momentum_1d=0.5,
        momentum_5d=1.5,
        momentum_30d=4.0,
        momentum_90d=8.0,
        ma20=400.0,
        ma50=390.0,
        ma200=380.0,
        volatility_30d=20.0,
        volume_anomaly=1.2,
        relative_performance_30d=1.0,
        drawdown_from_recent_high=-2.0,
        support=400.0,
        resistance=430.0,
        support_distance=3.0,
        resistance_distance=-3.0,
        week52_high=440.0,
        week52_low=350.0,
        week52_high_proximity=-5.0,
        week52_low_proximity=18.0,
        sector_strength=3.0,
    )
    base.update(kwargs)
    return MetricSet(**base)


_SPARSE = dict(
    momentum_1d=None,
    momentum_5d=None,
    momentum_30d=None,
    momentum_90d=None,
    ma20=None,
    ma50=None,
    ma200=None,
    volatility_30d=None,
    volume_anomaly=None,
    relative_performance_30d=None,
    drawdown_from_recent_high=None,
    support=None,
    resistance=None,
    support_distance=None,
    resistance_distance=None,
    week52_high=None,
    week52_low=None,
    week52_high_proximity=None,
    week52_low_proximity=None,
    sector_strength=None,
)


def test_short_assessment_bounded_and_explained():
    assessment = assess_short(_metric(), NewsContext())
    assert 0 <= assessment.score <= 100
    assert assessment.coverage > 0.5
    assert set(assessment.components) == set(SHORT_WEIGHTS)
    # No news collected -> explicitly reported as missing, never invented.
    assert any("actualité" in item.lower() for item in assessment.missing)


def test_medium_and_long_use_their_own_components():
    medium = assess_medium(_metric(), NewsContext())
    long_term = assess_long(_metric(), NewsContext(), history_days=300)
    assert set(medium.components) == set(MEDIUM_WEIGHTS)
    assert set(long_term.components) == set(LONG_WEIGHTS)
    assert 0 <= medium.score <= 100
    assert 0 <= long_term.score <= 100


def test_sparse_data_lowers_coverage_and_caps_confidence():
    assessment = assess_short(_metric(**_SPARSE), NewsContext())
    assert assessment.coverage < 0.5
    assert assessment.missing
    confidence, reason = compute_confidence(assessment, history_days=2)
    assert confidence <= 35
    assert reason


def test_risk_higher_for_crashing_stock():
    calm_risk, _ = compute_risk(_metric(), NewsContext(), history_days=120)
    crash_risk, reasons = compute_risk(
        _metric(
            momentum_30d=-15.0,
            volatility_30d=55.0,
            daily_variation=-6.0,
            drawdown_from_recent_high=-30.0,
            volume_anomaly=3.0,
        ),
        NewsContext(fresh_negative=True),
        history_days=120,
    )
    assert 0 <= calm_risk <= 100 and 0 <= crash_risk <= 100
    assert crash_risk > calm_risk
    assert reasons


def test_long_horizon_always_flags_missing_fundamentals():
    assessment = assess_long(_metric(), NewsContext(), history_days=300)
    assert any("fondamentaux" in item.lower() for item in assessment.missing)


def test_short_history_gates_ma200_and_notes_it():
    assessment = assess_long(_metric(), NewsContext(), history_days=30)
    assert any("mm200" in note.lower() for note in assessment.notes)
    assert any("historique" in note.lower() for note in assessment.notes)


def test_assess_all_returns_three_horizons():
    result = assess_all(_metric(), NewsContext(), history_days=100)
    assert set(result) == {"short", "medium", "long"}


def test_dividend_news_lifts_long_events_component():
    without = assess_long(_metric(), NewsContext(), history_days=300)
    with_dividend = assess_long(
        _metric(), NewsContext(count=1, has_dividend=True), history_days=300
    )
    assert with_dividend.components["evenements"] == 70.0
    assert without.components["evenements"] is None
