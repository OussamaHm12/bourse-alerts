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


def _key(value: str) -> str:
    value = normalize_text(value).lower()
    replacements = {
        "é": "e",
        "è": "e",
        "ê": "e",
        "à": "a",
        "ç": "c",
        "'": "",
        " ": "_",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return re.sub(r"[^a-z0-9_]+", "", value)


class CasablancaBourseScraper(MarketDataScraper):
    name = "Casablanca Bourse"
    url = "https://www.casablanca-bourse.com/fr/live-market/marche-actions-groupement"

    def collect(self) -> list[StockSnapshot]:
        html = self.fetch_html(self.url)
        snapshots = self.parse(html)
        if not snapshots:
            raise ScraperError("Casablanca Bourse returned no stock rows")
        LOG.info("parsed source=%s rows=%s", self.name, len(snapshots))
        return snapshots

    def parse(self, html: str) -> list[StockSnapshot]:
        soup = BeautifulSoup(html, "html.parser")
        observed_at = datetime.now(UTC)
        snapshots: list[StockSnapshot] = []
        seen_tables: set[int] = set()

        for heading in soup.find_all(["h2", "h3"]):
            sector = normalize_text(heading.get_text(" "))
            table = heading.find_next("table")
            if not isinstance(table, Tag) or id(table) in seen_tables:
                continue
            seen_tables.add(id(table))
            snapshots.extend(self._parse_table(table, sector, observed_at))

        if snapshots:
            return snapshots

        for table in soup.find_all("table"):
            snapshots.extend(self._parse_table(table, None, observed_at))
        return snapshots

    def _parse_table(
        self, table: Tag, sector: str | None, observed_at: datetime
    ) -> list[StockSnapshot]:
        headers = [_key(th.get_text(" ")) for th in table.find_all("th")]
        if not headers or "instrument" not in headers:
            return []

        rows: list[StockSnapshot] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < len(headers):
                continue

            values = {
                headers[index]: normalize_text(cell.get_text(" "))
                for index, cell in enumerate(cells[: len(headers)])
            }
            link = cells[0].find("a")
            if not isinstance(link, Tag):
                continue
            company_name = normalize_text(link.get_text(" "))
            href = str(link.get("href") or "")
            symbol = href.rstrip("/").split("/")[-1].upper()
            if not symbol or not company_name:
                continue

            source_url = urljoin("https://www.casablanca-bourse.com", href)
            rows.append(
                StockSnapshot(
                    symbol=symbol,
                    company_name=company_name,
                    sector=sector,
                    current_price=parse_number(_value(values, "dernier_cours")),
                    daily_variation=parse_number(_value(values, "variation_en", "variation_en_")),
                    volume=parse_number(_value(values, "volume")),
                    traded_quantity=parse_number(_value(values, "quantite_echangee")),
                    market_cap=parse_number(_value(values, "capitalisation")),
                    high_day=parse_number(_value(values, "haut_jour", "_haut_jour")),
                    low_day=parse_number(_value(values, "bas_jour", "_bas_jour")),
                    observed_at=observed_at,
                    source=self.name,
                    source_url=source_url,
                    raw=values,
                )
            )
        return rows


def _value(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if key in values:
            return values[key]
    return None
