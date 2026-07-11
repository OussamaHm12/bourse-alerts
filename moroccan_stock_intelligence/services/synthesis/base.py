"""Synthesizer protocol, factory, and the anti-hallucination validator."""

from __future__ import annotations

import logging
import re
from typing import Protocol

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.research.contracts import (
    InvestmentReport,
    report_to_dict,
)

LOG = logging.getLogger(__name__)

# Numbers that may legitimately appear in prose without being "facts from the data":
# small integers (counts, horizons, list positions) and the 0-100 score scale.
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_ALLOWED_FREE_NUMBERS = {float(n) for n in range(0, 101)}
_TOLERANCE = 0.011  # numbers are rendered rounded; allow the last decimal to differ


class Synthesizer(Protocol):
    """Turns an already-decided report into prose. It may never add a fact."""

    name: str

    def render(self, report: InvestmentReport) -> str: ...


def collect_known_numbers(report: InvestmentReport) -> set[float]:
    """Every number the report legitimately contains.

    Walks the ENTIRE serialized report: numeric fields, the raw `evidence` dicts,
    and any figure already written into an analyst's own prose. A probability is
    also admitted in its percentage form (0.42 -> 42), because that is how a report
    naturally reads.

    Anything the synthesizer prints that is not in this set — and is not a small
    integer or a 0-100 score — is treated as fabricated.
    """
    known: set[float] = set(_ALLOWED_FREE_NUMBERS)

    def add_number(value: float) -> None:
        known.add(round(value, 2))
        known.add(round(value, 1))
        known.add(float(int(value)))
        if 0.0 < abs(value) <= 1.0:  # probability -> percentage
            known.add(round(value * 100))

    def walk(node) -> None:  # noqa: ANN001
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            add_number(float(node))
        elif isinstance(node, str):
            # Figures the analysts themselves wrote are, by definition, known.
            for match in _NUMBER_RE.finditer(node):
                try:
                    add_number(float(match.group().replace(",", ".")))
                except ValueError:
                    continue

    walk(report_to_dict(report))
    return known


def validate_narrative(narrative: str, report: InvestmentReport) -> tuple[bool, list[str]]:
    """Reject prose that contains numbers the report never stated.

    This is the anti-hallucination gate. It cannot catch every invented *word*, but
    fabricated financial claims are almost always numeric, and the system prompt
    forbids introducing entities. A failure here means we discard the LLM output
    entirely rather than show an unverifiable report.
    """
    if not narrative or not narrative.strip():
        return False, ["Narratif vide."]

    known = collect_known_numbers(report)
    problems: list[str] = []
    for match in _NUMBER_RE.finditer(narrative):
        raw = match.group().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        rounded = round(value, 2)
        if any(abs(rounded - candidate) <= _TOLERANCE for candidate in known):
            continue
        problems.append(f"Nombre non présent dans les données : {match.group()}")

    # Cap the report: a couple of formatting artefacts are tolerable, a stream of
    # invented figures is not.
    if len(problems) > 3:
        return False, problems[:6]
    return not problems, problems


def get_synthesizer():  # noqa: ANN201 - returns a Synthesizer implementation
    """The template synthesizer unless an LLM is explicitly configured."""
    from moroccan_stock_intelligence.services.synthesis.template import TemplateSynthesizer

    if not settings.llm_enabled:
        return TemplateSynthesizer()

    try:
        from moroccan_stock_intelligence.services.synthesis.claude import ClaudeSynthesizer

        return ClaudeSynthesizer()
    except Exception:  # noqa: BLE001 - a missing SDK must never break reports
        LOG.exception("llm_synthesizer_unavailable_falling_back_to_template")
        return TemplateSynthesizer()
