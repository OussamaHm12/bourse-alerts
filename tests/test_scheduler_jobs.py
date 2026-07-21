"""Scheduler wiring tests.

The scheduler was previously untested (see AUDIT_TECHNIQUE.md §12) even though it
owns every collection, every report and every notification. These tests pin the
two properties that Priority 1 was about, so neither can regress silently:

  * exactly ONE process sends notifications — no second scheduled runner may
    reappear holding a notification secret;
  * the database is backed up on a schedule, and a failing backup is loud.

Job *logic* is tested in the modules that own it; what is asserted here is the
registration contract — that the jobs exist, at the times the README documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moroccan_stock_intelligence import scheduler as sched


@pytest.fixture
def jobs():
    """Build the real scheduler and index its jobs by id.

    Never started, so there is nothing to shut down and no job can fire during
    the suite — `get_jobs()` reports the pending registrations either way.
    """
    built = sched.build_scheduler(session_factory=lambda: None)
    return {job.id: job for job in built.get_jobs()}


# --------------------------------------------------------------------------- #
# One source of truth for notifications.                                       #
# --------------------------------------------------------------------------- #


def test_no_second_scheduled_notifier_exists_in_the_repo():
    """No CI workflow may send notifications again.

    A GitHub Actions cron used to send its own digests from a throwaway SQLite
    file restored from the Actions cache — a different database, so different
    scores, contradicting the app about the same stock.

    The channel that failure happened on (Telegram) is gone, but the failure mode
    is not: it belongs to *any* second scheduled sender. So the guard now polices
    the credential that would enable one today — the VAPID private key behind web
    push — plus the old bot token, so an old workflow restored from history still
    trips it.
    """
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return  # nothing to police

    forbidden = ("VAPID_PRIVATE_KEY", "TELEGRAM_BOT_TOKEN")
    offenders = [
        f"{wf.name} ({secret})"
        for wf in workflows.glob("*.yml")
        for secret in forbidden
        if secret in wf.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} carries a notification secret. The deployed service is the "
        "only sender — a second scheduled runner would notify from a different "
        "database."
    )


def test_exactly_one_digest_job_per_market_bookend(jobs):
    digest_jobs = [job_id for job_id in jobs if "digest" in job_id]
    assert sorted(digest_jobs) == ["closing_digest", "morning_digest"]


def test_digests_fire_at_the_documented_times(jobs):
    """09:00 / 17:00 Africa/Casablanca — the times the README now states."""
    assert str(jobs["morning_digest"].trigger.fields[jobs["morning_digest"].trigger.FIELD_NAMES.index("hour")]) == "9"
    assert str(jobs["closing_digest"].trigger.fields[jobs["closing_digest"].trigger.FIELD_NAMES.index("hour")]) == "17"


# --------------------------------------------------------------------------- #
# Backups.                                                                     #
# --------------------------------------------------------------------------- #


def test_a_backup_job_is_registered(jobs):
    assert "database_backup" in jobs, "the database is the only unrecoverable asset"


def test_backup_runs_daily_after_the_last_writing_job(jobs):
    """22:00: the last writer is the 18:00 research run, so a snapshot holds a full day."""
    trigger = jobs["database_backup"].trigger
    hour = trigger.fields[trigger.FIELD_NAMES.index("hour")]
    day_of_week = trigger.fields[trigger.FIELD_NAMES.index("day_of_week")]
    assert str(hour) == "22"
    assert day_of_week.is_default, "backups must run at weekends too"


class _FakeSession:
    """Minimal stand-in: the backup job only opens a session to report a failure."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_factory():
    return _FakeSession()


def _backup_result(**kwargs):
    module = __import__(
        "moroccan_stock_intelligence.services.backup", fromlist=["BackupResult"]
    )
    return module.BackupResult(**kwargs)


def test_backup_job_alerts_when_the_snapshot_fails(monkeypatch):
    """A silently failing backup is worse than none: it buys false confidence."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(sched, "save_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        sched, "send_push_to_all", lambda s, title, body, url="/": sent.append((title, body))
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: _backup_result(error="base illisible"),
    )

    sched._backup_job(_fake_factory)

    assert len(sent) == 1
    title, body = sent[0]
    assert "ÉCHOUÉE" in title
    assert "base illisible" in body


def test_backup_job_is_silent_on_success(monkeypatch):
    """Success must not notify: a nightly 'backup ok' would train the owner to ignore it."""
    sent: list = []
    monkeypatch.setattr(sched, "save_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        sched, "send_push_to_all", lambda *a, **k: sent.append(a)
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: _backup_result(
            path=Path("data/backups/market-x.db.gz"),
            size_bytes=1024,
            integrity_ok=True,
        ),
    )

    sched._backup_job(_fake_factory)

    assert sent == []


def test_backup_job_reports_a_skip_too(monkeypatch):
    """A skipped backup is still a day without a verified copy — say so.

    `skipped_reason` means "we did not try" (wrong backend, missing file). That is
    normal as a category but not as an outcome: the owner still has no snapshot
    for today, which is the thing worth knowing.
    """
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(sched, "save_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        sched, "send_push_to_all", lambda s, title, body, url="/": sent.append((title, body))
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: _backup_result(skipped_reason="La base n'est pas SQLite"),
    )

    sched._backup_job(_fake_factory)

    assert len(sent) == 1
    assert "SQLite" in sent[0][1]


def test_backup_job_survives_a_failing_notification(monkeypatch):
    """Reporting the failure must not become a second, louder failure."""
    monkeypatch.setattr(sched, "save_notification", lambda *a, **k: None)
    monkeypatch.setattr(
        sched,
        "send_push_to_all",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push endpoint down")),
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: _backup_result(error="base illisible"),
    )

    sched._backup_job(_fake_factory)  # must not raise


def test_backup_job_never_raises(monkeypatch):
    """One crashing job must not take the scheduler thread down with it."""
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: (_ for _ in ()).throw(RuntimeError("disk on fire")),
    )
    sched._backup_job(_fake_factory)  # must not raise
