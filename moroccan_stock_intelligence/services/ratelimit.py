"""In-process rate limiting for the routes that cost real money to serve.

WHY THIS EXISTS
---------------
Authentication stops a stranger reading the owner's P/L. It does not stop an
authenticated-but-hostile caller — or the owner's own runaway script — from
hammering the endpoints that scrape a third party or recompute ten analysts.
The audit (AUDIT_2026-07-18.md §16) rated that ÉLEVÉ: `/api/report/{sym}?fresh=true`
runs the whole research engine and writes rows, at the cost of one HTTP request.

WHY A FIXED WINDOW, IN MEMORY
-----------------------------
The project's standing constraint is "no new infrastructure", and the deployment
is a single container serving a single user. Redis would be a second service to
run, monitor and pay for, to protect one person from themselves.

The honest limitations, stated rather than papered over:

  * per-container — two replicas would each allow the full budget. There is one
    replica, and SQLite already prevents a second (see §14 of the audit).
  * lost on restart — a restart forgives outstanding penalties. Acceptable: this
    is a cost guard, not a security boundary. The security boundary is the
    session cookie.

A fixed window (rather than a token bucket) is chosen because it is trivially
auditable: you can read the counter and say exactly how many requests were let
through in the current minute. Burstiness at a window edge is irrelevant at
these limits.

WHAT IS NOT RATE LIMITED
------------------------
Cheap reads served from the market-state cache. Limiting those would degrade the
app for no benefit — they cost a dict lookup.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    """`max_requests` per `window_seconds`, per client."""

    max_requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.window_seconds < 1:
            raise ValueError("a limit must allow at least one request per second")


# Budgets, chosen from what each route actually costs.
#
# collect: reaches casablanca-bourse.com. The refresh cooldown (900 s) already
#   bounds real work; this bounds the requests themselves so a loop cannot turn
#   the platform into an amplifier pointed at the exchange.
# heavy:  runs the ten analysts and writes rows.
# notify: sends a web push to the owner's devices.
# login:  handled separately by services/auth's lockout, which is per-failure
#   rather than per-request; this is the blunt ceiling behind it.
LIMITS: dict[str, Limit] = {
    "collect": Limit(max_requests=6, window_seconds=300),
    "heavy": Limit(max_requests=20, window_seconds=60),
    "notify": Limit(max_requests=5, window_seconds=300),
    "login": Limit(max_requests=30, window_seconds=300),
}


@dataclass
class _Window:
    started_at: float
    count: int = 0


_state: dict[tuple[str, str], _Window] = {}
_lock = Lock()


def check(bucket: str, client: str, *, now: float | None = None) -> int:
    """Seconds to wait, or 0 when the caller may proceed.

    Consumes one unit of budget when it returns 0. Returning the retry delay
    rather than a bool lets the caller send a truthful `Retry-After`, which is
    what makes a well-behaved client back off instead of spinning.
    """
    limit = LIMITS.get(bucket)
    if limit is None:  # an unknown bucket must not silently allow everything
        raise KeyError(f"unknown rate-limit bucket: {bucket}")

    moment = time.monotonic() if now is None else now
    key = (bucket, client)
    with _lock:
        window = _state.get(key)
        if window is None or moment - window.started_at >= limit.window_seconds:
            _state[key] = _Window(started_at=moment, count=1)
            return 0
        if window.count < limit.max_requests:
            window.count += 1
            return 0
        elapsed = moment - window.started_at
        return max(1, int(limit.window_seconds - elapsed) + 1)


def reset() -> None:
    """Test seam, and a way to clear state after a config change. Never called by routes."""
    with _lock:
        _state.clear()
