from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone

import pytest

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CompanyIdentity,
    DataAvailability,
    DenominatorCompatibilityStatus,
    DenominatorSemanticEvidence,
    DirectEnterpriseEquityBridge,
    EnterpriseAdjustmentRequirement,
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    HistoricalDistributionUsability,
    HistoricalMultipleType,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    HistoricalWindow,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    OwnHistoryMethodReadiness,
    OwnHistoryMethodStatus,
    Provenance,
    ProviderSymbol,
    ShareCountBasis,
    ShareCountSemantics,
    ValuationBasis,
    stable_historical_valuation_id,
)
from stock_analyser.services import (
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    OwnHistoryInputPolicy,
    assess_own_history_method_readiness,
    build_capital_structure_snapshot,
    build_direct_enterprise_equity_bridge,
    build_forward_consensus,
    build_forward_denominator_alignment,
    build_historical_distribution,
    select_forward_denominator,
)


ANALYSIS_AS_OF = datetime(2026, 8, 27, 16, tzinfo=timezone.utc)
PERMISSIVE = HistoricalDistributionPolicy(
    policy_id="synthetic-7c-distribution",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=tuple(HistoricalWindowMinimum(window, 1, 0.01) for window in HistoricalWindow),
)
SEMANTICS = {
    HistoricalMultipleType.P_E: (
        ValuationBasis.EQUITY, HistoricalValuationDenominator.DILUTED_EPS, "ratio_price_to_earnings",
    ),
    HistoricalMultipleType.EV_EBITDA: (
        ValuationBasis.ENTERPRISE, HistoricalValuationDenominator.EBITDA, "ratio_ev_to_ebitda",
    ),
    HistoricalMultipleType.EV_EBIT: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
        "ratio_ev_to_ebit",
    ),
}


def identity(*, currency: str = "USD", security_type: str = "Ordinary share", adr_ratio=None):
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
        reporting_currency=currency,
        quote_currency=currency,
        quote_unit=currency,
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "F-SYN"), ProviderSymbol("fmp", "FMP-SYN")),
        underlying_security_id="underlying-syn" if is_adr else None,
        adr_ratio=adr_ratio,
        identity_availability=DataAvailability.PARTIAL if is_adr and adr_ratio is None else DataAvailability.AVAILABLE,
    )


def provenance(provider: str, symbol: str, source_metric: str, *, as_of=ANALYSIS_AS_OF):
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_canonical",
        provider_symbol=symbol,
        retrieved_at=ANALYSIS_AS_OF,
        as_of_at=as_of,
        source_metric=source_metric,
    )


def historical_observation(multiple_type: HistoricalMultipleType, observed: date, value: float):
    basis, denominator, source_metric = SEMANTICS[multiple_type]
    prov = provenance("fiscal", "F-SYN", source_metric)
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
        provenance=prov,
        source_metric=source_metric,
        eligibility=HistoricalValuationEligibility.ELIGIBLE,
        quote_currency_context="USD",
        reporting_currency_context="USD",
    )


