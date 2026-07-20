"""Per-horizon scoring models (short / medium / long) with explainability.

Each horizon scores 0-100 as a weighted mean of its AVAILABLE components only.
A missing metric is never replaced by a guess: it lowers the coverage (the sum
of available weights), it is listed in `missing`, and it caps the confidence.

Formulas (weights documented in the *_WEIGHTS dicts below):

- short  = 0.30 momentum(1j/5j) + 0.20 volume + 0.20 cassure + 0.15 support + 0.15 actus
           (minus a small "surchauffe" penalty after a > +4% day)
- medium = 0.35 tendance(30j/90j) + 0.25 moyennes mobiles + 0.15 secteur
           + 0.15 volatilite (inverse) + 0.10 actus
- long   = 0.25 tendance longue (90j + MM200 si >= 180 j d'historique)
           + 0.20 stabilite (volatilite + drawdown) + 0.15 structure 52 semaines
           + 0.10 secteur + 0.10 evenements (dividende / resultats)
           + 0.20 fondamentaux (PER, PBR, ROE, rendement — Phase 1b)

- risk       = clamp(volatilite + momentum negatif + drawdown + volume de baisse
               + actus negatives + incertitude d'historique), 0-100 (haut = risque)
- confidence = 50*coverage + 30*min(historique/cible, 1) + 20*coherence des signaux,
               plafonnee a 35 si coverage < 50%. Cibles : 30/90/250 jours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev

from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.utils import clamp

HORIZONS = ("short", "medium", "long")

HORIZON_LABELS_FR = {
    "short": "Court terme",
    "medium": "Moyen terme",
    "long": "Long terme",
}

# Days of collected price history needed for full confidence on each horizon.
HISTORY_TARGET_DAYS = {"short": 30, "medium": 90, "long": 250}

SHORT_WEIGHTS = {
    "momentum_court": 0.30,
    "volume": 0.20,
    "cassure": 0.20,
    "support": 0.15,
    "actualites": 0.15,
}
MEDIUM_WEIGHTS = {
    "tendance": 0.35,
    "moyennes_mobiles": 0.25,
    "secteur": 0.15,
    "volatilite": 0.15,
    "actualites": 0.10,
}
LONG_WEIGHTS = {
    "tendance_longue": 0.25,
    "stabilite": 0.20,
    "structure_52s": 0.15,
    "secteur": 0.10,
    "evenements": 0.10,
    # Added once the Phase 1b collectors actually populated `fundamentals`. Until
    # then this module hard-coded "Fondamentaux non collectés" into every long
    # assessment — a statement that had been false since the issuer collector
    # shipped (AUDIT_2026-07-18.md §7). A stock at PER 45 scored exactly like one
    # at PER 8 over a six-month horizon, which is the one horizon where valuation
    # is supposed to matter most.
    #
    # 20% is a stated prior, not a fitted weight: it is large enough that valuation
    # can change a verdict, small enough that six published ratios cannot outvote
    # three years of price behaviour. The backtest's ablation study
    # (services/backtest) is what can move it.
    "fondamentaux": 0.20,
}

# --------------------------------------------------------------------------- #
# Fundamentals scoring                                                          #
# --------------------------------------------------------------------------- #
#
# Six ratios are published per issuer (BPA, ROE, payout, rendement, PER, PBR).
# Revenue, margins and balance-sheet items are not published in machine-readable
# form, so there is no book-value or leverage term here — absent, never guessed.
#
# NORMALISATION, HONESTLY
# These are ABSOLUTE bands, not sector-relative percentiles. Sector-relative would
# be better — a PER of 20 means different things for a bank and a telecom — but it
# needs the cross-section, and `assess_long` scores one symbol at a time by design.
# Doing it properly means moving the ranking up into `gather()`, which is a real
# refactor rather than a tweak; it is recorded as remaining work instead of being
# faked with a constant that pretends to be a sector median.
#
# The bands below are winsorised on purpose: a PER of 300 (a company that barely
# earned anything last year) and a PER of 60 are both simply "expensive", and
# letting the first dominate a weighted mean would be a data artefact driving a
# recommendation. Negative earnings are NOT scored as a cheap valuation — a
# negative PER is meaningless as a multiple, so it is treated as unmeasurable and
# lowers coverage.

FUNDAMENTAL_WEIGHTS = {
    "valorisation": 0.45,  # PER + PBR
    "rentabilite": 0.35,  # ROE
    "rendement": 0.20,  # dividend yield
}

# (value, score) anchors, linearly interpolated between them and clamped outside.
_PER_BANDS = ((6.0, 90.0), (12.0, 70.0), (18.0, 50.0), (25.0, 30.0), (40.0, 10.0))
_PBR_BANDS = ((0.6, 90.0), (1.2, 70.0), (2.0, 50.0), (3.5, 30.0), (6.0, 10.0))
_ROE_BANDS = ((2.0, 10.0), (8.0, 35.0), (13.0, 55.0), (18.0, 75.0), (25.0, 92.0))
_YIELD_BANDS = ((0.0, 20.0), (2.0, 45.0), (4.0, 65.0), (6.0, 82.0), (9.0, 92.0))


def _interpolate(value: float, bands: tuple[tuple[float, float], ...]) -> float:
    """Piecewise-linear lookup, clamped at both ends (the winsorisation)."""
    first_x, first_y = bands[0]
    if value <= first_x:
        return first_y
    for (x0, y0), (x1, y1) in zip(bands, bands[1:]):
        if value <= x1:
            span = x1 - x0
            return y0 + (y1 - y0) * ((value - x0) / span) if span else y0
    return bands[-1][1]


def score_fundamentals(fundamentals) -> tuple[float | None, list[str], list[str], list[str]]:  # noqa: ANN001
    """(score, positives, negatives, missing) from the published ratios.

    Returns None when nothing is measurable, so the caller's coverage drops rather
    than a neutral 50 being invented — the same contract as every other component.
    """
    positives: list[str] = []
    negatives: list[str] = []
    missing: list[str] = []

    if fundamentals is None or not getattr(fundamentals, "has_data", False):
        missing.append(
            "Ratios fondamentaux (PER, PBR, ROE, rendement) non collectés pour ce titre."
        )
        return None, positives, negatives, missing

    components: dict[str, float | None] = {}

    # Valuation: PER and PBR, averaged over whichever is available.
    valuation_parts: list[float] = []
    per = getattr(fundamentals, "per", None)
    if per is not None and per > 0:
        valuation_parts.append(_interpolate(per, _PER_BANDS))
        if per <= 12:
            positives.append(f"Valorisation modérée (PER {per:.1f}).")
        elif per >= 25:
            negatives.append(f"Valorisation tendue (PER {per:.1f}).")
    elif per is not None and per <= 0:
        missing.append("PER négatif (bénéfice négatif) : non exploitable comme multiple.")
    else:
        missing.append("PER non publié.")

    pbr = getattr(fundamentals, "pbr", None)
    if pbr is not None and pbr > 0:
        valuation_parts.append(_interpolate(pbr, _PBR_BANDS))
        if pbr <= 1.0:
            positives.append(f"Cours sous l'actif net comptable (PBR {pbr:.2f}).")
    else:
        missing.append("PBR non publié.")

    components["valorisation"] = (
        sum(valuation_parts) / len(valuation_parts) if valuation_parts else None
    )

    roe = getattr(fundamentals, "roe", None)
    if roe is not None:
        components["rentabilite"] = _interpolate(roe, _ROE_BANDS)
        if roe >= 15:
            positives.append(f"Rentabilité élevée (ROE {roe:.1f}%).")
        elif roe <= 5:
            negatives.append(f"Rentabilité faible (ROE {roe:.1f}%).")
    else:
        components["rentabilite"] = None
        missing.append("ROE non publié.")

    dividend_yield = getattr(fundamentals, "dividend_yield", None)
    if dividend_yield is not None:
        components["rendement"] = _interpolate(dividend_yield, _YIELD_BANDS)
        if dividend_yield >= 4:
            positives.append(f"Rendement du dividende attractif ({dividend_yield:.1f}%).")
    else:
        components["rendement"] = None
        missing.append("Rendement du dividende non publié.")

    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None, positives, negatives, missing

    coverage = sum(FUNDAMENTAL_WEIGHTS[k] for k in available)
    score = sum(v * FUNDAMENTAL_WEIGHTS[k] for k, v in available.items()) / coverage
    # Same shrink-to-neutral rule the horizons use: a fundamental verdict resting
    # on one of three sub-components must not speak as loudly as a complete one.
    score = 50 + (score - 50) * min(1.0, coverage / 0.8)

    if getattr(fundamentals, "per_is_derived", False):
        missing.append(
            "PER calculé (cours / BPA) faute de valeur publiée : inférence, pas donnée officielle."
        )
    return round(clamp(score), 1), positives, negatives, missing


COMPONENT_LABELS_FR = {
    "momentum_court": "Momentum court",
    "volume": "Volume",
    "cassure": "Cassure",
    "support": "Support",
    "actualites": "Actualités",
    "tendance": "Tendance 1-3 mois",
    "moyennes_mobiles": "Moyennes mobiles",
    "secteur": "Secteur",
    "volatilite": "Volatilité",
    "tendance_longue": "Tendance longue",
    "stabilite": "Stabilité",
    "structure_52s": "Structure 52 sem.",
    "evenements": "Événements",
}


@dataclass(frozen=True)
class NewsContext:
    """Aggregated recent news for one symbol (built from the news table)."""

    count: int = 0
    avg_impact: float | None = None
    positive: int = 0
    negative: int = 0
    latest_title: str | None = None
    latest_at: datetime | None = None
    fresh_negative: bool = False  # negative news collected within the last 24h
    has_dividend: bool = False
    has_results: bool = False


@dataclass(frozen=True)
class HorizonAssessment:
    horizon: str
    score: float
    components: dict[str, float | None]
    weights: dict[str, float]
    coverage: float  # 0-1: sum of the weights whose component was computable
    positives: list[str]
    negatives: list[str]
    missing: list[str]
    notes: list[str]


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _aggregate(
    horizon: str,
    components: dict[str, float | None],
    weights: dict[str, float],
    positives: list[str],
    negatives: list[str],
    missing: list[str],
    notes: list[str],
    penalty: float = 0.0,
) -> HorizonAssessment:
    # A component with no declared weight is IGNORED, not fatal. Two reasons:
    # the ablation study (services/backtest) measures a component's contribution
    # by removing its weight and re-running, and a future assessor that computes
    # something it has not yet decided how to weight should degrade rather than
    # crash the whole report. Previously this raised KeyError.
    available = {
        key: value
        for key, value in components.items()
        if value is not None and key in weights
    }
    coverage = sum(weights[key] for key in available)
    if available and coverage > 0:
        score = sum(value * weights[key] for key, value in available.items()) / coverage
        # Shrink toward neutral when coverage is low: a strong score built on a
        # single available component would be fake certainty. Full signal only
        # from 80% coverage upward.
        score = 50 + (score - 50) * min(1.0, coverage / 0.8)
        if coverage < 0.6:
            notes.append(
                f"Signal atténué : seulement {int(coverage * 100)}% des indicateurs de cet horizon sont disponibles."
            )
    else:
        score = 50.0
        notes.append("Aucun indicateur exploitable pour cet horizon : score neutre par défaut.")
    score = clamp(score - penalty)
    return HorizonAssessment(
        horizon=horizon,
        score=round(score, 1),
        components={k: (None if v is None else round(v, 1)) for k, v in components.items()},
        weights=weights,
        coverage=round(coverage, 2),
        positives=positives,
        negatives=negatives,
        missing=missing,
        notes=notes,
    )


def assess_short(metric: MetricSet, news: NewsContext) -> HorizonAssessment:
    """Short-term setup (days to ~2 weeks): momentum, volume, breakout, support, news."""
    components: dict[str, float | None] = {}
    positives: list[str] = []
    negatives: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    # Momentum court : mix 1 jour (40%) / 5 jours (60%), 1% de variation ~ 4 pts.
    parts = [(metric.momentum_1d, 0.4), (metric.momentum_5d, 0.6)]
    available = [(value, weight) for value, weight in parts if value is not None]
    momentum = (
        sum(clamp(50 + value * 4) * weight for value, weight in available)
        / sum(weight for _, weight in available)
        if available
        else None
    )
    components["momentum_court"] = momentum
    if momentum is None:
        missing.append("Momentum court (1-5 jours) indisponible : pas assez de points de collecte.")
    elif momentum >= 65:
        positives.append(f"Momentum court positif ({_fmt_pct(metric.momentum_5d)} sur 5 jours).")
    elif momentum <= 35:
        negatives.append(f"Momentum court négatif ({_fmt_pct(metric.momentum_5d)} sur 5 jours).")

    # Volume : anomalie vs moyenne 20 jours (2.5x la moyenne -> 100).
    anomaly = metric.volume_anomaly
    components["volume"] = None if anomaly is None else clamp((anomaly - 1.0) / 1.5 * 100)
    if anomaly is None:
        missing.append("Anomalie de volume indisponible (volumes non collectés ou nuls).")
    elif anomaly >= 1.8:
        positives.append(f"Volume inhabituel à {anomaly:.1f}× la moyenne récente (intérêt du marché).")
    elif anomaly < 0.6:
        negatives.append(f"Volume atone ({anomaly:.1f}× la moyenne) : peu de conviction acheteuse.")

    # Cassure : proximité du plus haut 52 semaines, tempérée si le momentum est négatif.
    proximity = metric.week52_high_proximity
    if proximity is None:
        components["cassure"] = None
        missing.append("Distance au plus haut 52 semaines indisponible.")
    else:
        base = clamp(100 - abs(proximity) * 6)
        if (metric.momentum_5d or 0) <= 0:
            base *= 0.6
        components["cassure"] = base
        if proximity > -1:
            positives.append("Cours au contact de son plus haut 52 semaines (cassure potentielle).")

    # Support : plus le cours est proche d'un support récent, mieux le risque se borne.
    support_distance = metric.support_distance
    if support_distance is None:
        components["support"] = None
        missing.append("Distance au support récent indisponible.")
    else:
        components["support"] = clamp(100 - abs(support_distance) * 8)
        if metric.support == metric.resistance:
            notes.append("Support et résistance confondus : fourchette de prix encore trop étroite (historique court).")
        elif 0 <= support_distance <= 4:
            positives.append(f"Cours proche d'un support récent (~{metric.support:.2f} MAD) : risque borné.")
        elif support_distance > 15:
            negatives.append("Cours éloigné de son support : une correction aurait de la place.")

    # Actualités : impact moyen des actus des ~7-30 derniers jours.
    if news.count == 0:
        components["actualites"] = None
        missing.append("Aucune actualité récente collectée pour ce titre.")
    else:
        components["actualites"] = clamp(50 + (news.avg_impact or 0.0) * 35)
        if (news.avg_impact or 0) >= 0.3:
            positives.append("Actualités récentes plutôt favorables.")
        elif (news.avg_impact or 0) <= -0.3:
            title = f" (« {news.latest_title[:60]}… »)" if news.latest_title else ""
            negatives.append(f"Actualités récentes défavorables{title}.")

    # Pénalité surchauffe : forte hausse du jour = risque de repli immédiat.
    penalty = 0.0
    if metric.daily_variation is not None and metric.daily_variation > 4:
        penalty = 6.0
        negatives.append(
            f"Hausse de {_fmt_pct(metric.daily_variation)} aujourd'hui : risque de repli à très court terme."
        )

    return _aggregate("short", components, SHORT_WEIGHTS, positives, negatives, missing, notes, penalty)


def assess_medium(metric: MetricSet, news: NewsContext) -> HorizonAssessment:
    """Medium-term setup (1-3 months): trend, moving averages, sector, volatility, events."""
    components: dict[str, float | None] = {}
    positives: list[str] = []
    negatives: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    # Tendance : mix 30 jours (60%) / 90 jours (40%), 1% ~ 2.5 pts.
    parts = [(metric.momentum_30d, 0.6), (metric.momentum_90d, 0.4)]
    available = [(value, weight) for value, weight in parts if value is not None]
    trend = (
        sum(clamp(50 + value * 2.5) * weight for value, weight in available)
        / sum(weight for _, weight in available)
        if available
        else None
    )
    components["tendance"] = trend
    if trend is None:
        missing.append("Tendance 30-90 jours indisponible : l'historique collecté est trop court.")
    elif trend >= 65:
        positives.append(f"Tendance de fond positive ({_fmt_pct(metric.momentum_30d)} sur 30 jours).")
    elif trend <= 35:
        negatives.append(f"Tendance de fond négative ({_fmt_pct(metric.momentum_30d)} sur 30 jours).")

    # Moyennes mobiles : cours > MM20, cours > MM50, MM20 > MM50.
    conditions: list[bool] = []
    if metric.price is not None and metric.ma20 is not None:
        conditions.append(metric.price > metric.ma20)
    if metric.price is not None and metric.ma50 is not None:
        conditions.append(metric.price > metric.ma50)
    if metric.ma20 is not None and metric.ma50 is not None:
        conditions.append(metric.ma20 > metric.ma50)
    if conditions:
        components["moyennes_mobiles"] = sum(conditions) / len(conditions) * 100
        if all(conditions) and len(conditions) >= 2:
            positives.append("Cours au-dessus de ses moyennes mobiles : structure haussière.")
        elif not any(conditions):
            negatives.append("Cours sous ses moyennes mobiles : structure baissière.")
    else:
        components["moyennes_mobiles"] = None
        missing.append("Moyennes mobiles indisponibles (historique insuffisant).")

    # Secteur : momentum 30 jours moyen du secteur.
    strength = metric.sector_strength
    components["secteur"] = None if strength is None else clamp(50 + strength * 2.5)
    if strength is None:
        missing.append("Force du secteur indisponible (secteur inconnu ou sans historique).")
    elif strength >= 4:
        positives.append(f"Secteur porteur ({metric.sector}, {_fmt_pct(strength)} en moyenne sur 30 j).")
    elif strength <= -4:
        negatives.append(f"Secteur sous pression ({metric.sector}, {_fmt_pct(strength)} sur 30 j).")

    # Volatilité (inverse) : une volatilité contenue rend la tendance plus exploitable.
    volatility = metric.volatility_30d
    components["volatilite"] = None if volatility is None else clamp(100 - volatility * 1.5)
    if volatility is None:
        missing.append("Volatilité 30 jours indisponible.")
    elif volatility < 20:
        positives.append(f"Volatilité contenue ({volatility:.0f}% annualisée).")
    elif volatility > 40:
        negatives.append(f"Volatilité élevée ({volatility:.0f}% annualisée) : parcours heurté probable.")

    # Actualités / événements récents.
    if news.count == 0:
        components["actualites"] = None
        missing.append("Aucune actualité récente collectée pour ce titre.")
    else:
        components["actualites"] = clamp(50 + (news.avg_impact or 0.0) * 30)

    return _aggregate("medium", components, MEDIUM_WEIGHTS, positives, negatives, missing, notes)


def assess_long(
    metric: MetricSet,
    news: NewsContext,
    history_days: int,
    fundamentals=None,  # noqa: ANN001 - a research.context.Fundamentals, kept untyped to avoid a cycle
) -> HorizonAssessment:
    """Long-term setup (6+ months): long trend, stability, 52-week structure,
    sector, events and — since the Phase 1b collectors landed — the published
    fundamentals.

    `fundamentals` is optional so every existing caller keeps working; absent, the
    component is simply unavailable and lowers coverage, exactly like any other
    missing input.
    """
    components: dict[str, float | None] = {}
    positives: list[str] = []
    negatives: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    # Tendance longue : momentum 90 jours, ajusté par la MM200 quand elle est fiable.
    base = None if metric.momentum_90d is None else clamp(50 + metric.momentum_90d * 1.5)
    if base is not None and metric.price is not None and metric.ma200 is not None:
        if history_days >= 180:
            above = metric.price > metric.ma200
            base = clamp(base + (8 if above else -8))
            if above:
                positives.append("Cours au-dessus de sa moyenne mobile 200 jours (tendance de fond saine).")
            else:
                negatives.append("Cours sous sa moyenne mobile 200 jours (tendance de fond dégradée).")
        else:
            notes.append(f"MM200 ignorée : seulement {history_days} jours d'historique collecté (< 180).")
    components["tendance_longue"] = base
    if base is None:
        missing.append("Tendance longue (90 jours) indisponible : historique insuffisant.")

    # Stabilité : volatilité contenue + drawdown limité.
    stability_parts: list[float] = []
    if metric.volatility_30d is not None:
        stability_parts.append(clamp(100 - metric.volatility_30d * 1.8))
    if metric.drawdown_from_recent_high is not None:
        stability_parts.append(clamp(100 + metric.drawdown_from_recent_high * 2))
    components["stabilite"] = sum(stability_parts) / len(stability_parts) if stability_parts else None
    if not stability_parts:
        missing.append("Stabilité (volatilité, drawdown) non mesurable : historique insuffisant.")
    elif components["stabilite"] is not None and components["stabilite"] >= 70:
        positives.append("Comportement historiquement stable (volatilité et replis contenus).")
    elif components["stabilite"] is not None and components["stabilite"] <= 35:
        negatives.append("Comportement instable : forte volatilité ou repli marqué depuis les plus hauts.")

    # Structure 52 semaines : position du cours dans sa fourchette annuelle.
    high, low, price = metric.week52_high, metric.week52_low, metric.price
    if high is None or low is None or price is None or high <= low:
        components["structure_52s"] = None
        missing.append("Fourchette 52 semaines non établie (historique court).")
    else:
        position = (price - low) / (high - low) * 100
        components["structure_52s"] = clamp(position if position >= 20 else position * 0.5)
        if position >= 70:
            positives.append("Cours dans le haut de sa fourchette 52 semaines (force relative).")
        elif position < 20:
            negatives.append("Cours proche de son plus bas 52 semaines : tendance de fond fragile.")

    # Secteur.
    strength = metric.sector_strength
    components["secteur"] = None if strength is None else clamp(50 + strength * 2.5)
    if strength is None:
        missing.append("Qualité du secteur non mesurable (secteur inconnu ou sans historique).")

    # Événements d'entreprise (avis officiels) : dividende / résultats.
    if news.has_dividend:
        components["evenements"] = 70.0
        positives.append("Annonce de dividende récente dans les avis officiels.")
    elif news.has_results and (news.avg_impact or 0) > 0:
        components["evenements"] = 65.0
        positives.append("Publication de résultats récente plutôt bien orientée.")
    elif news.count > 0:
        components["evenements"] = 50.0
    else:
        components["evenements"] = None
        missing.append("Aucun événement d'entreprise récent collecté (avis officiels).")

    # Fondamentaux. Cette ligne annonçait inconditionnellement « non collectés »,
    # y compris quand la table `fundamentals` était pleine — l'affirmation était
    # fausse depuis la Phase 1b.
    fundamental_score, f_positives, f_negatives, f_missing = score_fundamentals(fundamentals)
    components["fondamentaux"] = fundamental_score
    positives.extend(f_positives)
    negatives.extend(f_negatives)
    missing.extend(f_missing)

    if history_days < 120:
        notes.append(
            f"Seulement {history_days} jours d'historique collecté : l'analyse long terme reste indicative."
        )

    return _aggregate("long", components, LONG_WEIGHTS, positives, negatives, missing, notes)


def assess_all(
    metric: MetricSet,
    news: NewsContext,
    history_days: int,
    fundamentals=None,  # noqa: ANN001 - see assess_long
) -> dict[str, HorizonAssessment]:
    return {
        "short": assess_short(metric, news),
        "medium": assess_medium(metric, news),
        "long": assess_long(metric, news, history_days, fundamentals),
    }


def compute_risk(metric: MetricSet, news: NewsContext, history_days: int) -> tuple[float, list[str]]:
    """Risk score 0-100 (higher = riskier) with the reasons that drove it."""
    risk = 0.0
    reasons: list[str] = []

    volatility = metric.volatility_30d
    if volatility is None:
        risk += 10
        reasons.append("Volatilité inconnue (historique trop court) : incertitude accrue.")
    elif volatility > 15:
        risk += clamp((volatility - 15) * 1.2, 0, 30)
        if volatility > 35:
            reasons.append(f"Volatilité élevée ({volatility:.0f}% annualisée).")

    momentum = metric.momentum_30d
    if momentum is not None and momentum < 0:
        risk += clamp(-momentum * 1.5, 0, 25)
        if momentum < -5:
            reasons.append(f"Tendance 30 jours négative ({_fmt_pct(momentum)}).")

    drawdown = metric.drawdown_from_recent_high
    if drawdown is not None and drawdown < -10:
        risk += clamp(-(drawdown + 10) * 1.2, 0, 20)
        reasons.append(f"Repli de {_fmt_pct(drawdown)} depuis le plus haut récent.")

    if (
        metric.volume_anomaly is not None
        and metric.volume_anomaly >= 2
        and (metric.daily_variation or 0) < 0
    ):
        risk += 10
        reasons.append("Volume anormalement élevé sur une séance de baisse (pression vendeuse).")

    if news.fresh_negative or (news.avg_impact is not None and news.avg_impact <= -0.4):
        risk += 12
        reasons.append("Actualités récentes défavorables.")

    if history_days < 30:
        risk += 10
        reasons.append(f"Historique limité ({history_days} jours de collecte) : estimation moins fiable.")

    if not reasons:
        reasons.append("Aucun facteur de risque technique majeur détecté sur les données disponibles.")
    return round(clamp(risk), 1), reasons


def compute_confidence(assessment: HorizonAssessment, history_days: int) -> tuple[float, str]:
    """Confidence 0-100: data coverage + history depth + agreement between signals."""
    coverage_pts = assessment.coverage * 50
    target = HISTORY_TARGET_DAYS[assessment.horizon]
    history_pts = min(history_days / target, 1.0) * 30 if target else 30.0

    available = [value for value in assessment.components.values() if value is not None]
    agreement_pts = clamp(20 - pstdev(available) / 2.5, 0, 20) if len(available) >= 2 else 5.0

    confidence = coverage_pts + history_pts + agreement_pts
    if assessment.coverage < 0.5:
        confidence = min(confidence, 35.0)

    label = HORIZON_LABELS_FR[assessment.horizon].lower()
    cohesion = "forte" if agreement_pts >= 14 else "moyenne" if agreement_pts >= 8 else "faible"
    reason = (
        f"{int(assessment.coverage * 100)}% des indicateurs {label} sont disponibles, "
        f"{history_days} jours d'historique collecté (cible {target} j), "
        f"cohérence des signaux {cohesion}."
    )
    return round(clamp(confidence), 1), reason
