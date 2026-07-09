"""Shared fetch for the Phase 1b collectors.

Mirrors `scrapers/base.py` exactly (same headers, tenacity retry, opt-in insecure
SSL fallback) but is a plain function rather than the StockSnapshot-shaped ABC.

`casablanca-bourse.com` intermittently read-times-out from some networks (observed
2026-07-09: the JSON:API root and robots.txt each needed 2-4 attempts), hence the
retry and the per-call timeout override.
"""

from __future__ import annotations

import logging

import requests
import urllib3
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.scrapers.base import HEADERS

LOG = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update(HEADERS)


class CollectorError(RuntimeError):
    pass


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(max(settings.http_retries, 3)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def fetch_text(url: str, source: str, timeout: float | None = None) -> str:
    """GET a page, honouring the project's SSL policy. Raises on HTTP error."""
    seconds = timeout or settings.http_timeout_seconds
    LOG.info("collector_fetch url=%s source=%s", url, source)
    try:
        response = _session.get(url, timeout=seconds, allow_redirects=True,
                                verify=settings.http_verify_ssl)
    except requests.exceptions.SSLError:
        if not settings.http_allow_insecure_source_retry:
            raise
        LOG.warning("ssl_verify_failed_retrying_without_verification url=%s source=%s", url, source)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = _session.get(url, timeout=seconds, allow_redirects=True, verify=False)
    response.raise_for_status()
    return response.text
