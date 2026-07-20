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

VERSION = "2.0"

# --------------------------------------------------------------------------- #
# The risk formula                                                             #
# --------------------------------------------------------------------------- #
#
# Until v2.0 these dimensions were computed, serialised, drawn as a radar in the
# app — and then discarded: `overall_risk` was `compute_risk(...) + flag_bonus`,
# and `_dimensions()` fed nothing (AUDIT_2026-07-18.md §7). The consequence was
# not cosmetic. `valorisation` is the only place a rich PER is penalised, so a
# stock at PER 45 and a stock at PER 8 produced the same risk, and the radar the
# owner was reading had no relationship to the number driving the recommendation.
#
# Now one arithmetic produces both:
#
#     overall_risk = Σ(dimension × weight)                    # what we measured
#                  + PRUDENT_UNKNOWN × (1 − coverage)         # what we could not
#                  + flag_penalty                             # analyst red flags
#
# Weights sum to 1.0 and are ordered by how directly each dimension has moved
# prices on this market: price behaviour first, then the data-confidence term
# (short history is genuinely the largest source of error here), then the
# fundamental and event terms, then liquidity and portfolio construction.
#
# They are a stated prior, not a fitted result — the backtest ablation
# (services/backtest) is what can move them, and until it says otherwise this is
# an informed guess and is labelled as one.
DIMENSION_WEIGHTS: dict[str, float] = {
    "technique": 0.30,
    "historique": 0.20,
    "valorisation": 0.15,
    "evenementiel": 0.15,
    "liquidite": 0.10,
    "portefeuille": 0.10,
}

# An unmeasurable dimension is NOT neutral. Everywhere else in this engine an
# absent input shrinks a score toward 50, because not knowing whether a setup is
# good means it is probably ordinary. Risk is asymmetric: not knowing whether
# something is dangerous is itself a reason for caution, so missing coverage
# pulls upward, not to the middle.
PRUDENT_UNKNOWN = 58.0

# Analyst red flags add on top of the measured dimensions, capped so a pile of
# minor flags cannot swamp what was actually measured.
MAX_FLAG_PENALTY = 22.0
FLAG_WEIGHT = 6.0


def _dimensions(
    ctx: ResearchContext, reports: dict[str, AnalystReport], technical_risk: float
) -> tuple[dict[str, float], list[str]]:
    m = ctx.metric
    dims: dict[str, float] = {}
    missing: list[str] = []

    # Technical. Takes `horizon_strategy.compute_risk`'s result rather than
    # recomputing it: this block used to hold a second, near-identical volatility
    # + momentum + drawdown formula, so the radar's "technique" slice and the
    # `avoid_score` shown on the Opportunités tab could disagree about the same
    # stock. One formula, two readers.
    dims["technique"] = round(clamp(technical_risk), 1)

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
    flag_penalty = 0.0
    drivers: list[Statement] = [inference(r, "bearish", 0.5) for r in base_reasons[:3]]
    for name, rep in reports.items():
        for flag in rep.risk_flags:
            if flag.polarity == "bearish":
                flag_penalty += FLAG_WEIGHT * flag.weight
                drivers.append(inference(f"[{name}] {flag.text}", "bearish", flag.weight))
    flag_penalty = round(min(flag_penalty, MAX_FLAG_PENALTY), 1)

    dims, missing = _dimensions(ctx, reports, base_risk)

    # The measured part. Each dimension contributes value x weight; a dimension we
    # could not compute contributes nothing here and instead shows up as missing
    # coverage below, so it can never be silently read as "risk = 0".
    contributions = {
        name: round(value * DIMENSION_WEIGHTS[name], 2)
        for name, value in dims.items()
        if name in DIMENSION_WEIGHTS
    }
    coverage = round(sum(DIMENSION_WEIGHTS[name] for name in contributions), 3)
    measured = sum(contributions.values())

    # The unmeasured part, priced prudently rather than assumed benign.
    unknown_penalty = round(PRUDENT_UNKNOWN * (1.0 - coverage), 2)

    overall = round(clamp(measured + unknown_penalty + flag_penalty), 1)

    if coverage < 0.6:
        missing.append(
            f"Seulement {coverage * 100:.0f}% des dimensions de risque sont mesurables : "
            "le risque non mesuré est valorisé prudemment, pas ignoré."
        )

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
        # The breakdown the app draws IS the arithmetic that produced overall_risk.
        weights={name: DIMENSION_WEIGHTS[name] for name in contributions},
        contributions=contributions,
        coverage=coverage,
        flag_penalty=flag_penalty,
        unknown_penalty=unknown_penalty,
    )
