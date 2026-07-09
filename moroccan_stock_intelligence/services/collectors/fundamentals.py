"""Fundamentals persister (thin): one row per (stock, fiscal year, source).

Official rows carry the six published ratios verbatim; a "-" cell is already None
by the time it gets here (never 0.0).

Derived PER — permitted only when ALL of these hold:
  * the published PER cell for that fiscal year is missing,
  * BPA (eps) is present and positive,
  * a current price is available.
It is written as a SEPARATE row with source="derived" so it can never overwrite an
official value, and `fundamental.py` labels it inference rather than fact.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import upsert_fundamental
from moroccan_stock_intelligence.services.collectors import DERIVED_SOURCE, OFFICIAL_SOURCE
from moroccan_stock_intelligence.services.collectors.issuer_page import IssuerPage

LOG = logging.getLogger(__name__)


def persist_fundamentals(
    session: Session,
    stock_id: int,
    page: IssuerPage,
    current_price: float | None = None,
) -> tuple[int, int]:
    """Store every published fiscal year. Returns (official_rows, derived_rows)."""
    if not page.ratios:
        return 0, 0

    official = 0
    for year in page.ratios:
        upsert_fundamental(
            session,
            stock_id=stock_id,
            fiscal_year=year.fiscal_year,
            source=OFFICIAL_SOURCE,
            values=year.values,
            source_url=page.emetteur_url,
            raw_payload=json.dumps(year.values, ensure_ascii=False),
        )
        official += 1

    derived = 0
    latest = max(page.ratios, key=lambda year: year.fiscal_year)
    eps = latest.values.get("eps")
    if latest.values.get("per") is None and eps and eps > 0 and current_price:
        per = round(current_price / eps, 2)
        upsert_fundamental(
            session,
            stock_id=stock_id,
            fiscal_year=latest.fiscal_year,
            source=DERIVED_SOURCE,
            values={"per": per},  # every other ratio stays None on a derived row
            source_url=page.emetteur_url,
            raw_payload=json.dumps(
                {"per": per, "formula": "current_price / eps", "price": current_price, "eps": eps},
                ensure_ascii=False,
            ),
        )
        derived = 1
        LOG.info("per_derived symbol=%s year=%s per=%s", page.symbol, latest.fiscal_year, per)

    LOG.info("fundamentals_stored symbol=%s official=%s derived=%s", page.symbol, official, derived)
    return official, derived
