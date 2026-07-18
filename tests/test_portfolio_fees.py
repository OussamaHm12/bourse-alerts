"""A round trip pays commission twice.

Only the sell side was charged (AUDIT_2026-07-18.md §8), so every displayed P/L
was optimistic by roughly `fee_rate` of the position. At the default 0.5% that is
enough to show a position clearing the +15% take-profit threshold when it has not
— i.e. the error could change the advice, not just the number.
"""

from __future__ import annotations

import pytest

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.portfolio import Holding, evaluate_holding

FEE = 0.005


def metric(price: float | None, momentum_30d: float = 5.0) -> MetricSet:
    return MetricSet(
        stock_id=1,
        symbol="ATW",
        company_name="ATTIJARIWAFA BANK",
        sector="Banques",
        price=price,
        daily_variation=0.3,
        volume=1_000_000.0,
        momentum_1d=0.1,
        momentum_5d=0.5,
        momentum_30d=momentum_30d,
        momentum_90d=4.0,
        ma20=None,
        ma50=None,
        ma200=None,
        volatility_30d=15.0,
        volume_anomaly=1.0,
        relative_performance_30d=0.0,
        drawdown_from_recent_high=-1.0,
        support=None,
        resistance=None,
        support_distance=None,
        resistance_distance=None,
        week52_high=None,
        week52_low=None,
        week52_high_proximity=None,
        week52_low_proximity=None,
        sector_strength=1.0,
    )


def evaluate(buy: float, now: float | None, quantity: float = 100.0, momentum: float = 5.0):
    return evaluate_holding(
        Holding(symbol="ATW", quantity=quantity, buy_price=buy),
        metric(now, momentum_30d=momentum),
        None,
        FEE,
    )


# --------------------------------------------------------------------------- #
# Both commissions are charged                                                 #
# --------------------------------------------------------------------------- #


def test_both_sides_of_the_round_trip_are_charged():
    result = evaluate(buy=100.0, now=110.0)
    assert result.entry_fees == pytest.approx(100.0 * 100 * FEE)  # 50
    assert result.exit_fees == pytest.approx(110.0 * 100 * FEE)  # 55
    assert result.fees == pytest.approx(105.0)


def test_the_net_pl_subtracts_both_commissions():
    result = evaluate(buy=100.0, now=110.0)
    assert result.gross_pl == pytest.approx(1_000.0)
    assert result.net_pl == pytest.approx(1_000.0 - 105.0)


def test_cost_basis_stays_the_pure_acquisition_cost():
    """It is what the app shows as "what you paid for the shares"; the commission
    is a separate, named term rather than folded into it."""
    assert evaluate(buy=100.0, now=110.0).cost_basis == pytest.approx(10_000.0)


# --------------------------------------------------------------------------- #
# Break-even moves — the point of the fix                                      #
# --------------------------------------------------------------------------- #


def test_a_flat_position_is_a_loss_once_commissions_are_counted():
    result = evaluate(buy=100.0, now=100.0)
    assert result.gross_pl == pytest.approx(0.0)
    assert result.net_pl < 0
    assert result.net_pl_pct < 0


def test_break_even_needs_slightly_more_than_the_entry_price():
    """Roughly 2 x fee_rate, which is exactly the amount previously ignored."""
    barely_up = evaluate(buy=100.0, now=100.5)
    assert barely_up.net_pl < 0
    clearly_up = evaluate(buy=100.0, now=101.5)
    assert clearly_up.net_pl > 0


def test_the_percentage_is_measured_against_capital_actually_committed():
    """cost + entry commission. Dividing by cost alone overstates the return."""
    result = evaluate(buy=100.0, now=110.0)
    invested = 10_000.0 + 50.0
    assert result.net_pl_pct == pytest.approx((1_000.0 - 105.0) / invested * 100)


def test_the_old_arithmetic_would_have_been_more_optimistic():
    """Guards the regression directly: the previous formula charged one side and
    divided by cost_basis."""
    result = evaluate(buy=100.0, now=110.0)
    old_net = 1_000.0 - (110.0 * 100 * FEE)
    old_pct = old_net / 10_000.0 * 100
    assert result.net_pl < old_net
    assert result.net_pl_pct < old_pct


# --------------------------------------------------------------------------- #
# Advice thresholds see the corrected number                                   #
# --------------------------------------------------------------------------- #


def test_a_position_just_under_take_profit_no_longer_reads_as_over_it():
    """+15.0% gross becomes ~+13.9% net, so a take-profit rule must not fire."""
    result = evaluate(buy=100.0, now=115.0, momentum=-5.0)
    assert result.net_pl_pct < 15.0
    assert result.advice == "HOLD"


def test_a_genuinely_large_gain_with_weak_momentum_still_triggers_a_sell():
    result = evaluate(buy=100.0, now=125.0, momentum=-5.0)
    assert result.net_pl_pct >= 15.0
    assert result.advice == "SELL"


# --------------------------------------------------------------------------- #
# Edge cases                                                                   #
# --------------------------------------------------------------------------- #


def test_a_losing_position_is_worse_than_the_price_move_alone():
    result = evaluate(buy=100.0, now=90.0)
    assert result.net_pl < -1_000.0


def test_a_missing_price_reports_nothing_rather_than_zero():
    result = evaluate(buy=100.0, now=None)
    assert result.net_pl is None
    assert result.net_pl_pct is None
    assert result.fees is None
    assert result.advice == "HOLD"


def test_a_zero_fee_rate_charges_nothing():
    result = evaluate_holding(
        Holding(symbol="ATW", quantity=100.0, buy_price=100.0), metric(110.0), None, 0.0
    )
    assert result.fees == pytest.approx(0.0)
    assert result.net_pl == pytest.approx(result.gross_pl)


def test_fractional_quantities_are_handled():
    result = evaluate(buy=100.0, now=110.0, quantity=2.5)
    assert result.entry_fees == pytest.approx(250.0 * FEE)
    assert result.exit_fees == pytest.approx(275.0 * FEE)
