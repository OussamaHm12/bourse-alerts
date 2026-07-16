"""Scheduler wiring tests.

The scheduler was previously untested (see AUDIT_TECHNIQUE.md §12) even though it
owns every collection, every report and every notification. These tests pin the
two properties that Priority 1 was about, so neither can regress silently:

  * exactly ONE process sends notifications — no second scheduled runner may
    reappear holding the bot token;
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
    """No CI workflow may send digests again.

    A GitHub Actions cron used to send its own Telegram digests from a throwaway
    SQLite file restored from the Actions cache — a different database, so
    different scores, contradicting the app about the same stock. If a workflow
    ever comes back, it must not carry the bot token.
    """
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github" / "workflows"
    if not workflows.exists():
        return  # nothing to police

    offenders = [
        wf.name
        for wf in workflows.glob("*.yml")
        if "TELEGRAM_BOT_TOKEN" in wf.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} carries TELEGRAM_BOT_TOKEN. The deployed service is the only "
        "sender — a second scheduled runner would notify from a different database."
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


def test_backup_job_alerts_when_the_snapshot_fails(monkeypatch):
    """A silently failing backup is worse than none: it buys false confidence."""
    sent: list[str] = []
    monkeypatch.setattr(sched, "send_telegram_message", lambda msg, **k: sent.append(msg))
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: __import__(
            "moroccan_stock_intelligence.services.backup", fromlist=["BackupResult"]
        ).BackupResult(error="base illisible"),
    )

    sched._backup_job()

    assert len(sent) == 1
    assert "ÉCHOUÉE" in sent[0]
    assert "base illisible" in sent[0]


def test_backup_job_warns_when_the_off_host_copy_did_not_leave(monkeypatch):
    """A local-only backup leaves the actual risk — losing the volume — uncovered."""
    sent: list[str] = []
    monkeypatch.setattr(sched, "send_telegram_message", lambda msg, **k: sent.append(msg))
    module = __import__(
        "moroccan_stock_intelligence.services.backup", fromlist=["BackupResult"]
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: module.BackupResult(
            path=Path("data/backups/market-x.db.gz"),
            size_bytes=1024,
            integrity_ok=True,
            shipped=False,
            ship_error="Archive trop volumineuse",
        ),
    )

    sched._backup_job()

    assert len(sent) == 1
    assert "hors-hôte" in sent[0]


def test_backup_job_is_silent_on_success(monkeypatch):
    """Success must not notify: a nightly 'backup ok' would train the owner to ignore it."""
    sent: list[str] = []
    monkeypatch.setattr(sched, "send_telegram_message", lambda msg, **k: sent.append(msg))
    module = __import__(
        "moroccan_stock_intelligence.services.backup", fromlist=["BackupResult"]
    )
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: module.BackupResult(
            path=Path("data/backups/market-x.db.gz"),
            size_bytes=1024,
            integrity_ok=True,
            shipped=True,
        ),
    )

    sched._backup_job()

    assert sent == []


def test_backup_job_never_raises(monkeypatch):
    """One crashing job must not take the scheduler thread down with it."""
    monkeypatch.setattr(sched, "send_telegram_message", lambda *a, **k: None)
    monkeypatch.setattr(
        "moroccan_stock_intelligence.services.backup.run_backup",
        lambda **k: (_ for _ in ()).throw(RuntimeError("disk on fire")),
    )
    sched._backup_job()  # must not raise
