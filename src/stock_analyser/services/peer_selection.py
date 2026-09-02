"""Deterministic, non-valuing peer comparability selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Iterable

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataAvailability,
    EstimateCase,
    Frequency,
    IndustryComparability,
    MetricId,
    MetricObservation,
    ObservationType,
    PeerCandidate,
    PeerComparabilityEvidence,
    PeerCriterion,
    PeerCriterionEvidence,
    PeerCriterionStatus,
    PeerIdentityEvidence,
    PeerMethod,
    PeerMethodDataReadiness,
    PeerMethodDataReason,
    PeerMethodDataStatus,
    PeerSecurityEligibilityEvidence,
    PeerSecurityEligibilityStatus,
    PeerSelectionReason,
    PeerSelectionResult,
    PeerSelectionStatus,
    PeerSet,
    PeerSetStatus,
    Provenance,
    peer_identity_evidence_from_company,
)
from stock_analyser.services.actual_selection import select_canonical_actual


def _canon(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


_UNKNOWN_CLASSIFICATIONS = frozenset({"", "unknown", "unavailable", "notavailable", "na", "other"})
_COMMON_EQUITY_TYPES = frozenset({
    "ordinaryshare", "ordinaryshares", "commonstock", "commonshare",
    "commonshares", "equity",
})
_OTHER_EQUITY_TYPES = frozenset({
    "adr", "depositaryreceipt", "depositaryreceipts", "preferredshare",
    "preferredshares", "preferredstock",
})
_NON_EQUITY_TYPES = frozenset({
    "etf", "exchangetradedfund", "fund", "mutualfund", "warrant", "warrants",
    "bond", "bonds", "right", "rights",
})
_EXCHANGE_EQUIVALENCE_GROUPS = (
    frozenset({"nasdaq", "nms", "ngm", "ncm", "xnas"}),
    frozenset({"nyse", "nyq", "xnys"}),
    frozenset({"hkex", "hkg", "xhkg"}),
    frozenset({"lse", "londonstockexchange", "xlon"}),
)


def _exchanges_compatible(left: str, right: str) -> bool:
    normalized = (_canon(left), _canon(right))
    if normalized[0] == normalized[1]:
        return True
    return any(normalized[0] in group and normalized[1] in group for group in _EXCHANGE_EQUIVALENCE_GROUPS)


@dataclass(frozen=True, slots=True)
class PeerSelectionPolicy:
    policy_id: str = "v1-peer-selection-8a2"
    minimum_peer_count: int = 3
    minimum_scale_ratio: float = 0.10
    maximum_scale_ratio: float = 10.0
    maximum_revenue_growth_gap: float = 0.25
    maximum_ebitda_margin_gap: float = 0.20
    eligible_security_types: tuple[str, ...] = (
        "ordinary share", "ordinary shares", "common stock", "common_stock",
        "common share", "common shares", "equity", "adr", "depositary receipt",
    )
    related_industry_groups: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        if isinstance(self.minimum_peer_count, bool) or self.minimum_peer_count < 1:
            raise ValueError("minimum_peer_count must be positive")
        for name in (
            "minimum_scale_ratio", "maximum_scale_ratio",
            "maximum_revenue_growth_gap", "maximum_ebitda_margin_gap",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.minimum_scale_ratio <= 0 or self.minimum_scale_ratio > self.maximum_scale_ratio:
            raise ValueError("scale ratio bounds must be positive and ordered")
        if not self.eligible_security_types:
            raise ValueError("eligible_security_types must not be empty")


DEFAULT_PEER_SELECTION_POLICY = PeerSelectionPolicy()


@dataclass(frozen=True, slots=True)
class PeerCompanyEvidence:
    identity: CompanyIdentity | PeerIdentityEvidence
    actual_observations: tuple[MetricObservation, ...] = ()
    forward_observations: tuple[MetricObservation, ...] = ()
    business_model: str | None = None
    business_model_provenance: Provenance | None = None
    security_eligibility: PeerSecurityEligibilityEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, (CompanyIdentity, PeerIdentityEvidence)):
            raise TypeError("identity must be CompanyIdentity or bounded PeerIdentityEvidence")
        object.__setattr__(self, "actual_observations", tuple(self.actual_observations))
        object.__setattr__(self, "forward_observations", tuple(self.forward_observations))
        if self.business_model is not None and not self.business_model.strip():
            raise ValueError("business_model must be non-empty when present")
        if self.business_model is not None and self.business_model_provenance is None:
            raise ValueError("a business-model label requires provenance")
        if self.security_eligibility is not None and (
            self.security_eligibility.security_id != self.identity.security_id
            or self.security_eligibility.issuer_id != self.identity.issuer_id
        ):
            raise ValueError("security eligibility must be bound to the company identity")


@dataclass(frozen=True, slots=True)
class PeerCandidateInput:
    candidate: PeerCandidate
    company: PeerCompanyEvidence


def classify_peer_security_eligibility(
    identity: CompanyIdentity | PeerIdentityEvidence,
    *,
    security_type: str | None,
    provenance: Provenance,
    listing_symbol: str,
    exchange: str,
) -> PeerSecurityEligibilityEvidence:
    """Classify security evidence without creating or changing canonical identity."""
    expected_symbols = {identity.canonical_symbol}
    if isinstance(identity, PeerIdentityEvidence):
        expected_symbols.add(identity.provider_symbol)
    symbol_compatible = _canon(listing_symbol) in {_canon(value) for value in expected_symbols}
    exchange_compatible = _exchanges_compatible(exchange, identity.exchange)
    normalized_type = _canon(security_type or "")
    if not symbol_compatible or not exchange_compatible:
        status = PeerSecurityEligibilityStatus.UNVERIFIED
        note = (
            "Security-class evidence was not applied because listing symbol or exchange "
            "was incompatible with the already anchored canonical security."
        )
    elif normalized_type in _COMMON_EQUITY_TYPES:
        status = PeerSecurityEligibilityStatus.VERIFIED_COMMON_EQUITY
        note = "Authoritative listing evidence classifies the anchored security as common equity."
    elif normalized_type in _OTHER_EQUITY_TYPES:
        status = PeerSecurityEligibilityStatus.VERIFIED_OTHER_EQUITY
        note = "Authoritative listing evidence classifies the anchored security as another equity class."
    elif normalized_type in _NON_EQUITY_TYPES:
        status = PeerSecurityEligibilityStatus.VERIFIED_NON_EQUITY
        note = "Authoritative listing evidence classifies the anchored security as non-equity."
    else:
        status = PeerSecurityEligibilityStatus.UNVERIFIED
        note = "Security class is absent or outside the controlled classification vocabulary."
    return PeerSecurityEligibilityEvidence(
        status=status,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        provider=provenance.provider,
        provider_symbol=provenance.provider_symbol,
        listing_symbol=listing_symbol,
        exchange=exchange,
        security_type=security_type,
        source_as_of=provenance.as_of_at,
        retrieved_at=provenance.retrieved_at,
        provenance=provenance,
        note=note,
    )


def _criterion(
    criterion: PeerCriterion,
    status: PeerCriterionStatus,
    *,
    target_value: str | float | None = None,
    candidate_value: str | float | None = None,
    comparison_value: float | None = None,
    currency: str | None = None,
    observations: Iterable[MetricObservation] = (),
    extra_provenance: Iterable[Provenance] = (),
    note: str | None = None,
) -> PeerCriterionEvidence:
    records = tuple(observations)
    provenance = tuple(dict.fromkeys((*tuple(item.provenance for item in records), *tuple(extra_provenance))))
    return PeerCriterionEvidence(
        criterion=criterion,
        status=status,
        target_value=target_value,
        candidate_value=candidate_value,
        comparison_value=comparison_value,
        currency=currency,
        input_observation_ids=tuple(dict.fromkeys(item.observation_id for item in records)),
        provenance=provenance,
        note=note,
    )


def _available_actuals(
    profile: PeerCompanyEvidence,
    metric: MetricId,
    analysis_as_of: datetime,
) -> tuple[MetricObservation, ...]:
    return tuple(sorted((
        item for item in profile.actual_observations
        if item.metric_id is metric
        and item.observation_type is ObservationType.ACTUAL
        and item.frequency is Frequency.ANNUAL
        and item.period_end is not None
        and item.period_end <= analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    ), key=lambda item: (item.period_end, item.as_of_at, item.retrieved_at), reverse=True))


def _period_value(
    observations: tuple[MetricObservation, ...],
    period_end,
    analysis_as_of: datetime,
) -> MetricObservation | None:
    rows = tuple(item for item in observations if item.period_end == period_end)
    if not rows:
        return None
    selection = select_canonical_actual(
        rows,
        metric_id=rows[0].metric_id,
        frequency=Frequency.ANNUAL,
        period_end=period_end,
        analysis_as_of=analysis_as_of,
    )
    return selection.selected_observation


def _latest_actual(
    profile: PeerCompanyEvidence,
    metric: MetricId,
    analysis_as_of: datetime,
) -> MetricObservation | None:
    rows = _available_actuals(profile, metric, analysis_as_of)
    return _period_value(rows, rows[0].period_end, analysis_as_of) if rows else None


def _revenue_growth(
    profile: PeerCompanyEvidence,
    analysis_as_of: datetime,
) -> tuple[float, tuple[MetricObservation, ...]] | None:
    rows = _available_actuals(profile, MetricId.REVENUE, analysis_as_of)
    periods = tuple(dict.fromkeys(item.period_end for item in rows))
    if len(periods) < 2:
        return None
    latest = _period_value(rows, periods[0], analysis_as_of)
    prior = _period_value(rows, periods[1], analysis_as_of)
    if latest is None or prior is None or latest.currency != prior.currency or prior.value <= 0:
        return None
    return latest.value / prior.value - 1.0, (latest, prior)


def _ebitda_margin(
    profile: PeerCompanyEvidence,
    analysis_as_of: datetime,
) -> tuple[float, tuple[MetricObservation, ...]] | None:
    revenue_rows = _available_actuals(profile, MetricId.REVENUE, analysis_as_of)
    ebitda_rows = _available_actuals(profile, MetricId.EBITDA, analysis_as_of)
    common_periods = sorted(
        {item.period_end for item in revenue_rows} & {item.period_end for item in ebitda_rows},
        reverse=True,
    )
    for period_end in common_periods:
        revenue = _period_value(revenue_rows, period_end, analysis_as_of)
        ebitda = _period_value(ebitda_rows, period_end, analysis_as_of)
        if (
            revenue is not None and ebitda is not None
            and revenue.currency == ebitda.currency and revenue.value > 0
        ):
            return ebitda.value / revenue.value, (revenue, ebitda)
    return None


def _forward_ebitda_observations(
    profile: PeerCompanyEvidence,
    analysis_as_of: datetime,
) -> tuple[MetricObservation, ...]:
    return tuple(item for item in profile.forward_observations if (
        item.metric_id is MetricId.EBITDA
        and item.observation_type is ObservationType.ESTIMATE
        and item.frequency is Frequency.ANNUAL
        and item.estimate_case is EstimateCase.AVERAGE
        and item.period_end is not None
        and item.period_end > analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
        and item.value > 0
    ))


def assess_peer_method_data_readiness(
    candidate: PeerCandidate,
    company: PeerCompanyEvidence,
    *,
    analysis_as_of: datetime,
    required_multiple_inputs_available: bool = False,
) -> PeerMethodDataReadiness:
    """Describe EV/EBITDA input coverage without calculating a multiple."""
    forward = _forward_ebitda_observations(company, analysis_as_of)
    forward_available = bool(forward)
    if forward_available and required_multiple_inputs_available:
        status = PeerMethodDataStatus.READY
        reasons = (PeerMethodDataReason.METHOD_INPUTS_AVAILABLE,)
    elif forward_available:
        status = PeerMethodDataStatus.PARTIAL
        reasons = (PeerMethodDataReason.REQUIRED_MULTIPLE_INPUTS_UNAVAILABLE,)
    else:
        status = PeerMethodDataStatus.UNAVAILABLE
        reasons = (
            PeerMethodDataReason.FORWARD_DENOMINATOR_UNAVAILABLE,
            PeerMethodDataReason.REQUIRED_MULTIPLE_INPUTS_UNAVAILABLE,
        )
    return PeerMethodDataReadiness(
        candidate_id=candidate.candidate_id,
        peer_security_id=candidate.candidate_security_id,
        peer_issuer_id=candidate.candidate_issuer_id,
        analysis_as_of=analysis_as_of,
        method=PeerMethod.EV_EBITDA,
        forward_denominator_available=forward_available,
        required_multiple_inputs_available=required_multiple_inputs_available,
        status=status,
        reasons=reasons,
        supporting_observation_ids=tuple(item.observation_id for item in forward),
        provenance=tuple(item.provenance for item in forward),
    )


def _industry_comparability(
    target: CompanyIdentity | PeerIdentityEvidence,
    candidate: CompanyIdentity | PeerIdentityEvidence,
    policy: PeerSelectionPolicy,
) -> IndustryComparability:
    target_industry = _canon(target.industry or "")
    candidate_industry = _canon(candidate.industry or "")
    if target_industry in _UNKNOWN_CLASSIFICATIONS or candidate_industry in _UNKNOWN_CLASSIFICATIONS:
        return IndustryComparability.UNRESOLVED
    if target_industry == candidate_industry:
        return IndustryComparability.SAME_INDUSTRY
    for group in policy.related_industry_groups:
        normalized = {_canon(value) for value in group}
        if target_industry in normalized and candidate_industry in normalized:
            return IndustryComparability.RELATED_INDUSTRY
    target_sector = _canon(target.sector or "")
    candidate_sector = _canon(candidate.sector or "")
    if (
        target_sector not in _UNKNOWN_CLASSIFICATIONS
        and target_sector == candidate_sector
    ):
        return IndustryComparability.SECTOR_ONLY
    return IndustryComparability.MISMATCH


def _security_preference(
    company: PeerCompanyEvidence,
    candidate: PeerCandidate,
) -> tuple[int, int, str]:
    security_type = _canon(
        company.security_eligibility.security_type
        if company.security_eligibility is not None and company.security_eligibility.security_type is not None
        else company.identity.security_type or ""
    )
    depositary = "adr" in security_type or "depositary" in security_type
    ordinary = security_type in {_canon(value) for value in ("ordinary share", "common stock", "common share", "equity")}
    security_rank = 0 if ordinary else 1 if not depositary else 2
    source_rank = candidate.source_rank if candidate.source_rank is not None else 2**31 - 1
    return security_rank, source_rank, candidate.candidate_id


def evaluate_peer_candidate(
    target: PeerCompanyEvidence,
    candidate_input: PeerCandidateInput,
    *,
    analysis_as_of: datetime,
    provider_agreement: tuple[str, ...] = (),
    policy: PeerSelectionPolicy = DEFAULT_PEER_SELECTION_POLICY,
) -> PeerSelectionResult:
    if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    candidate = candidate_input.candidate
    company = candidate_input.company
    criteria: list[PeerCriterionEvidence] = []
    hard_reasons: list[PeerSelectionReason] = []
    unverified_reasons: list[PeerSelectionReason] = []
    warnings: list[str] = []

    identity_matches = (
        candidate.target_security_id == target.identity.security_id
        and candidate.target_issuer_id == target.identity.issuer_id
        and candidate.candidate_security_id == company.identity.security_id
        and candidate.candidate_issuer_id == company.identity.issuer_id
    )
    criteria.append(_criterion(
        PeerCriterion.IDENTITY,
        PeerCriterionStatus.PASS if identity_matches else PeerCriterionStatus.FAIL,
        target_value=target.identity.issuer_id,
        candidate_value=company.identity.issuer_id,
        extra_provenance=(candidate.provenance,),
        note="Canonical security and issuer identifiers, never provider ticker text, control the join.",
    ))
    if not identity_matches:
        hard_reasons.append(PeerSelectionReason.IDENTITY_MISMATCH)

    self_reference = company.identity.issuer_id == target.identity.issuer_id
    criteria.append(_criterion(
        PeerCriterion.SELF_REFERENCE,
        PeerCriterionStatus.FAIL if self_reference else PeerCriterionStatus.PASS,
        target_value=target.identity.issuer_id,
        candidate_value=company.identity.issuer_id,
    ))
    if self_reference:
        hard_reasons.append(PeerSelectionReason.SELF_REFERENCE)

    security_evidence = company.security_eligibility or classify_peer_security_eligibility(
        company.identity,
        security_type=company.identity.security_type,
        provenance=candidate.provenance,
        listing_symbol=company.identity.canonical_symbol,
        exchange=company.identity.exchange,
    )
    allowed_types = {_canon(value) for value in policy.eligible_security_types}
    candidate_type = _canon(security_evidence.security_type or "")
    security_eligible = (
        security_evidence.status in {
            PeerSecurityEligibilityStatus.VERIFIED_COMMON_EQUITY,
            PeerSecurityEligibilityStatus.VERIFIED_OTHER_EQUITY,
        }
        and candidate_type in allowed_types
    )
    security_unverified = security_evidence.status is PeerSecurityEligibilityStatus.UNVERIFIED
    criteria.append(_criterion(
        PeerCriterion.SECURITY_TYPE,
        (
            PeerCriterionStatus.PASS if security_eligible
            else PeerCriterionStatus.UNRESOLVED if security_unverified
            else PeerCriterionStatus.FAIL
        ),
        target_value=target.identity.security_type,
        candidate_value=security_evidence.security_type,
        extra_provenance=(security_evidence.provenance,),
        note=security_evidence.note,
    ))
    if security_unverified:
        unverified_reasons.append(PeerSelectionReason.SECURITY_ELIGIBILITY_UNVERIFIED)
    elif not security_eligible:
        hard_reasons.append(PeerSelectionReason.WRONG_SECURITY_TYPE)
    is_depositary = "adr" in candidate_type or "depositary" in candidate_type
    if is_depositary and (
        getattr(company.identity, "identity_availability", DataAvailability.PARTIAL) is DataAvailability.PARTIAL
        or getattr(company.identity, "adr_ratio", None) is None
    ):
        unverified_reasons.append(PeerSelectionReason.ADR_CONVERSION_UNRESOLVED)

    industry = _industry_comparability(target.identity, company.identity, policy)
    industry_status = {
        IndustryComparability.SAME_INDUSTRY: PeerCriterionStatus.PASS,
        IndustryComparability.RELATED_INDUSTRY: PeerCriterionStatus.PASS,
        IndustryComparability.SECTOR_ONLY: PeerCriterionStatus.UNRESOLVED,
        IndustryComparability.MISMATCH: PeerCriterionStatus.FAIL,
        IndustryComparability.UNRESOLVED: PeerCriterionStatus.UNRESOLVED,
    }[industry]
    criteria.append(_criterion(
        PeerCriterion.INDUSTRY,
        industry_status,
        target_value=f"{target.identity.sector or 'unavailable'} / {target.identity.industry or 'unavailable'}",
        candidate_value=f"{company.identity.sector or 'unavailable'} / {company.identity.industry or 'unavailable'}",
        note=f"Controlled industry level: {industry.value}.",
    ))
    if industry is IndustryComparability.MISMATCH:
        hard_reasons.append(PeerSelectionReason.INDUSTRY_MISMATCH)
    elif industry is IndustryComparability.SECTOR_ONLY:
        unverified_reasons.append(PeerSelectionReason.SECTOR_ONLY)
    elif industry is IndustryComparability.UNRESOLVED:
        unverified_reasons.append(PeerSelectionReason.INDUSTRY_UNRESOLVED)

    if target.business_model is None or company.business_model is None:
        criteria.append(_criterion(
            PeerCriterion.BUSINESS_MODEL,
            PeerCriterionStatus.UNRESOLVED,
            target_value=target.business_model,
            candidate_value=company.business_model,
            note="No business-model label was inferred from a company name or generated by an LLM.",
        ))
        warnings.append("Business-model comparability remains unverified; no label was invented.")
    else:
        models_match = _canon(target.business_model) == _canon(company.business_model)
        criteria.append(_criterion(
            PeerCriterion.BUSINESS_MODEL,
            PeerCriterionStatus.PASS if models_match else PeerCriterionStatus.FAIL,
            target_value=target.business_model,
            candidate_value=company.business_model,
            extra_provenance=tuple(
                item for item in (target.business_model_provenance, company.business_model_provenance)
                if item is not None
            ),
        ))
        if not models_match:
            hard_reasons.append(PeerSelectionReason.BUSINESS_MODEL_MISMATCH)

    target_revenue = _latest_actual(target, MetricId.REVENUE, analysis_as_of)
    candidate_revenue = _latest_actual(company, MetricId.REVENUE, analysis_as_of)
    if target_revenue is None or candidate_revenue is None:
        scale = _criterion(
            PeerCriterion.SCALE, PeerCriterionStatus.UNRESOLVED,
            target_value=None if target_revenue is None else target_revenue.value,
            candidate_value=None if candidate_revenue is None else candidate_revenue.value,
            observations=tuple(item for item in (target_revenue, candidate_revenue) if item is not None),
            note="Missing canonical revenue stayed missing and was not converted to zero.",
        )
    elif target_revenue.currency != candidate_revenue.currency:
        scale = _criterion(
            PeerCriterion.SCALE, PeerCriterionStatus.UNRESOLVED,
            target_value=target_revenue.value,
            candidate_value=candidate_revenue.value,
            observations=(target_revenue, candidate_revenue),
            note=(
                f"Absolute scale was not compared across {target_revenue.currency} and "
                f"{candidate_revenue.currency}; no FX conversion was introduced."
            ),
        )
        unverified_reasons.append(PeerSelectionReason.CURRENCY_COMPARABILITY_UNRESOLVED)
    elif target_revenue.value <= 0 or candidate_revenue.value <= 0:
        scale = _criterion(
            PeerCriterion.SCALE, PeerCriterionStatus.UNRESOLVED,
            target_value=target_revenue.value, candidate_value=candidate_revenue.value,
            currency=target_revenue.currency, observations=(target_revenue, candidate_revenue),
            note="Non-positive revenue cannot establish an absolute scale ratio.",
        )
    else:
        ratio = candidate_revenue.value / target_revenue.value
        comparable = policy.minimum_scale_ratio <= ratio <= policy.maximum_scale_ratio
        scale = _criterion(
            PeerCriterion.SCALE,
            PeerCriterionStatus.PASS if comparable else PeerCriterionStatus.FAIL,
            target_value=target_revenue.value, candidate_value=candidate_revenue.value,
            comparison_value=ratio, currency=target_revenue.currency,
            observations=(target_revenue, candidate_revenue),
            note="Candidate-to-target revenue ratio under centralized same-currency bounds.",
        )
        if not comparable:
            hard_reasons.append(PeerSelectionReason.SCALE_NOT_COMPARABLE)
    criteria.append(scale)

    target_growth = _revenue_growth(target, analysis_as_of)
    candidate_growth = _revenue_growth(company, analysis_as_of)
    if target_growth is None or candidate_growth is None:
        growth = _criterion(
            PeerCriterion.GROWTH, PeerCriterionStatus.UNRESOLVED,
            target_value=None if target_growth is None else target_growth[0],
            candidate_value=None if candidate_growth is None else candidate_growth[0],
            observations=(
                *(target_growth[1] if target_growth is not None else ()),
                *(candidate_growth[1] if candidate_growth is not None else ()),
            ),
            note="Missing canonical revenue periods stayed missing and were not converted to zero.",
        )
    else:
        gap = abs(target_growth[0] - candidate_growth[0])
        comparable = gap <= policy.maximum_revenue_growth_gap
        growth = _criterion(
            PeerCriterion.GROWTH,
            PeerCriterionStatus.PASS if comparable else PeerCriterionStatus.FAIL,
            target_value=target_growth[0], candidate_value=candidate_growth[0],
            comparison_value=gap,
            observations=(*target_growth[1], *candidate_growth[1]),
            note="Dimensionless revenue growth may be compared across reporting currencies.",
        )
        if not comparable:
            hard_reasons.append(PeerSelectionReason.GROWTH_NOT_COMPARABLE)
    criteria.append(growth)

    target_margin = _ebitda_margin(target, analysis_as_of)
    candidate_margin = _ebitda_margin(company, analysis_as_of)
    if target_margin is None or candidate_margin is None:
        margin = _criterion(
            PeerCriterion.MARGIN, PeerCriterionStatus.UNRESOLVED,
            target_value=None if target_margin is None else target_margin[0],
            candidate_value=None if candidate_margin is None else candidate_margin[0],
            observations=(
                *(target_margin[1] if target_margin is not None else ()),
                *(candidate_margin[1] if candidate_margin is not None else ()),
            ),
            note="Missing canonical revenue or EBITDA stayed missing and was not converted to zero.",
        )
    else:
        gap = abs(target_margin[0] - candidate_margin[0])
        comparable = gap <= policy.maximum_ebitda_margin_gap
        margin = _criterion(
            PeerCriterion.MARGIN,
            PeerCriterionStatus.PASS if comparable else PeerCriterionStatus.FAIL,
            target_value=target_margin[0], candidate_value=candidate_margin[0],
            comparison_value=gap,
            observations=(*target_margin[1], *candidate_margin[1]),
            note="Dimensionless EBITDA margin may be compared across reporting currencies.",
        )
        if not comparable:
            hard_reasons.append(PeerSelectionReason.MARGIN_NOT_COMPARABLE)
    criteria.append(margin)

    agreement = tuple(sorted(set(provider_agreement or (candidate.provider,))))
    criteria.append(_criterion(
        PeerCriterion.PROVIDER_AGREEMENT,
        PeerCriterionStatus.PASS if len(agreement) > 1 else PeerCriterionStatus.NOT_EVALUATED,
        target_value="provider count",
        candidate_value=float(len(agreement)),
        extra_provenance=(candidate.provenance,),
        note="Provider agreement is audit evidence only and never a quality score or inclusion rule.",
    ))

    financial_criteria = (scale, growth, margin)
    if not any(item.status is PeerCriterionStatus.PASS for item in financial_criteria):
        unverified_reasons.append(PeerSelectionReason.INSUFFICIENT_FINANCIAL_DATA)
    elif any(item.status is PeerCriterionStatus.PASS for item in (growth, margin)):
        unverified_reasons = [
            reason for reason in unverified_reasons
            if reason is not PeerSelectionReason.CURRENCY_COMPARABILITY_UNRESOLVED
        ]

    if hard_reasons:
        status = PeerSelectionStatus.EXCLUDED
        reasons = tuple(dict.fromkeys(hard_reasons))
    elif unverified_reasons:
        status = PeerSelectionStatus.UNVERIFIED
        reasons = tuple(dict.fromkeys(unverified_reasons))
    else:
        status = PeerSelectionStatus.INCLUDED
        reasons = (PeerSelectionReason.INCLUSION_RULES_SATISFIED,)

    evidence = PeerComparabilityEvidence(
        candidate_id=candidate.candidate_id,
        analysis_as_of=analysis_as_of,
        identity=(
            company.identity
            if isinstance(company.identity, PeerIdentityEvidence)
            else peer_identity_evidence_from_company(
                company.identity, provenance=candidate.provenance,
            )
        ),
        security_eligibility=security_evidence,
        industry_comparability=industry,
        criteria=tuple(criteria),
        provider_agreement=agreement,
        warnings=tuple(warnings),
    )
    return PeerSelectionResult(candidate, status, reasons, evidence)


def select_peer_set(
    target: PeerCompanyEvidence,
    candidates: Iterable[PeerCandidateInput],
    *,
    analysis_as_of: datetime,
    provider_capabilities: Iterable[CapabilityResult] = (),
    policy: PeerSelectionPolicy = DEFAULT_PEER_SELECTION_POLICY,
) -> PeerSet:
    if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    inputs = tuple(candidates)
    capabilities = tuple(provider_capabilities)
    providers_by_issuer: dict[str, set[str]] = {}
    issuer_groups: dict[str, list[PeerCandidateInput]] = {}
    for item in inputs:
        issuer = item.candidate.candidate_issuer_id
        providers_by_issuer.setdefault(issuer, set()).add(item.candidate.provider)
        issuer_groups.setdefault(issuer, []).append(item)

    representative_ids: dict[str, str] = {}
    for issuer, group in issuer_groups.items():
        non_self = tuple(item for item in group if issuer != target.identity.issuer_id)
        eligible_group = non_self or tuple(group)
        representative = min(
            eligible_group,
            key=lambda item: _security_preference(item.company, item.candidate),
        )
        representative_ids[issuer] = representative.candidate.candidate_id

    selections: list[PeerSelectionResult] = []
    method_readiness: list[PeerMethodDataReadiness] = []
    for item in inputs:
        agreement = tuple(sorted(providers_by_issuer[item.candidate.candidate_issuer_id]))
        result = evaluate_peer_candidate(
            target, item, analysis_as_of=analysis_as_of,
            provider_agreement=agreement, policy=policy,
        )
        is_self = item.candidate.candidate_issuer_id == target.identity.issuer_id
        if (
            not is_self
            and representative_ids[item.candidate.candidate_issuer_id] != item.candidate.candidate_id
        ):
            result = PeerSelectionResult(
                candidate=result.candidate,
                status=PeerSelectionStatus.EXCLUDED,
                reasons=(PeerSelectionReason.DUPLICATE_ISSUER,),
                evidence=result.evidence,
            )
        selections.append(result)
        method_readiness.append(assess_peer_method_data_readiness(
            item.candidate,
            item.company,
            analysis_as_of=analysis_as_of,
        ))

    included = tuple(item for item in selections if item.status is PeerSelectionStatus.INCLUDED)
    if len(included) >= policy.minimum_peer_count:
        set_status = PeerSetStatus.USABLE
    elif included or any(item.status is PeerSelectionStatus.UNVERIFIED for item in selections):
        set_status = PeerSetStatus.PARTIAL
    elif inputs:
        set_status = PeerSetStatus.INSUFFICIENT
    elif capabilities and not any(item.status is CapabilityStatus.AVAILABLE for item in capabilities):
        set_status = PeerSetStatus.UNAVAILABLE
    else:
        set_status = PeerSetStatus.INSUFFICIENT

    warnings = []
    if len(included) < policy.minimum_peer_count:
        warnings.append(
            f"Only {len(included)} independent included issuers; policy requires {policy.minimum_peer_count}."
        )
    if set_status is PeerSetStatus.UNAVAILABLE:
        warnings.append("Candidate provider discovery is unavailable; peer selection failed closed.")

    return PeerSet(
        target_security_id=target.identity.security_id,
        target_issuer_id=target.identity.issuer_id,
        analysis_as_of=analysis_as_of,
        candidate_count=len(inputs),
        included_peer_issuer_ids=tuple(item.candidate.candidate_issuer_id for item in included),
        included_security_ids=tuple(item.candidate.candidate_security_id for item in included),
        excluded_candidate_ids=tuple(
            item.candidate.candidate_id for item in selections
            if item.status is PeerSelectionStatus.EXCLUDED
        ),
        unverified_candidate_ids=tuple(
            item.candidate.candidate_id for item in selections
            if item.status is PeerSelectionStatus.UNVERIFIED
        ),
        unavailable_candidate_ids=tuple(
            item.candidate.candidate_id for item in selections
            if item.status is PeerSelectionStatus.UNAVAILABLE
        ),
        minimum_required_peers=policy.minimum_peer_count,
        status=set_status,
        policy_id=policy.policy_id,
        selections=tuple(selections),
        method_data_readiness=tuple(method_readiness),
        warnings=tuple(warnings),
        provenance=tuple(dict.fromkeys(item.candidate.provenance for item in inputs)),
    )
