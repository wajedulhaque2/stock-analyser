"""Canonical, provider-independent peer EV/EBITDA method-data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re

from .enums import (
    EstimateCase,
    ForwardPeriodSelection,
    MetricId,
    MetricUnit,
    PeerIdentityBindingBasis,
    PeerCentralStatistic,
    PeerDistributionStatus,
    PeerMethod,
    PeerMethodEvidenceReason,
    PeerMethodEvidenceStatus,
    PeerSelectionStatus,
    PeerOutlierPolicy,
    PeerQuantileConvention,
    PeerValuationSubsetStatus,
    ValuationBasis,
)
from .models import DataIssue, MetricObservation, Provenance


_SENSITIVE_RE = re.compile(r"(?:authorization|api[_ -]?key|bearer\s+)", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


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


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"


def stable_peer_identity_binding_id(*parts: str) -> str:
    return _stable_id("peer-identity-binding", *parts)


def stable_peer_method_evidence_id(*parts: str) -> str:
    return _stable_id("peer-method-evidence", *parts)


def stable_peer_valuation_observation_id(*parts: str) -> str:
    return _stable_id("peer-valuation-observation", *parts)


def stable_peer_valuation_subset_id(*parts: str) -> str:
    return _stable_id("peer-valuation-subset", *parts)


def stable_peer_multiple_distribution_id(*parts: str) -> str:
    return _stable_id("peer-multiple-distribution", *parts)


def _normalized_identifier(basis: PeerIdentityBindingBasis, value: str) -> str:
    normalized = _text(value, "stable_identifier").replace(" ", "").upper()
    if basis is PeerIdentityBindingBasis.CIK:
        if not normalized.isdigit():
            raise ValueError("CIK must contain digits only")
        normalized = normalized.lstrip("0") or "0"
    elif basis is PeerIdentityBindingBasis.ISIN:
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", normalized):
            raise ValueError("ISIN must use its canonical 12-character form")
    elif basis is PeerIdentityBindingBasis.CUSIP:
        if not re.fullmatch(r"[A-Z0-9*@#]{9}", normalized):
            raise ValueError("CUSIP must use its canonical 9-character form")
    return normalized


@dataclass(frozen=True, slots=True)
class PeerProviderIdentityBinding:
    """Verified stable-identifier join from one provider to one canonical peer."""

    binding_id: str
    peer_security_id: str
    peer_issuer_id: str
    provider: str
    provider_symbol: str
    identifier_basis: PeerIdentityBindingBasis
    canonical_identifier: str
    provider_identifier: str
    verified_at: datetime
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("binding_id", "peer_security_id", "peer_issuer_id", "provider", "provider_symbol"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "provider", self.provider.lower())
        _aware(self.verified_at, "verified_at")
        if not isinstance(self.identifier_basis, PeerIdentityBindingBasis):
            raise TypeError("identifier_basis must use PeerIdentityBindingBasis")
        canonical = _normalized_identifier(self.identifier_basis, self.canonical_identifier)
        provider = _normalized_identifier(self.identifier_basis, self.provider_identifier)
        object.__setattr__(self, "canonical_identifier", canonical)
        object.__setattr__(self, "provider_identifier", provider)
        if canonical != provider:
            raise ValueError("stable canonical and provider identifiers must match")
        if self.provenance.provider.lower() != self.provider:
            raise ValueError("binding provider must match provenance provider")
        if self.provenance.provider_symbol != self.provider_symbol:
            raise ValueError("binding provider symbol must match provenance")
        if self.provenance.as_of_at != self.verified_at:
            raise ValueError("binding verified_at must match provenance as_of_at")


@dataclass(frozen=True, slots=True)
class PeerMetricEvidence:
    """A canonical metric observation explicitly bound to one peer identity."""

    peer_security_id: str
    peer_issuer_id: str
    observation: MetricObservation

    def __post_init__(self) -> None:
        object.__setattr__(self, "peer_security_id", _text(self.peer_security_id, "peer_security_id"))
        object.__setattr__(self, "peer_issuer_id", _text(self.peer_issuer_id, "peer_issuer_id"))


@dataclass(frozen=True, slots=True)
class PeerMethodDataEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    peer_security_id: str
    peer_issuer_id: str
    peer_selection_result_id: str
    economic_selection_status: PeerSelectionStatus
    method: PeerMethod
    valuation_basis: ValuationBasis
    forward_denominator: MetricId
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    analysis_as_of: datetime
    status: PeerMethodEvidenceStatus
    reasons: tuple[PeerMethodEvidenceReason, ...]
    enterprise_value_observation_id: str | None = None
    enterprise_value_date: date | None = None
    enterprise_value: float | None = None
    enterprise_value_currency: str | None = None
    forward_ebitda_observation_id: str | None = None
    forward_period_end: date | None = None
    forward_estimate_as_of: datetime | None = None
    forward_ebitda: float | None = None
    forward_ebitda_currency: str | None = None
    cross_provider_identity_binding_id: str | None = None
    provenance: tuple[Provenance, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    policy_id: str = "peer-ev-ebitda-inputs-v1"

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "target_security_id", "target_issuer_id", "peer_security_id",
            "peer_issuer_id", "peer_selection_result_id", "policy_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.analysis_as_of, "analysis_as_of")
        if self.forward_estimate_as_of is not None:
            _aware(self.forward_estimate_as_of, "forward_estimate_as_of")
        reasons = tuple(dict.fromkeys(self.reasons))
        if not reasons:
            raise ValueError("method-data evidence requires at least one controlled reason")
        object.__setattr__(self, "reasons", reasons)
        if self.method is not PeerMethod.EV_EBITDA:
            raise ValueError("Milestone 8B supports EV_EBITDA only")
        if self.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("EV/EBITDA requires enterprise valuation basis")
        if self.forward_denominator is not MetricId.EBITDA:
            raise ValueError("EV/EBITDA requires EBITDA as the forward denominator")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("the primary peer observation requires AVERAGE estimates")
        if self.target_issuer_id == self.peer_issuer_id:
            raise ValueError("target issuer cannot be its own peer")
        if self.cross_provider_identity_binding_id is not None:
            object.__setattr__(self, "cross_provider_identity_binding_id", _text(
                self.cross_provider_identity_binding_id, "cross_provider_identity_binding_id",
            ))
        for name in ("enterprise_value", "forward_ebitda"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite when present")
        for name in ("enterprise_value_currency", "forward_ebitda_currency"):
            value = getattr(self, name)
            if value is not None and not _CURRENCY_RE.fullmatch(value):
                raise ValueError(f"{name} must be an uppercase three-letter currency")
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))
        if self.status is PeerMethodEvidenceStatus.AVAILABLE:
            required = (
                self.enterprise_value_observation_id, self.enterprise_value_date,
                self.enterprise_value, self.enterprise_value_currency,
                self.forward_ebitda_observation_id, self.forward_period_end,
                self.forward_estimate_as_of, self.forward_ebitda,
                self.forward_ebitda_currency, self.cross_provider_identity_binding_id,
            )
            if any(value is None for value in required):
                raise ValueError("available method data requires complete EV, EBITDA, date, currency, and identity evidence")
            if self.economic_selection_status is not PeerSelectionStatus.INCLUDED:
                raise ValueError("available method data requires an INCLUDED economic peer")
            if self.enterprise_value <= 0 or self.forward_ebitda <= 0:
                raise ValueError("available method data requires positive EV and EBITDA")
            if self.enterprise_value_currency != self.forward_ebitda_currency:
                raise ValueError("available method data requires matching EV and EBITDA currencies")
            if self.enterprise_value_date > self.analysis_as_of.date():
                raise ValueError("available method data cannot use future enterprise value")
            if self.forward_estimate_as_of > self.analysis_as_of:
                raise ValueError("available method data cannot use a future estimate snapshot")
            if self.forward_period_end <= self.analysis_as_of.date():
                raise ValueError("available method data requires a forward period after analysis")
            if PeerMethodEvidenceReason.METHOD_DATA_AVAILABLE not in reasons:
                raise ValueError("available method data requires METHOD_DATA_AVAILABLE")


@dataclass(frozen=True, slots=True)
class PeerValuationObservation:
    observation_id: str
    target_security_id: str
    target_issuer_id: str
    peer_set_id: str
    peer_security_id: str
    peer_issuer_id: str
    peer_selection_result_id: str
    multiple_type: PeerMethod
    valuation_basis: ValuationBasis
    enterprise_value: float
    forward_denominator: MetricId
    forward_ebitda: float
    forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    ev_ebitda_multiple: float
    unit: MetricUnit
    currency: str
    enterprise_value_observation_id: str
    forward_ebitda_observation_id: str
    enterprise_value_date: date
    forward_period_end: date
    forward_estimate_as_of: datetime
    analysis_as_of: datetime
    provenance: tuple[Provenance, ...]
    policy_id: str
    status: PeerMethodEvidenceStatus = PeerMethodEvidenceStatus.AVAILABLE
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "observation_id", "target_security_id", "target_issuer_id", "peer_set_id", "peer_security_id",
            "peer_issuer_id", "peer_selection_result_id", "enterprise_value_observation_id",
            "forward_ebitda_observation_id", "policy_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.forward_estimate_as_of, "forward_estimate_as_of")
        _aware(self.analysis_as_of, "analysis_as_of")
        if self.multiple_type is not PeerMethod.EV_EBITDA:
            raise ValueError("Milestone 8B supports EV_EBITDA only")
        if self.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("EV/EBITDA is enterprise-based")
        if self.forward_denominator is not MetricId.EBITDA:
            raise ValueError("EV/EBITDA requires EBITDA")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer EV/EBITDA uses the AVERAGE estimate case")
        if self.unit is not MetricUnit.RATIO:
            raise ValueError("EV/EBITDA output must be a dimensionless ratio")
        if self.status is not PeerMethodEvidenceStatus.AVAILABLE:
            raise ValueError("a peer valuation observation is constructed only from available method data")
        if self.target_issuer_id == self.peer_issuer_id:
            raise ValueError("target issuer cannot be its own peer")
        if not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("currency must be an uppercase three-letter code")
        for name in ("enterprise_value", "forward_ebitda", "ev_ebitda_multiple"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        expected = self.enterprise_value / self.forward_ebitda
        if not math.isclose(self.ev_ebitda_multiple, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("ev_ebitda_multiple must equal peer enterprise value / peer forward EBITDA")
        if self.enterprise_value_date > self.analysis_as_of.date():
            raise ValueError("future enterprise value is not eligible")
        if self.forward_estimate_as_of > self.analysis_as_of:
            raise ValueError("future estimate snapshots are not eligible")
        if self.forward_period_end <= self.analysis_as_of.date():
            raise ValueError("forward period must end after analysis_as_of")
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))


@dataclass(frozen=True, slots=True)
class PeerValuationSubset:
    subset_id: str
    target_security_id: str
    target_issuer_id: str
    peer_set_id: str
    method: PeerMethod
    analysis_as_of: datetime
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    included_economic_peer_count: int
    method_data_available_peer_issuer_ids: tuple[str, ...]
    method_data_unavailable_peer_issuer_ids: tuple[str, ...]
    valid_observation_ids: tuple[str, ...]
    valid_observation_count: int
    minimum_required_valid_observations: int
    status: PeerValuationSubsetStatus
    method_data_evidence: tuple[PeerMethodDataEvidence, ...]
    observations: tuple[PeerValuationObservation, ...]
    policy_id: str
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        for name in ("subset_id", "target_security_id", "target_issuer_id", "peer_set_id", "policy_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.analysis_as_of, "analysis_as_of")
        if self.method is not PeerMethod.EV_EBITDA:
            raise ValueError("Milestone 8B supports EV_EBITDA only")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer valuation subset requires AVERAGE estimates")
        for name in ("included_economic_peer_count", "valid_observation_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.minimum_required_valid_observations, bool)
            or not isinstance(self.minimum_required_valid_observations, int)
            or self.minimum_required_valid_observations < 1
        ):
            raise ValueError("minimum_required_valid_observations must be positive")
        available = tuple(self.method_data_available_peer_issuer_ids)
        unavailable = tuple(self.method_data_unavailable_peer_issuer_ids)
        if len(set((*available, *unavailable))) != len((*available, *unavailable)):
            raise ValueError("each included issuer must appear in exactly one method-data partition")
        if len(available) + len(unavailable) != self.included_economic_peer_count:
            raise ValueError("method-data partitions must cover every included economic peer")
        observations = tuple(self.observations)
        if len({item.peer_issuer_id for item in observations}) != len(observations):
            raise ValueError("one issuer may contribute at most one peer valuation observation")
        if tuple(item.observation_id for item in observations) != tuple(self.valid_observation_ids):
            raise ValueError("valid observation IDs must match observations")
        if len(observations) != self.valid_observation_count:
            raise ValueError("valid_observation_count must match observations")
        if tuple(item.peer_issuer_id for item in observations) != available:
            raise ValueError("available issuer IDs must match valid observations")
        if any(item.peer_set_id != self.peer_set_id for item in observations):
            raise ValueError("every observation must belong to the subset peer_set_id")
        object.__setattr__(self, "method_data_available_peer_issuer_ids", available)
        object.__setattr__(self, "method_data_unavailable_peer_issuer_ids", unavailable)
        object.__setattr__(self, "valid_observation_ids", tuple(self.valid_observation_ids))
        object.__setattr__(self, "method_data_evidence", tuple(self.method_data_evidence))
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))


@dataclass(frozen=True, slots=True)
class PeerMultipleDistribution:
    """Cross-sectional statistics over one immutable 8B peer subset."""

    distribution_id: str
    target_security_id: str
    target_issuer_id: str
    peer_set_id: str
    peer_valuation_subset_id: str
    multiple_type: PeerMethod
    valuation_basis: ValuationBasis
    analysis_as_of: datetime
    forward_period_policy: ForwardPeriodSelection
    estimate_case: EstimateCase
    valid_observation_ids: tuple[str, ...]
    peer_issuer_ids: tuple[str, ...]
    sample_count: int
    p25: float | None
    median: float | None
    p75: float | None
    minimum: float | None
    maximum: float | None
    mean: float | None
    population_standard_deviation: float | None
    iqr: float | None
    iqr_to_median: float | None
    max_to_median: float | None
    min_to_median: float | None
    minimum_required_observations: int
    source_subset_status: PeerValuationSubsetStatus
    status: PeerDistributionStatus
    quantile_convention: PeerQuantileConvention
    central_statistic: PeerCentralStatistic
    outlier_policy_id: str
    policy_id: str
    provenance: tuple[Provenance, ...]
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    unit: MetricUnit = MetricUnit.RATIO
    observations_altered: bool = False
    excluded_observation_ids: tuple[str, ...] = ()
    automatic_outlier_removal_applied: bool = False

    def __post_init__(self) -> None:
        for name in (
            "distribution_id", "target_security_id", "target_issuer_id", "peer_set_id",
            "peer_valuation_subset_id", "outlier_policy_id", "policy_id",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        _aware(self.analysis_as_of, "analysis_as_of")
        for value, enum_type, name in (
            (self.multiple_type, PeerMethod, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.forward_period_policy, ForwardPeriodSelection, "forward_period_policy"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.status, PeerDistributionStatus, "status"),
            (self.source_subset_status, PeerValuationSubsetStatus, "source_subset_status"),
            (self.quantile_convention, PeerQuantileConvention, "quantile_convention"),
            (self.central_statistic, PeerCentralStatistic, "central_statistic"),
            (self.unit, MetricUnit, "unit"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.multiple_type is not PeerMethod.EV_EBITDA:
            raise ValueError("Milestone 8C supports EV_EBITDA only")
        if self.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("EV/EBITDA distributions require enterprise basis")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer distributions require AVERAGE estimates")
        if self.quantile_convention is not PeerQuantileConvention.TYPE_7_LINEAR:
            raise ValueError("Milestone 8C requires Type-7 linear quantiles")
        if self.central_statistic is not PeerCentralStatistic.MEDIAN:
            raise ValueError("median is the primary peer statistic")
        if self.outlier_policy_id != PeerOutlierPolicy.NO_AUTOMATIC_REMOVAL.value:
            raise ValueError("Milestone 8C permits no automatic outlier removal")
        if self.unit is not MetricUnit.RATIO:
            raise ValueError("peer multiple distributions must be dimensionless ratios")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError("sample_count must be a non-negative integer")
        if (
            isinstance(self.minimum_required_observations, bool)
            or not isinstance(self.minimum_required_observations, int)
            or self.minimum_required_observations < 1
        ):
            raise ValueError("minimum_required_observations must be positive")
        observation_ids = tuple(_text(value, "valid_observation_id") for value in self.valid_observation_ids)
        issuer_ids = tuple(_text(value, "peer_issuer_id") for value in self.peer_issuer_ids)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("valid observation IDs must be unique")
        if len(issuer_ids) != len(set(issuer_ids)):
            raise ValueError("one issuer may contribute at most one distribution observation")
        if len(observation_ids) != self.sample_count or len(issuer_ids) != self.sample_count:
            raise ValueError("observation and issuer IDs must match sample_count")
        object.__setattr__(self, "valid_observation_ids", observation_ids)
        object.__setattr__(self, "peer_issuer_ids", issuer_ids)
        statistics = (
            self.p25, self.median, self.p75, self.minimum, self.maximum, self.mean,
            self.population_standard_deviation, self.iqr, self.iqr_to_median,
            self.max_to_median, self.min_to_median,
        )
        if self.sample_count:
            if any(value is None or isinstance(value, bool) or not math.isfinite(float(value)) for value in statistics):
                raise ValueError("non-empty peer distributions require finite statistics")
            if not 0 < self.minimum <= self.p25 <= self.median <= self.p75 <= self.maximum:
                raise ValueError("peer distribution statistics must be positive and ordered")
            if not math.isclose(self.iqr, self.p75 - self.p25, rel_tol=0, abs_tol=1e-12):
                raise ValueError("iqr must equal p75 minus p25")
            if not math.isclose(self.iqr_to_median, self.iqr / self.median, rel_tol=0, abs_tol=1e-12):
                raise ValueError("iqr_to_median must match the raw statistics")
            if not math.isclose(self.max_to_median, self.maximum / self.median, rel_tol=0, abs_tol=1e-12):
                raise ValueError("max_to_median must match the raw statistics")
            if not math.isclose(self.min_to_median, self.minimum / self.median, rel_tol=0, abs_tol=1e-12):
                raise ValueError("min_to_median must match the raw statistics")
        elif any(value is not None for value in statistics):
            raise ValueError("empty distributions cannot carry statistics")
        if self.status is PeerDistributionStatus.USABLE and self.sample_count < self.minimum_required_observations:
            raise ValueError("a usable peer distribution requires the configured minimum observations")
        if self.status is PeerDistributionStatus.UNAVAILABLE and self.sample_count:
            raise ValueError("an unavailable distribution cannot contain observations")
        excluded = tuple(_text(value, "excluded_observation_id") for value in self.excluded_observation_ids)
        object.__setattr__(self, "excluded_observation_ids", excluded)
        if self.observations_altered or excluded or self.automatic_outlier_removal_applied:
            raise ValueError("Milestone 8C cannot alter or exclude valid observations")
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_text(value, "warning") for value in self.warnings))
