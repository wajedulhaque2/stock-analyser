"""Pure cross-family valuation publication; no providers, prices, or valuation arithmetic."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from stock_analyser.domain.enums import (
    AggregationStatus,
    ExternalReferenceType,
    PeerMethod,
    PublicationCentralEstimator,
    PublicationEvidenceType,
    PublicationRangeSemantics,
    ReverseDcfExecutionMode,
    ValuationFamily,
    ValuationMethodStatus,
)
from stock_analyser.domain.models import ExternalValuationReference, ValuationResult, _require_text, _validate_aware_datetime, _validate_currency
from stock_analyser.domain.own_history import OwnHistoryValuationResult
from stock_analyser.domain.peer_family import PeerFamilyOrchestrationResult
from stock_analyser.domain.peer_target_valuation import PeerTargetValuationResult
from stock_analyser.domain.publication import (
    FamilyValuationEvidence,
    PublicationDispersionDiagnostics,
    PublicationSupplementalEvidence,
    ValuationPublicationResult,
    stable_family_valuation_evidence_id,
    stable_publication_supplemental_evidence_id,
    stable_valuation_publication_id,
)
from stock_analyser.domain.reverse_dcf import ReverseDcfExecutionResult

from .statistics import linear_quantile


@dataclass(frozen=True, slots=True)
class ValuationPublicationPolicy:
    policy_id: str = "v1-11a-threshold-free-family-publication"
    minimum_independent_families: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        if self.minimum_independent_families != 2:
            raise ValueError("Milestone 11A requires exactly two independent families")


DEFAULT_VALUATION_PUBLICATION_POLICY = ValuationPublicationPolicy()


def _per_share_unit(currency: str) -> str:
    return f"{currency}/share"


def family_evidence_from_generic_result(
    result: ValuationResult,
    *,
    target_security_id: str,
    target_issuer_id: str,
    source_result_id: str,
    analysis_as_of: datetime,
    per_share_unit: str | None = None,
    central_valuation_eligible: bool = True,
    supporting_ids: tuple[str, ...] = (),
    policy_ids: tuple[str, ...] = (),
) -> FamilyValuationEvidence:
    """Adapt one already-selected generic upstream valuation without recalculating it."""
    if type(result) is not ValuationResult:
        raise TypeError("result must be the canonical ValuationResult contract")
    try:
        family = ValuationFamily(result.valuation_family)
    except ValueError as error:
        raise ValueError("generic result does not identify a controlled valuation family") from error
    if family not in {ValuationFamily.OWN_HISTORY, ValuationFamily.PEER}:
        raise ValueError("reverse/cash-flow outputs are not central family evidence in Milestone 11A")
    unit = per_share_unit or _per_share_unit(result.currency)
    eligible = central_valuation_eligible and result.status is ValuationMethodStatus.VALID
    evidence_id = stable_family_valuation_evidence_id(
        target_security_id, target_issuer_id, family.value, source_result_id,
        analysis_as_of.isoformat(), result.currency, unit,
    )
    return FamilyValuationEvidence(
        evidence_id=evidence_id,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        valuation_family=family,
        selected_method=result.method,
        source_result_id=source_result_id,
        analysis_as_of=analysis_as_of,
        currency=result.currency,
        per_share_unit=unit,
        lower_value=result.low,
        central_value=result.central,
        upper_value=result.high,
        family_status=result.status,
        central_valuation_eligible=eligible,
        issues=result.issues,
        warnings=result.warnings,
        supporting_ids=supporting_ids or result.input_observation_ids,
        policy_ids=policy_ids,
        provenance=result.provenance,
    )


def family_evidence_from_own_history(
    result: OwnHistoryValuationResult,
    *,
    per_share_unit: str | None = None,
    central_valuation_eligible: bool = True,
) -> FamilyValuationEvidence:
    if not isinstance(result, OwnHistoryValuationResult):
        raise TypeError("result must be OwnHistoryValuationResult")
    return family_evidence_from_generic_result(
        result.valuation_result,
        target_security_id=result.security_id,
        target_issuer_id=result.issuer_id,
        source_result_id=result.result_id,
        analysis_as_of=result.analysis_as_of,
        per_share_unit=per_share_unit,
        central_valuation_eligible=central_valuation_eligible,
        supporting_ids=(result.distribution_id, result.readiness_id, *result.supporting_observation_ids),
        policy_ids=(result.policy_id,),
    )


def family_evidence_from_peer_target(
    result: PeerTargetValuationResult,
    *,
    per_share_unit: str | None = None,
    central_valuation_eligible: bool = True,
) -> FamilyValuationEvidence:
    if not isinstance(result, PeerTargetValuationResult):
        raise TypeError("result must be PeerTargetValuationResult")
    if result.valuation_result is None:
        return FamilyValuationEvidence(
            evidence_id=stable_family_valuation_evidence_id(
                result.target_security_id, result.target_issuer_id, ValuationFamily.PEER.value,
                result.result_id, result.analysis_as_of.isoformat(),
            ),
            target_security_id=result.target_security_id,
            target_issuer_id=result.target_issuer_id,
            valuation_family=ValuationFamily.PEER,
            selected_method=result.multiple_type.value,
            source_result_id=result.result_id,
            analysis_as_of=result.analysis_as_of,
            currency=result.currency,
            per_share_unit=None if result.currency is None else (per_share_unit or _per_share_unit(result.currency)),
            lower_value=None,
            central_value=None,
            upper_value=None,
            family_status=ValuationMethodStatus.UNAVAILABLE,
            central_valuation_eligible=False,
            issues=result.issues,
            warnings=result.warnings,
            supporting_ids=result.supporting_observation_ids,
            policy_ids=(result.policy_id,),
            provenance=result.provenance,
        )
    return family_evidence_from_generic_result(
        result.valuation_result,
        target_security_id=result.target_security_id,
        target_issuer_id=result.target_issuer_id,
        source_result_id=result.result_id,
        analysis_as_of=result.analysis_as_of,
        per_share_unit=per_share_unit,
        central_valuation_eligible=central_valuation_eligible,
        supporting_ids=(
            result.distribution_id, result.peer_set_id, result.peer_valuation_subset_id,
            *result.supporting_observation_ids,
        ),
        policy_ids=(result.policy_id,),
    )


def family_evidence_from_peer_orchestration(
    result: PeerFamilyOrchestrationResult,
    *,
    per_share_unit: str | None = None,
) -> FamilyValuationEvidence:
    """Preserve a complete or unavailable 8E family result as one contribution candidate."""
    if not isinstance(result, PeerFamilyOrchestrationResult):
        raise TypeError("result must be PeerFamilyOrchestrationResult")
    if result.target_peer_valuation is not None:
        return family_evidence_from_peer_target(
            result.target_peer_valuation,
            per_share_unit=per_share_unit,
        )
    currency = result.reporting_currency
    return FamilyValuationEvidence(
        evidence_id=stable_family_valuation_evidence_id(
            result.target_security_id, result.target_issuer_id, ValuationFamily.PEER.value,
            result.orchestration_id, result.analysis_as_of.isoformat(),
        ),
        target_security_id=result.target_security_id,
        target_issuer_id=result.target_issuer_id,
        valuation_family=ValuationFamily.PEER,
        selected_method=PeerMethod.EV_EBITDA.value,
        source_result_id=result.orchestration_id,
        analysis_as_of=result.analysis_as_of,
        currency=currency,
        per_share_unit=None if currency is None else (per_share_unit or _per_share_unit(currency)),
        lower_value=None,
        central_value=None,
        upper_value=None,
        family_status=ValuationMethodStatus.UNAVAILABLE,
        central_valuation_eligible=False,
        warnings=tuple(dict.fromkeys((*result.warnings, *result.blocking_reasons))),
        supporting_ids=result.supporting_ids,
        policy_ids=result.policy_ids,
        provenance=result.provenance,
    )


def supplemental_evidence_from_reverse_dcf(
    result: ReverseDcfExecutionResult,
) -> PublicationSupplementalEvidence:
    if not isinstance(result, ReverseDcfExecutionResult):
        raise TypeError("result must be ReverseDcfExecutionResult")
    evidence_type = (
        PublicationEvidenceType.REVERSE_DCF_SCENARIO
        if result.execution_mode is ReverseDcfExecutionMode.EXPLICIT_SCENARIO
        else PublicationEvidenceType.REVERSE_DCF_CANONICAL_EXPECTATION
    )
    inputs = result.execution_inputs
    return PublicationSupplementalEvidence(
        evidence_id=stable_publication_supplemental_evidence_id(
            inputs.target_security_id, inputs.target_issuer_id, evidence_type.value,
            result.execution_result_id,
        ),
        target_security_id=inputs.target_security_id,
        target_issuer_id=inputs.target_issuer_id,
        evidence_type=evidence_type,
        source_result_id=result.execution_result_id,
        evidence_as_of=inputs.analysis_as_of,
        central_valuation_eligible=False,
        issues=result.issues,
        warnings=result.warnings,
        supporting_ids=(result.execution_inputs_id, result.solver_result.result_id),
        policy_ids=result.policy_ids,
        provenance=result.provenance,
    )


def supplemental_evidence_from_external_reference(
    reference: ExternalValuationReference,
    *,
    reference_id: str,
    target_security_id: str,
    target_issuer_id: str,
) -> PublicationSupplementalEvidence:
    if not isinstance(reference, ExternalValuationReference):
        raise TypeError("reference must be ExternalValuationReference")
    evidence_type = (
        PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE
        if reference.reference_type is ExternalReferenceType.FMP_STANDARD_DCF
        else PublicationEvidenceType.EXTERNAL_ANALYST_TARGET
    )
    return PublicationSupplementalEvidence(
        evidence_id=stable_publication_supplemental_evidence_id(
            target_security_id, target_issuer_id, evidence_type.value, reference_id,
        ),
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        evidence_type=evidence_type,
        source_result_id=reference_id,
        evidence_as_of=reference.as_of_at,
        central_valuation_eligible=False,
        provenance=(reference.provenance,),
    )


def _publication_dimensions(
    candidates: tuple[FamilyValuationEvidence, ...],
    *,
    currency: str | None,
    per_share_unit: str | None,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    if (currency is None) != (per_share_unit is None):
        raise ValueError("currency and per_share_unit must be supplied together")
    if currency is not None:
        _validate_currency(currency, "currency")
        return currency, _require_text(per_share_unit, "per_share_unit"), ()
    pairs = {
        (item.currency, item.per_share_unit)
        for item in candidates
        if item.central_valuation_eligible and item.currency is not None and item.per_share_unit is not None
    }
    if len(pairs) == 1:
        inferred_currency, inferred_unit = next(iter(pairs))
        return inferred_currency, inferred_unit, ()
    if len(pairs) > 1:
        return None, None, ("eligible family currency/per-share dimensions conflict; no FX or quote-unit conversion is permitted",)
    return None, None, ()


def publish_cross_family_valuation(
    *,
    target_security_id: str,
    target_issuer_id: str,
    analysis_as_of: datetime,
    family_candidates: tuple[FamilyValuationEvidence, ...] = (),
    supplemental_evidence: tuple[PublicationSupplementalEvidence, ...] = (),
    currency: str | None = None,
    per_share_unit: str | None = None,
    policy: ValuationPublicationPolicy = DEFAULT_VALUATION_PUBLICATION_POLICY,
) -> ValuationPublicationResult:
    """Publish threshold-free agreement across independently selected family outputs."""
    target_security_id = _require_text(target_security_id, "target_security_id")
    target_issuer_id = _require_text(target_issuer_id, "target_issuer_id")
    _validate_aware_datetime(analysis_as_of, "analysis_as_of")
    if not isinstance(policy, ValuationPublicationPolicy):
        raise TypeError("policy must be ValuationPublicationPolicy")
    candidates = tuple(family_candidates)
    supplements = tuple(supplemental_evidence)
    if any(not isinstance(value, FamilyValuationEvidence) for value in candidates):
        raise TypeError("family_candidates must contain FamilyValuationEvidence")
    if any(not isinstance(value, PublicationSupplementalEvidence) for value in supplements):
        raise TypeError("supplemental_evidence must contain PublicationSupplementalEvidence")
    candidates = tuple(sorted(candidates, key=lambda item: (item.valuation_family.value, item.evidence_id)))
    supplements = tuple(sorted(supplements, key=lambda item: item.evidence_id))
    counts = Counter(item.valuation_family for item in candidates)
    ambiguous = {family for family, count in counts.items() if count > 1}
    publication_currency, publication_unit, dimension_blockers = _publication_dimensions(
        tuple(item for item in candidates if item.valuation_family not in ambiguous),
        currency=currency,
        per_share_unit=per_share_unit,
    )

    blockers = list(dimension_blockers)
    for family in sorted(ambiguous, key=lambda item: item.value):
        blockers.append(f"{family.value} has multiple candidates and no unique upstream selection")
    eligible: list[FamilyValuationEvidence] = []
    for item in candidates:
        if item.valuation_family in ambiguous:
            continue
        if not item.central_valuation_eligible or item.family_status is not ValuationMethodStatus.VALID:
            continue
        if item.target_security_id != target_security_id or item.target_issuer_id != target_issuer_id:
            blockers.append(f"{item.valuation_family.value} target identity does not match the publication target")
            continue
        if item.analysis_as_of != analysis_as_of:
            blockers.append(f"{item.valuation_family.value} analysis snapshot does not match the publication snapshot")
            continue
        if publication_currency is None or publication_unit is None:
            continue
        if item.currency != publication_currency:
            blockers.append(f"{item.valuation_family.value} currency does not match the publication currency")
            continue
        if item.per_share_unit != publication_unit:
            blockers.append(f"{item.valuation_family.value} per-share unit does not match the publication unit")
            continue
        eligible.append(item)

    eligible.sort(key=lambda item: item.valuation_family.value)
    family_ids = tuple(item.valuation_family for item in eligible)
    count = len(eligible)
    lower_values = tuple(item.lower_value for item in eligible)
    central_values = tuple(item.central_value for item in eligible)
    upper_values = tuple(item.upper_value for item in eligible)

    envelope_lower = min(lower_values) if lower_values else None
    envelope_upper = max(upper_values) if upper_values else None
    envelope_semantics = (
        PublicationRangeSemantics.SINGLE_FAMILY_RANGE if count == 1
        else PublicationRangeSemantics.FAMILY_ENVELOPE if count >= 2
        else None
    )
    overlap_lower = max(lower_values) if count >= 2 else None
    overlap_upper_candidate = min(upper_values) if count >= 2 else None
    overlap_exists = count >= 2 and overlap_lower <= overlap_upper_candidate
    overlap_upper = overlap_upper_candidate if overlap_exists else None
    if not overlap_exists:
        overlap_lower = None
    overlap_semantics = PublicationRangeSemantics.COMMON_OVERLAP if overlap_exists else None

    sorted_centrals = tuple(sorted(central_values))
    median_central = linear_quantile(sorted_centrals, 0.5) if sorted_centrals else None
    minimum_central = min(sorted_centrals) if sorted_centrals else None
    maximum_central = max(sorted_centrals) if sorted_centrals else None
    central_spread = maximum_central - minimum_central if sorted_centrals else None
    spread_ratio = central_spread / median_central if median_central is not None and median_central > 0 else None
    diagnostics = PublicationDispersionDiagnostics(
        family_count=count,
        minimum_family_central=minimum_central,
        maximum_family_central=maximum_central,
        median_family_central=median_central,
        central_spread=central_spread,
        central_spread_to_median=spread_ratio,
        envelope_width=(envelope_upper - envelope_lower) if envelope_lower is not None else None,
        overlap_width=(overlap_upper - overlap_lower) if overlap_lower is not None else None,
    )

    if count == 0:
        status = AggregationStatus.UNAVAILABLE
        blockers.append("zero complete central-eligible valuation families")
    elif count == 1:
        status = AggregationStatus.UNRESOLVED
        blockers.append("minimum two independent complete valuation families not met")
    elif not overlap_exists:
        status = AggregationStatus.UNRESOLVED
        blockers.append("eligible family ranges have no common overlap")
    elif all(overlap_lower <= value <= overlap_upper for value in central_values):
        status = AggregationStatus.RESOLVED
    else:
        status = AggregationStatus.WIDE
        blockers.append("common overlap exists but not every family central lies inside it")

    overall_central = median_central if status is AggregationStatus.RESOLVED else None
    estimator = (
        PublicationCentralEstimator.MEDIAN_OF_FAMILY_CENTRALS
        if status is AggregationStatus.RESOLVED else None
    )
    expectation_ids = tuple(
        item.evidence_id for item in supplements
        if item.evidence_type is PublicationEvidenceType.REVERSE_DCF_CANONICAL_EXPECTATION
    )
    scenario_ids = tuple(
        item.evidence_id for item in supplements
        if item.evidence_type is PublicationEvidenceType.REVERSE_DCF_SCENARIO
    )
    reference_ids = tuple(
        item.evidence_id for item in supplements
        if item.evidence_type in {
            PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
            PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
        }
    )
    provenance = tuple(dict.fromkeys(
        value for item in (*candidates, *supplements) for value in item.provenance
    ))
    issues = tuple(dict.fromkeys(
        issue.reason for item in candidates for issue in item.issues
    )) + tuple(dict.fromkeys(
        issue for item in supplements for issue in item.issues
    ))
    warnings = tuple(dict.fromkeys(
        warning for item in (*candidates, *supplements) for warning in item.warnings
    ))
    publication_id = stable_valuation_publication_id(
        target_security_id, target_issuer_id, analysis_as_of.isoformat(),
        publication_currency, publication_unit, status.value,
        *(item.evidence_id for item in (*candidates, *supplements)), policy.policy_id,
    )
    return ValuationPublicationResult(
        publication_id=publication_id,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        currency=publication_currency,
        per_share_unit=publication_unit,
        publication_status=status,
        eligible_family_ids=family_ids,
        eligible_family_count=count,
        family_evidence=candidates,
        supplemental_evidence=supplements,
        overall_central_value=overall_central,
        central_estimator=estimator,
        envelope_lower=envelope_lower,
        envelope_upper=envelope_upper,
        envelope_semantics=envelope_semantics,
        overlap_lower=overlap_lower,
        overlap_upper=overlap_upper,
        overlap_semantics=overlap_semantics,
        diagnostics=diagnostics,
        expectation_evidence_ids=expectation_ids,
        scenario_evidence_ids=scenario_ids,
        reference_evidence_ids=reference_ids,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
        issues=tuple(dict.fromkeys(issues)),
        warnings=warnings,
        policy_id=policy.policy_id,
        provenance=provenance,
    )
