"""Is the data actually there, and is it recent enough to trust?

WHY
---
The audit found that on the inspected database `fundamentals`, `company_profiles`
and `macro_indicators` were all empty, which silently reduces three of the ten
analysts to "données non collectées" (AUDIT_2026-07-18.md §4). Nothing surfaced
that. The collectors log their tallies and move on, so an issuer sweep that has
been failing every Sunday for a month looks exactly like one that ran fine.

The failure mode this guards against is not a crash — it is a scraper that
returns 200 with an empty list, or a weekly job that quietly stopped. Both leave a
platform that keeps drawing charts from data that stopped moving.

WHAT "HEALTHY" MEANS
--------------------
Each domain declares how long it may go without an update before that is
suspicious, derived from its own collection cadence rather than a global rule: the
prices feed runs five times a trading day, macro daily, issuers weekly. A feed is
judged against its own promise.

Nothing here writes; it is safe to call from a request handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import (
    AnalysisReport,
    CompanyKnowledge,
    CompanyProfile,
    Fundamental,
    MacroIndicator,
    News,
    PredictionHistory,
    Price,
    Stock,
)

OK = "ok"
STALE = "stale"
EMPTY = "empty"
DEGRADED = "degraded"

# Order matters for the report: worst first is the wrong choice here, because the
# reader wants a consistent layout they can scan, not a leaderboard of problems.
_SEVERITY = {OK: 0, DEGRADED: 1, STALE: 2, EMPTY: 3}


@dataclass
class DomainHealth:
    """One feed's state. `coverage` is the share of tracked symbols it reaches."""

    domain: str
    rows: int
    last_collected: datetime | None
    max_age: timedelta
    status: str
    coverage: float | None = None
    detail: str = ""

    @property
    def age(self) -> timedelta | None:
        if self.last_collected is None:
            return None
        stamp = (
            self.last_collected
            if self.last_collected.tzinfo
            else self.last_collected.replace(tzinfo=UTC)
        )
        return datetime.now(UTC) - stamp

    @property
    def age_label(self) -> str:
        age = self.age
        if age is None:
            return "jamais"
        if age < timedelta(hours=1):
            return f"{int(age.total_seconds() // 60)} min"
        if age < timedelta(days=1):
            return f"{int(age.total_seconds() // 3600)} h"
        return f"{age.days} j"

    def as_dict(self) -> dict:
        return {
            "domain": self.domain,
            "rows": self.rows,
            "last_collected": self.last_collected.isoformat() if self.last_collected else None,
            "age": self.age_label,
            "max_age_hours": round(self.max_age.total_seconds() / 3600, 1),
            "coverage": self.coverage,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class HealthReport:
    domains: list[DomainHealth] = field(default_factory=list)
    analysts_degraded: list[str] = field(default_factory=list)
    tracked_symbols: int = 0

    @property
    def worst(self) -> str:
        return max((d.status for d in self.domains), key=lambda s: _SEVERITY[s], default=OK)

    @property
    def healthy(self) -> bool:
        return self.worst in (OK, DEGRADED)

    def as_dict(self) -> dict:
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "status": self.worst,
            "healthy": self.healthy,
            "tracked_symbols": self.tracked_symbols,
            "domains": [d.as_dict() for d in self.domains],
            "analysts_degraded": self.analysts_degraded,
        }


def _classify(rows: int, last: datetime | None, max_age: timedelta) -> str:
    if rows == 0:
        return EMPTY
    if last is None:
        # Rows exist but carry no timestamp — we cannot vouch for their freshness.
        return DEGRADED
    stamp = last if last.tzinfo else last.replace(tzinfo=UTC)
    return STALE if datetime.now(UTC) - stamp > max_age else OK


