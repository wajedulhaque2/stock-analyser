"""Provider-independent coordinator for the completed 8A-8D peer family."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CapitalStructureSnapshot,
    CompanyIdentity,
    DirectEnterpriseEquityBridge,
    EnterpriseBridgeMethod,
    ForwardPeriodSelection,
    MetricObservation,
    PeerCriterion,
    PeerCriterionStatus,
    PeerDistributionStatus,
    PeerFamilyFailureStage,
    PeerFamilyOrchestrationResult,
    PeerFamilyStatus,
    PeerMethodEvidenceReason,
    PeerSelectionStatus,
    PeerSet,
    PeerSetStatus,
    PeerTargetValuationSelection,
    PeerValuationSubsetStatus,
    ShareCountSemantics,
    ValuationMethodStatus,
    stable_peer_family_orchestration_id,
)
from .peer_target_valuation import calculate_peer_target_valuation
from .peer_valuation_distribution import build_peer_multiple_distribution
from .peer_valuation_inputs import PeerMethodDataInput, build_peer_valuation_subset


@dataclass(frozen=True, slots=True)
class PeerFamilyOrchestrationPolicy:
    unavailable_is_expected: bool = False
    policy_id: str = "peer-family-orchestration-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.unavailable_is_expected, bool):
            raise TypeError("unavailable_is_expected must be boolean")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        object.__setattr__(self, "policy_id", self.policy_id.strip())


DEFAULT_PEER_FAMILY_ORCHESTRATION_POLICY = PeerFamilyOrchestrationPolicy()


def _dedupe(items):
    return tuple(dict.fromkeys(item for item in items if item is not None))


def _peer_set_failure(peer_set: PeerSet) -> tuple[PeerFamilyFailureStage, tuple[str, ...]]:
    if not peer_set.candidate_count:
        return PeerFamilyFailureStage.CANDIDATE_DISCOVERY, ("No provider-discovered peer candidates were available.",)
    if not peer_set.included_peer_issuer_ids:
        security_unresolved = any(
            selection.status is PeerSelectionStatus.UNVERIFIED
            and selection.evidence.criterion(PeerCriterion.SECURITY_TYPE).status is not PeerCriterionStatus.PASS
            for selection in peer_set.selections
        )
        if security_unresolved:
            return PeerFamilyFailureStage.SECURITY_ELIGIBILITY, (
                "Canonical security eligibility was unresolved for every potential included peer.",
            )
        return PeerFamilyFailureStage.ECONOMIC_COMPARABILITY, (
            "No candidate satisfied the immutable economic comparability gates.",
        )
    return PeerFamilyFailureStage.PEER_SET, (
        f"Economic peer set is {peer_set.status.value} with {len(peer_set.included_peer_issuer_ids)} included issuers; three are required.",
    )


def _subset_failure(subset) -> tuple[PeerFamilyFailureStage, tuple[str, ...]]:
    reasons = _dedupe(reason for evidence in subset.method_data_evidence for reason in evidence.reasons)
    if PeerMethodEvidenceReason.FORWARD_IDENTITY_UNVERIFIED in reasons:
        stage = PeerFamilyFailureStage.PEER_PROVIDER_IDENTITY
    elif any(reason in reasons for reason in (
        PeerMethodEvidenceReason.ENTERPRISE_VALUE_UNAVAILABLE,
        PeerMethodEvidenceReason.ENTERPRISE_VALUE_FUTURE,
        PeerMethodEvidenceReason.ENTERPRISE_VALUE_STALE,
        PeerMethodEvidenceReason.ENTERPRISE_VALUE_INVALID,
    )):
        stage = PeerFamilyFailureStage.PEER_ENTERPRISE_VALUE
    elif any(reason in reasons for reason in (
        PeerMethodEvidenceReason.FORWARD_EBITDA_UNAVAILABLE,
        PeerMethodEvidenceReason.FORWARD_ESTIMATE_FUTURE,
        PeerMethodEvidenceReason.FORWARD_PERIOD_UNAVAILABLE,
        PeerMethodEvidenceReason.FORWARD_ESTIMATE_CASE_UNAVAILABLE,
        PeerMethodEvidenceReason.FORWARD_EBITDA_INVALID,
    )):
        stage = PeerFamilyFailureStage.PEER_FORWARD_DENOMINATOR
    else:
        stage = PeerFamilyFailureStage.PEER_VALUATION_SUBSET
    text = tuple(reason.value for reason in reasons) or (
        f"Peer valuation subset is {subset.status.value}.",
    )
    return stage, text


def _target_preflight(
    selection: PeerTargetValuationSelection | None,
    forward: MetricObservation | None,
    component: CapitalStructureSnapshot | None,
    direct: DirectEnterpriseEquityBridge | None,
) -> tuple[PeerFamilyFailureStage | None, tuple[str, ...]]:
    if selection is None or forward is None:
        return PeerFamilyFailureStage.TARGET_FORWARD_DENOMINATOR, (
            "Usable peer distribution lacks an explicit canonical target forward EBITDA selection.",
        )
    if selection.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE:
        if direct is None or component is not None:
            return PeerFamilyFailureStage.TARGET_BRIDGE, ("Exactly the selected direct target bridge is required.",)
        shares = direct.shares_outstanding
        complete = direct.completeness_status is CapitalStructureCompleteness.COMPLETE
        semantics_ok = direct.share_count_semantics is not ShareCountSemantics.UNVERIFIED
    else:
        if component is None or direct is not None:
            return PeerFamilyFailureStage.TARGET_BRIDGE, ("Exactly the selected component target bridge is required.",)
        shares = component.shares
        complete = component.completeness_status is CapitalStructureCompleteness.COMPLETE
        semantics_ok = True
    if not complete:
        return PeerFamilyFailureStage.TARGET_BRIDGE, ("Selected target enterprise bridge is incomplete.",)
    if shares is None or isinstance(shares, bool) or not math.isfinite(float(shares)) or shares <= 0 or not semantics_ok:
        return PeerFamilyFailureStage.TARGET_SHARES, ("Approved target share evidence is missing, invalid, or semantically unresolved.",)
    return None, ()


def orchestrate_peer_family(
    target_identity: CompanyIdentity,
    peer_set: PeerSet | None,
    method_inputs: Iterable[PeerMethodDataInput] = (),
    *,
    analysis_as_of,
    forward_period: ForwardPeriodSelection = ForwardPeriodSelection.FY1,
    target_selection: PeerTargetValuationSelection | None = None,
    target_forward_ebitda: MetricObservation | None = None,
    capital_structure_snapshot: CapitalStructureSnapshot | None = None,
    direct_enterprise_bridge: DirectEnterpriseEquityBridge | None = None,
    upstream_failure_stage: PeerFamilyFailureStage | None = None,
    upstream_blocking_reasons: tuple[str, ...] = (),
    policy: PeerFamilyOrchestrationPolicy = DEFAULT_PEER_FAMILY_ORCHESTRATION_POLICY,
) -> PeerFamilyOrchestrationResult:
    """Coordinate existing peer services and retain the earliest meaningful failure."""
    if not isinstance(target_identity, CompanyIdentity):
        raise TypeError("target_identity must use CompanyIdentity")
    if not isinstance(policy, PeerFamilyOrchestrationPolicy):
        raise TypeError("policy must use PeerFamilyOrchestrationPolicy")
    if not hasattr(analysis_as_of, "tzinfo") or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    if peer_set is not None and not isinstance(peer_set, PeerSet):
        raise TypeError("peer_set must use PeerSet")
    subset = distribution = valuation = None
    failure_stage = upstream_failure_stage
    reasons = tuple(upstream_blocking_reasons)
    if peer_set is None:
        failure_stage = failure_stage or PeerFamilyFailureStage.PEER_SET
        reasons = reasons or ("Canonical economic peer set is unavailable.",)
    else:
        if (
            peer_set.target_security_id != target_identity.security_id
            or peer_set.target_issuer_id != target_identity.issuer_id
        ):
            raise ValueError("peer set target identity must match canonical orchestration identity")
        if peer_set.analysis_as_of != analysis_as_of:
            raise ValueError("peer set analysis_as_of must match orchestration analysis_as_of")
        subset = build_peer_valuation_subset(
            peer_set, method_inputs, forward_period=forward_period,
        )
        distribution = build_peer_multiple_distribution(subset)
        if peer_set.status is not PeerSetStatus.USABLE:
            failure_stage, reasons = _peer_set_failure(peer_set)
        elif subset.status is not PeerValuationSubsetStatus.USABLE:
            failure_stage, reasons = _subset_failure(subset)
        elif distribution.status is not PeerDistributionStatus.USABLE:
            failure_stage = PeerFamilyFailureStage.PEER_DISTRIBUTION
            reasons = (f"Peer distribution is {distribution.status.value}.",)
        else:
            failure_stage, reasons = _target_preflight(
                target_selection, target_forward_ebitda,
                capital_structure_snapshot, direct_enterprise_bridge,
            )
            if failure_stage is None:
                valuation = calculate_peer_target_valuation(
                    distribution,
                    target_selection,
                    target_forward_ebitda,
                    capital_structure_snapshot=capital_structure_snapshot,
                    direct_enterprise_bridge=direct_enterprise_bridge,
                )
                if valuation.status is ValuationMethodStatus.VALID:
                    failure_stage = PeerFamilyFailureStage.COMPLETE
                    reasons = ()
                else:
                    failure_stage = PeerFamilyFailureStage.TARGET_APPLICATION
                    reasons = tuple(issue.reason for issue in valuation.issues) or (
                        f"Target peer valuation is {valuation.status.value}.",
                    )
    if failure_stage is PeerFamilyFailureStage.COMPLETE:
        status = PeerFamilyStatus.VALID
    elif valuation is not None and valuation.status is ValuationMethodStatus.PARTIAL:
        status = PeerFamilyStatus.PARTIAL
    elif policy.unavailable_is_expected:
        status = PeerFamilyStatus.EXPECTED_UNAVAILABLE
    elif peer_set is not None and peer_set.status in {PeerSetStatus.PARTIAL, PeerSetStatus.INSUFFICIENT}:
        status = PeerFamilyStatus.INSUFFICIENT
    else:
        status = PeerFamilyStatus.UNAVAILABLE
    included_count = len(peer_set.included_peer_issuer_ids) if peer_set is not None else 0
    candidate_count = peer_set.candidate_count if peer_set is not None else 0
    valid_count = subset.valid_observation_count if subset is not None else 0
    supporting = _dedupe((
        subset.peer_set_id if subset is not None else None,
        subset.subset_id if subset is not None else None,
        *((selection.candidate.candidate_id for selection in peer_set.selections) if peer_set is not None else ()),
        *(subset.valid_observation_ids if subset is not None else ()),
        distribution.distribution_id if distribution is not None else None,
        valuation.result_id if valuation is not None else None,
    ))
    policies = _dedupe((
        policy.policy_id,
        peer_set.policy_id if peer_set is not None else None,
        subset.policy_id if subset is not None else None,
        distribution.policy_id if distribution is not None else None,
        valuation.policy_id if valuation is not None else None,
    ))
    provenance = _dedupe((
        *(peer_set.provenance if peer_set is not None else ()),
        *(subset.provenance if subset is not None else ()),
        *(distribution.provenance if distribution is not None else ()),
        *(valuation.provenance if valuation is not None else ()),
    ))
    warnings = _dedupe((
        *(peer_set.warnings if peer_set is not None else ()),
        *(subset.warnings if subset is not None else ()),
        *(distribution.warnings if distribution is not None else ()),
        *(valuation.warnings if valuation is not None else ()),
    ))
    return PeerFamilyOrchestrationResult(
        orchestration_id=stable_peer_family_orchestration_id(
            target_identity.security_id, target_identity.issuer_id,
            analysis_as_of.isoformat(),
            subset.subset_id if subset is not None else "", policy.policy_id,
        ),
        target_security_id=target_identity.security_id,
        target_issuer_id=target_identity.issuer_id,
        canonical_symbol=target_identity.canonical_symbol,
        analysis_as_of=analysis_as_of,
        reporting_currency=target_identity.reporting_currency,
        quote_currency=target_identity.quote_currency,
        quote_unit=target_identity.quote_unit,
        quote_price_scale=target_identity.price_scale,
        peer_set_id=subset.peer_set_id if subset is not None else None,
        peer_set_status=peer_set.status if peer_set is not None else None,
        peer_subset_id=subset.subset_id if subset is not None else None,
        peer_subset_status=subset.status if subset is not None else None,
        peer_distribution_id=distribution.distribution_id if distribution is not None else None,
        peer_distribution_status=distribution.status if distribution is not None else None,
        target_peer_valuation_id=valuation.result_id if valuation is not None else None,
        target_peer_valuation_status=(
            valuation.status if valuation is not None else ValuationMethodStatus.UNAVAILABLE
            if distribution is not None else None
        ),
        candidate_count=candidate_count,
        included_peer_count=included_count,
        valid_peer_observation_count=valid_count,
        status=status,
        failure_stage=failure_stage,
        blocking_reasons=tuple(reasons),
        warnings=warnings,
        supporting_ids=supporting,
        policy_ids=policies,
        provenance=provenance,
        target_identity=target_identity,
        peer_set=peer_set,
        peer_subset=subset,
        peer_distribution=distribution,
        target_peer_valuation=valuation,
    )
