from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import inspect
from pathlib import Path

import pytest

import stock_analyser.application.live_configuration  # initialize application package imports
import stock_analyser.live_identity as identity_module
from stock_analyser.domain import CapabilityResult, CapabilityStatus
from stock_analyser.live_identity import (
    IdentityCandidateResolutionStatus,
    IdentityDiagnosticStage,
    resolve_live_target_identity,
)
from stock_analyser.live_smoke import LiveFiscalSource
from stock_analyser.providers import (
    FiscalAdapter,
    IdentityCandidate,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    ProviderIdentityResult,
    SecretReference,
)


NOW = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
SECRET = "SYNTHETIC_FISCAL_SECRET"


class Response:
    headers = {}

    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


class FiscalSession:
    def __init__(self, *, pages, exact=None, profiles=None):
        self.pages = dict(pages)
        self.exact = exact
        self.profiles = dict(profiles or {})
        self.calls = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append((url, dict(params), dict(headers)))
        if url.endswith("/v3/companies-list"):
            return Response(self.pages[int(params["pageNumber"])])
        if "ticker" in params:
            if isinstance(self.exact, Response):
                return self.exact
            return Response(self.exact)
        return Response(self.profiles[params["fscl"]])


def listing(
    ticker="PORT",
    venue="XLON",
    *,
    security_id="SEC-PORT-L",
    listing_id="LIST-PORT-L",
    operating_mic=None,
    currency="GBP",
):
    return {
        "ticker": ticker,
        "exchangeCode": venue,
        "exchangeName": "London Stock Exchange" if venue in {"LSE", "XLON"} else "Other Exchange",
        "operatingMic": operating_mic,
        "listingFiscalIdentifier": listing_id,
        "securityFiscalIdentifier": security_id,
        "securityType": "common_stock",
        "tradingCurrency": currency,
    }


def company(
    *,
    ticker="PORT",
    venue="XLON",
    issuer_id="ISSUER-PORT",
    security_id="SEC-PORT-L",
    listing_id="LIST-PORT-L",
):
    return {
        "companyKey": f"{venue}_{ticker}",
        "companyFiscalIdentifier": issuer_id,
        "displayNameEnglish": "Portable plc",
        "reportingCurrency": "GBP",
        "companyType": "operating_company",
        "primaryListing": listing(
            ticker, venue, security_id=security_id, listing_id=listing_id,
        ),
    }


def profile(
    *,
    ticker="PORT",
    venue="XLON",
    issuer_id="ISSUER-PORT",
    security_id="SEC-PORT-L",
    listing_id="LIST-PORT-L",
    primary=None,
    secondary=(),
):
    return {
        "companyKey": f"{venue}_{ticker}",
        "companyFiscalIdentifier": issuer_id,
        "displayNameEnglish": "Portable plc",
        "legalDomicileCountryCode": "GB",
        "headquartersCountryCode": "GB",
        "reportingCurrency": "GBP",
        "sector": "Industrials",
        "industry": "Industrial Products",
        "companyType": "operating_company",
        "primaryListing": primary or listing(
            ticker, venue, security_id=security_id, listing_id=listing_id,
        ),
        "secondaryListings": list(secondary),
    }


def page(*rows, total_pages=1, page_number=1):
    return {
        "pagination": {
            "page": page_number,
            "totalPages": total_pages,
            "hasNextPage": page_number < total_pages,
        },
        "data": list(rows),
    }


def adapter(session):
    return FiscalAdapter(
        LiveFiscalSource(session=session),
        credential=SecretReference("FISCAL_API_KEY", lambda: SECRET),
        clock=lambda: NOW,
    )


def test_unambiguous_us_identity_keeps_the_page_one_fast_path_and_stable_profile_lookup():
    row = company(ticker="FAST", venue="NASDAQ", issuer_id="ISSUER-FAST", security_id="SEC-FAST")
    full = profile(ticker="FAST", venue="NASDAQ", issuer_id="ISSUER-FAST", security_id="SEC-FAST")
    session = FiscalSession(pages={1: page(row)}, profiles={"ISSUER-FAST": full})

    result = adapter(session).fetch_identity("FAST")

    assert result.candidate is not None
    assert result.candidate.provider_issuer_id == "ISSUER-FAST"
    assert result.candidate.provider_security_id == "SEC-FAST"
    assert len(session.calls) == 2
    assert session.calls[0][1] == {"compact": "true", "pageNumber": 1}
    assert session.calls[1][1] == {"fscl": "ISSUER-FAST"}
    assert all("ticker" not in call[1] for call in session.calls)


