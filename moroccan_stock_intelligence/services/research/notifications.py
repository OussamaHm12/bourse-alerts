"""Phase 9 — thesis-based notifications.

The old rule was event-based ("volume spiked", "price crashed"), which is noisy:
the same technical event fires again and again while the investment case is
unchanged.

The new rule is **thesis-based**: we notify only when the *conclusion* changes.
A stock can move 4% and generate nothing, because the thesis survived. A stock
can be flat and generate a push, because new evidence broke the case.

Triggers (each deduplicated once per symbol per day via the alerts table, and
hard-capped per run):
  * the recommendation flipped for a horizon        (thesis changed)
  * confidence dropped materially                   (we know less than we did)
  * risk rose materially                            (same view, worse odds)
  * fresh negative news on a HELD position          (the thesis is under attack)

Everything else stays silent. These go to web push + the in-app inbox, like every
other notification in the project.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import Stock
from moroccan_stock_intelligence.repository import (
    create_alert_once,
    load_favorite_symbols,
    load_last_report_before,
    save_notification,
)
from moroccan_stock_intelligence.services.analysts.cio import RECOMMENDATION_LABELS_FR
from moroccan_stock_intelligence.services.research.contracts import (
    HORIZON_LABELS_FR,
    InvestmentReport,
)

LOG = logging.getLogger(__name__)

MAX_PUSHES_PER_RUN = 3
CONFIDENCE_DROP = 15.0  # points — below this it is noise, not news
RISK_RISE = 15.0


def _stock(session: Session, symbol: str) -> Stock | None:
    return session.scalar(select(Stock).where(Stock.symbol == symbol.upper()))


def evaluate_report(
    session: Session, report: InvestmentReport, report_id: int
) -> list[tuple[str, str, str]]:
    """Return (event_key_suffix, title, body) for every genuine thesis change.

    `report_id` is the row we just stored — we must look strictly BEFORE it, or we
    would compare the report against itself and never detect a change.
    """
    events: list[tuple[str, str, str]] = []
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    held = report.analysts.get("portfolio") is not None and any(
        "pèse" in statement.text for statement in report.analysts["portfolio"].observations
    )

    previous = load_last_report_before(
        session, report.symbol, report.horizon_focus, report_id
    )
    if previous is None:
        return events  # first ever report: nothing changed, so nothing to say

    for horizon, verdict in report.cio.verdicts.items():
        before = getattr(previous, f"recommendation_{horizon}", None)
        before_confidence = getattr(previous, f"confidence_{horizon}", None)
        label = HORIZON_LABELS_FR.get(horizon, horizon).lower()

        # 1. The thesis itself changed.
        if before is not None and before != verdict.recommendation:
            events.append(
                (
                    f"thesis-{horizon}-{day}",
                    f"🔄 Thèse modifiée : {report.symbol}",
                    f"{label} : {RECOMMENDATION_LABELS_FR.get(before, before)} → "
                    f"{verdict.recommendation_label} (confiance {verdict.confidence:.0f}/100).",
                )
            )
            continue  # a flip already says everything; don't also send "confidence dropped"

        # 2. Same view, but we are markedly less sure of it.
        if (
            before_confidence is not None
            and before_confidence - verdict.confidence >= CONFIDENCE_DROP
        ):
            events.append(
                (
                    f"confidence-{horizon}-{day}",
                    f"⚠️ Confiance en baisse : {report.symbol}",
                    f"{label} : confiance {before_confidence:.0f} → {verdict.confidence:.0f}/100 "
                    f"({verdict.recommendation_label} maintenu). Informations contradictoires.",
                )
            )

    # 3. Same view, worse odds.
    if previous.risk_score is not None and report.risk.overall_risk - previous.risk_score >= RISK_RISE:
        events.append(
            (
                f"risk-{day}",
                f"📈 Risque en hausse : {report.symbol}",
                f"Risque {previous.risk_score:.0f} → {report.risk.overall_risk:.0f}/100. "
                f"{report.risk.drivers[0].text if report.risk.drivers else ''}",
            )
        )

    # 4. A held position is under attack from fresh news.
    news = report.analysts.get("news")
    if held and news is not None:
        for flag in news.risk_flags:
            if "fraîche" in flag.text or "récente" in flag.text:
                events.append(
                    (
                        f"news-{day}",
                        f"📰 Actualité contraire : {report.symbol}",
                        f"Position détenue — {flag.text}",
                    )
                )
                break
    return events


def _by_attention(
    session: Session, generated: list[tuple[InvestmentReport, int]]
) -> list[tuple[InvestmentReport, int]]:
    """Favorites first, everything else after — order preserved within each group.

    This matters because of MAX_PUSHES_PER_RUN. `generated` arrives in the order the
    symbols were computed (alphabetical), so without this the 3 available push slots
    went to whichever symbols happened to sort first — not to the ones the owner
    actually watches. A thesis change on a favorite must never be crowded out by one
    on a stock he has never looked at.
    """
    try:
        favorites = set(load_favorite_symbols(session))
    # Ordering is an optimisation, never a dependency: on failure we notify in the
    # original order rather than not notifying at all.
    except Exception:  # noqa: BLE001
        LOG.exception("favorite_lookup_failed_using_default_order")
        return generated
    if not favorites:
        return generated
    return sorted(generated, key=lambda item: item[0].symbol not in favorites)


def dispatch_thesis_notifications(
    session: Session, generated: list[tuple[InvestmentReport, int]]
) -> int:
    """Push only genuine thesis changes, favorites first.

    `generated` is (report, stored_report_id) for each report produced this run.
    Never raises: notification is best-effort and must not sink a scheduled job.
    """
    # Imported here so the research package stays independent of the push stack
    # (and importable in environments without the web-push dependencies).
    from moroccan_stock_intelligence.services.push import send_push_to_all

    sent = 0
    for report, report_id in _by_attention(session, generated):
        if sent >= MAX_PUSHES_PER_RUN:
            break
        try:
            stock = _stock(session, report.symbol)
            if stock is None:
                continue
            for suffix, title, body in evaluate_report(session, report, report_id):
                if sent >= MAX_PUSHES_PER_RUN:
                    break
                alert = create_alert_once(
                    session,
                    stock.id,
                    f"{report.symbol}-{suffix}",
                    "thesis_change",
                    f"{title}\n{body}",
                )
                if alert is None:
                    continue  # already told the owner today — no spam
                save_notification(session, "analysis", title, body)
                send_push_to_all(session, title, body, "/")
                alert.sent = 1
                sent += 1
        except Exception:  # noqa: BLE001 - one symbol must not sink the run
            LOG.exception("thesis_notification_failed symbol=%s", report.symbol)
            session.rollback()
    session.commit()
    LOG.info("thesis_notifications_sent count=%s reports=%s", sent, len(generated))
    return sent
