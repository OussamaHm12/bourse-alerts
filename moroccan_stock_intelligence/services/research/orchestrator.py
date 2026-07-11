"""Analyst Orchestrator — the spine of the platform.

Turns a symbol into a full :class:`InvestmentReport`:

    cache?  ->  gather  ->  context  ->  10 analysts  ->  risk  ->  CIO
                                             |            |         |
                                             |            |         +-- debate (Phase 6)
                                             |            +------------ scenarios (Phase 7)
                                             +-------------------------- knowledge (Phase 4)
                                                                         thesis memory (Phase 5)
                                                                                |
                                             persist + predictions (Phases 2, 3)

Reports are served from the research database unless they are stale or
`fresh=True` is requested, so the expensive path runs on a schedule rather than
on every request.

Analysts are a deterministic, explicit registry; each is fault-isolated, so one
failure degrades that analyst's section instead of sinking the report.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.repository import load_cached_report, load_company_knowledge
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
    HORIZONS,
    AnalystReport,
    InvestmentReport,
    Scenario,
    report_to_dict,
    thesis_hash,
)
from moroccan_stock_intelligence.services.research.learning import reliability_map
from moroccan_stock_intelligence.services.research.scenarios import build_all_scenarios
from moroccan_stock_intelligence.services.research.store import persist_report, thesis_history_payload

LOG = logging.getLogger(__name__)

# Bumped in Phase 2-10: reports now carry debate, per-horizon scenarios, knowledge
# and thesis history. A stored report from an older engine is never served (the
# cache lookup filters on this) and never compared against by the learning engine.
ENGINE_VERSION = "2.0"

DISCLAIMER = (
    "Information seulement — ceci n'est pas un conseil en investissement. "
    "Cours différés ~15 min."
)

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
    except Exception as exc:  # noqa: BLE001 - an analyst must never sink the report
        LOG.exception("analyst_failed name=%s", name)
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


def run(
    session: Session,
    ctx: ResearchContext,
    gathered: GatheredState,
    horizon: str,
) -> InvestmentReport:
    """Run the full team for one symbol. Pure computation + read-only DB lookups."""
    versions = _versions()
    reports: dict[str, AnalystReport] = {}
    for name, fn in SYMBOL_ANALYSTS:
        reports[name] = _safe(name, versions[name], fn, ctx)
    reports["portfolio"] = _safe(
        "portfolio", versions["portfolio"], portfolio_analyst.analyze, ctx, gathered
    )

    risk = risk_manager.assess(ctx, reports)

    score = gathered.scores.get(ctx.symbol)
    avoid_score = score.avoid_score if score is not None else None

    # Phase 3: what the platform has learned about each analyst's reliability.
    # Empty (all 1.0) until enough predictions have matured — never faked.
    reliability = _reliability(session, horizon)

    cio_report = cio.decide(ctx, reports, risk, horizon, avoid_score, reliability)

    # Phase 7: best / base / worst for every horizon, with assumptions.
    scores = {h: cio_report.verdicts[h].score for h in HORIZONS if h in cio_report.verdicts}
    confidences = {
        h: cio_report.verdicts[h].confidence for h in HORIZONS if h in cio_report.verdicts
    }
    scenarios_by_horizon = build_all_scenarios(ctx, scores, confidences, risk)

    scenarios: list[Scenario] = []
    focus = scenarios_by_horizon.get(horizon)
    if focus is not None:
        scenarios = [focus.best, focus.base, focus.worst]
    for name in ("technical", "historical_behaviour"):
        rep = reports.get(name)
        if rep is not None:
            scenarios.extend(rep.scenarios)

    report = InvestmentReport(
        symbol=ctx.symbol,
        company_name=ctx.company_name,
        sector=ctx.sector,
        as_of=datetime.now(UTC),
        horizon_focus=horizon,
        cio=cio_report,
        risk=risk,
        analysts=reports,
        scenarios=scenarios,
        narrative=None,
        engine_version=ENGINE_VERSION,
        disclaimer=DISCLAIMER,
        scenarios_by_horizon=scenarios_by_horizon,
        knowledge=_knowledge(session, ctx.symbol),
        thesis_history=_thesis_history(session, ctx.symbol),
        cached=False,
        generated_at=datetime.now(UTC),
    )
    return _with_hash(report)


def _with_hash(report: InvestmentReport) -> InvestmentReport:
    from dataclasses import replace

    return replace(report, thesis_hash=thesis_hash(report))


def _reliability(session: Session, horizon: str) -> dict[str, float]:
    try:
        return reliability_map(session, horizon)
    except Exception:  # noqa: BLE001 - learning is an enhancement, never a dependency
        LOG.exception("reliability_lookup_failed")
        return {}


def _knowledge(session: Session, symbol: str) -> dict[str, list[dict]]:
    try:
        return load_company_knowledge(session, symbol)
    except Exception:  # noqa: BLE001
        LOG.exception("knowledge_lookup_failed symbol=%s", symbol)
        return {}


def _thesis_history(session: Session, symbol: str) -> list[dict]:
    try:
        return thesis_history_payload(session, symbol, limit=12)
    except Exception:  # noqa: BLE001
        LOG.exception("thesis_history_failed symbol=%s", symbol)
        return []


def generate_report(
    session: Session,
    symbol: str,
    horizon: str = "short",
    persist: bool = True,
) -> InvestmentReport | None:
    """Compute a fresh report (storing it is also what detects a thesis change)."""
    gathered = gather(session)
    market = build_market_context(gathered)
    ctx = build_context(session, symbol, gathered, market)
    if ctx is None:
        return None

    report = run(session, ctx, gathered, horizon)
    if persist:
        persist_report(session, report)
    return report


def generate_all(
    session: Session,
    horizon: str = "short",
    symbols: list[str] | None = None,
) -> list[tuple[InvestmentReport, int]]:
    """Generate + store a report for every tracked symbol, ONE gather for the run.

    This is the scheduled path. Returning (report, stored_id) lets the notification
    layer compare each report against its own predecessor without re-querying.
    """
    gathered = gather(session)
    market = build_market_context(gathered)
    wanted = {s.upper() for s in symbols} if symbols else None

    generated: list[tuple[InvestmentReport, int]] = []
    for metric in gathered.metrics:
        if wanted and metric.symbol.upper() not in wanted:
            continue
        try:
            ctx = build_context(session, metric.symbol, gathered, market)
            if ctx is None:
                continue
            report = run(session, ctx, gathered, horizon)
            row = persist_report(session, report)
            if row is not None:
                generated.append((report, row.id))
        except Exception:  # noqa: BLE001 - one symbol must not sink the sweep
            LOG.exception("report_generation_failed symbol=%s", metric.symbol)
            session.rollback()
    LOG.info("reports_generated count=%s horizon=%s", len(generated), horizon)
    return generated


def analyze_report(
    session: Session,
    symbol: str,
    horizon: str = "short",
    fresh: bool = False,
) -> dict | None:
    """Entry point for the API. Returns a JSON-safe dict (never a dataclass).

    Serves the stored report unless it is stale or `fresh=True`. The store IS the
    cache: a served report is byte-identical to the one that was generated, which
    is what makes every report reproducible.
    """
    if not fresh:
        cached = load_cached_report(
            session, symbol, horizon, ENGINE_VERSION, settings.report_cache_seconds
        )
        if cached is not None:
            try:
                data = json.loads(cached.report_json)
                data["cached"] = True
                data["narrative"] = cached.narrative
                LOG.info("report_cache_hit symbol=%s horizon=%s", symbol, horizon)
                return data
            except (TypeError, ValueError):
                LOG.warning("cached_report_unreadable symbol=%s — regenerating", symbol)

    report = generate_report(session, symbol, horizon, persist=True)
    if report is None:
        return None
    return report_to_dict(report)