def test_incomplete_first_page_triggers_one_exact_venue_scoped_profile_lookup():
    exact = profile(primary=listing("OTHER", "XNYS", security_id="SEC-US", listing_id="LIST-US"), secondary=(listing(),))
    session = FiscalSession(pages={1: page(company(ticker="OTHER", venue="XNYS"))}, exact=exact)

    result = adapter(session).fetch_identity("PORT.L")

    assert result.candidate is not None
    assert result.candidate.provider_symbol == "PORT"
    assert result.candidate.exchange == "XLON"
    assert result.candidate.provider_issuer_id == "ISSUER-PORT"
    assert result.candidate.provider_security_id == "SEC-PORT-L"
    assert len(session.calls) == 2
    assert session.calls[1][1] == {"ticker": "PORT", "micCode": "XLON"}


def test_exact_lookup_uses_secondary_listing_evidence_without_promoting_primary_listing():
    exact = profile(
        primary=listing("PORT", "XNYS", security_id="SEC-US", listing_id="LIST-US", currency="USD"),
        secondary=(listing("PORT", "XLON"),),
    )
    session = FiscalSession(pages={1: page()}, exact=exact)

    candidate = adapter(session).fetch_identity("PORT.L").candidate

    assert candidate.exchange == "XLON"
    assert candidate.quote_currency == "GBP"
    assert candidate.provider_security_id == "SEC-PORT-L"


def test_exact_lookup_404_activates_capped_pagination_and_normalizes_a_later_page():
    row = company()
    full = profile()
    session = FiscalSession(
        pages={
            1: page(company(ticker="OTHER", venue="XNYS"), total_pages=3, page_number=1),
            2: page(company(ticker="PORT", venue="XNYS"), total_pages=3, page_number=2),
            3: page(row, total_pages=3, page_number=3),
        },
        exact=Response({}, 404),
        profiles={"ISSUER-PORT": full},
    )

    candidate = adapter(session).fetch_identity("PORT.L").candidate

    assert candidate is not None and candidate.exchange == "XLON"
    page_numbers = [params["pageNumber"] for url, params, _ in session.calls if url.endswith("companies-list")]
    assert page_numbers == [1, 2, 3]
    assert session.calls[-1][1] == {"fscl": "ISSUER-PORT"}


def test_pagination_bound_is_enforced_before_page_two_is_requested(monkeypatch):
    maximum = LiveFiscalSource._IDENTITY_MAX_PAGES
    session = FiscalSession(
        pages={1: page(total_pages=maximum + 1)},
        exact=Response({}, 404),
    )

    result = adapter(session).fetch_identity("PORT.L")

    assert result.candidate is None
    assert [params.get("pageNumber") for _, params, _ in session.calls] == [1, None]


def test_candidate_volume_bound_is_enforced_after_bounded_pages(monkeypatch):
    monkeypatch.setattr(LiveFiscalSource, "_IDENTITY_MAX_CANDIDATES", 1)
    session = FiscalSession(
        pages={1: page(company(ticker="A"), company(ticker="B"))},
        exact=Response({}, 404),
    )

    result = adapter(session).fetch_identity("PORT.L")

    assert result.candidate is None
    assert result.capability.status in {CapabilityStatus.ERROR, CapabilityStatus.UNAVAILABLE}


def test_exact_lookup_access_denial_does_not_fan_out_into_pagination():
    session = FiscalSession(
        pages={1: page(total_pages=3)},
        exact=Response({}, 403),
    )

    result = adapter(session).fetch_identity("PORT.L")

    assert result.candidate is None
    assert len(session.calls) == 2
    assert session.calls[0][1]["pageNumber"] == 1
    assert session.calls[1][1] == {"ticker": "PORT", "micCode": "XLON"}


