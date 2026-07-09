"""Agent 8 — Portfolio Analyst (portfolio scope).

Analyses the WHOLE portfolio, not one stock: sector concentration, position count /
diversification, drawdown exposure, and the marginal effect of the stock under review
(does holding / adding it worsen concentration?). Its output tempers the CIO — an
already-overweight sector should push a borderline call toward Hold/Reduce.

Signature differs from the symbol analysts (it needs every holding's metrics), so the
orchestrator calls it explicitly and attaches the result to the report.
"""

from __future__ import annotations

from moroccan_stock_intelligence.services.analysts.base import fact, fmt, inference
from moroccan_stock_intelligence.services.research.context import GatheredState, ResearchContext
from moroccan_stock_intelligence.services.research.contracts import AnalystReport, Statement

VERSION = "1.0"
CONCENTRATION_WARN = 40.0  # % of portfolio in one sector


def analyze(ctx: ResearchContext, gathered: GatheredState) -> AnalystReport:
    holdings = gathered.holdings
    metrics_by_symbol = gathered.metrics_by_symbol
    obs: list[Statement] = []
    risk_flags: list[Statement] = []
    strengths: list[Statement] = []
    missing: list[str] = []
    used: list[str] = []
    notes: list[str] = []

    priced = {s: e for s, e in holdings.items() if e.market_value is not None}
    if not priced:
        return AnalystReport(
            analyst="portfolio",
            version=VERSION,
            scope="portfolio",
            headline="Aucune position valorisée : impact portefeuille indéterminé.",
            confidence=10.0,
            missing_data=["Aucune position enregistrée ou aucun cours disponible (PORTFOLIO_JSON)."],
        )

    total_value = sum(e.market_value for e in priced.values())
    used.append(f"{len(priced)} position(s) valorisée(s)")

    # Sector weights.
    sector_value: dict[str, float] = {}
    for symbol, e in priced.items():
        sector = metrics_by_symbol.get(symbol).sector if metrics_by_symbol.get(symbol) else None
        sector_value[sector or "Inconnu"] = sector_value.get(sector or "Inconnu", 0.0) + e.market_value
    top_sector, top_val = max(sector_value.items(), key=lambda kv: kv[1])
    top_pct = top_val / total_value * 100 if total_value else 0.0
    obs.append(
        fact(
            f"Portefeuille : {len(priced)} position(s), valeur {fmt(total_value)} MAD, "
            f"secteur le plus lourd {top_sector} ({top_pct:.0f}%).",
            evidence={"positions": len(priced), "top_sector": top_sector, "top_pct": top_pct},
        )
    )

    if len(priced) < 4:
        risk_flags.append(
            inference(f"Faible diversification ({len(priced)} position(s)).", "bearish", 0.5)
        )
    else:
        strengths.append(inference(f"Diversification correcte ({len(priced)} positions).", "bullish", 0.3))

    if top_pct >= CONCENTRATION_WARN:
        risk_flags.append(
            inference(
                f"Concentration sectorielle élevée : {top_pct:.0f}% sur {top_sector}.",
                "bearish", 0.6, top_sector=top_sector, top_pct=top_pct,
            )
        )
        notes.append("Une correction du secteur dominant pèserait fortement sur le portefeuille.")

    # Drawdown exposure.
    losers = [s for s, e in priced.items() if (e.net_pl_pct or 0) < 0]
    if losers:
        obs.append(
            fact(f"{len(losers)}/{len(priced)} position(s) en moins-value latente.", evidence={"losers": len(losers)})
        )

    # Marginal effect of the stock under review.
    held = ctx.symbol in holdings
    this_sector = ctx.sector or "Inconnu"
    if held and ctx.symbol in priced:
        w = priced[ctx.symbol].market_value / total_value * 100
        obs.append(fact(f"{ctx.symbol} pèse {w:.0f}% du portefeuille.", evidence={"weight_pct": w}))
        if w >= 25:
            risk_flags.append(inference(f"Position {ctx.symbol} surpondérée ({w:.0f}%).", "bearish", 0.6))
    elif this_sector in sector_value and top_sector == this_sector and top_pct >= CONCENTRATION_WARN:
        risk_flags.append(
            inference(
                f"Renforcer {this_sector} accentuerait une concentration déjà élevée ({top_pct:.0f}%).",
                "bearish", 0.6,
            )
        )
        notes.append(f"Pour {ctx.symbol}, privilégier la prudence tant que {this_sector} domine le portefeuille.")

    missing.append("Allocation cash non suivie ; corrélations approximées par le secteur (pas de matrice de corrélation).")

    return AnalystReport(
        analyst="portfolio",
        version=VERSION,
        scope="portfolio",
        headline=(
            f"Concentration {top_sector} {top_pct:.0f}% sur {len(priced)} position(s)."
        ),
        observations=obs,
        strengths=strengths,
        weaknesses=[],
        risk_flags=risk_flags,
        confidence=round(min(80.0, 30 + len(priced) * 8), 1),
        data_used=used,
        missing_data=missing,
        notes=notes,
    )
