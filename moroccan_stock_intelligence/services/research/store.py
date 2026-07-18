"""Phase 2 + Phase 5 — report persistence, the cache, and the investment memory.

Persisting a report does three things at once, which is why they live together:

  1. **Research database** — the full InvestmentReport JSON is stored verbatim, so
     any past report can be replayed exactly as it was written (`engine_version`
     pins the logic that produced it).
  2. **Cache** — the stored row IS the cache. `/api/report/{symbol}` serves the
     newest matching row unless it is stale or `?fresh=true` is passed.
  3. **Investment memory** — the new report's `thesis_hash` is compared with the
     previous one for that horizon. A different recommendation means the thesis
     changed, and we record WHY: which evidence is new, which assumptions the
     market invalidated, how confidence and risk moved.

Predictions are extracted here too: every horizon verdict is a falsifiable claim,
and each analyst's directional lean is a claim of its own, which is what allows
per-analyst accuracy to be measured later (Phase 3).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from moroccan_stock_intelligence.models import AnalysisReport, Stock
from moroccan_stock_intelligence.repository import (
    load_last_report_before,
    load_thesis_changes,
    record_thesis_change,
    save_analysis_report,
    save_prediction,
)
from moroccan_stock_intelligence.services.research.contracts import (
    HORIZONS,
    InvestmentReport,
    report_to_dict,
    thesis_hash,
)
from moroccan_stock_intelligence.services.research.learning import evaluation_date

LOG = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Prediction semantics v2                                                       #
# --------------------------------------------------------------------------- #
#
# v1 said: BULLISH = {STRONG_OPPORTUNITY, WATCH, HOLD} -> "up".
#
# WATCH means "no direction dominates; wait for confirmation" and is by far the
# most common verdict — it covers the whole 45-70 score band. Recording it as a
# bullish bet meant the engine mass-produced "up" calls nobody had made, and any
# hit rate computed from them would have measured how often the Casablanca market
# rises, not whether this platform is right (AUDIT_2026-07-18.md §9).
#
# v2 maps each verdict to what it actually asserts. "flat" is a real, falsifiable
# claim here — the evaluator's flat band is |return| < FLAT_RETURN_PCT — so
# WATCH and HOLD are scored, not excluded. What IS excluded from directional
# scoring is the pair that genuinely asserts something else:
#
#   RISKY       — a statement about volatility and drawdown, not direction. A
#                 risky stock that rises has not falsified it.
#   TAKE_PROFIT — an instruction about an existing position, conditional on a
#                 gain already realised. It is not a forecast at all.
#
# Both are still recorded, under their own claim_kind, so the rows exist when
# there is a way to grade them (realised volatility is already stored). Silently
# scoring them as "down" would have been the easy choice and the wrong one.
from moroccan_stock_intelligence.models import CURRENT_SEMANTICS_VERSION as SEMANTICS_VERSION

CLAIM_DIRECTION = "direction"
CLAIM_STABILITY = "stability"
CLAIM_ACTION = "action"


@dataclass(frozen=True)
class Claim:
    direction: str | None
    kind: str


_VERDICT_CLAIMS: dict[str, Claim] = {
    "STRONG_OPPORTUNITY": Claim("up", CLAIM_DIRECTION),
    "WATCH": Claim("flat", CLAIM_DIRECTION),
    "HOLD": Claim("flat", CLAIM_DIRECTION),
    "AVOID": Claim("down", CLAIM_DIRECTION),
    "RISKY": Claim(None, CLAIM_STABILITY),
    "TAKE_PROFIT": Claim(None, CLAIM_ACTION),
}

# The most a score alone may claim. 0.75 for a maximal score, because a
# single-market technical model with no validated edge asserting more than that
# would be a statement about the model's ego rather than the data.
MAX_EDGE = 0.25


# Derived from the table above rather than maintained separately: these are what
# `_explain_change` uses to decide which side of the case became the driver when a
# thesis flipped. Two hand-kept sets would be two things to forget to update.
BULLISH = frozenset(
    name for name, claim in _VERDICT_CLAIMS.items() if claim.direction == "up"
)
BEARISH = frozenset(
    name
    for name, claim in _VERDICT_CLAIMS.items()
    if claim.direction == "down" or claim.kind in (CLAIM_STABILITY, CLAIM_ACTION)
)


def _claim_for(recommendation: str) -> Claim:
    return _VERDICT_CLAIMS.get(recommendation, Claim("flat", CLAIM_DIRECTION))


def _signal_strength(score: float | None) -> float:
    """How far from "no opinion" the score itself sits, 0..1."""
    if score is None:
        return 0.0
    return min(1.0, abs(score - 50.0) / 50.0)


def _probability(signal_strength: float, data_confidence: float | None) -> float:
    """Probability that the stated direction happens.

    v1 computed this straight from `confidence`, which measures DATA COVERAGE —
    50% of it is the share of indicators available, 30% history depth, 20% signal
    agreement. It says nothing about whether the direction will occur. A stock with
    three complete years of history scored 0.82 regardless of how weak its setup
    was, and the Brier score built on that measured the calibration of a coverage
    metric reinterpreted as a probability: arithmetically correct, semantically
    meaningless.

    v2 separates the two:

        edge        = signal_strength x MAX_EDGE      (what the score claims)
        probability = 0.5 + edge x (data_confidence)  (discounted by what we know)

    Thin data therefore pulls the probability toward a coin flip instead of
    inflating it, which is the correct direction for both terms.

    This is deliberately UNCALIBRATED. It is a prior, and the honest source for a
    real probability is the empirical hit rate per score band, which is what the
    backtest produces. Until then, no claim of calibration is made anywhere.
    """
    coverage = 0.5 if data_confidence is None else max(0.0, min(1.0, data_confidence / 100.0))
    probability = 0.5 + (signal_strength * MAX_EDGE * coverage)
    return round(min(0.9, max(0.1, probability)), 4)


def _lean_direction(lean: float) -> str:
    if lean >= 58:
        return "up"
    if lean <= 42:
        return "down"
    return "flat"


def persist_report(session: Session, report: InvestmentReport) -> AnalysisReport | None:
    """Store the report, record its predictions, and detect a thesis change.

    Never raises: persistence must not break report delivery. If the store fails,
    the caller still has a perfectly good in-memory report to serve.
    """
    try:
        stock = session.scalar(select(Stock).where(Stock.symbol == report.symbol.upper()))
        if stock is None:
            LOG.warning("report_persist_unknown_symbol symbol=%s", report.symbol)
            return None

        verdicts = {
            horizon: {
                "recommendation": verdict.recommendation,
                "confidence": verdict.confidence,
            }
            for horizon, verdict in report.cio.verdicts.items()
        }
        row = save_analysis_report(
            session,
            stock_id=stock.id,
            symbol=report.symbol,
            horizon_focus=report.horizon_focus,
            engine_version=report.engine_version,
            thesis_hash=thesis_hash(report),
            report_json=json.dumps(report_to_dict(report), ensure_ascii=False),
            verdicts=verdicts,
            risk_score=report.risk.overall_risk,
            price_at_report=price_at(report),
            narrative=report.narrative,
        )
        _record_predictions(session, row, report, stock.id)
        _detect_thesis_change(session, row, report, stock.id)
        session.commit()
        return row
    except Exception:  # noqa: BLE001 - storage must never break delivery
        LOG.exception("report_persist_failed symbol=%s", report.symbol)
        session.rollback()
        return None


def price_at(report: InvestmentReport) -> float | None:
    """The price the report was written against — the anchor every prediction is
    later measured from."""
    technical = report.analysts.get("technical")
    if technical is None:
        return None
    for statement in technical.observations:
        price = statement.evidence.get("price")
        if price is not None:
            return float(price)
    return None


def _record_predictions(
    session: Session, row: AnalysisReport, report: InvestmentReport, stock_id: int
) -> None:
    """Every verdict and every analyst lean becomes a falsifiable, dated claim."""
    generated = row.generated_at or datetime.now(UTC)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=UTC)
    price = row.price_at_report

    for horizon in HORIZONS:
        verdict = report.cio.verdicts.get(horizon)
        if verdict is None:
            continue
        claim = _claim_for(verdict.recommendation)
        strength = _signal_strength(verdict.score)
        save_prediction(
            session,
            report_id=row.id,
            stock_id=stock_id,
            symbol=report.symbol,
            analyst="cio",
            horizon=horizon,
            scenario="direction",
            generated_at=generated,
            evaluate_at=evaluation_date(generated, horizon),
            engine_version=report.engine_version,
            predicted_direction=claim.direction,
            predicted_probability=_probability(strength, verdict.confidence),
            stated_confidence=verdict.confidence,
            price_at_prediction=price,
            semantics_version=SEMANTICS_VERSION,
            claim_kind=claim.kind,
            signal_strength=round(strength, 4),
            data_confidence=verdict.confidence,
        )

        # Each analyst's own directional lean — this is what makes per-analyst
        # accuracy measurable rather than only the CIO's. A lean IS a direction by
        # construction, so these are always directional claims.
        for name, analyst_report in report.analysts.items():
            lean = analyst_report.lean_for(horizon)
            if lean is None or analyst_report.confidence < 20:
                continue  # an analyst with no data made no claim
            lean_strength = _signal_strength(lean)
            save_prediction(
                session,
                report_id=row.id,
                stock_id=stock_id,
                symbol=report.symbol,
                analyst=name,
                horizon=horizon,
                scenario="direction",
                generated_at=generated,
                evaluate_at=evaluation_date(generated, horizon),
                engine_version=report.engine_version,
                predicted_direction=_lean_direction(lean),
                predicted_probability=_probability(lean_strength, analyst_report.confidence),
                stated_confidence=analyst_report.confidence,
                price_at_prediction=price,
                semantics_version=SEMANTICS_VERSION,
                claim_kind=CLAIM_DIRECTION,
                signal_strength=round(lean_strength, 4),
                data_confidence=analyst_report.confidence,
            )


def _detect_thesis_change(
    session: Session, row: AnalysisReport, report: InvestmentReport, stock_id: int
) -> list[str]:
    """Compare each horizon with the previous report; record real flips only."""
    previous = load_last_report_before(session, report.symbol, report.horizon_focus, row.id)
    if previous is None:
        return []  # first report for this symbol: nothing to compare against

    changed: list[str] = []
    for horizon in HORIZONS:
        verdict = report.cio.verdicts.get(horizon)
        if verdict is None:
            continue
        before = getattr(previous, f"recommendation_{horizon}", None)
        if before is None or before == verdict.recommendation:
            continue  # same thesis — not a change, and NOT a notification

        before_confidence = getattr(previous, f"confidence_{horizon}", None)
        new_evidence, invalidated = _explain_change(report, before, verdict.recommendation)
        reason = _change_reason(
            report, horizon, before, verdict.recommendation,
            before_confidence, verdict.confidence, previous.risk_score, report.risk.overall_risk,
        )
        record_thesis_change(
            session,
            stock_id=stock_id,
            symbol=report.symbol,
            horizon=horizon,
            previous_report_id=previous.id,
            report_id=row.id,
            from_recommendation=before,
            to_recommendation=verdict.recommendation,
            from_confidence=before_confidence,
            to_confidence=verdict.confidence,
            from_risk=previous.risk_score,
            to_risk=report.risk.overall_risk,
            reason=reason,
            new_evidence_json=json.dumps(new_evidence, ensure_ascii=False),
            invalidated_json=json.dumps(invalidated, ensure_ascii=False),
        )
        changed.append(horizon)
        LOG.info(
            "thesis_changed symbol=%s horizon=%s from=%s to=%s",
            report.symbol, horizon, before, verdict.recommendation,
        )
    return changed


def _explain_change(
    report: InvestmentReport, before: str, after: str
) -> tuple[list[str], list[str]]:
    """What is now true that wasn't, and what assumption stopped holding."""
    turned_bearish = before in BULLISH and after in BEARISH
    driver_pool = report.cio.bear_case if turned_bearish else report.cio.bull_case
    new_evidence = [s.text for s in driver_pool[:4]]

    invalidated: list[str] = []
    opposite = report.cio.bull_case if turned_bearish else report.cio.bear_case
    invalidated.extend(s.text for s in opposite[:2])
    for exchange in report.cio.debate:
        if exchange.winner not in ("unresolved", ""):
            invalidated.append(f"Arbitrage : {exchange.resolution}")
            break
    return new_evidence, invalidated[:4]