def check(session: Session) -> HealthReport:
    """Inspect every feed. Read-only."""
    tracked = session.scalar(select(func.count()).select_from(Stock)) or 0
    report = HealthReport(tracked_symbols=tracked)

    def add(
        domain: str,
        model,  # noqa: ANN001
        stamp_column,  # noqa: ANN001
        max_age: timedelta,
        *,
        symbol_column=None,  # noqa: ANN001
        detail: str = "",
    ) -> DomainHealth:
        rows = session.scalar(select(func.count()).select_from(model)) or 0
        last = session.scalar(select(func.max(stamp_column))) if rows else None
        coverage = None
        if symbol_column is not None and tracked:
            distinct = (
                session.scalar(select(func.count(func.distinct(symbol_column)))) or 0
            )
            coverage = round(distinct / tracked, 3)
        health = DomainHealth(
            domain=domain,
            rows=rows,
            last_collected=last,
            max_age=max_age,
            status=_classify(rows, last, max_age),
            coverage=coverage,
            detail=detail,
        )
        report.domains.append(health)
        return health

    # Prices: five collections per trading day, so a full weekend plus a holiday
    # is the longest legitimate silence.
    add(
        "Cours",
        Price,
        Price.observed_at,
        timedelta(days=4),
        symbol_column=Price.stock_id,
        detail="Collecte 5x/jour ouvré (09:00, 11:00, 13:00, 15:00, 17:00).",
    )
    # Notices are published irregularly; two weeks without one is plausible but
    # worth surfacing.
    add(
        "Actualités",
        News,
        News.collected_at,
        timedelta(days=14),
        detail="Avis officiels de la Bourse de Casablanca, collectés avec les digests.",
    )
    fundamentals = add(
        "Fondamentaux",
        Fundamental,
        Fundamental.collected_at,
        timedelta(days=14),
        symbol_column=Fundamental.stock_id,
        detail="Balayage hebdomadaire des pages émetteurs (dimanche 03:00).",
    )
    profiles = add(
        "Profils société",
        CompanyProfile,
        CompanyProfile.updated_at,
        timedelta(days=14),
        symbol_column=CompanyProfile.stock_id,
        detail="Même page que les fondamentaux, même cadence.",
    )
    macro = add(
        "Macro (BAM)",
        MacroIndicator,
        MacroIndicator.collected_at,
        timedelta(days=4),
        detail="Bank Al-Maghrib, collecte quotidienne 07:30.",
    )
    add(
        "Rapports",
        AnalysisReport,
        AnalysisReport.generated_at,
        timedelta(days=4),
        symbol_column=AnalysisReport.stock_id,
        detail="Génération multi-analystes, jours ouvrés 18:00.",
    )
    add(
        "Connaissance",
        CompanyKnowledge,
        CompanyKnowledge.last_seen,
        timedelta(days=14),
        symbol_column=CompanyKnowledge.stock_id,
        detail="Faits dédupliqués, moissonnés le dimanche 04:30.",
    )

    # Predictions are judged on maturation, not freshness: rows accumulate quickly
    # and then wait weeks to be gradable, so "recent" says nothing useful.
    total_predictions = session.scalar(select(func.count()).select_from(PredictionHistory)) or 0
    evaluated = (
        session.scalar(
            select(func.count())
            .select_from(PredictionHistory)
            .where(PredictionHistory.evaluated_at.is_not(None))
        )
        or 0
    )
    report.domains.append(
        DomainHealth(
            domain="Prédictions",
            rows=total_predictions,
            last_collected=session.scalar(select(func.max(PredictionHistory.generated_at)))
            if total_predictions
            else None,
            max_age=timedelta(days=7),
            status=_classify(
                total_predictions,
                session.scalar(select(func.max(PredictionHistory.generated_at)))
                if total_predictions
                else None,
                timedelta(days=7),
            ),
            detail=(
                f"{evaluated} évaluée(s) sur {total_predictions}. "
                "Une prédiction n'est notable qu'à son échéance (10/60/180 j)."
            ),
        )
    )

    # The point of the whole exercise: name the analysts that are running blind,
    # instead of leaving the owner to infer it from a report full of "données non
    # collectées".
    if fundamentals.status in (EMPTY, STALE):
        report.analysts_degraded.append("fundamental")
    if profiles.status in (EMPTY, STALE):
        report.analysts_degraded.append("company")
    if macro.status in (EMPTY, STALE):
        report.analysts_degraded.append("macro")

    return report


def render(report: HealthReport) -> str:
    """A fixed-width table for a terminal. Deliberately not JSON: this is what a
    human runs when something looks wrong."""
    icons = {OK: "OK  ", STALE: "VIEUX", EMPTY: "VIDE", DEGRADED: "PARTIEL"}
    lines = [
        "État des données — Moroccan Stock Intelligence",
        f"Titres suivis : {report.tracked_symbols}",
        "",
        f"{'Domaine':<18}{'Lignes':>9}{'Dernière':>12}{'Couverture':>12}  État",
        "-" * 66,
    ]
    for domain in report.domains:
        coverage = "—" if domain.coverage is None else f"{domain.coverage * 100:.0f}%"
        lines.append(
            f"{domain.domain:<18}{domain.rows:>9}{domain.age_label:>12}{coverage:>12}  "
            f"{icons[domain.status]}"
        )
    lines.append("")
    for domain in report.domains:
        if domain.status != OK:
            lines.append(f"  · {domain.domain} : {domain.detail}")

    if report.analysts_degraded:
        lines += [
            "",
            "Analystes dégradés (ils rendront « données non collectées ») :",
            "  " + ", ".join(report.analysts_degraded),
        ]
    else:
        lines += ["", "Tous les analystes disposent de leurs données."]
    return "\n".join(lines)
