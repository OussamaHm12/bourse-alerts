from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import (
    CURRENT_SEMANTICS_VERSION,
    Alert,
    AnalysisReport,
    AnalystPerformance,
    CompanyKnowledge,
    CompanyProfile,
    Favorite,
    Fundamental,
    MacroIndicator,
    News,
    Notification,
    PredictionHistory,
    Price,
    Stock,
    ThesisChange,
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


# --------------------------------------------------------------------------- #
# Favorites — the owner's explicit watchlist.                                   #
#                                                                               #
# Separate from the portfolio by design: a favorite carries no quantity and no  #
# buy price, so it has no P/L. It only buys the symbol attention (urgent crash  #
# alert, priority on the capped thesis pushes, its own digest section).         #
# --------------------------------------------------------------------------- #

def add_favorite(session: Session, symbol: str, note: str | None = None) -> Favorite | None:
    """Favorite a symbol. Idempotent: re-favoriting updates the note, never duplicates.

    Returns None when the symbol is unknown — we refuse to watch a stock the
    collector has never seen, rather than storing a dangling row.
    """
    stock = session.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
    if stock is None:
        return None

    existing = session.scalar(select(Favorite).where(Favorite.stock_id == stock.id))
    if existing is not None:
        if note is not None:
            existing.note = note
        return existing

    favorite = Favorite(stock_id=stock.id, symbol=stock.symbol, note=note)
    session.add(favorite)
    session.flush()
    return favorite


def remove_favorite(session: Session, symbol: str) -> bool:
    """Un-favorite a symbol. Returns True if a row was actually removed."""
    favorite = session.scalar(select(Favorite).where(Favorite.symbol == symbol.upper()))
    if favorite is None:
        return False
    session.delete(favorite)
    return True


def load_favorite_symbols(session: Session) -> list[str]:
    """The watched symbols, oldest favorite first (a stable order for the digest)."""
    return list(
        session.scalars(select(Favorite.symbol).order_by(Favorite.created_at, Favorite.id)).all()
    )


def load_favorites(session: Session) -> list[dict]:
    """Favorites joined with their stock, for the API listing."""
    rows = session.execute(
        select(Favorite, Stock)
        .join(Stock, Stock.id == Favorite.stock_id)
        .order_by(Favorite.created_at, Favorite.id)
    ).all()
    return [
        {
            "symbol": favorite.symbol,
            "company_name": stock.company_name,
            "sector": stock.sector,
            "note": favorite.note,
            "created_at": favorite.created_at.isoformat() if favorite.created_at else None,
        }
        for favorite, stock in rows
    ]


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


# --------------------------------------------------------------------------- #
# Research database: reports, predictions, performance, knowledge, thesis.      #
# --------------------------------------------------------------------------- #

def save_analysis_report(
    session: Session,
    stock_id: int,
    symbol: str,
    horizon_focus: str,
    engine_version: str,
    thesis_hash: str,
    report_json: str,
    verdicts: dict[str, dict],
    risk_score: float | None,
    price_at_report: float | None,
    narrative: str | None = None,
) -> AnalysisReport:
    """Persist a generated report. Always inserts: reports are an append-only log."""
    row = AnalysisReport(
        stock_id=stock_id,
        symbol=symbol.upper(),
        generated_at=datetime.now(UTC),
        horizon_focus=horizon_focus,
        engine_version=engine_version,
        thesis_hash=thesis_hash,
        recommendation_short=verdicts.get("short", {}).get("recommendation"),
        recommendation_medium=verdicts.get("medium", {}).get("recommendation"),
        recommendation_long=verdicts.get("long", {}).get("recommendation"),
        confidence_short=verdicts.get("short", {}).get("confidence"),
        confidence_medium=verdicts.get("medium", {}).get("confidence"),
        confidence_long=verdicts.get("long", {}).get("confidence"),
        risk_score=risk_score,
        price_at_report=price_at_report,
        report_json=report_json,
        narrative=narrative,
    )
    session.add(row)
    session.flush()  # id needed for predictions / thesis changes
    return row


def load_cached_report(
    session: Session,
    symbol: str,
    horizon: str,
    engine_version: str,
    max_age_seconds: int,
) -> AnalysisReport | None:
    """Newest report for (symbol, horizon) that is fresh enough AND was produced by
    the running engine. A version bump therefore invalidates the cache implicitly —
    we never serve a report whose logic no longer exists.
    """
    row = session.scalars(
        select(AnalysisReport)
        .where(
            AnalysisReport.symbol == symbol.upper(),
            AnalysisReport.horizon_focus == horizon,
            AnalysisReport.engine_version == engine_version,
        )
        .order_by(AnalysisReport.generated_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    generated = row.generated_at
    if generated is None:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    if (datetime.now(UTC) - generated).total_seconds() > max_age_seconds:
        return None
    return row


def load_report_history(session: Session, symbol: str, limit: int = 50) -> list[AnalysisReport]:
    return list(
        session.scalars(
            select(AnalysisReport)
            .where(AnalysisReport.symbol == symbol.upper())
            .order_by(AnalysisReport.generated_at.desc())
            .limit(limit)
        ).all()
    )


def load_last_report_before(
    session: Session, symbol: str, horizon: str, before_id: int
) -> AnalysisReport | None:
    """The previous report for this symbol+horizon: the baseline a thesis change is
    measured against."""
    return session.scalars(
        select(AnalysisReport)
        .where(
            AnalysisReport.symbol == symbol.upper(),
            AnalysisReport.horizon_focus == horizon,
            AnalysisReport.id < before_id,
        )
        .order_by(AnalysisReport.id.desc())
        .limit(1)
    ).first()


def save_prediction(
    session: Session,
    report_id: int,
    stock_id: int,
    symbol: str,
    analyst: str,
    horizon: str,
    scenario: str,
    generated_at: datetime,
    evaluate_at: datetime,
    engine_version: str,
    predicted_direction: str | None,
    predicted_probability: float,
    stated_confidence: float | None,
    price_at_prediction: float | None,
    semantics_version: int = 1,
    claim_kind: str = "direction",
    signal_strength: float | None = None,
    data_confidence: float | None = None,
) -> PredictionHistory | None:
    """Record one falsifiable claim. None if this exact claim already exists."""
    existing = session.scalar(
        select(PredictionHistory).where(
            PredictionHistory.report_id == report_id,
            PredictionHistory.analyst == analyst,
            PredictionHistory.horizon == horizon,
            PredictionHistory.scenario == scenario,
        )
    )
    if existing is not None:
        return None
    row = PredictionHistory(
        report_id=report_id,
        stock_id=stock_id,
        symbol=symbol.upper(),
        analyst=analyst,
        horizon=horizon,
        scenario=scenario,
        generated_at=generated_at,
        evaluate_at=evaluate_at,
        engine_version=engine_version,
        predicted_direction=predicted_direction,
        predicted_probability=predicted_probability,
        stated_confidence=stated_confidence,
        price_at_prediction=price_at_prediction,
        semantics_version=semantics_version,
        claim_kind=claim_kind,
        signal_strength=signal_strength,
        data_confidence=data_confidence,
    )
    session.add(row)
    return row


def load_due_predictions(session: Session, now: datetime | None = None) -> list[PredictionHistory]:
    """Predictions whose evaluation date has passed and which carry an anchor price.
    Un-evaluated rows stay NULL rather than being scored as wrong."""
    moment = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(PredictionHistory).where(
                PredictionHistory.evaluated_at.is_(None),
                PredictionHistory.evaluate_at <= moment,
                PredictionHistory.price_at_prediction.is_not(None),
            )
        ).all()
    )


def load_evaluated_predictions(
    session: Session, analyst: str | None = None, horizon: str | None = None
) -> list[PredictionHistory]:
    query = select(PredictionHistory).where(
        PredictionHistory.evaluated_at.is_not(None),
        # v1 recorded WATCH/HOLD as bullish bets and derived probabilities from a
        # coverage metric. Those rows are real observations of what the engine
        # said, so they are kept — but mixing two semantics in one statistic would
        # produce a number that means neither.
        PredictionHistory.semantics_version == CURRENT_SEMANTICS_VERSION,
        # RISKY asserts volatility and TAKE_PROFIT is an instruction, not a
        # forecast; neither is falsified by a price going the "wrong" way.
        PredictionHistory.claim_kind == "direction",
        PredictionHistory.predicted_direction.is_not(None),
    )
    if analyst:
        query = query.where(PredictionHistory.analyst == analyst)
    if horizon:
        query = query.where(PredictionHistory.horizon == horizon)
    return list(session.scalars(query).all())


def upsert_analyst_performance(
    session: Session, analyst: str, horizon: str, stats: dict
) -> AnalystPerformance:
    row = session.scalar(
        select(AnalystPerformance).where(
            AnalystPerformance.analyst == analyst, AnalystPerformance.horizon == horizon
        )
    )
    if row is None:
        row = AnalystPerformance(analyst=analyst, horizon=horizon)
        session.add(row)
    for key, value in stats.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    return row


def load_analyst_performance(session: Session) -> dict[tuple[str, str], dict]:
    """(analyst, horizon) -> stats. The CIO uses this to weight proven analysts."""
    return {
        (row.analyst, row.horizon): {
            "sample_size": row.sample_size,
            "hit_rate": row.hit_rate,
            "brier_score": row.brier_score,
            "calibration_error": row.calibration_error,
            "precision": row.precision,
            "recall": row.recall,
            "confidence_multiplier": row.confidence_multiplier,
        }
        for row in session.scalars(select(AnalystPerformance)).all()
    }


def upsert_knowledge_fact(
    session: Session,
    stock_id: int,
    category: str,
    key: str,
    value: str,
    fact_hash: str,
    kind: str = "fact",
    source: str | None = None,
    source_url: str | None = None,
    observed_at: datetime | None = None,
) -> tuple[CompanyKnowledge, bool]:
    """Insert a fact, or refresh `last_seen` if we already know it.

    Returns (row, created). De-duplication is the point: the same fact re-observed
    every week must not accumulate rows.
    """
    row = session.scalar(
        select(CompanyKnowledge).where(
            CompanyKnowledge.stock_id == stock_id, CompanyKnowledge.fact_hash == fact_hash
        )
    )
    if row is not None:
        row.value = value
        row.last_seen = datetime.now(UTC)
        return row, False
    row = CompanyKnowledge(
        stock_id=stock_id,
        category=category,
        key=key,
        value=value,
        kind=kind,
        fact_hash=fact_hash,
        source=source,
        source_url=source_url,
        observed_at=observed_at,
    )
    session.add(row)
    # Flush so a second observation of the same fact within one transaction finds
    # this row instead of racing it into a UNIQUE violation.
    session.flush()
    return row, True


def load_company_knowledge(session: Session, symbol: str) -> dict[str, list[dict]]:
    """category -> facts, most recently seen first."""
    rows = (
        session.execute(
            select(CompanyKnowledge)
            .join(Stock, CompanyKnowledge.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .order_by(CompanyKnowledge.category, CompanyKnowledge.last_seen.desc())
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.category, []).append(
            {
                "key": row.key,
                "value": row.value,
                "kind": row.kind,
                "source": row.source,
                "source_url": row.source_url,
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            }
        )
    return grouped


def record_thesis_change(session: Session, **fields) -> ThesisChange:
    row = ThesisChange(**fields)
    session.add(row)
    return row


def load_thesis_changes(
    session: Session, symbol: str, horizon: str | None = None, limit: int = 30
) -> list[ThesisChange]:
    query = select(ThesisChange).where(ThesisChange.symbol == symbol.upper())
    if horizon:
        query = query.where(ThesisChange.horizon == horizon)
    return list(
        session.scalars(query.order_by(ThesisChange.changed_at.desc()).limit(limit)).all()
    )


def latest_price_observed_at(session: Session) -> datetime | None:
    """When the market was last collected. None on an empty database.

    This is what tells the app whether the data it is about to show is stale enough
    to be worth re-scraping.
    """
    return session.scalar(select(func.max(Price.observed_at)))


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
