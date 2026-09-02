from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    EstimateCase,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    ProviderSymbol,
    ReconciliationDatasetType,
    ReconciliationStatus,
    ReconciliationStream,
    SourceAgreementLevel,
    stable_observation_id,
)
from stock_analyser.services import (
    DEFAULT_RECONCILIATION_POLICY,
    ObservationSource,
    reconcile_many,
    reconcile_sources,
)


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
COMPARISON = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)


def identity(provider, provider_symbol, *, security_id="security-1", issuer_id="issuer-1"):
    return CompanyIdentity(
        canonical_symbol="CANON", security_id=security_id, issuer_id=issuer_id,
        company_name="Synthetic plc", issuer_domicile="US", listing_country="US",
        exchange="Synthetic Exchange", sector="Industrials", industry="Engineering",
        security_type="Ordinary share", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1, fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol(provider, provider_symbol),),
    )


def observation(
    provider,
    provider_symbol,
    value=100.0,
    *,
    metric=MetricId.REVENUE,
    frequency=Frequency.ANNUAL,
    observation_type=ObservationType.ESTIMATE,
    estimate_case=EstimateCase.AVERAGE,
    currency="USD",
    unit=MetricUnit.CURRENCY,
    period_start=None,
    period_end=None,
    fiscal_year=None,
    fiscal_quarter=None,
    as_of=NOW,
    retrieved=None,
    source_metric="synthetic_metric",
):
    if frequency is Frequency.ANNUAL:
        period_start = period_start or date(2027, 1, 1)
        period_end = period_end or date(2027, 12, 31)
        fiscal_year = 2027 if fiscal_year is None else fiscal_year
        fiscal_quarter = None
    elif frequency is Frequency.QUARTERLY:
        period_start = period_start or date(2027, 1, 1)
        period_end = period_end or date(2027, 3, 31)
        fiscal_year = 2027 if fiscal_year is None else fiscal_year
        fiscal_quarter = 1 if fiscal_quarter is None else fiscal_quarter
    elif frequency is Frequency.POINT_IN_TIME:
        period_start = None
        period_end = period_end or as_of.date()
        fiscal_year = None
        fiscal_quarter = None
    else:
        period_start = period_start or date(2026, 9, 1)
        period_end = period_end or date(2027, 8, 31)
        fiscal_year = None
        fiscal_quarter = None
    if observation_type is ObservationType.ACTUAL:
        estimate_case = EstimateCase.NOT_APPLICABLE
    retrieved = retrieved or (as_of + timedelta(hours=1))
    provenance = Provenance(
        provider=provider, endpoint_or_dataset="synthetic_dataset",
        provider_symbol=provider_symbol, retrieved_at=retrieved, as_of_at=as_of,
        source_metric=source_metric,
    )
    observation_id = stable_observation_id(
        metric_id=metric.value, provider=provider, provider_symbol=provider_symbol,
        frequency=frequency.value, observation_type=observation_type.value,
        estimate_case=estimate_case.value,
        period_start=period_start.isoformat() if period_start else "",
        period_end=period_end.isoformat() if period_end else "",
        as_of_at=as_of.isoformat(),
    )
    return MetricObservation(
        observation_id=observation_id, metric_id=metric, value=value, unit=unit,
        frequency=frequency, observation_type=observation_type,
        estimate_case=estimate_case, currency=currency,
        period_start=period_start, period_end=period_end,
        fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
        retrieved_at=retrieved, as_of_at=as_of, provenance=provenance,
    )


def source(provider, provider_symbol, observations, dataset_type, **changes):
    return ObservationSource(
        identity=identity(provider, provider_symbol, **{
            key: changes.pop(key) for key in tuple(changes) if key in {"security_id", "issuer_id"}
        }),
        provider=provider, dataset_type=dataset_type,
        observations=tuple(observations), **changes,
    )


def forward_pair(
    canonical_value=100.0,
    validator_value=100.0,
    *,
    metric=MetricId.REVENUE,
    validator_provider="finnhub",
    canonical_changes=None,
    validator_changes=None,
    canonical_identity_changes=None,
    validator_identity_changes=None,
):
    canonical_changes = canonical_changes or {}
    validator_changes = validator_changes or {}
    canonical = observation("fmp", "FMP-SYN", canonical_value, **{"metric": metric, **canonical_changes})
    validator = observation(
        validator_provider, "VAL-SYN", validator_value, **{"metric": metric, **validator_changes},
    )
    canonical_source = source(
        "fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS,
        **(canonical_identity_changes or {}),
    )
    validator_source = source(
        validator_provider, "VAL-SYN", (validator,), ReconciliationDatasetType.VALIDATOR_ESTIMATE,
        **(validator_identity_changes or {}),
    )
    return canonical, validator, reconcile_sources(
        canonical_source, validator_source,
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )


def actual_pair(metric=MetricId.REVENUE, *, canonical_changes=None, validator_changes=None):
    canonical = observation("fiscal", "FISCAL-SYN", **{
        "metric": metric, "observation_type": ObservationType.ACTUAL, **(canonical_changes or {}),
    })
    validator = observation("sec", "CIK-0001", **{
        "metric": metric, "observation_type": ObservationType.ACTUAL, **(validator_changes or {}),
    })
    return reconcile_sources(
        source("fiscal", "FISCAL-SYN", (canonical,), ReconciliationDatasetType.STANDARDIZED_ACTUAL),
        source("sec", "CIK-0001", (validator,), ReconciliationDatasetType.REPORTED_ACTUAL),
        stream=ReconciliationStream.HISTORICAL_STANDARDIZED_FINANCIALS,
        comparison_as_of=COMPARISON,
    )


def price_result(validator_value):
    kwargs = dict(
        metric=MetricId.SHARE_PRICE, frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL, unit=MetricUnit.CURRENCY_PER_SHARE,
    )
    canonical = observation("yahoo", "Y-SYN", 100, **kwargs)
    validator = observation("fmp", "FMP-SYN", validator_value, **kwargs)
    return reconcile_sources(
        source("yahoo", "Y-SYN", (canonical,), ReconciliationDatasetType.MARKET),
        source("fmp", "FMP-SYN", (validator,), ReconciliationDatasetType.MARKET),
        stream=ReconciliationStream.CURRENT_MARKET_PRICE,
        comparison_as_of=COMPARISON,
    )


def test_same_security_with_different_provider_symbols_reconciles():
    _, _, result = forward_pair()
    assert result.status is ReconciliationStatus.COMPARED
    assert result.canonical_provider_symbol == "FMP-SYN"
    assert result.validator_provider_symbol == "VAL-SYN"
    assert result.agreement_level is SourceAgreementLevel.CONFIRMED


@pytest.mark.parametrize("identity_change", [{"security_id": "security-2"}, {"issuer_id": "issuer-2"}])
def test_different_canonical_identity_cannot_reconcile(identity_change):
    _, _, result = forward_pair(validator_identity_changes=identity_change)
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert set(result.semantic_mismatches) & set(identity_change)


def test_same_metric_period_case_currency_and_unit_compares_successfully():
    _, _, result = forward_pair(100, 102)
    assert result.status is ReconciliationStatus.COMPARED
    assert result.absolute_difference == 2
    assert result.relative_difference == 0.02


@pytest.mark.parametrize(
    "validator_changes,mismatch",
    [
        ({"frequency": Frequency.QUARTERLY}, "frequency"),
        ({"frequency": Frequency.LTM}, "frequency"),
        ({"observation_type": ObservationType.ACTUAL}, "observation_type"),
        ({"estimate_case": EstimateCase.LOW}, "estimate_case"),
        ({"currency": "GBP"}, "currency"),
        ({"unit": MetricUnit.CURRENCY_PER_SHARE}, "unit"),
    ],
)
def test_core_semantic_mismatches_are_not_comparable(validator_changes, mismatch):
    _, _, result = forward_pair(validator_changes=validator_changes)
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert mismatch in result.semantic_mismatches
    assert result.relative_difference is None


@pytest.mark.parametrize(
    "canonical_metric,validator_metric",
    [
        (MetricId.OPERATING_INCOME, MetricId.EBIT),
        (MetricId.NET_INCOME, MetricId.NET_INCOME_COMMON),
    ],
)
def test_distinct_income_statement_concepts_are_not_comparable(canonical_metric, validator_metric):
    result = actual_pair(canonical_metric, validator_changes={"metric": validator_metric})
    assert result.agreement_level is SourceAgreementLevel.NOT_COMPARABLE
    assert "metric" in result.semantic_mismatches


