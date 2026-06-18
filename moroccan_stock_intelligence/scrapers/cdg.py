from __future__ import annotations

import logging
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from moroccan_stock_intelligence.schemas import StockSnapshot
from moroccan_stock_intelligence.scrapers.base import MarketDataScraper, ScraperError
from moroccan_stock_intelligence.utils import normalize_text, parse_number

LOG = logging.getLogger(__name__)


class CDGCapitalScraper(MarketDataScraper):
    name = "CDG Capital Bourse"
    url = "https://www.cdgcapitalbourse.ma/"

    def collect(self) -> list[StockSnapshot]:
        html = self.fetch_html(self.url)
        snapshots = self.parse(html)
        if not snapshots:
            raise ScraperError("CDG Capital Bourse returned no stock rows")
        LOG.info("parsed source=%s rows=%s", self.name, len(snapshots))
        return snapshots

    def parse(self, html: str) -> list[StockSnapshot]:
        soup = BeautifulSoup(html, "html.parser")
        observed_at = datetime.now(UTC)
        rows: list[StockSnapshot] = []

        for tr in soup.find_all("tr"):
            cells = [normalize_text(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < 4:
                continue
            symbol = cells[0].upper()
            if not symbol or len(symbol) > 8:
                continue
            numbers = [parse_number(cell) for cell in cells[1:]]
            numeric = [number for number in numbers if number is not None]
            if not numeric:
                continue
            rows.append(
                StockSnapshot(
                    symbol=symbol,
                    company_name=symbol,
                    sector=None,
                    current_price=numeric[0],
                    daily_variation=next((parse_number(cell) for cell in cells if "%" in cell), None),
                    volume=numeric[-1] if len(numeric) > 1 else None,
                    traded_quantity=None,
                    market_cap=None,
                    observed_at=observed_at,
                    source=self.name,
                    source_url=self.url,
                    raw={"cells": cells},
                )
            )
        return rows