def distribution(multiple_type: HistoricalMultipleType, window: HistoricalWindow = HistoricalWindow.FIVE_YEAR):
    years = {
        HistoricalWindow.THREE_YEAR: 2023,
        HistoricalWindow.FIVE_YEAR: 2021,
        HistoricalWindow.TEN_YEAR: 2016,
    }
    observations = (
        historical_observation(multiple_type, date(years[window], 8, 27), 10),
        historical_observation(multiple_type, ANALYSIS_AS_OF.date(), 20),
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


def forward_observation(
    metric: MetricId,
    year: int,
    case: EstimateCase,
    *,
    value: float = 100,
    currency: str = "USD",
    symbol: str = "FMP-SYN",
):
    unit = MetricUnit.CURRENCY_PER_SHARE if metric is MetricId.EPS else MetricUnit.CURRENCY
    prov = provenance("fmp", symbol, metric.value)
    return MetricObservation(
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
        currency=currency,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        fiscal_year=year,
    )


def consensus(*, include=(MetricId.EPS, MetricId.EBITDA, MetricId.EBIT), currency="USD", symbol="FMP-SYN"):
    observations = tuple(
        forward_observation(metric, year, case, value=100 + year - 2027, currency=currency, symbol=symbol)
        for year in (2027, 2028)
        for metric in include
        for case in (EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH)
    )
    return build_forward_consensus(observations, as_of_at=ANALYSIS_AS_OF)


def actual(
    metric: MetricId,
    value: float,
    period_end: date,
    *,
    currency: str | None = "USD",
    provider: str = "fiscal",
    provider_symbol: str = "F-SYN",
):
    unit = MetricUnit.SHARES if metric in {
        MetricId.SHARES_OUTSTANDING, MetricId.SHARES_BASIC, MetricId.SHARES_DILUTED,
    } else MetricUnit.CURRENCY
    prov = provenance(provider, provider_symbol, metric.value)
    return MetricObservation(
        observation_id=f"obs:{provider}:{metric.value}:{period_end.isoformat()}:{value}",
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
        period_end=period_end,
    )


def bridge_observations(*, cash_currency="USD", debt_currency="USD", include_cash=True, include_debt=True, include_shares=True):
    items = []
    if include_cash:
        items.append(actual(MetricId.CASH_AND_EQUIVALENTS, 30, date(2026, 6, 30), currency=cash_currency))
    if include_debt:
        items.append(actual(MetricId.GROSS_DEBT, 80, date(2026, 6, 30), currency=debt_currency))
    if include_shares:
        items.append(actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 8, 1), currency=None))
    return tuple(items)


def evidence(multiple_type: HistoricalMultipleType, status=DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT):
    denominator = SEMANTICS[multiple_type][1]
    metric = {
        HistoricalMultipleType.P_E: MetricId.EPS,
        HistoricalMultipleType.EV_EBITDA: MetricId.EBITDA,
        HistoricalMultipleType.EV_EBIT: MetricId.EBIT,
    }[multiple_type]
    return DenominatorSemanticEvidence(
        evidence_id=f"semantic:{multiple_type.value.lower()}",
        historical_multiple_type=multiple_type,
        historical_denominator=denominator,
        forward_metric_id=metric,
        compatibility_status=status,
        historical_definition=f"verified historical {denominator.value}",
        forward_definition=f"verified forward {metric.value}",
        reason="Synthetic explicit provider-definition evidence.",
        source_references=("synthetic official definition A", "synthetic official definition B"),
        policy_id="synthetic-semantic-policy",
    )


def capital(**kwargs):
    observations = kwargs.pop("observations", bridge_observations())
    return build_capital_structure_snapshot(
        identity(), observations, analysis_as_of=ANALYSIS_AS_OF, **kwargs,
    )


@pytest.mark.parametrize(
    ("multiple_type", "expected"),
    [
        (HistoricalMultipleType.P_E, DenominatorCompatibilityStatus.UNVERIFIED),
        (HistoricalMultipleType.EV_EBITDA, DenominatorCompatibilityStatus.EXACT),
        (HistoricalMultipleType.EV_EBIT, DenominatorCompatibilityStatus.UNVERIFIED),
    ],
)
def test_default_alignment_policy_is_explicit_and_conservative(multiple_type, expected):
    selected = select_forward_denominator(consensus(), multiple_type=multiple_type)
    aligned = build_forward_denominator_alignment(
        multiple_type=multiple_type,
        distribution=distribution(multiple_type),
        forward_selection=selected,
    )
    assert aligned.compatibility_status is expected
    assert aligned.supporting_distribution_id
    assert aligned.supporting_observation_ids == (selected.observation_id,)
    assert aligned.provenance == selected.provenance


