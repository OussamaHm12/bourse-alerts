"""Authentication — the control that stops the platform serving personal data.

The audit (AUDIT_2026-07-18.md §16) rated the absence of this layer CRITIQUE: the
deployed service exposed holdings, buy prices and P/L to anyone with the URL.

What is asserted here is the *policy*, not the plumbing:

  * deny-by-default — a route nobody thought about is private
  * fail-closed     — a missing secret answers 503, never 200
  * stateless       — a session survives a restart, and rotation kills it
  * the throttle cannot be bypassed with a forged header

`conftest.py` sets AUTH_PASSWORD before `config` is first imported, so the whole
suite runs against a configured auth layer; the tests that need a *different*
configuration swap `settings` explicitly (it is a frozen dataclass, so
`dataclasses.replace` is the honest way to do it).
"""

from __future__ import annotations

import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from moroccan_stock_intelligence import api as api_module
from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services import auth

from tests.conftest import TEST_AUTH_PASSWORD

# A route that carries real personal data. If deny-by-default ever regresses,
# this is what leaks.
PRIVATE_ROUTE = "/api/overview"


@pytest.fixture
def client():
    return TestClient(api_module.app)


@pytest.fixture(autouse=True)
def clean_throttle():
    """The lockout store is a module global: one test's failures must not lock another."""
    auth.reset_throttle()
    yield
    auth.reset_throttle()


def login(client: TestClient, password: str = TEST_AUTH_PASSWORD):
    return client.post("/api/auth/login", json={"password": password})


# --------------------------------------------------------------------------- #
# Login / logout                                                               #
# --------------------------------------------------------------------------- #


def test_login_with_the_right_password_sets_a_session_cookie(client):
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth.COOKIE_NAME in response.cookies


def test_login_with_the_wrong_password_is_401_and_sets_no_cookie(client):
    response = login(client, "definitely-not-the-password")
    assert response.status_code == 401
    assert auth.COOKIE_NAME not in response.cookies


def test_login_with_no_password_field_is_401_not_500(client):
    assert client.post("/api/auth/login", json={}).status_code == 401


def test_login_with_a_non_json_body_is_400(client):
    response = client.post(
        "/api/auth/login", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_logout_clears_the_session(client):
    login(client)
    assert client.get(PRIVATE_ROUTE).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get(PRIVATE_ROUTE).status_code == 401


# --------------------------------------------------------------------------- #
# Deny-by-default                                                              #
# --------------------------------------------------------------------------- #


def test_a_private_route_without_a_session_is_401(client):
    assert client.get(PRIVATE_ROUTE).status_code == 401


@pytest.mark.parametrize("path", sorted(auth.PUBLIC_PATHS))
def test_public_paths_answer_without_a_session(client, path):
    """Every allowlisted path must work unauthenticated — that is why it is listed."""
    response = client.post(path) if path.startswith("/api/auth/log") else client.get(path)
    assert response.status_code != 401


def test_the_portfolio_route_is_private():
    """The single most sensitive payload: quantities, buy prices, P/L."""
    assert not auth.is_public("/api/analysis/portfolio")


def test_run_now_is_private():
    """A public run-now lets a stranger trigger a scrape and a Telegram message."""
    assert not auth.is_public("/api/run-now")


def test_the_allowlist_is_exactly_what_we_intend():
    """A route added to PUBLIC_PATHS must be a deliberate, reviewed decision.

    This test is the review: widening the allowlist has to be done here, on
    purpose, rather than as a side effect of a merge.
    """
    assert auth.PUBLIC_PATHS == frozenset(
        {
            "/api/health",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/auth/status",
            "/api/auth/session",
        }
    )


# --------------------------------------------------------------------------- #
# Token semantics                                                              #
# --------------------------------------------------------------------------- #


def test_a_garbage_cookie_is_rejected(client):
    client.cookies.set(auth.COOKIE_NAME, "not-a-token")
    assert client.get(PRIVATE_ROUTE).status_code == 401


def test_a_tampered_signature_is_rejected(client):
    token = auth.mint_token()
    version, stamp, _signature = token.split(".")
    client.cookies.set(auth.COOKIE_NAME, f"{version}.{stamp}.forged")
    assert client.get(PRIVATE_ROUTE).status_code == 401


def test_a_token_whose_timestamp_was_edited_is_rejected():
    """The timestamp is inside the signed payload, so moving it breaks the HMAC."""
    version, stamp, signature = auth.mint_token().split(".")
    assert not auth.verify_token(f"{version}.{int(stamp) + 10_000}.{signature}")


def test_an_expired_token_is_rejected():
    issued = int(time.time()) - (settings.auth_session_days * 86400) - 60
    assert not auth.verify_token(auth.mint_token(issued_at=issued))


def test_a_token_issued_in_the_future_is_rejected():
    assert not auth.verify_token(auth.mint_token(issued_at=int(time.time()) + 3600))


def test_a_session_survives_a_restart():
    """Stateless by design: no session table, so a redeploy does not log the owner out.

    Simulated by clearing the derived-key cache — the only in-process state — and
    verifying the token still checks out.
    """
    token = auth.mint_token()
    auth._key_cache.clear()
    assert auth.verify_token(token)


def test_rotating_the_password_invalidates_every_existing_session(monkeypatch):
    """Rotation that left old cookies alive would not be rotation."""
    token = auth.mint_token()
    assert auth.verify_token(token)

    rotated = dataclasses.replace(settings, auth_password="a-brand-new-secret-value")
    monkeypatch.setattr(auth, "settings", rotated)
    assert not auth.verify_token(token)


# --------------------------------------------------------------------------- #
# Fail-closed                                                                  #
# --------------------------------------------------------------------------- #


def test_an_unset_password_makes_private_routes_503(client, monkeypatch):
    """503, not 200. An operator error must not silently publish the data."""
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_password=None))
    assert client.get(PRIVATE_ROUTE).status_code == 503


