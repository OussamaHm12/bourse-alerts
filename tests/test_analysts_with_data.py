"""Phase 1b analysts, exercised WITH data.

`company`, `fundamental` and `macro` sat at 17-24% coverage, and the reason is
worth stating: every existing test ran against empty Phase 1b tables, so only the
honest "données non collectées" branch was ever taken. The branch that actually
reads a ratio, an ownership table or a policy rate — the one that will run in
production once the weekly issuer sweep and the daily BKAM job have filled those
tables — was never executed.

The guarantees under test are the ones the architecture claims loudest, and they
only mean anything on this path:

  * a value that is NOT published stays in `missing_data` — never invented, never
    defaulted to zero;
  * a PER computed as price/BPA is an INFERENCE, never a fact, and costs confidence;
  * confidence scales with how many fields were genuinely available.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moroccan_stock_intelligence.services.analysts import company, fundamental, macro
from moroccan_stock_intelligence.services.analytics import MetricSet
from moroccan_stock_intelligence.services.horizon_strategy import NewsContext
from moroccan_stock_intelligence.services.portfolio import Portfolio
from moroccan_stock_intelligence.services.research.context import (
    CompanyProfile,
    Fundamentals,
    MacroSnapshot,
    MarketContext,
    ResearchContext,
)


def _metric(**kw) -> MetricSet:
    base = {f: None for f in MetricSet.__dataclass_fields__}
    base.update(
        {
            "stock_id": 1,
            "symbol": "ATW",
            "company_name": "ATTIJARIWAFA BANK",
            "sector": "Banques",
            "price": 500.0,
        }
    )
    base.update(kw)
    return MetricSet(**base)


def _market(macro: MacroSnapshot | None = None) -> MarketContext:
    return MarketContext(
        as_of=datetime.now(UTC),
        tracked=80,
        regime="neutre",
        breadth_above_ma50_pct=50.0,
        advancers=40,
        decliners=40,
        avg_momentum_30d=0.5,
        msi20_proxy={"5d": 0.2, "30d": 0.5},
        sector_strength={"Banques": 1.2},
        sector_rank={"Banques": 1},
        macro=macro,
    )


def _ctx(*, macro_snapshot: MacroSnapshot | None = None, **kw) -> ResearchContext:
    """The context the analysts read. `macro` hangs off MarketContext, not the
    ResearchContext — macro is market-wide, not per-symbol, which is exactly why
    the macro analyst casts no per-stock vote."""
    defaults = {
        "symbol": "ATW",
        "company_name": "ATTIJARIWAFA BANK",
        "sector": "Banques",
        "as_of": datetime.now(UTC),
        "metric": _metric(),
        "history_days": 400,
        "price_history": [],
        "news": NewsContext(),
        "news_items": [],
        "holding": None,
        "portfolio": Portfolio(holdings=[], fee_rate=0.005),
        "fundamentals": Fundamentals(),
        "company_profile": CompanyProfile(),
        "market": _market(macro_snapshot),
    }
    defaults.update(kw)
    return ResearchContext(**defaults)


# --------------------------------------------------------------------------- #
# Fundamental                                                                  #
# --------------------------------------------------------------------------- #


def test_fundamental_reads_the_published_ratios():
    report = fundamental.analyze(
        _ctx(
            fundamentals=Fundamentals(
                fiscal_year=2025,
                eps=49.48,
                roe=12.0,
                payout=45.0,
                dividend_yield=3.8,
                per=14.76,
                pbr=1.95,
                source="Casablanca Bourse",
            )
        )
    )
    assert report.analyst == "fundamental"
    assert report.confidence > 0
    assert report.data_used
    text = " ".join(s.text for s in report.observations)
    assert "14" in text or "PER" in text


def test_fundamental_never_invents_what_the_page_does_not_publish():
    """Revenue, net income, margins, ROA, debt/equity and book value are not
    published in machine-readable form (validated 2026-07-09)."""
    report = fundamental.analyze(
        _ctx(fundamentals=Fundamentals(fiscal_year=2025, per=14.76, eps=49.48))
    )
    assert report.missing_data, "the unpublished ratios must be named, not silently absent"


def test_a_derived_per_is_an_inference_and_costs_confidence():
    """A PER computed as price/BPA because the published cell was "-" must never be
    presented as a fact."""
    published = fundamental.analyze(
        _ctx(fundamentals=Fundamentals(fiscal_year=2025, per=14.76, eps=49.48, roe=12.0))
    )
    derived = fundamental.analyze(
        _ctx(
            fundamentals=Fundamentals(
                fiscal_year=2025, per=14.76, eps=49.48, roe=12.0, per_is_derived=True
            )
        )
    )
    assert derived.confidence < published.confidence
    assert derived.notes, "a derived value must say so"


def test_fundamental_confidence_grows_with_available_fields():
    thin = fundamental.analyze(_ctx(fundamentals=Fundamentals(fiscal_year=2025, per=14.0)))
    rich = fundamental.analyze(
        _ctx(
            fundamentals=Fundamentals(
                fiscal_year=2025, per=14.0, eps=49.0, roe=12.0, payout=45.0,
                dividend_yield=3.8, pbr=1.9,
            )
        )
    )
    assert rich.confidence > thin.confidence


def test_fundamental_without_data_is_honest_not_silent():
    report = fundamental.analyze(_ctx(fundamentals=Fundamentals()))
    assert report.confidence == 0.0
    assert report.missing_data
    assert not report.horizon_signals or all(
        s.lean == 50.0 for s in report.horizon_signals
    ), "no data must not become a directional view"


# --------------------------------------------------------------------------- #
# Company                                                                      #
# --------------------------------------------------------------------------- #


def test_company_reads_the_issuer_profile():
    report = company.analyze(
        _ctx(
            company_profile=CompanyProfile(
                company_name="ATTIJARIWAFA BANK",
                description="Établissement bancaire",
                siege_social="Casablanca",
                commissaire_aux_comptes="Deloitte",
                date_introduction="1992",
                ownership=[{"holder": "AL MADA", "pct": 46.5}],
                source="Casablanca Bourse",
            )
        )
    )
    assert report.analyst == "company"
    assert report.confidence > 0
    assert report.observations


def test_company_flags_a_controlling_shareholder():
    """Above 50% a holder controls the company — a fact with real consequences for
    a minority holder, so it must surface."""
    controlled = company.analyze(
        _ctx(
            company_profile=CompanyProfile(
                description="X", ownership=[{"holder": "ÉTAT", "pct": 72.0}]
            )
        )
    )
    text = " ".join(
        s.text for s in controlled.observations + controlled.strengths + controlled.weaknesses
    )
    assert "72" in text or "contrôl" in text.lower()


def test_company_does_not_synthesise_an_unpublished_business_model():
    """No business-model narrative is published, so none may be written."""
    report = company.analyze(_ctx(company_profile=CompanyProfile(description="Objet social")))
    assert report.missing_data


def test_company_without_data_is_honest():
    report = company.analyze(_ctx(company_profile=CompanyProfile()))
    assert report.confidence == 0.0
    assert report.missing_data


# --------------------------------------------------------------------------- #
# Macro                                                                        #
# --------------------------------------------------------------------------- #


def test_macro_reads_the_bkam_snapshot():
    report = macro.analyze(
        _ctx(
            macro_snapshot=MacroSnapshot(
                as_of=datetime.now(UTC),
                policy_rate=2.25,
                interbank_rate=2.30,
                inflation=1.2,
                inflation_underlying=1.0,
                mad_eur=10.691,
                mad_usd=9.350,
            )
        )
    )
    assert report.analyst == "macro"
    assert report.confidence > 0
    text = " ".join(s.text for s in report.observations)
    assert "2,25" in text or "2.25" in text or "taux" in text.lower()


def test_macro_never_reports_oil_or_phosphate_as_zero():
    """BAM does not publish them. Permanently None, and named as missing — a zero
    would be a claim about data that does not exist."""
    report = macro.analyze(
        _ctx(macro_snapshot=MacroSnapshot(as_of=datetime.now(UTC), policy_rate=2.25, inflation=1.2))
    )
    missing = " ".join(report.missing_data).lower()
    assert "pétrole" in missing or "petrole" in missing or "phosphate" in missing


def test_macro_casts_no_directional_vote():
    """Macro is market context: it informs the CIO, it does not vote on a price."""
    report = macro.analyze(
        _ctx(macro_snapshot=MacroSnapshot(as_of=datetime.now(UTC), policy_rate=2.25, inflation=1.2))
    )
    assert not report.horizon_signals, "macro must not lean on a per-stock horizon"


def test_macro_without_data_is_honest():
    report = macro.analyze(_ctx(macro_snapshot=MacroSnapshot()))
    assert report.confidence == 0.0
    assert report.missing_data


@pytest.mark.parametrize("analyst", [company, fundamental, macro])
def test_no_analyst_ever_recommends(analyst):
    """Structurally impossible — AnalystReport has no recommendation field. Asserted
    on the path where data EXISTS, which is where the temptation would be."""
    report = analyst.analyze(
        _ctx(
            fundamentals=Fundamentals(fiscal_year=2025, per=14.0, eps=49.0, roe=12.0),
            company_profile=CompanyProfile(description="X", ownership=[{"holder": "A", "pct": 60.0}]),
            macro_snapshot=MacroSnapshot(as_of=datetime.now(UTC), policy_rate=2.25, inflation=1.2),
        )
    )
    assert not hasattr(report, "recommendation")
    for statement in report.observations + report.strengths + report.weaknesses:
        assert statement.kind in {"fact", "inference", "opinion"}