def test_generic_eps_name_does_not_establish_diluted_eps_but_evidence_can():
    dist = distribution(HistoricalMultipleType.P_E)
    selected = select_forward_denominator(consensus(), multiple_type=HistoricalMultipleType.P_E)
    default = build_forward_denominator_alignment(
        multiple_type=HistoricalMultipleType.P_E, distribution=dist, forward_selection=selected,
    )
    verified = build_forward_denominator_alignment(
        multiple_type=HistoricalMultipleType.P_E,
        distribution=dist,
        forward_selection=selected,
        semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    assert default.historical_denominator is HistoricalValuationDenominator.DILUTED_EPS
    assert default.forward_metric_id is MetricId.EPS
    assert default.compatibility_status is DenominatorCompatibilityStatus.UNVERIFIED
    assert verified.compatibility_status is DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT
    assert verified.semantic_evidence_id == "semantic:p_e"


def test_operating_profit_never_uses_operating_income_or_generic_ebit_shortcut():
    dist = distribution(HistoricalMultipleType.EV_EBIT)
    selected = select_forward_denominator(consensus(), multiple_type=HistoricalMultipleType.EV_EBIT)
    aligned = build_forward_denominator_alignment(
        multiple_type=HistoricalMultipleType.EV_EBIT, distribution=dist, forward_selection=selected,
    )
    assert aligned.historical_denominator is HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT
    assert aligned.forward_metric_id is MetricId.EBIT
    assert aligned.compatibility_status is DenominatorCompatibilityStatus.UNVERIFIED
    wrong = replace(evidence(HistoricalMultipleType.EV_EBIT), forward_metric_id=MetricId.OPERATING_INCOME)
    with pytest.raises(ValueError, match="does not describe"):
        build_forward_denominator_alignment(
            multiple_type=HistoricalMultipleType.EV_EBIT,
            distribution=dist,
            forward_selection=selected,
            semantic_evidence=wrong,
        )


def test_explicit_incompatibility_blocks_alignment_and_cannot_be_free_text_only():
    dist = distribution(HistoricalMultipleType.P_E)
    selected = select_forward_denominator(consensus(), multiple_type=HistoricalMultipleType.P_E)
    aligned = build_forward_denominator_alignment(
        multiple_type=HistoricalMultipleType.P_E,
        distribution=dist,
        forward_selection=selected,
        semantic_evidence=evidence(HistoricalMultipleType.P_E, DenominatorCompatibilityStatus.INCOMPATIBLE),
    )
    assert aligned.compatibility_status is DenominatorCompatibilityStatus.INCOMPATIBLE
    with pytest.raises(TypeError):
        replace(evidence(HistoricalMultipleType.P_E), compatibility_status="verified_equivalent")


def test_fy1_fy2_selection_and_average_case_are_explicit_and_never_ntm_or_scenarios():
    snapshot = consensus()
    fy1 = select_forward_denominator(snapshot, multiple_type=HistoricalMultipleType.P_E)
    fy2 = select_forward_denominator(
        snapshot,
        multiple_type=HistoricalMultipleType.P_E,
        period_selection=ForwardPeriodSelection.FY2,
    )
    assert fy1.period_selection is ForwardPeriodSelection.FY1 and fy1.fiscal_year == 2027
    assert fy2.period_selection is ForwardPeriodSelection.FY2 and fy2.fiscal_year == 2028
    assert fy1.estimate_case is EstimateCase.AVERAGE
    assert fy1.available_estimate_cases == (EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH)
    assert not fy1.is_ntm and not fy1.scenarios_created
    assert fy1.period_end > ANALYSIS_AS_OF.date()


def test_low_high_ranges_remain_cases_and_missing_selected_denominator_is_unavailable():
    low = select_forward_denominator(
        consensus(), multiple_type=HistoricalMultipleType.EV_EBITDA, estimate_case=EstimateCase.LOW,
    )
    missing = select_forward_denominator(
        consensus(include=(MetricId.EPS,)), multiple_type=HistoricalMultipleType.EV_EBITDA,
    )
    assert low.estimate_case is EstimateCase.LOW and not low.scenarios_created
    assert missing.observation_id is None and "unavailable" in missing.reason
    aligned = build_forward_denominator_alignment(
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        distribution=distribution(HistoricalMultipleType.EV_EBITDA),
        forward_selection=missing,
    )
    assert aligned.compatibility_status is DenominatorCompatibilityStatus.UNAVAILABLE


@pytest.mark.parametrize("window", tuple(HistoricalWindow))
def test_explicit_windows_are_preserved_and_never_blended(window):
    result = assess_own_history_method_readiness(
        identity(),
        [distribution(HistoricalMultipleType.P_E, selected) for selected in HistoricalWindow],
        consensus(),
        multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
        selected_window=window,
        semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    assert result.selected_window is window
    assert result.distribution_id == distribution(HistoricalMultipleType.P_E, window).distribution_id


def test_default_selected_window_is_five_year_and_partial_distribution_is_blocked():
    dist = replace(
        distribution(HistoricalMultipleType.P_E), usability=HistoricalDistributionUsability.PARTIAL,
    )
    result = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF, semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    assert result.selected_window is HistoricalWindow.FIVE_YEAR
    assert result.status is OwnHistoryMethodStatus.NOT_READY
    assert any("only USABLE" in reason for reason in result.blocking_reasons)


def test_latest_actual_bridge_inputs_are_selected_and_future_evidence_is_excluded():
    observations = (
        actual(MetricId.CASH_AND_EQUIVALENTS, 10, date(2025, 12, 31)),
        actual(MetricId.CASH_AND_EQUIVALENTS, 30, date(2026, 6, 30)),
        actual(MetricId.CASH_AND_EQUIVALENTS, 999, date(2026, 12, 31)),
        actual(MetricId.GROSS_DEBT, 80, date(2026, 6, 30)),
        actual(MetricId.GROSS_DEBT, 999, date(2027, 6, 30)),
        actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 8, 1), currency=None),
    )
    snapshot = capital(observations=observations)
    assert snapshot.cash_and_equivalents == 30 and snapshot.cash_source_date == date(2026, 6, 30)
    assert snapshot.gross_debt == 80 and snapshot.debt_source_date == date(2026, 6, 30)
    assert snapshot.net_debt == 50
    assert not any("999" in observation_id for observation_id in snapshot.source_observation_ids)


