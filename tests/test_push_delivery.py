"""Web push delivery — the properties that decide whether the owner hears anything.

WHY THIS FILE EXISTS
--------------------
Push had no tests, and it broke in the way untested code breaks: silently. The
owner reported "je ne reçois plus les notifs" and nothing in the project could
say why — the scheduler logged successful jobs, the data pipeline was healthy,
and the failure was two layers down inside `send_push_to_all`.

Two defects, both of which end in total silence with no error:

  1. `pywebpush.webpush()` defaults `timeout` to `None`, which `requests` reads as
     "block forever". The scheduler is single-threaded, so one endpoint that
     accepts a connection and never replies parks the job thread and every later
     job stops with it.
  2. Only `WebPushException` was caught, but transport errors (reset, DNS, TLS)
     surface as raw `requests` exceptions — so one unreachable device aborted the
     loop before the remaining devices were tried.

Each test below pins one of those, plus the pruning rule they interact with.
"""

from __future__ import annotations

import logging

import pytest
import requests
from pywebpush import WebPushException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from moroccan_stock_intelligence.models import Base, PushSubscription
from moroccan_stock_intelligence.services import push as push_service


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s


@pytest.fixture(autouse=True)
def _vapid(monkeypatch):
    """A real-shaped private key, so `Vapid01.from_raw` is exercised, not stubbed.

    `Settings` is a frozen dataclass, so the whole object is swapped rather than
    one field (the repo's convention — see test_backup / test_favorites).
    """
    from dataclasses import replace

    from moroccan_stock_intelligence.config import settings as real
    from moroccan_stock_intelligence.services.push import generate_vapid_keys

    _public, private = generate_vapid_keys()
    monkeypatch.setattr(push_service, "settings", replace(real, vapid_private_key=private))
    return private


def _subscribe(session, n: int = 1) -> None:
    for i in range(n):
        session.add(
            PushSubscription(
                endpoint=f"https://push.example.com/device-{i}",
                p256dh=f"p256dh-{i}",
                auth=f"auth-{i}",
            )
        )
    session.commit()


# --------------------------------------------------------------------------- #
# 1. Every send is bounded in time.                                            #
# --------------------------------------------------------------------------- #


def test_every_push_passes_an_explicit_timeout(session, monkeypatch):
    """Without this the scheduler thread can block forever on one dead endpoint.

    Asserted on the ARGUMENT rather than by timing anything: the bug is not "it is
    slow", it is "there is no bound at all", and only the argument proves a bound
    exists.
    """
    seen: list = []
    monkeypatch.setattr(
        push_service, "webpush", lambda **kw: seen.append(kw.get("timeout", "ABSENT"))
    )
    _subscribe(session)

    push_service.send_push_to_all(session, "t", "b")

    assert seen, "webpush was never called"
    assert seen[0] != "ABSENT", "no timeout passed — requests would wait forever"
    assert isinstance(seen[0], (int, float)) and seen[0] > 0


# --------------------------------------------------------------------------- #
# 2. One device's failure must not cost the others theirs.                     #
# --------------------------------------------------------------------------- #


def test_a_transport_error_does_not_abort_the_remaining_devices(session, monkeypatch):
    """A connection reset is NOT a WebPushException — it used to escape the loop.

    Three devices, the first one unreachable. The other two must still be tried;
    before the fix they were silently skipped.
    """
    tried: list[str] = []

    def flaky(**kw):
        endpoint = kw["subscription_info"]["endpoint"]
        tried.append(endpoint)
        if endpoint.endswith("device-0"):
            raise requests.exceptions.ConnectionError("connection reset")

    monkeypatch.setattr(push_service, "webpush", flaky)
    _subscribe(session, 3)

    sent = push_service.send_push_to_all(session, "t", "b")

    assert len(tried) == 3, "the loop stopped early on the failing device"
    assert sent == 2, "the two healthy devices must still receive the push"


def test_a_timeout_on_one_device_does_not_sink_the_batch(session, monkeypatch):
    def slow(**kw):
        if kw["subscription_info"]["endpoint"].endswith("device-0"):
            raise requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(push_service, "webpush", slow)
    _subscribe(session, 2)

    assert push_service.send_push_to_all(session, "t", "b") == 1