def _change_reason(
    report: InvestmentReport,
    horizon: str,
    before: str,
    after: str,
    before_confidence: float | None,
    after_confidence: float | None,
    before_risk: float | None,
    after_risk: float | None,
) -> str:
    parts = [f"Thèse {horizon} : « {before} » → « {after} »."]
    if before_confidence is not None and after_confidence is not None:
        delta = after_confidence - before_confidence
        if abs(delta) >= 5:
            parts.append(
                f"Confiance {'en hausse' if delta > 0 else 'en baisse'} "
                f"({before_confidence:.0f} → {after_confidence:.0f}/100)."
            )
    if before_risk is not None and after_risk is not None:
        delta = after_risk - before_risk
        if abs(delta) >= 5:
            parts.append(
                f"Risque {'accru' if delta > 0 else 'réduit'} "
                f"({before_risk:.0f} → {after_risk:.0f}/100)."
            )
    if report.cio.contradictions:
        parts.append(report.cio.contradictions[0])
    return " ".join(parts)


def thesis_history_payload(session: Session, symbol: str, limit: int = 30) -> list[dict]:
    """The investment memory: how the thesis evolved, and why, newest first."""
    return [
        {
            "changed_at": change.changed_at.isoformat() if change.changed_at else None,
            "horizon": change.horizon,
            "from": change.from_recommendation,
            "to": change.to_recommendation,
            "from_confidence": change.from_confidence,
            "to_confidence": change.to_confidence,
            "from_risk": change.from_risk,
            "to_risk": change.to_risk,
            "reason": change.reason,
            "new_evidence": json.loads(change.new_evidence_json or "[]"),
            "invalidated": json.loads(change.invalidated_json or "[]"),
        }
        for change in load_thesis_changes(session, symbol, limit=limit)
    ]
