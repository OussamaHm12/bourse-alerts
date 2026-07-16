"""Phase 4 — Knowledge base.

Companies accumulate knowledge instead of being re-derived from scratch every run.
Facts are harvested from the data we already collect (issuer profile, fundamentals,
official notices) and stored ONCE: `fact_hash` = sha256(category|key|value), so
re-observing the same fact refreshes `last_seen` rather than inserting a duplicate.

Only what a source actually published becomes a fact. Nothing is inferred into the
knowledge base — the `kind` column carries the fact/inference/opinion label so a
consumer can never mistake one for the other.

Categories: identity, ownership, management, governance, fundamentals,
dividend_history, capital_actions, sector, events.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import CompanyProfile, Fundamental, News, Stock
from moroccan_stock_intelligence.repository import load_company_knowledge, upsert_knowledge_fact
from moroccan_stock_intelligence.services.news_classifier import event_family

LOG = logging.getLogger(__name__)

VERSION = "1.0"

CATEGORIES = (
    "identity",
    "ownership",
    "management",
    "governance",
    "fundamentals",
    "dividend_history",
    "capital_actions",
    "events",
)


def fact_hash(category: str, key: str, value: str) -> str:
    return hashlib.sha256(f"{category}|{key}|{value}".encode()).hexdigest()


def _remember(
    session: Session,
    stock_id: int,
    category: str,
    key: str,
    value: str | None,
    kind: str = "fact",
    source: str | None = None,
    source_url: str | None = None,
    observed_at: datetime | None = None,
) -> bool:
    """Store one fact if it is real. Returns True when newly learned."""
    if value is None or not str(value).strip():
        return False
    text = str(value).strip()
    _, created = upsert_knowledge_fact(
        session,
        stock_id=stock_id,
        category=category,
        key=key,
        value=text,
        fact_hash=fact_hash(category, key, text),
        kind=kind,
        source=source,
        source_url=source_url,
        observed_at=observed_at,
    )
    return created


def harvest_company(session: Session, stock: Stock) -> int:
    """Turn everything collected about one company into de-duplicated knowledge."""
    learned = 0

    # --- Identity, ownership, management, governance (issuer profile) ---
    profile = session.scalar(
        select(CompanyProfile).where(CompanyProfile.stock_id == stock.id)
    )
    if profile is not None:
        src, url = profile.source, profile.source_url
        learned += _remember(session, stock.id, "identity", "Dénomination",
                             profile.company_name, source=src, source_url=url)
        learned += _remember(session, stock.id, "identity", "Objet social",
                             profile.description, source=src, source_url=url)
        learned += _remember(session, stock.id, "identity", "Siège social",
                             profile.siege_social, source=src, source_url=url)
        learned += _remember(session, stock.id, "identity", "Date de constitution",
                             profile.date_constitution, source=src, source_url=url)
        learned += _remember(session, stock.id, "identity", "Date d'introduction en bourse",
                             profile.date_introduction, source=src, source_url=url)
        learned += _remember(session, stock.id, "governance", "Commissaire aux comptes",
                             profile.commissaire_aux_comptes, source=src, source_url=url)
        learned += _remember(session, stock.id, "identity", "Secteur",
                             stock.sector, source=src, source_url=url)

        for holder in _json_list(profile.ownership_json):
            name, pct = holder.get("holder"), holder.get("pct")
            if name and pct is not None:
                learned += _remember(session, stock.id, "ownership", name, f"{pct:.2f}%",
                                     source=src, source_url=url)
        for person in _json_list(profile.management_json):
            role, name = person.get("role"), person.get("name")
            if role and name:
                learned += _remember(session, stock.id, "management", role, name,
                                     source=src, source_url=url)

    # --- Fundamentals + dividend history (one entry per fiscal year) ---
    rows = session.scalars(
        select(Fundamental)
        .where(Fundamental.stock_id == stock.id)
        .order_by(Fundamental.fiscal_year.desc())
    ).all()
    for row in rows:
        if row.source == "derived":
            continue  # derived values are inference, not knowledge
        year = row.fiscal_year
        for label, value, unit in (
            ("BPA", row.eps, " MAD"), ("ROE", row.roe_pct, "%"), ("PER", row.per, ""),
            ("PBR", row.pbr, ""), ("Payout", row.payout_pct, "%"),
        ):
            if value is not None:
                learned += _remember(session, stock.id, "fundamentals", f"{label} {year}",
                                     f"{value:g}{unit}", source=row.source,
                                     source_url=row.source_url)
        if row.dividend_yield_pct is not None:
            learned += _remember(
                session, stock.id, "dividend_history", f"Rendement du dividende {year}",
                f"{row.dividend_yield_pct:g}%", source=row.source, source_url=row.source_url,
            )

    # --- Corporate events (official notices) ---
    news_rows = session.scalars(
        select(News).where(News.stock_id == stock.id).order_by(News.collected_at.desc()).limit(60)
    ).all()
    for item in news_rows:
        category = {
            "capital_action": "capital_actions",
            "dividend": "dividend_history",
        }.get(event_family(item.event_type), "events")
        when = item.published_at or item.collected_at
        key = f"{when.date().isoformat()} — {item.event_type or 'avis'}" if when else (item.event_type or "avis")
        learned += _remember(session, stock.id, category, key, item.title,
                             source=item.source, source_url=item.url, observed_at=when)

    if learned:
        session.commit()
    LOG.info("knowledge_harvested symbol=%s new_facts=%s", stock.symbol, learned)
    return learned


def harvest_all(session: Session) -> int:
    """Refresh the knowledge base for every tracked company."""
    total = 0
    for stock in session.scalars(select(Stock)).all():
        try:
            total += harvest_company(session, stock)
        except Exception:  # noqa: BLE001 - one company must not sink the sweep
            LOG.exception("knowledge_harvest_failed symbol=%s", stock.symbol)
            session.rollback()
    LOG.info("knowledge_harvest_done new_facts=%s", total)
    return total


def _json_list(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def knowledge_payload(session: Session, symbol: str) -> dict:
    knowledge = load_company_knowledge(session, symbol)
    counts = {category: len(facts) for category, facts in knowledge.items()}
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "symbol": symbol.upper(),
        "categories": counts,
        "total_facts": sum(counts.values()),
        "knowledge": knowledge,
        "note": (
            "Base de connaissances accumulée à partir des sources officielles collectées. "
            "Rien n'est inventé : chaque fait porte sa source."
            if knowledge
            else "Aucune connaissance accumulée pour l'instant (collecte des émetteurs non encore effectuée)."
        ),
    }
