import pytest

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.portfolio import Holding, evaluate_holding
from moroccan_stock_intelligence.services.scoring import score_opportunity


def _metric(symbol="ATW", price=415.0, momentum_30d=4.0, daily_variation=1.0, **kwargs):
    base = dict(
        stock_id=1,
        symbol=symbol,
        company_name="Attijariwafa",
        sector="Banques",
        price=price,
        daily_variation=daily_variation,
        volume=1000.0,
        momentum_1d=0.5,
        momentum_5d=1.0,
        momentum_30d=momentum_30d,
        momentum_90d=5.0,
        ma20=400.0,
        ma50=390.0,
        ma200=None,
        volatility_30d=20.0,
        volume_anomaly=1.0,
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


def test_net_pl_is_after_both_commissions():
    """A round trip pays commission twice.

    This test previously encoded only the sell-side fee, which is the defect the
    audit found (§8): it made every P/L optimistic by about `fee_rate` of the
    position. Deep-dive coverage lives in tests/test_portfolio_fees.py.
    """
    holding = Holding(symbol="ATW", quantity=10, buy_price=410.0)
    metric = _metric(price=415.0)
    ev = evaluate_holding(holding, metric, score_opportunity(metric), fee_rate=0.005)

    assert ev.cost_basis == 4100.0
    assert ev.market_value == 4150.0
    assert ev.gross_pl == 50.0
    assert ev.entry_fees == 4100.0 * 0.005  # 20.50, previously ignored
    assert ev.exit_fees == 4150.0 * 0.005  # 20.75
    assert ev.fees == pytest.approx(41.25)
    assert round(ev.net_pl, 2) == round(50.0 - 41.25, 2)  # 8.75


def test_advice_hold_when_trend_intact():
    holding = Holding(symbol="ATW", quantity=10, buy_price=410.0)
    metric = _metric(price=415.0, momentum_30d=4.0)
    ev = evaluate_holding(holding, metric, score_opportunity(metric), fee_rate=0.005)
    assert ev.advice == "HOLD"


def test_advice_sell_on_stop_loss():
    holding = Holding(symbol="ATW", quantity=10, buy_price=500.0)
    metric = _metric(price=440.0, momentum_30d=-1.0)  # -12% before fees
    ev = evaluate_holding(holding, metric, score_opportunity(metric), fee_rate=0.005)
    assert ev.advice == "SELL"
    assert "Stop-loss" in ev.advice_reason


def test_advice_take_profit_with_weak_momentum():
    holding = Holding(symbol="ATW", quantity=10, buy_price=350.0)
    metric = _metric(price=415.0, momentum_30d=-4.0)  # +~17% net, momentum weakening
    ev = evaluate_holding(holding, metric, score_opportunity(metric), fee_rate=0.005)
    assert ev.advice == "SELL"
    assert "bénéfices" in ev.advice_reason


def test_missing_price_defaults_to_hold():
    holding = Holding(symbol="ATW", quantity=10, buy_price=410.0)
    metric = _metric(price=None)
    ev = evaluate_holding(holding, metric, None, fee_rate=0.005)
    assert ev.advice == "HOLD"
    assert ev.net_pl is None
