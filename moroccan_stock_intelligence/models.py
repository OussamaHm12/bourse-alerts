from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list[Price]] = relationship(back_populates="stock")


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("stock_id", "observed_at", "source", name="uq_price_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_variation: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    traded_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock: Mapped[Stock] = relationship(back_populates="prices")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="info")
    explanation: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("stock_id", "event_key", name="uq_alert_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_key: Mapped[str] = mapped_column(String(255), index=True)
    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    sent: Mapped[int] = mapped_column(Integer, default=0)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="digest")
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)


class News(Base):
    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("url", name="uq_news_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id"), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)


# --------------------------------------------------------------------------- #
# Phase 1b feeds. All three are NEW tables, so `create_all` creates them        #
# idempotently and no existing table is ever ALTERed.                           #
#                                                                               #
# Every ratio/value column is nullable on purpose: the sources publish a literal #
# "-" for a missing cell, which must land as NULL — never 0.0. A field we cannot #
# collect is absent from the row, and the owning analyst reports it as missing.  #
# --------------------------------------------------------------------------- #

class Fundamental(Base):
    """The six ratios officially published on the Casablanca Bourse issuer page.

    One row per (stock, fiscal year, source). The page exposes a
    `Ratio | 2025 | 2024 | 2023` table, so `fiscal_year` — not a collection
    timestamp — is the natural key.

    Deliberately narrow (validated 2026-07-09): revenue, net income, margins,
    ROA, debt/equity and book value are NOT published in machine-readable form,
    so they get no permanently-NULL columns here.

    `source` is "Casablanca Bourse" for published values, or "derived" for a PER
    computed as price / BPA when the published cell was "-" (labelled inference,
    never fact, and never overwriting an official value).
    """

    __tablename__ = "fundamentals"
    __table_args__ = (
        UniqueConstraint("stock_id", "fiscal_year", "source", name="uq_fundamental_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)  # BPA (MAD)
    roe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # ROE (en %)
    payout_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # Payout (en %)
    dividend_yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    per: Mapped[float | None] = mapped_column(Float, nullable=True)
    pbr: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CompanyProfile(Base):
    """Issuer identity from the same page that serves the fundamentals.

    `description` holds "Objet social" (the company's stated business purpose).
    There is no separate business-model field published, so `business_model`
    stays NULL rather than being synthesised. `management_json` stays NULL until
    the `Dirigeants de l'entreprise` table layout is confirmed.
    """

    __tablename__ = "company_profiles"
    __table_args__ = (UniqueConstraint("stock_id", name="uq_company_profile_stock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    emetteur_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emetteur_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # Objet social
    business_model: Mapped[str | None] = mapped_column(Text, nullable=True)  # not published
    siege_social: Mapped[str | None] = mapped_column(Text, nullable=True)
    commissaire_aux_comptes: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_constitution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date_introduction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duree_exercice_social: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ownership_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    management_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MacroIndicator(Base):
    """One observation of one Bank Al-Maghrib series.

    Long/narrow rather than wide, so a new indicator never needs a schema change.
    Known indicators: policy_rate, interbank_money_market, inflation_rate,
    inflation_underlying_rate, mad_eur, mad_usd. Oil and phosphate are NOT
    published by BAM and are therefore simply absent (never zero).
    """

    __tablename__ = "macro_indicators"
    __table_args__ = (
        UniqueConstraint("indicator", "as_of", "source", name="uq_macro_observation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    indicator: Mapped[str] = mapped_column(String(48), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
