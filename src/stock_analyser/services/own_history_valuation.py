"""Canonical-only numeric application of one READY own-history method."""

from __future__ import annotations

from dataclasses import dataclass
import math

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CapitalStructureSnapshot,
    DataIssue,
    DenominatorCompatibilityStatus,
    DirectEnterpriseEquityBridge,
    EnterpriseBridgeMethod,
    EstimateCase,
    Frequency,
    HistoricalDistributionUsability,
    HistoricalMultipleDistribution,
    HistoricalMultipleType,
    HistoricalStatistic,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    OwnHistoryMethodReadiness,
    OwnHistoryMethodStatus,
    OwnHistoryValuationPoint,
    OwnHistoryValuationResult,
    Provenance,
    ValuationBasis,
    ValuationMethodStatus,
    ValuationResult,
    stable_own_history_point_id,
    stable_own_history_valuation_id,
)


_EXPECTED_FORWARD = {
    HistoricalMultipleType.P_E: (MetricId.EPS, MetricUnit.CURRENCY_PER_SHARE),
    HistoricalMultipleType.EV_EBITDA: (MetricId.EBITDA, MetricUnit.CURRENCY),
    HistoricalMultipleType.EV_EBIT: (MetricId.EBIT, MetricUnit.CURRENCY),
}
_STATISTICS = (
    (HistoricalStatistic.P25, "p25"),
    (HistoricalStatistic.MEDIAN, "median"),
    (HistoricalStatistic.P75, "p75"),
)


@dataclass(frozen=True, slots=True)
class OwnHistoryValuationPolicy:
    policy_id: str = "own-history-valuation-v1-historical-band-average-consensus"

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        object.__setattr__(self, "policy_id", self.policy_id.strip())


DEFAULT_OWN_HISTORY_VALUATION_POLICY = OwnHistoryValuationPolicy()


def _issue(reason: str, multiple_type: HistoricalMultipleType) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.BLOCKING,
        metric=multiple_type.value,
        provider="own_history_valuation",
        reason=reason,
    )


def _dedupe(items):
    return tuple(dict.fromkeys(items))


def _point_provenance(
    readiness: OwnHistoryMethodReadiness,
    forward: MetricObservation,
    statistic: HistoricalStatistic,
    supporting_ids: tuple[str, ...],
    policy: OwnHistoryValuationPolicy,
    *,
    enterprise: bool,
) -> tuple[Provenance, ...]:
    inputs = _dedupe((*readiness.provenance, forward.provenance))
    retrieved_at = max(item.retrieved_at for item in inputs)
    formula = (
        "historical multiple times average forward denominator, less one approved enterprise adjustment, divided by approved shares"
        if enterprise
        else "historical P/E multiple times average forward EPS"
    )
    derived = Provenance(
        provider="derived",
        endpoint_or_dataset="v1_own_history_numeric_valuation",
        provider_symbol=readiness.security_id,
        retrieved_at=retrieved_at,
        as_of_at=readiness.analysis_as_of,
        transformation_steps=(
            f"applied unrounded historical {statistic.value} statistic from one selected distribution",
            "used selected FMP AVERAGE annual forward denominator; analyst low/high were not used",
            formula,
        ),
        input_observation_ids=supporting_ids,
        configuration_or_override_id=policy.policy_id,
        source_metric=readiness.multiple_type.value,
    )
    return _dedupe((*inputs, derived))


