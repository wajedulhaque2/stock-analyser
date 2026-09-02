"""Prepare peer EV/EBITDA observations without changing economic membership."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Iterable

from stock_analyser.domain import (
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    MetricId,
    MetricUnit,
    ObservationType,
    PeerMethod,
    PeerMethodDataEvidence,
    PeerMethodEvidenceReason,
    PeerMethodEvidenceStatus,
    PeerMetricEvidence,
    PeerProviderIdentityBinding,
    PeerSelectionResult,
    PeerSelectionStatus,
    PeerSet,
    PeerSetStatus,
    PeerValuationObservation,
    PeerValuationSubset,
    PeerValuationSubsetStatus,
    ValuationBasis,
    stable_peer_method_evidence_id,
    stable_peer_valuation_observation_id,
    stable_peer_valuation_subset_id,
)


@dataclass(frozen=True, slots=True)
class PeerValuationPolicy:
    maximum_enterprise_value_age_days: int = 7
    minimum_valid_observations: int = 3
    default_forward_period: ForwardPeriodSelection = ForwardPeriodSelection.FY1
    estimate_case: EstimateCase = EstimateCase.AVERAGE
    policy_id: str = "peer-ev-ebitda-inputs-v1"

    def __post_init__(self) -> None:
        if (
            isinstance(self.maximum_enterprise_value_age_days, bool)
            or not isinstance(self.maximum_enterprise_value_age_days, int)
            or self.maximum_enterprise_value_age_days < 0
        ):
            raise ValueError("maximum_enterprise_value_age_days must be non-negative")
        if (
            isinstance(self.minimum_valid_observations, bool)
            or not isinstance(self.minimum_valid_observations, int)
            or self.minimum_valid_observations < 1
        ):
            raise ValueError("minimum_valid_observations must be positive")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("the primary peer valuation policy requires AVERAGE estimates")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")


DEFAULT_PEER_VALUATION_POLICY = PeerValuationPolicy()


@dataclass(frozen=True, slots=True)
class PeerMethodDataInput:
    candidate_id: str
    peer_security_id: str
    peer_issuer_id: str
    peer_fiscal_year_end: str
    enterprise_value_evidence: tuple[PeerMetricEvidence, ...] = ()
    forward_ebitda_evidence: tuple[PeerMetricEvidence, ...] = ()
    forward_identity_binding: PeerProviderIdentityBinding | None = None

    def __post_init__(self) -> None:
        for name in ("candidate_id", "peer_security_id", "peer_issuer_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if not re.fullmatch(r"(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])", self.peer_fiscal_year_end):
            raise ValueError("peer_fiscal_year_end must use MM-DD format")
        object.__setattr__(self, "enterprise_value_evidence", tuple(self.enterprise_value_evidence))
        object.__setattr__(self, "forward_ebitda_evidence", tuple(self.forward_ebitda_evidence))


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _selection_result_id(selection: PeerSelectionResult, analysis_as_of: datetime) -> str:
    return stable_peer_method_evidence_id(
        "selection",
        selection.candidate.candidate_id,
        selection.candidate.candidate_security_id,
        selection.candidate.candidate_issuer_id,
        selection.status.value,
        analysis_as_of.isoformat(),
    )


def _peer_set_id(peer_set: PeerSet) -> str:
    return stable_peer_valuation_subset_id(
        "economic-peer-set",
        peer_set.target_security_id,
        peer_set.target_issuer_id,
        peer_set.analysis_as_of.isoformat(),
        peer_set.policy_id,
        *peer_set.included_peer_issuer_ids,
    )


def _matches_peer(evidence: PeerMetricEvidence, selection: PeerSelectionResult) -> bool:
    return (
        evidence.peer_security_id == selection.candidate.candidate_security_id
        and evidence.peer_issuer_id == selection.candidate.candidate_issuer_id
    )


def _select_enterprise_value(
    selection: PeerSelectionResult,
    evidence: tuple[PeerMetricEvidence, ...],
    *,
    analysis_as_of: datetime,
    policy: PeerValuationPolicy,
):
    reasons: list[PeerMethodEvidenceReason] = []
    eligible = []
    for item in evidence:
        if not _matches_peer(item, selection):
            reasons.append(PeerMethodEvidenceReason.IDENTITY_MISMATCH)
            continue
        observation = item.observation
        if (
            observation.metric_id is not MetricId.ENTERPRISE_VALUE
            or observation.unit is not MetricUnit.CURRENCY
            or observation.frequency is not Frequency.POINT_IN_TIME
            or observation.observation_type is not ObservationType.ACTUAL
            or observation.estimate_case is not EstimateCase.NOT_APPLICABLE
            or observation.currency is None
            or observation.period_end is None
        ):
            reasons.append(PeerMethodEvidenceReason.UNIT_MISMATCH)
            continue
        if observation.period_end > analysis_as_of.date() or observation.as_of_at > analysis_as_of:
            reasons.append(PeerMethodEvidenceReason.ENTERPRISE_VALUE_FUTURE)
            continue
        age = (analysis_as_of.date() - observation.period_end).days
        if age > policy.maximum_enterprise_value_age_days:
            reasons.append(PeerMethodEvidenceReason.ENTERPRISE_VALUE_STALE)
            continue
        if not math.isfinite(observation.value) or observation.value <= 0:
            reasons.append(PeerMethodEvidenceReason.ENTERPRISE_VALUE_INVALID)
            continue
        eligible.append(observation)
    if not eligible:
        reasons.append(PeerMethodEvidenceReason.ENTERPRISE_VALUE_UNAVAILABLE)
        return None, tuple(dict.fromkeys(reasons))
    latest_date = max(item.period_end for item in eligible)
    latest_date_rows = [item for item in eligible if item.period_end == latest_date]
    latest_as_of = max(item.as_of_at for item in latest_date_rows)
    latest = [item for item in latest_date_rows if item.as_of_at == latest_as_of]
    signatures = {(item.value, item.currency, item.unit) for item in latest}
    if len(signatures) != 1:
        return None, (PeerMethodEvidenceReason.CONFLICTING_OBSERVATIONS,)
    return min(latest, key=lambda item: item.observation_id), ()


def _fiscal_period_matches(period_end: date, fiscal_year_end: str) -> bool:
    month, day = (int(part) for part in fiscal_year_end.split("-"))
    expected_day = min(day, calendar.monthrange(period_end.year, month)[1])
    return (period_end.month, period_end.day) == (month, expected_day)


def _select_forward_ebitda(
    selection: PeerSelectionResult,
    method_input: PeerMethodDataInput,
    *,
    analysis_as_of: datetime,
    forward_period: ForwardPeriodSelection,
):
    binding = method_input.forward_identity_binding
    if (
        binding is None
        or binding.provider != "fmp"
        or binding.peer_security_id != selection.candidate.candidate_security_id
        or binding.peer_issuer_id != selection.candidate.candidate_issuer_id
        or binding.verified_at > analysis_as_of
    ):
        return None, (PeerMethodEvidenceReason.FORWARD_IDENTITY_UNVERIFIED,)
    reasons: list[PeerMethodEvidenceReason] = []
    eligible = []
    for item in method_input.forward_ebitda_evidence:
        if not _matches_peer(item, selection):
            reasons.append(PeerMethodEvidenceReason.IDENTITY_MISMATCH)
            continue
        observation = item.observation
        if (
            observation.provenance.provider.lower() != binding.provider
            or observation.provenance.provider_symbol != binding.provider_symbol
        ):
            reasons.append(PeerMethodEvidenceReason.FORWARD_IDENTITY_UNVERIFIED)
            continue
        if (
            observation.metric_id is not MetricId.EBITDA
            or observation.unit is not MetricUnit.CURRENCY
            or observation.frequency is not Frequency.ANNUAL
            or observation.observation_type is not ObservationType.ESTIMATE
            or observation.currency is None
            or observation.period_end is None
        ):
            reasons.append(PeerMethodEvidenceReason.UNIT_MISMATCH)
            continue
        if observation.as_of_at > analysis_as_of:
            reasons.append(PeerMethodEvidenceReason.FORWARD_ESTIMATE_FUTURE)
            continue
        if observation.period_end <= analysis_as_of.date():
            continue
        if not _fiscal_period_matches(observation.period_end, method_input.peer_fiscal_year_end):
            reasons.append(PeerMethodEvidenceReason.FISCAL_PERIOD_MISMATCH)
            continue
        eligible.append(observation)
    if not eligible:
        reasons.append(PeerMethodEvidenceReason.FORWARD_EBITDA_UNAVAILABLE)
        return None, tuple(dict.fromkeys(reasons))
    period_ends = tuple(sorted({item.period_end for item in eligible}))
    period_index = 0 if forward_period is ForwardPeriodSelection.FY1 else 1
    if len(period_ends) <= period_index:
        return None, (PeerMethodEvidenceReason.FORWARD_PERIOD_UNAVAILABLE,)
    selected_end = period_ends[period_index]
    selected_period = [item for item in eligible if item.period_end == selected_end]
    averages = [item for item in selected_period if item.estimate_case is EstimateCase.AVERAGE]
    if not averages:
        return None, (PeerMethodEvidenceReason.FORWARD_ESTIMATE_CASE_UNAVAILABLE,)
    latest_as_of = max(item.as_of_at for item in averages)
    latest = [item for item in averages if item.as_of_at == latest_as_of]
    signatures = {(item.value, item.currency, item.unit) for item in latest}
    if len(signatures) != 1:
        return None, (PeerMethodEvidenceReason.CONFLICTING_OBSERVATIONS,)
    selected = min(latest, key=lambda item: item.observation_id)
    if not math.isfinite(selected.value) or selected.value <= 0:
        return None, (PeerMethodEvidenceReason.FORWARD_EBITDA_INVALID,)
    return selected, ()


def assess_peer_method_data(
    target_security_id: str,
    target_issuer_id: str,
    selection: PeerSelectionResult,
    method_input: PeerMethodDataInput | None,
    *,
    analysis_as_of: datetime,
    peer_set_id: str,
    forward_period: ForwardPeriodSelection = ForwardPeriodSelection.FY1,
    policy: PeerValuationPolicy = DEFAULT_PEER_VALUATION_POLICY,
) -> tuple[PeerMethodDataEvidence, PeerValuationObservation | None]:
    """Assess one 8A selection; only INCLUDED may produce one peer multiple."""
    _aware(analysis_as_of, "analysis_as_of")
    if not isinstance(peer_set_id, str) or not peer_set_id.strip():
        raise ValueError("peer_set_id must be a non-empty string")
    candidate = selection.candidate
    selection_id = _selection_result_id(selection, analysis_as_of)
    base_parts = (
        target_security_id, target_issuer_id, candidate.candidate_security_id,
        candidate.candidate_issuer_id, selection_id, analysis_as_of.isoformat(),
        forward_period.value, policy.policy_id,
    )
    if selection.status is not PeerSelectionStatus.INCLUDED:
        evidence = PeerMethodDataEvidence(
            evidence_id=stable_peer_method_evidence_id(*base_parts),
            target_security_id=target_security_id,
            target_issuer_id=target_issuer_id,
            peer_security_id=candidate.candidate_security_id,
            peer_issuer_id=candidate.candidate_issuer_id,
            peer_selection_result_id=selection_id,
            economic_selection_status=selection.status,
            method=PeerMethod.EV_EBITDA,
            valuation_basis=ValuationBasis.ENTERPRISE,
            forward_denominator=MetricId.EBITDA,
            selected_forward_period=forward_period,
            estimate_case=EstimateCase.AVERAGE,
            analysis_as_of=analysis_as_of,
            status=PeerMethodEvidenceStatus.UNAVAILABLE,
            reasons=(PeerMethodEvidenceReason.ECONOMIC_MEMBERSHIP_REQUIRED,),
            provenance=(candidate.provenance,),
            policy_id=policy.policy_id,
        )
        return evidence, None
    if method_input is None:
        ev = forward = None
        ev_reasons = (PeerMethodEvidenceReason.ENTERPRISE_VALUE_UNAVAILABLE,)
        forward_reasons = (
            PeerMethodEvidenceReason.FORWARD_IDENTITY_UNVERIFIED,
            PeerMethodEvidenceReason.FORWARD_EBITDA_UNAVAILABLE,
        )
        binding = None
    elif (
        method_input.candidate_id != candidate.candidate_id
        or method_input.peer_security_id != candidate.candidate_security_id
        or method_input.peer_issuer_id != candidate.candidate_issuer_id
    ):
        ev = forward = None
        ev_reasons = forward_reasons = (PeerMethodEvidenceReason.IDENTITY_MISMATCH,)
        binding = method_input.forward_identity_binding
    else:
        ev, ev_reasons = _select_enterprise_value(
            selection, method_input.enterprise_value_evidence,
            analysis_as_of=analysis_as_of, policy=policy,
        )
        forward, forward_reasons = _select_forward_ebitda(
            selection, method_input,
            analysis_as_of=analysis_as_of, forward_period=forward_period,
        )
        binding = method_input.forward_identity_binding
    reasons = list(dict.fromkeys((*ev_reasons, *forward_reasons)))
    if ev is not None and forward is not None and ev.currency != forward.currency:
        reasons.append(PeerMethodEvidenceReason.CURRENCY_MISMATCH)
        forward = None
    complete = ev is not None and forward is not None
    status = (
        PeerMethodEvidenceStatus.AVAILABLE if complete
        else PeerMethodEvidenceStatus.PARTIAL if ev is not None or forward is not None
        else PeerMethodEvidenceStatus.UNAVAILABLE
    )
    if complete:
        reasons = [PeerMethodEvidenceReason.METHOD_DATA_AVAILABLE]
    provenance = tuple(dict.fromkeys((
        candidate.provenance,
        *((ev.provenance,) if ev is not None else ()),
        *((forward.provenance,) if forward is not None else ()),
        *((binding.provenance,) if binding is not None else ()),
    )))
    evidence = PeerMethodDataEvidence(
        evidence_id=stable_peer_method_evidence_id(*base_parts),
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        peer_security_id=candidate.candidate_security_id,
        peer_issuer_id=candidate.candidate_issuer_id,
        peer_selection_result_id=selection_id,
        economic_selection_status=selection.status,
        method=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        forward_denominator=MetricId.EBITDA,
        selected_forward_period=forward_period,
        estimate_case=EstimateCase.AVERAGE,
        analysis_as_of=analysis_as_of,
        status=status,
        reasons=tuple(dict.fromkeys(reasons)),
        enterprise_value_observation_id=ev.observation_id if ev is not None else None,
        enterprise_value_date=ev.period_end if ev is not None else None,
        enterprise_value=ev.value if ev is not None else None,
        enterprise_value_currency=ev.currency if ev is not None else None,
        forward_ebitda_observation_id=forward.observation_id if forward is not None else None,
        forward_period_end=forward.period_end if forward is not None else None,
        forward_estimate_as_of=forward.as_of_at if forward is not None else None,
        forward_ebitda=forward.value if forward is not None else None,
        forward_ebitda_currency=forward.currency if forward is not None else None,
        cross_provider_identity_binding_id=(binding.binding_id if complete and binding is not None else None),
        provenance=provenance,
        policy_id=policy.policy_id,
    )
    if not complete:
        return evidence, None
    multiple = ev.value / forward.value
    observation = PeerValuationObservation(
        observation_id=stable_peer_valuation_observation_id(
            *base_parts, peer_set_id, ev.observation_id, forward.observation_id,
        ),
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        peer_set_id=peer_set_id,
        peer_security_id=candidate.candidate_security_id,
        peer_issuer_id=candidate.candidate_issuer_id,
        peer_selection_result_id=selection_id,
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        enterprise_value=ev.value,
        forward_denominator=MetricId.EBITDA,
        forward_ebitda=forward.value,
        forward_period=forward_period,
        estimate_case=EstimateCase.AVERAGE,
        ev_ebitda_multiple=multiple,
        unit=MetricUnit.RATIO,
        currency=ev.currency,
        enterprise_value_observation_id=ev.observation_id,
        forward_ebitda_observation_id=forward.observation_id,
        enterprise_value_date=ev.period_end,
        forward_period_end=forward.period_end,
        forward_estimate_as_of=forward.as_of_at,
        analysis_as_of=analysis_as_of,
        provenance=provenance,
        policy_id=policy.policy_id,
    )
    return evidence, observation


def build_peer_valuation_subset(
    peer_set: PeerSet,
    method_inputs: Iterable[PeerMethodDataInput] = (),
    *,
    analysis_as_of: datetime | None = None,
    forward_period: ForwardPeriodSelection | None = None,
    policy: PeerValuationPolicy = DEFAULT_PEER_VALUATION_POLICY,
) -> PeerValuationSubset:
    """Build an EV/EBITDA-ready subset from immutable 8A economic membership."""
    snapshot = analysis_as_of or peer_set.analysis_as_of
    _aware(snapshot, "analysis_as_of")
    if snapshot != peer_set.analysis_as_of:
        raise ValueError("peer valuation analysis_as_of must match the economic peer-set snapshot")
    selected_period = forward_period or policy.default_forward_period
    inputs = tuple(method_inputs)
    if len({item.candidate_id for item in inputs}) != len(inputs):
        raise ValueError("method inputs must contain at most one record per candidate")
    by_candidate = {item.candidate_id: item for item in inputs}
    peer_set_id = _peer_set_id(peer_set)
    evidence_records = []
    observations = []
    for selection in peer_set.selections:
        evidence, observation = assess_peer_method_data(
            peer_set.target_security_id,
            peer_set.target_issuer_id,
            selection,
            by_candidate.get(selection.candidate.candidate_id),
            analysis_as_of=snapshot,
            peer_set_id=peer_set_id,
            forward_period=selected_period,
            policy=policy,
        )
        evidence_records.append(evidence)
        if observation is not None:
            observations.append(observation)
    included = tuple(
        item.candidate.candidate_issuer_id
        for item in peer_set.selections
        if item.status is PeerSelectionStatus.INCLUDED
    )
    valid_by_issuer = {item.peer_issuer_id: item for item in observations}
    available = tuple(issuer for issuer in included if issuer in valid_by_issuer)
    unavailable = tuple(issuer for issuer in included if issuer not in valid_by_issuer)
    selected_observations = tuple(valid_by_issuer[issuer] for issuer in available)
    valid_count = len(selected_observations)
    if not included:
        status = PeerValuationSubsetStatus.UNAVAILABLE
    elif valid_count >= policy.minimum_valid_observations and peer_set.status is PeerSetStatus.USABLE:
        status = PeerValuationSubsetStatus.USABLE
    elif valid_count and valid_count < len(included):
        status = PeerValuationSubsetStatus.PARTIAL
    else:
        status = PeerValuationSubsetStatus.INSUFFICIENT
    warnings = []
    if not included:
        warnings.append("No INCLUDED economic peers are eligible for method-data assessment.")
    if valid_count < policy.minimum_valid_observations:
        warnings.append(
            f"Only {valid_count} valid independent issuer observations; policy requires "
            f"{policy.minimum_valid_observations}."
        )
    if peer_set.status is not PeerSetStatus.USABLE:
        warnings.append("A non-USABLE economic peer set cannot be rescued by valuation data.")
    provenance = tuple(dict.fromkeys(
        item for observation in selected_observations for item in observation.provenance
    ))
    return PeerValuationSubset(
        subset_id=stable_peer_valuation_subset_id(
            peer_set_id, PeerMethod.EV_EBITDA.value, snapshot.isoformat(),
            selected_period.value, policy.policy_id,
        ),
        target_security_id=peer_set.target_security_id,
        target_issuer_id=peer_set.target_issuer_id,
        peer_set_id=peer_set_id,
        method=PeerMethod.EV_EBITDA,
        analysis_as_of=snapshot,
        selected_forward_period=selected_period,
        estimate_case=EstimateCase.AVERAGE,
        included_economic_peer_count=len(included),
        method_data_available_peer_issuer_ids=available,
        method_data_unavailable_peer_issuer_ids=unavailable,
        valid_observation_ids=tuple(item.observation_id for item in selected_observations),
        valid_observation_count=valid_count,
        minimum_required_valid_observations=policy.minimum_valid_observations,
        status=status,
        method_data_evidence=tuple(evidence_records),
        observations=selected_observations,
        policy_id=policy.policy_id,
        warnings=tuple(warnings),
        provenance=provenance,
    )
