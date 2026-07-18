"""Rate limiting and input validation on the routes that cost something to serve.

The audit (AUDIT_2026-07-18.md §16) rated two things ÉLEVÉ:

  * no rate limiting anywhere — `/api/report/{sym}?fresh=true` runs the whole
    research engine and writes rows, for the price of one HTTP request;
  * `POST /api/push/subscribe` stored whatever JSON it was handed.

Both are asserted here against the real app, not against the helper in isolation:
what matters is that the route enforces it, not that the counter can count.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from moroccan_stock_intelligence import api as api_module
from moroccan_stock_intelligence.models import Base, PushSubscription
from moroccan_stock_intelligence.services import ratelimit
from moroccan_stock_intelligence.services.push import MAX_SUBSCRIPTIONS, save_subscription

from tests.conftest import TEST_AUTH_PASSWORD


@pytest.fixture
def client():
    test_client = TestClient(api_module.app)
    assert test_client.post(
        "/api/auth/login", json={"password": TEST_AUTH_PASSWORD}
    ).status_code == 200
    return test_client


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(api_module.engine)
    Base.metadata.create_all(api_module.engine)
    yield


def valid_subscription(suffix: str = "abc") -> dict:
    return {
        "endpoint": f"https://fcm.googleapis.com/fcm/send/{suffix}",
        "keys": {"p256dh": "B" * 88, "auth": "C" * 24},
    }


# --------------------------------------------------------------------------- #
# The limiter itself                                                           #
# --------------------------------------------------------------------------- #


def test_the_budget_is_spent_then_refused():
    limit = ratelimit.LIMITS["notify"]
    for _ in range(limit.max_requests):
        assert ratelimit.check("notify", "client-a") == 0
    assert ratelimit.check("notify", "client-a") > 0


def test_clients_have_separate_budgets():
    """One noisy caller must not lock out another."""
    for _ in range(ratelimit.LIMITS["notify"].max_requests):
        ratelimit.check("notify", "client-a")
    assert ratelimit.check("notify", "client-b") == 0


def test_the_window_rolls_over():
    limit = ratelimit.LIMITS["notify"]
    for _ in range(limit.max_requests):
        ratelimit.check("notify", "client-a", now=1000.0)
    assert ratelimit.check("notify", "client-a", now=1000.0) > 0
    assert ratelimit.check("notify", "client-a", now=1000.0 + limit.window_seconds) == 0


def test_retry_after_never_advertises_zero_seconds():
    """A Retry-After of 0 tells a client to retry immediately, which is a busy loop."""
    limit = ratelimit.LIMITS["notify"]
    for _ in range(limit.max_requests):
        ratelimit.check("notify", "c", now=500.0)
    # Ask again a hair before the window closes.
    wait = ratelimit.check("notify", "c", now=500.0 + limit.window_seconds - 0.01)
    assert wait >= 1


def test_an_unknown_bucket_raises_rather_than_allowing_everything():
    with pytest.raises(KeyError):
        ratelimit.check("no-such-bucket", "c")


def test_a_limit_must_be_sane():
    with pytest.raises(ValueError):
        ratelimit.Limit(max_requests=0, window_seconds=60)


# --------------------------------------------------------------------------- #
# Enforcement on real routes                                                   #
# --------------------------------------------------------------------------- #


def test_push_test_is_rate_limited(client):
    limit = ratelimit.LIMITS["notify"]
    for _ in range(limit.max_requests):
        assert client.post("/api/push/test").status_code == 200
    refused = client.post("/api/push/test")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1


def test_run_now_is_rate_limited(client):
    limit = ratelimit.LIMITS["notify"]
    for _ in range(limit.max_requests):
        assert client.post("/api/run-now").status_code == 200
    assert client.post("/api/run-now").status_code == 429


def test_a_cached_report_read_is_not_rate_limited(client):
    """The cheap path must stay cheap — limiting it would degrade the app for nothing."""
    for _ in range(ratelimit.LIMITS["heavy"].max_requests + 5):
        response = client.get("/api/report/NOPE")
        assert response.status_code != 429


# --------------------------------------------------------------------------- #
# Push subscription validation                                                 #
# --------------------------------------------------------------------------- #


def test_a_valid_subscription_is_accepted(client):
    assert client.post("/api/push/subscribe", json=valid_subscription()).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"endpoint": "https://fcm.googleapis.com/x"},  # no keys
        {"endpoint": "not-a-url", "keys": {"p256dh": "B" * 88, "auth": "C" * 24}},
        {"endpoint": "http://insecure.example/x", "keys": {"p256dh": "B" * 88, "auth": "C" * 24}},
        {"endpoint": "file:///etc/passwd", "keys": {"p256dh": "B" * 88, "auth": "C" * 24}},
        {"endpoint": "https://x.example/y", "keys": {"p256dh": "short", "auth": "C" * 24}},
        {"endpoint": "https://" + "a" * 4000, "keys": {"p256dh": "B" * 88, "auth": "C" * 24}},
    ],
)
def test_a_malformed_subscription_is_422_not_500(client, payload):
    assert client.post("/api/push/subscribe", json=payload).status_code == 422


def test_a_plain_http_endpoint_is_refused(client):
    """An unvalidated endpoint is an SSRF primitive: we later make a request to it."""
    payload = valid_subscription()
    payload["endpoint"] = "http://169.254.169.254/latest/meta-data/"
    assert client.post("/api/push/subscribe", json=payload).status_code == 422


def test_resubscribing_the_same_endpoint_updates_in_place(client):
    payload = valid_subscription()
    client.post("/api/push/subscribe", json=payload)
    payload["keys"]["p256dh"] = "D" * 88
    client.post("/api/push/subscribe", json=payload)

    with api_module.SessionFactory() as session:
        rows = session.query(PushSubscription).all()
        assert len(rows) == 1
        assert rows[0].p256dh == "D" * 88


def test_the_device_ceiling_is_enforced(client):
    for index in range(MAX_SUBSCRIPTIONS):
        assert (
            client.post("/api/push/subscribe", json=valid_subscription(f"dev{index}")).status_code
            == 200
        )
    over = client.post("/api/push/subscribe", json=valid_subscription("one-too-many"))
    assert over.status_code == 409


def test_the_ceiling_does_not_block_refreshing_a_known_device(client):
    """A real user re-subscribes constantly; that must never hit the ceiling."""
    for index in range(MAX_SUBSCRIPTIONS):
        client.post("/api/push/subscribe", json=valid_subscription(f"dev{index}"))
    known = valid_subscription("dev0")
    known["keys"]["auth"] = "E" * 24
    assert client.post("/api/push/subscribe", json=known).status_code == 200


def test_save_subscription_still_guards_its_own_input():
    """The edge validates, but the service must not assume it was called through the edge."""
    with api_module.SessionFactory() as session:
        with pytest.raises(ValueError):
            save_subscription(session, {"endpoint": "https://x.example/y", "keys": {}})
