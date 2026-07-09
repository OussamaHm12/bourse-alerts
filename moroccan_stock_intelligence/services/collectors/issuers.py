"""Issuer collection loop: ONE page fetch feeds BOTH the profile and the ratios.

Kept separate from `issuer_page.py` (which owns fetch+parse) and from the two thin
persisters, so nothing imports in a cycle.

Politeness: issuers are fetched sequentially with a small delay. `casablanca-bourse.com`
intermittently read-times-out, so a per-issuer failure is logged and skipped — it never
aborts the run, and the affected analyst simply keeps reporting "unavailable".
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import load_symbol_history
from moroccan_stock_intelligence.services.collectors.company import persist_profile
from moroccan_stock_intelligence.services.collectors.fundamentals import persist_fundamentals
from moroccan_stock_intelligence.services.collectors.issuer_page import fetch_issuer_page

LOG = logging.getLogger(__name__)

REQUEST_DELAY_SECONDS = 1.0


def _latest_price(session: Session, symbol: str) -> float | None:
    history = load_symbol_history(session, symbol, limit=1)
    return history[-1][1] if history else None


def collect_issuers(
    session: Session,
    symbols: list[str] | None = None,
    with_profile: bool = True,
    with_fundamentals: bool = True,
    delay: float = REQUEST_DELAY_SECONDS,
) -> dict[str, int]:
    """Fetch each issuer page once and persist whichever feeds are requested."""
    stocks = session.scalars(select(Stock)).all()
    if symbols:
        wanted = {s.upper() for s in symbols}
        stocks = [stock for stock in stocks if stock.symbol.upper() in wanted]

    tally = {"issuers": 0, "profiles": 0, "fundamental_years": 0, "derived_per": 0, "failed": 0}
    for index, stock in enumerate(stocks):
        if index:
            time.sleep(delay)
        page = fetch_issuer_page(stock.symbol)
        if page is None:
            tally["failed"] += 1
            continue
        tally["issuers"] += 1

        if with_profile and persist_profile(session, stock.id, page):
            tally["profiles"] += 1
        if with_fundamentals:
            price = _latest_price(session, stock.symbol)
            official, derived = persist_fundamentals(session, stock.id, page, price)
            tally["fundamental_years"] += official
            tally["derived_per"] += derived
        session.commit()

    LOG.info("issuer_collect_done %s", tally)
    return tally
