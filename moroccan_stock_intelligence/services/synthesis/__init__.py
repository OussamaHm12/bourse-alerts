"""Phase 10 — report synthesis.

The platform is FULLY functional with no LLM. Synthesis only changes how a report
is WORDED, never what it says: the analysts and the CIO have already decided
everything by the time a synthesizer runs.

Two implementations, one interface:
  * TemplateSynthesizer — deterministic, offline, free. The default.
  * ClaudeSynthesizer   — optional, active only when LLM_PROVIDER=anthropic and
                          ANTHROPIC_API_KEY is set. Receives ONLY the structured
                          report JSON — never raw HTML, never scraped text — and
                          its output is validated before use. Any validation
                          failure falls back to the template.
"""

from moroccan_stock_intelligence.services.synthesis.base import Synthesizer, get_synthesizer

__all__ = ["Synthesizer", "get_synthesizer"]
