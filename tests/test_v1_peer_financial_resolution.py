from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from stock_analyser.domain import (
    CapabilityStatus,
    CompanyIdentity,
    Frequency,
    MetricId,
    PeerCandidateSource,
    PeerIdentityEvidenceStatus,
    ProviderSymbol,
)
from stock_analyser.providers import (
    FiscalAdapter,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    ProviderLookupStatus,
    ProviderPeerCandidate,
    resolve_fiscal_peer_financial_lookup,
)
from stock_analyser.live_peer_audit import (
    LivePeerCandidateContext,
    _finalize_annual_revenue_contexts,
)
from stock_analyser.live_smoke import LiveFiscalSource


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def candidate(**changes) -> ProviderPeerCandidate:
    values = dict(
        provider=ProviderId.FISCAL,
        provider_symbol="SYN",
        provider_issuer_id="FSCLC-SYN",
        provider_security_id="FSCLS-SYN",
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_rank=1,
        source_as_of=NOW,
        retrieved_at=NOW,
        company_name="Synthetic Peer",
        listing_country="US",
        exchange="NASDAQ",
        sector="Technology",
        industry="Internet Content & Information",
        security_type="common_stock",
        provider_company_key="OPAQUE-SYN",
    )
    values.update(changes)
    return ProviderPeerCandidate(**values)


def company_record(**changes):
    row = {
        "companyKey": "OPAQUE-SYN",
        "companyFiscalIdentifier": "FSCLC-SYN",
        "displayNameEnglish": "Synthetic Peer",
        "reportingCurrency": "USD",
        "primaryListing": {
            "ticker": "SYN",
            "exchangeCode": "NASDAQ",
            "securityFiscalIdentifier": "FSCLS-SYN",
        },
    }
    row.update(changes)
    return row


def financial_rows():
    return (
        {
            "metric": "revenue",
            "sourceMetric": "income_statement_total_revenues",
            "value": 100,
            "currency": "USD",
            "periodType": "annual",
            "periodStart": "2024-01-01",
            "periodEnd": "2024-12-31",
            "fiscalYear": 2024,
            "asOf": "2025-02-01T00:00:00+00:00",
        },
        {
            "metric": "revenue",
            "sourceMetric": "income_statement_total_revenues",
            "value": 110,
            "currency": "USD",
            "periodType": "annual",
            "periodStart": "2025-01-01",
            "periodEnd": "2025-12-31",
            "fiscalYear": 2025,
            "asOf": "2026-02-01T00:00:00+00:00",
        },
    )


class StableIdSource:
    def __init__(self, records=(), rows=None, fail_lookup=False):
        self.records = tuple(records)
        self.rows = tuple(financial_rows() if rows is None else rows)
        self.fail_lookup = fail_lookup
        self.by_key_calls = []
        self.symbol_calls = []

    def company_lookup_records(self, *, credential=None):
        if self.fail_lookup:
            raise RuntimeError("synthetic private provider failure")
        return self.records

    def standardized_financials_by_company_key(
        self, company_key, *, provider_symbol, credential=None,
    ):
        self.by_key_calls.append((company_key, provider_symbol))
        return self.rows

    def standardized_financials(self, symbol, *, credential=None):
        self.symbol_calls.append(symbol)
        return self.rows


class FailingFinancialSource(StableIdSource):
    def standardized_financials_by_company_key(
        self, company_key, *, provider_symbol, credential=None,
    ):
        self.by_key_calls.append((company_key, provider_symbol))
        raise ProviderError(
            provider=ProviderId.FISCAL,
            endpoint_id="fiscal:standardized-financials",
            category=ProviderErrorCategory.UNKNOWN,
            status_code=403,
            retryable=False,
            safe_message="synthetic safe provider rejection",
        )


def full_identity() -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol="SYN",
        security_id="security:fiscal:FSCLS-SYN",
        issuer_id="issuer:fiscal:FSCLC-SYN",
        company_name="Synthetic Peer",
        issuer_domicile="US",
        listing_country="US",
        exchange="NASDAQ",
        sector="Technology",
        industry="Internet Content & Information",
        security_type="common stock",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "SYN"),),
    )


def test_same_record_stable_issuer_resolves_opaque_provider_company_key():
    discovered = candidate()
    identity = FiscalAdapter.build_summary_peer_identity(discovered)
    result = resolve_fiscal_peer_financial_lookup(discovered, (company_record(),))

    assert result.status is ProviderLookupStatus.AVAILABLE
    assert result.provider_company_key == "OPAQUE-SYN"
    assert result.source_dataset == "fiscal_v3_companies_list"
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert identity.issuer_id == "issuer:fiscal:FSCLC-SYN"
    assert identity.security_id == "security:fiscal:FSCLS-SYN"
    assert not hasattr(identity, "provider_company_key")


