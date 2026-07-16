"""Tests for the event-driven notice classifier.

The corpus in `test_real_production_corpus` is the exact set of titles collected
from casablanca-bourse.com/fr/avis and found in the production DB, so the rules
are pinned against reality rather than against invented examples.
"""

import pytest

from moroccan_stock_intelligence.services.news import classify_event, classify_sentiment
from moroccan_stock_intelligence.services.news_classifier import (
    classify,
    event_family,
    fold,
    is_mechanical,
)

# --------------------------------------------------------------------------- #
# Regressions: the three defects the keyword model actually shipped.           #
# --------------------------------------------------------------------------- #


def test_ex_dividend_is_not_positive():
    """The old model scored this +0.6 because "dividende" sounds bullish.

    The ex-dividend date is when the price drops by the coupon. Scoring it
    positive pushed the score UP on a mechanical drop.
    """
    verdict = classify("ATW : Détachement du dividende")
    assert verdict.event_type == "ex_dividend"
    assert verdict.sentiment == "neutral"
    assert verdict.impact_score == 0.0
    assert verdict.is_mechanical is True


def test_cash_capital_increase_is_not_positive():
    """The old model scored this +0.6 on the word "augmentation". It dilutes."""
    verdict = classify("CDM : Augmentation de capital en numéraire")
    assert verdict.event_type == "capital_increase_cash"
    assert verdict.sentiment == "negative"
    assert verdict.impact_score < 0


def test_profit_warning_is_not_cancelled_by_the_word_resultat():
    """The old model returned neutral 0.0 here.

    "profit warning" (negative) and "resultat" (positive) each scored one hit,
    the counts tied, and the tie collapsed to neutral — silencing the single
    most bearish signal the source can publish.
    """
    verdict = classify("XX : Profit warning sur le résultat annuel")
    assert verdict.event_type == "profit_warning"
    assert verdict.sentiment == "negative"
    assert verdict.impact_score <= -0.8


# --------------------------------------------------------------------------- #
# Event taxonomy: one case per rule, asserted on the business meaning.          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("title", "expected_event"),
    [
        # Dividend family
        ("ATW : Détachement du dividende", "ex_dividend"),
        ("SAH : Détachement du dividende avec assimilation des deux lignes", "ex_dividend"),
        ("LBV : Mise en paiement du dividende 2025", "dividend_payment"),
        ("IAM : Distribution de dividendes au titre de l'exercice 2025", "dividend_announcement"),
        ("XYZ : Réduction du dividende au titre de 2025", "dividend_cut"),
        ("XYZ : Suspension du dividende", "dividend_cut"),
        # Capital actions
        (
            "CDM : Valeur théorique du droit de souscription relatif à "
            "l'augmentation de capital en numéraire",
            "subscription_right",
        ),
        ("CDM : Augmentation de capital en numéraire", "capital_increase_cash"),
        ("XYZ : Augmentation de capital par incorporation de réserves", "capital_increase_reserves"),
        (
            "SAH : Augmentation de capital au titre de la fusion-absorption réservée "
            'aux "Actionnaires de Allianz Maroc"',
            "capital_increase_merger",
        ),
        ("XYZ : Augmentation de capital réservée aux salariés", "capital_increase_employees"),
        ("XYZ : Fusion-absorption de la filiale", "merger"),
        ("XYZ : OPA visant les actions de la société", "tender_offer_buy"),
        ("XYZ : OPR sur les titres restants", "tender_offer_withdraw"),
        ("XYZ : OPV dans le cadre de l'introduction en bourse", "public_offering"),
        ("XYZ : Programme de rachat d'actions", "share_buyback"),
        ("XYZ : Division du nominal par 10", "stock_split"),
        # Trading notices
        ("XYZ : Suspension de cotation", "trading_suspension"),
        ("XYZ : Reprise de cotation", "trading_resumption"),
        ("XYZ : Radiation de la cote", "delisting"),
        # Results
        ("XYZ : Résultats annuels 2025", "results"),
        ("XYZ : Chiffre d'affaires du premier trimestre", "results"),
        ("XYZ : Indicateurs trimestriels au 30 septembre", "results"),
        ("XYZ : Perte nette au titre de l'exercice", "profit_warning"),
        # Generic / market-level
        ("XYZ : Franchissement de seuil de participation", "threshold_crossing"),
        ("SGMAT : Admission d'une échéance du contrat à terme ferme sur indice MASI 20", "market_notice"),
        ("Réglementation", "market_notice"),
        ("Avis totalement inconnu du référentiel", "announcement"),
    ],
)
def test_event_taxonomy(title, expected_event):
    assert classify(title).event_type == expected_event


# --------------------------------------------------------------------------- #
# Business rules: the direction each event implies, and why.                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "title",
    [
        "XX : Profit warning sur le résultat annuel",
        "XX : Radiation de la cote",
        "XX : Réduction du dividende",
        "XX : Suspension de cotation",
        "CDM : Augmentation de capital en numéraire",
        "XX : Résultats annuels en forte baisse",
        "XX : Perte nette au titre de l'exercice",
    ],
)
def test_negative_events(title):
    assert classify(title).sentiment == "negative"


