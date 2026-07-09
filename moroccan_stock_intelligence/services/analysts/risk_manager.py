"""Agent 9 — Risk Manager (aggregator).

Consumes the context plus every analyst report. Reuses the proven
``horizon_strategy.compute_risk`` for the technical/history baseline, then harvests
each analyst's ``risk_flags`` so a bearish news item, a fundamental red flag, or a
portfolio concentration automatically raises risk. Produces a per-dimension
breakdown and base / best / worst scenarios (probabilities, never certainties).
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fmt, inference
from moroccan_stock_intelligence.services.horizon_strategy import compute_risk
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    RiskReport,
    Scenario,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.0"


def _dimensions(ctx: ResearchContext, reports: dict[str, AnalystReport]) -> tuple[dict[str, float], list[str]]:
    m = ctx.metric
    dims: dict[str, float] = {}
    missing: list[str] = []

    # Technical.
    tech = 0.0
    if m.volatility_30d is not None:
        tech += clamp((m.volatility_30d - 15) * 1.2, 0, 40)
    if m.momentum_30d is not None and m.momentum_30d < 0:
        tech += clamp(-m.momentum_30d * 1.5, 0, 30)
    if m.drawdown_from_recent_high is not None and m.drawdown_from_recent_high < -10:
        tech += clamp(-(m.drawdown_from_recent_high + 10) * 1.2, 0, 30)
    dims["technique"] = round(clamp(tech), 1)

    # Liquidity.
    if m.volume is None:
        dims["liquidite"] = 60.0
        missing.append("Liquidité mal mesurée (volume non collecté).")
    elif m.volume_anomaly is not None and m.volume_anomaly < 0.5:
        dims["liquidite"] = 55.0
    else:
        dims["liquidite"] = 30.0

    # Event (news).
    if ctx.news.count == 0:
        dims["evenementiel"] = 40.0
    elif ctx.news.fresh_negative or (ctx.news.avg_impact or 0) <= -0.3:
        dims["evenementiel"] = 70.0
    else:
        dims["evenementiel"] = 30.0

    # Valuation (needs fundamentals).
    if ctx.fundamentals is not None and ctx.fundamentals.per is not None:
        dims["valorisation"] = round(clamp((ctx.fundamentals.per - 15) * 3, 0, 100), 1)
    else:
        missing.append("Risque de valorisation non chiffrable (fondamentaux non collectés).")

    # Portfolio.
    pf = reports.get("portfolio")
    if pf is not None and pf.scope == "portfolio":
        pf_risk = 20.0 + 20.0 * sum(1 for f in pf.risk_flags if f.polarity == "bearish")
        dims["portefeuille"] = round(clamp(pf_risk), 1)

    # History / data-confidence.
    dims["historique"] = round(clamp(100 - ctx.history_days * 1.1, 10, 90), 1)
    if ctx.history_days < 30:
        missing.append(f"Historique court ({ctx.history_days} j) : estimation de risque moins fiable.")

    return dims, missing


def assess(ctx: ResearchContext, reports: dict[str, AnalystReport]) -> RiskReport:
    m = ctx.metric
    base_risk, base_reasons = compute_risk(ctx.metric, ctx.news, ctx.history_days)

    # Harvest analyst risk flags (extra risk not already in the technical baseline).
    flag_bonus = 0.0
    drivers: list[Statement] = [inference(r, "bearish", 0.5) for r in base_reasons[:3]]
    for name, rep in reports.items():
        for flag in rep.risk_flags:
            if flag.polarity == "bearish":
                flag_bonus += 6 * flag.weight
                drivers.append(inference(f"[{name}] {flag.text}", "bearish", flag.weight))
    flag_bonus = min(flag_bonus, 22.0)
    overall = round(clamp(base_risk + flag_bonus), 1)

    dims, missing = _dimensions(ctx, reports)
    measurable = len(dims)
    confidence = round(clamp(35 + measurable / 6 * 40 + min(ctx.history_days / 90, 1.0) * 20), 1)

    # Base / best / worst scenarios anchored on support/resistance.
    downside = f"support ~{fmt(m.support)} MAD" if m.support is not None else "un support technique"
    upside = f"résistance ~{fmt(m.resistance)} MAD" if m.resistance is not None else "une résistance"
    risk_frac = overall / 100
    p_worst = round(clamp(0.15 + risk_frac * 0.4, 0.05, 0.7), 2)
    p_best = round(clamp(0.45 - risk_frac * 0.35, 0.05, 0.6), 2)
    p_base = round(max(0.05, 1 - p_worst - p_best), 2)
    worst = Scenario("Scénario défavorable", p_worst, confidence, f"Repli vers {downside} si les supports cèdent.")
    base = Scenario("Scénario central", p_base, confidence, "Évolution sans catalyseur majeur, dans la fourchette récente.")
    best = Scenario("Scénario favorable", p_best, confidence, f"Extension vers {upside} en cas de confirmation.")

    if not drivers:
        drivers.append(inference("Aucun facteur de risque majeur détecté sur les données disponibles.", "neutral", 0.3))

    return RiskReport(
        overall_risk=overall,
        confidence=confidence,
        dimensions=dims,
        worst_case=worst,
        base_case=base,
        best_case=best,
        drivers=drivers[:6],
        missing_data=missing,
        version=VERSION,
    )
