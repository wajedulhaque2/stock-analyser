from __future__ import annotations

from datetime import date, datetime, timezone

from stock_analyser.domain import (
    CapabilityStatus,
    CompanyIdentity,
    EstimateCase,
    Frequency,
    IssueSeverity,
    MetricId,
    ObservationType,
    ProviderSymbol,
)
from stock_analyser.providers import (
    AlphaVantageAdapter,
    CacheKey,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    SecretReference,
    TransportResponse,
)


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
SECRET_A = "SYNTHETIC_ALPHA_A"
SECRET_B = "SYNTHETIC_ALPHA_B"


def company() -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol="CANON", security_id="security", issuer_id="issuer",
        company_name="Synthetic plc", issuer_domicile="GB", listing_country="GB",
        exchange="Synthetic Exchange", sector="Industrials", industry="Engineering",
        security_type="Ordinary share", reporting_currency="GBP", quote_currency="GBP",
        quote_unit="GBP", price_scale=1, fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("alpha_vantage", "AV-SYN"),),
    )


def annual_row(**changes):
    value = {
        "periodEnd": "2027-12-31", "asOf": NOW,
        "revenueLow": 90, "revenueAverage": 100, "revenueHigh": 110,
        "revenueAnalystCount": 8, "revenuePriorEstimate": 98,
        "revenueUpRevisions30Days": 3, "revenueDownRevisions30Days": 1,
        "epsLow": 1.8, "epsAverage": 2.0, "epsHigh": 2.2,
        "epsAnalystCount": 6, "epsPriorEstimate": 1.9,
        "epsUpRevisions30Days": 2, "epsDownRevisions30Days": 0,
    }
    value.update(changes)
    return value


def quarterly_row(**changes):
    value = annual_row(
        periodStart="2027-01-01", periodEnd="2027-03-31",
        fiscalYear=2027, fiscalQuarter=1,
    )
    value.update(changes)
    return value


class FakeTransport:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return TransportResponse(status_code=200, headers={}, body=self.outcome, retrieved_at=NOW)


def adapter(body, *, secret=SECRET_A):
    source = FakeTransport(body)
    transport = RetryingTransport(
        source, retry_policy=RetryPolicy(max_attempts=1, jitter=0),
        sleep=lambda _: None, clock=lambda: NOW,
    )
    return AlphaVantageAdapter(
        transport, credential=SecretReference("alpha_vantage", lambda: secret), clock=lambda: NOW,
    ), source


def test_annual_and_quarterly_revenue_and_generic_eps_are_distinct_normalized_estimates():
    client, _ = adapter({
        "annualEstimates": [annual_row()],
        "quarterlyEstimates": [quarterly_row()],
    })
    result = client.fetch_earnings_estimates(company())
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert {item.metric_id for item in result.observations} == {MetricId.REVENUE, MetricId.EPS}
    assert not any(item.metric_id in {MetricId.EPS_BASIC, MetricId.EPS_DILUTED} for item in result.observations)
    assert {item.frequency for item in result.observations} == {Frequency.ANNUAL, Frequency.QUARTERLY}
    annual = [item for item in result.observations if item.frequency is Frequency.ANNUAL]
    quarterly = [item for item in result.observations if item.frequency is Frequency.QUARTERLY]
    assert all(item.period_start == date(2027, 1, 1) and item.fiscal_quarter is None for item in annual)
    assert all(item.period_end == date(2027, 3, 31) and item.fiscal_quarter == 1 for item in quarterly)
    assert all(item.observation_type is ObservationType.ESTIMATE for item in result.observations)


def test_live_unified_snake_case_schema_normalizes_without_raw_fixture():
    live_row = {
        "date": "2027-12-31",
        "horizon": "fiscal year",
        "revenue_estimate_low": 90,
        "revenue_estimate_average": 100,
        "revenue_estimate_high": 110,
        "revenue_estimate_analyst_count": 8,
        "eps_estimate_low": 1.8,
        "eps_estimate_average": 2.0,
        "eps_estimate_high": 2.2,
        "eps_estimate_analyst_count": 6,
        "eps_estimate_average_30_days_ago": 1.9,
        "eps_estimate_revision_up_trailing_30_days": 2,
        "eps_estimate_revision_down_trailing_30_days": 0,
    }
    quarterly = dict(live_row, date="2027-03-31", horizon="fiscal quarter")
    client, _ = adapter({"symbol": "AV-SYN", "estimates": [live_row, quarterly]})

    result = client.fetch_earnings_estimates(company())

    assert {item.frequency for item in result.observations} == {
        Frequency.ANNUAL, Frequency.QUARTERLY,
    }
    assert {item.analyst_count for item in result.observations if item.metric_id is MetricId.REVENUE} == {8}
    assert any(
        item.provenance.source_metric == "revenue_estimate_average"
        for item in result.observations
    )
    quarterly_items = [item for item in result.observations if item.frequency is Frequency.QUARTERLY]
    assert quarterly_items and all(item.fiscal_quarter == 1 for item in quarterly_items)
    assert all(item.period_start == date(2027, 1, 1) for item in quarterly_items)
    eps_revisions = [item for item in result.revisions if item.metric_id is MetricId.EPS]
    assert eps_revisions and all(item.prior_estimate == 1.9 for item in eps_revisions)
    assert not any(item.metric_id is MetricId.REVENUE for item in result.revisions)


