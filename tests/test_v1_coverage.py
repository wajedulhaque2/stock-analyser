from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stock_analyser.domain import (
    AnalystCoverageBand,
    CapabilityResult,
    CapabilityStatus,
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    CoverageAssessment,
    CoverageDimension,
    CoverageLevel,
    DataAvailability,
    DataIssue,
    DefinitionVerificationStatus,
    DimensionStatus,
    EstimateCase,
    EvidenceConfidence,
    Frequency,
    IssueSeverity,
    MacroFrequency,
    MacroMetric,
    MacroObservation,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    ProviderSymbol,
    ReconciliationDatasetType,
    ReconciliationStream,
    ValuationFamily,
    ValuationReadiness,
    stable_observation_id,
)
from stock_analyser.services import (
    CoverageInputs,
    DEFAULT_COVERAGE_POLICY,
    HistoricalCoveragePolicy,
    ObservationSource,
    RiskFreeRateResult,
    assess_coverage,
    reconcile_sources,
)


ANALYSIS = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
SNAPSHOT = ANALYSIS - timedelta(days=1)
CORE_HISTORY = (
    MetricId.REVENUE,
    MetricId.OPERATING_INCOME,
    MetricId.NET_INCOME,
    MetricId.OPERATING_CASH_FLOW,
    MetricId.CAPITAL_EXPENDITURE,
)


def identity(currency: str = "USD") -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol="SYN",
        security_id="security-synthetic",
        issuer_id="issuer-synthetic",
        company_name="Synthetic plc",
        issuer_domicile="US",
        listing_country="US",
        exchange="Synthetic Exchange",
        sector="Industrials",
        industry="Engineering",
        security_type="Ordinary share",
        reporting_currency=currency,
        quote_currency=currency,
        quote_unit=currency,
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(
            ProviderSymbol("yahoo", "SYN"),
            ProviderSymbol("fiscal", "SYN"),
            ProviderSymbol("fmp", "SYN"),
            ProviderSymbol("finnhub", "SYN"),
        ),
    )


def observation(
    metric: MetricId,
    *,
    provider: str,
    year: int | None = None,
    value: float = 100.0,
    actual: bool = False,
    frequency: Frequency = Frequency.ANNUAL,
    analyst_count: int | None = None,
    as_of: datetime = SNAPSHOT,
    currency: str = "USD",
) -> MetricObservation:
    if frequency is Frequency.POINT_IN_TIME:
        period_start = None
        period_end = as_of.date()
        fiscal_year = None
    else:
        fiscal_year = year if year is not None else 2027
        period_start = date(fiscal_year, 1, 1)
        period_end = date(fiscal_year, 12, 31)
    observation_type = ObservationType.ACTUAL if actual else ObservationType.ESTIMATE
    estimate_case = EstimateCase.NOT_APPLICABLE if actual else EstimateCase.AVERAGE
    unit = (
        MetricUnit.CURRENCY_PER_SHARE
        if metric in {MetricId.SHARE_PRICE, MetricId.EPS}
        else MetricUnit.SHARES
        if metric in {MetricId.SHARES_BASIC, MetricId.SHARES_DILUTED, MetricId.SHARES_OUTSTANDING}
        else MetricUnit.CURRENCY
    )
    observation_currency = None if unit is MetricUnit.SHARES else currency
    retrieved = as_of + timedelta(hours=1)
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset="synthetic_coverage_fixture",
        provider_symbol="SYN",
        retrieved_at=retrieved,
        as_of_at=as_of,
        source_metric=metric.value,
    )
    identifier = stable_observation_id(
        metric_id=metric.value,
        provider=provider,
        provider_symbol="SYN",
        frequency=frequency.value,
        observation_type=observation_type.value,
        estimate_case=estimate_case.value,
        period_start=period_start.isoformat() if period_start else "",
        period_end=period_end.isoformat(),
        as_of_at=as_of.isoformat(),
    )
    return MetricObservation(
        observation_id=identifier,
        metric_id=metric,
        value=value,
        unit=unit,
        frequency=frequency,
        observation_type=observation_type,
        estimate_case=estimate_case,
        retrieved_at=retrieved,
        as_of_at=as_of,
        provenance=provenance,
        currency=observation_currency,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=1 if frequency is Frequency.QUARTERLY else None,
        analyst_count=analyst_count,
    )


