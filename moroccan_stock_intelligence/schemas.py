from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class StockSnapshot:
    symbol: str
    company_name: str
    sector: str | None
    current_price: float | None
    daily_variation: float | None
    volume: float | None
    traded_quantity: float | None
    market_cap: float | None
    observed_at: datetime
    source: str
    source_url: str | None = None
    high_day: float | None = None
    low_day: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    company_symbol: str | None = None
    event_type: str | None = None
    sentiment: str | None = None
    impact_score: float | None = None
