from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.scoring import score_opportunity


def test_score_opportunity_is_bounded_and_explained():
    metric = MetricSet(
        stock_id=1,
        symbol="TGC",
        company_name="TGCC",
        sector="Construction",
        price=700,
        daily_variation=2,
        volume=100000,
        momentum_1d=1,
        momentum_5d=4,
        momentum_30d=12,
        momentum_90d=20,
        ma20=680,
        ma50=650,
        ma200=600,
        volatility_30d=22,
        volume_anomaly=2.5,
        relative_performance_30d=5,
        drawdown_from_recent_high=-4,
        support=690,
        resistance=730,
        support_distance=1.45,
        resistance_distance=-4.1,
        week52_high=730,
        week52_low=500,
        week52_high_proximity=-4.1,
        week52_low_proximity=40,
        sector_strength=8,
    )
    score = score_opportunity(metric, news_sentiment_score=0.5)
    assert 0 <= score.buy_score <= 100
    assert 0 <= score.watch_score <= 100
    assert 0 <= score.avoid_score <= 100
    assert score.reasons
    assert score.risks