def test_a_short_password_makes_private_routes_503(client, monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_password="short"))
    assert client.get(PRIVATE_ROUTE).status_code == 503


def test_health_still_answers_when_auth_is_unconfigured(client, monkeypatch):
    """The platform must be able to see the container even when auth is broken."""
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_password=None))
    assert client.get("/api/health").status_code == 200


def test_login_is_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_password=None))
    assert login(client).status_code == 503


def test_minting_a_token_without_a_secret_raises(monkeypatch):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(settings, auth_password=None))
    with pytest.raises(RuntimeError):
        auth.mint_token()


# --------------------------------------------------------------------------- #
# Throttling                                                                   #
# --------------------------------------------------------------------------- #


def test_repeated_failures_lock_the_client_out(client):
    for _ in range(settings.auth_max_attempts):
        assert login(client, "wrong").status_code == 401
    locked = login(client, "wrong")
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


def test_the_lockout_also_blocks_the_correct_password(client):
    """Otherwise the throttle would be trivially bypassed by guessing correctly."""
    for _ in range(settings.auth_max_attempts):
        login(client, "wrong")
    assert login(client).status_code == 429


def test_a_successful_login_clears_the_failure_streak(client):
    for _ in range(settings.auth_max_attempts - 1):
        login(client, "wrong")
    assert login(client).status_code == 200
    assert login(client, "wrong").status_code == 401  # streak reset, not locked


def test_the_throttle_key_uses_the_rightmost_forwarded_hop():
    """Railway appends the peer it actually saw, so the rightmost entry is the
    one the platform vouches for. Taking the leftmost would let an attacker rotate
    a header value and reset their own lockout."""
    assert auth.client_key(peer="10.0.0.1", forwarded_for="1.2.3.4, 5.6.7.8") == "5.6.7.8"


def test_the_throttle_key_falls_back_to_the_peer():
    assert auth.client_key(peer="10.0.0.1", forwarded_for=None) == "10.0.0.1"
    assert auth.client_key(peer=None, forwarded_for=None) == "unknown"


def test_an_empty_forwarded_header_does_not_crash():
    assert auth.client_key(peer="10.0.0.1", forwarded_for="  ,  ") == "10.0.0.1"


# --------------------------------------------------------------------------- #
# Cookie policy                                                                #
# --------------------------------------------------------------------------- #


def test_the_cookie_is_httponly_and_samesite_strict():
    """HttpOnly stops an XSS reading it; SameSite=Strict is the CSRF control."""
    params = auth.cookie_params()
    assert params["httponly"] is True
    assert params["samesite"] == "strict"
    assert params["path"] == "/"


def test_the_status_route_reports_configuration_and_session(client):
    anonymous = client.get("/api/auth/status").json()
    assert anonymous == {"configured": True, "authenticated": False}
    login(client)
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_session_is_an_alias_of_status(client):
    assert client.get("/api/auth/session").json() == client.get("/api/auth/status").json()
