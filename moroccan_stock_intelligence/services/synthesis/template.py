"""Deterministic report renderer — the default, and the fallback.

Offline, free, reproducible: the same report always renders the same prose. This is
what guarantees the platform is fully functional with no LLM configured.

It writes the full institutional structure: executive summary, thesis, bull case,
bear case, the analyst debate, risk, scenarios per horizon, recommendation by
horizon, what would invalidate it, and what to watch next.
"""

from __future__ import annotations

import logging

from moroccan_stock_intelligence.services.research.contracts import (
    HORIZON_LABELS_FR,
    HORIZONS,
    InvestmentReport,
)

LOG = logging.getLogger(__name__)


class TemplateSynthesizer:
    name = "template"

    def render(self, report: InvestmentReport) -> str:
        cio = report.cio
        lines: list[str] = []

        lines.append(f"# {report.symbol} — {report.company_name}")
        if report.sector:
            lines.append(f"*Secteur : {report.sector}*")
        lines.append("")

        lines.append("## Résumé")
        lines.append(cio.executive_summary)
        lines.append("")

        lines.append("## Recommandation par horizon")
        for horizon in HORIZONS:
            verdict = cio.verdicts.get(horizon)
            if verdict is None:
                continue
            lines.append(
                f"- **{HORIZON_LABELS_FR[horizon]}** : {verdict.recommendation_label} "
                f"(score {verdict.score:.0f}/100, confiance {verdict.confidence:.0f}/100)"
            )
            lines.append(f"  - {verdict.rationale}")
        lines.append("")

        if cio.bull_case:
            lines.append("## Thèse haussière")
            for statement in cio.bull_case:
                lines.append(f"- [{statement.kind}] {statement.text}")
            lines.append("")

        if cio.bear_case:
            lines.append("## Thèse baissière")
            for statement in cio.bear_case:
                lines.append(f"- [{statement.kind}] {statement.text}")
            lines.append("")

        if cio.debate:
            lines.append("## Débat des analystes")
            for exchange in cio.debate:
                lines.append(
                    f"- **{HORIZON_LABELS_FR[exchange.horizon]} — {exchange.topic}**"
                )
                lines.append(f"  - {exchange.bull_analyst} : {exchange.bull_claim}")
                lines.append(f"  - {exchange.bear_analyst} : {exchange.bear_claim}")
                lines.append(f"  - *Arbitrage :* {exchange.resolution}")
            lines.append("")

        lines.append("## Risque")
        lines.append(
            f"Risque global {report.risk.overall_risk:.0f}/100 "
            f"(confiance {report.risk.confidence:.0f}/100)."
        )
        for driver in report.risk.drivers:
            lines.append(f"- {driver.text}")
        lines.append("")

        if report.scenarios_by_horizon:
            lines.append("## Scénarios")
            for horizon in HORIZONS:
                scenarios = report.scenarios_by_horizon.get(horizon)
                if scenarios is None:
                    continue
                lines.append(f"### {HORIZON_LABELS_FR[horizon]}")
                for scenario in (scenarios.best, scenarios.base, scenarios.worst):
                    lines.append(
                        f"- **{scenario.name}** — probabilité {scenario.probability * 100:.0f}% "
                        f"(confiance {scenario.confidence:.0f}/100) : {scenario.rationale}"
                    )
                lines.append("")

        focus = cio.verdicts.get(report.horizon_focus)
        if focus is not None:
            if focus.invalidation:
                lines.append("## Ce qui invaliderait cette opinion")
                for item in focus.invalidation:
                    lines.append(f"- {item}")
                lines.append("")
            if focus.watch_next:
                lines.append("## À surveiller")
                for item in focus.watch_next:
                    lines.append(f"- {item}")
                lines.append("")

        missing = sorted(
            {item for analyst in report.analysts.values() for item in analyst.missing_data}
        )
        if missing:
            lines.append("## Informations manquantes")
            for item in missing[:8]:
                lines.append(f"- {item}")
            lines.append("")

        lines.append("## Verdict")
        lines.append(cio.final_verdict)
        lines.append("")
        lines.append(f"*{report.disclaimer}*")
        return "\n".join(lines)