def test_send_push_never_raises_out_of_the_loop(session, monkeypatch):
    """The callers wrap this in a blanket except, so an escape here is invisible."""
    monkeypatch.setattr(
        push_service,
        "webpush",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("something unforeseen")),
    )
    _subscribe(session, 2)

    assert push_service.send_push_to_all(session, "t", "b") == 0  # must not raise


# --------------------------------------------------------------------------- #
# 3. Pruning: only a dead device is forgotten.                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_device_is_pruned(session, monkeypatch, status):
    """404/410 is the push service saying this subscription is dead for good."""

    class _Resp:
        status_code = status

    monkeypatch.setattr(
        push_service,
        "webpush",
        lambda **kw: (_ for _ in ()).throw(WebPushException("gone", response=_Resp())),
    )
    _subscribe(session)

    push_service.send_push_to_all(session, "t", "b")

    assert session.scalars(select(PushSubscription)).all() == []


def test_a_transient_failure_never_prunes_a_subscription(session, monkeypatch):
    """The distinction that matters: a network blip must not unsubscribe a device.

    Pruning on a transport error would make an outage permanent — the owner would
    stop receiving pushes and the subscription that proves he wanted them would be
    gone, so nothing would ever recover on its own.
    """
    monkeypatch.setattr(
        push_service,
        "webpush",
        lambda **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError("blip")),
    )
    _subscribe(session, 2)

    push_service.send_push_to_all(session, "t", "b")

    assert len(session.scalars(select(PushSubscription)).all()) == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_server_side_error_never_prunes(session, monkeypatch, status):
    class _Resp:
        status_code = status

    monkeypatch.setattr(
        push_service,
        "webpush",
        lambda **kw: (_ for _ in ()).throw(WebPushException("busy", response=_Resp())),
    )
    _subscribe(session)

    push_service.send_push_to_all(session, "t", "b")

    assert len(session.scalars(select(PushSubscription)).all()) == 1


# --------------------------------------------------------------------------- #
# 4. The two silent no-ops are reported, not hidden.                           #
# --------------------------------------------------------------------------- #


def test_no_vapid_key_sends_nothing_without_crashing(session, monkeypatch):
    from dataclasses import replace

    from moroccan_stock_intelligence.config import settings as real

    monkeypatch.setattr(push_service, "settings", replace(real, vapid_private_key=None))
    _subscribe(session)
    assert push_service.send_push_to_all(session, "t", "b") == 0


def test_no_subscriptions_is_reported_as_a_warning(session, monkeypatch):
    """"Sent to nobody" is a success by return value and a problem in reality.

    This is what a re-installed PWA looks like, and it is indistinguishable from a
    healthy run unless it is said out loud.

    Captured with a handler attached to the module's own logger rather than with
    `caplog`: `logging_config.configure_logging()` calls `basicConfig(force=True)`,
    which removes every root handler — pytest's capture handler included. Any test
    running after one that configures logging would otherwise see an empty log and
    fail for a reason that has nothing to do with push.
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    previous_level = push_service.LOG.level
    previous_disable = logging.root.manager.disable
    previously_disabled = push_service.LOG.disabled

    # Pin ALL THREE gates that can swallow the record before a handler sees it:
    # the logger's own level, the global `logging.disable` threshold, and the
    # logger's `disabled` flag. Running the whole suite trips the third one — some
    # other module's logging setup marks existing loggers disabled — and the test
    # would then fail for a reason that has nothing to do with push.
    push_service.LOG.setLevel(logging.WARNING)
    push_service.LOG.disabled = False
    logging.disable(logging.NOTSET)
    push_service.LOG.addHandler(handler)
    try:
        monkeypatch.setattr(push_service, "webpush", lambda **kw: None)
        assert push_service.send_push_to_all(session, "t", "b") == 0
    finally:
        push_service.LOG.removeHandler(handler)
        push_service.LOG.setLevel(previous_level)
        push_service.LOG.disabled = previously_disabled
        logging.disable(previous_disable)

    assert any("push_no_subscriptions" in r.getMessage() for r in records)
