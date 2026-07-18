"""Single-owner authentication: a signed, stateless session cookie.

WHY A COOKIE, and not Basic Auth or a bearer token
--------------------------------------------------
The client is an installed Flutter PWA that reaches the API through same-origin
`HttpRequest` (main.dart) and `fetch` (web/push.js). Both send cookies on
same-origin requests with no code at the call site, so ~20 call sites keep
working untouched.

  * Basic Auth would put the password itself on every request and hand the
    browser a native credential dialog — awkward inside a standalone PWA, cached
    by the browser, and with no way to log out.
  * A bearer token would have to live somewhere JavaScript can read (localStorage),
    which is exactly what an XSS steals, and it would need plumbing at every call
    site.

The cookie is HttpOnly, so the app's own JS cannot read it either: nothing
secret ever reaches the compiled frontend. The owner types the password; it
travels once, in the login body, over TLS.

STATELESS, AND WHY THAT MAKES ROTATION WORK
-------------------------------------------
The cookie carries `v1.<issued_at>.<hmac>` — no session table, no Redis, in
keeping with the project's "no new infrastructure" constraint. The signing key is
derived from AUTH_PASSWORD, which is what makes rotation real: change the env
var, and every cookie ever issued stops verifying. Rotation that left old
sessions alive would not be rotation.

FAIL-CLOSED
-----------
No AUTH_PASSWORD, or one shorter than MIN_PASSWORD_LENGTH, means protected
routes answer 503 — not 200. The bug this whole module exists to fix is data
being public without anyone deciding it should be; an auth layer that quietly
disables itself on a missing env var would reintroduce it on the first typo.
/api/health stays up regardless so the platform can still see the container.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from threading import Lock

from moroccan_stock_intelligence.config import settings

LOG = logging.getLogger(__name__)

COOKIE_NAME = "msi_session"

# Deny-by-default: every route not named here needs a session.
#
# The audit's finding was not "someone protected the wrong routes", it was that
# nobody had ever decided. An allowlist inverts the default, so a route added next
# month is private until a human types its path in this set on purpose.
#
#   /api/health      — the platform's healthcheck; must answer before anyone logs in
#   /api/auth/*      — you cannot present a session in order to obtain a session
#
# The compiled PWA (index.html, main.dart.js, …) is served by a StaticFiles mount,
# which router dependencies do not cover. That is deliberate and safe: the bundle
# is application code, holds no personal data, and has to load in order to draw the
# login screen. Locked by test.
PUBLIC_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/status",
        # `/session` is the same payload under the name the client naturally reaches
        # for. Both are listed rather than one redirecting to the other: a redirect
        # on an unauthenticated probe is one more thing to reason about.
        "/api/auth/session",
    }
)

# NOTE: /api/vapid-public-key is deliberately NOT public. The push subscription
# flow runs inside the loaded app, i.e. after login, so it has a session; making
# the key readable anonymously would hand an attacker the identity the push
# endpoints are keyed on for nothing.


def is_public(path: str) -> bool:
    normalised = path.rstrip("/") or "/"
    return normalised in PUBLIC_PATHS


_TOKEN_VERSION = "v1"
# A FIXED salt, deliberately — static analysers flag this (python:S2053) because the
# usual context is password *storage*, where a per-user random salt is mandatory.
# This is not storage: it derives a signing key, and the derivation has to be
# reproducible or every restart would issue a new key and log the owner out. There
# is no stable per-deployment secret other than AUTH_PASSWORD itself to salt with.
#
# What the salt would buy — precomputation resistance — is bought instead by
# MIN_PASSWORD_LENGTH plus 200k PBKDF2 iterations, and the attack it defends
# against (offline cracking) already requires having stolen a valid cookie.
_KEY_SALT = b"moroccan-stock-intelligence/session-key/v1"
# Cost paid once per password (cached below), not per request. It only matters if
# a stolen cookie is used to brute-force the password offline: signature checks
# stay a single HMAC.
_KDF_ITERATIONS = 200_000

# A password short enough to brute-force makes every other control here theatre.
MIN_PASSWORD_LENGTH = 12

_key_cache: dict[str, bytes] = {}
_key_lock = Lock()


# --------------------------------------------------------------------------- #
# Configuration state                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ConfigState:
    """Whether auth can operate at all. `reason` is for the operator, not the client."""

    configured: bool
    reason: str = ""


def config_state() -> ConfigState:
    password = settings.auth_password
    if not password:
        return ConfigState(False, "AUTH_PASSWORD is not set")
    if len(password) < MIN_PASSWORD_LENGTH:
        return ConfigState(
            False, f"AUTH_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters"
        )
    return ConfigState(True)


# --------------------------------------------------------------------------- #
# Token minting / verification                                                 #
# --------------------------------------------------------------------------- #


def _signing_key(password: str) -> bytes:
    """PBKDF2 of the password, memoised per password value.

    Cached because the derivation is deliberately expensive and the input changes
    only when the owner rotates the secret. Keyed by the password so a rotation —
    or a test that swaps `settings` — derives a fresh key instead of silently
    reusing the old one.
    """
    with _key_lock:
        key = _key_cache.get(password)
        if key is None:
            key = hashlib.pbkdf2_hmac("sha256", password.encode(), _KEY_SALT, _KDF_ITERATIONS)
            _key_cache[password] = key
        return key


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign(payload: str, password: str) -> str:
    return _b64(hmac.new(_signing_key(password), payload.encode(), hashlib.sha256).digest())


def mint_token(*, issued_at: int | None = None) -> str:
    """A session token for the owner. Caller must have already checked the password."""
    state = config_state()
    if not state.configured:
        raise RuntimeError(f"cannot mint a session token: {state.reason}")
    stamp = int(time.time()) if issued_at is None else issued_at
    payload = f"{_TOKEN_VERSION}.{stamp}"
    return f"{payload}.{_sign(payload, settings.auth_password)}"


def verify_token(token: str | None) -> bool:
    """True only for a token this server signed, with the current password, unexpired.

    Every failure path returns False rather than raising: a malformed cookie is an
    unauthenticated request, not a server error. A cookie signed under a previous
    password fails here — that is rotation working.
    """
    if not token:
        return False
    state = config_state()
    if not state.configured:
        return False

    parts = token.split(".")
    if len(parts) != 3:
        return False
    version, stamp, signature = parts
    if version != _TOKEN_VERSION:
        return False

    expected = _sign(f"{version}.{stamp}", settings.auth_password)
    if not hmac.compare_digest(signature, expected):
        return False

    # Only trusted after the signature check: an unsigned timestamp is attacker input.
    try:
        issued_at = int(stamp)
    except ValueError:
        return False
    age = time.time() - issued_at
    if age < 0:
        return False  # issued in the future — a clock jump or a forgery attempt
    return age <= settings.auth_session_days * 86400


def cookie_params() -> dict:
    """Cookie flags, in one place so the API layer cannot drift from the policy.

    httponly  — the app's own JS cannot read it, so an XSS cannot exfiltrate it
    secure    — Railway terminates TLS; the cookie must never travel in clear
    samesite  — "strict" is the primary CSRF control: the browser withholds the
                cookie on any cross-site request, so a hostile page cannot make an
                authenticated POST /api/run-now. The app's own fetches are
                same-site and unaffected.
    """
    return {
        "max_age": settings.auth_session_days * 86400,
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "strict",
        "path": "/",
    }


def check_password(candidate: str | None) -> bool:
    """Constant-time comparison against the configured secret."""
    state = config_state()
    if not state.configured or not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), settings.auth_password.encode())


# --------------------------------------------------------------------------- #
# Login throttling                                                             #
# --------------------------------------------------------------------------- #
#
# In-memory and per-container: a restart forgets it, and it does not span replicas.
# Both are acceptable here (one container, one user) and neither is load-bearing —
# the real control against guessing is MIN_PASSWORD_LENGTH. This exists to make an
# online guessing loop pointless rather than merely slow.
#
# Deliberately NOT global: a global lockout would let anyone lock the owner out of
# their own platform by spamming wrong passwords, trading a small brute-force
# gain for a free denial of service.


@dataclass
class _Attempts:
    count: int = 0
    blocked_until: float = 0.0


_attempts: dict[str, _Attempts] = {}
_attempts_lock = Lock()


def client_key(*, peer: str | None, forwarded_for: str | None) -> str:
    """Identify the caller for throttling.

    Takes the RIGHTMOST X-Forwarded-For entry, not the leftmost. Railway's edge
    appends the peer it actually saw, so the rightmost entry is the one the
    platform vouches for; the leftmost is whatever the client typed and is free to
    forge. Wrong choice here would make the throttle bypassable with a header.
    """
    if forwarded_for:
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return peer or "unknown"


def throttle_retry_after(key: str) -> int:
    """Seconds the caller must wait, or 0 if they may try now."""
    now = time.time()
    with _attempts_lock:
        record = _attempts.get(key)
        if record is None or record.blocked_until <= now:
            return 0
        return int(record.blocked_until - now) + 1


def record_failure(key: str) -> None:
    now = time.time()
    with _attempts_lock:
        record = _attempts.setdefault(key, _Attempts())
        if record.blocked_until and record.blocked_until <= now:
            record.count = 0  # the lockout expired; start a fresh streak
        record.count += 1
        if record.count >= settings.auth_max_attempts:
            record.blocked_until = now + settings.auth_lockout_seconds
            record.count = 0
            LOG.warning(
                "auth_lockout client=%s seconds=%s", key, settings.auth_lockout_seconds
            )


def record_success(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def reset_throttle() -> None:
    """Test seam. Never called by the app."""
    with _attempts_lock:
        _attempts.clear()
