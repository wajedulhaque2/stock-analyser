from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from stock_analyser.domain import (
    CapabilityStatus,
    EstimateCase,
    ExternalReferenceType,
    ExternalValuationReference,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricUnit,
    ObservationType,
    ProviderSymbol,
    ValuationResult,
    CompanyIdentity,
    ensure_internal_valuation_results,
)
from stock_analyser.providers import (
    CacheKey,
    FmpAdapter,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    SecretReference,
    TransportResponse,
)
from stock_analyser.services import build_forward_consensus


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
SECRET_A = "SYNTHETIC_FMP_CREDENTIAL_A"
SECRET_B = "SYNTHETIC_FMP_CREDENTIAL_B"


def company(*, reporting_currency="GBP", quote_currency="GBP", price_scale=0.01):
    return CompanyIdentity(
        canonical_symbol="CANON", security_id="security", issuer_id="issuer",
        company_name="Synthetic plc", issuer_domicile="GB", listing_country="GB",
        exchange="Synthetic Exchange", sector="Industrials", industry="Engineering",
        security_type="Ordinary share", reporting_currency=reporting_currency,
        quote_currency=quote_currency, quote_unit="GBp", price_scale=price_scale,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fmp", "FMP-SYN"), ProviderSymbol("yahoo", "Y-SYN")),
    )


def estimate_row(year=2027, **changes):
    row = {
        "symbol": "FMP-SYN", "date": f"{year}-12-31",
        "revenueLow": 130, "revenueAvg": 145, "revenueHigh": 160,
        "ebitLow": 20, "ebitAvg": 25, "ebitHigh": 30,
        "ebitdaLow": 25, "ebitdaAvg": 32, "ebitdaHigh": 38,
        "netIncomeLow": 14, "netIncomeAvg": 18, "netIncomeHigh": 22,
        "epsLow": 1.4, "epsAvg": 1.8, "epsHigh": 2.2,
        "numAnalystsRevenue": 7, "numAnalystsEps": 4,
    }
    row.update(changes)
    return row


class EndpointTransport:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        outcome = self.outcomes[request.endpoint_id]
        if isinstance(outcome, BaseException):
            raise outcome
        return TransportResponse(status_code=200, headers={}, body=outcome, retrieved_at=NOW)


def adapter(outcomes, *, secret=SECRET_A):
    source = EndpointTransport(outcomes)
    transport = RetryingTransport(
        source, retry_policy=RetryPolicy(max_attempts=1, jitter=0),
        sleep=lambda _: None, clock=lambda: NOW,
    )
    return FmpAdapter(
        transport, credential=SecretReference("fmp", lambda: secret), clock=lambda: NOW,
    ), source


def estimate_adapter(rows):
    return adapter({"fmp_annual_analyst_estimates": rows})


def test_generic_fmp_metric_ids_do_not_overstate_economic_definitions():
    assert MetricId.NET_INCOME is not MetricId.NET_INCOME_COMMON
    assert MetricId.EPS is not MetricId.EPS_DILUTED
    assert MetricId.EPS is not MetricId.EPS_BASIC
    assert MetricId.EBIT_MARGIN is not MetricId.OPERATING_MARGIN


def test_fmp_provider_and_annual_capability_registration():
    client, _ = estimate_adapter([estimate_row()])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    assert client.provider is ProviderId.FMP
    assert result.capabilities[0].capability == "annual_analyst_estimates"
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE


def test_all_supported_estimate_ranges_normalize_without_definition_or_count_overstatement():
    client, _ = estimate_adapter([estimate_row()])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    by_metric = {}
    for observation in result.observations:
        by_metric.setdefault(observation.metric_id, []).append(observation)
    assert set(by_metric) == {MetricId.REVENUE, MetricId.EBIT, MetricId.EBITDA, MetricId.NET_INCOME, MetricId.EPS}
    assert {item.estimate_case for item in by_metric[MetricId.REVENUE]} == {
        EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH,
    }
    assert {item.analyst_count for item in by_metric[MetricId.REVENUE]} == {7}
    assert {item.analyst_count for item in by_metric[MetricId.EPS]} == {4}
    assert all(item.analyst_count is None for metric in (MetricId.EBIT, MetricId.EBITDA, MetricId.NET_INCOME) for item in by_metric[metric])
    assert all(item.observation_type is ObservationType.ESTIMATE for item in result.observations)
    assert all(item.frequency is Frequency.ANNUAL for item in result.observations)
    assert all(item.fiscal_year == 2027 and item.period_start == date(2027, 1, 1) for item in result.observations)
    assert all(item.currency == "GBP" for item in result.observations)
    assert all(item.provenance.endpoint_or_dataset == "fmp_annual_analyst_estimates" for item in result.observations)