def annual_history(years: int, *, frequency: Frequency = Frequency.ANNUAL):
    start = 2025 - years + 1
    return tuple(
        observation(metric, provider="fiscal", year=year, actual=True, frequency=frequency)
        for year in range(start, 2026)
        for metric in CORE_HISTORY
    )


def forward_consensus(
    years: int,
    *,
    revenue_counts: tuple[int | None, ...] | None = None,
    eps_counts: tuple[int | None, ...] | None = None,
    revenue_only: bool = False,
    as_of: datetime = SNAPSHOT,
    currency: str = "USD",
):
    revenue_counts = revenue_counts or (None,) * years
    eps_counts = eps_counts or (None,) * years
    items = []
    for index in range(years):
        year = 2027 + index
        items.append(
            observation(
                MetricId.REVENUE,
                provider="fmp",
                year=year,
                analyst_count=revenue_counts[index],
                as_of=as_of,
                currency=currency,
            )
        )
        if not revenue_only:
            items.append(
                observation(
                    MetricId.EPS,
                    provider="fmp",
                    year=year,
                    value=4,
                    analyst_count=eps_counts[index],
                    as_of=as_of,
                    currency=currency,
                )
            )
    return tuple(items)


def market_observations(*, present: bool = True):
    if not present:
        return ()
    return (
        observation(MetricId.SHARE_PRICE, provider="yahoo", actual=True, frequency=Frequency.POINT_IN_TIME),
        observation(MetricId.MARKET_CAP, provider="yahoo", actual=True, frequency=Frequency.POINT_IN_TIME),
    )


def assessment(
    *,
    history_years: int = 5,
    forward_years: int = 3,
    price: bool = True,
    revenue_counts: tuple[int | None, ...] | None = None,
    eps_counts: tuple[int | None, ...] | None = None,
    revenue_only: bool = False,
    reconciliations=(),
    capabilities=(),
    cash=(),
    definitions=(),
    risk_free_rate=None,
    issues=(),
    company=None,
    policy=DEFAULT_COVERAGE_POLICY,
    extra=(),
) -> CoverageAssessment:
    canonical = (
        *market_observations(present=price),
        *annual_history(history_years),
        *forward_consensus(
            forward_years,
            revenue_counts=revenue_counts,
            eps_counts=eps_counts,
            revenue_only=revenue_only,
            currency=(company or identity()).reporting_currency,
        ),
        *extra,
    )
    return assess_coverage(
        CoverageInputs(
            identity=company or identity(),
            canonical_observations=canonical,
            cash_flow_observations=tuple(cash),
            reconciliations=tuple(reconciliations),
            capabilities=tuple(capabilities),
            cash_flow_definitions=tuple(definitions),
            risk_free_rate=risk_free_rate,
            issues=tuple(issues),
        ),
        analysis_as_of=ANALYSIS,
        policy=policy,
    )


def dimension(result: CoverageAssessment, name: CoverageDimension):
    return next(item for item in result.dimension_results if item.dimension is name)


def readiness(result: CoverageAssessment, family: ValuationFamily):
    return next(item for item in result.valuation_readiness_results if item.family is family)


@pytest.mark.parametrize(
    "years,status,complete,label",
    [
        (5, DimensionStatus.PASS, 5, "strong"),
        (4, DimensionStatus.PARTIAL, 4, "medium"),
        (3, DimensionStatus.PARTIAL, 3, "medium"),
        (2, DimensionStatus.PARTIAL, 2, "limited"),
        (1, DimensionStatus.PARTIAL, 1, "limited"),
        (0, DimensionStatus.NOT_AVAILABLE, 0, "No canonical"),
    ],
)
def test_historical_annual_thresholds_are_explicit(years, status, complete, label):
    result = dimension(assessment(history_years=years), CoverageDimension.HISTORICAL_FINANCIALS)
    assert result.status is status
    assert next(item.value for item in result.counts if item.name == "complete_annual_periods") == complete
    assert label.lower() in result.reason.lower()


def test_quarterly_observations_cannot_manufacture_annual_history():
    quarterly = annual_history(5, frequency=Frequency.QUARTERLY)
    result = assessment(history_years=0, extra=quarterly)
    history = dimension(result, CoverageDimension.HISTORICAL_FINANCIALS)
    assert history.status is DimensionStatus.NOT_AVAILABLE
    assert not history.supporting_observation_ids


