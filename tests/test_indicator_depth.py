"""A windowed indicator may not claim its own name without the history to back it.

The defect (AUDIT_2026-07-18.md §6, rated P1): `prices.tail(200).mean()` returns
the mean of whatever exists, so a symbol with ten séances reported a MA200. It was
never None, so it bypassed the coverage mechanism the whole engine rests on — the
medium horizon counted "price > MA50" as satisfied, `_trend()` called the symbol
bullish, and market breadth aggregated fictional MA50s.

These tests pin the boundary exactly: at N-1 observations the value is absent, at
N it appears. Boundaries are where this class of bug lives.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from moroccan_stock_intelligence.services import analytics
from moroccan_stock_intelligence.services.analytics import (
    MA_WINDOWS,
    RANGE_MIN_OBSERVATIONS,
    VOLATILITY_MIN_OBSERVATIONS,
    WEEK52_MIN_OBSERVATIONS,
    compute_metrics,
)


def frame(count: int, *, price=None, volume: float | None = 1_000_000.0) -> pd.DataFrame:
    """`count` daily séances for one symbol, oldest first."""
    start = datetime.now(UTC) - timedelta(days=count)
    rows = []
    for day in range(count):
        rows.append(
            {
                "stock_id": 1,
                "symbol": "ATW",
                "company_name": "ATTIJARIWAFA BANK",
                "sector": "Banques",
                "observed_at": start + timedelta(days=day),
                "current_price": (100.0 + day) if price is None else price(day),
                "daily_variation": 0.5,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def only(count: int, **kwargs):
    metrics = compute_metrics(frame(count, **kwargs))
    return metrics[0] if metrics else None


# --------------------------------------------------------------------------- #
# Degenerate inputs                                                            #
# --------------------------------------------------------------------------- #


def test_an_empty_frame_yields_nothing():
    assert compute_metrics(pd.DataFrame()) == []


@pytest.mark.parametrize("count", [1, 2, 3])
def test_almost_no_history_still_produces_a_metric_with_a_price(count):
    """The symbol must still appear — with a price, and with everything else absent."""
    metric = only(count)
    assert metric is not None
    assert metric.price is not None
    assert metric.ma20 is None
    assert metric.ma50 is None
    assert metric.ma200 is None
    assert metric.week52_high is None


# --------------------------------------------------------------------------- #
# Moving averages — the exact boundary                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field,window", sorted(MA_WINDOWS.items()))
def test_a_moving_average_is_absent_one_observation_short(field, window):
    assert getattr(only(window - 1), field) is None


@pytest.mark.parametrize("field,window", sorted(MA_WINDOWS.items()))
def test_a_moving_average_appears_exactly_at_its_window(field, window):
    assert getattr(only(window), field) is not None


@pytest.mark.parametrize("count", [10, 19, 49, 179, 199])
def test_a_short_history_never_reports_a_ma200(count):
    """The headline defect: ten séances presented as a 200-day average."""
    assert only(count).ma200 is None


def test_a_moving_average_is_the_mean_of_its_window_not_of_everything():
    """With 300 rising séances, MA20 must be the last 20 — near the top, not the middle."""
    metric = only(300)
    assert metric.ma20 > metric.ma50 > metric.ma200
    assert metric.ma20 == pytest.approx(sum(range(280, 300)) / 20 + 100.0)


# --------------------------------------------------------------------------- #
# Configurable tolerance — bounded, and it cannot resurrect the bug            #
# --------------------------------------------------------------------------- #


def test_the_tolerance_can_relax_the_requirement(monkeypatch):
    relaxed = dataclasses.replace(analytics.settings, ma_min_coverage=0.9)
    monkeypatch.setattr(analytics, "settings", relaxed)
    assert only(180).ma200 is not None  # 180 >= 200 * 0.9


def test_the_tolerance_is_floored_and_cannot_resurrect_the_defect(monkeypatch):
    """MA_MIN_COVERAGE=0.05 must NOT turn ten séances into a MA200."""
    absurd = dataclasses.replace(analytics.settings, ma_min_coverage=0.05)
    monkeypatch.setattr(analytics, "settings", absurd)
    assert only(10).ma200 is None
    assert only(149).ma200 is None  # floored at 0.75 -> needs 150


def test_the_tolerance_cannot_exceed_one(monkeypatch):
    generous = dataclasses.replace(analytics.settings, ma_min_coverage=5.0)
    monkeypatch.setattr(analytics, "settings", generous)
    assert only(200).ma200 is not None


# --------------------------------------------------------------------------- #
# Volatility                                                                   #
# --------------------------------------------------------------------------- #


def test_volatility_needs_a_meaningful_number_of_returns():
    assert only(VOLATILITY_MIN_OBSERVATIONS - 1).volatility_30d is None
    assert only(VOLATILITY_MIN_OBSERVATIONS).volatility_30d is not None


def test_three_observations_do_not_make_a_thirty_day_volatility():
    assert only(3).volatility_30d is None


def test_a_constant_series_has_zero_volatility_not_none():
    """Zero is a fact about the data; None would claim it was unmeasurable."""
    metric = only(60, price=lambda _day: 100.0)
    assert metric.volatility_30d == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Ranges                                                                       #
# --------------------------------------------------------------------------- #


def test_support_and_resistance_need_a_range_not_a_line():
    assert only(RANGE_MIN_OBSERVATIONS - 1).support is None
    assert only(RANGE_MIN_OBSERVATIONS).support is not None


def test_the_52_week_range_needs_most_of_a_year():
    assert only(WEEK52_MIN_OBSERVATIONS - 1).week52_high is None
    assert only(WEEK52_MIN_OBSERVATIONS).week52_high is not None


def test_derived_distances_are_absent_when_their_anchor_is():
    """A distance to a level that does not exist must not be a number."""
    metric = only(10)
    assert metric.support_distance is None
    assert metric.resistance_distance is None
    assert metric.week52_high_proximity is None
    assert metric.drawdown_from_recent_high is None


# --------------------------------------------------------------------------- #
# Dirty data                                                                   #
# --------------------------------------------------------------------------- #


def test_zero_volumes_do_not_produce_a_volume_anomaly():
    """Dividing by a zero mean would be inf, which is worse than None."""
    assert only(60, volume=0.0).volume_anomaly is None


def test_missing_volumes_do_not_crash_the_pipeline():
    assert only(60, volume=None).volume_anomaly is None


def test_gaps_in_the_calendar_do_not_invent_observations():
    """Resampling to 1D emits NaN rows for missing days; they must not count as history."""
    rows = frame(30)
    sparse = rows.iloc[::3].reset_index(drop=True)  # ~10 real séances spread over 30 days
    metric = compute_metrics(sparse)[0]
    assert metric.ma20 is None, "resampled gaps must not be counted as observations"


def test_rows_without_a_price_are_dropped_not_treated_as_zero():
    rows = frame(60)
    rows.loc[rows.index[:30], "current_price"] = None
    metric = compute_metrics(rows)[0]
    assert metric.price is not None
    assert metric.ma50 is None, "only 30 priced séances remain"


# --------------------------------------------------------------------------- #
# The consumers                                                                #
# --------------------------------------------------------------------------- #


def test_a_short_history_lowers_medium_horizon_coverage():
    """The whole point: an absent MA must reduce coverage, not silently satisfy a condition."""
    from moroccan_stock_intelligence.services.horizon_strategy import NewsContext, assess_medium

    shallow = assess_medium(only(10), NewsContext())
    deep = assess_medium(only(300), NewsContext())
    assert shallow.coverage < deep.coverage
    assert shallow.components["moyennes_mobiles"] is None
    assert deep.components["moyennes_mobiles"] is not None


def test_a_short_history_cannot_produce_a_confident_medium_score():
    from moroccan_stock_intelligence.services.horizon_strategy import (
        NewsContext,
        assess_medium,
        compute_confidence,
    )

    confidence, _ = compute_confidence(assess_medium(only(10), NewsContext()), history_days=10)
    assert confidence <= 50
