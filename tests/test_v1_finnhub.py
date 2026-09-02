from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_analyser.domain import (
    CapabilityStatus,
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    DefinitionVerificationStatus,
    EstimateCase,
    IssueSeverity,
    MetricId,
    ObservationType,
    ProviderSymbol,
)
from stock_analyser.providers import (
    CacheKey,
    FinnhubAdapter,
    ProviderCapability,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    SecretReference,
    TransportResponse,
)


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
SECRET_A = "SYNTHETIC_FINNHUB_A"
SECRET_B = "SYNTHETIC_FINNHUB_B"


def company():
    return CompanyIdentity(
        canonical_symbol="CANON", security_id="security", issuer_id="issuer",
        company_name="Synthetic plc", issuer_domicile="GB", listing_country="GB",
        exchange="Synthetic Exchange", sector="Industrials", industry="Engineering",
        security_type="Ordinary share", reporting_currency="GBP", quote_currency="GBP",
        quote_unit="GBP", price_scale=1, fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("finnhub", "FH-SYN"),),
    )


def row(**changes):
    value = {
        "periodEnd": "2027-12-31", "asOf": NOW,
        "low": 90, "average": 100, "high": 110, "analystCount": 5,
    }
    value.update(changes)
    return value


class MetricTransport:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        outcome = self.outcomes[request.parameters["metric"]]
        if isinstance(outcome, BaseException):
            raise outcome
        return TransportResponse(status_code=200, headers={}, body=outcome, retrieved_at=NOW)


def adapter(outcomes, *, secret=SECRET_A, evidence=()):
    source = MetricTransport(outcomes)
    transport = RetryingTransport(
        source, retry_policy=RetryPolicy(max_attempts=1, jitter=0),
        sleep=lambda _: None, clock=lambda: NOW,
    )
    return FinnhubAdapter(
        transport, credential=SecretReference("finnhub", lambda: secret),
        cash_flow_definitions=tuple(evidence),
        endpoint_overrides={capability: "stock/estimates" for capability in (
            ProviderCapability.ANNUAL_REVENUE_ESTIMATES,
            ProviderCapability.ANNUAL_EBIT_ESTIMATES,
            ProviderCapability.ANNUAL_EBITDA_ESTIMATES,
            ProviderCapability.ANNUAL_NET_INCOME_ESTIMATES,
            ProviderCapability.ANNUAL_EPS_ESTIMATES,
            ProviderCapability.ANNUAL_OCF_ESTIMATES,
            ProviderCapability.ANNUAL_CAPEX_ESTIMATES,
            ProviderCapability.ANNUAL_FCF_ESTIMATES,
        )},
        clock=lambda: NOW,
    ), source


def test_documented_finnhub_nested_revenue_schema_and_endpoint_normalize():
    source = MetricTransport({})
    source.send = lambda request: (
        source.requests.append(request)
        or TransportResponse(
            status_code=200,
            headers={},
            body={
                "data": [{
                    "period": "2027-12-31",
                    "revenueLow": 90,
                    "revenueAvg": 100,
                    "revenueHigh": 110,
                    "numberAnalysts": 12,
                    "year": 2027,
                }],
                "freq": "annual",
                "symbol": "FH-SYN",
            },
            retrieved_at=NOW,
        )
    )
    client = FinnhubAdapter(
        RetryingTransport(source, retry_policy=RetryPolicy(max_attempts=1), clock=lambda: NOW),
        credential=SecretReference("finnhub", lambda: SECRET_A),
        clock=lambda: NOW,
    )
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert {item.value for item in result.observations} == {90, 100, 110}
    assert {item.analyst_count for item in result.observations} == {12}
    request = source.requests[0]
    assert request.url.endswith("/stock/revenue-estimate")
    assert request.parameters["freq"] == "annual"
    assert request.parameters["token"] == SECRET_A
    assert request.headers == {}
    assert "metric" not in request.parameters