@pytest.mark.parametrize(
    "title",
    [
        "IAM : Distribution de dividendes au titre de 2025",
        "XX : Programme de rachat d'actions",
        "XX : OPA visant les actions de la société",
        "XX : Résultats annuels en forte progression",
        "LBV : Mise en paiement du dividende 2025",
    ],
)
def test_positive_events(title):
    assert classify(title).sentiment == "positive"


@pytest.mark.parametrize(
    "title",
    [
        # Mechanical: the holder is made whole, so it is not directional news.
        "ATW : Détachement du dividende",
        "XX : Augmentation de capital par incorporation de réserves",
        "XX : Division du nominal par 10",
        # Ambiguous without the deal terms: staying silent beats guessing.
        "XX : Fusion-absorption de la filiale",
        "XX : Résultats annuels 2025",
        "XX : Franchissement de seuil de participation",
        # Not about an issuer at all.
        "Réglementation",
    ],
)
def test_neutral_events(title):
    verdict = classify(title)
    assert verdict.sentiment == "neutral"
    assert verdict.impact_score == 0.0


def test_dilution_is_not_double_counted():
    """The exchange publishes the subscription-right value as its OWN notice.

    Both notices reference "augmentation de capital". If the right notice were
    also scored as a capital increase, the same dilution would be counted twice
    for the same operation — CDM has exactly this pair in production.
    """
    operation = classify("CDM : Augmentation de capital en numéraire")
    right = classify(
        "CDM : Valeur théorique du droit de souscription relatif à "
        "l'augmentation de capital en numéraire"
    )
    assert operation.impact_score < 0
    assert right.impact_score == 0.0
    assert right.is_mechanical is True


def test_results_direction_read_only_when_stated():
    """A results title is scored only if it states the direction."""
    assert classify("XX : Résultats annuels 2025").impact_score == 0.0
    assert classify("XX : Résultats annuels en hausse").impact_score > 0
    assert classify("XX : Résultats annuels en forte baisse").impact_score < 0


def test_qualifier_never_overrides_a_strong_event():
    """"en hausse" must not soften a profit warning: a qualifier refines, it never arbitrates."""
    verdict = classify("XX : Profit warning malgré un chiffre d'affaires en hausse")
    assert verdict.event_type == "profit_warning"
    assert verdict.impact_score <= -0.8


def test_severity_is_ordered():
    """A profit warning must outrank a dilution, which must outrank a filing notice."""
    warning = classify("XX : Profit warning sur le résultat annuel").impact_score
    dilution = classify("XX : Augmentation de capital en numéraire").impact_score
    mechanical = classify("XX : Détachement du dividende").impact_score
    assert warning < dilution < mechanical


# --------------------------------------------------------------------------- #
# Robustness: the shapes the source actually varies on.                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "title",
    [
        "ATW : Détachement du dividende",
        "ATW : DETACHEMENT DU DIVIDENDE",
        "ATW : detachement du dividende",
        "ATW : Détachement  du   dividende",  # collapsed whitespace
        "ATW : Détachement du dividende",  # non-breaking space
    ],
)
def test_accent_case_and_whitespace_insensitive(title):
    assert classify(title).event_type == "ex_dividend"


def test_curly_apostrophe_is_handled():
    """Real titles mix U+2019 and U+0027 — the SGMAT row in production uses U+2019."""
    curly = classify("SGMAT : Admission d’une échéance du contrat à terme ferme sur indice MASI 20")
    straight = classify("SGMAT : Admission d'une échéance du contrat à terme ferme sur indice MASI 20")
    assert curly.event_type == straight.event_type == "market_notice"


def test_hyphen_variants_are_equivalent():
    assert classify("XX : Fusion-absorption").event_type == classify("XX : Fusion absorption").event_type


def test_word_boundaries_prevent_substring_false_positives():
    """"opa" must not fire inside "opaque" — a bare substring match would read a
    takeover bid into a governance notice."""
    assert classify("XX : Communication sur une structure opaque").event_type == "announcement"
    assert classify("XX : Note d'information sur le capital social").event_type == "announcement"
    # ...while the real token still classifies.
    assert classify("XX : OPA visant les actions").event_type == "tender_offer_buy"


@pytest.mark.parametrize("title", [None, "", "   ", " "])
def test_empty_input_is_total_and_neutral(title):
    verdict = classify(title)
    assert verdict.event_type == "announcement"
    assert verdict.impact_score == 0.0
    assert verdict.sentiment == "neutral"


def test_unknown_title_does_not_invent_a_signal():
    verdict = classify("Avis relatif à une procédure administrative interne")
    assert verdict.event_type == "announcement"
    assert verdict.impact_score == 0.0
    assert verdict.rationale


def test_fold_is_idempotent():
    once = fold("SAH : Détachement du dividende — fusion-absorption")
    assert fold(once) == once