def test_same_peer_summary_record_can_supply_lookup_metadata_without_profile():
    result = resolve_fiscal_peer_financial_lookup(candidate(), ())
    assert result.status is ProviderLookupStatus.AVAILABLE
    assert result.source_dataset == "fiscal_v3_company_profile_peers"


@pytest.mark.parametrize("descriptive_match", ["ticker", "name"])
def test_ticker_or_name_alone_cannot_resolve_company_key(descriptive_match):
    discovered = candidate(provider_company_key=None)
    row = company_record(companyFiscalIdentifier="FSCLC-OTHER")
    if descriptive_match == "ticker":
        row["displayNameEnglish"] = "Different Name"
    else:
        row["primaryListing"] = {"ticker": "OTHER", "exchangeCode": "NASDAQ"}
    result = resolve_fiscal_peer_financial_lookup(discovered, (row,))
    assert result.status is ProviderLookupStatus.UNAVAILABLE
    assert result.provider_company_key is None


def test_multiple_records_claiming_stable_identifier_fail_ambiguous():
    second = company_record(companyKey="OPAQUE-OTHER")
    result = resolve_fiscal_peer_financial_lookup(candidate(), (company_record(), second))
    assert result.status is ProviderLookupStatus.AMBIGUOUS
    assert result.provider_company_key is None


def test_stable_identifier_mismatch_and_missing_company_key_fail_closed():
    mismatch = resolve_fiscal_peer_financial_lookup(
        candidate(provider_company_key=None),
        (company_record(companyFiscalIdentifier="FSCLC-OTHER"),),
    )
    missing = resolve_fiscal_peer_financial_lookup(
        candidate(provider_company_key=None),
        (company_record(companyKey=None),),
    )
    assert mismatch.status is ProviderLookupStatus.UNAVAILABLE
    assert missing.status is ProviderLookupStatus.UNAVAILABLE


def test_conflicting_summary_and_company_list_keys_fail_ambiguous():
    result = resolve_fiscal_peer_financial_lookup(
        candidate(provider_company_key="OPAQUE-CONFLICT"),
        (company_record(),),
    )
    assert result.status is ProviderLookupStatus.AMBIGUOUS


def test_summary_key_claimed_by_different_stable_issuer_fails_ambiguous():
    result = resolve_fiscal_peer_financial_lookup(
        candidate(),
        (company_record(companyFiscalIdentifier="FSCLC-OTHER"),),
    )
    assert result.status is ProviderLookupStatus.AMBIGUOUS


@pytest.mark.parametrize("listing", [
    {"ticker": "OTHER", "exchangeCode": "NASDAQ", "securityFiscalIdentifier": "FSCLS-SYN"},
    {"ticker": "SYN", "exchangeCode": "NASDAQ", "securityFiscalIdentifier": "FSCLS-OTHER"},
    {"ticker": "SYN", "exchangeCode": "NYSE", "securityFiscalIdentifier": "FSCLS-SYN"},
])
def test_material_listing_conflicts_fail_ambiguous(listing):
    result = resolve_fiscal_peer_financial_lookup(
        candidate(),
        (company_record(primaryListing=listing),),
    )
    assert result.status is ProviderLookupStatus.AMBIGUOUS


def test_summary_verified_peer_obtains_canonical_actuals_without_full_profile():
    discovered = candidate()
    identity = FiscalAdapter.build_summary_peer_identity(discovered)
    source = StableIdSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)

    resolution = adapter.resolve_peer_financial_lookup(discovered)
    result = adapter.fetch_peer_standardized_actuals(identity, resolution)

    assert source.by_key_calls == [("OPAQUE-SYN", "SYN")]
    assert source.symbol_calls == []
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert [item.metric_id for item in result.observations] == [MetricId.REVENUE, MetricId.REVENUE]
    assert all(item.frequency is Frequency.ANNUAL for item in result.observations)


def test_full_profile_identity_uses_same_stable_key_and_canonical_normalizer():
    discovered = candidate()
    source = StableIdSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    resolution = adapter.resolve_peer_financial_lookup(discovered)

    peer_result = adapter.fetch_peer_standardized_actuals(full_identity(), resolution)
    target_result = adapter.fetch_standardized_actuals(full_identity())

    assert source.by_key_calls == [("OPAQUE-SYN", "SYN")]
    assert source.symbol_calls == ["SYN"]
    assert tuple((item.metric_id, item.value, item.period_end) for item in peer_result.observations) == tuple(
        (item.metric_id, item.value, item.period_end) for item in target_result.observations
    )