def test_quote_subunit_scale_never_changes_statement_or_eps_consensus_levels():
    client, _ = estimate_adapter([estimate_row()])
    result = client.fetch_annual_estimates(company(price_scale=0.01), source_as_of_at=NOW)
    average_revenue = next(
        item for item in result.observations
        if item.metric_id is MetricId.REVENUE and item.estimate_case is EstimateCase.AVERAGE
    )
    average_eps = next(
        item for item in result.observations
        if item.metric_id is MetricId.EPS and item.estimate_case is EstimateCase.AVERAGE
    )
    assert average_revenue.value == 145
    assert average_eps.value == 1.8
    assert average_eps.unit is MetricUnit.CURRENCY_PER_SHARE


def test_missing_or_nonintegral_analyst_counts_remain_unknown():
    client, _ = estimate_adapter([estimate_row(numAnalystsRevenue=7.5, numAnalystsEps=None)])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    revenue = [item for item in result.observations if item.metric_id is MetricId.REVENUE]
    eps = [item for item in result.observations if item.metric_id is MetricId.EPS]
    assert revenue and eps
    assert all(item.analyst_count is None for item in (*revenue, *eps))
    assert any("analyst_count remains unknown" in item.reason for item in result.issues)


@pytest.mark.parametrize(
    "changes",
    [
        {"revenueLow": 150, "revenueAvg": 145, "revenueHigh": 160},
        {"revenueLow": 130, "revenueAvg": 165, "revenueHigh": 160},
    ],
)
def test_invalid_estimate_order_is_blocked_without_reordering(changes):
    client, _ = estimate_adapter([estimate_row(**changes)])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    assert not any(item.metric_id is MetricId.REVENUE for item in result.observations)
    issue = next(item for item in result.issues if item.metric is MetricId.REVENUE)
    assert issue.severity is IssueSeverity.BLOCKING
    assert "low <= average <= high" in issue.reason


def test_missing_metric_case_keeps_other_estimates_available_and_does_not_fabricate():
    client, _ = estimate_adapter([estimate_row(ebitdaAvg=None)])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    assert any(item.metric_id is MetricId.REVENUE for item in result.observations)
    assert not any(
        item.metric_id is MetricId.EBITDA and item.estimate_case is EstimateCase.AVERAGE
        for item in result.observations
    )
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE


def test_reporting_currency_is_required_and_usd_is_never_defaulted():
    incomplete = SimpleNamespace(
        provider_symbols=(ProviderSymbol("fmp", "FMP-SYN"),),
        reporting_currency=None, fiscal_year_end="12-31",
    )
    client, source = estimate_adapter([estimate_row()])
    result = client.fetch_annual_estimates(incomplete, source_as_of_at=NOW)
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert source.requests == []
    assert "currency" in result.issues[0].reason


def test_incompatible_fiscal_period_is_structured_and_not_guessed():
    client, _ = estimate_adapter([estimate_row(date="2027-09-30")])
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    assert result.observations == ()
    assert result.issues[0].severity is IssueSeverity.ERROR


def test_missing_source_as_of_uses_retrieval_time_with_explicit_provenance():
    client, _ = estimate_adapter([estimate_row()])
    result = client.fetch_annual_estimates(company())
    assert result.observations[0].as_of_at == NOW
    assert any("retrieval time" in step for step in result.observations[0].provenance.transformation_steps)


def test_historical_estimates_remain_estimates_but_are_excluded_from_forward_horizon():
    rows = [
        estimate_row(2025),
        estimate_row(2026, revenueLow=110, revenueAvg=120, revenueHigh=130),
        estimate_row(2027),
    ]
    client, _ = estimate_adapter(rows)
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    consensus = build_forward_consensus(normalized.observations, as_of_at=NOW)
    assert {item.fiscal_year for item in consensus.historical_estimates} == {2025}
    assert all(item.observation_type is ObservationType.ESTIMATE for item in consensus.historical_estimates)
    assert [period.fiscal_year for period in consensus.periods] == [2026, 2027]


