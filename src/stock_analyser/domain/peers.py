"""Immutable provider-independent peer discovery and selection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import re
from typing import Any

from .enums import (
    IndustryComparability,
    PeerCandidateSource,
    PeerCriterion,
    PeerCriterionStatus,
    PeerIdentityEvidenceStatus,
    PeerMethod,
    PeerMethodDataReason,
    PeerMethodDataStatus,
    PeerSecurityEligibilityStatus,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
)
from .models import CompanyIdentity, DataIssue, Provenance


_SENSITIVE_RE = re.compile(r"(?:authorization|api[_ -]?key|bearer\s+)", re.IGNORECASE)


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if _SENSITIVE_RE.search(normalized):
        raise ValueError(f"{field_name} must not contain secret-bearing content")
    return normalized


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _finite_optional(value: float | None, field_name: str) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ValueError(f"{field_name} must be finite when present")


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"


def stable_peer_candidate_id(
    *,
    target_security_id: str,
    candidate_security_id: str,
    provider: str,
    provider_symbol: str,
    candidate_source: PeerCandidateSource,
) -> str:
    return _stable_id(
        "peer-candidate",
        target_security_id,
        candidate_security_id,
        provider.lower(),
        provider_symbol,
        candidate_source.value,
    )


@dataclass(frozen=True, slots=True)
class PeerCandidate:
    candidate_id: str
    target_security_id: str
    target_issuer_id: str
    candidate_security_id: str
    candidate_issuer_id: str
    provider: str
    provider_symbol: str
    canonical_symbol: str
    candidate_source: PeerCandidateSource
    source_as_of: datetime
    retrieved_at: datetime
    provenance: Provenance
    source_rank: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "candidate_id", "target_security_id", "target_issuer_id",
            "candidate_security_id", "candidate_issuer_id", "provider",
            "provider_symbol", "canonical_symbol",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "provider", self.provider.lower())
        _aware(self.source_as_of, "source_as_of")
        _aware(self.retrieved_at, "retrieved_at")
        if self.source_rank is not None and (
            isinstance(self.source_rank, bool)
            or not isinstance(self.source_rank, int)
            or self.source_rank < 1
        ):
            raise ValueError("source_rank must be a positive integer when present")
        if self.provenance.provider.lower() != self.provider:
            raise ValueError("candidate provider must match provenance provider")
        if self.provenance.provider_symbol != self.provider_symbol:
            raise ValueError("candidate provider_symbol must match provenance")
        if self.provenance.retrieved_at != self.retrieved_at:
            raise ValueError("candidate retrieved_at must match provenance")
        if self.provenance.as_of_at != self.source_as_of:
            raise ValueError("candidate source_as_of must match provenance")


@dataclass(frozen=True, slots=True)
class PeerIdentityEvidence:
    """Bounded peer identity; security classification is separate evidence."""

    status: PeerIdentityEvidenceStatus
    canonical_symbol: str
    security_id: str
    issuer_id: str
    provider: str
    provider_symbol: str
    listing_country: str | None
    exchange: str
    security_type: str | None
    source_as_of: datetime
    retrieved_at: datetime
    provenance: Provenance
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    unavailable_enrichment_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "canonical_symbol", "security_id", "issuer_id", "provider",
            "provider_symbol", "exchange",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "provider", self.provider.lower())
        for name in ("listing_country", "security_type", "company_name", "sector", "industry"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        _aware(self.source_as_of, "source_as_of")
        _aware(self.retrieved_at, "retrieved_at")
        if self.status is PeerIdentityEvidenceStatus.UNRESOLVED:
            raise ValueError("unresolved identity evidence cannot seed a canonical peer identity")
        if self.provenance.provider.lower() != self.provider:
            raise ValueError("identity provider must match provenance provider")
        if self.provenance.provider_symbol != self.provider_symbol:
            raise ValueError("identity provider_symbol must match provenance")
        fields = tuple(_text(value, "unavailable_enrichment_field") for value in self.unavailable_enrichment_fields)
        object.__setattr__(self, "unavailable_enrichment_fields", tuple(dict.fromkeys(fields)))


@dataclass(frozen=True, slots=True)
class PeerSecurityEligibilityEvidence:
    """Security-class evidence bound to an already established canonical identity."""

    status: PeerSecurityEligibilityStatus
    security_id: str
    issuer_id: str
    provider: str
    provider_symbol: str
    listing_symbol: str
    exchange: str
    security_type: str | None
    source_as_of: datetime
    retrieved_at: datetime
    provenance: Provenance
    note: str

    def __post_init__(self) -> None:
        for name in (
            "security_id", "issuer_id", "provider", "provider_symbol",
            "listing_symbol", "exchange", "note",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "provider", self.provider.lower())
        if self.security_type is not None:
            object.__setattr__(self, "security_type", _text(self.security_type, "security_type"))
        if (
            self.status is not PeerSecurityEligibilityStatus.UNVERIFIED
            and self.security_type is None
        ):
            raise ValueError("verified security eligibility requires a security type")
        _aware(self.source_as_of, "source_as_of")
        _aware(self.retrieved_at, "retrieved_at")
        if self.provenance.provider.lower() != self.provider:
            raise ValueError("security-eligibility provider must match provenance")
        if self.provenance.provider_symbol != self.provider_symbol:
            raise ValueError("security-eligibility provider_symbol must match provenance")
        if self.provenance.as_of_at != self.source_as_of:
            raise ValueError("security-eligibility source_as_of must match provenance")
        if self.provenance.retrieved_at != self.retrieved_at:
            raise ValueError("security-eligibility retrieved_at must match provenance")


def peer_identity_evidence_from_company(
    identity: CompanyIdentity,
    *,
    provenance: Provenance,
    status: PeerIdentityEvidenceStatus = PeerIdentityEvidenceStatus.PROFILE_VERIFIED,
) -> PeerIdentityEvidence:
    return PeerIdentityEvidence(
        status=status,
        canonical_symbol=identity.canonical_symbol,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        provider=provenance.provider,
        provider_symbol=provenance.provider_symbol,
        listing_country=identity.listing_country,
        exchange=identity.exchange,
        security_type=identity.security_type,
        source_as_of=provenance.as_of_at,
        retrieved_at=provenance.retrieved_at,
        provenance=provenance,
        company_name=identity.company_name,
        sector=identity.sector,
        industry=identity.industry,
    )


@dataclass(frozen=True, slots=True)
class PeerCriterionEvidence:
    criterion: PeerCriterion
    status: PeerCriterionStatus
    target_value: str | float | None = None
    candidate_value: str | float | None = None
    comparison_value: float | None = None
    currency: str | None = None
    input_observation_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        for name in ("target_value", "candidate_value"):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, _text(value, name))
            elif value is not None:
                _finite_optional(float(value), name)
        _finite_optional(self.comparison_value, "comparison_value")
        if self.currency is not None:
            if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
                raise ValueError("currency must be an uppercase three-letter code")
        ids = tuple(_text(value, "input_observation_id") for value in self.input_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("input_observation_ids must be unique")
        object.__setattr__(self, "input_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        if self.note is not None:
            object.__setattr__(self, "note", _text(self.note, "note"))


@dataclass(frozen=True, slots=True)
class PeerComparabilityEvidence:
    candidate_id: str
    analysis_as_of: datetime
    identity: PeerIdentityEvidence
    security_eligibility: PeerSecurityEligibilityEvidence
    industry_comparability: IndustryComparability
    criteria: tuple[PeerCriterionEvidence, ...]
    provider_agreement: tuple[str, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _text(self.candidate_id, "candidate_id"))
        _aware(self.analysis_as_of, "analysis_as_of")
        if self.identity.security_id == self.identity.issuer_id:
            raise ValueError("peer security and issuer identities must remain distinct")
        if (
            self.security_eligibility.security_id != self.identity.security_id
            or self.security_eligibility.issuer_id != self.identity.issuer_id
        ):
            raise ValueError("security eligibility must be bound to the peer identity")
        criteria = tuple(self.criteria)
        if len({item.criterion for item in criteria}) != len(criteria):
            raise ValueError("criteria must contain at most one result per criterion")
        object.__setattr__(self, "criteria", criteria)
        providers = tuple(sorted({_text(value, "provider_agreement").lower() for value in self.provider_agreement}))
        object.__setattr__(self, "provider_agreement", providers)
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))

    def criterion(self, criterion: PeerCriterion) -> PeerCriterionEvidence:
        return next(item for item in self.criteria if item.criterion is criterion)


@dataclass(frozen=True, slots=True)
class PeerSelectionResult:
    candidate: PeerCandidate
    status: PeerSelectionStatus
    reasons: tuple[PeerSelectionReason, ...]
    evidence: PeerComparabilityEvidence

    def __post_init__(self) -> None:
        reasons = tuple(dict.fromkeys(self.reasons))
        if not reasons:
            raise ValueError("peer selection result requires at least one controlled reason")
        object.__setattr__(self, "reasons", reasons)
        if self.evidence.candidate_id != self.candidate.candidate_id:
            raise ValueError("selection evidence must reference the selected candidate")
        if self.status is PeerSelectionStatus.INCLUDED and PeerSelectionReason.INCLUSION_RULES_SATISFIED not in reasons:
            raise ValueError("included candidates require INCLUSION_RULES_SATISFIED")


@dataclass(frozen=True, slots=True)
class PeerMethodDataReadiness:
    candidate_id: str
    peer_security_id: str
    peer_issuer_id: str
    analysis_as_of: datetime
    method: PeerMethod
    forward_denominator_available: bool
    required_multiple_inputs_available: bool
    status: PeerMethodDataStatus
    reasons: tuple[PeerMethodDataReason, ...]
    supporting_observation_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        for name in ("candidate_id", "peer_security_id", "peer_issuer_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.analysis_as_of, "analysis_as_of")
        reasons = tuple(dict.fromkeys(self.reasons))
        if not reasons:
            raise ValueError("method-data readiness requires at least one controlled reason")
        object.__setattr__(self, "reasons", reasons)
        ids = tuple(_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting_observation_ids must be unique")
        object.__setattr__(self, "supporting_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        expected = (
            PeerMethodDataStatus.READY
            if self.forward_denominator_available and self.required_multiple_inputs_available
            else PeerMethodDataStatus.PARTIAL
            if self.forward_denominator_available
            else PeerMethodDataStatus.UNAVAILABLE
        )
        if self.status is not expected:
            raise ValueError("method-data status must match explicit input availability")


@dataclass(frozen=True, slots=True)
class PeerSet:
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    candidate_count: int
    included_peer_issuer_ids: tuple[str, ...]
    included_security_ids: tuple[str, ...]
    excluded_candidate_ids: tuple[str, ...]
    unverified_candidate_ids: tuple[str, ...]
    unavailable_candidate_ids: tuple[str, ...]
    minimum_required_peers: int
    status: PeerSetStatus
    policy_id: str
    selections: tuple[PeerSelectionResult, ...]
    method_data_readiness: tuple[PeerMethodDataReadiness, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        for name in ("target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.analysis_as_of, "analysis_as_of")
        if isinstance(self.candidate_count, bool) or self.candidate_count < 0:
            raise ValueError("candidate_count must be non-negative")
        if isinstance(self.minimum_required_peers, bool) or self.minimum_required_peers < 1:
            raise ValueError("minimum_required_peers must be positive")
        selections = tuple(self.selections)
        if len(selections) != self.candidate_count:
            raise ValueError("candidate_count must equal the number of selection results")
        if len({item.candidate.candidate_id for item in selections}) != len(selections):
            raise ValueError("selection candidate IDs must be unique")
        included = tuple(item for item in selections if item.status is PeerSelectionStatus.INCLUDED)
        if tuple(item.candidate.candidate_issuer_id for item in included) != tuple(self.included_peer_issuer_ids):
            raise ValueError("included issuer IDs must match included selections")
        if tuple(item.candidate.candidate_security_id for item in included) != tuple(self.included_security_ids):
            raise ValueError("included security IDs must match included selections")
        if len(set(self.included_peer_issuer_ids)) != len(self.included_peer_issuer_ids):
            raise ValueError("included peers must be independent issuer identities")
        status_fields = (
            (PeerSelectionStatus.EXCLUDED, tuple(self.excluded_candidate_ids)),
            (PeerSelectionStatus.UNVERIFIED, tuple(self.unverified_candidate_ids)),
            (PeerSelectionStatus.UNAVAILABLE, tuple(self.unavailable_candidate_ids)),
        )
        for status, values in status_fields:
            expected = tuple(item.candidate.candidate_id for item in selections if item.status is status)
            if expected != values:
                raise ValueError(f"{status.value} candidate IDs must match selections")
        object.__setattr__(self, "included_peer_issuer_ids", tuple(self.included_peer_issuer_ids))
        object.__setattr__(self, "included_security_ids", tuple(self.included_security_ids))
        object.__setattr__(self, "excluded_candidate_ids", tuple(self.excluded_candidate_ids))
        object.__setattr__(self, "unverified_candidate_ids", tuple(self.unverified_candidate_ids))
        object.__setattr__(self, "unavailable_candidate_ids", tuple(self.unavailable_candidate_ids))
        object.__setattr__(self, "selections", selections)
        readiness = tuple(self.method_data_readiness)
        if len(readiness) != len(selections):
            raise ValueError("method_data_readiness must contain one result per selection")
        if tuple(item.candidate.candidate_id for item in selections) != tuple(
            item.candidate_id for item in readiness
        ):
            raise ValueError("method-data readiness order must match selections")
        object.__setattr__(self, "method_data_readiness", readiness)
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))
        object.__setattr__(self, "provenance", tuple(self.provenance))
