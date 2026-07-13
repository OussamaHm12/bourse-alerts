"""On-open refresh: bring the data up to date when the app is launched.

This is deliberately NOT `_digest_job`. That job collects *and* sends the Telegram
digest and a web push — firing it every time the app opens would notify the owner
several times a day for nothing. This path is silent: it collects, persists, and
recomputes the analysis. Notifications stay owned by the scheduler.

Two guards, because a scrape is not free (one fetch of the Bourse page, then ~80
new price rows, since `observed_at` is the collection instant):

  * **Cooldown.** Casablanca Bourse publishes prices with a stated ~15 min delay, so
    re-scraping faster than that returns data we already have. Below
    `app_refresh_cooldown_seconds` we report the data as fresh and skip the fetch.
    An explicit user action (the Actualiser button) may `force=True` past it.
  * **Single-flight.** `try_begin()` claims the slot SYNCHRONOUSLY, before the
    endpoint returns. FastAPI runs background tasks *after* the response is sent, so
    claiming inside the task would leave a window where the app polls, sees
    `running=False`, and concludes the refresh had already finished.

State lives in this module (one web process). It is a UI convenience, not a source
of truth: the truth is the newest row in `prices`, which is what `data_age` reads.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.repository import latest_price_observed_at

LOG = logging.getLogger(__name__)

# A collection takes ~30 s. Well past that, the run is presumed dead (worker killed
# mid-task, for instance) and the slot is released — a crashed refresh must never
# wedge the app into "updating…" forever.
STUCK_AFTER_SECONDS = 300

_guard = threading.Lock()


class RefreshState:
    """What the last (or current) app-triggered refresh is doing."""

    def __init__(self) -> None:
        self._running = False
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.snapshots = 0
        self.error: str | None = None

    @property
    def running(self) -> bool:
        if not self._running:
            return False
        if self.started_at is None:
            return True
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        return elapsed <= STUCK_AFTER_SECONDS

    def try_begin(self) -> bool:
        """Claim the single-flight slot. False when a refresh is already in flight."""
        with _guard:
            if self.running:
                return False
            self._running = True
            self.started_at = datetime.now(UTC)
            self.error = None
            return True

    def end(self, snapshots: int = 0, error: str | None = None) -> None:
        with _guard:
            self._running = False
            self.finished_at = datetime.now(UTC)
            self.snapshots = snapshots
            self.error = error


STATE = RefreshState()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def data_age_seconds(session: Session) -> float | None:
    """Seconds since the market was last collected. None if it never was."""
    latest = _aware(latest_price_observed_at(session))
    if latest is None:
        return None
    return max(0.0, (datetime.now(UTC) - latest).total_seconds())


def is_stale(session: Session) -> bool:
    """True when the data is old enough that a re-scrape would actually return something."""
    age = data_age_seconds(session)
    return age is None or age >= settings.app_refresh_cooldown_seconds


def refresh_market_data(session_factory) -> dict:  # noqa: ANN001
    """Collect + persist + recompute. Silent: no Telegram, no push.

    Runs the slot already claimed by `STATE.try_begin()`, and always releases it —
    including on failure, so one bad scrape does not block every later refresh.
    """
    # Imported here so this module stays importable without the scraping stack.
    from moroccan_stock_intelligence.cli import run_analysis
    from moroccan_stock_intelligence.services.collector import (
        collect_market_snapshots,
        persist_snapshots,
    )

    try:
        with session_factory() as session:
            stored = persist_snapshots(session, collect_market_snapshots())
            metrics = run_analysis(session)["metrics"]
        STATE.end(snapshots=stored)
        LOG.info("refresh_done snapshots=%s metrics=%s", stored, len(metrics))  # type: ignore[arg-type]
        return {"status": "done", "snapshots": stored}
    except Exception as exc:  # noqa: BLE001 - a failed refresh must not 500 the app
        LOG.exception("refresh_failed")
        STATE.end(error=f"{type(exc).__name__}: {exc}")
        return {"status": "error", "error": str(exc)}


def status_payload(session: Session) -> dict:
    """What the app polls while it waits, and reads to decide whether to reload."""
    latest = _aware(latest_price_observed_at(session))
    return {
        "running": STATE.running,
        "as_of": latest.isoformat() if latest else None,
        "data_age_seconds": data_age_seconds(session),
        "stale": is_stale(session),
        "cooldown_seconds": settings.app_refresh_cooldown_seconds,
        "last_finished_at": STATE.finished_at.isoformat() if STATE.finished_at else None,
        "last_snapshots": STATE.snapshots,
        "last_error": STATE.error,
    }
