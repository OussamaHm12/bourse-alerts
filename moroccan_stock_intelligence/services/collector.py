from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import store_snapshot
from moroccan_stock_intelligence.schemas import StockSnapshot
from moroccan_stock_intelligence.scrapers import (
    BMCECapitalScraper,
    CasablancaBourseScraper,
    CDGCapitalScraper,
    MarketDataScraper,
)

LOG = logging.getLogger(__name__)


def default_scrapers() -> list[MarketDataScraper]:
    return [CasablancaBourseScraper(), BMCECapitalScraper(), CDGCapitalScraper()]


def collect_market_snapshots(scrapers: list[MarketDataScraper] | None = None) -> list[StockSnapshot]:
    errors: list[str] = []
    for scraper in scrapers or default_scrapers():
        try:
            snapshots = scraper.collect()
        except Exception as exc:  # noqa: BLE001 - source isolation is intentional here.
            LOG.warning("scraper_failed source=%s error=%s", scraper.name, exc)
            errors.append(f"{scraper.name}: {exc}")
            continue
        if snapshots:
            return snapshots
    raise RuntimeError("all market data sources failed: " + "; ".join(errors))


def persist_snapshots(session: Session, snapshots: list[StockSnapshot]) -> int:
    for snapshot in snapshots:
        store_snapshot(session, snapshot)
    session.commit()
    LOG.info("stored_snapshots count=%s", len(snapshots))
    return len(snapshots)