def test_generic_eps_and_diluted_eps_are_not_comparable():
    _, _, result = forward_pair(
        metric=MetricId.EPS,
        canonical_changes={"unit": MetricUnit.CURRENCY_PER_SHARE},
        validator_changes={"metric": MetricId.EPS_DILUTED, "unit": MetricUnit.CURRENCY_PER_SHARE},
        validator_provider="alpha_vantage",
    )
    assert result.agreement_level is SourceAgreementLevel.NOT_COMPARABLE
    assert "metric" in result.semantic_mismatches


@pytest.mark.parametrize("validator_metric", [MetricId.FCFF, MetricId.FCFE])
def test_provider_defined_fcf_is_not_fcff_or_fcfe_without_verified_evidence(validator_metric):
    _, _, result = forward_pair(
        metric=MetricId.PROVIDER_DEFINED_FCF,
        validator_changes={"metric": validator_metric},
    )
    assert result.agreement_level is SourceAgreementLevel.NOT_COMPARABLE
    assert "metric" in result.semantic_mismatches


@pytest.mark.parametrize("metric", [MetricId.OPERATING_CASH_FLOW, MetricId.CAPITAL_EXPENDITURE])
def test_aligned_canonical_cash_flow_components_compare(metric):
    result = actual_pair(metric)
    assert result.status is ReconciliationStatus.COMPARED
    assert result.agreement_level is SourceAgreementLevel.COMPARABLE_UNSCORED


@pytest.mark.parametrize("validator_provider", ["finnhub", "alpha_vantage"])
def test_fmp_forward_revenue_reconciles_independently_with_approved_validators(validator_provider):
    _, _, result = forward_pair(100, 102, validator_provider=validator_provider)
    assert result.status is ReconciliationStatus.COMPARED
    assert result.canonical_provider == "fmp"
    assert result.validator_provider == validator_provider


def test_aligned_quarterly_estimates_do_not_create_unapproved_canonical_consensus():
    _, _, result = forward_pair(
        canonical_changes={"frequency": Frequency.QUARTERLY},
        validator_changes={"frequency": Frequency.QUARTERLY},
        validator_provider="alpha_vantage",
    )
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert "canonical_stream_semantics" in result.semantic_mismatches


def test_alpha_is_not_an_approved_ebit_validator():
    _, _, result = forward_pair(metric=MetricId.EBIT, validator_provider="alpha_vantage")
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert "validator_metric_policy" in result.semantic_mismatches


def test_forward_fcff_fcfe_has_no_canonical_provider_policy_yet():
    canonical = observation("fmp", "FMP-SYN", metric=MetricId.FCFF)
    validator = observation("finnhub", "FH-SYN", metric=MetricId.FCFF)
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.CASH_FLOW_ESTIMATE),
        source("finnhub", "FH-SYN", (validator,), ReconciliationDatasetType.CASH_FLOW_ESTIMATE),
        stream=ReconciliationStream.FORWARD_CASH_FLOW,
        comparison_as_of=COMPARISON,
    )
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert "canonical_provider_not_established" in result.semantic_mismatches


def test_fmp_generic_eps_and_alpha_generic_eps_compare():
    _, _, result = forward_pair(
        2.0, 2.1, metric=MetricId.EPS, validator_provider="alpha_vantage",
        canonical_changes={"unit": MetricUnit.CURRENCY_PER_SHARE},
        validator_changes={"unit": MetricUnit.CURRENCY_PER_SHARE},
    )
    assert result.status is ReconciliationStatus.COMPARED
    assert result.agreement_level is SourceAgreementLevel.WARNING


def test_historical_estimate_row_cannot_validate_current_forward_period():
    _, _, result = forward_pair(validator_changes={
        "period_start": date(2025, 1, 1), "period_end": date(2025, 12, 31), "fiscal_year": 2025,
    })
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert "period_end" in result.semantic_mismatches


