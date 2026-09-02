"""Canonical assembly and independent audit of own-history valuation methods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Iterable

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CompanyIdentity,
    DataIssue,
    DenominatorSemanticEvidence,
    DirectEnterpriseEquityBridge,
    EstimateCase,
    ForwardPeriodSelection,
    HistoricalDistributionUsability,
    HistoricalMultipleDistribution,
    HistoricalMultipleType,
    HistoricalStatistic,
    HistoricalValuationObservation,
    HistoricalWindow,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    OwnHistoryMethodReadiness,
    OwnHistoryMethodStatus,
    OwnHistoryValuationResult,
    ShareCountSemantics,
    ValuationMethodStatus,
)
from stock_analyser.providers.contracts import IdentityCandidate
from stock_analyser.services.consensus import ForwardConsensus, build_forward_consensus
from stock_analyser.services.identity import IdentityResolution, IdentitySeed, resolve_company_identity
from stock_analyser.services.own_history_inputs import (
    DEFAULT_OWN_HISTORY_INPUT_POLICY,
    OwnHistoryInputPolicy,
    assess_own_history_method_readiness,
    build_direct_enterprise_equity_bridge,
)
from stock_analyser.services.own_history_valuation import (
    DEFAULT_OWN_HISTORY_VALUATION_POLICY,
    OwnHistoryValuationPolicy,
    calculate_own_history_valuation,
)
from stock_analyser.services.valuation_history import (
    DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    HistoricalDistributionPolicy,
    build_historical_distribution,
)


class OwnHistoryAuditStage(str, Enum):
    IDENTITY = "IDENTITY"
    HISTORICAL_OBSERVATIONS = "HISTORICAL_OBSERVATIONS"
    DISTRIBUTION = "DISTRIBUTION"
    FORWARD_DENOMINATOR = "FORWARD_DENOMINATOR"
    ALIGNMENT = "ALIGNMENT"
    BRIDGE = "BRIDGE"
    READINESS = "READINESS"
    VALUATION = "VALUATION"


_METHODS = (
    HistoricalMultipleType.P_E,
    HistoricalMultipleType.EV_EBITDA,
    HistoricalMultipleType.EV_EBIT,
)
_FORWARD_METRICS = {
    HistoricalMultipleType.P_E: MetricId.EPS,
    HistoricalMultipleType.EV_EBITDA: MetricId.EBITDA,
    HistoricalMultipleType.EV_EBIT: MetricId.EBIT,
}


@dataclass(frozen=True, slots=True)
class OwnHistoryScaleAudit:
    forward_unit: MetricUnit | None
    enterprise_value_unit: MetricUnit | None
    market_cap_unit: MetricUnit | None
    share_count_unit: MetricUnit | None
    unit_compatibility_passed: bool
    currency_compatibility_passed: bool
    scale_compatibility_passed: bool


@dataclass(frozen=True, slots=True)
class OwnHistoryArithmeticAudit:
    statistic: HistoricalStatistic
    implied_enterprise_value_passed: bool
    enterprise_equity_adjustment_passed: bool
    implied_equity_value_passed: bool
    per_share_value_passed: bool

    @property
    def passed(self) -> bool:
        return all((
            self.implied_enterprise_value_passed,
            self.enterprise_equity_adjustment_passed,
            self.implied_equity_value_passed,
            self.per_share_value_passed,
        ))


@dataclass(frozen=True, slots=True)
class OwnHistoryMethodAudit:
    multiple_type: HistoricalMultipleType
    distribution: HistoricalMultipleDistribution
    readiness: OwnHistoryMethodReadiness
    forward_denominator: MetricObservation | None
    valuation: OwnHistoryValuationResult | None
    scale_audit: OwnHistoryScaleAudit
    arithmetic_audits: tuple[OwnHistoryArithmeticAudit, ...]
    failure_stage: OwnHistoryAuditStage | None
    issues: tuple[DataIssue, ...]

    @property
    def executed(self) -> bool:
        return self.valuation is not None


@dataclass(frozen=True, slots=True)
class OwnHistoryOrchestrationResult:
    analysis_as_of: datetime
    identity_resolution: IdentityResolution
    historical_window: HistoricalWindow
    forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    consensus: ForwardConsensus
    direct_bridge: DirectEnterpriseEquityBridge
    methods: tuple[OwnHistoryMethodAudit, ...]
    issues: tuple[DataIssue, ...]

    @property
    def identity(self) -> CompanyIdentity:
        return self.identity_resolution.identity

    @property
    def valuations(self) -> tuple[OwnHistoryValuationResult, ...]:
        return tuple(item.valuation for item in self.methods if item.valuation is not None)

    def method(self, multiple_type: HistoricalMultipleType) -> OwnHistoryMethodAudit:
        return next(item for item in self.methods if item.multiple_type is multiple_type)


def _selected_forward(
    consensus: ForwardConsensus,
    readiness: OwnHistoryMethodReadiness,
) -> MetricObservation | None:
    selected_id = readiness.forward_selection.observation_id
    if selected_id is None:
        return None
    return next(
        (item for period in consensus.periods for item in period.observations if item.observation_id == selected_id),
        None,
    )


def _source_observation(
    observations: tuple[MetricObservation, ...], observation_id: str | None,
) -> MetricObservation | None:
    if observation_id is None:
        return None
    return next((item for item in observations if item.observation_id == observation_id), None)


def _scale_audit(
    identity: CompanyIdentity,
    readiness: OwnHistoryMethodReadiness,
    forward: MetricObservation | None,
    bridge: DirectEnterpriseEquityBridge,
    bridge_observations: tuple[MetricObservation, ...],
) -> OwnHistoryScaleAudit:
    direct = readiness.enterprise_equity_bridge_id is not None
    tev = _source_observation(bridge_observations, bridge.enterprise_value_observation_id) if direct else None
    market_cap = _source_observation(bridge_observations, bridge.market_cap_observation_id) if direct else None
    shares = _source_observation(bridge_observations, readiness.selected_share_observation_id)
    expected_forward = (
        MetricUnit.CURRENCY_PER_SHARE
        if readiness.multiple_type is HistoricalMultipleType.P_E
        else MetricUnit.CURRENCY
    )
    unit_pass = (
        forward is not None
        and forward.unit is expected_forward
        and (
            readiness.multiple_type is HistoricalMultipleType.P_E
            or (
                tev is not None and tev.unit is MetricUnit.CURRENCY
                and market_cap is not None and market_cap.unit is MetricUnit.CURRENCY
                and shares is not None and shares.unit is MetricUnit.SHARES
            )
        )
    )
    currency_pass = (
        forward is not None
        and forward.currency == identity.reporting_currency
        and (
            readiness.multiple_type is HistoricalMultipleType.P_E
            or (
                tev is not None and market_cap is not None
                and tev.currency == market_cap.currency == forward.currency
            )
        )
    )
    finite_positive = (
        forward is not None
        and math.isfinite(float(forward.value)) and forward.value > 0
        and (
            readiness.multiple_type is HistoricalMultipleType.P_E
            or all(
                item is not None and math.isfinite(float(item.value)) and item.value > 0
                for item in (tev, market_cap, shares)
            )
        )
    )
    return OwnHistoryScaleAudit(
        forward_unit=forward.unit if forward is not None else None,
        enterprise_value_unit=tev.unit if tev is not None else None,
        market_cap_unit=market_cap.unit if market_cap is not None else None,
        share_count_unit=shares.unit if shares is not None else None,
        unit_compatibility_passed=unit_pass,
        currency_compatibility_passed=currency_pass,
        scale_compatibility_passed=unit_pass and currency_pass and finite_positive,
    )


def _arithmetic_audits(
    valuation: OwnHistoryValuationResult,
    bridge,
    *,
    tolerance: float,
) -> tuple[OwnHistoryArithmeticAudit, ...]:
    output = []
    for point in (valuation.lower_point, valuation.central_point, valuation.upper_point):
        if point.status is not ValuationMethodStatus.VALID:
            continue
        if point.implied_enterprise_value is None:
            # P/E has no enterprise arithmetic; its single multiplication is checked by the 7D model.
            output.append(OwnHistoryArithmeticAudit(point.statistic, True, True, True, math.isclose(
                point.per_share_value,
                point.historical_multiple * point.forward_denominator,
                rel_tol=tolerance,
                abs_tol=tolerance,
            )))
            continue
        expected_ev = point.historical_multiple * point.forward_denominator
        expected_adjustment = bridge.enterprise_value - bridge.market_cap
        expected_equity = expected_ev - expected_adjustment
        expected_per_share = expected_equity / bridge.shares_outstanding
        output.append(OwnHistoryArithmeticAudit(
            statistic=point.statistic,
            implied_enterprise_value_passed=math.isclose(
                point.implied_enterprise_value, expected_ev, rel_tol=tolerance, abs_tol=tolerance,
            ),
            enterprise_equity_adjustment_passed=math.isclose(
                point.enterprise_equity_adjustment, expected_adjustment,
                rel_tol=tolerance, abs_tol=tolerance,
            ),
            implied_equity_value_passed=math.isclose(
                point.implied_equity_value, expected_equity, rel_tol=tolerance, abs_tol=tolerance,
            ),
            per_share_value_passed=math.isclose(
                point.per_share_value, expected_per_share, rel_tol=tolerance, abs_tol=tolerance,
            ),
        ))
    return tuple(output)


def _failure_stage(
    distribution: HistoricalMultipleDistribution,
    readiness: OwnHistoryMethodReadiness,
    forward: MetricObservation | None,
) -> OwnHistoryAuditStage | None:
    if distribution.total_candidate_count == 0:
        return OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS
    if distribution.usability is not HistoricalDistributionUsability.USABLE:
        return OwnHistoryAuditStage.DISTRIBUTION
    if forward is None:
        return OwnHistoryAuditStage.FORWARD_DENOMINATOR
    if readiness.denominator_alignment.compatibility_status.value not in {"exact", "verified_equivalent"}:
        return OwnHistoryAuditStage.ALIGNMENT
    if readiness.status is OwnHistoryMethodStatus.PARTIAL:
        return OwnHistoryAuditStage.BRIDGE
    if readiness.status is not OwnHistoryMethodStatus.READY:
        return OwnHistoryAuditStage.READINESS
    return None


def assemble_own_history_valuation(
    *,
    identity_seed: IdentitySeed,
    identity_candidates: Iterable[IdentityCandidate],
    historical_observations: Iterable[HistoricalValuationObservation],
    forward_observations: Iterable[MetricObservation],
    bridge_observations: Iterable[MetricObservation],
    analysis_as_of: datetime,
    historical_window: HistoricalWindow | None = None,
    forward_period: ForwardPeriodSelection | None = None,
    estimate_case: EstimateCase | None = None,
    semantic_evidence: Iterable[DenominatorSemanticEvidence] = (),
    share_count_semantics: ShareCountSemantics | None = None,
    methods: Iterable[HistoricalMultipleType] = _METHODS,
    distribution_policy: HistoricalDistributionPolicy = DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    input_policy: OwnHistoryInputPolicy = DEFAULT_OWN_HISTORY_INPUT_POLICY,
    valuation_policy: OwnHistoryValuationPolicy = DEFAULT_OWN_HISTORY_VALUATION_POLICY,
    arithmetic_tolerance: float = 1e-12,
) -> OwnHistoryOrchestrationResult:
    """Compose existing 7A-7D services and execute each individually READY method."""
    if not isinstance(analysis_as_of, datetime) or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    window = historical_window or input_policy.default_window
    period = forward_period or input_policy.default_forward_period
    selected_case = estimate_case or input_policy.default_estimate_case
    if selected_case is not EstimateCase.AVERAGE:
        raise ValueError("Milestone 7D.1 executes only the AVERAGE estimate case")
    selected_methods = tuple(dict.fromkeys(methods))
    if not selected_methods or any(not isinstance(item, HistoricalMultipleType) for item in selected_methods):
        raise TypeError("methods must contain controlled HistoricalMultipleType values")

    identity_resolution = resolve_company_identity(identity_seed, tuple(identity_candidates))
    identity = identity_resolution.identity
    historical = tuple(historical_observations)
    forwards = tuple(forward_observations)
    bridge_inputs = tuple(bridge_observations)
    consensus = build_forward_consensus(forwards, as_of_at=analysis_as_of)
    direct_bridge = build_direct_enterprise_equity_bridge(
        identity,
        bridge_inputs,
        analysis_as_of=analysis_as_of,
        share_count_semantics=share_count_semantics,
        policy=input_policy,
    )
    evidence_by_method = {item.historical_multiple_type: item for item in semantic_evidence}
    method_audits = []
    all_issues = [*identity_resolution.issues, *consensus.issues, *direct_bridge.issues]

    for multiple_type in selected_methods:
        distribution = build_historical_distribution(
            historical,
            security_id=identity.security_id,
            issuer_id=identity.issuer_id,
            provider="fiscal",
            multiple_type=multiple_type,
            analysis_as_of=analysis_as_of,
            window=window,
            policy=distribution_policy,
        )
        readiness = assess_own_history_method_readiness(
            identity,
            (distribution,),
            consensus,
            multiple_type=multiple_type,
            analysis_as_of=analysis_as_of,
            direct_enterprise_bridge=direct_bridge,
            selected_window=window,
            selected_forward_period=period,
            estimate_case=selected_case,
            semantic_evidence=evidence_by_method.get(multiple_type),
            policy=input_policy,
        )
        forward = _selected_forward(consensus, readiness)
        scale = _scale_audit(identity, readiness, forward, direct_bridge, bridge_inputs)
        valuation = None
        arithmetic = ()
        stage = _failure_stage(distribution, readiness, forward)
        method_issues = list(distribution.issues)
        if readiness.status is not OwnHistoryMethodStatus.READY:
            method_issues.extend(DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric=multiple_type.value,
                provider="own_history_orchestration",
                reason=reason,
            ) for reason in readiness.blocking_reasons)
        elif not scale.scale_compatibility_passed:
            stage = OwnHistoryAuditStage.VALUATION
            method_issues.append(DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric=multiple_type.value,
                provider="own_history_orchestration",
                reason="Canonical unit, currency, or base-unit scale compatibility audit failed.",
                action="trace the normalization defect to the provider boundary; do not scale in orchestration",
            ))
        elif forward is None:
            stage = OwnHistoryAuditStage.FORWARD_DENOMINATOR
        else:
            valuation = calculate_own_history_valuation(
                readiness,
                distribution,
                forward,
                direct_enterprise_bridge=(
                    direct_bridge
                    if multiple_type in {HistoricalMultipleType.EV_EBITDA, HistoricalMultipleType.EV_EBIT}
                    else None
                ),
                policy=valuation_policy,
            )
            arithmetic = _arithmetic_audits(valuation, direct_bridge, tolerance=arithmetic_tolerance)
            if valuation.status is ValuationMethodStatus.UNAVAILABLE or not arithmetic or not all(
                item.passed for item in arithmetic
            ):
                stage = OwnHistoryAuditStage.VALUATION
                method_issues.extend(valuation.issues)
            else:
                stage = None
        audit = OwnHistoryMethodAudit(
            multiple_type=multiple_type,
            distribution=distribution,
            readiness=readiness,
            forward_denominator=forward,
            valuation=valuation,
            scale_audit=scale,
            arithmetic_audits=arithmetic,
            failure_stage=stage,
            issues=tuple(dict.fromkeys(method_issues)),
        )
        method_audits.append(audit)
        all_issues.extend(audit.issues)

    return OwnHistoryOrchestrationResult(
        analysis_as_of=analysis_as_of,
        identity_resolution=identity_resolution,
        historical_window=window,
        forward_period=period,
        estimate_case=selected_case,
        consensus=consensus,
        direct_bridge=direct_bridge,
        methods=tuple(method_audits),
        issues=tuple(dict.fromkeys(all_issues)),
    )