@pytest.mark.parametrize("venue", ["LSE", "XLON"])
def test_unique_explicit_london_venue_evidence_is_accepted(venue):
    row = company(venue=venue)
    full = profile(venue=venue)
    session = FiscalSession(pages={1: page(row)}, profiles={"ISSUER-PORT": full})
    candidate = adapter(session).fetch_identity("PORT.L").candidate
    assert candidate is not None
    assert candidate.exchange == venue


@pytest.mark.parametrize(
    "candidate_listing",
    [
        listing("PORT", ""),
        listing("PORT", "XNYS"),
        listing("OTHER", "XLON"),
    ],
)
def test_suffix_or_base_ticker_without_matching_venue_and_symbol_is_insufficient(candidate_listing):
    exact = profile(primary=candidate_listing)
    session = FiscalSession(pages={1: page()}, exact=exact)
    result = adapter(session).fetch_identity("PORT.L")
    assert result.candidate is None


def test_multiple_london_listings_remain_a_conflict():
    exact = profile(
        primary=listing(listing_id="LIST-ONE", security_id="SEC-ONE"),
        secondary=(listing(listing_id="LIST-TWO", security_id="SEC-TWO"),),
    )
    result = adapter(FiscalSession(pages={1: page()}, exact=exact)).fetch_identity("PORT.L")
    assert result.candidate is None


def test_conflicting_exchange_code_and_operating_mic_fail_closed():
    conflicted = listing(venue="LSE", operating_mic="XNYS")
    exact = profile(primary=conflicted)
    result = adapter(FiscalSession(pages={1: page()}, exact=exact)).fetch_identity("PORT.L")
    assert result.candidate is None


def test_profile_enrichment_is_bound_to_the_stable_fiscal_issuer_identifier():
    row = company()
    session = FiscalSession(pages={1: page(row)}, profiles={"ISSUER-PORT": profile()})
    adapter(session).fetch_identity("PORT.L")
    assert session.calls[-1][1] == {"fscl": "ISSUER-PORT"}
    assert "companyKey" not in session.calls[-1][1]


def test_profile_stable_issuer_conflict_fails_closed():
    row = company()
    conflicting = profile(issuer_id="OTHER-ISSUER")
    session = FiscalSession(pages={1: page(row)}, profiles={"ISSUER-PORT": conflicting})
    assert adapter(session).fetch_identity("PORT.L").candidate is None


def test_profile_stable_security_conflict_fails_closed():
    row = company()
    conflicting = profile(security_id="OTHER-SECURITY")
    session = FiscalSession(pages={1: page(row)}, profiles={"ISSUER-PORT": conflicting})
    assert adapter(session).fetch_identity("PORT.L").candidate is None


def test_exact_lookup_requires_stable_issuer_and_security_identifiers():
    exact = dict(profile())
    exact.pop("companyFiscalIdentifier")
    exact["primaryListing"] = dict(exact["primaryListing"])
    exact["primaryListing"].pop("securityFiscalIdentifier")
    result = adapter(FiscalSession(pages={1: page()}, exact=exact)).fetch_identity("PORT.L")
    assert result.candidate is None


def test_profile_listing_bound_prevents_unbounded_candidate_enrichment(monkeypatch):
    monkeypatch.setattr(LiveFiscalSource, "_IDENTITY_MAX_PROFILE_LISTINGS", 1)
    exact = profile(primary=listing("OTHER", "XNYS"), secondary=(listing(),))
    session = FiscalSession(pages={1: page()}, exact=exact)
    assert adapter(session).fetch_identity("PORT.L").candidate is None
    assert len(session.calls) == 2


def _install_live_fiscal(monkeypatch, session):
    original = LiveFiscalSource
    monkeypatch.setattr(
        identity_module,
        "LiveFiscalSource",
        lambda *, observer=None: original(session=session, observer=observer),
    )


