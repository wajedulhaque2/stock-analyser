from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
import math

import pytest

from stock_analyser.domain import (
    AggregationStatus,
    CapabilityStatus,
    CompanyIdentity,
    CoverageLevel,
    DataAvailability,
    EstimateCase,
    ExternalReferenceType,
    ExternalValuationReference,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    ProviderSymbol,
    ValuationMethodStatus,
    ValuationResult,
    ensure_internal_valuation_results,
    stable_observation_id,
    validate_derived_provenance,
    validate_estimate_case_collection,
)


RETRIEVED = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc)


def provenance(*, as_of=AS_OF, inputs=(), steps=()) -> Provenance:
    return Provenance(
        provider="synthetic-consensus",
        endpoint_or_dataset="synthetic-estimates-v1",
        provider_symbol="SYN",
        retrieved_at=RETRIEVED,
        as_of_at=as_of,
        transformation_steps=tuple(steps),
        input_observation_ids=tuple(inputs),
    )


def observation(
    *,
    value=100.0,
    metric=MetricId.REVENUE,
    frequency=Frequency.ANNUAL,
    observation_type=ObservationType.ACTUAL,
    estimate_case=EstimateCase.NOT_APPLICABLE,
    period_start=date(2025, 1, 1),
    period_end=date(2025, 12, 31),
    fiscal_year=2025,
    fiscal_quarter=None,
    unit=MetricUnit.CURRENCY,
    currency="USD",
    analyst_count=None,
    as_of=AS_OF,
) -> MetricObservation:
    prov = provenance(as_of=as_of)
    obs_id = stable_observation_id(
        metric_id=metric.value,
        provider=prov.provider,
        provider_symbol=prov.provider_symbol,
        frequency=frequency.value,
        observation_type=observation_type.value,
        estimate_case=estimate_case.value,
        period_start=str(period_start or ""),
        period_end=str(period_end or ""),
        as_of_at=as_of.isoformat(),
    )
    return MetricObservation(
        observation_id=obs_id,
        metric_id=metric,
        value=value,
        unit=unit,
        currency=currency,
        frequency=frequency,
        observation_type=observation_type,
        estimate_case=estimate_case,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        analyst_count=analyst_count,
        retrieved_at=RETRIEVED,
        as_of_at=as_of,
        provenance=prov,
    )


def test_status_concepts_are_distinct_enum_types():
    assert DataAvailability.AVAILABLE is not CapabilityStatus.AVAILABLE
    assert ValuationMethodStatus.UNAVAILABLE is not AggregationStatus.UNAVAILABLE
    assert CoverageLevel.INSUFFICIENT.value == "insufficient"


def test_normal_usd_listed_company_identity_is_immutable():
    company = CompanyIdentity(
        canonical_symbol="SYN",
        security_id="security-us-syn",
        issuer_id="issuer-syn",
        company_name="Synthetic Corporation",
        issuer_domicile="US",
        listing_country="US",
        exchange="Synthetic Exchange",
        sector="Industrials",
        industry="Synthetic Manufacturing",
        security_type="Common stock",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1.0,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("synthetic", "SYN"),),
    )
    assert company.normalize_quote_price(125.5) == 125.5
    with pytest.raises(FrozenInstanceError):
        company.price_scale = 2.0


def test_london_gbp_quote_scale_only_normalizes_quote_price():
    company = CompanyIdentity(
        canonical_symbol="LON-SYN",
        security_id="security-gb-syn",
        issuer_id="issuer-gb-syn",
        company_name="Synthetic London Company",
        issuer_domicile="GB",
        listing_country="GB",
        exchange="Synthetic London Exchange",
        sector="Industrials",
        industry="Synthetic Engineering",
        security_type="Ordinary share",
        reporting_currency="GBP",
        quote_currency="GBP",
        quote_unit="GBp",
        price_scale=0.01,
        fiscal_year_end="12-31",
    )
    shares = 1_000_000_000.0
    reported_revenue = 5_000_000_000.0
    assert company.normalize_quote_price(1_265.0) == pytest.approx(12.65)
    assert shares == 1_000_000_000.0
    assert reported_revenue == 5_000_000_000.0
    assert company.quote_currency == "GBP" and company.quote_unit == "GBp"


