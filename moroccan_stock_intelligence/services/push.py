from __future__ import annotations

import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush
from sqlalchemy import select
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


def save_subscription(session: Session, subscription: dict) -> None:
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
        existing.p256dh = p256dh
        existing.auth = auth
        return
    session.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth))


def send_push_to_all(session: Session, title: str, body: str, url: str = "/") -> int:
    if not settings.vapid_private_key:
        LOG.warning("vapid_keys_missing push_skipped=true")
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url})
    vapid = Vapid01.from_raw(settings.vapid_private_key.encode())
    sent = 0
    stale: list[PushSubscription] = []

    for sub in session.scalars(select(PushSubscription)).all():
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
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                stale.append(sub)
            LOG.warning("push_failed endpoint=%s status=%s", sub.endpoint[:40], status)

    for sub in stale:
        session.delete(sub)
    session.commit()
    LOG.info("push_sent count=%s stale_removed=%s", sent, len(stale))
    return sent