def test_metric_specific_counts_and_missing_cases_are_not_fabricated():
    client, _ = adapter({
        "annualEstimates": [annual_row(revenueLow=None, epsHigh=None, revenueAnalystCount=None)],
        "quarterlyEstimates": [],
    })
    result = client.fetch_earnings_estimates(company())
    revenue = [item for item in result.observations if item.metric_id is MetricId.REVENUE]
    eps = [item for item in result.observations if item.metric_id is MetricId.EPS]
    assert {item.estimate_case for item in revenue} == {EstimateCase.AVERAGE, EstimateCase.HIGH}
    assert {item.estimate_case for item in eps} == {EstimateCase.LOW, EstimateCase.AVERAGE}
    assert all(item.analyst_count is None for item in revenue)
    assert {item.analyst_count for item in eps} == {6}


def test_malformed_level_and_revision_counts_remain_unknown_without_crashing():
    client, _ = adapter({
        "annualEstimates": [annual_row(
            revenueAnalystCount="not-a-count", revenueUpRevisions30Days="not-a-count",
        )],
        "quarterlyEstimates": [],
    })
    result = client.fetch_earnings_estimates(company())
    revenue = [item for item in result.observations if item.metric_id is MetricId.REVENUE]
    revision = next(item for item in result.revisions if item.metric_id is MetricId.REVENUE)
    assert revenue and all(item.analyst_count is None for item in revenue)
    assert revision.analyst_count is None and revision.up_revisions is None
    assert any("invalid and remains unknown" in item.reason for item in result.issues)


def test_revision_evidence_is_separate_from_estimate_observations_and_preserves_window():
    client, _ = adapter({"annualEstimates": [annual_row()], "quarterlyEstimates": []})
    result = client.fetch_earnings_estimates(company())
    assert result.capabilities[1].status is CapabilityStatus.AVAILABLE
    assert len(result.revisions) == 2
    revenue = next(item for item in result.revisions if item.metric_id is MetricId.REVENUE)
    assert revenue.revision_window == "30_days"
    assert (revenue.current_estimate, revenue.prior_estimate) == (100, 98)
    assert (revenue.up_revisions, revenue.down_revisions, revenue.analyst_count) == (3, 1, 8)
    assert revenue.provenance.endpoint_or_dataset == "alpha_estimate_revisions"


def test_absent_revision_fields_remain_absent_and_do_not_create_revision_records():
    changes = {
        key: None for key in (
            "revenuePriorEstimate", "revenueUpRevisions30Days", "revenueDownRevisions30Days",
            "epsPriorEstimate", "epsUpRevisions30Days", "epsDownRevisions30Days",
        )
    }
    client, _ = adapter({"annualEstimates": [annual_row(**changes)], "quarterlyEstimates": []})
    result = client.fetch_earnings_estimates(company())
    assert result.observations
    assert result.revisions == ()
    assert result.capabilities[1].status is CapabilityStatus.UNAVAILABLE


def test_invalid_range_blocks_only_that_metric_without_reordering():
    client, _ = adapter({
        "annualEstimates": [annual_row(revenueLow=105, revenueAverage=100)],
        "quarterlyEstimates": [],
    })
    result = client.fetch_earnings_estimates(company())
    assert not any(item.metric_id is MetricId.REVENUE for item in result.observations)
    assert any(item.metric_id is MetricId.EPS for item in result.observations)
    assert any(item.severity is IssueSeverity.BLOCKING for item in result.issues)


def test_historical_rows_from_estimate_endpoint_are_not_treated_as_forward():
    client, _ = adapter({"annualEstimates": [annual_row(periodEnd="2025-12-31")], "quarterlyEstimates": []})
    result = client.fetch_earnings_estimates(company())
    assert result.observations and not any(item.is_forward_as_of for item in result.observations)


def test_provider_failure_sets_both_shared_endpoint_capabilities_without_payload_leakage():
    failure = ProviderError(
        provider=ProviderId.ALPHA_VANTAGE, endpoint_id="alpha_earnings_estimates",
        category=ProviderErrorCategory.ENTITLEMENT, retryable=False,
        safe_message="safe synthetic provider failure", status_code=403,
    )
    client, _ = adapter(failure)
    result = client.fetch_earnings_estimates(company())
    assert {item.status for item in result.capabilities} == {CapabilityStatus.LOCKED}
    assert all(SECRET_A not in (item.reason or "") for item in result.issues)


def test_alpha_secret_is_absent_from_safe_identity_repr_and_cache_key():
    first, first_source = adapter({"annualEstimates": [], "quarterlyEstimates": []})
    second, second_source = adapter({"annualEstimates": [], "quarterlyEstimates": []}, secret=SECRET_B)
    first.fetch_earnings_estimates(company())
    second.fetch_earnings_estimates(company())
    request_a, request_b = first_source.requests[0], second_source.requests[0]
    assert SECRET_A not in repr(first._credential)
    assert SECRET_A not in repr(request_a)
    assert SECRET_A not in repr(request_a.safe_identity)
    assert CacheKey.from_request(request_a.safe_identity) == CacheKey.from_request(request_b.safe_identity)


def test_naive_snapshot_timestamp_is_rejected_before_provider_io():
    client, source = adapter({"annualEstimates": [], "quarterlyEstimates": []})
    result = client.fetch_earnings_estimates(company(), source_as_of_at=datetime(2026, 8, 26, 12))
    assert not source.requests
    assert {item.status for item in result.capabilities} == {CapabilityStatus.ERROR}
