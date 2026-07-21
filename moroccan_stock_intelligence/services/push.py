from __future__ import annotations

import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import PushSubscription

LOG = logging.getLogger(__name__)


def generate_vapid_keys() -> tuple[str, str]:
    """Return (public_key, private_key) as URL-safe base64 strings for Web Push."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_value = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_point = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public = base64.urlsafe_b64encode(public_point).rstrip(b"=").decode()
    private = base64.urlsafe_b64encode(private_value).rstrip(b"=").decode()
    return public, private


# One owner, a handful of devices. The ceiling exists so a caller cannot grow the
# table without bound; re-subscribing from a known device updates in place and
# never counts against it, so a real user cannot hit this.
MAX_SUBSCRIPTIONS = 20


def save_subscription(session: Session, subscription: dict) -> None:
    """Store or refresh one device's push subscription.

    The payload is validated at the API edge (`api_models.PushSubscriptionIn`), so
    the required fields are known present here. The checks below are the ones the
    edge cannot make because they involve the database: idempotency on endpoint,
    and the ceiling on how many devices may be registered at all.
    """
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        raise ValueError("invalid subscription payload")

    existing = session.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )
    if existing is not None:
        # Re-subscribing rotates the keys; the browser does this on its own
        # schedule, so treating it as an update is correct, not a special case.
        existing.p256dh = p256dh
        existing.auth = auth
        return

    count = session.scalar(select(func.count()).select_from(PushSubscription)) or 0
    if count >= MAX_SUBSCRIPTIONS:
        LOG.warning("push_subscription_rejected reason=limit count=%s", count)
        raise ValueError(f"too many push subscriptions (max {MAX_SUBSCRIPTIONS})")

    session.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth))


def send_push_to_all(session: Session, title: str, body: str, url: str = "/") -> int:
    """Deliver one notification to every registered device. Returns the number sent.

    TWO THINGS HERE ARE LOAD-BEARING, both learned from a silent outage
    -------------------------------------------------------------------
    1. **Every call is bounded by a timeout.** `pywebpush.webpush()` defaults its
       `timeout` to `None`, and `requests` reads that as "block forever". The
       scheduler is a single-threaded `BackgroundScheduler`, so one push endpoint
       that accepts a connection and never answers does not merely lose its own
       notification — it parks the job thread indefinitely and every later job
       (digest, intraday, research, backup) silently stops running.

    2. **One device's failure must not cost the others theirs.** `webpush()` only
       raises `WebPushException` for an HTTP status above 202; a connection reset,
       DNS failure or TLS error surfaces as a raw `requests` exception. Catching
       only `WebPushException` let one unreachable endpoint abort the loop, skip
       every remaining device, and skip the stale-pruning below — and the callers
       wrap this in a blanket `except`, so it looked like nothing happened at all.

    Both failures present identically to the owner: notifications simply stop,
    with no error anywhere. That is why they are handled here rather than left to
    the caller.
    """
    if not settings.vapid_private_key:
        LOG.warning("vapid_keys_missing push_skipped=true")
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url})
    vapid = Vapid01.from_raw(settings.vapid_private_key.encode())
    sent = 0
    stale: list[PushSubscription] = []
    subscriptions = session.scalars(select(PushSubscription)).all()

    if not subscriptions:
        # Not an error, but worth saying: the job ran correctly and reached nobody.
        # This is what a re-installed PWA or a pruned subscription looks like.
        LOG.warning("push_no_subscriptions title=%s", title)
        return 0

    for sub in subscriptions:
        info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.vapid_subject},
                timeout=settings.push_timeout_seconds,
            )
            sent += 1
        except WebPushException as exc:
            # 404/410 is the push service saying this device is gone for good.
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                stale.append(sub)
            LOG.warning("push_failed endpoint=%s status=%s", sub.endpoint[:40], status)
        except Exception:  # noqa: BLE001 - see (2) above: never abort the batch
            # Transport-level failure (timeout, reset, DNS, TLS). Transient by
            # nature, so the subscription is NOT pruned — only a 404/410 proves
            # the device is really gone.
            LOG.exception("push_transport_failed endpoint=%s", sub.endpoint[:40])

    for sub in stale:
        session.delete(sub)
    session.commit()
    LOG.info(
        "push_sent count=%s of=%s stale_removed=%s", sent, len(subscriptions), len(stale)
    )
    return sent
