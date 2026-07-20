from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.utils import pct_distance

_NS_PER_DAY = 86_400_000_000_000

# --------------------------------------------------------------------------- #
# How much history a windowed statistic needs before it may claim its own name  #
# --------------------------------------------------------------------------- #
#
# `prices.tail(200).mean()` on a symbol with ten séances returns the mean of ten
# séances — and every consumer then reads it as a 200-day moving average. The
# audit (AUDIT_2026-07-18.md §6) rated this P1, and the reason is not that the
# number is imprecise: it is that this single field bypasses the coverage
# mechanism the rest of the engine is built on.
#
# Everywhere else, a metric that cannot be computed is None, which lowers the
# horizon's coverage, shrinks its score toward neutral and caps its confidence.
# A moving average that is silently always available tells that machinery it has
# data it does not have — so a 10-séance symbol reported a trend, a MA50 crossing
# and a market-wide breadth contribution, all with full confidence.
#
# So: a windowed statistic returns None unless it has enough observations. The
# thresholds below are minimums, not targets.
#
# `ma_min_coverage` (default 1.0 = strict) is the only tolerance, and it is
# deliberately opt-in: an operator who accepts a MA200 built from 180 séances can
# say so, and the fact appears in `evidence` on the technical analyst's
# statements. It can never turn 10 séances into a MA200 — MIN_ABSOLUTE below is
# a floor no coverage setting can go under.
MA_WINDOWS = {"ma20": 20, "ma50": 50, "ma200": 200}

# Annualised volatility from two returns is not a 30-day volatility, it is noise
# with a confident label. 20 returns is two thirds of the window: enough for the
# standard deviation to mean something, lenient enough to survive holidays.
VOLATILITY_MIN_OBSERVATIONS = 21  # 21 prices -> 20 returns

# Support/resistance is a *recent range*. It degrades gracefully with fewer
# points, but three points describe a line, not a range.
RANGE_MIN_OBSERVATIONS = 20

# A "52-week high" drawn from six weeks of data is a different statistic wearing
# the same label, and it feeds `structure_52s` in the long-horizon score.
WEEK52_MIN_OBSERVATIONS = 120

# No coverage setting may reduce a window below this fraction of its nominal
# length. Without it, `MA_MIN_COVERAGE=0.05` would resurrect the exact bug.
MIN_ABSOLUTE_COVERAGE = 0.75


def _required(window: int) -> int:
    """Observations a window needs, after applying the configured tolerance."""
    coverage = max(MIN_ABSOLUTE_COVERAGE, min(1.0, settings.ma_min_coverage))
    return max(2, int(round(window * coverage)))


def _windowed(series: pd.Series, window: int, reducer: str) -> float | None:
    """`series.tail(window).<reducer>()`, or None when the history is too short.

    One helper for every windowed statistic so a new one cannot be added without
    inheriting the guard.
    """
    if len(series) < _required(window):
        return None
    return _float_or_none(getattr(series.tail(window), reducer)())


@dataclass(frozen=True)
class MetricSet:
    stock_id: int
    symbol: str
    company_name: str
    sector: str | None
    price: float | None
    daily_variation: float | None
    volume: float | None
    momentum_1d: float | None
    momentum_5d: float | None
    momentum_30d: float | None
    momentum_90d: float | None
    ma20: float | None
    ma50: float | None
    ma200: float | None
    volatility_30d: float | None
    volume_anomaly: float | None
    relative_performance_30d: float | None
    drawdown_from_recent_high: float | None
    support: float | None
    resistance: float | None
    support_distance: float | None
    resistance_distance: float | None
    week52_high: float | None
    week52_low: float | None
    week52_high_proximity: float | None
    week52_low_proximity: float | None
    sector_strength: float | None


