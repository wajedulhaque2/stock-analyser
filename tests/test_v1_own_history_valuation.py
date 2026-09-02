from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timezone
import inspect
import math

import pytest

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CompanyIdentity,
    DataAvailability,
    DenominatorCompatibilityStatus,
    DenominatorSemanticEvidence,
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    HistoricalMultipleType,
    HistoricalStatistic,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    HistoricalWindow,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    OwnHistoryMethodStatus,
    OwnHistoryValuationPoint,
    OwnHistoryValuationResult,
    Provenance,
    ProviderSymbol,
    ShareCountBasis,
    ShareCountSemantics,
    ValuationBasis,
    ValuationMethodStatus,
    stable_historical_valuation_id,
)
from stock_analyser.services import (
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    assess_own_history_method_readiness,
    build_capital_structure_snapshot,
    build_direct_enterprise_equity_bridge,
    build_forward_consensus,
    build_historical_distribution,
    calculate_own_history_valuation,
)


ANALYSIS_AS_OF = datetime(2026, 8, 27, 16, tzinfo=timezone.utc)
PERMISSIVE = HistoricalDistributionPolicy(
    policy_id="synthetic-7d-distribution",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=tuple(HistoricalWindowMinimum(window, 1, 0.01) for window in HistoricalWindow),
)
SEMANTICS = {
    HistoricalMultipleType.P_E: (
        ValuationBasis.EQUITY, HistoricalValuationDenominator.DILUTED_EPS,
    ),
    HistoricalMultipleType.EV_EBITDA: (
        ValuationBasis.ENTERPRISE, HistoricalValuationDenominator.EBITDA,
    ),
    HistoricalMultipleType.EV_EBIT: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
    ),
}
FORWARD_METRIC = {
    HistoricalMultipleType.P_E: MetricId.EPS,
    HistoricalMultipleType.EV_EBITDA: MetricId.EBITDA,
    HistoricalMultipleType.EV_EBIT: MetricId.EBIT,
}
DEFAULT_DENOMINATOR = {
    HistoricalMultipleType.P_E: 5.0,
    HistoricalMultipleType.EV_EBITDA: 100.0,
    HistoricalMultipleType.EV_EBIT: 80.0,
}


def identity(*, security_type="Ordinary share", adr_ratio=None) -> CompanyIdentity:
    is_adr = "ADR" in security_type
    return CompanyIdentity(
        canonical_symbol="SYN",
        security_id="security-syn",
        issuer_id="issuer-syn",
        company_name="Synthetic Corp",
        issuer_domicile="US",
        listing_country="US",
        exchange="Synthetic Exchange",
        sector="Industrials",
        industry="Machinery",
        security_type=security_type,
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "F-SYN"), ProviderSymbol("fmp", "FMP-SYN")),
        underlying_security_id="underlying-syn" if is_adr else None,
        adr_ratio=adr_ratio,
        identity_availability=(
            DataAvailability.PARTIAL if is_adr and adr_ratio is None else DataAvailability.AVAILABLE
        ),
    )


def provenance(provider: str, source_metric: str, *, symbol: str | None = None) -> Provenance:
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_canonical",
        provider_symbol=symbol or ("FMP-SYN" if provider == "fmp" else "F-SYN"),
        retrieved_at=ANALYSIS_AS_OF,
        as_of_at=ANALYSIS_AS_OF,
        source_metric=source_metric,
    )


def historical_observation(
    multiple_type: HistoricalMultipleType, observed: date, value: float,
) -> HistoricalValuationObservation:
    basis, denominator = SEMANTICS[multiple_type]
    source_metric = {
        HistoricalMultipleType.P_E: "ratio_price_to_earnings",
        HistoricalMultipleType.EV_EBITDA: "ratio_ev_to_ebitda",
        HistoricalMultipleType.EV_EBIT: "ratio_ev_to_ebit",
    }[multiple_type]
    return HistoricalValuationObservation(
        observation_id=stable_historical_valuation_id(
            security_id="security-syn",
            issuer_id="issuer-syn",
            provider="fiscal",
            provider_symbol="F-SYN",
            multiple_type=multiple_type,
            observation_date=observed,
            period_end=None,
            sampling=HistoricalValuationSampling.DAILY,
            source_metric=source_metric,
        ),
        security_id="security-syn",
        issuer_id="issuer-syn",
        provider="fiscal",
        provider_symbol="F-SYN",
        multiple_type=multiple_type,
        valuation_basis=basis,
        denominator=denominator,
        value=value,
        observation_date=observed,
        sampling=HistoricalValuationSampling.DAILY,
        as_of_at=ANALYSIS_AS_OF,
        retrieved_at=ANALYSIS_AS_OF,
        provenance=provenance("fiscal", source_metric),
        source_metric=source_metric,
        eligibility=HistoricalValuationEligibility.ELIGIBLE,
        quote_currency_context="USD",
        reporting_currency_context="USD",
    )


