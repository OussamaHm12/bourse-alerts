from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import (
    Alert,
    News,
    Notification,
    Price,
    Signal,
    Stock,
)
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
    existing_price = session.scalar(
        select(Price).where(
            Price.stock_id == stock.id,
            Price.observed_at == snapshot.observed_at,
            Price.source == snapshot.source,
        )
    )
    if existing_price is not None:
        return

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


def save_notification(session: Session, kind: str, title: str, body: str) -> Notification:
    """Persist a delivered notification so the app can show a history of them."""
    notification = Notification(kind=kind, title=title, body=body)
    session.add(notification)
    return notification


def load_recent_notifications(session: Session, limit: int = 50) -> list[Notification]:
    return list(
        session.scalars(
            select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        ).all()
    )


def load_symbol_history(
    session: Session, symbol: str, limit: int = 180
) -> list[tuple[datetime, float]]:
    """Return the most recent (observed_at, price) points for one symbol, oldest first."""
    rows = session.execute(
        select(Price.observed_at, Price.current_price)
        .join(Stock, Price.stock_id == Stock.id)
        .where(Stock.symbol == symbol.upper(), Price.current_price.is_not(None))
        .order_by(Price.observed_at.desc())
        .limit(limit)
    ).all()
    points = [(row[0], float(row[1])) for row in rows]
    points.reverse()
    return points


def load_history_depths(session: Session) -> dict[str, int]:
    """Days of collected price history per symbol (distinct calendar days).

    This is the honest measure of history depth: tail-based metrics such as
    ma200 return a value even with one snapshot, so the analysis layer uses
    this count to gate long-window conclusions and to compute confidence.
    """
    rows = session.execute(
        select(Stock.symbol, func.count(func.distinct(func.date(Price.observed_at))))
        .join(Price, Price.stock_id == Stock.id)
        .where(Price.current_price.is_not(None))
        .group_by(Stock.symbol)
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def load_recent_news(
    session: Session, limit: int = 30, symbol: str | None = None
) -> list[tuple[News, str | None]]:
    """Return recent news joined to their stock symbol (None if unlinked)."""
    query = (
        select(News, Stock.symbol)
        .join(Stock, News.stock_id == Stock.id, isouter=True)
        .order_by(News.collected_at.desc())
        .limit(limit)
    )
    if symbol is not None:
        query = (
            select(News, Stock.symbol)
            .join(Stock, News.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .order_by(News.collected_at.desc())
            .limit(limit)
        )
    return [(row[0], row[1]) for row in session.execute(query).all()]


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