# --------------------------------------------------------------------------- #
# Invariants that must hold for every title, whatever the rules become.        #
# --------------------------------------------------------------------------- #

_CORPUS = [
    "Réglementation",
    "NKL : Détachement du dividende",
    "ATW : Détachement du dividende",
    "CDM : Valeur théorique du droit de souscription relatif à l'augmentation de capital en numéraire",
    "SAH : Détachement du dividende avec assimilation des deux lignes",
    "SOT : Détachement du dividende",
    'SAH : Augmentation de capital au titre de la fusion-absorption réservée aux "Actionnaires de Allianz Maroc"',
    "SGMAT : Admission d’une échéance du contrat à terme ferme sur indice MASI 20",
    "CDM : Augmentation de capital en numéraire",
    "CIH : Détachement du dividende",
    "BCI : Détachement du dividende",
]


@pytest.mark.parametrize("title", _CORPUS)
def test_real_production_corpus(title):
    """Every title currently in the production DB must classify without exploding."""
    verdict = classify(title)
    assert -1.0 <= verdict.impact_score <= 1.0
    assert verdict.sentiment in {"positive", "negative", "neutral"}
    assert verdict.event_type
    assert verdict.rationale
    assert event_family(verdict.event_type) in {
        "dividend",
        "capital_action",
        "results",
        "trading_notice",
        "announcement",
    }


def test_production_corpus_is_no_longer_uniformly_positive():
    """The old classifier labelled 9 of these 11 positive and none negative.

    Every one of those 9 was a mechanical detachment or a dilutive operation.
    """
    sentiments = [classify(title).sentiment for title in _CORPUS]
    assert sentiments.count("positive") == 0
    assert sentiments.count("negative") == 1  # the CDM cash capital increase
    assert sentiments.count("neutral") == 10


@pytest.mark.parametrize("title", _CORPUS)
def test_sentiment_label_always_agrees_with_impact_sign(title):
    verdict = classify(title)
    if verdict.sentiment == "positive":
        assert verdict.impact_score > 0
    elif verdict.sentiment == "negative":
        assert verdict.impact_score < 0
    else:
        assert -0.15 < verdict.impact_score < 0.15


def test_mechanical_events_never_carry_a_signal():
    """A mechanical event must be exactly 0.0: arithmetic is not information."""
    for title in _CORPUS:
        verdict = classify(title)
        if verdict.is_mechanical:
            assert verdict.impact_score == 0.0
            assert verdict.sentiment == "neutral"


# --------------------------------------------------------------------------- #
# Family folding: the refined taxonomy must not break existing consumers.      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("event_type", "expected_family"),
    [
        ("ex_dividend", "dividend"),
        ("dividend_announcement", "dividend"),
        ("dividend_cut", "dividend"),
        ("capital_increase_cash", "capital_action"),
        ("subscription_right", "capital_action"),
        ("merger", "capital_action"),
        ("profit_warning", "results"),
        ("results", "results"),
        ("trading_suspension", "trading_notice"),
        ("delisting", "trading_notice"),
        ("market_notice", "announcement"),
        ("announcement", "announcement"),
    ],
)
def test_event_family_folds_to_the_legacy_taxonomy(event_type, expected_family):
    assert event_family(event_type) == expected_family


@pytest.mark.parametrize(
    "legacy_value", ["dividend", "capital_action", "results", "trading_notice", "announcement"]
)
def test_legacy_rows_still_resolve_to_their_own_family(legacy_value):
    """Rows written by the old classifier are still in the DB and must keep working."""
    assert event_family(legacy_value) == legacy_value


def test_event_family_is_total():
    assert event_family(None) == "announcement"
    assert event_family("") == "announcement"
    assert event_family("something_invented_later") == "announcement"


def test_has_dividend_predicate_still_fires_on_a_detachment():
    """`has_dividend` drives the long-horizon `evenements` component.

    Splitting `dividend` into `ex_dividend`/`dividend_announcement` must not
    silently turn that predicate off — which is exactly what comparing
    `event_type == "dividend"` would now do.
    """
    verdict = classify("ATW : Détachement du dividende")
    assert verdict.event_type != "dividend"  # the taxonomy did get finer...
    assert event_family(verdict.event_type) == "dividend"  # ...but the family holds


def test_is_mechanical_helper_matches_the_classification():
    verdict = classify("ATW : Détachement du dividende")
    assert is_mechanical(verdict.event_type) is verdict.is_mechanical is True
    assert is_mechanical("capital_increase_cash") is False
    assert is_mechanical(None) is False


# --------------------------------------------------------------------------- #
# The collector-facing wrappers kept in news.py.                               #
# --------------------------------------------------------------------------- #


def test_news_module_wrappers_delegate_to_the_classifier():
    assert classify_event("ATW : Détachement du dividende") == "ex_dividend"
    sentiment, impact = classify_sentiment("CDM : Augmentation de capital en numéraire")
    assert sentiment == "negative"
    assert impact < 0
