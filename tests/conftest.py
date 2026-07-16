"""Test-wide isolation.

This file exists for one reason: `api.py` opens an engine and runs `init_db()` at
**import time**, reading `settings.database_url`. Importing it in a test would
therefore create tables in the developer's real `data/market.db` — which is why
the API layer had no tests at all (AUDIT_TECHNIQUE.md §12: 194 statements, 0%).

`Settings` is a frozen dataclass whose field defaults are evaluated when the class
is created, so `DATABASE_URL` is read exactly once, at the first import of
`config`. conftest is imported before any test module, so this is the only place
early enough to redirect it. `load_dotenv()` does not override variables that are
already set, so this also wins over the repo's `.env`.

Assigned directly rather than via `setdefault`: an inherited DATABASE_URL from the
shell would silently point the suite at a real database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="msi-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DB_DIR / 'test.db').as_posix()}"
# The API starts APScheduler in its lifespan. Tests drive routes, not cron.
os.environ["ENABLE_SCHEDULER"] = "false"
# No test may reach Telegram or a push endpoint; absent credentials make the
# senders return False instead of attempting a request.
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
os.environ.pop("VAPID_PRIVATE_KEY", None)
# Holdings would otherwise be read from the developer's private portfolio file.
os.environ["PORTFOLIO_JSON"] = '{"fee_rate": 0.005, "holdings": []}'


@pytest.fixture(autouse=True)
def clear_market_state_cache():
    """`compute_state` caches on a fingerprint of its inputs, in a module global.

    In production there is one database, so the fingerprint identifies the data.
    Across tests there are many: two in-memory databases with the same row counts
    fingerprint identically, and the second test would silently read the first
    one's scores. Cleared around every test so a pass never depends on ordering.
    """
    from moroccan_stock_intelligence.services import market_state

    market_state.invalidate()
    yield
    market_state.invalidate()


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch, request):
    """Fail loudly if a test reaches the internet.

    Not hypothetical: the first version of `test_refresh_reports_fresh_when_data_is_recent`
    scraped casablanca-bourse.com for real, 81 rows, on every run — `/api/refresh`
    queues a real collection as a background task and TestClient executes it. A
    suite that quietly hammers a third-party site is a bug in the suite.

    Only `requests` is blocked. TestClient talks to the ASGI app in-process and
    never opens a socket, so it is unaffected.

    Opt out with `@pytest.mark.allow_network` if a test ever genuinely needs it.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    import requests

    def blocked(self, method, url, *args, **kwargs):
        raise AssertionError(
            f"Test attempted a real network call: {method} {url}\n"
            "Stub the collector/scraper instead — the suite must not depend on, "
            "or hammer, a live third-party site."
        )

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: this test may reach the internet")
