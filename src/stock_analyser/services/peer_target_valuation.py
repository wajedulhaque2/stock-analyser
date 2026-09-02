"""Pure target-side application of one usable peer EV/EBITDA distribution."""

from __future__ import annotations

from dataclasses import dataclass
import math

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CapitalStructureSnapshot,
    DataIssue,
    DirectEnterpriseEquityBridge,
    EnterpriseBridgeMethod,
    EstimateCase,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerDistributionStatus,
    PeerMethod,
    PeerMultipleDistribution,
    PeerTargetValuationPoint,
    PeerTargetValuationResult,
    PeerTargetValuationSelection,
    PeerValuationStatistic,
    Provenance,
    ShareCountSemantics,
    ValuationBasis,
    ValuationMethodStatus,
    ValuationResult,
    stable_peer_target_point_id,
    stable_peer_target_valuation_id,
)


@dataclass(frozen=True, slots=True)
class PeerTargetValuationPolicy:
    policy_id: str = "peer-target-ev-ebitda-valuation-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        object.__setattr__(self, "policy_id", self.policy_id.strip())


DEFAULT_PEER_TARGET_VALUATION_POLICY = PeerTargetValuationPolicy()

_STATISTICS = (
    (PeerValuationStatistic.P25, "p25"),
    (PeerValuationStatistic.MEDIAN, "median"),
    (PeerValuationStatistic.P75, "p75"),
)


def _issue(reason: str) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.BLOCKING,
        metric=PeerMethod.EV_EBITDA.value,
        provider="peer_target_valuation",
        reason=reason,
    )


def _dedupe(items):
    return tuple(dict.fromkeys(items))


def _point_free_result(
    distribution: PeerMultipleDistribution,
    issues: tuple[DataIssue, ...],
    policy: PeerTargetValuationPolicy,
) -> PeerTargetValuationResult:
    supporting_ids = tuple(distribution.valid_observation_ids)
    return PeerTargetValuationResult(
        result_id=stable_peer_target_valuation_id(
            distribution.distribution_id, "unavailable", policy.policy_id,
        ),
        target_security_id=distribution.target_security_id,
        target_issuer_id=distribution.target_issuer_id,
        analysis_as_of=distribution.analysis_as_of,
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        distribution_id=distribution.distribution_id,
        peer_set_id=distribution.peer_set_id,
        peer_valuation_subset_id=distribution.peer_valuation_subset_id,
        selected_forward_period=distribution.forward_period_policy,
        estimate_case=EstimateCase.AVERAGE,
        lower_point=None,
        central_point=None,
        upper_point=None,
        currency=None,
        bridge_method=None,
        bridge_id=None,
        share_basis=None,
        share_observation_id=None,
        target_forward_observation_id=None,
        target_forward_period_end=None,
        status=ValuationMethodStatus.UNAVAILABLE,
        issues=issues,
        warnings=distribution.warnings,
        supporting_observation_ids=supporting_ids,
        policy_id=policy.policy_id,
        provenance=distribution.provenance,
        valuation_result=None,
    )


def _derived_provenance(
    distribution: PeerMultipleDistribution,
    forward: MetricObservation,
    bridge_provenance: tuple[Provenance, ...],
    statistic: PeerValuationStatistic,
    supporting_ids: tuple[str, ...],
    policy: PeerTargetValuationPolicy,
) -> tuple[Provenance, ...]:
    inputs = _dedupe((*distribution.provenance, forward.provenance, *bridge_provenance))
    derived = Provenance(
        provider="derived",
        endpoint_or_dataset="v1_peer_target_ev_ebitda_valuation",
        provider_symbol=distribution.target_security_id,
        retrieved_at=max(item.retrieved_at for item in inputs),
        as_of_at=distribution.analysis_as_of,
        transformation_steps=(
            f"applied unrounded peer {statistic.value} EV/EBITDA statistic from one 8C distribution",
            "used one target annual AVERAGE EBITDA aligned to the peer FY policy; LOW/HIGH and NTM were not used",
            "calculated implied enterprise value, subtracted exactly one approved enterprise-equity adjustment, and divided by approved shares",
            "performed no FX, provider-specific scaling, GBP-to-GBp conversion, current-price comparison, or aggregation",
        ),
        input_observation_ids=supporting_ids,
        configuration_or_override_id=policy.policy_id,
        source_metric=PeerMethod.EV_EBITDA.value,
    )
    return _dedupe((*inputs, derived))


