from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from moroccan_stock_intelligence.schemas import StockSnapshot
from moroccan_stock_intelligence.scrapers.base import MarketDataScraper, ScraperError
from moroccan_stock_intelligence.utils import normalize_text, parse_number

LOG = logging.getLogger(__name__)


class BMCECapitalScraper(MarketDataScraper):
    name = "BMCE Capital Bourse"
    url = "https://www.bmcecapitalbourse.com/bkbbourse/lists/"

    def collect(self) -> list[StockSnapshot]:
        html = self.fetch_html(self.url)
        snapshots = self.parse(html)
        if not snapshots:
            raise ScraperError("BMCE Capital Bourse returned no stock rows")
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
            link = tr.find("a", href=re.compile(r"details", re.IGNORECASE))
            if not isinstance(link, Tag):
                continue

            company_name = normalize_text(link.get_text(" ")) or cells[0]
            if not company_name:
                continue
            symbol = self._guess_symbol(company_name, str(link.get("href") or ""))
            numbers = [parse_number(cell) for cell in cells]
            numeric = [number for number in numbers if number is not None]
            if not numeric:
                continue

            rows.append(
                StockSnapshot(
                    symbol=symbol,
                    company_name=company_name,
                    sector=None,
                    current_price=numeric[0],
                    daily_variation=self._first_percent(cells),
                    volume=numeric[-1] if len(numeric) > 1 else None,
                    traded_quantity=None,
                    market_cap=None,
                    observed_at=observed_at,
                    source=self.name,
                    source_url=urljoin("https://www.bmcecapitalbourse.com", str(link.get("href"))),
                    raw={"cells": cells},
                )
            )
        return rows

    @staticmethod
    def _guess_symbol(company_name: str, href: str) -> str:
        aliases = {
            "TGCC": "TGC",
            "AKDITAL": "AKT",
            "ATTIJARIWAFA": "ATW",
            "MARSA MAROC": "MSA",
            "MANAGEM": "MNG",
            "HPS": "HPS",
            "CIH": "CIH",
        }
        upper = company_name.upper()
        for needle, symbol in aliases.items():
            if needle in upper:
                return symbol
        tokens = re.findall(r"[A-Z0-9]{2,5}", upper)
        return tokens[0] if tokens else href.rstrip("/").split("/")[-1].upper()

    @staticmethod
    def _first_percent(cells: list[str]) -> float | None:
        for cell in cells:
            if "%" in cell:
                return parse_number(cell)
        return None