def test_future_validator_snapshot_is_excluded():
    canonical = observation("fmp", "FMP-SYN")
    future = observation("finnhub", "FH-SYN", as_of=COMPARISON + timedelta(days=1))
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source("finnhub", "FH-SYN", (future,), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    assert result.status is ReconciliationStatus.NO_VALIDATOR
    assert result.validator_observation_id is None


def test_latest_eligible_validator_snapshot_is_selected_before_future_data():
    canonical = observation("fmp", "FMP-SYN", as_of=NOW - timedelta(days=3))
    old = observation("finnhub", "FH-SYN", 99, as_of=NOW - timedelta(days=2))
    latest = observation("finnhub", "FH-SYN", 101, as_of=NOW)
    future = observation("finnhub", "FH-SYN", 130, as_of=COMPARISON + timedelta(days=1))
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source("finnhub", "FH-SYN", (old, latest, future), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    assert result.validator_observation_id == latest.observation_id
    assert result.validator_value == 101


def test_retrieval_timestamp_mismatch_alone_does_not_prevent_comparison():
    _, _, result = forward_pair(
        canonical_changes={"retrieved": NOW + timedelta(hours=1)},
        validator_changes={"retrieved": NOW + timedelta(days=10)},
    )
    assert result.status is ReconciliationStatus.COMPARED
    assert result.canonical_retrieved_at != result.validator_retrieved_at


def test_explicit_as_of_gap_policy_marks_stale_comparison_not_comparable():
    canonical = observation("fmp", "FMP-SYN", as_of=NOW - timedelta(days=10))
    validator = observation("finnhub", "FH-SYN", as_of=NOW)
    policy = replace(DEFAULT_RECONCILIATION_POLICY, maximum_as_of_gap=timedelta(days=2))
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source("finnhub", "FH-SYN", (validator,), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON, policy=policy,
    )
    assert result.status is ReconciliationStatus.NOT_COMPARABLE
    assert "as_of_gap_exceeds_policy" in result.semantic_mismatches
    assert result.issues[0].severity is IssueSeverity.WARNING


def test_exact_equal_values_are_confirmed():
    _, _, result = forward_pair(100, 100)
    assert result.agreement_level is SourceAgreementLevel.CONFIRMED
    assert result.absolute_difference == 0
    assert result.relative_difference == 0


@pytest.mark.parametrize(
    "validator_value,expected",
    [
        (100.99, SourceAgreementLevel.CONFIRMED),
        (101.0, SourceAgreementLevel.WARNING),
        (103.0, SourceAgreementLevel.WARNING),
        (103.01, SourceAgreementLevel.CONFLICT),
    ],
)
def test_current_price_threshold_boundaries(validator_value, expected):
    result = price_result(validator_value)
    assert result.agreement_level is expected
    assert result.tolerance_policy_id == "current-price-v1"


@pytest.mark.parametrize(
    "validator_value,expected",
    [
        (102.99, SourceAgreementLevel.CONFIRMED),
        (103.0, SourceAgreementLevel.WARNING),
        (110.0, SourceAgreementLevel.WARNING),
        (110.01, SourceAgreementLevel.CONFLICT),
    ],
)
def test_forward_revenue_threshold_boundaries(validator_value, expected):
    _, _, result = forward_pair(100, validator_value)
    assert result.agreement_level is expected
    assert result.tolerance_policy_id == "forward-revenue-v1"


@pytest.mark.parametrize(
    "validator_value,expected",
    [
        (104.99, SourceAgreementLevel.CONFIRMED),
        (105.0, SourceAgreementLevel.WARNING),
        (115.0, SourceAgreementLevel.WARNING),
        (115.01, SourceAgreementLevel.CONFLICT),
    ],
)
def test_forward_eps_threshold_boundaries(validator_value, expected):
    _, _, result = forward_pair(
        100, validator_value, metric=MetricId.EPS, validator_provider="alpha_vantage",
        canonical_changes={"unit": MetricUnit.CURRENCY_PER_SHARE},
        validator_changes={"unit": MetricUnit.CURRENCY_PER_SHARE},
    )
    assert result.agreement_level is expected
    assert result.tolerance_policy_id == "forward-eps-v1"


def test_zero_base_cases_are_explicit_and_never_divide_by_zero():
    _, _, exact = forward_pair(0, 0)
    _, _, nonzero = forward_pair(0, 1)
    assert exact.agreement_level is SourceAgreementLevel.CONFIRMED
    assert exact.relative_difference == 0
    assert nonzero.agreement_level is SourceAgreementLevel.CONFLICT
    assert nonzero.absolute_difference == 1
    assert nonzero.relative_difference is None
    assert nonzero.tolerance_policy_id == "zero-base-nonzero-v1"


def test_absolute_and_relative_differences_use_canonical_denominator():
    _, _, result = forward_pair(200, 230)
    assert result.absolute_difference == 30
    assert result.relative_difference == 0.15


def test_comparable_metric_without_approved_tolerance_is_unscored():
    result = actual_pair(MetricId.OPERATING_CASH_FLOW)
    assert result.status is ReconciliationStatus.COMPARED
    assert result.agreement_level is SourceAgreementLevel.COMPARABLE_UNSCORED
    assert result.tolerance_policy_id is None


def test_warning_and_conflict_create_structured_issues_without_replacement():
    _, _, warning = forward_pair(100, 105)
    canonical, _, conflict = forward_pair(100, 130)
    assert warning.issues[0].severity is IssueSeverity.WARNING
    assert conflict.issues[0].severity is IssueSeverity.ERROR
    assert conflict.agreement_level is SourceAgreementLevel.CONFLICT
    assert conflict.canonical_value == canonical.value == 100
    assert "Preserve the canonical observation" in conflict.issues[0].action
    assert canonical.period_end.isoformat() in conflict.issues[0].reason


def test_missing_validator_is_not_zero_or_disagreement():
    canonical = observation("fmp", "FMP-SYN", 100)
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source("finnhub", "FH-SYN", (), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    assert result.status is ReconciliationStatus.NO_VALIDATOR
    assert result.agreement_level is SourceAgreementLevel.NO_VALIDATOR
    assert result.validator_value is None and result.absolute_difference is None
    assert result.issues == ()


@pytest.mark.parametrize("status", [CapabilityStatus.LOCKED, CapabilityStatus.ERROR])
def test_validator_capability_failure_is_not_financial_conflict(status):
    canonical = observation("fmp", "FMP-SYN", 100)
    capability = CapabilityResult(
        provider="finnhub", capability="annual_revenue_estimates", status=status,
        checked_at=NOW, reason="synthetic provider availability state",
    )
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source(
            "finnhub", "FH-SYN", (), ReconciliationDatasetType.VALIDATOR_ESTIMATE,
            capability=capability,
        ),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    assert result.status is ReconciliationStatus.VALIDATOR_UNAVAILABLE
    assert result.agreement_level is SourceAgreementLevel.NO_VALIDATOR
    assert result.issues == ()


def test_two_validators_create_independent_records_without_numeric_averaging():
    canonical = observation("fmp", "FMP-SYN", 100)
    finn = observation("finnhub", "FH-SYN", 102)
    alpha = observation("alpha_vantage", "AV-SYN", 99)
    canonical_source = source(
        "fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS,
    )
    audit = reconcile_many(
        canonical_source,
        (
            source("finnhub", "FH-SYN", (finn,), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
            source("alpha_vantage", "AV-SYN", (alpha,), ReconciliationDatasetType.VALIDATOR_ESTIMATE),
        ),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    assert len(audit.comparisons) == 2
    assert {item.validator_value for item in audit.comparisons} == {99, 102}
    assert audit.canonical_value == canonical.value == 100
    assert audit.validator_count == 2 and audit.confirmed_count == 2
    assert not hasattr(audit, "averaged_value")


def test_canonical_observation_remains_frozen_and_unchanged_after_conflict():
    canonical, _, result = forward_pair(100, 130)
    assert result.canonical_value == canonical.value == 100
    with pytest.raises(FrozenInstanceError):
        canonical.value = 115


def test_fiscal_standardized_and_sec_reported_context_remains_visible():
    result = actual_pair(MetricId.REVENUE)
    assert result.status is ReconciliationStatus.COMPARED
    assert result.canonical_dataset_type is ReconciliationDatasetType.STANDARDIZED_ACTUAL
    assert result.validator_dataset_type is ReconciliationDatasetType.REPORTED_ACTUAL
    assert "standardized" in result.canonical_reason.lower()


def test_audit_output_excludes_raw_provider_fields_and_secret_capability_reason():
    canonical = observation("fmp", "FMP-SYN", source_metric="revenueAvg")
    capability = CapabilityResult(
        provider="finnhub", capability="annual_revenue_estimates",
        status=CapabilityStatus.ERROR, checked_at=NOW,
        reason="api_key=SYNTHETIC_SECRET should never be copied",
    )
    result = reconcile_sources(
        source("fmp", "FMP-SYN", (canonical,), ReconciliationDatasetType.FORWARD_CONSENSUS),
        source(
            "finnhub", "FH-SYN", (), ReconciliationDatasetType.VALIDATOR_ESTIMATE,
            capability=capability,
        ),
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=COMPARISON,
    )
    rendered = repr(result)
    assert "revenueAvg" not in rendered
    assert "SYNTHETIC_SECRET" not in rendered
    assert result.validator_capability_reason == "validator capability is error"