@pytest.mark.parametrize(
    ("include_cash", "include_debt", "missing_name"),
    [(False, True, "cash"), (True, False, "debt")],
)
def test_missing_cash_or_debt_is_not_zero_and_net_debt_is_unavailable(include_cash, include_debt, missing_name):
    snapshot = capital(observations=bridge_observations(include_cash=include_cash, include_debt=include_debt))
    assert snapshot.net_debt is None
    assert snapshot.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert any(missing_name in item for item in snapshot.missing_requirements)
    assert (snapshot.cash_and_equivalents is None) is (not include_cash)
    assert (snapshot.gross_debt is None) is (not include_debt)


def test_net_debt_derives_only_from_both_inputs_and_preserves_both_provenances():
    snapshot = capital()
    assert snapshot.net_debt == snapshot.gross_debt - snapshot.cash_and_equivalents == 50
    assert snapshot.completeness_status is CapitalStructureCompleteness.COMPLETE
    assert {snapshot.cash_observation_id, snapshot.debt_observation_id}.issubset(snapshot.source_observation_ids)
    assert {item.source_metric for item in snapshot.provenance} >= {
        MetricId.CASH_AND_EQUIVALENTS.value, MetricId.GROSS_DEBT.value,
    }


def test_bridge_currency_mismatch_is_partial_and_never_converted():
    snapshot = capital(observations=bridge_observations(cash_currency="USD", debt_currency="EUR"))
    assert snapshot.currency is None and snapshot.net_debt is None
    assert snapshot.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert any("no FX conversion" in warning for warning in snapshot.warnings)