def test_undocumented_cash_flow_estimate_endpoints_are_unavailable_without_io():
    source = MetricTransport({})
    client = FinnhubAdapter(
        RetryingTransport(source, retry_policy=RetryPolicy(max_attempts=1), clock=lambda: NOW),
        credential=SecretReference("finnhub", lambda: SECRET_A),
        clock=lambda: NOW,
    )
    for capability in (
        ProviderCapability.ANNUAL_OCF_ESTIMATES,
        ProviderCapability.ANNUAL_CAPEX_ESTIMATES,
        ProviderCapability.ANNUAL_FCF_ESTIMATES,
    ):
        result = client.fetch_annual_estimates(company(), capability)
        assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
        assert "No approved documented" in result.capabilities[0].reason
    assert source.requests == []


def test_documented_finnhub_403_is_classified_as_entitlement_by_adapter():
    source = MetricTransport({})
    source.send = lambda request: (
        source.requests.append(request)
        or TransportResponse(status_code=403, headers={}, body={}, retrieved_at=NOW)
    )
    client = FinnhubAdapter(
        RetryingTransport(source, retry_policy=RetryPolicy(max_attempts=1), clock=lambda: NOW),
        credential=SecretReference("finnhub", lambda: SECRET_A),
        clock=lambda: NOW,
    )

    result = client.fetch_annual_estimates(
        company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES,
    )

    assert result.capabilities[0].status is CapabilityStatus.LOCKED
    assert len(source.requests) == 1

    override_source = MetricTransport({})
    override_source.send = lambda request: TransportResponse(
        status_code=403, headers={}, body={}, retrieved_at=NOW,
    )
    override_client = FinnhubAdapter(
        RetryingTransport(
            override_source, retry_policy=RetryPolicy(max_attempts=1), clock=lambda: NOW,
        ),
        credential=SecretReference("finnhub", lambda: SECRET_A),
        endpoint_overrides={ProviderCapability.ANNUAL_REVENUE_ESTIMATES: "stock/estimates"},
        clock=lambda: NOW,
    )

    override_result = override_client.fetch_annual_estimates(
        company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES,
    )
    assert override_result.capabilities[0].status is CapabilityStatus.ERROR


@pytest.mark.parametrize(
    "capability,provider_metric,canonical_metric",
    [
        (ProviderCapability.ANNUAL_REVENUE_ESTIMATES, "revenue", MetricId.REVENUE),
        (ProviderCapability.ANNUAL_EBIT_ESTIMATES, "ebit", MetricId.EBIT),
        (ProviderCapability.ANNUAL_EBITDA_ESTIMATES, "ebitda", MetricId.EBITDA),
        (ProviderCapability.ANNUAL_NET_INCOME_ESTIMATES, "netIncome", MetricId.NET_INCOME),
        (ProviderCapability.ANNUAL_EPS_ESTIMATES, "eps", MetricId.EPS),
        (ProviderCapability.ANNUAL_OCF_ESTIMATES, "operatingCashFlow", MetricId.OPERATING_CASH_FLOW),
    ],
)
def test_finnhub_supported_validator_metrics_normalize_without_definition_overstatement(
    capability, provider_metric, canonical_metric,
):
    client, _ = adapter({provider_metric: [row()]})
    result = client.fetch_annual_estimates(company(), capability)
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert {item.metric_id for item in result.observations} == {canonical_metric}
    assert {item.estimate_case for item in result.observations} == {
        EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH,
    }
    assert all(item.observation_type is ObservationType.ESTIMATE for item in result.observations)
    assert all(item.provenance.provider == "finnhub" for item in result.observations)
    if canonical_metric is MetricId.NET_INCOME:
        assert not any(item.metric_id is MetricId.NET_INCOME_COMMON for item in result.observations)
    if canonical_metric is MetricId.EPS:
        assert not any(item.metric_id in {MetricId.EPS_BASIC, MetricId.EPS_DILUTED} for item in result.observations)