@pytest.mark.parametrize(
    "years,status,complete",
    [
        (3, DimensionStatus.PASS, 3),
        (2, DimensionStatus.PARTIAL, 2),
        (1, DimensionStatus.PARTIAL, 1),
        (0, DimensionStatus.NOT_AVAILABLE, 0),
    ],
)
def test_forward_horizon_thresholds_are_explicit(years, status, complete):
    result = dimension(assessment(forward_years=years), CoverageDimension.FORWARD_CONSENSUS)
    assert result.status is status
    assert next(item.value for item in result.counts if item.name == "core_complete_periods") == complete


def test_historical_estimate_rows_do_not_count_as_forward():
    old_estimate = observation(MetricId.REVENUE, provider="fmp", year=2025)
    result = assessment(forward_years=0, extra=(old_estimate,))
    forward = dimension(result, CoverageDimension.FORWARD_CONSENSUS)
    assert forward.status is DimensionStatus.NOT_AVAILABLE
    assert old_estimate.observation_id not in forward.supporting_observation_ids


def test_revenue_plus_profitability_supports_strong_but_revenue_only_is_partial():
    complete = dimension(assessment(forward_years=3), CoverageDimension.FORWARD_CONSENSUS)
    revenue_only = dimension(
        assessment(forward_years=3, revenue_only=True), CoverageDimension.FORWARD_CONSENSUS
    )
    assert complete.status is DimensionStatus.PASS
    assert revenue_only.status is DimensionStatus.PARTIAL
    assert next(item.value for item in revenue_only.counts if item.name == "revenue_only_periods") == 3


@pytest.mark.parametrize(
    "count,band",
    [
        (20, AnalystCoverageBand.STRONG),
        (19, AnalystCoverageBand.MEDIUM),
        (8, AnalystCoverageBand.MEDIUM),
        (7, AnalystCoverageBand.LIMITED),
        (3, AnalystCoverageBand.LIMITED),
        (2, AnalystCoverageBand.WEAK),
        (None, AnalystCoverageBand.UNKNOWN),
    ],
)
def test_analyst_count_bands_preserve_unknown(count, band):
    result = assessment(forward_years=1, revenue_counts=(count,), eps_counts=(count,))
    horizon = dimension(result, CoverageDimension.ANALYST_COVERAGE).analyst_horizons[0]
    assert horizon.revenue_analyst_count is count
    assert horizon.eps_analyst_count is count
    assert horizon.overall_band is band


def test_per_horizon_counts_preserved_and_distant_weakness_does_not_erase_fy1():
    counts = (35, 32, 20, 9, 4)
    result = assessment(forward_years=5, revenue_counts=counts, eps_counts=counts)
    analyst = dimension(result, CoverageDimension.ANALYST_COVERAGE)
    assert tuple(item.revenue_analyst_count for item in analyst.analyst_horizons) == counts
    assert analyst.analyst_horizons[0].overall_band is AnalystCoverageBand.STRONG
    assert analyst.analyst_horizons[-1].overall_band is AnalystCoverageBand.LIMITED
    assert analyst.status is DimensionStatus.PASS


def source(provider: str, item: MetricObservation, *, company=None, capability=None):
    return ObservationSource(
        identity=company or identity(),
        provider=provider,
        dataset_type=(
            ReconciliationDatasetType.FORWARD_CONSENSUS
            if provider == "fmp"
            else ReconciliationDatasetType.VALIDATOR_ESTIMATE
        ),
        observations=(item,) if item is not None else (),
        capability=capability,
    )


def reconciliation(
    difference: float = 0,
    *,
    year: int = 2027,
    metric: MetricId = MetricId.REVENUE,
    validator_company=None,
    validator_capability=None,
):
    canonical = observation(metric, provider="fmp", year=year, value=100)
    validator = observation(metric, provider="finnhub", year=year, value=100 + difference)
    validator_source = source(
        "finnhub", validator, company=validator_company, capability=validator_capability
    )
    if validator_capability is not None and validator_capability.status is not CapabilityStatus.AVAILABLE:
        validator_source = ObservationSource(
            identity=validator_company or identity(),
            provider="finnhub",
            dataset_type=ReconciliationDatasetType.VALIDATOR_ESTIMATE,
            observations=(),
            capability=validator_capability,
        )
    return reconcile_sources(
        source("fmp", canonical),
        validator_source,
        stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
        comparison_as_of=ANALYSIS,
    )