def test_current_unfinished_fiscal_year_is_fy1_and_fy1_is_not_ntm_or_scenario():
    client, _ = estimate_adapter([
        estimate_row(2026, revenueLow=110, revenueAvg=120, revenueHigh=130), estimate_row(2027),
    ])
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    consensus = build_forward_consensus(normalized.observations, as_of_at=NOW)
    assert consensus.fy1.fiscal_year == 2026 and consensus.fy1.horizon_label == "FY1"
    assert consensus.fy2.fiscal_year == 2027 and consensus.fy2.horizon_label == "FY2"
    assert all(item.frequency is Frequency.ANNUAL for period in consensus.periods for item in period.observations)
    assert not hasattr(consensus, "ntm")
    assert not hasattr(consensus, "bear") and not hasattr(consensus, "bull")


def test_forward_years_are_ordered_by_period_not_response_order():
    client, _ = estimate_adapter([estimate_row(2029), estimate_row(2027), estimate_row(2028)])
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    consensus = build_forward_consensus(normalized.observations, as_of_at=NOW)
    assert [item.fiscal_year for item in consensus.periods] == [2027, 2028, 2029]
    assert [item.horizon_label for item in consensus.periods] == ["FY1", "FY2", "FY3"]


def test_consensus_derives_only_aligned_average_growth_and_margins_with_input_provenance():
    rows = [
        estimate_row(2026, revenueLow=110, revenueAvg=120, revenueHigh=130, ebitAvg=24, ebitdaAvg=30),
        estimate_row(
            2027, revenueLow=130, revenueAvg=150, revenueHigh=170,
            ebitAvg=33, ebitdaAvg=39, ebitdaHigh=45,
        ),
    ]
    client, _ = estimate_adapter(rows)
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    consensus = build_forward_consensus(normalized.observations, as_of_at=NOW)
    derived = {(item.metric_id, item.fiscal_year): item for item in consensus.derived_observations}
    assert derived[(MetricId.REVENUE_GROWTH, 2027)].value == pytest.approx(0.25)
    assert derived[(MetricId.EBIT_MARGIN, 2026)].value == pytest.approx(0.20)
    assert derived[(MetricId.EBITDA_MARGIN, 2027)].value == pytest.approx(0.26)
    assert all(item.estimate_case is EstimateCase.AVERAGE for item in derived.values())
    assert all(item.unit is MetricUnit.PERCENT_DECIMAL for item in derived.values())
    assert all(item.provenance.input_observation_ids for item in derived.values())
    assert all(item.provenance.endpoint_or_dataset == "v1_forward_consensus_derived" for item in derived.values())


def test_missing_average_input_prevents_only_dependent_derivation():
    rows = [
        estimate_row(2026, revenueLow=110, revenueAvg=120, revenueHigh=130),
        estimate_row(2027, revenueLow=130, revenueAvg=150, revenueHigh=170, ebitdaAvg=None),
    ]
    client, _ = estimate_adapter(rows)
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    consensus = build_forward_consensus(normalized.observations, as_of_at=NOW)
    assert any(item.metric_id is MetricId.REVENUE_GROWTH for item in consensus.derived_observations)
    assert not any(
        item.metric_id is MetricId.EBITDA_MARGIN and item.fiscal_year == 2027
        for item in consensus.derived_observations
    )
    assert consensus.fy2.observation(MetricId.REVENUE, EstimateCase.AVERAGE) is not None


def test_consensus_service_blocks_invalid_canonical_ranges_too():
    client, _ = estimate_adapter([estimate_row()])
    normalized = client.fetch_annual_estimates(company(), source_as_of_at=NOW).observations
    revenue = [item for item in normalized if item.metric_id is MetricId.REVENUE]
    corrupted = tuple(
        replace(item, value=200)
        if item.metric_id is MetricId.REVENUE and item.estimate_case is EstimateCase.LOW
        else item
        for item in normalized
    )
    consensus = build_forward_consensus(corrupted, as_of_at=NOW)
    assert not any(item.metric_id is MetricId.REVENUE for item in consensus.fy1.observations)
    assert any(item.severity is IssueSeverity.BLOCKING for item in consensus.issues)
    assert len(revenue) == 3


