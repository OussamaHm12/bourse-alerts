from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import (
    Alert,
    CompanyProfile,
    Fundamental,
    MacroIndicator,
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


# --------------------------------------------------------------------------- #
# Phase 1b feeds: fundamentals, company profiles, macro indicators.            #
#                                                                              #
# Loaders return plain dicts (not ORM rows) so the research read-model stays   #
# decoupled from the ORM, the same way NewsView does. A value that was not     #
# published stays None here and is reported as missing by the owning analyst — #
# it is never coerced to 0.0.                                                  #
# --------------------------------------------------------------------------- #

_FUNDAMENTAL_FIELDS = ("eps", "roe_pct", "payout_pct", "dividend_yield_pct", "per", "pbr")


def upsert_fundamental(
    session: Session,
    stock_id: int,
    fiscal_year: int,
    source: str,
    values: dict[str, float | None],
    source_url: str | None = None,
    raw_payload: str | None = None,
) -> Fundamental:
    """Insert or refresh one (stock, fiscal_year, source) ratio row."""
    row = session.scalar(
        select(Fundamental).where(
            Fundamental.stock_id == stock_id,
            Fundamental.fiscal_year == fiscal_year,
            Fundamental.source == source,
        )
    )
    if row is None:
        row = Fundamental(stock_id=stock_id, fiscal_year=fiscal_year, source=source)
        session.add(row)
    for field in _FUNDAMENTAL_FIELDS:
        setattr(row, field, values.get(field))
    row.source_url = source_url
    row.raw_payload = raw_payload
    row.collected_at = datetime.now(UTC)
    return row


def upsert_company_profile(session: Session, stock_id: int, fields: dict) -> CompanyProfile:
    """Insert or refresh the single profile row for a stock."""
    row = session.scalar(select(CompanyProfile).where(CompanyProfile.stock_id == stock_id))
    if row is None:
        row = CompanyProfile(stock_id=stock_id)
        session.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    return row


def store_macro_observation(
    session: Session,
    indicator: str,
    as_of: datetime,
    value: float,
    unit: str | None,
    source: str,
    source_url: str | None = None,
) -> MacroIndicator | None:
    """Idempotent insert: an unchanged re-collection adds nothing."""
    existing = session.scalar(
        select(MacroIndicator).where(
            MacroIndicator.indicator == indicator,
            MacroIndicator.as_of == as_of,
            MacroIndicator.source == source,
        )
    )
    if existing is not None:
        return None
    row = MacroIndicator(
        indicator=indicator,
        as_of=as_of,
        value=value,
        unit=unit,
        source=source,
        source_url=source_url,
    )
    session.add(row)
    return row


def load_fundamentals(session: Session) -> dict[str, list[dict]]:
    """symbol -> ratio rows, newest fiscal year first (all sources kept, so the
    caller can prefer an official value over a derived one)."""
    rows = session.execute(
        select(Fundamental, Stock.symbol)
        .join(Stock, Fundamental.stock_id == Stock.id)
        .order_by(Fundamental.fiscal_year.desc())
    ).all()
    grouped: dict[str, list[dict]] = {}
    for row, symbol in rows:
        grouped.setdefault(str(symbol), []).append(
            {
                "fiscal_year": row.fiscal_year,
                "source": row.source,
                "source_url": row.source_url,
                **{field: getattr(row, field) for field in _FUNDAMENTAL_FIELDS},
            }
        )
    return grouped


def load_company_profiles(session: Session) -> dict[str, dict]:
    """symbol -> profile fields."""
    rows = session.execute(
        select(CompanyProfile, Stock.symbol).join(Stock, CompanyProfile.stock_id == Stock.id)
    ).all()
    return {
        str(symbol): {
            "company_name": row.company_name,
            "description": row.description,
            "business_model": row.business_model,
            "siege_social": row.siege_social,
            "commissaire_aux_comptes": row.commissaire_aux_comptes,
            "date_constitution": row.date_constitution,
            "date_introduction": row.date_introduction,
            "duree_exercice_social": row.duree_exercice_social,
            "ownership_json": row.ownership_json,
            "management_json": row.management_json,
            "source": row.source,
            "source_url": row.source_url,
            "updated_at": row.updated_at,
        }
        for row, symbol in rows
    }


def load_latest_macro(session: Session) -> dict[str, dict]:
    """indicator -> most recent observation."""
    newest = (
        select(MacroIndicator.indicator, func.max(MacroIndicator.as_of).label("as_of"))
        .group_by(MacroIndicator.indicator)
        .subquery()
    )
    rows = session.scalars(
        select(MacroIndicator).join(
            newest,
            (MacroIndicator.indicator == newest.c.indicator)
            & (MacroIndicator.as_of == newest.c.as_of),
        )
    ).all()
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(
            row.indicator,
            {
                "as_of": row.as_of,
                "value": row.value,
                "unit": row.unit,
                "source": row.source,
                "source_url": row.source_url,
            },
        )
    return latest


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
