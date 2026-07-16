"""One-off reclassification of notices already stored in the `news` table.

`repository.store_news` dedups on URL and returns early when the row exists — it
never updates. So every notice collected before `news_classifier` landed keeps the
verdict the old keyword model gave it (9 rows scored a flat +0.6, including
mechanical ex-dividend detachments), and those rows stay inside the 30-day window
that feeds `NewsContext`. Wiring `news_sentiment_score` into the scoring engine
while they sit there would pipe the *old* bug into the *newly* connected path.

This pass re-runs the current classifier over the stored titles and rewrites only
the three derived columns: `event_type`, `sentiment`, `impact_score`. The title,
URL, source, dates and `stock_id` are read-only here — the classifier derives from
the title, so re-deriving must never touch the thing it derives from.

**Idempotent by construction.** The classifier is a pure function of the title, so
a second run recomputes identical values, finds no differences, and writes nothing.
That property is also what makes per-batch commits safe: a run that dies halfway
leaves earlier batches committed and correct, and re-running simply finishes the
job. There is no ordering or accumulation to corrupt.

**Dry-run is the default** and is enforced in two places: the caller must pass
`apply=True` to mutate anything, and a dry run additionally rolls back before
returning, so no write can escape even if a future edit introduces one.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import News
from moroccan_stock_intelligence.services.news_classifier import classify

LOG = logging.getLogger(__name__)

BATCH_SIZE = 500

# Impact is a float column; compare with a tolerance rather than `!=` so a value
# that merely round-tripped through the DB is not reported as a change.
_IMPACT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class RowChange:
    """One row whose derived columns the current classifier disagrees with."""

    news_id: int
    title: str
    old_event_type: str | None
    new_event_type: str
    old_sentiment: str | None
    new_sentiment: str
    old_impact: float | None
    new_impact: float


@dataclass
class BackfillReport:
    scanned: int = 0
    applied: bool = False
    batches_committed: int = 0
    changes: list[RowChange] = field(default_factory=list)
    before_events: Counter = field(default_factory=Counter)
    after_events: Counter = field(default_factory=Counter)
    before_sentiments: Counter = field(default_factory=Counter)
    after_sentiments: Counter = field(default_factory=Counter)

    @property
    def changed(self) -> int:
        return len(self.changes)

    @property
    def unchanged(self) -> int:
        return self.scanned - self.changed


def _impact_differs(old: float | None, new: float) -> bool:
    if old is None:
        return True
    return abs(old - new) > _IMPACT_TOLERANCE


def _iter_batches(session: Session, batch_size: int) -> Iterator[list[News]]:
    """Keyset pagination on the primary key.

    Stable across the commits an `apply` run interleaves — an OFFSET walk is not,
    because a commit can shift rows under the cursor.
    """
    last_id = 0
    while True:
        rows = list(
            session.scalars(
                select(News).where(News.id > last_id).order_by(News.id).limit(batch_size)
            ).all()
        )
        if not rows:
            return
        yield rows
        last_id = rows[-1].id


def reclassify_news(
    session: Session,
    *,
    apply: bool = False,
    batch_size: int = BATCH_SIZE,
) -> BackfillReport:
    """Re-derive event/sentiment/impact for stored notices.

    Returns the full plan whether or not it is applied, so the dry run and the real
    run report exactly the same thing. Raises after a rollback if anything fails.
    """
    report = BackfillReport(applied=apply)
    try:
        for batch in _iter_batches(session, batch_size):
            touched = False
            for row in batch:
                verdict = classify(row.title)
                report.scanned += 1
                report.before_events[row.event_type or "—"] += 1
                report.after_events[verdict.event_type] += 1
                report.before_sentiments[row.sentiment or "—"] += 1
                report.after_sentiments[verdict.sentiment] += 1

                if (
                    row.event_type == verdict.event_type
                    and row.sentiment == verdict.sentiment
                    and not _impact_differs(row.impact_score, verdict.impact_score)
                ):
                    continue

                report.changes.append(
                    RowChange(
                        news_id=row.id,
                        title=row.title,
                        old_event_type=row.event_type,
                        new_event_type=verdict.event_type,
                        old_sentiment=row.sentiment,
                        new_sentiment=verdict.sentiment,
                        old_impact=row.impact_score,
                        new_impact=verdict.impact_score,
                    )
                )
                if apply:
                    # Derived columns only. Title/url/source/dates/stock_id are the
                    # inputs to this derivation and must survive it untouched.
                    row.event_type = verdict.event_type
                    row.sentiment = verdict.sentiment
                    row.impact_score = verdict.impact_score
                    touched = True

            if apply and touched:
                session.commit()
                report.batches_committed += 1
    except Exception:
        session.rollback()
        LOG.exception("news_reclassify_failed rolled_back=true scanned=%s", report.scanned)
        raise

    if not apply:
        # Nothing above mutates in dry-run; this is the guarantee, not the mechanism.
        session.rollback()

    LOG.info(
        "news_reclassify_done applied=%s scanned=%s changed=%s batches=%s",
        apply,
        report.scanned,
        report.changed,
        report.batches_committed,
    )
    return report


def _counter_line(before: Counter, after: Counter) -> list[str]:
    lines = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key, 0), after.get(key, 0)
        delta = now - was
        arrow = f"{delta:+d}" if delta else "="
        lines.append(f"    {key:<26} {was:>3} → {now:>3}  ({arrow})")
    return lines


def render_report(report: BackfillReport, *, max_rows: int = 50) -> str:
    """Human-readable rendering. Same shape for a dry run and a real run."""
    mode = "APPLIQUÉ (écriture en base)" if report.applied else "DRY-RUN (aucune écriture)"
    out = [
        "",
        f"  Reclassification des avis — {mode}",
        "  " + "─" * 78,
        f"    Lignes analysées   : {report.scanned}",
        f"    Lignes modifiées   : {report.changed}",
        f"    Lignes inchangées  : {report.unchanged}",
    ]
    if report.applied:
        out.append(f"    Lots committés     : {report.batches_committed}")

    if report.changes:
        out += ["", "  Détail des changements", "  " + "─" * 78]
        for change in report.changes[:max_rows]:
            old_impact = "—" if change.old_impact is None else f"{change.old_impact:+.2f}"
            out += [
                f"    #{change.news_id} {change.title[:66]}",
                f"        avant : {str(change.old_event_type):<24} "
                f"{str(change.old_sentiment):<9} {old_impact}",
                f"        après : {change.new_event_type:<24} "
                f"{change.new_sentiment:<9} {change.new_impact:+.2f}",
            ]
        if report.changed > max_rows:
            out.append(f"    … et {report.changed - max_rows} autre(s) ligne(s).")
    else:
        out += ["", "    Aucun changement : la base est déjà cohérente avec le classificateur."]

    out += ["", "  Agrégat par event_type", "  " + "─" * 78]
    out += _counter_line(report.before_events, report.after_events)
    out += ["", "  Agrégat par sentiment", "  " + "─" * 78]
    out += _counter_line(report.before_sentiments, report.after_sentiments)

    if not report.applied and report.changes:
        out += ["", "  Rien n'a été écrit. Relancer avec --apply pour appliquer.", ""]
    return "\n".join(out)
