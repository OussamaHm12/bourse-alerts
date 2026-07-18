"""API layer tests — 25 routes that had no test at all (AUDIT_TECHNIQUE.md §12).

They were untestable rather than untested: `api.py` opens an engine and runs
`init_db()` at import time, so importing it pointed at the developer's real
database. `tests/conftest.py` redirects DATABASE_URL before the first import of
`config`, which is what makes this file possible.

What is guarded here is the contract the PWA depends on — status codes, route
resolution order, payload shape and idempotency — not the analytics underneath,
which their own modules cover.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from moroccan_stock_intelligence import api as api_module
from moroccan_stock_intelligence.models import Base, News, Price, Stock

from tests.conftest import TEST_AUTH_PASSWORD


@pytest.fixture(scope="module")
def client():
    """An **authenticated** client.

    Every route except the handful in `auth.PUBLIC_PATHS` now requires a session
    (deny-by-default), so a bare TestClient would exercise the auth layer rather
    than the routes. Logging in once per module keeps these tests about what they
    were written to check; `test_auth.py` owns the auth behaviour itself.

    No context manager: entering it would run the lifespan and start the
    scheduler. Routes do not need it.
    """
    test_client = TestClient(api_module.app)
    response = test_client.post("/api/auth/login", json={"password": TEST_AUTH_PASSWORD})
    assert response.status_code == 200, response.text
    return test_client


@pytest.fixture(scope="module")
def anonymous_client():
    """A client with no session — for asserting that a route is actually protected."""
    return TestClient(api_module.app)


@pytest.fixture(autouse=True)
def seeded_db():
    """A small, real market in the API's own engine, rebuilt per test."""
    Base.metadata.drop_all(api_module.engine)
    Base.metadata.create_all(api_module.engine)
    with api_module.SessionFactory() as session:
        session.add(Stock(id=1, symbol="ATW", company_name="ATTIJARIWAFA BANK", sector="Banques"))
        session.add(Stock(id=2, symbol="IAM", company_name="MAROC TELECOM", sector="Télécoms"))
        start = datetime.now(UTC) - timedelta(days=40)
        for stock_id, base_price in ((1, 400.0), (2, 90.0)):
            for day in range(40):
                session.add(
                    Price(
                        stock_id=stock_id,
                        observed_at=start + timedelta(days=day),
                        current_price=base_price + day * 0.5,
                        daily_variation=0.12,
                        volume=1_000_000.0,
                        source="test",
                    )
                )
        session.add(
            News(
                stock_id=1,
                title="ATW : Détachement du dividende",
                url="https://www.casablanca-bourse.com/fr/avis/1.pdf",
                source="Casablanca Bourse Avis",
                collected_at=datetime.now(UTC),
                event_type="ex_dividend",
                sentiment="neutral",
                impact_score=0.0,
            )
        )
        session.commit()
    yield


# --------------------------------------------------------------------------- #
# Route resolution — the failure the audit rated highest.                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["market-summary", "portfolio", "opportunities"])
def test_fixed_analysis_routes_are_not_swallowed_by_the_symbol_route(client, path):
    """`/api/analysis/{symbol}` is declared after these on purpose.

    Reorder them and FastAPI matches "opportunities" as a stock symbol, so the
    route 404s or — worse — silently analyses a stock that does not exist. There
    is a comment in api.py warning about it; this is the test that enforces it.
    """
    response = client.get(f"/api/analysis/{path}")
    assert response.status_code == 200
    body = response.json()
    assert "symbol" not in body or body.get("symbol") not in {
        "MARKET-SUMMARY",
        "PORTFOLIO",
        "OPPORTUNITIES",
    }


def test_the_symbol_route_still_works(client):
    assert client.get("/api/analysis/ATW").status_code == 200


def test_report_narrative_is_not_read_as_a_symbol(client):
    """`/api/report/{symbol}/narrative` must not resolve as symbol="ATW/narrative"."""
    response = client.get("/api/report/ATW/narrative")
    assert response.status_code == 200
    assert "narrative" in response.json()


# --------------------------------------------------------------------------- #
# Status codes                                                                 #
# --------------------------------------------------------------------------- #