def test_missing_fiscal_canonical_identity_never_constructs_yahoo_fallback(monkeypatch):
    session = FiscalSession(pages={1: page()}, exact=profile(primary=listing("PORT", "XNYS")))
    _install_live_fiscal(monkeypatch, session)
    monkeypatch.setattr(
        identity_module,
        "YahooAdapter",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo must not replace Fiscal")),
    )

    result = resolve_live_target_identity(
        "PORT.L", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )

    assert result.identity is None
    assert result.yahoo_result is None
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.NORMALIZATION


def test_safe_diagnostic_reports_controlled_acquisition_facts_only(monkeypatch):
    exact = profile(primary=listing("OTHER", "XNYS"), secondary=(listing(),))
    session = FiscalSession(pages={1: page()}, exact=exact)
    _install_live_fiscal(monkeypatch, session)

    class Yahoo:
        def __init__(self, source):
            pass

        def fetch_identity(self, symbol):
            return ProviderIdentityResult(
                IdentityCandidate(
                    provider=ProviderId.YAHOO,
                    provider_symbol="PORT.L",
                    retrieved_at=NOW,
                    company_name="Portable plc",
                    issuer_domicile="GB",
                    listing_country="GB",
                    exchange="LSE",
                    security_type="EQUITY",
                    reporting_currency="GBP",
                    quote_currency="GBP",
                    quote_unit="GBp",
                    price_scale=0.01,
                    fiscal_year_end="12-31",
                ),
                CapabilityResult(
                    provider=ProviderId.YAHOO.value,
                    capability="identity",
                    status=CapabilityStatus.UNAVAILABLE,
                    checked_at=NOW,
                    reason="synthetic unavailable",
                ),
            )

    monkeypatch.setattr(identity_module, "LiveYahooSource", lambda: object())
    monkeypatch.setattr(identity_module, "YahooAdapter", Yahoo)
    result = resolve_live_target_identity(
        "PORT.L", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )

    diagnostic = result.diagnostic
    assert result.identity is not None
    assert diagnostic.candidate_count == 1
    assert diagnostic.pages_examined == 1
    assert diagnostic.venue_evidence_found is True
    assert diagnostic.candidate_resolution_status is IdentityCandidateResolutionStatus.RESOLVED
    assert diagnostic.profile_enrichment_attempted is True
    assert diagnostic.bounded_search_exhausted is False
    assert {item.name for item in fields(diagnostic)}.isdisjoint(
        {"payload", "response", "headers", "companyKey", "api_key", "url"}
    )
    rendered = repr(diagnostic)
    assert SECRET not in rendered
    assert "companyKey" not in rendered


def test_bounded_search_diagnostic_marks_exhaustion_without_raw_data(monkeypatch):
    session = FiscalSession(
        pages={1: page(total_pages=LiveFiscalSource._IDENTITY_MAX_PAGES + 1)},
        exact=Response({}, 404),
    )
    _install_live_fiscal(monkeypatch, session)
    result = resolve_live_target_identity(
        "PORT.L", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )
    assert result.identity is None
    assert result.diagnostic.bounded_search_exhausted is True
    assert result.diagnostic.candidate_resolution_status is IdentityCandidateResolutionStatus.BOUND_EXHAUSTED
    assert SECRET not in repr(result.diagnostic)


def test_production_repair_is_generic_and_contains_no_target_specific_branch():
    source = inspect.getsource(LiveFiscalSource)
    for forbidden in ("SHEL", "Shell", "RR.L", "Rolls-Royce"):
        assert forbidden not in source
    assert "while " not in source


def test_identity_acquisition_contains_no_unit_fx_or_valuation_arithmetic():
    source = inspect.getsource(LiveFiscalSource).lower()
    for forbidden in (
        "divide by 100", "/ 100", "fx_rate", "exchange_rate", "dgs10",
        "fair_value", "reverse_dcf", "recommendation", "buy", "sell",
    ):
        assert forbidden not in source


def test_normalized_registry_and_application_contracts_remain_raw_payload_free():
    context_source = Path(
        identity_module.__file__
    ).with_name("application").joinpath("research_build_context.py").read_text(encoding="utf-8")
    assert "raw payload" in context_source.lower()
    assert "companykey" in context_source.lower()
