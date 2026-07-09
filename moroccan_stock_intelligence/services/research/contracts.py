"""Structured JSON contracts for the analyst team (Phase 1, PRIORITY 2).

Every analyst returns an :class:`AnalystReport`. The schema deliberately has
**no recommendation field** — only the Chief Investment Officer (:class:`CIOReport`)
is allowed to recommend. Each :class:`Statement` is labelled ``fact | inference |
opinion`` so the reader (and, later, the LLM synthesizer) can never blur them,
and carries the raw ``evidence`` behind it so nothing is unfalsifiable.

These are plain frozen dataclasses: trivially serialisable (see
:func:`report_to_dict`) for the API today and the research database later.

Versioning discipline (locked decision): every analyst carries its own
``version`` and the engine carries ``engine_version``. Bump them whenever the
logic changes so stored reports stay reproducible and the learning engine never
compares outcomes produced by different logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

Kind = Literal["fact", "inference", "opinion"]
Polarity = Literal["bullish", "bearish", "neutral"]
Horizon = Literal["short", "medium", "long"]
Scope = Literal["symbol", "portfolio", "market"]

HORIZONS: tuple[Horizon, ...] = ("short", "medium", "long")

HORIZON_LABELS_FR: dict[str, str] = {
    "short": "Court terme",
    "medium": "Moyen terme",
    "long": "Long terme",
}


# --------------------------------------------------------------------------- #
# Building blocks                                                               #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Statement:
    """One labelled claim. ``kind`` separates facts from inference from opinion."""

    text: str
    kind: Kind = "inference"
    polarity: Polarity = "neutral"
    weight: float = 0.5  # 0..1 — how much the analyst leans on this claim
    evidence: dict = field(default_factory=dict)  # raw numbers behind the claim


@dataclass(frozen=True)
class HorizonSignal:
    """An analyst's directional lean for one horizon.

    ``lean`` is 0..100 (50 = neutral). ``components`` records the sub-scores the
    analyst computed so the reasoning is inspectable. In Phase 1 the CIO uses the
    lean for contradiction detection and attribution; the authoritative per-horizon
    score comes from the shared ``horizon_strategy`` kernel (see cio.py).
    """

    horizon: Horizon
    lean: float
    components: dict[str, float | None] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    """A possible future path expressed as a probability, never a certainty."""

    name: str
    probability: float  # 0..1
    confidence: float  # 0..100 — how sure we are of THAT probability
    rationale: str


@dataclass(frozen=True)
class AnalystReport:
    """The universal output of every analyst. NEVER carries a recommendation."""

    analyst: str  # module id: "technical" | "news" | …
    version: str
    scope: Scope = "symbol"
    headline: str = ""  # one-line French summary
    observations: list[Statement] = field(default_factory=list)  # neutral facts
    strengths: list[Statement] = field(default_factory=list)  # bullish factors
    weaknesses: list[Statement] = field(default_factory=list)  # bearish factors
    horizon_signals: list[HorizonSignal] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    risk_flags: list[Statement] = field(default_factory=list)
    confidence: float = 0.0  # 0..100
    data_used: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lean_for(self, horizon: str) -> float | None:
        for signal in self.horizon_signals:
            if signal.horizon == horizon:
                return signal.lean
        return None


# --------------------------------------------------------------------------- #
# Aggregator outputs                                                            #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskReport:
    """Risk Manager (Agent 9): aggregates risk across analysts + metrics."""

    overall_risk: float  # 0..100 (higher = riskier)
    confidence: float
    dimensions: dict[str, float]  # technical/liquidity/event/valuation/portfolio/history
    worst_case: Scenario
    base_case: Scenario
    best_case: Scenario
    drivers: list[Statement] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    version: str = "1.0"


@dataclass(frozen=True)
class HorizonVerdict:
    """The CIO's decision for ONE horizon. Recommendations may differ by horizon."""

    horizon: Horizon
    recommendation: str  # STRONG_OPPORTUNITY|WATCH|HOLD|TAKE_PROFIT|AVOID|RISKY
    recommendation_label: str
    score: float  # authoritative 0..100 (from the horizon_strategy kernel)
    confidence: float
    rationale: str
    invalidation: list[str] = field(default_factory=list)  # what would change this
    watch_next: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CIOReport:
    """Chief Investment Officer (Agent 10): the ONLY module that recommends."""

    symbol: str
    verdicts: dict[str, HorizonVerdict]  # short/medium/long
    contradictions: list[str] = field(default_factory=list)
    bull_case: list[Statement] = field(default_factory=list)
    bear_case: list[Statement] = field(default_factory=list)
    executive_summary: str = ""
    final_verdict: str = ""
    version: str = "1.0"


@dataclass(frozen=True)
class InvestmentReport:
    """The full deliverable: what the API returns and the research DB will store."""

    symbol: str
    company_name: str
    sector: str | None
    as_of: datetime
    horizon_focus: str
    cio: CIOReport
    risk: RiskReport
    analysts: dict[str, AnalystReport]  # every analyst's raw JSON (drill-down)
    scenarios: list[Scenario]
    narrative: str | None  # filled later by the Synthesizer; None = template/not yet
    engine_version: str
    disclaimer: str


# --------------------------------------------------------------------------- #
# Serialisation                                                                 #
# --------------------------------------------------------------------------- #

def report_to_dict(report: InvestmentReport) -> dict:
    """JSON-safe dict of a full report (datetimes → ISO strings)."""
    data = asdict(report)
    data["as_of"] = report.as_of.isoformat()
    return data
