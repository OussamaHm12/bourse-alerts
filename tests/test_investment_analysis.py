from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.investment_analysis import (
    RECOMMENDATION_LABELS_FR,
    compose_analysis,
)
from moroccan_stock_intelligence.services.portfolio import Holding, evaluate_holding
from moroccan_stock_intelligence.services.scoring import score_opportunity


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


EXPLAINABILITY_KEYS = {
    "data_used",
    "positive_factors",
    "negative_factors",
    "missing_data",
    "decision_reason",
    "confidence_reason",
    "risk_reason",
}


def test_compose_analysis_structure_and_explainability():
    metric = _metric()
    analysis = compose_analysis(
        metric, score_opportunity(metric), None, NewsContext(), history_days=100, horizon="short"
    )
    assert analysis["symbol"] == "ATW"
    assert analysis["recommendation"] in RECOMMENDATION_LABELS_FR
    assert analysis["recommendation_label"] == RECOMMENDATION_LABELS_FR[analysis["recommendation"]]
    assert 0 <= analysis["confidence"] <= 100
    assert 0 <= analysis["risk_score"] <= 100
    assert set(analysis["scores"]) == {"short", "medium", "long"}
    assert set(analysis["explainability"]) == EXPLAINABILITY_KEYS
    assert analysis["explainability"]["data_used"]
    assert analysis["explanation"]
    assert analysis["expected_scenario"]
    assert analysis["watch_next"]
    assert "conseil en investissement" in analysis["disclaimer"]
    assert analysis["portfolio"] is None


def test_compose_analysis_includes_portfolio_impact():
    metric = _metric(price=415.0)
    score = score_opportunity(metric)
    holding = evaluate_holding(
        Holding(symbol="ATW", quantity=10, buy_price=410.0), metric, score, fee_rate=0.005
    )
    analysis = compose_analysis(metric, score, holding, NewsContext(), 100, "medium")
    assert analysis["portfolio"] is not None
    assert analysis["portfolio"]["held"] is True
    assert analysis["portfolio"]["net_pl_pct"] is not None
    # A held position never gets a pure buy-side call.
    assert analysis["recommendation"] in {"HOLD", "TAKE_PROFIT", "RISKY"}


def test_missing_data_is_reported_not_invented():
    sparse = _metric(
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
    analysis = compose_analysis(sparse, None, None, NewsContext(), history_days=2, horizon="long")
    assert analysis["explainability"]["missing_data"]
    assert analysis["confidence"] <= 35
    assert "insuffisant" in analysis["explanation"].lower() or "trop court" in analysis["explanation"].lower()


def test_language_is_probabilistic_not_certain():
    metric = _metric(momentum_5d=6.0, momentum_30d=12.0, volume_anomaly=2.5)
    analysis = compose_analysis(
        metric, score_opportunity(metric), None, NewsContext(), history_days=200, horizon="short"
    )
    text = (analysis["explanation"] + " " + analysis["expected_scenario"]).lower()
    assert "va monter" not in text
    assert "garanti" not in text or "sans garantie" in text