def test_all_four_price_targets_are_external_references_in_quote_currency():
    body = [{
        "targetLow": 80, "targetMedian": 100, "targetConsensus": 105, "targetHigh": 130,
        "lastUpdated": "2026-08-25T18:00:00+00:00",
    }]
    client, _ = adapter({"fmp_analyst_price_targets": body})
    result = client.fetch_price_targets(company())
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert {item.reference_type for item in result.references} == {
        ExternalReferenceType.ANALYST_TARGET_LOW,
        ExternalReferenceType.ANALYST_TARGET_MEDIAN,
        ExternalReferenceType.ANALYST_TARGET_CONSENSUS,
        ExternalReferenceType.ANALYST_TARGET_HIGH,
    }
    assert all(type(item) is ExternalValuationReference for item in result.references)
    assert all(item.currency == "GBP" for item in result.references)
    assert not any(isinstance(item, ValuationResult) for item in result.references)
    with pytest.raises(TypeError):
        ensure_internal_valuation_results(result.references)


def test_standard_dcf_is_external_only_and_returned_stock_price_is_ignored():
    client, _ = adapter({
        "fmp_external_standard_dcf": [{"date": "2026-08-25", "dcf": 111, "Stock Price": 999}],
    })
    result = client.fetch_standard_dcf(company())
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert len(result.references) == 1
    reference = result.references[0]
    assert reference.reference_type is ExternalReferenceType.FMP_STANDARD_DCF
    assert reference.value == 111
    assert type(reference) is ExternalValuationReference
    assert not isinstance(reference, ValuationResult)
    assert not hasattr(result, "observations")
    with pytest.raises(TypeError):
        ensure_internal_valuation_results(result.references)


def test_external_reference_currency_is_required_and_never_defaulted():
    incomplete = SimpleNamespace(
        provider_symbols=(ProviderSymbol("fmp", "FMP-SYN"),), quote_currency=None,
    )
    client, source = adapter({"fmp_analyst_price_targets": [{"targetLow": 80}]})
    result = client.fetch_price_targets(incomplete)
    assert result.references == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert source.requests == []


def provider_error(category):
    return ProviderError(
        provider=ProviderId.FMP, endpoint_id="synthetic_fmp_endpoint",
        category=category, retryable=False, safe_message="safe synthetic failure",
        status_code=403 if category is ProviderErrorCategory.ENTITLEMENT else None,
    )


@pytest.mark.parametrize(
    "error,status",
    [
        (provider_error(ProviderErrorCategory.ENTITLEMENT), CapabilityStatus.LOCKED),
        (provider_error(ProviderErrorCategory.NOT_FOUND), CapabilityStatus.UNAVAILABLE),
        (provider_error(ProviderErrorCategory.UNKNOWN), CapabilityStatus.ERROR),
    ],
)
def test_estimate_endpoint_errors_have_controlled_independent_status(error, status):
    client, _ = adapter({"fmp_annual_analyst_estimates": error})
    result = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    assert result.capabilities[0].status is status


def test_price_targets_and_dcf_capabilities_are_independent_of_estimates():
    client, _ = adapter({
        "fmp_annual_analyst_estimates": provider_error(ProviderErrorCategory.UNKNOWN),
        "fmp_analyst_price_targets": [{"targetLow": 80, "lastUpdated": NOW}],
        "fmp_external_standard_dcf": provider_error(ProviderErrorCategory.ENTITLEMENT),
    })
    estimates = client.fetch_annual_estimates(company(), source_as_of_at=NOW)
    targets = client.fetch_price_targets(company())
    dcf = client.fetch_standard_dcf(company())
    assert estimates.capabilities[0].status is CapabilityStatus.ERROR
    assert targets.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert dcf.capabilities[0].status is CapabilityStatus.LOCKED
    assert client.capability("analyst_price_targets").status is CapabilityStatus.AVAILABLE


def test_secret_is_absent_from_request_repr_safe_identity_and_cache_key():
    first, first_source = estimate_adapter([estimate_row()])
    second, second_source = adapter({"fmp_annual_analyst_estimates": [estimate_row()]}, secret=SECRET_B)
    first.fetch_annual_estimates(company(), source_as_of_at=NOW)
    second.fetch_annual_estimates(company(), source_as_of_at=NOW)
    request_a, request_b = first_source.requests[0], second_source.requests[0]
    assert request_a.url == "https://financialmodelingprep.com/stable/analyst-estimates"
    assert request_a.parameters == {
        "symbol": "FMP-SYN", "apikey": SECRET_A, "period": "annual",
    }
    assert SECRET_A not in repr(first._credential)
    assert SECRET_A not in repr(request_a)
    assert SECRET_A not in repr(request_a.safe_identity)
    assert "apikey" not in repr(request_a.safe_identity).lower()
    assert CacheKey.from_request(request_a.safe_identity) == CacheKey.from_request(request_b.safe_identity)