def test_explicitly_required_unsupported_enterprise_adjustment_is_not_assumed_zero():
    snapshot = capital(policy=OwnHistoryInputPolicy(
        policy_id="synthetic-nci-required",
        required_additional_adjustments=(EnterpriseAdjustmentRequirement.NON_CONTROLLING_INTEREST,),
    ))
    assert snapshot.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert snapshot.required_additional_adjustments == (
        EnterpriseAdjustmentRequirement.NON_CONTROLLING_INTEREST,
    )
    assert any("non_controlling_interest" in item for item in snapshot.missing_requirements)
    assert any("not assumed to be zero" in warning for warning in snapshot.warnings)


def test_cash_debt_date_mismatch_is_visible_without_silent_threshold():
    observations = (
        actual(MetricId.CASH_AND_EQUIVALENTS, 30, date(2026, 3, 31)),
        actual(MetricId.GROSS_DEBT, 80, date(2026, 6, 30)),
        actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 8, 1), currency=None),
    )
    default = capital(observations=observations)
    strict = capital(
        observations=observations,
        policy=OwnHistoryInputPolicy(
            policy_id="synthetic-strict-gap", maximum_cash_debt_date_gap_days=30,
        ),
    )
    assert default.cash_debt_date_gap_days == 91
    assert default.maximum_date_gap_days is None and not default.date_gap_exceeded
    assert default.completeness_status is CapitalStructureCompleteness.COMPLETE
    assert any("differ by 91 days" in warning for warning in default.warnings)
    assert strict.date_gap_exceeded
    assert strict.completeness_status is CapitalStructureCompleteness.PARTIAL


@pytest.mark.parametrize(
    ("basis", "metric"),
    [
        (ShareCountBasis.SHARES_OUTSTANDING, MetricId.SHARES_OUTSTANDING),
        (ShareCountBasis.BASIC_WEIGHTED_AVERAGE, MetricId.SHARES_BASIC),
        (ShareCountBasis.DILUTED_WEIGHTED_AVERAGE, MetricId.SHARES_DILUTED),
    ],
)
def test_share_bases_are_distinct_and_selected_explicitly(basis, metric):
    observations = (
        *bridge_observations(include_shares=False),
        actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 6, 30), currency=None),
        actual(MetricId.SHARES_BASIC, 11, date(2026, 6, 30), currency=None),
        actual(MetricId.SHARES_DILUTED, 12, date(2026, 6, 30), currency=None),
    )
    snapshot = capital(observations=observations, share_basis=basis)
    assert snapshot.share_basis is basis
    assert snapshot.shares == {MetricId.SHARES_OUTSTANDING: 10, MetricId.SHARES_BASIC: 11, MetricId.SHARES_DILUTED: 12}[metric]


def test_pe_needs_no_enterprise_bridge_but_unverified_eps_is_not_ready():
    dist = distribution(HistoricalMultipleType.P_E)
    unverified = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
    )
    verified = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF, semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    assert unverified.status is OwnHistoryMethodStatus.NOT_READY
    assert unverified.capital_structure_snapshot_id is None
    assert verified.status is OwnHistoryMethodStatus.READY
    assert verified.capital_structure_snapshot_id is None


def test_ev_ebitda_requires_complete_bridge_and_missing_debt_is_partial_not_zero_filled():
    dist = distribution(HistoricalMultipleType.EV_EBITDA)
    complete = capital()
    incomplete = capital(observations=bridge_observations(include_debt=False))
    ready = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF, capital_structure_snapshot=complete,
    )
    partial = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF, capital_structure_snapshot=incomplete,
    )
    assert ready.status is OwnHistoryMethodStatus.READY
    assert partial.status is OwnHistoryMethodStatus.PARTIAL
    assert incomplete.gross_debt is None and incomplete.net_debt is None