def _unavailable_points(
    readiness: OwnHistoryMethodReadiness,
    distribution: HistoricalMultipleDistribution,
    forward: MetricObservation,
    issues: tuple[DataIssue, ...],
    supporting_ids: tuple[str, ...],
    policy: OwnHistoryValuationPolicy,
) -> tuple[OwnHistoryValuationPoint, OwnHistoryValuationPoint, OwnHistoryValuationPoint]:
    points = []
    for statistic, attribute in _STATISTICS:
        raw_multiple = getattr(distribution, attribute)
        multiple = (
            float(raw_multiple)
            if raw_multiple is not None
            and not isinstance(raw_multiple, bool)
            and math.isfinite(float(raw_multiple))
            and raw_multiple > 0
            else None
        )
        denominator = (
            float(forward.value)
            if not isinstance(forward.value, bool)
            and math.isfinite(float(forward.value))
            and forward.value > 0
            else None
        )
        point_id = stable_own_history_point_id(
            readiness.readiness_id, statistic.value, forward.observation_id, policy.policy_id, "unavailable"
        )
        points.append(OwnHistoryValuationPoint(
            point_id=point_id,
            statistic=statistic,
            multiple_type=readiness.multiple_type,
            valuation_basis=distribution.valuation_basis,
            analysis_as_of=readiness.analysis_as_of,
            selected_forward_period=readiness.selected_forward_period,
            estimate_case=EstimateCase.AVERAGE,
            distribution_id=distribution.distribution_id,
            forward_observation_id=forward.observation_id,
            historical_multiple=multiple,
            forward_denominator=denominator,
            implied_enterprise_value=None,
            enterprise_equity_adjustment=None,
            implied_equity_value=None,
            share_count=None,
            share_basis=None,
            share_observation_id=None,
            per_share_value=None,
            currency=forward.currency,
            status=ValuationMethodStatus.UNAVAILABLE,
            bridge_id=None,
            supporting_observation_ids=supporting_ids,
            provenance=_point_provenance(
                readiness, forward, statistic, supporting_ids, policy,
                enterprise=distribution.valuation_basis is ValuationBasis.ENTERPRISE,
            ),
            issues=issues,
            policy_id=policy.policy_id,
        ))
    return tuple(points)


def _make_result(
    readiness: OwnHistoryMethodReadiness,
    distribution: HistoricalMultipleDistribution,
    points: tuple[OwnHistoryValuationPoint, OwnHistoryValuationPoint, OwnHistoryValuationPoint],
    supporting_ids: tuple[str, ...],
    policy: OwnHistoryValuationPolicy,
    *,
    bridge_id: str | None,
    warnings: tuple[str, ...],
) -> OwnHistoryValuationResult:
    valid = tuple(point.status is ValuationMethodStatus.VALID for point in points)
    if all(valid):
        status = ValuationMethodStatus.VALID
    elif any(valid):
        status = ValuationMethodStatus.PARTIAL
    else:
        status = ValuationMethodStatus.UNAVAILABLE
    values = tuple(point.per_share_value if is_valid else None for point, is_valid in zip(points, valid))
    issues = _dedupe(issue for point in points for issue in point.issues)
    provenance = _dedupe(item for point in points for item in point.provenance)
    reason = issues[0].reason if status is ValuationMethodStatus.UNAVAILABLE and issues else None
    generic = ValuationResult(
        method=readiness.multiple_type.value,
        valuation_family="own_history",
        currency=points[0].currency,
        status=status,
        low=values[0],
        central=values[1],
        high=values[2],
        input_observation_ids=supporting_ids,
        provenance=provenance,
        issues=issues,
        warnings=warnings,
        reason=reason,
    )
    result_id = stable_own_history_valuation_id(
        readiness.readiness_id,
        distribution.distribution_id,
        *(point.point_id for point in points),
        bridge_id or "",
        policy.policy_id,
    )
    return OwnHistoryValuationResult(
        result_id=result_id,
        security_id=readiness.security_id,
        issuer_id=readiness.issuer_id,
        analysis_as_of=readiness.analysis_as_of,
        multiple_type=readiness.multiple_type,
        selected_window=readiness.selected_window,
        selected_forward_period=readiness.selected_forward_period,
        estimate_case=EstimateCase.AVERAGE,
        distribution_id=distribution.distribution_id,
        readiness_id=readiness.readiness_id,
        lower_point=points[0],
        central_point=points[1],
        upper_point=points[2],
        currency=points[0].currency,
        status=status,
        issues=issues,
        warnings=warnings,
        supporting_observation_ids=supporting_ids,
        supporting_distribution_id=distribution.distribution_id,
        bridge_id=bridge_id,
        policy_id=policy.policy_id,
        provenance=provenance,
        valuation_result=generic,
    )