def calculate_peer_target_valuation(
    distribution: PeerMultipleDistribution,
    selection: PeerTargetValuationSelection | None = None,
    target_forward_ebitda: MetricObservation | None = None,
    *,
    capital_structure_snapshot: CapitalStructureSnapshot | None = None,
    direct_enterprise_bridge: DirectEnterpriseEquityBridge | None = None,
    policy: PeerTargetValuationPolicy = DEFAULT_PEER_TARGET_VALUATION_POLICY,
) -> PeerTargetValuationResult:
    """Apply peer P25/median/P75 to one aligned target EBITDA and one bridge."""
    if not isinstance(distribution, PeerMultipleDistribution):
        raise TypeError("distribution must use PeerMultipleDistribution")
    if not isinstance(policy, PeerTargetValuationPolicy):
        raise TypeError("policy must use PeerTargetValuationPolicy")
    if distribution.status is not PeerDistributionStatus.USABLE:
        return _point_free_result(
            distribution,
            (_issue(
                f"Peer distribution is {distribution.status.value}; target valuation requires USABLE.",
            ),),
            policy,
        )
    issues: list[DataIssue] = []
    if selection is None:
        issues.append(_issue("Usable peer distribution requires an explicit target denominator and bridge selection."))
    elif not isinstance(selection, PeerTargetValuationSelection):
        raise TypeError("selection must use PeerTargetValuationSelection")
    if target_forward_ebitda is None:
        issues.append(_issue("Canonical target annual AVERAGE EBITDA is unavailable."))
    elif not isinstance(target_forward_ebitda, MetricObservation):
        raise TypeError("target_forward_ebitda must use MetricObservation")
    if issues:
        return _point_free_result(distribution, tuple(issues), policy)

    statistics = (distribution.p25, distribution.median, distribution.p75)
    if any(
        value is None or isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0
        for value in statistics
    ):
        issues.append(_issue("Peer P25, median, and P75 must all be finite and positive."))
    elif not distribution.p25 <= distribution.median <= distribution.p75:
        issues.append(_issue("Peer statistics must satisfy P25 <= median <= P75; values were not reordered."))
    if distribution.multiple_type is not PeerMethod.EV_EBITDA:
        issues.append(_issue("Milestone 8D supports EV_EBITDA only."))
    if distribution.valuation_basis is not ValuationBasis.ENTERPRISE:
        issues.append(_issue("Peer EV/EBITDA target application requires enterprise basis."))
    if (
        selection.target_security_id != distribution.target_security_id
        or selection.target_issuer_id != distribution.target_issuer_id
    ):
        issues.append(_issue("Target denominator identity does not match the peer distribution target."))
    if (
        selection.distribution_id != distribution.distribution_id
        or selection.peer_set_id != distribution.peer_set_id
        or selection.peer_valuation_subset_id != distribution.peer_valuation_subset_id
    ):
        issues.append(_issue("Target selection does not reference the exact 8C distribution and upstream peer set/subset."))
    if selection.analysis_as_of != distribution.analysis_as_of:
        issues.append(_issue("Target selection analysis_as_of does not match the peer distribution snapshot."))
    if selection.selected_forward_period is not distribution.forward_period_policy:
        issues.append(_issue("Target FY selection must exactly match the peer distribution FY policy."))
    if selection.estimate_case is not EstimateCase.AVERAGE or distribution.estimate_case is not EstimateCase.AVERAGE:
        issues.append(_issue("Peer target valuation requires AVERAGE estimates for peers and target."))

    forward = target_forward_ebitda
    if selection.target_forward_observation_id != forward.observation_id:
        issues.append(_issue("Target EBITDA does not match the exact observation approved by selection."))
    if forward.metric_id is not MetricId.EBITDA or forward.unit is not MetricUnit.CURRENCY:
        issues.append(_issue("Target denominator must be canonical base-unit EBITDA, without substitution or scaling."))
    if (
        forward.frequency is not Frequency.ANNUAL
        or forward.observation_type is not ObservationType.ESTIMATE
        or forward.estimate_case is not EstimateCase.AVERAGE
    ):
        issues.append(_issue("Target denominator must be one annual AVERAGE estimate; FY1/FY2 are not NTM."))
    if forward.period_end != selection.target_forward_period_end:
        issues.append(_issue("Target EBITDA fiscal period does not match the exact FY selection."))
    if forward.period_end is None or forward.period_end <= distribution.analysis_as_of.date():
        issues.append(_issue("Target EBITDA period must end after analysis_as_of."))
    if forward.as_of_at != distribution.analysis_as_of:
        issues.append(_issue("Target EBITDA as-of does not match the peer distribution analysis snapshot."))
    if isinstance(forward.value, bool) or not math.isfinite(float(forward.value)) or forward.value <= 0:
        issues.append(_issue("Target forward EBITDA must be finite and positive."))

    bridge_id = None
    adjustment = None
    shares = None
    share_basis = None
    share_observation_id = None
    bridge_currency = None
    bridge_source_ids: tuple[str, ...] = ()
    bridge_provenance: tuple[Provenance, ...] = ()
    bridge_warnings: tuple[str, ...] = ()
    if selection.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE:
        if capital_structure_snapshot is not None or direct_enterprise_bridge is None:
            issues.append(_issue("Exactly the selected direct TEV-market-cap bridge is required."))
        else:
            bridge = direct_enterprise_bridge
            bridge_id = bridge.bridge_id
            adjustment = bridge.enterprise_equity_adjustment
            shares = bridge.shares_outstanding
            share_basis = bridge.share_basis
            share_observation_id = bridge.shares_observation_id
            bridge_currency = bridge.currency
            bridge_source_ids = bridge.source_observation_ids
            bridge_provenance = bridge.provenance
            bridge_warnings = bridge.warnings
            if bridge.bridge_id != selection.bridge_id:
                issues.append(_issue("Direct bridge ID does not match the approved selection."))
            if bridge.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                issues.append(_issue("Direct enterprise bridge is not complete."))
            if (
                bridge.security_id != distribution.target_security_id
                or bridge.issuer_id != distribution.target_issuer_id
                or bridge.analysis_as_of != distribution.analysis_as_of
            ):
                issues.append(_issue("Direct bridge identity or analysis timestamp does not match the target."))
            if bridge.share_count_semantics is ShareCountSemantics.UNVERIFIED:
                issues.append(_issue("Target ADR/ADS share conversion semantics are unresolved."))
    elif selection.bridge_method is EnterpriseBridgeMethod.COMPONENT_BRIDGE:
        if direct_enterprise_bridge is not None or capital_structure_snapshot is None:
            issues.append(_issue("Exactly the selected component bridge is required."))
        else:
            bridge = capital_structure_snapshot
            bridge_id = bridge.snapshot_id
            adjustment = bridge.net_debt
            shares = bridge.shares
            share_basis = bridge.share_basis
            share_observation_id = bridge.share_observation_id
            bridge_currency = bridge.currency
            bridge_source_ids = bridge.source_observation_ids
            bridge_provenance = bridge.provenance
            bridge_warnings = bridge.warnings
            if bridge.snapshot_id != selection.bridge_id:
                issues.append(_issue("Component bridge ID does not match the approved selection."))
            if bridge.completeness_status is not CapitalStructureCompleteness.COMPLETE:
                issues.append(_issue("Component enterprise bridge is not complete."))
            if (
                bridge.security_id != distribution.target_security_id
                or bridge.issuer_id != distribution.target_issuer_id
                or bridge.analysis_as_of != distribution.analysis_as_of
            ):
                issues.append(_issue("Component bridge identity or analysis timestamp does not match the target."))
    else:
        issues.append(_issue("A controlled direct or component bridge is required."))

    if bridge_currency != forward.currency:
        issues.append(_issue("Target EBITDA and bridge currencies differ; no FX was performed."))
    if share_basis is not selection.share_basis or share_observation_id != selection.share_observation_id:
        issues.append(_issue("Share count/basis does not match the exact evidence approved by selection."))
    if shares is None or isinstance(shares, bool) or not math.isfinite(float(shares)) or shares <= 0:
        issues.append(_issue("Approved target share count must be finite and positive."))
    if adjustment is None or isinstance(adjustment, bool) or not math.isfinite(float(adjustment)):
        issues.append(_issue("Enterprise-to-equity adjustment must be finite and complete."))
    if issues:
        return _point_free_result(distribution, tuple(_dedupe(issues)), policy)

    supporting_ids = _dedupe((
        *distribution.valid_observation_ids,
        forward.observation_id,
        *bridge_source_ids,
    ))
    points: list[PeerTargetValuationPoint] = []
    for statistic, attribute in _STATISTICS:
        multiple = float(getattr(distribution, attribute))
        denominator = float(forward.value)
        implied_enterprise_value = multiple * denominator
        implied_equity_value = implied_enterprise_value - adjustment
        point_issues: list[DataIssue] = []
        per_share_value = None
        if not math.isfinite(implied_enterprise_value):
            point_issues.append(_issue("Implied enterprise value is non-finite."))
        elif not math.isfinite(implied_equity_value):
            point_issues.append(_issue("Implied equity value is non-finite."))
        elif implied_equity_value <= 0:
            point_issues.append(_issue("Implied equity value is non-positive; it was not floored to zero."))
        else:
            per_share_value = implied_equity_value / shares
            if not math.isfinite(per_share_value) or per_share_value <= 0:
                point_issues.append(_issue("Per-share value is non-finite or non-positive."))
                per_share_value = None
        point_status = (
            ValuationMethodStatus.UNAVAILABLE if point_issues else ValuationMethodStatus.VALID
        )
        points.append(PeerTargetValuationPoint(
            point_id=stable_peer_target_point_id(
                distribution.distribution_id, selection.selection_id, statistic.value,
                forward.observation_id, bridge_id, policy.policy_id,
            ),
            statistic=statistic,
            target_security_id=distribution.target_security_id,
            target_issuer_id=distribution.target_issuer_id,
            multiple_type=PeerMethod.EV_EBITDA,
            valuation_basis=ValuationBasis.ENTERPRISE,
            analysis_as_of=distribution.analysis_as_of,
            distribution_id=distribution.distribution_id,
            peer_set_id=distribution.peer_set_id,
            peer_valuation_subset_id=distribution.peer_valuation_subset_id,
            selected_forward_period=distribution.forward_period_policy,
            target_forward_period_end=forward.period_end,
            estimate_case=EstimateCase.AVERAGE,
            peer_multiple=multiple,
            target_forward_ebitda=denominator,
            target_forward_observation_id=forward.observation_id,
            implied_enterprise_value=implied_enterprise_value,
            enterprise_equity_adjustment=adjustment,
            implied_equity_value=implied_equity_value,
            share_count=shares,
            share_basis=share_basis,
            share_observation_id=share_observation_id,
            per_share_value=per_share_value,
            currency=forward.currency,
            bridge_method=selection.bridge_method,
            bridge_id=bridge_id,
            status=point_status,
            supporting_observation_ids=supporting_ids,
            provenance=_derived_provenance(
                distribution, forward, bridge_provenance, statistic, supporting_ids, policy,
            ),
            issues=tuple(point_issues),
            policy_id=policy.policy_id,
        ))

    valid_values = tuple(
        point.per_share_value for point in points if point.status is ValuationMethodStatus.VALID
    )
    if any(left > right for left, right in zip(valid_values, valid_values[1:])):
        return _point_free_result(
            distribution,
            (_issue("Calculated valid points violate lower <= central <= upper; outputs were not sorted."),),
            policy,
        )
    valid = tuple(point.status is ValuationMethodStatus.VALID for point in points)
    status = (
        ValuationMethodStatus.VALID if all(valid)
        else ValuationMethodStatus.PARTIAL if any(valid)
        else ValuationMethodStatus.UNAVAILABLE
    )
    result_issues = _dedupe(issue for point in points for issue in point.issues)
    result_warnings = _dedupe((*distribution.warnings, *bridge_warnings))
    provenance = _dedupe(item for point in points for item in point.provenance)
    values = tuple(point.per_share_value if ok else None for point, ok in zip(points, valid))
    generic = ValuationResult(
        method=PeerMethod.EV_EBITDA.value,
        valuation_family="peer",
        currency=forward.currency,
        status=status,
        low=values[0],
        central=values[1],
        high=values[2],
        input_observation_ids=supporting_ids,
        provenance=provenance,
        issues=result_issues,
        warnings=result_warnings,
        reason=result_issues[0].reason if status is ValuationMethodStatus.UNAVAILABLE else None,
    )
    return PeerTargetValuationResult(
        result_id=stable_peer_target_valuation_id(
            distribution.distribution_id, selection.selection_id,
            *(point.point_id for point in points), bridge_id, policy.policy_id,
        ),
        target_security_id=distribution.target_security_id,
        target_issuer_id=distribution.target_issuer_id,
        analysis_as_of=distribution.analysis_as_of,
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        distribution_id=distribution.distribution_id,
        peer_set_id=distribution.peer_set_id,
        peer_valuation_subset_id=distribution.peer_valuation_subset_id,
        selected_forward_period=distribution.forward_period_policy,
        estimate_case=EstimateCase.AVERAGE,
        lower_point=points[0],
        central_point=points[1],
        upper_point=points[2],
        currency=forward.currency,
        bridge_method=selection.bridge_method,
        bridge_id=bridge_id,
        share_basis=share_basis,
        share_observation_id=share_observation_id,
        target_forward_observation_id=forward.observation_id,
        target_forward_period_end=forward.period_end,
        status=status,
        issues=result_issues,
        warnings=result_warnings,
        supporting_observation_ids=supporting_ids,
        policy_id=policy.policy_id,
        provenance=provenance,
        valuation_result=generic,
    )