def test_adr_identity_keeps_traded_and_underlying_security_distinct():
    adr = CompanyIdentity(
        canonical_symbol="ADR-SYN",
        security_id="traded-depositary-receipt",
        issuer_id="underlying-issuer",
        company_name="Synthetic Depositary Receipt",
        issuer_domicile="TW",
        listing_country="US",
        exchange="Synthetic US Exchange",
        sector="Technology",
        industry="Semiconductors",
        security_type="ADR",
        reporting_currency="TWD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1.0,
        fiscal_year_end="12-31",
        underlying_security_id="underlying-ordinary-share",
        adr_ratio=5.0,
    )
    assert adr.security_id != adr.underlying_security_id
    assert adr.issuer_id == "underlying-issuer"
    assert adr.adr_ratio == 5.0
    with pytest.raises(ValueError, match="explicit adr_ratio"):
        CompanyIdentity(
            canonical_symbol="BAD-ADR", security_id="bad-adr", issuer_id="issuer",
            company_name="Bad Synthetic ADR", issuer_domicile="GB", listing_country="US",
            exchange="Synthetic Exchange", sector="Other", industry="Other",
            security_type="ADR", reporting_currency="GBP", quote_currency="USD",
            quote_unit="USD", price_scale=1.0, fiscal_year_end="12-31",
            underlying_security_id="ordinary-share",
        )


def test_price_scale_and_adr_ratio_must_be_positive():
    base = dict(
        canonical_symbol="SYN", security_id="security", issuer_id="issuer",
        company_name="Synthetic", issuer_domicile="US", listing_country="US",
        exchange="Synthetic Exchange", sector="Other", industry="Other",
        security_type="Common stock", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", fiscal_year_end="12-31",
    )
    with pytest.raises(ValueError, match="price_scale"):
        CompanyIdentity(**base, price_scale=0)
    with pytest.raises(ValueError, match="adr_ratio"):
        CompanyIdentity(**base, price_scale=1, underlying_security_id="underlying", adr_ratio=0)


def test_annual_and_quarterly_actual_observations_have_explicit_semantics():
    annual = observation()
    quarterly = observation(
        frequency=Frequency.QUARTERLY,
        period_start=date(2025, 4, 1),
        period_end=date(2025, 6, 30),
        fiscal_year=2025,
        fiscal_quarter=2,
    )
    assert annual.frequency is Frequency.ANNUAL
    assert quarterly.frequency is Frequency.QUARTERLY
    assert annual != quarterly


def test_annual_estimate_requires_explicit_case_and_preserves_analyst_count():
    estimate = observation(
        observation_type=ObservationType.ESTIMATE,
        estimate_case=EstimateCase.AVERAGE,
        analyst_count=12,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        fiscal_year=2026,
    )
    assert estimate.estimate_case is EstimateCase.AVERAGE
    assert estimate.analyst_count == 12
    with pytest.raises(ValueError, match="explicit"):
        observation(observation_type=ObservationType.ESTIMATE)
    with pytest.raises(ValueError, match="non-negative"):
        observation(
            observation_type=ObservationType.ESTIMATE,
            estimate_case=EstimateCase.AVERAGE,
            analyst_count=-1,
        )


def test_actual_cannot_silently_carry_estimate_case_or_analyst_count():
    with pytest.raises(ValueError, match="cannot carry estimate"):
        observation(estimate_case=EstimateCase.LOW)
    with pytest.raises(ValueError, match="analyst_count"):
        observation(analyst_count=0)


def test_low_average_high_collection_and_blocking_inversion():
    def cases(values):
        return tuple(
            observation(
                value=value,
                observation_type=ObservationType.ESTIMATE,
                estimate_case=case,
                analyst_count=8,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 12, 31),
                fiscal_year=2026,
            )
            for case, value in zip((EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH), values)
        )

    assert validate_estimate_case_collection(cases((90, 100, 110))) == ()
    issues = validate_estimate_case_collection(cases((105, 100, 110)))
    assert len(issues) == 1
    assert issues[0].severity is IssueSeverity.BLOCKING
    assert "low <= average <= high" in issues[0].reason


def test_ltm_and_annual_same_metric_remain_distinct():
    annual = observation()
    ltm = observation(
        frequency=Frequency.LTM,
        period_start=date(2025, 7, 1),
        period_end=date(2026, 6, 30),
        fiscal_year=None,
    )
    assert annual.metric_id is ltm.metric_id
    assert annual.frequency is not ltm.frequency
    assert annual.observation_id != ltm.observation_id


def test_actual_and_estimate_same_fiscal_period_remain_distinct():
    actual = observation()
    estimate = observation(
        observation_type=ObservationType.ESTIMATE,
        estimate_case=EstimateCase.AVERAGE,
        analyst_count=None,
    )
    assert actual.period_end == estimate.period_end
    assert actual.observation_type is not estimate.observation_type
    assert actual.observation_id != estimate.observation_id