@pytest.mark.parametrize(
    "difference,status,confidence",
    [
        (0, DimensionStatus.PASS, EvidenceConfidence.HIGH),
        (6, DimensionStatus.PARTIAL, EvidenceConfidence.MEDIUM),
        (20, DimensionStatus.FAIL, EvidenceConfidence.LOW),
    ],
)
def test_source_agreement_contributes_without_replacing_canonical_data(difference, status, confidence):
    recon = reconciliation(difference, year=2029)
    result = assessment(
        revenue_counts=(25, 25, 25),
        eps_counts=(25, 25, 25),
        reconciliations=(recon,),
    )
    agreement = dimension(result, CoverageDimension.SOURCE_AGREEMENT)
    assert agreement.status is status
    assert result.evidence_confidence is confidence
    assert agreement.supporting_reconciliation_ids[0].startswith("recon-")


def test_no_validator_and_locked_validator_are_not_conflicts():
    locked = CapabilityResult(
        provider="finnhub",
        capability="annual_estimates",
        status=CapabilityStatus.LOCKED,
        checked_at=SNAPSHOT,
        reason="Synthetic entitlement boundary.",
    )
    no_validator = reconciliation(validator_capability=locked)
    result = assessment(reconciliations=(no_validator,), capabilities=(locked,))
    agreement = dimension(result, CoverageDimension.SOURCE_AGREEMENT)
    assert agreement.status is DimensionStatus.NOT_AVAILABLE
    assert next(item.value for item in agreement.counts if item.name == "conflicts") == 0
    assert next(item.value for item in agreement.counts if item.name == "no_validator") >= 1
    assert not result.critical_issues


def test_distant_conflict_does_not_make_overall_insufficient_but_near_conflict_is_critical():
    distant = assessment(
        revenue_counts=(25, 25, 25),
        eps_counts=(25, 25, 25),
        reconciliations=(reconciliation(20, year=2029),),
    )
    near = assessment(
        revenue_counts=(25, 25, 25),
        eps_counts=(25, 25, 25),
        reconciliations=(reconciliation(20, year=2027),),
    )
    assert distant.overall_coverage is not CoverageLevel.INSUFFICIENT
    assert not distant.critical_issues
    assert near.evidence_confidence is EvidenceConfidence.LOW
    assert any("near-term" in issue.reason for issue in near.critical_issues)


def cash_observation(metric: MetricId, year: int = 2027):
    return observation(metric, provider="finnhub", year=year)


def definition(item: MetricObservation, kind: CashFlowDefinition, *, verified=True):
    return CashFlowDefinitionEvidence(
        provider="finnhub",
        provider_metric=item.metric_id.value,
        endpoint_or_dataset="synthetic_cash_flow",
        definition=kind,
        verification_status=(
            DefinitionVerificationStatus.VERIFIED
            if verified
            else DefinitionVerificationStatus.UNVERIFIED
        ),
        definition_reference="synthetic-methodology-v1" if verified else None,
        verified_at=SNAPSHOT if verified else None,
        observation_ids=(item.observation_id,),
    )


def test_provider_fcf_and_ocf_capex_without_verified_definition_are_incomplete():
    items = (
        cash_observation(MetricId.PROVIDER_DEFINED_FCF),
        cash_observation(MetricId.OPERATING_CASH_FLOW),
        cash_observation(MetricId.CAPITAL_EXPENDITURE),
    )
    result = assessment(cash=items, definitions=(definition(items[0], CashFlowDefinition.UNKNOWN, verified=False),))
    cash = dimension(result, CoverageDimension.CASH_FLOW_EVIDENCE)
    assert cash.status is DimensionStatus.PARTIAL
    assert readiness(result, ValuationFamily.CASH_FLOW).readiness is ValuationReadiness.NOT_READY


