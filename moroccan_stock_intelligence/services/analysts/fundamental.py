"""Agent 4 — Fundamental Analyst.

Reads the six ratios officially published on the Casablanca Bourse issuer page:
BPA (EPS), ROE, Payout, Dividend yield, PER, PBR — for the latest fiscal year.

Revenue, net income, margins, ROA, debt/equity and book value are NOT published in
machine-readable form (validated 2026-07-09); they live only inside issuer PDFs.
They are named in `missing_data` and never invented.

A PER computed as price / BPA (because the published cell was "-") is reported as
an **inference**, never as a fact, and is flagged in the notes.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import (
    fact,
    inference,
    lean_from,
    unavailable_report,
)
from moroccan_stock_intelligence.services.research.context import ResearchContext
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    HorizonSignal,
    Statement,
)
from moroccan_stock_intelligence.utils import clamp

VERSION = "1.1"

WEIGHTS = {"valorisation": 0.35, "rentabilite": 0.30, "rendement": 0.20, "actif_net": 0.15}

# Never published in machine-readable form — always declared, never guessed.
NOT_PUBLISHED = (
    "Chiffre d'affaires, résultat net, marges, ROA, dette/fonds propres et actif net "
    "comptable ne sont pas publiés sous forme exploitable (uniquement dans les PDF des émetteurs)."
)


def analyze(ctx: ResearchContext) -> AnalystReport:
    f = ctx.fundamentals
    if f is None or not f.has_data:
        return unavailable_report(
            "fundamental",
            VERSION,
            "Fondamentaux",
            [
                "Ratios officiels (BPA, ROE, Payout, rendement, PER, PBR) non collectés pour ce titre.",
                NOT_PUBLISHED,
            ],
        )

    obs: list[Statement] = []
    strengths: list[Statement] = []
    weaknesses: list[Statement] = []
    risk_flags: list[Statement] = []
    used: list[str] = []
    notes: list[str] = []
    components: dict[str, float | None] = {}

    year = f.fiscal_year
    year_txt = f" (exercice {year})" if year else ""

    # --- PER: valuation. May be official (fact) or derived (inference). ---
    if f.per is not None:
        used.append("PER")
        components["valorisation"] = clamp(50 - (f.per - 15) * 3)
        if f.per_is_derived:
            obs.append(
                inference(
                    f"PER estimé à {f.per:.2f} (cours ÷ BPA) : le PER publié était absent{year_txt}.",
                    weight=0.4, per=f.per, derived=True,
                )
            )
            notes.append("Le PER n'est pas publié pour cet exercice : valeur estimée, à traiter comme une inférence.")
        else:
            obs.append(fact(f"PER {f.per:.2f}{year_txt}.", per=f.per))
        if f.per < 12:
            strengths.append(
                inference("Valorisation modérée au regard des bénéfices (PER faible).", "bullish", 0.55, per=f.per)
            )
        elif f.per > 25:
            weaknesses.append(
                inference("Valorisation tendue au regard des bénéfices (PER élevé).", "bearish", 0.55, per=f.per)
            )

    # --- ROE: profitability. ---
    if f.roe is not None:
        used.append("ROE")
        components["rentabilite"] = clamp(50 + (f.roe - 10) * 2.5)
        obs.append(fact(f"ROE {f.roe:.2f}%{year_txt}.", roe=f.roe))
        if f.roe >= 15:
            strengths.append(fact("Rentabilité des fonds propres élevée.", "bullish", 0.6, roe=f.roe))
        elif f.roe < 8:
            weaknesses.append(fact("Rentabilité des fonds propres faible.", "bearish", 0.55, roe=f.roe))

    # --- Dividend yield + payout sustainability. ---
    if f.dividend_yield is not None:
        used.append("rendement du dividende")
        components["rendement"] = clamp(25 + f.dividend_yield * 10)
        obs.append(fact(f"Rendement du dividende {f.dividend_yield:.2f}%{year_txt}.", dy=f.dividend_yield))
        if f.dividend_yield >= 4:
            strengths.append(
                inference("Dividende généreux : soutien au rendement long terme.", "bullish", 0.5, dy=f.dividend_yield)
            )
    if f.payout is not None:
        used.append("payout")
        obs.append(fact(f"Payout {f.payout:.2f}%{year_txt}.", payout=f.payout))
        if f.payout > 85:
            risk_flags.append(
                inference("Payout très élevé : la couverture du dividende par les bénéfices est mince.",
                          "bearish", 0.5, payout=f.payout)
            )

    # --- PBR: price vs net assets. ---
    if f.pbr is not None:
        used.append("PBR")
        components["actif_net"] = clamp(85 - (f.pbr - 1) * 15)
        obs.append(fact(f"PBR {f.pbr:.2f}{year_txt}.", pbr=f.pbr))
        if f.pbr < 1.2:
            strengths.append(inference("Cours proche de l'actif net comptable (PBR bas).", "bullish", 0.45, pbr=f.pbr))
        elif f.pbr > 4:
            weaknesses.append(inference("Cours très au-dessus de l'actif net comptable (PBR élevé).", "bearish", 0.45, pbr=f.pbr))

    if f.eps is not None:
        used.append("BPA")

    missing = [NOT_PUBLISHED]
    for label, value in (("PER", f.per), ("PBR", f.pbr), ("BPA", f.eps), ("ROE", f.roe),
                         ("Payout", f.payout), ("Rendement du dividende", f.dividend_yield)):
        if value is None:
            missing.append(f"{label} non publié pour l'exercice {year}." if year else f"{label} non publié.")

    lean = lean_from(components, WEIGHTS)
    available = sum(1 for v in components.values() if v is not None)
    confidence = round(clamp(20 + len(used) * 12 - (8 if f.per_is_derived else 0)), 1)
    notes.append(f"Source : {f.source or 'inconnue'}" + (f" — exercice {year}." if year else "."))

    direction = "solides" if lean >= 58 else "fragiles" if lean <= 42 else "moyens"
    return AnalystReport(
        analyst="fundamental",
        version=VERSION,
        headline=(
            f"Fondamentaux {direction}{year_txt} "
            f"(lean {lean:.0f}/100, {len(used)} ratio(s) publié(s), {available} composante(s) notée(s))."
        ),
        observations=obs,
        strengths=strengths,
        weaknesses=weaknesses,
        horizon_signals=[
            HorizonSignal("medium", lean, components, WEIGHTS),
            HorizonSignal("long", lean, components, WEIGHTS),
        ],
        risk_flags=risk_flags,
        confidence=confidence,
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
