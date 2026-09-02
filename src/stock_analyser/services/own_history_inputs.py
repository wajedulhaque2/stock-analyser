"""Semantic and bridge prerequisites for a future own-history valuation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CapitalStructureSnapshot,
    CompanyIdentity,
    DataIssue,
    DenominatorCompatibilityStatus,
    DenominatorSemanticEvidence,
    DirectEnterpriseEquityBridge,
    EnterpriseAdjustmentRequirement,
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardDenominatorAlignment,
    ForwardDenominatorSelection,
    ForwardPeriodSelection,
    Frequency,
    HistoricalDistributionUsability,
    HistoricalMultipleDistribution,
    HistoricalMultipleType,
    HistoricalValuationDenominator,
    HistoricalWindow,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    OwnHistoryMethodReadiness,
    OwnHistoryMethodStatus,
    ShareCountBasis,
    ShareCountSemantics,
    stable_capital_structure_id,
    stable_denominator_alignment_id,
    stable_direct_bridge_id,
    stable_forward_selection_id,
    stable_own_history_readiness_id,
)
from stock_analyser.services.consensus import ForwardConsensus


_FORWARD_METRICS = {
    HistoricalMultipleType.P_E: MetricId.EPS,
    HistoricalMultipleType.EV_EBITDA: MetricId.EBITDA,
    HistoricalMultipleType.EV_EBIT: MetricId.EBIT,
}
_HISTORICAL_DEFINITIONS = {
    HistoricalValuationDenominator.DILUTED_EPS: "Fiscal share price divided by diluted EPS",
    HistoricalValuationDenominator.EBITDA: "Fiscal enterprise value divided by EBITDA",
    HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT: (
        "Fiscal enterprise value divided by the provider-documented Operating Profit denominator"
    ),
}
_FORWARD_DEFINITIONS = {
    MetricId.EPS: "FMP generic EPS annual consensus estimate; diluted economics are not established",
    MetricId.EBITDA: "FMP annual EBITDA consensus estimate using the canonical EBITDA metric",
    MetricId.EBIT: "FMP generic EBIT annual consensus estimate; equivalence to Fiscal Operating Profit is not established",
}
_SHARE_METRICS = {
    ShareCountBasis.SHARES_OUTSTANDING: MetricId.SHARES_OUTSTANDING,
    ShareCountBasis.BASIC_WEIGHTED_AVERAGE: MetricId.SHARES_BASIC,
    ShareCountBasis.DILUTED_WEIGHTED_AVERAGE: MetricId.SHARES_DILUTED,
}


@dataclass(frozen=True, slots=True)
class OwnHistoryInputPolicy:
    policy_id: str
    default_window: HistoricalWindow = HistoricalWindow.FIVE_YEAR
    default_forward_period: ForwardPeriodSelection = ForwardPeriodSelection.FY1
    default_estimate_case: EstimateCase = EstimateCase.AVERAGE
    required_enterprise_share_basis: ShareCountBasis = ShareCountBasis.SHARES_OUTSTANDING
    canonical_actual_providers: tuple[str, ...] = ("fiscal",)
    maximum_cash_debt_date_gap_days: int | None = None
    required_additional_adjustments: tuple[EnterpriseAdjustmentRequirement, ...] = ()
    maximum_direct_bridge_date_gap_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if self.default_estimate_case is EstimateCase.NOT_APPLICABLE:
            raise ValueError("default_estimate_case must be an estimate case")
        providers = tuple(dict.fromkeys(value.strip().lower() for value in self.canonical_actual_providers))
        if not providers or any(not value for value in providers):
            raise ValueError("canonical_actual_providers must be non-empty")
        object.__setattr__(self, "canonical_actual_providers", providers)
        adjustments = tuple(dict.fromkeys(self.required_additional_adjustments))
        if any(not isinstance(value, EnterpriseAdjustmentRequirement) for value in adjustments):
            raise TypeError("required_additional_adjustments must use EnterpriseAdjustmentRequirement")
        object.__setattr__(self, "required_additional_adjustments", adjustments)
        if self.maximum_cash_debt_date_gap_days is not None and (
            isinstance(self.maximum_cash_debt_date_gap_days, bool)
            or not isinstance(self.maximum_cash_debt_date_gap_days, int)
            or self.maximum_cash_debt_date_gap_days < 0
        ):
            raise ValueError("maximum_cash_debt_date_gap_days must be a non-negative integer")
        if self.maximum_direct_bridge_date_gap_days is not None and (
            isinstance(self.maximum_direct_bridge_date_gap_days, bool)
            or not isinstance(self.maximum_direct_bridge_date_gap_days, int)
            or self.maximum_direct_bridge_date_gap_days < 0
        ):
            raise ValueError("maximum_direct_bridge_date_gap_days must be a non-negative integer")


DEFAULT_OWN_HISTORY_INPUT_POLICY = OwnHistoryInputPolicy(
    policy_id="own-history-inputs-v1-fy1-average-fiscal-actuals",
)


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _provider_symbol(identity: CompanyIdentity, provider: str) -> str | None:
    return next((item.symbol for item in identity.provider_symbols if item.provider == provider), None)


def select_forward_denominator(
    consensus: ForwardConsensus,
    *,
    multiple_type: HistoricalMultipleType,
    period_selection: ForwardPeriodSelection = ForwardPeriodSelection.FY1,
    estimate_case: EstimateCase = EstimateCase.AVERAGE,
) -> ForwardDenominatorSelection:
    """Select exactly one FY1/FY2 annual estimate and preserve all available range cases."""
    if not isinstance(multiple_type, HistoricalMultipleType):
        raise TypeError("multiple_type must use HistoricalMultipleType")
    if not isinstance(period_selection, ForwardPeriodSelection):
        raise TypeError("period_selection must use ForwardPeriodSelection")
    if estimate_case is EstimateCase.NOT_APPLICABLE:
        raise ValueError("estimate_case must be LOW, AVERAGE, or HIGH")
    metric = _FORWARD_METRICS[multiple_type]
    horizon_index = 1 if period_selection is ForwardPeriodSelection.FY1 else 2
    period = consensus.periods[horizon_index - 1] if len(consensus.periods) >= horizon_index else None
    selection_id = stable_forward_selection_id(
        consensus.as_of_at.isoformat(), multiple_type.value, period_selection.value, estimate_case.value,
    )
    if period is None:
        return ForwardDenominatorSelection(
            selection_id=selection_id,
            period_selection=period_selection,
            estimate_case=estimate_case,
            forward_metric_id=metric,
            observation_id=None,
            fiscal_year=None,
            period_start=None,
            period_end=None,
            currency=None,
            provider=None,
            provider_symbol=None,
            reason=f"{period_selection.value} annual consensus period is unavailable",
        )
    available = tuple(item for item in period.observations if item.metric_id is metric)
    chosen = period.observation(metric, estimate_case)
    if chosen is None:
        return ForwardDenominatorSelection(
            selection_id=selection_id,
            period_selection=period_selection,
            estimate_case=estimate_case,
            forward_metric_id=metric,
            observation_id=None,
            fiscal_year=None,
            period_start=None,
            period_end=None,
            currency=None,
            provider=None,
            provider_symbol=None,
            available_estimate_cases=tuple(item.estimate_case for item in available),
            available_observation_ids=tuple(item.observation_id for item in available),
            reason=f"{estimate_case.value} {metric.value} estimate is unavailable for {period_selection.value}",
        )
    return ForwardDenominatorSelection(
        selection_id=selection_id,
        period_selection=period_selection,
        estimate_case=estimate_case,
        forward_metric_id=metric,
        observation_id=chosen.observation_id,
        fiscal_year=chosen.fiscal_year,
        period_start=chosen.period_start,
        period_end=chosen.period_end,
        currency=chosen.currency,
        provider=chosen.provenance.provider,
        provider_symbol=chosen.provenance.provider_symbol,
        provenance=(chosen.provenance,),
        available_estimate_cases=tuple(item.estimate_case for item in available),
        available_observation_ids=tuple(item.observation_id for item in available),
    )


def build_forward_denominator_alignment(
    *,
    multiple_type: HistoricalMultipleType,
    distribution: HistoricalMultipleDistribution | None,
    forward_selection: ForwardDenominatorSelection,
    semantic_evidence: DenominatorSemanticEvidence | None = None,
    policy: OwnHistoryInputPolicy = DEFAULT_OWN_HISTORY_INPUT_POLICY,
) -> ForwardDenominatorAlignment:
    """Classify one economic pairing; matching field names alone never verify it."""
    metric = _FORWARD_METRICS[multiple_type]
    if forward_selection.forward_metric_id is not metric:
        raise ValueError("forward selection metric does not match the historical multiple")
    if distribution is not None and distribution.multiple_type is not multiple_type:
        raise ValueError("distribution multiple_type does not match the requested method")
    denominator = (
        distribution.denominator if distribution is not None else {
            HistoricalMultipleType.P_E: HistoricalValuationDenominator.DILUTED_EPS,
            HistoricalMultipleType.EV_EBITDA: HistoricalValuationDenominator.EBITDA,
            HistoricalMultipleType.EV_EBIT: HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
        }[multiple_type]
    )
    historical_definition = _HISTORICAL_DEFINITIONS[denominator]
    forward_definition = _FORWARD_DEFINITIONS[metric]
    evidence_id = None
    if forward_selection.observation_id is None or distribution is None:
        status = DenominatorCompatibilityStatus.UNAVAILABLE
        reason = "Both a historical distribution and selected forward denominator observation are required."
    elif semantic_evidence is not None:
        expected = (multiple_type, denominator, metric)
        observed = (
            semantic_evidence.historical_multiple_type,
            semantic_evidence.historical_denominator,
            semantic_evidence.forward_metric_id,
        )
        if observed != expected:
            raise ValueError("semantic evidence does not describe the requested denominator pairing")
        status = semantic_evidence.compatibility_status
        reason = semantic_evidence.reason
        historical_definition = semantic_evidence.historical_definition
        forward_definition = semantic_evidence.forward_definition
        evidence_id = semantic_evidence.evidence_id
    elif multiple_type is HistoricalMultipleType.EV_EBITDA:
        status = DenominatorCompatibilityStatus.EXACT
        reason = (
            "The historical denominator and canonical FMP forward metric both use the explicit "
            "EBITDA economic concept under the approved V1 semantic policy."
        )
    elif multiple_type is HistoricalMultipleType.P_E:
        status = DenominatorCompatibilityStatus.UNVERIFIED
        reason = "FMP generic EPS is not proven to be economically identical to Fiscal diluted EPS."
    else:
        status = DenominatorCompatibilityStatus.UNVERIFIED
        reason = "FMP generic EBIT is not proven equivalent to Fiscal's documented Operating Profit denominator."
    provenance = forward_selection.provenance
    supporting_ids = ()
    if forward_selection.observation_id is not None:
        supporting_ids = (forward_selection.observation_id,)
    alignment_id = stable_denominator_alignment_id(
        multiple_type.value,
        denominator.value,
        metric.value,
        status.value,
        distribution.distribution_id if distribution is not None else "",
        forward_selection.selection_id,
        evidence_id or "",
        policy.policy_id,
    )
    return ForwardDenominatorAlignment(
        alignment_id=alignment_id,
        historical_multiple_type=multiple_type,
        historical_denominator=denominator,
        forward_metric_id=metric,
        compatibility_status=status,
        reason=reason,
        historical_definition=historical_definition,
        forward_definition=forward_definition,
        supporting_distribution_id=distribution.distribution_id if distribution is not None else None,
        supporting_observation_ids=supporting_ids,
        provenance=provenance,
        policy_id=policy.policy_id,
        semantic_evidence_id=evidence_id,
    )


def _latest_actual(
    observations: tuple[MetricObservation, ...],
    *,
    metric: MetricId,
    analysis_as_of: datetime,
    providers: tuple[str, ...],
) -> MetricObservation | None:
    candidates = tuple(
        item for item in observations
        if item.metric_id is metric
        and item.observation_type is ObservationType.ACTUAL
        and item.as_of_at <= analysis_as_of
        and item.period_end is not None
        and item.period_end <= analysis_as_of.date()
        and item.provenance.provider.lower() in providers
    )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.period_end, item.as_of_at, item.observation_id))


def build_capital_structure_snapshot(
    identity: CompanyIdentity,
    observations: Iterable[MetricObservation],
    *,
    analysis_as_of: datetime,
    share_basis: ShareCountBasis | None = None,
    policy: OwnHistoryInputPolicy = DEFAULT_OWN_HISTORY_INPUT_POLICY,
) -> CapitalStructureSnapshot:
    """Select latest eligible current actual bridge inputs without zero filling or FX."""
    _aware(analysis_as_of, "analysis_as_of")
    selected_share_basis = share_basis or policy.required_enterprise_share_basis
    if not isinstance(selected_share_basis, ShareCountBasis):
        raise TypeError("share_basis must use ShareCountBasis")
    items = tuple(observations)
    cash = _latest_actual(
        items, metric=MetricId.CASH_AND_EQUIVALENTS, analysis_as_of=analysis_as_of,
        providers=policy.canonical_actual_providers,
    )
    debt = _latest_actual(
        items, metric=MetricId.GROSS_DEBT, analysis_as_of=analysis_as_of,
        providers=policy.canonical_actual_providers,
    )
    shares = _latest_actual(
        items, metric=_SHARE_METRICS[selected_share_basis], analysis_as_of=analysis_as_of,
        providers=policy.canonical_actual_providers,
    )
    warnings: list[str] = []
    missing: list[str] = []
    for item, name, expected_unit in (
        (cash, "cash and equivalents", MetricUnit.CURRENCY),
        (debt, "gross debt", MetricUnit.CURRENCY),
        (shares, f"{selected_share_basis.value} share count", MetricUnit.SHARES),
    ):
        if item is None:
            missing.append(name)
        elif item.unit is not expected_unit:
            missing.append(f"{name} with compatible unit semantics")
            if item is cash:
                cash = None
            elif item is debt:
                debt = None
            else:
                shares = None
    for item, name in ((cash, "cash and equivalents"), (debt, "gross debt")):
        if item is not None and item.value < 0:
            missing.append(f"non-negative {name}")
            if item is cash:
                cash = None
            else:
                debt = None
    if shares is not None and shares.value <= 0:
        missing.append(f"positive {selected_share_basis.value} share count")
        shares = None
    currency = None
    net_debt = None
    if cash is not None and debt is not None:
        if cash.currency == debt.currency and cash.currency is not None:
            currency = cash.currency
            net_debt = debt.value - cash.value
        else:
            missing.append("cash and debt in one compatible currency")
            warnings.append("Cash and gross debt currencies differ; no FX conversion or net-debt derivation was performed.")
    date_gap = None
    if cash is not None and debt is not None:
        date_gap = abs((cash.period_end - debt.period_end).days)
        if date_gap:
            warnings.append(f"Cash and gross debt source dates differ by {date_gap} days.")
    gap_exceeded = (
        policy.maximum_cash_debt_date_gap_days is not None
        and date_gap is not None
        and date_gap > policy.maximum_cash_debt_date_gap_days
    )
    if gap_exceeded:
        missing.append("cash/debt date gap within the explicitly configured maximum")
        warnings.append(
            f"Cash/debt date gap exceeds the configured {policy.maximum_cash_debt_date_gap_days}-day maximum."
        )
    for adjustment in policy.required_additional_adjustments:
        missing.append(f"canonical {adjustment.value} observation")
        warnings.append(
            f"Required {adjustment.value} is unavailable in the approved V1 canonical observation set; it was not assumed to be zero."
        )
    selected = tuple(item for item in (cash, debt, shares) if item is not None)
    if net_debt is not None and shares is not None and not gap_exceeded and not missing:
        completeness = CapitalStructureCompleteness.COMPLETE
    elif selected:
        completeness = CapitalStructureCompleteness.PARTIAL
    else:
        completeness = CapitalStructureCompleteness.UNAVAILABLE
    snapshot_id = stable_capital_structure_id(
        identity.security_id,
        identity.issuer_id,
        analysis_as_of.isoformat(),
        selected_share_basis.value,
        *(item.observation_id for item in selected),
        policy.policy_id,
        *(item.value for item in policy.required_additional_adjustments),
    )
    return CapitalStructureSnapshot(
        snapshot_id=snapshot_id,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of,
        cash_and_equivalents=cash.value if cash is not None else None,
        cash_observation_id=cash.observation_id if cash is not None else None,
        cash_source_date=cash.period_end if cash is not None else None,
        gross_debt=debt.value if debt is not None else None,
        debt_observation_id=debt.observation_id if debt is not None else None,
        debt_source_date=debt.period_end if debt is not None else None,
        net_debt=net_debt,
        shares=shares.value if shares is not None else None,
        share_observation_id=shares.observation_id if shares is not None else None,
        share_source_date=shares.period_end if shares is not None else None,
        share_basis=selected_share_basis,
        currency=currency,
        completeness_status=completeness,
        source_observation_ids=tuple(item.observation_id for item in selected),
        provenance=tuple(item.provenance for item in selected),
        provider_symbols=tuple(item.provenance.provider_symbol for item in selected),
        cash_debt_date_gap_days=date_gap,
        maximum_date_gap_days=policy.maximum_cash_debt_date_gap_days,
        date_gap_exceeded=gap_exceeded,
        missing_requirements=tuple(missing),
        warnings=tuple(warnings),
        policy_id=policy.policy_id,
        required_additional_adjustments=policy.required_additional_adjustments,
    )


def _direct_candidates(
    observations: tuple[MetricObservation, ...],
    *,
    metric: MetricId,
    analysis_as_of: datetime,
    provider_symbol: str | None,
) -> tuple[MetricObservation, ...]:
    if provider_symbol is None:
        return ()
    return tuple(
        item for item in observations
        if item.metric_id is metric
        and item.observation_type is ObservationType.ACTUAL
        and item.frequency is Frequency.POINT_IN_TIME
        and item.as_of_at <= analysis_as_of
        and item.period_end is not None
        and item.period_end <= analysis_as_of.date()
        and item.provenance.provider.lower() == "fiscal"
        and item.provenance.provider_symbol == provider_symbol
    )


def _latest_by_date(items: tuple[MetricObservation, ...]) -> dict[object, MetricObservation]:
    selected: dict[object, MetricObservation] = {}
    for item in sorted(items, key=lambda value: (value.period_end, value.as_of_at, value.observation_id)):
        selected[item.period_end] = item
    return selected


def build_direct_enterprise_equity_bridge(
    identity: CompanyIdentity,
    observations: Iterable[MetricObservation],
    *,
    analysis_as_of: datetime,
    share_count_semantics: ShareCountSemantics | None = None,
    policy: OwnHistoryInputPolicy = DEFAULT_OWN_HISTORY_INPUT_POLICY,
) -> DirectEnterpriseEquityBridge:
    """Build Fiscal TEV minus Fiscal market cap without applying it to a valuation."""
    _aware(analysis_as_of, "analysis_as_of")
    is_depositary = "adr" in identity.security_type.lower() or "depositary" in identity.security_type.lower()
    semantics = share_count_semantics or (
        ShareCountSemantics.UNVERIFIED if is_depositary else ShareCountSemantics.ISSUER_SHARES
    )
    if not isinstance(semantics, ShareCountSemantics):
        raise TypeError("share_count_semantics must use ShareCountSemantics")
    items = tuple(observations)
    fiscal_symbol = _provider_symbol(identity, "fiscal")
    tev_by_date = _latest_by_date(_direct_candidates(
        items,
        metric=MetricId.ENTERPRISE_VALUE,
        analysis_as_of=analysis_as_of,
        provider_symbol=fiscal_symbol,
    ))
    market_cap_by_date = _latest_by_date(_direct_candidates(
        items,
        metric=MetricId.MARKET_CAP,
        analysis_as_of=analysis_as_of,
        provider_symbol=fiscal_symbol,
    ))
    common_dates = tuple(sorted(set(tev_by_date).intersection(market_cap_by_date)))
    if common_dates:
        selected_date = common_dates[-1]
        enterprise_value = tev_by_date[selected_date]
        market_cap = market_cap_by_date[selected_date]
    else:
        enterprise_value = tev_by_date[max(tev_by_date)] if tev_by_date else None
        market_cap = market_cap_by_date[max(market_cap_by_date)] if market_cap_by_date else None
    shares = max(
        _direct_candidates(
            items,
            metric=MetricId.SHARES_OUTSTANDING,
            analysis_as_of=analysis_as_of,
            provider_symbol=fiscal_symbol,
        ),
        key=lambda item: (item.period_end, item.as_of_at, item.observation_id),
        default=None,
    )
    missing: list[str] = []
    warnings: list[str] = []
    issues = []
    for item, name, expected_unit in (
        (enterprise_value, "Fiscal calculated TEV", MetricUnit.CURRENCY),
        (market_cap, "Fiscal calculated market cap", MetricUnit.CURRENCY),
        (shares, "Fiscal total shares outstanding", MetricUnit.SHARES),
    ):
        if item is None:
            missing.append(name)
        elif item.unit is not expected_unit:
            missing.append(f"{name} with compatible unit semantics")
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR,
                metric=item.metric_id,
                provider="own_history_inputs",
                reason=f"{name} has incompatible canonical unit semantics",
            ))
            if item is enterprise_value:
                enterprise_value = None
            elif item is market_cap:
                market_cap = None
            else:
                shares = None
    for item, name in ((enterprise_value, "Fiscal calculated TEV"), (market_cap, "Fiscal calculated market cap")):
        if item is not None and item.value <= 0:
            missing.append(f"positive {name}")
            issues.append(DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric=item.metric_id,
                provider="own_history_inputs",
                reason=f"{name} is non-positive and economically invalid for the direct bridge",
            ))
            if item is enterprise_value:
                enterprise_value = None
            else:
                market_cap = None
    if shares is not None and shares.value <= 0:
        missing.append("positive Fiscal total shares outstanding")
        shares = None
    date_gap = None
    if enterprise_value is not None and market_cap is not None:
        date_gap = abs((enterprise_value.period_end - market_cap.period_end).days)
        if date_gap:
            warnings.append(f"Fiscal TEV and market-cap observation dates differ by {date_gap} days.")
    gap_exceeded = (
        date_gap is not None
        and (
            (policy.maximum_direct_bridge_date_gap_days is None and date_gap != 0)
            or (
                policy.maximum_direct_bridge_date_gap_days is not None
                and date_gap > policy.maximum_direct_bridge_date_gap_days
            )
        )
    )
    if gap_exceeded:
        missing.append("Fiscal TEV and market cap satisfying the explicit observation-date policy")
        warnings.append("Date-mismatched TEV and market cap were not silently combined.")
    currency = None
    adjustment = None
    if enterprise_value is not None and market_cap is not None:
        if enterprise_value.currency == market_cap.currency and enterprise_value.currency is not None:
            currency = enterprise_value.currency
            if not gap_exceeded:
                adjustment = enterprise_value.value - market_cap.value
        else:
            missing.append("Fiscal TEV and market cap in one compatible currency")
            warnings.append("Fiscal TEV and market-cap currencies differ; no FX conversion was performed.")
    if is_depositary:
        share_semantics_suitable = (
            semantics is ShareCountSemantics.ADS_CONVERTED
            or (semantics is ShareCountSemantics.ISSUER_SHARES and identity.adr_ratio is not None)
        )
    else:
        share_semantics_suitable = semantics is ShareCountSemantics.ISSUER_SHARES
    if not share_semantics_suitable:
        missing.append("suitable explicit issuer/ADR share-count semantics")
    selected = tuple(item for item in (enterprise_value, market_cap, shares) if item is not None)
    if adjustment is not None and shares is not None and share_semantics_suitable and not missing:
        completeness = CapitalStructureCompleteness.COMPLETE
    elif selected:
        completeness = CapitalStructureCompleteness.PARTIAL
    else:
        completeness = CapitalStructureCompleteness.UNAVAILABLE
    bridge_id = stable_direct_bridge_id(
        identity.security_id,
        identity.issuer_id,
        analysis_as_of.isoformat(),
        *(item.observation_id for item in selected),
        semantics.value,
        policy.policy_id,
        policy.maximum_direct_bridge_date_gap_days,
    )
    return DirectEnterpriseEquityBridge(
        bridge_id=bridge_id,
        bridge_method=EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of,
        enterprise_value=enterprise_value.value if enterprise_value is not None else None,
        market_cap=market_cap.value if market_cap is not None else None,
        enterprise_equity_adjustment=adjustment,
        currency=currency,
        enterprise_value_observation_id=(enterprise_value.observation_id if enterprise_value is not None else None),
        market_cap_observation_id=market_cap.observation_id if market_cap is not None else None,
        enterprise_value_observation_date=enterprise_value.period_end if enterprise_value is not None else None,
        market_cap_observation_date=market_cap.period_end if market_cap is not None else None,
        provider="fiscal",
        provider_symbols=tuple(item.provenance.provider_symbol for item in selected),
        shares_outstanding=shares.value if shares is not None else None,
        shares_observation_id=shares.observation_id if shares is not None else None,
        shares_observation_date=shares.period_end if shares is not None else None,
        share_basis=ShareCountBasis.SHARES_OUTSTANDING,
        share_count_semantics=semantics,
        completeness_status=completeness,
        observation_date_gap_days=date_gap,
        maximum_date_gap_days=policy.maximum_direct_bridge_date_gap_days,
        date_gap_exceeded=gap_exceeded,
        source_observation_ids=tuple(item.observation_id for item in selected),
        provenance=tuple(item.provenance for item in selected),
        issues=tuple(issues),
        missing_requirements=tuple(missing),
        warnings=tuple(warnings),
        policy_id=policy.policy_id,
    )


def assess_own_history_method_readiness(
    identity: CompanyIdentity,
    distributions: Iterable[HistoricalMultipleDistribution],
    consensus: ForwardConsensus,
    *,
    multiple_type: HistoricalMultipleType,
    analysis_as_of: datetime,
    capital_structure_snapshot: CapitalStructureSnapshot | None = None,
    direct_enterprise_bridge: DirectEnterpriseEquityBridge | None = None,
    selected_window: HistoricalWindow | None = None,
    selected_forward_period: ForwardPeriodSelection | None = None,
    estimate_case: EstimateCase | None = None,
    semantic_evidence: DenominatorSemanticEvidence | None = None,
    policy: OwnHistoryInputPolicy = DEFAULT_OWN_HISTORY_INPUT_POLICY,
) -> OwnHistoryMethodReadiness:
    """Assess one method independently; no multiple or denominator is applied."""
    _aware(analysis_as_of, "analysis_as_of")
    window = selected_window or policy.default_window
    forward_period = selected_forward_period or policy.default_forward_period
    selected_case = estimate_case or policy.default_estimate_case
    matching = tuple(
        item for item in distributions
        if item.multiple_type is multiple_type
        and item.window is window
        and item.security_id == identity.security_id
        and item.issuer_id == identity.issuer_id
    )
    if len(matching) > 1:
        raise ValueError("more than one distribution matches the explicit method/window/identity")
    distribution = matching[0] if matching else None
    selection = select_forward_denominator(
        consensus,
        multiple_type=multiple_type,
        period_selection=forward_period,
        estimate_case=selected_case,
    )
    alignment = build_forward_denominator_alignment(
        multiple_type=multiple_type,
        distribution=distribution,
        forward_selection=selection,
        semantic_evidence=semantic_evidence,
        policy=policy,
    )
    hard_blocks: list[str] = []
    bridge_blocks: list[str] = []
    warnings: list[str] = []
    if distribution is None:
        hard_blocks.append(f"No {window.value} {multiple_type.value} historical distribution is available.")
    elif distribution.analysis_as_of != analysis_as_of:
        hard_blocks.append("Historical distribution analysis_as_of does not match the readiness snapshot.")
    elif distribution.usability is not HistoricalDistributionUsability.USABLE:
        hard_blocks.append(
            f"Historical distribution is {distribution.usability.value}; only USABLE distributions are eligible."
        )
    if consensus.as_of_at != analysis_as_of:
        hard_blocks.append("Forward consensus analysis_as_of does not match the readiness snapshot.")
    if selection.observation_id is None:
        hard_blocks.append(selection.reason or "Selected forward denominator is unavailable.")
    if alignment.compatibility_status not in {
        DenominatorCompatibilityStatus.EXACT,
        DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT,
    }:
        hard_blocks.append(
            f"Denominator compatibility is {alignment.compatibility_status.value}: {alignment.reason}"
        )
    if selection.currency is not None and selection.currency != identity.reporting_currency:
        hard_blocks.append("Forward denominator currency does not match canonical reporting currency; no FX conversion is allowed.")
    expected_fmp_symbol = _provider_symbol(identity, "fmp")
    if selection.provider_symbol is not None and (
        expected_fmp_symbol is None or selection.provider_symbol != expected_fmp_symbol
    ):
        hard_blocks.append("Forward observation provider symbol does not match canonical identity provenance.")
    is_depositary = "adr" in identity.security_type.lower() or "depositary" in identity.security_type.lower()
    enterprise_method = multiple_type in {HistoricalMultipleType.EV_EBITDA, HistoricalMultipleType.EV_EBIT}
    selected_component = None
    selected_direct = None
    selected_bridge_method = None
    if enterprise_method:
        if (
            direct_enterprise_bridge is not None
            and direct_enterprise_bridge.completeness_status is CapitalStructureCompleteness.COMPLETE
        ):
            selected_direct = direct_enterprise_bridge
            selected_bridge_method = EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
        elif (
            capital_structure_snapshot is not None
            and capital_structure_snapshot.completeness_status is CapitalStructureCompleteness.COMPLETE
        ):
            selected_component = capital_structure_snapshot
            selected_bridge_method = EnterpriseBridgeMethod.COMPONENT_BRIDGE
        elif direct_enterprise_bridge is not None:
            selected_direct = direct_enterprise_bridge
            selected_bridge_method = EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
        elif capital_structure_snapshot is not None:
            selected_component = capital_structure_snapshot
            selected_bridge_method = EnterpriseBridgeMethod.COMPONENT_BRIDGE
        else:
            bridge_blocks.append("A current component or direct Fiscal enterprise bridge is required.")
        if selected_component is not None:
            if (
                selected_component.security_id != identity.security_id
                or selected_component.issuer_id != identity.issuer_id
            ):
                hard_blocks.append("Capital-structure snapshot does not match canonical security and issuer identity.")
            if selected_component.analysis_as_of != analysis_as_of:
                hard_blocks.append("Capital-structure snapshot analysis_as_of does not match method readiness.")
            if selected_component.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                bridge_blocks.extend(selected_component.missing_requirements or (
                    "Capital-structure snapshot is incomplete.",
                ))
            if (
                selection.currency is not None
                and selected_component.currency is not None
                and selection.currency != selected_component.currency
            ):
                hard_blocks.append("Forward denominator and enterprise bridge currencies differ; no FX conversion is allowed.")
            if selected_component.share_basis is not policy.required_enterprise_share_basis:
                bridge_blocks.append(
                    f"Enterprise method requires explicit {policy.required_enterprise_share_basis.value} share basis."
                )
            warnings.extend(selected_component.warnings)
        if selected_direct is not None:
            if selected_direct.provider.lower() != "fiscal":
                hard_blocks.append("Direct TEV-market-cap bridge must use Fiscal evidence only.")
            if selected_direct.security_id != identity.security_id or selected_direct.issuer_id != identity.issuer_id:
                hard_blocks.append("Direct bridge does not match canonical security and issuer identity.")
            if selected_direct.analysis_as_of != analysis_as_of:
                hard_blocks.append("Direct bridge analysis_as_of does not match method readiness.")
            if selected_direct.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                bridge_blocks.extend(selected_direct.missing_requirements or (
                    "Direct Fiscal enterprise bridge is incomplete.",
                ))
            if selection.currency is not None and selected_direct.currency is not None and selection.currency != selected_direct.currency:
                hard_blocks.append("Forward denominator and direct bridge currencies differ; no FX conversion is allowed.")
            if selected_direct.share_basis is not policy.required_enterprise_share_basis:
                bridge_blocks.append(
                    f"Enterprise method requires explicit {policy.required_enterprise_share_basis.value} share basis."
                )
            warnings.extend(selected_direct.warnings)
        if capital_structure_snapshot is not None and direct_enterprise_bridge is not None:
            warnings.append("Component and direct bridges remain separate; no bridge average or consensus was created.")
        adr_satisfied_by_direct = (
            selected_direct is not None
            and selected_direct.share_count_semantics is ShareCountSemantics.ADS_CONVERTED
        )
        if is_depositary and identity.adr_ratio is None and not adr_satisfied_by_direct:
            hard_blocks.append("ADR/share-class conversion is required but no explicit ratio or ADS-converted shares exist.")
    elif capital_structure_snapshot is not None or direct_enterprise_bridge is not None:
        warnings.append("P/E is equity-based; the supplied enterprise bridge was not required for readiness.")
    if not enterprise_method and is_depositary and identity.adr_ratio is None:
        hard_blocks.append("ADR/share-class conversion is required but the canonical ADR ratio is unavailable.")
    if selected_case is not EstimateCase.AVERAGE:
        warnings.append("A non-average analyst range case was selected explicitly; it is not a valuation scenario.")
    if hard_blocks:
        status = OwnHistoryMethodStatus.NOT_READY
        blockers = (*hard_blocks, *bridge_blocks)
    elif bridge_blocks:
        status = OwnHistoryMethodStatus.PARTIAL
        blockers = tuple(bridge_blocks)
    else:
        status = OwnHistoryMethodStatus.READY
        blockers = ()
    supporting_ids = tuple(dict.fromkeys(
        (*(() if distribution is None else distribution.supporting_observation_ids),
         *((selection.observation_id,) if selection.observation_id is not None else ()),
         *(() if selected_component is None else selected_component.source_observation_ids),
         *(() if selected_direct is None else selected_direct.source_observation_ids))
    ))
    provenance = tuple(dict.fromkeys(
        (
            *selection.provenance,
            *(() if selected_component is None else selected_component.provenance),
            *(() if selected_direct is None else selected_direct.provenance),
        )
    ))
    readiness_id = stable_own_history_readiness_id(
        identity.security_id,
        identity.issuer_id,
        analysis_as_of.isoformat(),
        multiple_type.value,
        window.value,
        forward_period.value,
        selected_case.value,
        distribution.distribution_id if distribution is not None else "",
        selection.selection_id,
        selected_component.snapshot_id if selected_component is not None else "",
        selected_direct.bridge_id if selected_direct is not None else "",
        policy.policy_id,
    )
    return OwnHistoryMethodReadiness(
        readiness_id=readiness_id,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of,
        multiple_type=multiple_type,
        selected_window=window,
        selected_forward_period=forward_period,
        estimate_case=selected_case,
        distribution_id=distribution.distribution_id if distribution is not None else None,
        forward_selection=selection,
        denominator_alignment=alignment,
        capital_structure_snapshot_id=selected_component.snapshot_id if selected_component is not None else None,
        status=status,
        blocking_reasons=tuple(blockers),
        warnings=tuple(warnings),
        supporting_observation_ids=supporting_ids,
        provenance=provenance,
        policy_id=policy.policy_id,
        enterprise_equity_bridge_id=selected_direct.bridge_id if selected_direct is not None else None,
        bridge_method=selected_bridge_method,
        selected_share_basis=(
            selected_direct.share_basis if selected_direct is not None
            else selected_component.share_basis if selected_component is not None
            else None
        ),
        selected_share_observation_id=(
            selected_direct.shares_observation_id if selected_direct is not None
            else selected_component.share_observation_id if selected_component is not None
            else None
        ),
    )