@pytest.mark.parametrize(
    "metric,kind,count_name",
    [
        (MetricId.FCFF, CashFlowDefinition.FCFF, "verified_fcff"),
        (MetricId.FCFE, CashFlowDefinition.FCFE, "verified_fcfe"),
    ],
)
def test_verified_fcff_and_fcfe_are_represented_separately(metric, kind, count_name):
    item = cash_observation(metric)
    result = assessment(cash=(item,), definitions=(definition(item, kind),))
    cash = dimension(result, CoverageDimension.CASH_FLOW_EVIDENCE)
    assert cash.status is DimensionStatus.PASS
    assert next(entry.value for entry in cash.counts if entry.name == count_name) == 1


def test_future_cash_flow_definition_verification_is_excluded_by_snapshot():
    item = cash_observation(MetricId.FCFF)
    future_definition = replace(
        definition(item, CashFlowDefinition.FCFF),
        verified_at=ANALYSIS + timedelta(days=1),
    )
    result = assessment(cash=(item,), definitions=(future_definition,))
    assert dimension(result, CoverageDimension.CASH_FLOW_EVIDENCE).status is DimensionStatus.PARTIAL
    assert readiness(result, ValuationFamily.CASH_FLOW).readiness is ValuationReadiness.NOT_READY


def usd_macro(observation_date=date(2026, 8, 25)):
    provenance = Provenance(
        provider="fred",
        endpoint_or_dataset="DGS10",
        provider_symbol="DGS10",
        retrieved_at=SNAPSHOT + timedelta(hours=1),
        as_of_at=SNAPSHOT,
        source_metric="DGS10",
    )
    item = MacroObservation(
        series_id="DGS10",
        metric=MacroMetric.TREASURY_YIELD,
        value=0.04,
        unit=MetricUnit.PERCENT_DECIMAL,
        currency="USD",
        observation_date=observation_date,
        frequency=MacroFrequency.DAILY,
        as_of_at=SNAPSHOT,
        retrieved_at=SNAPSHOT + timedelta(hours=1),
        provider="fred",
        provenance=provenance,
    )
    return RiskFreeRateResult(
        currency="USD", availability=DataAvailability.AVAILABLE, observation=item
    )


def test_usd_macro_ready_and_supports_stable_observation_identifier():
    result = assessment(risk_free_rate=usd_macro())
    macro = dimension(result, CoverageDimension.MACRO_READINESS)
    assert macro.status is DimensionStatus.PASS
    assert macro.supporting_observation_ids[0].startswith("macro-")


def test_gbp_macro_fails_closed_without_usd_fallback_or_harming_own_history():
    unavailable = RiskFreeRateResult(
        currency="GBP",
        availability=DataAvailability.UNAVAILABLE,
        reason="No approved GBP risk-free-rate source configured.",
    )
    result = assessment(company=identity("GBP"), risk_free_rate=unavailable)
    macro = dimension(result, CoverageDimension.MACRO_READINESS)
    own = readiness(result, ValuationFamily.OWN_HISTORY)
    assert macro.status is DimensionStatus.NOT_AVAILABLE
    assert "GBP" in macro.reason
    assert own.readiness is ValuationReadiness.PARTIAL
    assert not macro.supporting_observation_ids


def test_readiness_states_do_not_claim_unimplemented_methods_are_ready():
    item = cash_observation(MetricId.FCFF)
    result = assessment(
        risk_free_rate=usd_macro(), cash=(item,), definitions=(definition(item, CashFlowDefinition.FCFF),)
    )
    assert readiness(result, ValuationFamily.OWN_HISTORY).readiness is ValuationReadiness.PARTIAL
    assert readiness(result, ValuationFamily.PEER).readiness is ValuationReadiness.NOT_READY
    assert readiness(result, ValuationFamily.CASH_FLOW).readiness is ValuationReadiness.PARTIAL
    assert readiness(result, ValuationFamily.REVERSE_CASH_FLOW).readiness is ValuationReadiness.NOT_READY
    assert dimension(result, CoverageDimension.PEER_READINESS).status is DimensionStatus.NOT_EVALUATED
    assert "ERP" in readiness(result, ValuationFamily.CASH_FLOW).missing_requirements


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (
            {
                "revenue_counts": (25, 25, 25),
                "eps_counts": (25, 25, 25),
                "reconciliations": (reconciliation(0, year=2027),),
            },
            CoverageLevel.HIGH,
        ),
        ({"history_years": 4, "forward_years": 2}, CoverageLevel.MEDIUM),
        ({"history_years": 1, "forward_years": 1}, CoverageLevel.LIMITED),
        ({"price": False}, CoverageLevel.INSUFFICIENT),
        ({"forward_years": 0}, CoverageLevel.INSUFFICIENT),
    ],
)
def test_overall_coverage_is_rule_based(kwargs, expected):
    assert assessment(**kwargs).overall_coverage is expected