def distribution(
    multiple_type: HistoricalMultipleType,
    window: HistoricalWindow = HistoricalWindow.FIVE_YEAR,
):
    start_year = {
        HistoricalWindow.THREE_YEAR: 2023,
        HistoricalWindow.FIVE_YEAR: 2021,
        HistoricalWindow.TEN_YEAR: 2016,
    }[window]
    observations = (
        historical_observation(multiple_type, date(start_year, 8, 27), 10),
        historical_observation(multiple_type, date(2024, 8, 27), 20),
        historical_observation(multiple_type, ANALYSIS_AS_OF.date(), 30),
    )
    return build_historical_distribution(
        observations,
        security_id="security-syn",
        issuer_id="issuer-syn",
        provider="fiscal",
        multiple_type=multiple_type,
        analysis_as_of=ANALYSIS_AS_OF,
        window=window,
        policy=PERMISSIVE,
    )


def forward_observations(
    multiple_type: HistoricalMultipleType,
    *,
    selected_value: float | None = None,
) -> tuple[MetricObservation, ...]:
    metric = FORWARD_METRIC[multiple_type]
    average = DEFAULT_DENOMINATOR[multiple_type] if selected_value is None else selected_value
    unit = MetricUnit.CURRENCY_PER_SHARE if metric is MetricId.EPS else MetricUnit.CURRENCY
    values = {
        EstimateCase.LOW: average - 1.0 if average <= 0 else 1.0,
        EstimateCase.AVERAGE: average,
        EstimateCase.HIGH: average + 1.0 if average <= 0 else 999.0,
    }
    output = []
    for year in (2027, 2028):
        for case in (EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH):
            value = values[case] if year == 2027 else values[case] * 2
            prov = provenance("fmp", metric.value)
            output.append(MetricObservation(
                observation_id=f"obs:fmp:{metric.value}:{year}:{case.value}",
                metric_id=metric,
                value=value,
                unit=unit,
                frequency=Frequency.ANNUAL,
                observation_type=ObservationType.ESTIMATE,
                estimate_case=case,
                retrieved_at=ANALYSIS_AS_OF,
                as_of_at=ANALYSIS_AS_OF,
                provenance=prov,
                currency="USD",
                period_start=date(year, 1, 1),
                period_end=date(year, 12, 31),
                fiscal_year=year,
            ))
    return tuple(output)


def actual(metric: MetricId, value: float, observed: date, *, currency="USD") -> MetricObservation:
    unit = MetricUnit.SHARES if metric is MetricId.SHARES_OUTSTANDING else MetricUnit.CURRENCY
    prov = provenance("fiscal", metric.value)
    return MetricObservation(
        observation_id=f"obs:fiscal:{metric.value}:{observed.isoformat()}:{value}",
        metric_id=metric,
        value=value,
        unit=unit,
        frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=ANALYSIS_AS_OF,
        as_of_at=ANALYSIS_AS_OF,
        provenance=prov,
        currency=None if unit is MetricUnit.SHARES else currency,
        period_end=observed,
    )


def direct_bridge(
    *, adjustment=50.0, shares=10.0, company=None,
    semantics=ShareCountSemantics.ISSUER_SHARES,
):
    bridge_date = date(2026, 8, 26)
    observations = (
        actual(MetricId.ENTERPRISE_VALUE, adjustment + 100.0, bridge_date),
        actual(MetricId.MARKET_CAP, 100.0, bridge_date),
        actual(MetricId.SHARES_OUTSTANDING, shares, date(2026, 6, 30), currency=None),
    )
    return build_direct_enterprise_equity_bridge(
        company or identity(),
        observations,
        analysis_as_of=ANALYSIS_AS_OF,
        share_count_semantics=semantics,
    )