def test_issuer_financial_binding_does_not_require_security_or_provider_symbol_equality():
    discovered = candidate()
    source = StableIdSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    issuer_identity = replace(
        full_identity(),
        security_id="security:fiscal:REPRESENTATIVE-OTHER-LISTING",
        provider_symbols=(ProviderSymbol("fiscal", "ALTERNATE-LISTING"),),
    )
    result = adapter.fetch_peer_standardized_actuals(
        issuer_identity,
        adapter.resolve_peer_financial_lookup(discovered),
    )
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert source.by_key_calls == [("OPAQUE-SYN", "SYN")]


def test_provider_lookup_failure_preserves_canonical_peer_identity():
    discovered = candidate(provider_company_key=None)
    identity = FiscalAdapter.build_summary_peer_identity(discovered)
    result = FiscalAdapter(StableIdSource(fail_lookup=True)).resolve_peer_financial_lookup(discovered)
    assert result.status is ProviderLookupStatus.UNAVAILABLE
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert "private" not in repr(result)


def test_candidate_provider_http_403_is_endpoint_error_and_does_not_erase_identity():
    discovered = candidate()
    identity = FiscalAdapter.build_summary_peer_identity(discovered)
    source = FailingFinancialSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    result = adapter.fetch_peer_standardized_actuals(
        identity,
        adapter.resolve_peer_financial_lookup(discovered),
    )
    assert result.capabilities[0].status is CapabilityStatus.ERROR
    assert not result.observations
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert "synthetic safe provider rejection" not in repr(result.issues)


def test_resolution_or_identity_conflict_never_calls_financial_endpoint():
    discovered = candidate()
    source = StableIdSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    resolution = adapter.resolve_peer_financial_lookup(discovered)
    conflicting = replace(
        FiscalAdapter.build_summary_peer_identity(discovered),
        issuer_id="issuer:fiscal:FSCLC-OTHER",
    )
    result = adapter.fetch_peer_standardized_actuals(conflicting, resolution)
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert source.by_key_calls == []


def test_missing_ebitda_is_not_manufactured_by_peer_actual_normalization():
    discovered = candidate()
    source = StableIdSource((company_record(),))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    result = adapter.fetch_peer_standardized_actuals(
        FiscalAdapter.build_summary_peer_identity(discovered),
        adapter.resolve_peer_financial_lookup(discovered),
    )
    assert {item.metric_id for item in result.observations} == {MetricId.REVENUE}
    assert MetricId.EBITDA not in {item.metric_id for item in result.observations}


def test_live_source_peer_financial_route_uses_company_key_without_symbol_lookup():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                compact = company_record()
                compact.pop("reportingCurrency")
                return Response({"data": [compact]})
            if "income-statement" in url:
                return Response({
                    "metrics": [{
                        "standardizedMetricId": "income_statement_total_revenues",
                        "metricName": "Total Revenues",
                    }],
                    "data": [{
                        "periodType": "Annual",
                        "fiscalYear": 2025,
                        "reportDate": "2025-12-31",
                        "metricsValues": {
                            "income_statement_total_revenues": {"value": 110, "currency": "USD"},
                        },
                    }],
                })
            return Response({"data": []})

    session = Session()
    source = LiveFiscalSource(session=session)
    source.company_lookup_records(credential="synthetic-secret")
    rows = tuple(source.standardized_financials_by_company_key(
        "OPAQUE-SYN",
        provider_symbol="SYN",
        credential="synthetic-secret",
    ))

    assert len(rows) == 1 and rows[0]["metric"] == "revenue"
    assert not any(url.endswith("/v3/company/profile") for url, _ in session.calls)
    financial_calls = [kwargs for url, kwargs in session.calls if "/standardized" in url]
    assert len(financial_calls) == 3
    assert all(call["params"] == {
        "companyKey": "OPAQUE-SYN",
        "periodType": "annual,quarterly",
    } for call in financial_calls)