def test_missing_current_price_and_forward_consensus_are_critical():
    result = assessment(price=False, forward_years=0)
    reasons = " ".join(item.reason.lower() for item in result.critical_issues)
    assert "current price" in reasons
    assert "forward consensus" in reasons


def test_identity_mismatch_is_critical():
    other = replace(identity(), security_id="other-security")
    result = assessment(reconciliations=(reconciliation(0, validator_company=other),))
    assert any("identity semantics" in item.reason for item in result.critical_issues)
    assert result.overall_coverage is CoverageLevel.INSUFFICIENT


def test_supplied_blocking_issue_is_critical():
    issue = DataIssue(
        severity=IssueSeverity.BLOCKING,
        metric="estimate_range",
        provider="canonical_validation",
        reason="Synthetic estimate-range inversion.",
    )
    result = assessment(issues=(issue,))
    assert issue in result.critical_issues
    assert result.overall_coverage is CoverageLevel.INSUFFICIENT


def test_explicit_as_of_excludes_future_snapshot_observations():
    future = forward_consensus(1, as_of=ANALYSIS + timedelta(days=1))
    result = assessment(forward_years=0, extra=future)
    forward = dimension(result, CoverageDimension.FORWARD_CONSENSUS)
    assert result.analysis_as_of == ANALYSIS
    assert forward.status is DimensionStatus.NOT_AVAILABLE
    assert not set(item.observation_id for item in future) & set(result.supporting_observation_ids)


def test_assessment_does_not_mutate_observations_reconciliations_or_capabilities():
    canonical = (*market_observations(), *annual_history(5), *forward_consensus(3))
    recon = reconciliation(6, year=2029)
    capability = CapabilityResult(
        provider="finnhub",
        capability="annual_estimates",
        status=CapabilityStatus.AVAILABLE,
        checked_at=SNAPSHOT,
    )
    inputs = CoverageInputs(
        identity=identity(),
        canonical_observations=canonical,
        reconciliations=(recon,),
        capabilities=(capability,),
    )
    before = (inputs.canonical_observations, inputs.reconciliations, inputs.capabilities)
    assess_coverage(inputs, analysis_as_of=ANALYSIS)
    assert before == (inputs.canonical_observations, inputs.reconciliations, inputs.capabilities)
    with pytest.raises(FrozenInstanceError):
        inputs.canonical_observations[0].value = 1


def test_output_contains_no_monetary_result_or_investment_stance_fields():
    result = assessment()
    names = {item.name for item in fields(result)}
    assert not names & {"low", "central", "high", "fair_value", "price_target", "stance"}
    text = " ".join(
        [*result.reasons]
        + [item.reason for item in result.dimension_results]
        + [item.reason for item in result.valuation_readiness_results]
    ).upper()
    assert not {"BUY", "SELL", "ATTRACTIVE", "OVERVALUED", "UNDERVALUED"} & set(text.split())


def test_custom_policy_changes_history_classification_without_ticker_rules():
    custom = replace(
        DEFAULT_COVERAGE_POLICY,
        policy_id="synthetic-custom-history",
        historical=HistoricalCoveragePolicy(
            strong_complete_periods=3,
            medium_complete_periods=2,
            limited_complete_periods=1,
        ),
    )
    default_result = dimension(assessment(history_years=3), CoverageDimension.HISTORICAL_FINANCIALS)
    custom_result = dimension(
        assessment(history_years=3, policy=custom), CoverageDimension.HISTORICAL_FINANCIALS
    )
    assert default_result.status is DimensionStatus.PARTIAL
    assert custom_result.status is DimensionStatus.PASS


def test_coverage_module_has_no_provider_calls_ui_or_calculation_dependencies():
    source_text = Path("src/stock_analyser/services/coverage.py").read_text(encoding="utf-8")
    forbidden_imports = ("requests", "httpx", "streamlit", "providers.", "adapters.", "valuation.")
    assert not any(value in source_text for value in forbidden_imports)
    assert "today()" not in source_text and "now()" not in source_text
    assert "canonical_symbol ==" not in source_text