def component_bridge():
    return build_capital_structure_snapshot(
        identity(),
        (
            actual(MetricId.CASH_AND_EQUIVALENTS, 30, date(2026, 6, 30)),
            actual(MetricId.GROSS_DEBT, 80, date(2026, 6, 30)),
            actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 6, 30), currency=None),
        ),
        analysis_as_of=ANALYSIS_AS_OF,
    )


def semantic_evidence(multiple_type: HistoricalMultipleType) -> DenominatorSemanticEvidence:
    return DenominatorSemanticEvidence(
        evidence_id=f"semantic:{multiple_type.value.lower()}",
        historical_multiple_type=multiple_type,
        historical_denominator=SEMANTICS[multiple_type][1],
        forward_metric_id=FORWARD_METRIC[multiple_type],
        compatibility_status=DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT,
        historical_definition="Synthetic verified historical denominator.",
        forward_definition="Synthetic verified forward denominator.",
        reason="Synthetic explicit semantic equivalence.",
        source_references=("synthetic definition A", "synthetic definition B"),
        policy_id="synthetic-semantic-policy",
    )


def bundle(
    multiple_type=HistoricalMultipleType.EV_EBITDA,
    *,
    window=HistoricalWindow.FIVE_YEAR,
    period=ForwardPeriodSelection.FY1,
    estimate_case=EstimateCase.AVERAGE,
    denominator=None,
    bridge_method="direct",
    adjustment=50.0,
    shares=10.0,
    verify_semantics=True,
    company=None,
    share_semantics=ShareCountSemantics.ISSUER_SHARES,
):
    company = company or identity()
    dist = distribution(multiple_type, window)
    observations = forward_observations(multiple_type, selected_value=denominator)
    consensus = build_forward_consensus(observations, as_of_at=ANALYSIS_AS_OF)
    enterprise = multiple_type is not HistoricalMultipleType.P_E
    direct = direct_bridge(
        adjustment=adjustment,
        shares=shares,
        company=company,
        semantics=share_semantics,
    ) if enterprise and bridge_method == "direct" else None
    component = component_bridge() if enterprise and bridge_method == "component" else None
    evidence = (
        semantic_evidence(multiple_type)
        if verify_semantics and multiple_type in {HistoricalMultipleType.P_E, HistoricalMultipleType.EV_EBIT}
        else None
    )
    readiness = assess_own_history_method_readiness(
        company,
        (dist,),
        consensus,
        multiple_type=multiple_type,
        analysis_as_of=ANALYSIS_AS_OF,
        selected_window=window,
        selected_forward_period=period,
        estimate_case=estimate_case,
        semantic_evidence=evidence,
        capital_structure_snapshot=component,
        direct_enterprise_bridge=direct,
    )
    forward = next(
        item for item in observations if item.observation_id == readiness.forward_selection.observation_id
    )
    return readiness, dist, forward, component, direct


def calculate(items):
    readiness, dist, forward, component, direct = items
    return calculate_own_history_valuation(
        readiness,
        dist,
        forward,
        capital_structure_snapshot=component,
        direct_enterprise_bridge=direct,
    )


def test_not_ready_and_partial_prerequisites_produce_no_valuation():
    not_ready = bundle(HistoricalMultipleType.P_E, verify_semantics=False)
    partial = bundle(bridge_method=None)
    assert not_ready[0].status is OwnHistoryMethodStatus.NOT_READY
    assert partial[0].status is OwnHistoryMethodStatus.PARTIAL
    for result in (calculate(not_ready), calculate(partial)):
        assert result.status is ValuationMethodStatus.UNAVAILABLE
        assert result.valuation_result.low is result.valuation_result.central is result.valuation_result.high is None


def test_ready_prerequisite_calculates_three_historical_band_points_and_reuses_generic_result():
    result = calculate(bundle())
    assert result.status is ValuationMethodStatus.VALID
    assert (
        result.lower_point.historical_multiple,
        result.central_point.historical_multiple,
        result.upper_point.historical_multiple,
    ) == (15, 20, 25)
    assert (result.valuation_result.low, result.valuation_result.central, result.valuation_result.high) == (
        145, 195, 245,
    )
    assert result.valuation_result.method == HistoricalMultipleType.EV_EBITDA.value
    assert result.valuation_result.valuation_family == "own_history"