def test_ev_ebit_requires_both_verified_semantics_and_complete_bridge():
    dist = distribution(HistoricalMultipleType.EV_EBIT)
    default = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.EV_EBIT,
        analysis_as_of=ANALYSIS_AS_OF, capital_structure_snapshot=capital(),
    )
    verified = assess_own_history_method_readiness(
        identity(), [dist], consensus(), multiple_type=HistoricalMultipleType.EV_EBIT,
        analysis_as_of=ANALYSIS_AS_OF, capital_structure_snapshot=capital(),
        semantic_evidence=evidence(HistoricalMultipleType.EV_EBIT),
    )
    assert default.status is OwnHistoryMethodStatus.NOT_READY
    assert verified.status is OwnHistoryMethodStatus.READY


def test_forward_bridge_currency_mismatch_blocks_method_and_performs_no_fx():
    result = assess_own_history_method_readiness(
        identity(currency="USD"),
        [distribution(HistoricalMultipleType.EV_EBITDA)],
        consensus(currency="EUR"),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        capital_structure_snapshot=capital(),
    )
    assert result.status is OwnHistoryMethodStatus.NOT_READY
    assert any("no FX conversion" in reason for reason in result.blocking_reasons)


def test_missing_suitable_share_basis_keeps_enterprise_method_partial():
    snapshot = capital(observations=bridge_observations(include_shares=False))
    result = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        capital_structure_snapshot=snapshot,
    )
    assert result.status is OwnHistoryMethodStatus.PARTIAL
    assert any("share" in reason for reason in result.blocking_reasons)


def test_missing_adr_ratio_and_identity_symbol_mismatch_block_readiness():
    dist = distribution(HistoricalMultipleType.P_E)
    adr = assess_own_history_method_readiness(
        identity(security_type="ADR", adr_ratio=None), [dist], consensus(),
        multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
        semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    symbol = assess_own_history_method_readiness(
        identity(), [dist], consensus(symbol="WRONG-FMP"),
        multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
        semantic_evidence=evidence(HistoricalMultipleType.P_E),
    )
    assert adr.status is OwnHistoryMethodStatus.NOT_READY
    assert any("ADR" in reason for reason in adr.blocking_reasons)
    assert symbol.status is OwnHistoryMethodStatus.NOT_READY
    assert any("canonical identity" in reason for reason in symbol.blocking_reasons)


def test_readiness_contract_is_immutable_provenance_bearing_and_has_no_valuation_outputs():
    result = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        capital_structure_snapshot=capital(),
    )
    names = {item.name for item in fields(OwnHistoryMethodReadiness)}
    forbidden = {"fair_value", "target_price", "upside", "downside", "low", "central", "high", "weight"}
    assert not names.intersection(forbidden)
    assert result.supporting_observation_ids and result.provenance
    with pytest.raises(FrozenInstanceError):
        result.status = OwnHistoryMethodStatus.NOT_READY


def test_provider_symbols_are_audit_metadata_not_cross_provider_identity_keys():
    snapshot = capital()
    assert snapshot.security_id == "security-syn" and snapshot.issuer_id == "issuer-syn"
    assert snapshot.provider_symbols == ("F-SYN",)
    assert "F-SYN" not in snapshot.snapshot_id


def direct_observations(
    *,
    tev=True,
    market_cap=True,
    shares=True,
    tev_date=date(2026, 8, 26),
    market_cap_date=date(2026, 8, 26),
    tev_currency="USD",
    market_cap_currency="USD",
    market_cap_provider="fiscal",
    tev_value=150,
):
    items = []
    if tev:
        items.append(actual(MetricId.ENTERPRISE_VALUE, tev_value, tev_date, currency=tev_currency))
    if market_cap:
        items.append(actual(
            MetricId.MARKET_CAP, 100, market_cap_date,
            currency=market_cap_currency, provider=market_cap_provider,
        ))
    if shares:
        items.append(actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 6, 30), currency=None))
    return tuple(items)


def direct_bridge(*, company=None, observations=None, semantics=None, policy=None):
    kwargs = {}
    if semantics is not None:
        kwargs["share_count_semantics"] = semantics
    if policy is not None:
        kwargs["policy"] = policy
    return build_direct_enterprise_equity_bridge(
        company or identity(),
        observations if observations is not None else direct_observations(),
        analysis_as_of=ANALYSIS_AS_OF,
        **kwargs,
    )


