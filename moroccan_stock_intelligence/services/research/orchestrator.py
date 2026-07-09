"""Analyst Orchestrator (Phase 1, PRIORITY 3).

Turns a symbol into a full :class:`InvestmentReport`: build the shared context,
run the ten analysts (fault-isolated — one failure never sinks the report), then
the Risk Manager, then the CIO (the only recommender). Analysts are a deterministic,
explicit registry (order below); each is a pure function of the context.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.services.analysts import (
    cio,
    company,
    fundamental,
    historical_behaviour,
    macro,
    market_structure,
    news_analyst,
    portfolio_analyst,
    risk_manager,
    technical,
)
from moroccan_stock_intelligence.services.analysts.base import degraded_report
from moroccan_stock_intelligence.services.research.context import (
    GatheredState,
    ResearchContext,
    build_context,
    build_market_context,
    gather,
)
from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    InvestmentReport,
    Scenario,
)

ENGINE_VERSION = "1.0"

DISCLAIMER = (
    "Information seulement — ceci n'est pas un conseil en investissement. "
    "Cours différés ~15 min."
)

# Explicit, deterministic registry of symbol-scope analysts (have-data first).
SYMBOL_ANALYSTS: list[tuple[str, object]] = [
    ("technical", technical.analyze),
    ("market_structure", market_structure.analyze),
    ("news", news_analyst.analyze),
    ("historical_behaviour", historical_behaviour.analyze),
    ("macro", macro.analyze),
    ("company", company.analyze),
    ("fundamental", fundamental.analyze),
]


def _safe(name: str, version: str, fn, *args) -> AnalystReport:
    try:
        return fn(*args)
    except Exception as exc:  # analysts must never sink the report
        return degraded_report(name, version, f"{type(exc).__name__}: {exc}")


def _versions() -> dict[str, str]:
    return {
        "technical": technical.VERSION,
        "market_structure": market_structure.VERSION,
        "news": news_analyst.VERSION,
        "historical_behaviour": historical_behaviour.VERSION,
        "macro": macro.VERSION,
        "company": company.VERSION,
        "fundamental": fundamental.VERSION,
        "portfolio": portfolio_analyst.VERSION,
    }


def run(ctx: ResearchContext, gathered: GatheredState, horizon: str) -> InvestmentReport:
    versions = _versions()
    reports: dict[str, AnalystReport] = {}
    for name, fn in SYMBOL_ANALYSTS:
        reports[name] = _safe(name, versions[name], fn, ctx)
    # Portfolio analyst has a portfolio scope (needs the whole gathered state).
    reports["portfolio"] = _safe(
        "portfolio", versions["portfolio"], portfolio_analyst.analyze, ctx, gathered
    )

    risk = risk_manager.assess(ctx, reports)

    score = gathered.scores.get(ctx.symbol)
    avoid_score = score.avoid_score if score is not None else None
    cio_report = cio.decide(ctx, reports, risk, horizon, avoid_score)

    # Consolidated forward scenarios (directional + historical analogy).
    scenarios: list[Scenario] = []
    for name in ("technical", "historical_behaviour"):
        rep = reports.get(name)
        if rep is not None:
            scenarios.extend(rep.scenarios)

    return InvestmentReport(
        symbol=ctx.symbol,
        company_name=ctx.company_name,
        sector=ctx.sector,
        as_of=datetime.now(UTC),
        horizon_focus=horizon,
        cio=cio_report,
        risk=risk,
        analysts=reports,
        scenarios=scenarios,
        narrative=None,  # filled by the Synthesizer in a later phase
        engine_version=ENGINE_VERSION,
        disclaimer=DISCLAIMER,
    )


def analyze_report(session: Session, symbol: str, horizon: str = "short") -> InvestmentReport | None:
    """Entry point: build context once, run the full team for one symbol."""
    gathered = gather(session)
    market = build_market_context(gathered)
    ctx = build_context(session, symbol, gathered, market)
    if ctx is None:
        return None
    return run(ctx, gathered, horizon)
