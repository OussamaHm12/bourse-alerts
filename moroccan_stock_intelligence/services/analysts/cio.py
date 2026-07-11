"""Agent 10 — Chief Investment Officer (the ONLY module that recommends).

Consumes every analyst report + the Risk Manager's report and:
  1. Aggregates an authoritative per-horizon score using the proven, tested
     ``horizon_strategy`` kernel (assess_all + compute_confidence) — no drift.
  2. Detects contradictions by comparing the analysts' per-horizon leans.
  3. Decides per horizon (recommendations may differ by horizon).
  4. Writes the thesis: executive summary, bull case, bear case, final verdict —
     every claim cited by the module that produced it, always probabilistic.
"""

from __future__ import annotations

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.analysts.base import fmt
from moroccan_stock_intelligence.services.horizon_strategy import (
    HORIZON_LABELS_FR,
    HORIZONS,
    assess_all,
    compute_confidence,
)
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    CIOReport,
    HorizonVerdict,
    RiskReport,
    Statement,
)
from moroccan_stock_intelligence.services.research.debate import build_debate, debate_summary

VERSION = "1.0"

RECOMMENDATION_LABELS_FR = {
    "STRONG_OPPORTUNITY": "Forte opportunité",
    "WATCH": "À surveiller",
    "HOLD": "Conserver",
    "TAKE_PROFIT": "Prendre des bénéfices",
    "AVOID": "Éviter",
    "RISKY": "Risqué",
}

HOLDING_RISK = 70.0
BULL_LEAN = 58.0
BEAR_LEAN = 42.0
# Analysts whose leans participate in the contradiction check (directional views).
DIRECTIONAL = ("technical", "market_structure", "news", "historical_behaviour", "fundamental")


def _recommend(score: float, confidence: float, risk: float, avoid_score: float | None, ctx: ResearchContext) -> str:
    holding = ctx.holding
    held = holding is not None and holding.current_price is not None
    if held:
        if holding.advice == "SELL":
            if holding.net_pl_pct is not None and holding.net_pl_pct >= settings.take_profit_pct:
                return "TAKE_PROFIT"
            return "RISKY"
        if risk >= HOLDING_RISK:
            return "RISKY"
        return "HOLD"
    if risk >= 65 and score < 70:
        return "RISKY"
    if avoid_score is not None and avoid_score >= 60:
        return "AVOID"
    if score >= 70 and confidence >= 50:
        return "STRONG_OPPORTUNITY"
    if score >= 55:
        return "WATCH"
    if score < 45:
        return "AVOID"
    return "WATCH"


def _contradictions(reports: dict[str, AnalystReport]) -> list[str]:
    out: list[str] = []
    for horizon in HORIZONS:
        bulls, bears = [], []
        for name in DIRECTIONAL:
            rep = reports.get(name)
            if rep is None or rep.confidence < 20:
                continue
            lean = rep.lean_for(horizon)
            if lean is None:
                continue
            if lean >= BULL_LEAN:
                bulls.append(name)
            elif lean <= BEAR_LEAN:
                bears.append(name)
        if bulls and bears:
            label = HORIZON_LABELS_FR[horizon].lower()
            out.append(
                f"{label} : signaux divergents — {', '.join(bulls)} orienté(s) à la hausse "
                f"contre {', '.join(bears)} à la baisse."
            )
    return out


def _invalidation(ctx: ResearchContext, recommendation: str) -> list[str]:
    m = ctx.metric
    inv: list[str] = []
    bullish = recommendation in {"STRONG_OPPORTUNITY", "WATCH", "HOLD"}
    if bullish and m.support is not None:
        inv.append(f"Cassure durable sous le support (~{fmt(m.support)} MAD).")
    if bullish and m.ma50 is not None:
        inv.append("Passage durable sous la MM50.")
    if not bullish and m.resistance is not None:
        inv.append(f"Franchissement confirmé de la résistance (~{fmt(m.resistance)} MAD).")
    inv.append("Actualité officielle inversant la thèse (résultats, opération sur capital).")
    return inv[:3]


def _watch_next(ctx: ResearchContext) -> list[str]:
    m = ctx.metric
    watch: list[str] = []
    if m.support is not None:
        watch.append(f"Tenue du support (~{fmt(m.support)} MAD).")
    if m.resistance is not None and m.resistance != m.support:
        watch.append(f"Test de la résistance (~{fmt(m.resistance)} MAD).")
    watch.append("Volume des prochaines séances (confirmation).")
    watch.append("Prochains avis officiels de la Bourse de Casablanca.")
    if ctx.holding is not None:
        watch.append("Votre seuil de vente / prise de bénéfices.")
    return watch[:4]


