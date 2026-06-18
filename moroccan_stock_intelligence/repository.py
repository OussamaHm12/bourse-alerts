from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import Alert, News, Price, Signal, Stock
from moroccan_stock_intelligence.schemas import NewsItem, StockSnapshot


def upsert_stock(session: Session, snapshot: StockSnapshot) -> Stock:
    stock = session.scalar(select(Stock).where(Stock.symbol == snapshot.symbol))
    if stock is None:
        stock = Stock(
            symbol=snapshot.symbol,
            company_name=snapshot.company_name,
            sector=snapshot.sector,
            source=snapshot.source,
            source_url=snapshot.source_url,
        )
        session.add(stock)
        session.flush()
        return stock

    stock.company_name = snapshot.company_name or stock.company_name
    stock.sector = snapshot.sector or stock.sector
    stock.source = snapshot.source
    stock.source_url = snapshot.source_url
    return stock


def store_snapshot(session: Session, snapshot: StockSnapshot) -> None:
    stock = upsert_stock(session, snapshot)
    price = Price(
        stock_id=stock.id,
        observed_at=snapshot.observed_at,
        current_price=snapshot.current_price,
        daily_variation=snapshot.daily_variation,
        volume=snapshot.volume,
        traded_quantity=snapshot.traded_quantity,
        market_cap=snapshot.market_cap,
        high_day=snapshot.high_day,
        low_day=snapshot.low_day,
        source=snapshot.source,
        source_url=snapshot.source_url,
        raw_payload=json.dumps(snapshot.raw, ensure_ascii=False),
    )
    session.add(price)


def store_signal(
    session: Session,
    stock_id: int,
    signal_type: str,
    explanation: str,
    score: float | None = None,
    severity: str = "info",
    metrics: dict | None = None,
) -> Signal:
    signal = Signal(
        stock_id=stock_id,
        generated_at=datetime.now(UTC),
        signal_type=signal_type,
        score=score,
        severity=severity,
        explanation=explanation,
        metrics_json=json.dumps(metrics or {}, ensure_ascii=False),
    )
    session.add(signal)
    return signal


def create_alert_once(
    session: Session,
    stock_id: int,
    event_key: str,
    alert_type: str,
    message: str,
) -> Alert | None:
    existing = session.scalar(select(Alert).where(Alert.stock_id == stock_id, Alert.event_key == event_key))
    if existing:
        return None
    alert = Alert(stock_id=stock_id, event_key=event_key, alert_type=alert_type, message=message, sent=0)
    session.add(alert)
    return alert


def store_news(session: Session, item: NewsItem, stock_id: int | None) -> None:
    existing = session.scalar(select(News).where(News.url == item.url))
    if existing:
        return
    session.add(
        News(
            stock_id=stock_id,
            published_at=item.published_at,
            source=item.source,
            title=item.title,
            url=item.url,
            event_type=item.event_type,
            sentiment=item.sentiment,
            impact_score=item.impact_score,
        )
    )


def load_price_frame(session: Session) -> pd.DataFrame:
    rows = session.execute(
        select(
            Stock.id.label("stock_id"),
            Stock.symbol,
            Stock.company_name,
            Stock.sector,
            Price.observed_at,
            Price.current_price,
            Price.daily_variation,
            Price.volume,
            Price.traded_quantity,
            Price.market_cap,
            Price.high_day,
            Price.low_day,
            Price.source,
        ).join(Price, Price.stock_id == Stock.id)
    ).mappings()
    return pd.DataFrame(rows)
