"""Optional Claude-backed report narrator.

Active ONLY when `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` are set. The
platform is fully functional without it.

Hard guarantees:
  * Claude receives ONLY the structured InvestmentReport JSON — never raw HTML,
    never scraped page text, never a database row.
  * The system prompt forbids introducing any fact not present in that JSON.
  * The output is VALIDATED (`validate_narrative`): any number that does not
    appear in the report is treated as a fabrication.
  * On refusal, API error, timeout, or failed validation we fall back to the
    deterministic TemplateSynthesizer. A report is never blocked on the LLM, and
    an unverifiable narrative is never shown.

Note on parameters: `temperature` / `top_p` / `top_k` are rejected (400) on
claude-opus-4-8, and `budget_tokens` is removed — so neither is sent.
"""

from __future__ import annotations

import json
import logging

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.services.research.contracts import (
    InvestmentReport,
    report_to_dict,
)
from moroccan_stock_intelligence.services.synthesis.base import validate_narrative
from moroccan_stock_intelligence.services.synthesis.template import TemplateSynthesizer

LOG = logging.getLogger(__name__)

MAX_TOKENS = 8000

SYSTEM_PROMPT = """Tu es un rédacteur de recherche financière pour la Bourse de Casablanca.

On te fournit UNIQUEMENT un rapport d'investissement déjà produit, au format JSON
structuré. Toutes les décisions ont déjà été prises par les analystes et le
directeur des investissements (CIO). Ton rôle est de RÉDIGER, pas d'analyser.

RÈGLES ABSOLUES :
1. N'introduis JAMAIS un chiffre, un fait, une date, une société ou un événement
   qui ne figure pas dans le JSON. Aucune connaissance extérieure.
2. Ne modifie JAMAIS une recommandation, un score, une probabilité ou une
   confiance. Reprends-les exactement.
3. Respecte les étiquettes : ce qui est marqué "fact" est un fait, "inference"
   une déduction, "opinion" une opinion. Ne transforme pas une inférence en fait.
4. Exprime l'incertitude. N'affirme jamais une certitude sur l'avenir.
5. Cite le module à l'origine de chaque conclusion (technical, news, fundamental,
   macro, company, portfolio, historical_behaviour, market_structure, risk, cio).
6. Si une information est absente, dis-le explicitement. N'invente rien pour
   combler un vide.

Rédige en français, en markdown, dans le style d'une note de recherche
institutionnelle : Résumé, Thèse, Cas haussier, Cas baissier, Débat des analystes,
Risque, Scénarios, Recommandation par horizon, Ce qui invaliderait la thèse,
À surveiller, Verdict. Termine par le disclaimer fourni dans le JSON."""


class ClaudeSynthesizer:
    name = "claude"

    def __init__(self) -> None:
        import anthropic  # imported lazily: a missing SDK must not break reports

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_seconds,
        )
        self._fallback = TemplateSynthesizer()

    def render(self, report: InvestmentReport) -> str:
        payload = json.dumps(report_to_dict(report), ensure_ascii=False, indent=2)
        try:
            response = self._client.messages.create(
                model=settings.llm_model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Rédige la note de recherche à partir de ce rapport structuré. "
                            "N'ajoute aucune information absente du JSON.\n\n"
                            f"```json\n{payload}\n```"
                        ),
                    }
                ],
            )
        except self._anthropic.RateLimitError:
            LOG.warning("llm_rate_limited_falling_back symbol=%s", report.symbol)
            return self._fallback.render(report)
        except self._anthropic.APIStatusError as exc:
            LOG.warning(
                "llm_api_error_falling_back symbol=%s status=%s", report.symbol, exc.status_code
            )
            return self._fallback.render(report)
        except self._anthropic.APIConnectionError:
            LOG.warning("llm_unreachable_falling_back symbol=%s", report.symbol)
            return self._fallback.render(report)
        except Exception:  # noqa: BLE001 - synthesis must never break a report
            LOG.exception("llm_unexpected_error_falling_back symbol=%s", report.symbol)
            return self._fallback.render(report)

        if response.stop_reason == "refusal":
            LOG.warning("llm_refused_falling_back symbol=%s", report.symbol)
            return self._fallback.render(report)

        narrative = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

        # The anti-hallucination gate. An unverifiable narrative is discarded.
        valid, problems = validate_narrative(narrative, report)
        if not valid:
            LOG.warning(
                "llm_validation_failed_falling_back symbol=%s problems=%s",
                report.symbol,
                problems[:3],
            )
            return self._fallback.render(report)

        LOG.info("llm_narrative_ok symbol=%s chars=%s", report.symbol, len(narrative))
        return narrative
