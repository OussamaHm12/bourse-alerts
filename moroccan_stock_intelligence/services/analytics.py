from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from moroccan_stock_intelligence.utils import pct_distance


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
    market_30d = pd.Series(
        [_momentum(group, 30) for _, group in daily.groupby("symbol")], dtype="float64"
    )
    market_return = market_30d.dropna().mean() if not market_30d.empty else None

    metrics: list[MetricSet] = []
    sector_momentum: dict[str, float] = {}
    pending: list[tuple[MetricSet, float | None]] = []

    for symbol, group in daily.groupby("symbol"):
        group = group.sort_values("observed_at").dropna(subset=["current_price"])
        if group.empty:
            continue
        latest = group.iloc[-1]
        prices = group["current_price"].astype(float)
        volumes = group["volume"].astype(float)
        returns = prices.pct_change()

        momentum_30d = _momentum(group, 30)
        sector = _none_if_nan(latest.get("sector"))
        if sector and momentum_30d is not None:
            sector_momentum.setdefault(sector, []).append(momentum_30d)  # type: ignore[union-attr]

        price = _float_or_none(latest.get("current_price"))
        support = _float_or_none(prices.tail(90).min())
        resistance = _float_or_none(prices.tail(90).max())
        high_52 = _float_or_none(prices.tail(365).max())
        low_52 = _float_or_none(prices.tail(365).min())
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
            ma20=_float_or_none(prices.tail(20).mean()),
            ma50=_float_or_none(prices.tail(50).mean()),
            ma200=_float_or_none(prices.tail(200).mean()),
            volatility_30d=_float_or_none(returns.tail(30).std() * math.sqrt(252) * 100),
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
    group = group.sort_values("observed_at")
    if group.empty:
        return None
    latest = group.iloc[-1]
    cutoff = latest["observed_at"] - pd.Timedelta(days=days)
    older = group[group["observed_at"] <= cutoff]
    if older.empty:
        return None
    old_price = _float_or_none(older.iloc[-1]["current_price"])
    new_price = _float_or_none(latest["current_price"])
    return pct_distance(new_price, old_price)


def _float_or_none(value) -> float | None:  # noqa: ANN001
    if value is None or pd.isna(value):
        return None
    return float(value)


def _none_if_nan(value) -> str | None:  # noqa: ANN001
    if value is None or pd.isna(value):
        return None
    return str(value)