def test_historical_rows_from_estimate_endpoint_are_not_forward_by_origin():
    snapshot = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    rows = tuple(
        observation(
            value=year * 1_000_000.0,
            observation_type=ObservationType.ESTIMATE,
            estimate_case=EstimateCase.AVERAGE,
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            fiscal_year=year,
            as_of=snapshot,
        )
        for year in range(2021, 2026)
    )
    assert all(row.provenance.endpoint_or_dataset == "synthetic-estimates-v1" for row in rows)
    assert not any(row.is_forward_as_of for row in rows)


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_observations_reject_non_finite_values(bad_value):
    with pytest.raises(ValueError, match="finite"):
        observation(value=bad_value)


def test_observation_currency_period_and_point_in_time_invariants():
    with pytest.raises(ValueError, match="currency"):
        observation(currency=None)
    with pytest.raises(ValueError, match="three-letter"):
        observation(currency="usd")
    with pytest.raises(ValueError, match="period_start"):
        observation(period_start=date(2026, 1, 1), period_end=date(2025, 12, 31))
    with pytest.raises(ValueError, match="point-in-time"):
        observation(
            metric=MetricId.SHARE_PRICE,
            frequency=Frequency.POINT_IN_TIME,
            period_start=date(2025, 1, 1),
            period_end=AS_OF.date(),
            fiscal_year=None,
            unit=MetricUnit.CURRENCY_PER_SHARE,
        )
    point = observation(
        metric=MetricId.SHARE_PRICE,
        frequency=Frequency.POINT_IN_TIME,
        period_start=None,
        period_end=AS_OF.date(),
        fiscal_year=None,
        unit=MetricUnit.CURRENCY_PER_SHARE,
    )
    assert point.period_end == point.as_of_at.date()


def test_stable_observation_id_is_deterministic_and_semantic():
    one = observation()
    two = observation()
    quarterly = observation(
        frequency=Frequency.QUARTERLY,
        period_start=date(2025, 10, 1), period_end=date(2025, 12, 31),
        fiscal_year=2025, fiscal_quarter=4,
    )
    assert one.observation_id == two.observation_id
    assert one.observation_id != quarterly.observation_id


def test_derived_observation_provenance_requires_input_ids():
    with pytest.raises(ValueError, match="input_observation_ids"):
        validate_derived_provenance(provenance(steps=("derived ratio",)))
    validate_derived_provenance(provenance(steps=("derived ratio",), inputs=("obs:synthetic-input",)))


def test_cash_flow_and_profit_metrics_are_structurally_distinct():
    assert MetricId.OPERATING_INCOME is not MetricId.EBIT
    assert len({MetricId.FCFF, MetricId.FCFE, MetricId.OCF_LESS_CAPEX, MetricId.PROVIDER_DEFINED_FCF}) == 4


def test_generic_consensus_income_eps_and_derived_metrics_are_structurally_distinct():
    assert MetricId.NET_INCOME is not MetricId.NET_INCOME_COMMON
    assert len({MetricId.EPS, MetricId.EPS_BASIC, MetricId.EPS_DILUTED}) == 3
    assert MetricId.EBIT_MARGIN is not MetricId.OPERATING_MARGIN
    assert MetricId.REVENUE_GROWTH.value == "revenue_growth"


def test_structured_unavailable_valuation_has_no_fabricated_value():
    result = ValuationResult(
        method="synthetic verified cash-flow method",
        valuation_family="standard corporate",
        currency="USD",
        status=ValuationMethodStatus.UNAVAILABLE,
        reason="cash-flow economic definition is unknown",
    )
    assert result.low is result.central is result.high is None
    with pytest.raises(ValueError, match="cannot contain"):
        ValuationResult(
            method="synthetic method", valuation_family="standard corporate", currency="USD",
            status=ValuationMethodStatus.UNAVAILABLE, central=100, reason="missing inputs",
        )


@pytest.mark.parametrize(
    "reference_type,provider",
    [
        (ExternalReferenceType.FMP_STANDARD_DCF, "synthetic-fmp-reference"),
        (ExternalReferenceType.ANALYST_TARGET_CONSENSUS, "synthetic-target-provider"),
    ],
)
def test_external_references_cannot_be_internal_valuation_results(reference_type, provider):
    prov = Provenance(
        provider=provider,
        endpoint_or_dataset="synthetic-reference-dataset",
        provider_symbol="SYN",
        retrieved_at=RETRIEVED,
        as_of_at=AS_OF,
    )
    reference = ExternalValuationReference(
        reference_type=reference_type,
        value=125.0,
        currency="USD",
        provider=provider,
        as_of_at=AS_OF,
        provenance=prov,
    )
    assert not isinstance(reference, ValuationResult)
    with pytest.raises(TypeError, match="ValuationResult"):
        ensure_internal_valuation_results((reference,))