def compute_metrics(price_frame: pd.DataFrame) -> list[MetricSet]:
    if price_frame.empty:
        return []

    frame = price_frame.copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame = frame.dropna(subset=["current_price"]).sort_values(["symbol", "observed_at"])
    if frame.empty:
        return []

    daily_parts = []
    for symbol, group in frame.groupby("symbol"):
        resampled = (
            group.set_index("observed_at")
            .sort_index()
            .resample("1D")
            .last()
            .drop(columns=["symbol"], errors="ignore")
            .reset_index()
        )
        resampled["symbol"] = symbol
        daily_parts.append(resampled)
    daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()

    # One pass. `momentum_30d` used to be computed twice per symbol: once here for
    # the index proxy, once again in the loop below — and on DIFFERENT data, since
    # only the loop dropped the NaN rows `resample` emits for days with no séance.
    # So a symbol whose cutoff landed on a weekend contributed None to the market
    # return while reporting a real momentum for itself. Computing it once, on the
    # cleaned group, removes the duplicate work and that inconsistency with it.
    groups: list[tuple[str, pd.DataFrame]] = []
    for symbol, group in daily.groupby("symbol"):
        group = group.sort_values("observed_at").dropna(subset=["current_price"])
        if not group.empty:
            groups.append((str(symbol), group))

    momentum_30d_by_symbol = {symbol: _momentum(group, 30) for symbol, group in groups}
    market_30d = pd.Series(list(momentum_30d_by_symbol.values()), dtype="float64")
    market_return = market_30d.dropna().mean() if not market_30d.empty else None

    metrics: list[MetricSet] = []
    sector_momentum: dict[str, float] = {}
    pending: list[tuple[MetricSet, float | None]] = []

    for symbol, group in groups:
        latest = group.iloc[-1]
        prices = group["current_price"].astype(float)
        volumes = group["volume"].astype(float)
        returns = prices.pct_change()

        momentum_30d = momentum_30d_by_symbol[symbol]
        sector = _none_if_nan(latest.get("sector"))
        if sector and momentum_30d is not None:
            sector_momentum.setdefault(sector, []).append(momentum_30d)  # type: ignore[union-attr]

        price = _float_or_none(latest.get("current_price"))
        # Each of these is None rather than a short-history impostor — see the
        # threshold block at the top of the module for why that matters.
        has_range = len(prices) >= RANGE_MIN_OBSERVATIONS
        support = _float_or_none(prices.tail(90).min()) if has_range else None
        resistance = _float_or_none(prices.tail(90).max()) if has_range else None
        has_year = len(prices) >= WEEK52_MIN_OBSERVATIONS
        high_52 = _float_or_none(prices.tail(365).max()) if has_year else None
        low_52 = _float_or_none(prices.tail(365).min()) if has_year else None
        volume_avg = volumes.tail(20).replace(0, math.nan).mean()
        latest_volume = _float_or_none(latest.get("volume"))

        metric = MetricSet(
            stock_id=int(latest["stock_id"]),
            symbol=str(symbol),
            company_name=str(latest["company_name"]),
            sector=sector,
            price=price,
            daily_variation=_float_or_none(latest.get("daily_variation")),
            volume=latest_volume,
            momentum_1d=_momentum(group, 1),
            momentum_5d=_momentum(group, 5),
            momentum_30d=momentum_30d,
            momentum_90d=_momentum(group, 90),
            ma20=_windowed(prices, MA_WINDOWS["ma20"], "mean"),
            ma50=_windowed(prices, MA_WINDOWS["ma50"], "mean"),
            ma200=_windowed(prices, MA_WINDOWS["ma200"], "mean"),
            volatility_30d=(
                _float_or_none(returns.tail(30).std() * math.sqrt(252) * 100)
                if len(prices) >= VOLATILITY_MIN_OBSERVATIONS
                else None
            ),
            volume_anomaly=_float_or_none(latest_volume / volume_avg)
            if latest_volume is not None and volume_avg and not math.isnan(volume_avg)
            else None,
            relative_performance_30d=(momentum_30d - market_return)
            if momentum_30d is not None and market_return is not None
            else None,
            drawdown_from_recent_high=pct_distance(price, resistance),
            support=support,
            resistance=resistance,
            support_distance=pct_distance(price, support),
            resistance_distance=pct_distance(price, resistance),
            week52_high=high_52,
            week52_low=low_52,
            week52_high_proximity=pct_distance(price, high_52),
            week52_low_proximity=pct_distance(price, low_52),
            sector_strength=None,
        )
        pending.append((metric, momentum_30d))

    sector_strength = {
        sector: float(pd.Series(values).dropna().mean())
        for sector, values in sector_momentum.items()
        if values
    }
    for metric, _ in pending:
        metrics.append(
            MetricSet(
                **{
                    **metric.__dict__,
                    "sector_strength": sector_strength.get(metric.sector) if metric.sector else None,
                }
            )
        )
    return metrics


def _momentum(group: pd.DataFrame, days: int) -> float | None:
    """Percentage move over `days`, against the last séance at or before the cutoff.

    Every caller already holds a group sorted by `observed_at`, so this does NOT
    re-sort: it was called ~5 times per symbol and the redundant sorts made it 40%
    of compute_metrics at production volume. `searchsorted` replaces the boolean
    mask for the same reason — the mask allocated a full copy per call to find one
    row.
    """
    if group.empty:
        return None
    # Nanoseconds since epoch, as int64. `observed_at` is tz-aware, so .to_numpy()
    # yields an OBJECT array of Timestamps rather than datetime64 — and subtracting
    # a timedelta from those went through NumPy's deprecated generic-unit path,
    # which is documented to become an error. Integers are unambiguous and faster.
    dates_ns = group["observed_at"].astype("int64").to_numpy()
    prices = group["current_price"].to_numpy()
    cutoff_ns = dates_ns[-1] - days * _NS_PER_DAY
    # Rightmost index whose date is <= cutoff; 0 means no séance is old enough.
    index = int(np.searchsorted(dates_ns, cutoff_ns, side="right"))
    if index == 0:
        return None
    return pct_distance(_float_or_none(prices[-1]), _float_or_none(prices[index - 1]))


def _float_or_none(value) -> float | None:  # noqa: ANN001
    if value is None or pd.isna(value):
        return None
    return float(value)


def _none_if_nan(value) -> str | None:  # noqa: ANN001
    if value is None or pd.isna(value):
        return None
    return str(value)
