"""Pydantic models for request bodies the API accepts from the outside world.

The audit (AUDIT_2026-07-18.md §16) rated `POST /api/push/subscribe` ÉLEVÉ: the
handler passed `await request.json()` straight to the persistence layer, so a
caller chose both the shape and the size of what got stored, and a malformed body
surfaced as a 500 rather than a 422.

Validating at the edge — instead of inside `save_subscription` — is what lets the
service layer assume its input is already well-formed, and what turns "bad
request" into the status code that actually means it.

Only INPUT is modelled here. Response payloads stay dicts for now: they are built
by the view layer from dataclasses that already have their own tests, and typing
them is a larger refactor (tracked as remaining work, not silently skipped).
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A real endpoint is a few hundred characters. The ceiling exists so a caller
# cannot store a megabyte per row; it is not a guess about any one push service.
MAX_ENDPOINT_LENGTH = 2048
# Web Push keys are fixed-size base64: p256dh is 65 raw bytes (88 chars), auth is
# 16 (24 chars). The bounds are generous around those, not exact, because the
# encoding's padding varies between browsers.
MAX_KEY_LENGTH = 255

# Push services the browser may legitimately hand us. Checked as a scheme +
# hostname policy rather than an allowlist of vendors: a vendor allowlist would
# break the day a browser ships a new push backend, and the property that
# actually matters is "https, to a real host".
_ALLOWED_SCHEMES = frozenset({"https"})


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=8, max_length=MAX_KEY_LENGTH)
    auth: str = Field(min_length=8, max_length=MAX_KEY_LENGTH)


class PushSubscriptionIn(BaseModel):
    """The `PushSubscription.toJSON()` shape a browser produces.

    `extra="forbid"` on the keys but NOT here: browsers add fields to the top-level
    object (`expirationTime`, and vendor extensions), and rejecting a subscription
    because Chrome added a field would break push for no security gain. The fields
    we persist are exactly the three below.
    """

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=8, max_length=MAX_ENDPOINT_LENGTH)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_https_with_a_host(cls, value: str) -> str:
        """Reject anything that is not an https URL to a named host.

        Without this the endpoint column accepts `file:///etc/passwd`,
        `http://169.254.169.254/…` (cloud metadata) or a bare string. We later hand
        this value to `webpush()`, which will make a request to it — so an
        unvalidated endpoint is an SSRF primitive, not just untidy data.
        """
        parsed = urlparse(value)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError("endpoint must be an https URL")
        if not parsed.hostname:
            raise ValueError("endpoint must include a host")
        return value


class LoginIn(BaseModel):
    """The login body.

    `max_length` is not a password policy — it stops a caller forcing a PBKDF2
    derivation over a multi-megabyte string, which is CPU the login route hands
    out for free.
    """

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=512)
