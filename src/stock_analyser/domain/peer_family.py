"""Bounded canonical result for one end-to-end peer-family orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from .enums import (
    PeerDistributionStatus,
    PeerFamilyFailureStage,
    PeerFamilyStatus,
    PeerSetStatus,
    PeerValuationSubsetStatus,
    ValuationMethodStatus,
)
from .models import CompanyIdentity, Provenance, _require_text, _validate_aware_datetime, _validate_currency
from .peer_target_valuation import PeerTargetValuationResult
from .peer_valuation import PeerMultipleDistribution, PeerValuationSubset
from .peers import PeerSet


def stable_peer_family_orchestration_id(*parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"peerfamily:{digest}"


@dataclass(frozen=True, slots=True)
class PeerFamilyOrchestrationResult:
    orchestration_id: str
    target_security_id: str
    target_issuer_id: str
    canonical_symbol: str
    analysis_as_of: datetime
    reporting_currency: str | None
    quote_currency: str | None
    quote_unit: str | None
    quote_price_scale: float | None
    peer_set_id: str | None
    peer_set_status: PeerSetStatus | None
    peer_subset_id: str | None
    peer_subset_status: PeerValuationSubsetStatus | None
    peer_distribution_id: str | None
    peer_distribution_status: PeerDistributionStatus | None
    target_peer_valuation_id: str | None
    target_peer_valuation_status: ValuationMethodStatus | None
    candidate_count: int
    included_peer_count: int
    valid_peer_observation_count: int
    status: PeerFamilyStatus
    failure_stage: PeerFamilyFailureStage
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    target_identity: CompanyIdentity
    peer_set: PeerSet | None
    peer_subset: PeerValuationSubset | None
    peer_distribution: PeerMultipleDistribution | None
    target_peer_valuation: PeerTargetValuationResult | None

    def __post_init__(self) -> None:
        for name in ("orchestration_id", "target_security_id", "target_issuer_id", "canonical_symbol"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.target_identity, CompanyIdentity):
            raise TypeError("target_identity must use CompanyIdentity")
        if (
            self.target_identity.security_id != self.target_security_id
            or self.target_identity.issuer_id != self.target_issuer_id
            or self.target_identity.canonical_symbol != self.canonical_symbol
        ):
            raise ValueError("orchestration target fields must match canonical target identity")
        for name in ("reporting_currency", "quote_currency"):
            _validate_currency(getattr(self, name), name, required=False)
        if self.quote_unit is not None:
            object.__setattr__(self, "quote_unit", _require_text(self.quote_unit, "quote_unit"))
        if self.quote_price_scale is not None and self.quote_price_scale <= 0:
            raise ValueError("quote_price_scale must be positive when present")
        for value, enum_type, name in (
            (self.status, PeerFamilyStatus, "status"),
            (self.failure_stage, PeerFamilyFailureStage, "failure_stage"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        for name in ("candidate_count", "included_peer_count", "valid_peer_observation_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.included_peer_count > self.candidate_count:
            raise ValueError("included peer count cannot exceed candidate count")
        if self.valid_peer_observation_count > self.included_peer_count:
            raise ValueError("valid peer observations cannot exceed included peers")
        reasons = tuple(_require_text(value, "blocking_reason") for value in self.blocking_reasons)
        warnings = tuple(_require_text(value, "warning") for value in self.warnings)
        supporting = tuple(_require_text(value, "supporting_id") for value in self.supporting_ids)
        policies = tuple(_require_text(value, "policy_id") for value in self.policy_ids)
        if len(supporting) != len(set(supporting)) or len(policies) != len(set(policies)):
            raise ValueError("supporting and policy IDs must be unique")
        object.__setattr__(self, "blocking_reasons", tuple(dict.fromkeys(reasons)))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(warnings)))
        object.__setattr__(self, "supporting_ids", supporting)
        object.__setattr__(self, "policy_ids", policies)
        provenance = tuple(dict.fromkeys(self.provenance))
        if any(not isinstance(value, Provenance) for value in provenance):
            raise TypeError("provenance must contain Provenance values")
        object.__setattr__(self, "provenance", provenance)
        if self.failure_stage is PeerFamilyFailureStage.COMPLETE:
            if self.status is not PeerFamilyStatus.VALID or reasons:
                raise ValueError("complete orchestration requires VALID status and no blocking reasons")
            if self.target_peer_valuation_status is not ValuationMethodStatus.VALID:
                raise ValueError("complete orchestration requires a valid target peer valuation")
        elif not reasons:
            raise ValueError("non-complete orchestration requires a blocking reason")
        if self.peer_set is not None:
            if self.peer_set.target_security_id != self.target_security_id or self.peer_set.target_issuer_id != self.target_issuer_id:
                raise ValueError("peer set target identity must match orchestration")
            if self.peer_set.analysis_as_of != self.analysis_as_of or self.peer_set.status is not self.peer_set_status:
                raise ValueError("peer set snapshot/status must match orchestration")
            if self.peer_set.candidate_count != self.candidate_count:
                raise ValueError("candidate count must match peer set")
        if self.peer_subset is not None:
            if self.peer_subset.subset_id != self.peer_subset_id or self.peer_subset.status is not self.peer_subset_status:
                raise ValueError("peer subset ID/status must flow unchanged")
            if self.peer_subset.peer_set_id != self.peer_set_id:
                raise ValueError("peer subset peer_set_id must flow unchanged")
            if self.peer_subset.valid_observation_count != self.valid_peer_observation_count:
                raise ValueError("valid observation count must match peer subset")
        if self.peer_distribution is not None:
            if (
                self.peer_distribution.distribution_id != self.peer_distribution_id
                or self.peer_distribution.status is not self.peer_distribution_status
                or self.peer_distribution.peer_set_id != self.peer_set_id
                or self.peer_distribution.peer_valuation_subset_id != self.peer_subset_id
            ):
                raise ValueError("peer distribution IDs/status must flow unchanged")
        if self.target_peer_valuation is not None:
            if (
                self.target_peer_valuation.result_id != self.target_peer_valuation_id
                or self.target_peer_valuation.status is not self.target_peer_valuation_status
                or self.target_peer_valuation.distribution_id != self.peer_distribution_id
            ):
                raise ValueError("target valuation IDs/status must flow unchanged")