def test_metric_specific_analyst_count_is_used_only_when_present():
    client, _ = adapter({"revenue": [row(analystCount=None)]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    assert all(item.analyst_count is None for item in result.observations)


def test_capex_negative_outflow_normalizes_magnitude_and_inverts_range_endpoints_explicitly():
    client, _ = adapter({
        "capitalExpenditure": [row(
            low=-130, average=-120, high=-110, capexSignConvention="negative_outflow",
        )],
    })
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_CAPEX_ESTIMATES)
    values = {item.estimate_case: item.value for item in result.observations}
    assert values == {EstimateCase.LOW: 110, EstimateCase.AVERAGE: 120, EstimateCase.HIGH: 130}
    assert "inverted range endpoints" in result.observations[0].provenance.transformation_steps[-1]


def test_capex_without_explicit_sign_semantics_is_withheld_not_absolutized():
    client, _ = adapter({"capitalExpenditure": [row(low=-130, average=-120, high=-110)]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_CAPEX_ESTIMATES)
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert "sign convention" in result.issues[0].reason


def test_generic_fcf_stays_provider_defined_and_is_not_fcff_or_fcfe_by_name():
    client, _ = adapter({"freeCashFlow": [row()]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_FCF_ESTIMATES)
    assert {item.metric_id for item in result.observations} == {MetricId.PROVIDER_DEFINED_FCF}
    assert not any(item.metric_id in {MetricId.FCFF, MetricId.FCFE} for item in result.observations)
    evidence = result.cash_flow_definitions[0]
    assert evidence.definition is CashFlowDefinition.PROVIDER_DEFINED
    assert evidence.verification_status is DefinitionVerificationStatus.UNVERIFIED
    assert not evidence.is_verified_fcff and not evidence.is_verified_fcfe
    assert set(evidence.observation_ids) == {item.observation_id for item in result.observations}


def test_explicit_verified_fcff_evidence_is_machine_readable():
    evidence = CashFlowDefinitionEvidence(
        provider="finnhub", provider_metric="freeCashFlow",
        endpoint_or_dataset="finnhub_annual_estimates",
        definition=CashFlowDefinition.FCFF,
        verification_status=DefinitionVerificationStatus.VERIFIED,
        definition_reference="semantic-contract:finnhub-fcff-v1",
        verified_at=NOW,
        notes="Short audit reference only.",
    )
    client, _ = adapter({"freeCashFlow": [row()]}, evidence=(evidence,))
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_FCF_ESTIMATES)
    assert {item.metric_id for item in result.observations} == {MetricId.FCFF}
    assert result.cash_flow_definitions[0].is_verified_fcff


def test_invalid_range_is_blocked_without_reordering():
    client, _ = adapter({"revenue": [row(low=105, average=100, high=110)]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    assert result.observations == ()
    assert result.issues[0].severity is IssueSeverity.BLOCKING


def test_historical_estimate_row_remains_estimate_and_not_forward():
    client, _ = adapter({"revenue": [row(periodEnd="2025-12-31")]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    assert all(item.observation_type is ObservationType.ESTIMATE for item in result.observations)
    assert not any(item.is_forward_as_of for item in result.observations)


def test_missing_range_cases_remain_partial_without_fabrication():
    client, _ = adapter({"ebit": [row(low=None, high=None)]})
    result = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_EBIT_ESTIMATES)
    assert len(result.observations) == 1
    assert result.observations[0].estimate_case is EstimateCase.AVERAGE


def error(category):
    return ProviderError(
        provider=ProviderId.FINNHUB, endpoint_id="finnhub_annual_estimates",
        category=category, retryable=False, safe_message="safe synthetic provider failure",
        status_code=403 if category is ProviderErrorCategory.ENTITLEMENT else None,
    )


def test_locked_fcf_capability_does_not_invalidate_available_revenue():
    client, _ = adapter({
        "revenue": [row()],
        "freeCashFlow": error(ProviderErrorCategory.ENTITLEMENT),
    })
    revenue = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    fcf = client.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_FCF_ESTIMATES)
    assert revenue.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert fcf.capabilities[0].status is CapabilityStatus.LOCKED
    assert client.capability("annual_revenue_estimates").status is CapabilityStatus.AVAILABLE


def test_finnhub_secret_is_absent_from_safe_identity_repr_and_cache_key():
    first, first_source = adapter({"revenue": [row()]})
    second, second_source = adapter({"revenue": [row()]}, secret=SECRET_B)
    first.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    second.fetch_annual_estimates(company(), ProviderCapability.ANNUAL_REVENUE_ESTIMATES)
    request_a, request_b = first_source.requests[0], second_source.requests[0]
    assert SECRET_A not in repr(first._credential)
    assert SECRET_A not in repr(request_a)
    assert SECRET_A not in repr(request_a.safe_identity)
    assert CacheKey.from_request(request_a.safe_identity) == CacheKey.from_request(request_b.safe_identity)
