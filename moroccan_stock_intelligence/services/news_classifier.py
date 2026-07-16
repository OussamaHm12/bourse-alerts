"""Event-driven classification of Casablanca Bourse official notices.

The source (`/fr/avis`) does NOT publish editorial news: it publishes *procedural
notices* about corporate actions — an ex-dividend date, the terms of a capital
increase, the theoretical value of a subscription right. Those carry a mechanical
price effect, not a tone. Reading them with a bag-of-words sentiment model is a
category error, and it produced three real defects in the previous version:

  * "Détachement du dividende" scored +0.6 (positive) because the word "dividende"
    is bullish-sounding — while the ex-dividend date is exactly when the price
    drops by the coupon. The model pushed the score UP on a mechanical drop.
  * "Augmentation de capital" scored +0.6 because of the word "augmentation",
    while a cash capital increase dilutes existing holders.
  * "Profit warning sur le résultat annuel" scored 0.0 (neutral): the negative
    term was CANCELLED by the incidental positive word "résultat", because the
    old model compared counts of positive vs negative hits and called a tie.

So sentiment here is derived, in order, from:

  1. the EVENT, identified by an ordered rule table (most specific rule wins), and
  2. a small set of qualifiers that only apply where the event alone is genuinely
     ambiguous (a results publication).

Two principles from ARCHITECTURE_AI_ANALYST.md are load-bearing here:

* **Never fabricate.** Most notices are procedural and carry no directional
  information. They return 0.0 — not a small invented number. A classifier that
  manufactures a signal from a filing date is worse than one that stays silent.
* **Facts vs inference.** A mechanical event (`is_mechanical`) is a *fact* about
  price arithmetic — the holder is made whole, so it is not bearish news. It is
  kept distinct from an informational event, which is where a real signal lives.

`event_type` is the precise taxonomy. `event_family()` folds it back to the five
coarse values the rest of the platform already consumes (`has_dividend`,
`has_results`, the knowledge harvester), so refining the taxonomy does not
silently change any existing behaviour — and legacy rows written before this
module keep resolving to their own family.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from moroccan_stock_intelligence.utils import normalize_text

# --------------------------------------------------------------------------- #
# Families: the coarse taxonomy the rest of the platform already reads.         #
# --------------------------------------------------------------------------- #

FAMILY_DIVIDEND = "dividend"
FAMILY_CAPITAL_ACTION = "capital_action"
FAMILY_RESULTS = "results"
FAMILY_TRADING_NOTICE = "trading_notice"
FAMILY_ANNOUNCEMENT = "announcement"

_FAMILY: dict[str, str] = {
    # Dividend
    "ex_dividend": FAMILY_DIVIDEND,
    "dividend_payment": FAMILY_DIVIDEND,
    "dividend_announcement": FAMILY_DIVIDEND,
    "dividend_cut": FAMILY_DIVIDEND,
    # Capital actions
    "subscription_right": FAMILY_CAPITAL_ACTION,
    "capital_increase_cash": FAMILY_CAPITAL_ACTION,
    "capital_increase_reserves": FAMILY_CAPITAL_ACTION,
    "capital_increase_employees": FAMILY_CAPITAL_ACTION,
    "capital_increase_merger": FAMILY_CAPITAL_ACTION,
    "merger": FAMILY_CAPITAL_ACTION,
    "tender_offer_buy": FAMILY_CAPITAL_ACTION,
    "tender_offer_withdraw": FAMILY_CAPITAL_ACTION,
    "public_offering": FAMILY_CAPITAL_ACTION,
    "share_buyback": FAMILY_CAPITAL_ACTION,
    "stock_split": FAMILY_CAPITAL_ACTION,
    # Results
    "profit_warning": FAMILY_RESULTS,
    "results": FAMILY_RESULTS,
    # Trading notices
    "trading_suspension": FAMILY_TRADING_NOTICE,
    "trading_resumption": FAMILY_TRADING_NOTICE,
    "delisting": FAMILY_TRADING_NOTICE,
    # Generic / not company-specific
    "threshold_crossing": FAMILY_ANNOUNCEMENT,
    "market_notice": FAMILY_ANNOUNCEMENT,
    "announcement": FAMILY_ANNOUNCEMENT,
    # Legacy values written before this module: resolve to themselves so rows
    # collected by the old classifier keep behaving exactly as they did.
    FAMILY_DIVIDEND: FAMILY_DIVIDEND,
    FAMILY_CAPITAL_ACTION: FAMILY_CAPITAL_ACTION,
    FAMILY_RESULTS: FAMILY_RESULTS,
    FAMILY_TRADING_NOTICE: FAMILY_TRADING_NOTICE,
    FAMILY_ANNOUNCEMENT: FAMILY_ANNOUNCEMENT,
}

# Events whose price effect is arithmetic, not informational. The holder is made
# whole (a coupon paid, shares multiplied), so the move is NOT a bearish signal
# and must never be scored as one. Kept separate so a later integration can
# explain a price drop instead of reading it as weakness.
MECHANICAL_EVENTS: frozenset[str] = frozenset(
    {"ex_dividend", "capital_increase_reserves", "stock_split", "subscription_right"}
)

# Notices about the market itself (indices, derivatives, regulation) rather than
# about an issuer. They carry no company signal whatsoever.
NON_COMPANY_EVENTS: frozenset[str] = frozenset({"market_notice"})


@dataclass(frozen=True)
class Classification:
    """The full, explainable verdict for one notice title."""

    event_type: str
    family: str
    sentiment: str  # "positive" | "negative" | "neutral"
    impact_score: float  # -1.0 .. +1.0
    is_mechanical: bool
    is_company_event: bool
    rationale: str  # French, user-facing: why this score and not another


# --------------------------------------------------------------------------- #
# Text folding: the source mixes accents, curly apostrophes and hyphens.        #
# --------------------------------------------------------------------------- #

def fold(value: str | None) -> str:
    """Lowercase, strip accents, and flatten the separators the source varies on.

    Real titles contain "d’une" (U+2019) next to "l'augmentation" (U+0027), and
    "fusion-absorption" next to "fusion absorption". Folding all of them to one
    shape lets every rule below be written once, in plain ASCII.
    """
    text = normalize_text(value)
    if not text:
        return ""
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = text.replace("œ", "oe").replace("Œ", "OE")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[-–—/]", " ", text)
    return " ".join(text.split()).lower()


@dataclass(frozen=True)
class _Rule:
    """One classification rule. `any_of` fires it, `all_of` further constrains it."""

    event_type: str
    any_of: tuple[str, ...]
    all_of: tuple[str, ...] = ()
    why: str = ""


# Ordered, most specific first. The FIRST rule that matches wins outright — there
# is no scoring across rules and no tie to resolve, which is precisely what made
# the old model cancel a profit warning against the word "résultat".
_RULES: tuple[_Rule, ...] = (
    # --- Unambiguous, high-severity signals. Deliberately first: nothing later
    #     in the table may soften them.
    _Rule(
        "profit_warning",
        (r"profit\s+warning", r"avertissement\s+sur\s+(?:le\s+|les\s+)?resultat"),
        why="Avertissement sur résultat : signal baissier fort et explicite.",
    ),
    _Rule(
        "delisting",
        (r"\bradiation\b", r"retrait\s+de\s+la\s+cote"),
        why="Radiation de la cote : perte de liquidité et de valorisation.",
    ),
    _Rule(
        "trading_suspension",
        (r"suspension\s+de\s+(?:la\s+)?cotation", r"suspension\s+de\s+cours"),
        why="Suspension de cotation : incertitude, souvent en amont d'une annonce sensible.",
    ),
    _Rule(
        "trading_resumption",
        (r"reprise\s+de\s+(?:la\s+)?cotation",),
        why="Reprise de cotation : levée de l'incertitude.",
    ),
    # --- Dividend family. The detachment is a scheduled arithmetic adjustment and
    #     must be separated from an actual distribution decision.
    _Rule(
        "dividend_cut",
        (
            r"(?:reduction|baisse|diminution)\s+du\s+dividende",
            r"suspension\s+du\s+dividende",
            r"non\s+distribution\s+du\s+dividende",
        ),
        why="Réduction ou suppression du dividende : signal négatif sur la trésorerie.",
    ),
    _Rule(
        "ex_dividend",
        (r"detachement\s+d(?:u|e)\s+(?:dividende|coupon)", r"detachement\s+du\s+droit"),
        why=(
            "Détachement du dividende : le cours baisse mécaniquement du montant du "
            "coupon, que l'actionnaire encaisse. Aucun signal directionnel."
        ),
    ),
    _Rule(
        "dividend_payment",
        (r"mise\s+en\s+paiement", r"paiement\s+du\s+dividende"),
        why="Mise en paiement du dividende : confirme le versement, signal faiblement positif.",
    ),
    # --- Capital actions. `subscription_right` sits BEFORE the capital-increase
    #     rules on purpose: the exchange publishes a separate notice for the
    #     theoretical value of the right, and counting it as a second capital
    #     increase would double-count the same dilution.
    _Rule(
        "subscription_right",
        (r"droits?\s+de\s+souscription",),
        why=(
            "Avis technique sur la valeur du droit de souscription : la dilution est "
            "déjà portée par l'avis d'augmentation de capital, elle n'est pas comptée deux fois."
        ),
    ),
    _Rule(
        "capital_increase_reserves",
        (r"incorporation\s+de\s+reserves",),
        why="Augmentation de capital par incorporation de réserves : purement mécanique (actions gratuites).",
    ),
    _Rule(
        "capital_increase_employees",
        (r"augmentation\s+de\s+capital",),
        all_of=(r"reservee?\s+aux\s+salaries",),
        why="Augmentation de capital réservée aux salariés : dilution marginale.",
    ),
    _Rule(
        "capital_increase_merger",
        (r"augmentation\s+de\s+capital",),
        all_of=(r"fusion|absorption|apport",),
        why=(
            "Augmentation de capital liée à une fusion : l'effet dépend entièrement des "
            "termes de l'opération, que le libellé ne donne pas. Neutre par honnêteté."
        ),
    ),
    _Rule(
        "capital_increase_cash",
        (r"augmentation\s+de\s+capital\s+en\s+numeraire", r"augmentation\s+de\s+capital"),
        why="Augmentation de capital en numéraire : dilutive pour l'actionnaire existant.",
    ),
    _Rule(
        "share_buyback",
        (r"rachat\s+d'actions", r"programme\s+de\s+rachat"),
        why="Rachat d'actions : soutient le cours et signale la confiance du management.",
    ),
    _Rule(
        "stock_split",
        (r"division\s+du\s+nominal", r"division\s+de\s+la\s+valeur\s+nominale", r"\bsplit\b"),
        why="Division du nominal : mécanique, la capitalisation est inchangée.",
    ),
    _Rule(
        "tender_offer_buy",
        (r"\bopa\b", r"offre\s+publique\s+d'achat"),
        why="Offre publique d'achat : généralement assortie d'une prime pour la cible.",
    ),
    _Rule(
        "tender_offer_withdraw",
        (r"\bopr\b", r"offre\s+publique\s+de\s+retrait"),
        why="Offre publique de retrait : sortie de cote, effet ambigu sans les termes de l'offre.",
    ),
    _Rule(
        "public_offering",
        (r"\bopv\b", r"offre\s+publique\s+de\s+vente", r"introduction\s+en\s+bourse"),
        why="Offre publique de vente : neutre en soi pour le titre.",
    ),
    _Rule(
        "merger",
        (r"fusion\s+absorption", r"\bfusion\b", r"\babsorption\b"),
        why="Opération de fusion : l'effet dépend des termes, non déductibles du libellé.",
    ),
    # --- Results. The bare event is genuinely ambiguous: the title rarely states
    #     the direction. Qualifiers below refine it when — and only when — it does.
    _Rule(
        "profit_warning",
        (r"\bperte\b", r"\bpertes\b", r"\bdeficit\b", r"resultat\s+negatif"),
        why="Perte ou déficit annoncé : signal baissier fort.",
    ),
    _Rule(
        "results",
        (
            r"\bresultats?\b",
            r"chiffre\s+d'affaires",
            r"indicateurs\s+(?:trimestriels|annuels|semestriels)",
            r"\bbenefices?\b",
        ),
        why="Publication de résultats : direction non déductible du seul libellé.",
    ),
    _Rule(
        "threshold_crossing",
        (r"franchissement\s+de\s+seuil",),
        why="Franchissement de seuil : information de détention, sans effet directionnel établi.",
    ),
    # --- Market-level notices: about the market, not about an issuer.
    _Rule(
        "market_notice",
        (
            r"contrat\s+a\s+terme",
            r"\bindice\b",
            r"\bmasi\b",
            r"reglementation",
            r"admission\s+d'une\s+echeance",
            r"calendrier\s+de\s+bourse",
            r"jours?\s+feries?",
        ),
        why="Avis de marché (indice, dérivé, réglementation) : ne concerne pas un émetteur en particulier.",
    ),
    _Rule(
        "dividend_announcement",
        (r"distribution\s+d(?:e|es|u)\s+dividende", r"\bdividende\b", r"\bcoupon\b"),
        why="Annonce de distribution de dividende : signal positif sur la trésorerie.",
    ),
)

# Base impact per event, in [-1, +1]. A 0.0 means "this notice carries no
# directional information" — an honest silence, not a missing value.
_BASE_IMPACT: dict[str, float] = {
    "profit_warning": -0.85,
    "delisting": -0.80,
    "dividend_cut": -0.60,
    "trading_suspension": -0.50,
    "capital_increase_cash": -0.35,
    "capital_increase_employees": -0.10,
    "capital_increase_merger": 0.0,
    "merger": 0.0,
    "ex_dividend": 0.0,
    "capital_increase_reserves": 0.0,
    "stock_split": 0.0,
    "subscription_right": 0.0,
    "tender_offer_withdraw": 0.0,
    "public_offering": 0.0,
    "threshold_crossing": 0.0,
    "market_notice": 0.0,
    "results": 0.0,
    "announcement": 0.0,
    "trading_resumption": 0.10,
    "dividend_payment": 0.20,
    "dividend_announcement": 0.35,
    "share_buyback": 0.40,
    "tender_offer_buy": 0.50,
}

# Directional qualifiers. Applied ONLY to `results`, the one event whose direction
# the title sometimes states and the event alone cannot settle. They never touch a
# mechanical event and never override a strong signal — a qualifier refines, it
# does not arbitrate.
_RESULT_QUALIFIERS: tuple[tuple[float, tuple[str, ...], str], ...] = (
    (
        -0.55,
        (r"en\s+(?:forte\s+|nette\s+)?(?:baisse|recul|repli|degradation|contraction)", r"\bchute\b"),
        "résultats annoncés en baisse",
    ),
    (
        0.50,
        (
            r"en\s+(?:forte\s+|nette\s+)?(?:hausse|progression|croissance|amelioration)",
            r"\brecord\b",
        ),
        "résultats annoncés en hausse",
    ),
)

_POSITIVE_THRESHOLD = 0.15
_NEGATIVE_THRESHOLD = -0.15


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _label(impact: float) -> str:
    if impact >= _POSITIVE_THRESHOLD:
        return "positive"
    if impact <= _NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def classify(title: str | None) -> Classification:
    """Classify one official notice title into an event and a defensible impact.

    Total: an unrecognised title yields a neutral `announcement` with impact 0.0
    rather than a guess.
    """
    text = fold(title)
    if not text:
        return Classification(
            event_type="announcement",
            family=FAMILY_ANNOUNCEMENT,
            sentiment="neutral",
            impact_score=0.0,
            is_mechanical=False,
            is_company_event=False,
            rationale="Libellé vide : aucun événement identifiable.",
        )

    event_type = "announcement"
    why = "Avis non reconnu : aucun effet directionnel déduit (aucune supposition)."
    for rule in _RULES:
        if not _matches(rule.any_of, text):
            continue
        if rule.all_of and not all(re.search(pattern, text) for pattern in rule.all_of):
            continue
        event_type = rule.event_type
        why = rule.why
        break

    impact = _BASE_IMPACT.get(event_type, 0.0)

    if event_type == "results":
        for delta, patterns, note in _RESULT_QUALIFIERS:
            if _matches(patterns, text):
                impact = delta
                why = f"Publication de résultats, {note} : direction lue dans le libellé."
                break

    impact = max(-1.0, min(1.0, impact))
    return Classification(
        event_type=event_type,
        family=_FAMILY.get(event_type, FAMILY_ANNOUNCEMENT),
        sentiment=_label(impact),
        impact_score=round(impact, 2),
        is_mechanical=event_type in MECHANICAL_EVENTS,
        is_company_event=event_type not in NON_COMPANY_EVENTS,
        rationale=why,
    )


def event_family(event_type: str | None) -> str:
    """Fold a precise `event_type` back to the coarse family the platform reads.

    Handles legacy values, so rows collected before this module keep resolving to
    the same family they were written with.
    """
    if not event_type:
        return FAMILY_ANNOUNCEMENT
    return _FAMILY.get(event_type, FAMILY_ANNOUNCEMENT)


def is_mechanical(event_type: str | None) -> bool:
    """True when the price effect is arithmetic rather than informational."""
    return (event_type or "") in MECHANICAL_EVENTS