def _cited(reports: dict[str, AnalystReport], attr: str, limit: int) -> list[Statement]:
    collected: list[tuple[float, Statement]] = []
    for name, rep in reports.items():
        for st in getattr(rep, attr):
            collected.append((st.weight, Statement(
                text=f"[{name}] {st.text}", kind=st.kind, polarity=st.polarity,
                weight=st.weight, evidence=st.evidence,
            )))
    collected.sort(key=lambda x: x[0], reverse=True)
    return [st for _, st in collected[:limit]]


def decide(
    ctx: ResearchContext,
    reports: dict[str, AnalystReport],
    risk: RiskReport,
    horizon_focus: str,
    avoid_score: float | None,
    reliability: dict[str, float] | None = None,
) -> CIOReport:
    """Reach the verdict. `reliability` is the learned per-analyst confidence
    multiplier (Phase 3); absent or 1.0 means the analyst has not yet earned a
    track record, and the debate then rests on stated confidence alone."""
    assessments = assess_all(ctx.metric, ctx.news, ctx.history_days)
    reliability = reliability or {}

    # Phase 6: the analysts argue, and the argument is resolved explicitly.
    debate = build_debate(reports, reliability)

    verdicts: dict[str, HorizonVerdict] = {}
    for horizon in HORIZONS:
        a = assessments[horizon]
        confidence, conf_reason = compute_confidence(a, ctx.history_days)
        rec = _recommend(a.score, confidence, risk.overall_risk, avoid_score, ctx)
        rationale = (
            f"Score {a.score:.0f}/100, risque {risk.overall_risk:.0f}/100, "
            f"confiance {confidence:.0f}/100. {conf_reason} {debate_summary(debate, horizon)}"
        )
        verdicts[horizon] = HorizonVerdict(
            horizon=horizon,
            recommendation=rec,
            recommendation_label=RECOMMENDATION_LABELS_FR[rec],
            score=a.score,
            confidence=confidence,
            rationale=rationale,
            invalidation=_invalidation(ctx, rec),
            watch_next=_watch_next(ctx),
        )

    contradictions = _contradictions(reports)
    bull_case = _cited(reports, "strengths", 6)
    bear_case = _cited(reports, "weaknesses", 6)

    proven = {name: mult for name, mult in reliability.items() if abs(mult - 1.0) > 0.01}
    if proven:
        best = max(proven.items(), key=lambda kv: kv[1])
        calibration_note = (
            "Pondération ajustée par l'historique de fiabilité : "
            + ", ".join(f"{name} ×{mult:.2f}" for name, mult in sorted(proven.items()))
            + f". L'avis de « {best[0]} » pèse le plus lourd au vu de ses résultats passés."
        )
    else:
        calibration_note = (
            "Aucun analyste n'a encore assez de prédictions évaluées pour être recalibré : "
            "tous les avis sont pondérés par leur seule confiance déclarée."
        )

    focus = verdicts[horizon_focus]
    regime = ctx.market.regime
    # Probabilistic core — never a certainty.
    if focus.score >= 70 and focus.confidence >= 50:
        core = "la configuration est favorable, sans garantie de hausse"
    elif focus.score >= 55:
        core = "la configuration est intéressante mais demande confirmation"
    elif focus.score >= 45:
        core = "aucune direction ne domine clairement"
    else:
        core = "la configuration est défavorable ou trop incertaine pour agir"

    # Horizon disagreement is a feature, not a bug — surface it.
    recs = {h: verdicts[h].recommendation for h in HORIZONS}
    differ = len(set(recs.values())) > 1
    horizon_note = (
        f" Les horizons divergent ({', '.join(f'{HORIZON_LABELS_FR[h].lower()} : {RECOMMENDATION_LABELS_FR[recs[h]]}' for h in HORIZONS)})."
        if differ else ""
    )
    contradiction_note = f" Attention : {contradictions[0]}" if contradictions else ""

    executive_summary = (
        f"{ctx.symbol} ({ctx.company_name}) — sur l'horizon {HORIZON_LABELS_FR[horizon_focus].lower()}, "
        f"{RECOMMENDATION_LABELS_FR[focus.recommendation].lower()} : {core} "
        f"(score {focus.score:.0f}/100, risque {risk.overall_risk:.0f}/100, confiance {focus.confidence:.0f}/100). "
        f"Contexte de marché {regime}.{horizon_note}{contradiction_note} "
        "Ceci est une estimation probabiliste, pas une prévision."
    )
    final_verdict = (
        f"{HORIZON_LABELS_FR[horizon_focus]} : {RECOMMENDATION_LABELS_FR[focus.recommendation]} "
        f"(confiance {focus.confidence:.0f}/100)."
    )

    return CIOReport(
        symbol=ctx.symbol,
        verdicts=verdicts,
        contradictions=contradictions,
        bull_case=bull_case,
        bear_case=bear_case,
        executive_summary=executive_summary,
        final_verdict=final_verdict,
        debate=debate,
        calibration_note=calibration_note,
        version=VERSION,
    )
