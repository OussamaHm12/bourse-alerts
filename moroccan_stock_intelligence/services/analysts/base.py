"""Shared analyst plumbing: the protocol, a degraded-report helper, and small utils.

The orchestrator holds the registry (an explicit, ordered list) — see
``research/orchestrator.py``. Keeping registration explicit there (rather than via
import-side-effect decorators) makes execution order deterministic and testable.
"""

from __future__ import annotations

from typing import Protocol

from moroccan_stock_intelligence.services.research.contracts import (
    AnalystReport,
    Scope,
    Statement,
)
from moroccan_stock_intelligence.services.research.context import ResearchContext


class Analyst(Protocol):
    """A symbol-scope analyst. Pure function of the context — no I/O, no DB."""

    def __call__(self, ctx: ResearchContext) -> AnalystReport: ...


def degraded_report(name: str, version: str, error: str, scope: Scope = "symbol") -> AnalystReport:
    """Returned when an analyst raises, so one failure never sinks the report."""
    return AnalystReport(
        analyst=name,
        version=version,
        scope=scope,
        headline="Analyse indisponible (erreur interne).",
        confidence=0.0,
        notes=[f"Analyste en échec : {error}"],
        missing_data=["Rapport de cet analyste indisponible pour ce cycle."],
    )


def unavailable_report(
    name: str,
    version: str,
    label: str,
    missing: list[str],
) -> AnalystReport:
    """Honest 'no data collected yet' report (company / fundamental / macro until Phase 1b)."""
    return AnalystReport(
        analyst=name,
        version=version,
        headline=f"{label} : données non collectées pour l'instant.",
        confidence=0.0,
        data_used=[],
        missing_data=missing,
        notes=[
            "Cet analyste est prêt : il produira une analyse dès que la source de "
            "données correspondante sera collectée. Aucune donnée n'est inventée."
        ],
    )


# --------------------------------------------------------------------------- #
# Small shared helpers                                                          #
# --------------------------------------------------------------------------- #

def fmt(value: float | None, decimals: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{decimals}f}".replace(",", " ")


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def fact(text: str, polarity: str = "neutral", weight: float = 0.5, **evidence) -> Statement:
    return Statement(text=text, kind="fact", polarity=polarity, weight=weight, evidence=evidence)


def inference(text: str, polarity: str = "neutral", weight: float = 0.5, **evidence) -> Statement:
    return Statement(
        text=text, kind="inference", polarity=polarity, weight=weight, evidence=evidence
    )


def opinion(text: str, polarity: str = "neutral", weight: float = 0.5, **evidence) -> Statement:
    return Statement(text=text, kind="opinion", polarity=polarity, weight=weight, evidence=evidence)


def lean_from(components: dict[str, float | None], weights: dict[str, float]) -> float:
    """Coverage-weighted mean of available components; 50 (neutral) if none."""
    available = {k: v for k, v in components.items() if v is not None and k in weights}
    total_w = sum(weights[k] for k in available)
    if not available or total_w <= 0:
        return 50.0
    raw = sum(v * weights[k] for k, v in available.items()) / total_w
    # Shrink toward neutral when few components are present (avoid fake certainty).
    coverage = total_w / sum(weights.values()) if weights else 0.0
    return round(50 + (raw - 50) * min(1.0, coverage / 0.8), 1)