def test_target_and_peer_requests_share_contract_without_reusing_target_key_or_ticker():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                target = company_record(
                    companyKey="OPAQUE-TARGET",
                    companyFiscalIdentifier="FSCLC-TARGET",
                    primaryListing={"ticker": "TGT", "securityFiscalIdentifier": "FSCLS-TARGET"},
                )
                return Response({"data": [target, company_record()]})
            return Response({"data": []})

    session = Session()
    source = LiveFiscalSource(session=session)
    tuple(source.standardized_financials("TGT", credential="synthetic-secret"))
    tuple(source.standardized_financials_by_company_key(
        "OPAQUE-SYN", provider_symbol="SYN", credential="synthetic-secret",
    ))

    calls = [(url, kwargs) for url, kwargs in session.calls if "/standardized" in url]
    assert len(calls) == 6
    target_calls, peer_calls = calls[:3], calls[3:]
    assert [url for url, _ in target_calls] == [url for url, _ in peer_calls]
    assert all(kwargs["params"]["companyKey"] == "OPAQUE-TARGET" for _, kwargs in target_calls)
    assert all(kwargs["params"]["companyKey"] == "OPAQUE-SYN" for _, kwargs in peer_calls)
    assert all(kwargs["params"]["companyKey"] not in {"SYN", "FSCLC-SYN", "companyKey"} for _, kwargs in peer_calls)
    assert all(kwargs["params"]["periodType"] == "annual,quarterly" for _, kwargs in calls)
    assert all(kwargs["headers"]["User-Agent"] == "StockAnalyser/0.7.1" for _, kwargs in calls)
    assert all(kwargs["headers"]["Accept"] == "application/json" for _, kwargs in calls)
    assert all(kwargs["headers"]["X-Api-Key"] == "synthetic-secret" for _, kwargs in calls)
    assert all(kwargs["timeout"] == 20 for _, kwargs in calls)


def test_verified_cached_profile_currency_is_the_only_peer_row_fallback():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        def get(self, url, **kwargs):
            if url.endswith("/v3/companies-list"):
                compact = company_record()
                compact.pop("reportingCurrency")
                return Response({"data": [compact]})
            if url.endswith("/v3/company/profile"):
                return Response({
                    "reportingCurrency": "GBP",
                    "primaryListing": {
                        "ticker": "SYN",
                        "exchangeCode": "NASDAQ",
                        "securityFiscalIdentifier": "FSCLS-SYN",
                    },
                })
            if "income-statement" in url:
                return Response({
                    "metrics": [{
                        "standardizedMetricId": "income_statement_total_revenues",
                        "metricName": "Total Revenues",
                    }],
                    "data": [{
                        "periodType": "Annual",
                        "fiscalYear": 2025,
                        "reportDate": "2025-12-31",
                        "metricsValues": {"income_statement_total_revenues": {"value": 110}},
                    }],
                })
            return Response({"data": []})

    source = LiveFiscalSource(session=Session())
    source.identity_metadata("SYN", credential="synthetic-secret")
    rows = tuple(source.standardized_financials_by_company_key(
        "OPAQUE-SYN", provider_symbol="SYN", credential="synthetic-secret",
    ))
    assert len(rows) == 1
    assert rows[0]["currency"] == "GBP"


def test_missing_all_currency_evidence_remains_unavailable_without_usd_default():
    rows = tuple({**row, "currency": None} for row in financial_rows())
    discovered = candidate()
    source = StableIdSource((company_record(),), rows=rows)
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    result = adapter.fetch_peer_standardized_actuals(
        FiscalAdapter.build_summary_peer_identity(discovered),
        adapter.resolve_peer_financial_lookup(discovered),
    )
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert not result.observations
    assert all(item.reason.startswith("Fiscal row") for item in result.issues)


def test_live_audit_uses_one_post_retrieval_cutoff_for_substituted_actuals():
    discovered = candidate()
    rows = tuple({**row, "asOf": None} for row in financial_rows())
    source = StableIdSource((company_record(),), rows=rows)
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    actuals = adapter.fetch_peer_standardized_actuals(
        FiscalAdapter.build_summary_peer_identity(discovered),
        adapter.resolve_peer_financial_lookup(discovered),
    ).observations
    context = LivePeerCandidateContext(
        candidate_id="candidate",
        listing_country="US",
        identity_source="summary_verified",
        identity_enrichment_missing=(),
        provider_relationship=None,
        financial_resolution_status="available",
        financial_resolution_source="synthetic",
        financial_resolution_reason=None,
        standardized_financial_status="available",
        standardized_financial_reason=None,
        normalized_annual_observation_count=2,
        annual_revenue_status="not_evaluated",
        latest_annual_revenue_period=None,
        previous_annual_revenue_period=None,
    )
    finalized = _finalize_annual_revenue_contexts(
        (context,),
        {"candidate": actuals},
        analysis_as_of=NOW + timedelta(seconds=1),
    )[0]
    assert finalized.annual_revenue_status == "available"
    assert str(finalized.latest_annual_revenue_period) == "2025-12-31"
    assert str(finalized.previous_annual_revenue_period) == "2024-12-31"