def test_direct_bridge_requires_fiscal_tev_and_market_cap_without_zero_fill():
    no_tev = direct_bridge(observations=direct_observations(tev=False))
    no_market_cap = direct_bridge(observations=direct_observations(market_cap=False))
    assert no_tev.enterprise_value is None and no_tev.enterprise_equity_adjustment is None
    assert no_market_cap.market_cap is None and no_market_cap.enterprise_equity_adjustment is None
    assert no_tev.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert no_market_cap.completeness_status is CapitalStructureCompleteness.PARTIAL


def test_direct_bridge_is_exact_tev_minus_market_cap_with_both_provenances():
    result = direct_bridge()
    names = {item.name for item in fields(DirectEnterpriseEquityBridge)}
    forbidden = {
        "fair_value", "target_price", "upside", "downside", "implied_enterprise_value",
        "implied_equity_value", "applied_multiple",
    }
    assert not names.intersection(forbidden)
    assert result.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
    assert result.enterprise_equity_adjustment == 50
    assert result.completeness_status is CapitalStructureCompleteness.COMPLETE
    assert {result.enterprise_value_observation_id, result.market_cap_observation_id}.issubset(
        result.source_observation_ids
    )
    assert {item.metric_id for item in direct_observations()[:2]} == {
        MetricId.ENTERPRISE_VALUE, MetricId.MARKET_CAP,
    }
    assert len(result.provenance) == 3


def test_direct_bridge_rejects_fiscal_tev_plus_yahoo_market_cap():
    result = direct_bridge(observations=direct_observations(market_cap_provider="yahoo"))
    assert result.provider == "fiscal"
    assert result.market_cap is None
    assert result.enterprise_equity_adjustment is None
    assert result.completeness_status is CapitalStructureCompleteness.PARTIAL


def test_direct_bridge_rejects_fiscal_observation_from_another_identity_symbol():
    observations = (
        actual(MetricId.ENTERPRISE_VALUE, 150, date(2026, 8, 26)),
        actual(
            MetricId.MARKET_CAP,
            100,
            date(2026, 8, 26),
            provider_symbol="F-OTHER",
        ),
        actual(MetricId.SHARES_OUTSTANDING, 10, date(2026, 6, 30), currency=None),
    )
    result = direct_bridge(observations=observations)
    assert result.market_cap is None
    assert result.enterprise_equity_adjustment is None
    assert result.completeness_status is CapitalStructureCompleteness.PARTIAL


def test_direct_bridge_currency_mismatch_does_not_perform_fx():
    result = direct_bridge(observations=direct_observations(market_cap_currency="EUR"))
    assert result.currency is None and result.enterprise_equity_adjustment is None
    assert any("no FX conversion" in warning for warning in result.warnings)


@pytest.mark.parametrize("future_metric", [MetricId.ENTERPRISE_VALUE, MetricId.MARKET_CAP])
def test_direct_bridge_excludes_future_tev_and_market_cap(future_metric):
    observations = list(direct_observations())
    observations.append(actual(future_metric, 999, date(2026, 8, 28)))
    result = direct_bridge(observations=observations)
    assert result.enterprise_value == 150
    assert result.market_cap == 100
    assert result.enterprise_equity_adjustment == 50


def test_direct_bridge_prefers_latest_same_date_pair_over_newer_unmatched_observation():
    observations = (
        *direct_observations(tev_date=date(2026, 8, 25), market_cap_date=date(2026, 8, 25)),
        actual(MetricId.ENTERPRISE_VALUE, 175, date(2026, 8, 26)),
    )
    result = direct_bridge(observations=observations)
    assert result.enterprise_value_observation_date == result.market_cap_observation_date == date(2026, 8, 25)
    assert result.enterprise_value == 150