@pytest.mark.parametrize("window", tuple(HistoricalWindow))
def test_selected_window_is_preserved_and_never_blended(window):
    result = calculate(bundle(window=window))
    assert result.selected_window is window
    assert result.distribution_id == distribution(HistoricalMultipleType.EV_EBITDA, window).distribution_id
    assert result.supporting_distribution_id == result.distribution_id


@pytest.mark.parametrize("period", tuple(ForwardPeriodSelection))
def test_selected_fy_period_is_preserved_and_never_ntm_or_averaged(period):
    result = calculate(bundle(period=period))
    expected_denominator = 100 if period is ForwardPeriodSelection.FY1 else 200
    assert result.selected_forward_period is period
    assert result.central_point.forward_denominator == expected_denominator
    assert result.central_point.selected_forward_period is period
    assert result.estimate_case is EstimateCase.AVERAGE


def test_average_consensus_only_drives_all_points_not_analyst_low_or_high():
    result = calculate(bundle())
    assert {point.forward_denominator for point in (
        result.lower_point, result.central_point, result.upper_point,
    )} == {100}
    assert result.lower_point.per_share_value == 145
    assert result.upper_point.per_share_value == 245
    assert result.lower_point.forward_denominator not in {1, 999}


def test_non_average_readiness_fails_closed_without_scenario_creation():
    result = calculate(bundle(estimate_case=EstimateCase.LOW))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("AVERAGE" in issue.reason for issue in result.issues)


def test_wrong_forward_observation_and_unit_are_rejected_not_substituted_or_scaled():
    readiness, dist, forward, _, direct = bundle()
    wrong = next(
        item for item in forward_observations(HistoricalMultipleType.EV_EBITDA)
        if item.estimate_case is EstimateCase.LOW and item.fiscal_year == 2027
    )
    wrong_case = calculate_own_history_valuation(
        readiness, dist, wrong, direct_enterprise_bridge=direct,
    )
    object.__setattr__(forward, "unit", MetricUnit.CURRENCY_PER_SHARE)
    wrong_unit = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    assert wrong_case.status is wrong_unit.status is ValuationMethodStatus.UNAVAILABLE
    assert any("exact readiness selection" in issue.reason for issue in wrong_case.issues)
    assert any("canonical monetary unit" in issue.reason for issue in wrong_unit.issues)