def test_health_reports_the_scheduler_state(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduler": False}


@pytest.mark.parametrize(
    "path",
    ["/api/stock/NOPE", "/api/analysis/NOPE", "/api/report/NOPE"],
)
def test_unknown_symbol_is_404_not_500(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("horizon", ["yesterday", "SHORT", "", "1d"])
def test_an_invalid_horizon_is_rejected(client, horizon):
    assert client.get(f"/api/analysis/ATW?horizon={horizon}").status_code == 400


@pytest.mark.parametrize("horizon", ["short", "medium", "long"])
def test_every_valid_horizon_is_accepted(client, horizon):
    assert client.get(f"/api/analysis/ATW?horizon={horizon}").status_code == 200


# --------------------------------------------------------------------------- #
# Payload shape — what the PWA reads.                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "/api/overview",
        "/api/stocks",
        "/api/opportunities",
        "/api/news",
        "/api/notifications",
        "/api/sectors",
        "/api/favorites",
        "/api/performance",
        "/api/stock/ATW",
        "/api/knowledge/ATW",
        "/api/reports/history/ATW",
        "/api/analysis/market-summary",
        "/api/analysis/portfolio",
        "/api/analysis/opportunities",
        "/api/report/ATW",
    ],
)
def test_every_read_route_answers_with_json(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_stock_detail_carries_the_price_history_and_linked_news(client):
    body = client.get("/api/stock/ATW").json()
    assert body["symbol"] == "ATW"
    assert body["history"]
    assert body["news"][0]["event_type"] == "ex_dividend"


def test_stocks_can_be_filtered_by_sector(client):
    body = client.get("/api/stocks?sector=Banques").json()
    assert [row["symbol"] for row in body["stocks"]] == ["ATW"]


def test_stocks_can_be_searched(client):
    body = client.get("/api/stocks?q=telecom").json()
    assert [row["symbol"] for row in body["stocks"]] == ["IAM"]


def test_opportunities_respects_the_min_score(client):
    assert client.get("/api/opportunities?min_score=101").json()["opportunities"] == []


def test_news_exposes_the_classification(client):
    item = client.get("/api/news").json()["news"][0]
    assert item["event_type"] == "ex_dividend"
    assert item["sentiment"] == "neutral"
    assert item["impact_score"] == 0.0


# --------------------------------------------------------------------------- #
# Favorites — the only write routes the app calls.                             #
# --------------------------------------------------------------------------- #


def test_starring_is_idempotent(client):
    first = client.post("/api/favorites/ATW")
    second = client.post("/api/favorites/ATW")
    assert first.status_code == second.status_code == 200
    assert second.json()["is_favorite"] is True
    assert client.get("/api/favorites").json()["favorites"][0]["symbol"] == "ATW"


def test_starring_an_unknown_symbol_is_404(client):
    assert client.post("/api/favorites/NOPE").status_code == 404


def test_unstarring_something_never_starred_is_a_no_op_not_404(client):
    response = client.delete("/api/favorites/IAM")
    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_unstar_removes_it(client):
    client.post("/api/favorites/ATW")
    response = client.delete("/api/favorites/ATW")
    assert response.json()["removed"] is True
    assert client.get("/api/favorites").json()["favorites"] == []


def test_symbols_are_case_insensitive(client):
    assert client.post("/api/favorites/atw").status_code == 200
    assert client.get("/api/favorites").json()["favorites"][0]["symbol"] == "ATW"


# --------------------------------------------------------------------------- #
# Refresh state machine.                                                       #
# --------------------------------------------------------------------------- #


def test_refresh_reports_fresh_inside_the_cooldown(client):
    """Recent data must not re-scrape: the exchange publishes on a ~15 min delay,
    so a faster poll only costs the source bandwidth and returns nothing new."""
    with api_module.SessionFactory() as session:
        session.add(
            Price(
                stock_id=1,
                observed_at=datetime.now(UTC),
                current_price=420.0,
                daily_variation=0.1,
                volume=1_000.0,
                source="fresh",
            )
        )
        session.commit()

    body = client.post("/api/refresh").json()
    assert body["status"] == "fresh"


def test_refresh_starts_a_collection_when_data_is_stale(client, monkeypatch):
    """The seeded market's newest séance is a day old, so this is the stale path.

    The collector is stubbed: `/api/refresh` queues a real scrape as a background
    task and TestClient runs it, so an unstubbed version of this test hits
    casablanca-bourse.com for real on every run.
    """
    called = []
    monkeypatch.setattr(
        api_module, "refresh_market_data", lambda factory: called.append(True)
    )
    api_module.STATE.end()  # make sure no earlier test left the single-flight slot claimed

    body = client.post("/api/refresh").json()

    assert body["status"] == "started"
    assert called == [True]


def test_refresh_status_is_pollable(client):
    body = client.get("/api/refresh/status").json()
    assert "running" in body


def test_vapid_key_route_answers_without_keys_configured(client):
    assert client.get("/api/vapid-public-key").status_code == 200


# --------------------------------------------------------------------------- #
# The news wiring, end to end through the real API.                            #
# --------------------------------------------------------------------------- #


def test_a_profit_warning_lowers_the_score_served_by_the_api(client):
    """The dead 10% weight, verified through the public surface rather than a unit."""
    before = {row["symbol"]: row for row in client.get("/api/stocks").json()["stocks"]}

    with api_module.SessionFactory() as session:
        session.add(
            News(
                stock_id=1,
                title="ATW : Profit warning sur le résultat annuel",
                url="https://www.casablanca-bourse.com/fr/avis/warning.pdf",
                source="Casablanca Bourse Avis",
                collected_at=datetime.now(UTC),
                event_type="profit_warning",
                sentiment="negative",
                impact_score=-0.85,
            )
        )
        session.commit()

    after = {row["symbol"]: row for row in client.get("/api/stocks").json()["stocks"]}
    assert after["ATW"]["buy_score"] < before["ATW"]["buy_score"]
    assert after["IAM"]["buy_score"] == before["IAM"]["buy_score"], "IAM has no news"