def test_date_mismatch_is_visible_and_requires_explicit_gap_policy():
    observations = direct_observations(
        tev_date=date(2026, 8, 26), market_cap_date=date(2026, 8, 24),
    )
    default = direct_bridge(observations=observations)
    configured = direct_bridge(
        observations=observations,
        policy=OwnHistoryInputPolicy(
            policy_id="synthetic-direct-gap",
            maximum_direct_bridge_date_gap_days=2,
        ),
    )
    assert default.observation_date_gap_days == 2 and default.date_gap_exceeded
    assert default.enterprise_equity_adjustment is None
    assert default.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert configured.enterprise_equity_adjustment == 50
    assert configured.completeness_status is CapitalStructureCompleteness.COMPLETE


def test_nonpositive_tev_fails_closed_with_structured_issue():
    result = direct_bridge(observations=direct_observations(tev_value=-1))
    assert result.enterprise_value is None and result.enterprise_equity_adjustment is None
    assert result.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert result.issues and result.issues[0].severity is IssueSeverity.BLOCKING


def test_direct_and_component_bridges_remain_distinct_and_are_never_averaged():
    direct = direct_bridge()
    component = capital()
    result = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        capital_structure_snapshot=component,
        direct_enterprise_bridge=direct,
    )
    assert type(direct) is DirectEnterpriseEquityBridge
    assert type(component).__name__ == "CapitalStructureSnapshot"
    assert result.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
    assert result.enterprise_equity_bridge_id == direct.bridge_id
    assert result.capital_structure_snapshot_id is None
    assert any("no bridge average" in warning for warning in result.warnings)


def test_complete_direct_bridge_makes_ev_ebitda_ready_without_debt_observation():
    direct = direct_bridge()
    assert not any(item.metric_id is MetricId.GROSS_DEBT for item in direct_observations())
    result = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=direct,
    )
    assert result.status is OwnHistoryMethodStatus.READY
    assert result.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE


def test_direct_bridge_missing_shares_keeps_ev_ebitda_partial():
    direct = direct_bridge(observations=direct_observations(shares=False))
    result = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=direct,
    )
    assert direct.enterprise_equity_adjustment == 50
    assert direct.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert result.status is OwnHistoryMethodStatus.PARTIAL


def test_direct_bridge_does_not_weaken_pe_or_ev_ebit_semantics():
    bridge = direct_bridge()
    pe = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.P_E)], consensus(),
        multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=bridge,
    )
    ev_ebit = assess_own_history_method_readiness(
        identity(), [distribution(HistoricalMultipleType.EV_EBIT)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBIT,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=bridge,
    )
    assert pe.status is OwnHistoryMethodStatus.NOT_READY
    assert pe.denominator_alignment.compatibility_status is DenominatorCompatibilityStatus.UNVERIFIED
    assert ev_ebit.status is OwnHistoryMethodStatus.NOT_READY
    assert ev_ebit.denominator_alignment.compatibility_status is DenominatorCompatibilityStatus.UNVERIFIED


def test_fiscal_ads_converted_shares_can_satisfy_adr_bridge_but_unverified_semantics_cannot():
    adr = identity(security_type="ADR", adr_ratio=None)
    verified = direct_bridge(company=adr, semantics=ShareCountSemantics.ADS_CONVERTED)
    unverified = direct_bridge(company=adr, semantics=ShareCountSemantics.UNVERIFIED)
    ready = assess_own_history_method_readiness(
        adr, [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=verified,
    )
    blocked = assess_own_history_method_readiness(
        adr, [distribution(HistoricalMultipleType.EV_EBITDA)], consensus(),
        multiple_type=HistoricalMultipleType.EV_EBITDA,
        analysis_as_of=ANALYSIS_AS_OF,
        direct_enterprise_bridge=unverified,
    )
    assert verified.completeness_status is CapitalStructureCompleteness.COMPLETE
    assert ready.status is OwnHistoryMethodStatus.READY
    assert unverified.completeness_status is CapitalStructureCompleteness.PARTIAL
    assert blocked.status is OwnHistoryMethodStatus.NOT_READY