def test_distribution_not_selected_by_readiness_is_rejected_without_window_fallback():
    readiness, _, forward, _, direct = bundle(window=HistoricalWindow.FIVE_YEAR)
    wrong_distribution = distribution(HistoricalMultipleType.EV_EBITDA, HistoricalWindow.THREE_YEAR)
    result = calculate_own_history_valuation(
        readiness, wrong_distribution, forward, direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("not the distribution selected" in issue.reason for issue in result.issues)


def test_ready_pe_is_multiple_times_eps_and_uses_no_bridge_or_share_division():
    result = calculate(bundle(HistoricalMultipleType.P_E))
    assert result.status is ValuationMethodStatus.VALID
    assert (result.valuation_result.low, result.valuation_result.central, result.valuation_result.high) == (
        75, 100, 125,
    )
    for point in (result.lower_point, result.central_point, result.upper_point):
        assert point.valuation_basis is ValuationBasis.EQUITY
        assert point.bridge_id is None and point.share_count is None
        assert point.implied_enterprise_value is None and point.implied_equity_value is None


def test_generic_eps_without_diluted_semantic_evidence_calculates_nothing():
    result = calculate(bundle(HistoricalMultipleType.P_E, verify_semantics=False))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.central_point.per_share_value is None


def test_ev_ebitda_direct_bridge_calculation_trace_is_exact():
    readiness, dist, forward, component, direct = bundle()
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    point = result.central_point
    assert direct.enterprise_equity_adjustment == direct.enterprise_value - direct.market_cap == 50
    assert point.implied_enterprise_value == 20 * 100 == 2000
    assert point.enterprise_equity_adjustment == 50
    assert point.implied_equity_value == 1950
    assert point.per_share_value == 195
    assert point.share_count == 10
    assert point.share_basis is ShareCountBasis.SHARES_OUTSTANDING


def test_verified_ev_ebit_uses_same_enterprise_mechanics():
    result = calculate(bundle(HistoricalMultipleType.EV_EBIT))
    assert result.status is ValuationMethodStatus.VALID
    assert result.central_point.implied_enterprise_value == 1600
    assert result.central_point.implied_equity_value == 1550
    assert result.central_point.per_share_value == 155


def test_component_bridge_alternative_uses_net_debt_and_exact_selected_shares():
    result = calculate(bundle(bridge_method="component"))
    assert result.status is ValuationMethodStatus.VALID
    assert result.bridge_id and result.central_point.bridge_id == result.bridge_id
    assert result.central_point.enterprise_equity_adjustment == 50
    assert result.central_point.share_observation_id in result.supporting_observation_ids
    assert result.central_point.per_share_value == 195


def test_ads_converted_share_evidence_preserves_adr_readiness_and_per_share_mechanics():
    adr = identity(security_type="ADR", adr_ratio=None)
    result = calculate(bundle(
        company=adr,
        share_semantics=ShareCountSemantics.ADS_CONVERTED,
    ))
    assert result.status is ValuationMethodStatus.VALID
    assert result.central_point.share_basis is ShareCountBasis.SHARES_OUTSTANDING
    assert result.central_point.per_share_value == 195


def test_direct_and_component_bridges_are_never_combined_or_averaged():
    readiness, dist, forward, _, direct = bundle()
    result = calculate_own_history_valuation(
        readiness,
        dist,
        forward,
        capital_structure_snapshot=component_bridge(),
        direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("Exactly the selected direct" in issue.reason for issue in result.issues)


def test_wrong_or_missing_bridge_is_rejected_for_enterprise_method():
    readiness, dist, forward, _, _ = bundle()
    missing = calculate_own_history_valuation(readiness, dist, forward)
    wrong = calculate_own_history_valuation(
        readiness, dist, forward, capital_structure_snapshot=component_bridge(),
    )
    assert missing.status is wrong.status is ValuationMethodStatus.UNAVAILABLE


def test_pe_rejects_irrelevant_enterprise_bridge_input():
    readiness, dist, forward, _, _ = bundle(HistoricalMultipleType.P_E)
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct_bridge(),
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("must not consume" in issue.reason for issue in result.issues)


def test_missing_shares_prevents_normal_enterprise_valuation():
    items = bundle(bridge_method=None)
    readiness, dist, forward, _, _ = items
    incomplete = build_direct_enterprise_equity_bridge(
        identity(),
        (
            actual(MetricId.ENTERPRISE_VALUE, 150, date(2026, 8, 26)),
            actual(MetricId.MARKET_CAP, 100, date(2026, 8, 26)),
        ),
        analysis_as_of=ANALYSIS_AS_OF,
    )
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=incomplete,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE


@pytest.mark.parametrize("invalid_shares", [0.0, -10.0, math.inf])
def test_zero_negative_or_nonfinite_shares_fail_closed(invalid_shares):
    readiness, dist, forward, _, direct = bundle()
    object.__setattr__(direct, "shares_outstanding", invalid_shares)
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("finite and positive" in issue.reason for issue in result.issues)


def test_wrong_share_basis_is_rejected_against_readiness():
    readiness, dist, forward, _, direct = bundle()
    object.__setattr__(direct, "share_basis", ShareCountBasis.BASIC_WEIGHTED_AVERAGE)
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("Share count/basis" in issue.reason for issue in result.issues)


def test_currency_mismatch_fails_without_fx_conversion():
    readiness, dist, forward, _, direct = bundle()
    object.__setattr__(direct, "currency", "EUR")
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.currency == "USD"
    assert any("no FX" in issue.reason for issue in result.issues)


@pytest.mark.parametrize(
    "multiple_type",
    (HistoricalMultipleType.P_E, HistoricalMultipleType.EV_EBITDA, HistoricalMultipleType.EV_EBIT),
)
@pytest.mark.parametrize("invalid_denominator", [0.0, -1.0])
def test_zero_or_negative_forward_earnings_denominator_is_rejected(multiple_type, invalid_denominator):
    result = calculate(bundle(multiple_type, denominator=invalid_denominator))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.central_point.per_share_value is None
    assert any("finite and positive" in issue.reason for issue in result.issues)


def test_nonfinite_forward_denominator_is_rejected_defensively():
    readiness, dist, forward, component, direct = bundle()
    object.__setattr__(forward, "value", math.nan)
    result = calculate_own_history_valuation(
        readiness, dist, forward,
        capital_structure_snapshot=component,
        direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("field", "value"),
    (("p25", math.nan), ("p25", 21.0), ("median", 26.0)),
)
def test_nonfinite_or_malformed_historical_statistics_fail_without_reordering(field, value):
    readiness, dist, forward, _, direct = bundle()
    object.__setattr__(dist, field, value)
    result = calculate_own_history_valuation(
        readiness, dist, forward, direct_enterprise_bridge=direct,
    )
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert all(point.per_share_value is None for point in (
        result.lower_point, result.central_point, result.upper_point,
    ))
    assert any("finite and positive" in issue.reason or "not reordered" in issue.reason for issue in result.issues)


def test_invalid_lower_point_is_preserved_and_method_becomes_partial():
    result = calculate(bundle(adjustment=1600))
    assert result.status is ValuationMethodStatus.PARTIAL
    assert result.lower_point.status is ValuationMethodStatus.UNAVAILABLE
    assert result.lower_point.implied_equity_value == -100
    assert result.lower_point.per_share_value is None
    assert result.central_point.per_share_value == 40
    assert result.upper_point.per_share_value == 90
    assert result.valuation_result.low is None


def test_invalid_central_point_prevents_resolved_method_and_is_not_floored():
    result = calculate(bundle(adjustment=2100))
    assert result.status is ValuationMethodStatus.PARTIAL
    assert result.central_point.status is ValuationMethodStatus.UNAVAILABLE
    assert result.central_point.implied_equity_value == -100
    assert result.central_point.per_share_value is None
    assert result.upper_point.per_share_value == 40


def test_all_nonpositive_equity_points_make_method_unavailable():
    result = calculate(bundle(adjustment=2600))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.valuation_result.low is result.valuation_result.central is result.valuation_result.high is None
    assert all(point.implied_equity_value <= 0 for point in (
        result.lower_point, result.central_point, result.upper_point,
    ))


def test_valid_range_ordering_is_retained_without_sorting_or_rounding():
    result = calculate(bundle(denominator=100.123, adjustment=50.456))
    values = tuple(point.per_share_value for point in (
        result.lower_point, result.central_point, result.upper_point,
    ))
    assert values[0] < values[1] < values[2]
    assert result.central_point.implied_enterprise_value == pytest.approx(20 * 100.123)
    assert result.central_point.per_share_value == pytest.approx((20 * 100.123 - 50.456) / 10)


def test_result_retains_distribution_forward_bridge_share_currency_and_calculation_provenance():
    result = calculate(bundle())
    point = result.central_point
    assert result.distribution_id == point.distribution_id
    assert point.forward_observation_id in result.supporting_observation_ids
    assert point.bridge_id == result.bridge_id
    assert point.share_observation_id in result.supporting_observation_ids
    assert result.currency == point.currency == "USD"
    derived = next(item for item in point.provenance if item.endpoint_or_dataset == "v1_own_history_numeric_valuation")
    assert derived.configuration_or_override_id == result.policy_id
    assert point.forward_observation_id in derived.input_observation_ids
    assert "median" in derived.transformation_steps[0]


def test_numeric_contracts_are_immutable_and_exclude_market_upside_target_and_aggregation_fields():
    result = calculate(bundle())
    forbidden = {
        "current_price", "upside", "downside", "target_price", "margin_of_safety",
        "weight", "aggregation", "stance", "analyst_target", "external_dcf",
    }
    assert not forbidden.intersection(item.name for item in fields(OwnHistoryValuationPoint))
    assert not forbidden.intersection(item.name for item in fields(OwnHistoryValuationResult))
    with pytest.raises(FrozenInstanceError):
        result.status = ValuationMethodStatus.UNAVAILABLE
    with pytest.raises(FrozenInstanceError):
        result.central_point.per_share_value = 0


def test_valuation_function_accepts_no_market_price_or_external_reference_inputs():
    parameters = inspect.signature(calculate_own_history_valuation).parameters
    assert not {
        "current_price", "analyst_target", "fmp_dcf", "peers", "weights", "stance",
    }.intersection(parameters)


def test_provider_specific_scale_adjustment_is_absent_from_valuation_service():
    source = inspect.getsource(inspect.getmodule(calculate_own_history_valuation))
    forbidden = ("1_000", "1000", "million", "billion")
    assert not any(token.lower() in source.lower() for token in forbidden)
