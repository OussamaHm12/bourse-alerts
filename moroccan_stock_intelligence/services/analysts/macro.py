"""Agent 7 — Macroeconomic Analyst.

Reads the Bank Al-Maghrib snapshot: policy rate, interbank rate (TMP), inflation and
core inflation, EUR/MAD and USD/MAD.

Oil and phosphate are NOT published by BAM. They are always named in `missing_data`
and never defaulted to zero. Macro is market context, so this analyst contributes no
directional per-stock lean — it informs the CIO, it does not vote on price.
"""

from __future__ import annotations

from datetime import UTC, datetime

from moroccan_stock_intelligence.services.analysts.base import fact, inference, unavailable_report
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, Statement
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.1"

# Coarse first-pass sensitivity: these sectors are the most rate-driven on the CSE.
RATE_SENSITIVE = ("banque", "immobilier", "assurance", "crédit", "credit", "financement")

STALE_DAYS = 45  # inflation is monthly; beyond this we say so rather than imply freshness


def analyze(ctx: ResearchContext) -> AnalystReport:
    macro = ctx.market.macro
    if macro is None or not macro.has_data:
        return unavailable_report(
            "macro",
            VERSION,
            "Macroéconomie",
            [
                "Indicateurs macro (taux directeur, inflation, change) non collectés.",
                "Pétrole et phosphate ne sont pas publiés par Bank Al-Maghrib.",
            ],
        )

    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    used: list[str] = []
    notes: list[str] = []
    sector = (ctx.sector or "").lower()

    if macro.policy_rate is not None:
        used.append("taux directeur")
        obs.append(fact(f"Taux directeur Bank Al-Maghrib : {macro.policy_rate:.2f}%.",
                        policy_rate=macro.policy_rate))
    if macro.interbank_rate is not None:
        used.append("taux interbancaire (TMP)")
        obs.append(fact(f"Taux interbancaire moyen pondéré : {macro.interbank_rate:.2f}%.",
                        interbank=macro.interbank_rate))
    if macro.inflation is not None:
        used.append("inflation")
        obs.append(fact(f"Inflation : {macro.inflation:.2f}%.", inflation=macro.inflation))
        if macro.inflation <= 2:
            strengths.append(
                inference("Inflation contenue : contexte monétaire plutôt favorable aux actions.",
                          "bullish", 0.35, inflation=macro.inflation)
            )
        elif macro.inflation >= 5:
            weaknesses.append(
                inference("Inflation élevée : risque de resserrement monétaire.",
                          "bearish", 0.45, inflation=macro.inflation)
            )
    if macro.inflation_underlying is not None:
        used.append("inflation sous-jacente")
        obs.append(fact(f"Inflation sous-jacente : {macro.inflation_underlying:.2f}%.",
                        core=macro.inflation_underlying))
    if macro.mad_eur is not None:
        used.append("EUR/MAD")
        obs.append(fact(f"EUR/MAD : {macro.mad_eur:.3f}.", mad_eur=macro.mad_eur))
    if macro.mad_usd is not None:
        used.append("USD/MAD")
        obs.append(fact(f"USD/MAD : {macro.mad_usd:.3f}.", mad_usd=macro.mad_usd))

    # Sector sensitivity — an opinion about transmission, not a claim about price.
    if any(key in sector for key in RATE_SENSITIVE) and macro.policy_rate is not None:
        obs.append(
            inference(
                f"Le secteur « {ctx.sector} » est sensible aux taux : toute décision de Bank Al-Maghrib "
                "se transmet directement à sa rentabilité.",
                weight=0.4,
            )
        )

    # Freshness: inflation is a monthly series and can legitimately lag.
    if macro.as_of is not None:
        age = (datetime.now(UTC) - macro.as_of).days
        if age > STALE_DAYS:
            notes.append(f"Données macro datées de {age} jours : à interpréter avec prudence.")

    missing = [
        "Pétrole et phosphate non publiés par Bank Al-Maghrib : impact matières premières non évalué.",
    ]
    for label, value in (("Taux directeur", macro.policy_rate), ("Inflation", macro.inflation),
                         ("EUR/MAD", macro.mad_eur), ("USD/MAD", macro.mad_usd)):
        if value is None:
            missing.append(f"{label} non collecté.")

    notes.append(f"Source : {macro.source or 'inconnue'}.")
    confidence = round(clamp(20 + len(used) * 11), 1)
    return AnalystReport(
        analyst="macro",
        version=VERSION,
        headline=(
            f"Contexte macro : taux directeur {macro.policy_rate:.2f}%"
            if macro.policy_rate is not None else "Contexte macroéconomique marocain."
        ) + (f", inflation {macro.inflation:.2f}%." if macro.inflation is not None else "."),
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