def calculate_own_history_valuation(
    readiness: OwnHistoryMethodReadiness,
    distribution: HistoricalMultipleDistribution,
    forward_denominator: MetricObservation,
    *,
    capital_structure_snapshot: CapitalStructureSnapshot | None = None,
    direct_enterprise_bridge: DirectEnterpriseEquityBridge | None = None,
    policy: OwnHistoryValuationPolicy = DEFAULT_OWN_HISTORY_VALUATION_POLICY,
) -> OwnHistoryValuationResult:
    """Apply P25/median/P75 to one selected AVERAGE denominator without aggregation."""
    for value, expected, name in (
        (readiness, OwnHistoryMethodReadiness, "readiness"),
        (distribution, HistoricalMultipleDistribution, "distribution"),
        (forward_denominator, MetricObservation, "forward_denominator"),
        (policy, OwnHistoryValuationPolicy, "policy"),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} must use {expected.__name__}")

    issues: list[DataIssue] = []
    expected_metric, expected_unit = _EXPECTED_FORWARD[readiness.multiple_type]
    selection = readiness.forward_selection
    alignment = readiness.denominator_alignment

    if readiness.status is not OwnHistoryMethodStatus.READY:
        issues.append(_issue(
            f"Own-history prerequisite is {readiness.status.value}; numeric valuation requires READY.",
            readiness.multiple_type,
        ))
    if readiness.estimate_case is not EstimateCase.AVERAGE or selection.estimate_case is not EstimateCase.AVERAGE:
        issues.append(_issue("Numeric own-history valuation requires the selected AVERAGE estimate case.", readiness.multiple_type))
    if readiness.distribution_id != distribution.distribution_id:
        issues.append(_issue("The supplied distribution is not the distribution selected by readiness.", readiness.multiple_type))
    if (
        distribution.security_id != readiness.security_id
        or distribution.issuer_id != readiness.issuer_id
        or distribution.multiple_type is not readiness.multiple_type
    ):
        issues.append(_issue("Distribution identity or multiple type does not match readiness.", readiness.multiple_type))
    if distribution.window is not readiness.selected_window:
        issues.append(_issue("Distribution window does not match the single window selected by readiness.", readiness.multiple_type))
    if distribution.analysis_as_of != readiness.analysis_as_of:
        issues.append(_issue("Distribution analysis_as_of does not match readiness.", readiness.multiple_type))
    if distribution.usability is not HistoricalDistributionUsability.USABLE:
        issues.append(_issue("Numeric valuation requires a USABLE historical distribution.", readiness.multiple_type))
    statistics = (distribution.p25, distribution.median, distribution.p75)
    if any(
        value is None or isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0
        for value in statistics
    ):
        issues.append(_issue("P25, median, and P75 must all be finite and positive.", readiness.multiple_type))
    elif not distribution.p25 <= distribution.median <= distribution.p75:
        issues.append(_issue("Historical statistics must satisfy P25 <= median <= P75; they were not reordered.", readiness.multiple_type))

    if selection.observation_id != forward_denominator.observation_id:
        issues.append(_issue("Forward observation does not match the exact readiness selection.", readiness.multiple_type))
    if forward_denominator.metric_id is not expected_metric or selection.forward_metric_id is not expected_metric:
        issues.append(_issue("Forward metric does not match the selected historical multiple denominator.", readiness.multiple_type))
    if forward_denominator.unit is not expected_unit:
        issues.append(_issue("Forward denominator lacks the required normalized canonical monetary unit.", readiness.multiple_type))
    if (
        forward_denominator.frequency is not Frequency.ANNUAL
        or forward_denominator.observation_type is not ObservationType.ESTIMATE
        or forward_denominator.estimate_case is not EstimateCase.AVERAGE
    ):
        issues.append(_issue("Forward denominator must be one annual AVERAGE estimate; FY selections are not NTM.", readiness.multiple_type))
    if (
        forward_denominator.period_start != selection.period_start
        or forward_denominator.period_end != selection.period_end
        or forward_denominator.fiscal_year != selection.fiscal_year
    ):
        issues.append(_issue("Forward fiscal period does not match the exact FY selection.", readiness.multiple_type))
    if (
        forward_denominator.currency != selection.currency
        or forward_denominator.provenance.provider != selection.provider
        or forward_denominator.provenance.provider_symbol != selection.provider_symbol
    ):
        issues.append(_issue("Forward currency/provider provenance does not match readiness selection.", readiness.multiple_type))
    if forward_denominator.as_of_at != readiness.analysis_as_of:
        issues.append(_issue("Forward denominator as-of does not match readiness analysis_as_of.", readiness.multiple_type))
    if (
        isinstance(forward_denominator.value, bool)
        or not math.isfinite(float(forward_denominator.value))
        or forward_denominator.value <= 0
    ):
        issues.append(_issue("Selected forward denominator must be finite and positive.", readiness.multiple_type))
    if alignment.compatibility_status not in {
        DenominatorCompatibilityStatus.EXACT,
        DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT,
    }:
        issues.append(_issue("Historical and forward denominator economics are not verified compatible.", readiness.multiple_type))
    if (
        alignment.historical_multiple_type is not readiness.multiple_type
        or alignment.supporting_distribution_id != distribution.distribution_id
        or forward_denominator.observation_id not in alignment.supporting_observation_ids
    ):
        issues.append(_issue("Denominator alignment evidence does not match the selected inputs.", readiness.multiple_type))

    enterprise = distribution.valuation_basis is ValuationBasis.ENTERPRISE
    bridge_id = None
    adjustment = None
    shares = None
    share_basis = None
    share_observation_id = None
    bridge_source_ids: tuple[str, ...] = ()
    bridge_warnings: tuple[str, ...] = ()

    if enterprise:
        if readiness.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE:
            if capital_structure_snapshot is not None or direct_enterprise_bridge is None:
                issues.append(_issue("Exactly the selected direct Fiscal bridge is required.", readiness.multiple_type))
            else:
                bridge = direct_enterprise_bridge
                bridge_id = bridge.bridge_id
                adjustment = bridge.enterprise_equity_adjustment
                shares = bridge.shares_outstanding
                share_basis = bridge.share_basis
                share_observation_id = bridge.shares_observation_id
                bridge_source_ids = bridge.source_observation_ids
                bridge_warnings = bridge.warnings
                if bridge.bridge_id != readiness.enterprise_equity_bridge_id:
                    issues.append(_issue("Direct bridge ID does not match readiness.", readiness.multiple_type))
                if bridge.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                    issues.append(_issue("Direct enterprise bridge is not complete.", readiness.multiple_type))
                if (
                    bridge.security_id != readiness.security_id
                    or bridge.issuer_id != readiness.issuer_id
                    or bridge.analysis_as_of != readiness.analysis_as_of
                ):
                    issues.append(_issue("Direct bridge identity or analysis timestamp does not match readiness.", readiness.multiple_type))
                if bridge.currency != forward_denominator.currency:
                    issues.append(_issue("Forward denominator and direct bridge currencies differ; no FX was performed.", readiness.multiple_type))
        elif readiness.bridge_method is EnterpriseBridgeMethod.COMPONENT_BRIDGE:
            if direct_enterprise_bridge is not None or capital_structure_snapshot is None:
                issues.append(_issue("Exactly the selected component bridge is required.", readiness.multiple_type))
            else:
                bridge = capital_structure_snapshot
                bridge_id = bridge.snapshot_id
                adjustment = bridge.net_debt
                shares = bridge.shares
                share_basis = bridge.share_basis
                share_observation_id = bridge.share_observation_id
                bridge_source_ids = bridge.source_observation_ids
                bridge_warnings = bridge.warnings
                if bridge.snapshot_id != readiness.capital_structure_snapshot_id:
                    issues.append(_issue("Component bridge ID does not match readiness.", readiness.multiple_type))
                if bridge.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                    issues.append(_issue("Component enterprise bridge is not complete.", readiness.multiple_type))
                if (
                    bridge.security_id != readiness.security_id
                    or bridge.issuer_id != readiness.issuer_id
                    or bridge.analysis_as_of != readiness.analysis_as_of
                ):
                    issues.append(_issue("Component bridge identity or analysis timestamp does not match readiness.", readiness.multiple_type))
                if bridge.currency != forward_denominator.currency:
                    issues.append(_issue("Forward denominator and component bridge currencies differ; no FX was performed.", readiness.multiple_type))
        else:
            issues.append(_issue("Enterprise valuation requires the controlled bridge selected by readiness.", readiness.multiple_type))
        if share_basis is not readiness.selected_share_basis or share_observation_id != readiness.selected_share_observation_id:
            issues.append(_issue("Share count/basis does not match the exact evidence approved by readiness.", readiness.multiple_type))
        if shares is None or isinstance(shares, bool) or not math.isfinite(float(shares)) or shares <= 0:
            issues.append(_issue("Approved share count must be finite and positive.", readiness.multiple_type))
        if adjustment is None or isinstance(adjustment, bool) or not math.isfinite(float(adjustment)):
            issues.append(_issue("Enterprise-to-equity adjustment must be finite and complete.", readiness.multiple_type))
    else:
        if readiness.bridge_method is not None or capital_structure_snapshot is not None or direct_enterprise_bridge is not None:
            issues.append(_issue("Equity-basis P/E must not consume an enterprise bridge.", readiness.multiple_type))

    supporting_ids = _dedupe((
        *distribution.supporting_observation_ids,
        forward_denominator.observation_id,
        *bridge_source_ids,
    ))
    warnings = _dedupe((*readiness.warnings, *bridge_warnings))
    if issues:
        points = _unavailable_points(
            readiness, distribution, forward_denominator, tuple(issues), supporting_ids, policy,
        )
        return _make_result(
            readiness, distribution, points, supporting_ids, policy,
            bridge_id=None, warnings=warnings,
        )

    points: list[OwnHistoryValuationPoint] = []
    for statistic, attribute in _STATISTICS:
        multiple = float(getattr(distribution, attribute))
        denominator = float(forward_denominator.value)
        point_issues: list[DataIssue] = []
        implied_enterprise_value = None
        implied_equity_value = None
        per_share_value = None
        if enterprise:
            implied_enterprise_value = multiple * denominator
            if not math.isfinite(implied_enterprise_value):
                point_issues.append(_issue("Implied enterprise value is non-finite.", readiness.multiple_type))
            else:
                implied_equity_value = implied_enterprise_value - adjustment
                if not math.isfinite(implied_equity_value):
                    point_issues.append(_issue("Implied equity value is non-finite.", readiness.multiple_type))
                elif implied_equity_value <= 0:
                    point_issues.append(_issue(
                        "Implied equity value is non-positive; it was not floored to zero.",
                        readiness.multiple_type,
                    ))
                else:
                    per_share_value = implied_equity_value / shares
                    if not math.isfinite(per_share_value) or per_share_value <= 0:
                        point_issues.append(_issue("Per-share value is non-finite or non-positive.", readiness.multiple_type))
                        per_share_value = None
        else:
            per_share_value = multiple * denominator
            if not math.isfinite(per_share_value) or per_share_value <= 0:
                point_issues.append(_issue("P/E per-share value is non-finite or non-positive.", readiness.multiple_type))
                per_share_value = None
        status = ValuationMethodStatus.UNAVAILABLE if point_issues else ValuationMethodStatus.VALID
        point_id = stable_own_history_point_id(
            readiness.readiness_id,
            statistic.value,
            distribution.distribution_id,
            forward_denominator.observation_id,
            bridge_id or "",
            policy.policy_id,
        )
        points.append(OwnHistoryValuationPoint(
            point_id=point_id,
            statistic=statistic,
            multiple_type=readiness.multiple_type,
            valuation_basis=distribution.valuation_basis,
            analysis_as_of=readiness.analysis_as_of,
            selected_forward_period=readiness.selected_forward_period,
            estimate_case=EstimateCase.AVERAGE,
            distribution_id=distribution.distribution_id,
            forward_observation_id=forward_denominator.observation_id,
            historical_multiple=multiple,
            forward_denominator=denominator,
            implied_enterprise_value=implied_enterprise_value,
            enterprise_equity_adjustment=adjustment if enterprise else None,
            implied_equity_value=implied_equity_value,
            share_count=shares if enterprise else None,
            share_basis=share_basis if enterprise else None,
            share_observation_id=share_observation_id if enterprise else None,
            per_share_value=per_share_value,
            currency=forward_denominator.currency,
            status=status,
            bridge_id=bridge_id if enterprise else None,
            supporting_observation_ids=supporting_ids,
            provenance=_point_provenance(
                readiness, forward_denominator, statistic, supporting_ids, policy,
                enterprise=enterprise,
            ),
            issues=tuple(point_issues),
            policy_id=policy.policy_id,
        ))

    valid_values = tuple(
        point.per_share_value for point in points if point.status is ValuationMethodStatus.VALID
    )
    if any(left > right for left, right in zip(valid_values, valid_values[1:])):
        ordering_issue = (_issue(
            "Calculated valid points violate lower <= central <= upper; outputs were not sorted.",
            readiness.multiple_type,
        ),)
        unavailable = _unavailable_points(
            readiness, distribution, forward_denominator, ordering_issue, supporting_ids, policy,
        )
        return _make_result(
            readiness, distribution, unavailable, supporting_ids, policy,
            bridge_id=None, warnings=warnings,
        )
    return _make_result(
        readiness, distribution, tuple(points), supporting_ids, policy,
        bridge_id=bridge_id, warnings=warnings,
    )
