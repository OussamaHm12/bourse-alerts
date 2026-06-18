from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.schemas import StockSnapshot

LOG = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36 MoroccanStockIntelligence/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class ScraperError(RuntimeError):
    pass


class MarketDataScraper(ABC):
    name: str
    url: str

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(settings.http_retries),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def fetch_html(self, url: str) -> str:
        LOG.info("fetching url=%s source=%s", url, self.name)
        response = self.session.get(
            url,
            timeout=settings.http_timeout_seconds,
            allow_redirects=True,
            verify=settings.http_verify_ssl,
        )
        response.raise_for_status()
        return response.text

    @abstractmethod
    def collect(self) -> list[StockSnapshot]:
        raise NotImplementedError
