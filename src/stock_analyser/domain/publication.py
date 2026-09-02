"""Provider-independent contracts for cross-family valuation publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import math

from .enums import (
    AggregationStatus,
    PublicationCentralEstimator,
    PublicationEvidenceType,
    PublicationRangeSemantics,
    ValuationFamily,
    ValuationMethodStatus,
)
from .models import DataIssue, Provenance, _require_text, _validate_aware_datetime, _validate_currency


CENTRAL_VALUATION_FAMILIES = frozenset((ValuationFamily.OWN_HISTORY, ValuationFamily.PEER))


def _stable_id(prefix: str, *parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def stable_family_valuation_evidence_id(*parts: object) -> str:
    return _stable_id("familyvalue", *parts)


def stable_publication_supplemental_evidence_id(*parts: object) -> str:
    return _stable_id("publicationevidence", *parts)


def stable_valuation_publication_id(*parts: object) -> str:
    return _stable_id("publication", *parts)


def _optional_finite(value: float | None, name: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite when present")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive when present")


def _texts(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} values must be unique")
    return normalized


def _provenance(values: tuple[Provenance, ...]) -> tuple[Provenance, ...]:
    normalized = tuple(values)
    if any(not isinstance(value, Provenance) for value in normalized):
        raise TypeError("provenance must contain Provenance values")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class FamilyValuationEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    valuation_family: ValuationFamily
    selected_method: str
    source_result_id: str
    analysis_as_of: datetime
    currency: str | None
    per_share_unit: str | None
    lower_value: float | None
    central_value: float | None
    upper_value: float | None
    family_status: ValuationMethodStatus
    central_valuation_eligible: bool
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "target_security_id", "target_issuer_id",
            "selected_method", "source_result_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if not isinstance(self.valuation_family, ValuationFamily):
            raise TypeError("valuation_family must use ValuationFamily")
        if self.valuation_family not in CENTRAL_VALUATION_FAMILIES:
            raise ValueError("family publication evidence supports OWN_HISTORY and PEER only")
        if not isinstance(self.family_status, ValuationMethodStatus):
            raise TypeError("family_status must use ValuationMethodStatus")
        if not isinstance(self.central_valuation_eligible, bool):
            raise TypeError("central_valuation_eligible must be boolean")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency", required=False)
        if self.per_share_unit is not None:
            object.__setattr__(self, "per_share_unit", _require_text(self.per_share_unit, "per_share_unit"))
        values = (self.lower_value, self.central_value, self.upper_value)
        for name, value in zip(("lower_value", "central_value", "upper_value"), values):
            _optional_finite(value, name, positive=True)
        if all(value is not None for value in values) and not self.lower_value <= self.central_value <= self.upper_value:
            raise ValueError("family range must satisfy lower <= central <= upper; ranges are never reordered")
        if self.family_status is ValuationMethodStatus.UNAVAILABLE and any(value is not None for value in values):
            raise ValueError("unavailable family evidence cannot contain valuation points")
        if self.family_status is ValuationMethodStatus.PARTIAL and all(value is None for value in values):
            raise ValueError("partial family evidence requires at least one valuation point")
        if self.family_status is ValuationMethodStatus.VALID and any(value is None for value in values):
            raise ValueError("valid family evidence requires lower, central, and upper values")
        if self.central_valuation_eligible and (
            self.family_status is not ValuationMethodStatus.VALID
            or any(value is None for value in values)
            or self.currency is None
            or self.per_share_unit is None
        ):
            raise ValueError("central-eligible family evidence must be a complete valid per-share result")
        if self.family_status is not ValuationMethodStatus.VALID and self.central_valuation_eligible:
            raise ValueError("partial or unavailable family evidence cannot be central eligible")
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class PublicationSupplementalEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    evidence_type: PublicationEvidenceType
    source_result_id: str
    evidence_as_of: datetime
    central_valuation_eligible: bool = False
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        for name in ("evidence_id", "target_security_id", "target_issuer_id", "source_result_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if not isinstance(self.evidence_type, PublicationEvidenceType):
            raise TypeError("evidence_type must use PublicationEvidenceType")
        if not isinstance(self.central_valuation_eligible, bool):
            raise TypeError("central_valuation_eligible must be boolean")
        _validate_aware_datetime(self.evidence_as_of, "evidence_as_of")
        if self.central_valuation_eligible:
            raise ValueError("expectation, scenario, and external-reference evidence is never central eligible")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class PublicationDispersionDiagnostics:
    family_count: int
    minimum_family_central: float | None
    maximum_family_central: float | None
    median_family_central: float | None
    central_spread: float | None
    central_spread_to_median: float | None
    envelope_width: float | None
    overlap_width: float | None

    def __post_init__(self) -> None:
        if isinstance(self.family_count, bool) or not isinstance(self.family_count, int) or self.family_count < 0:
            raise ValueError("family_count must be a non-negative integer")
        for name in (
            "minimum_family_central", "maximum_family_central", "median_family_central",
            "central_spread", "central_spread_to_median", "envelope_width", "overlap_width",
        ):
            _optional_finite(getattr(self, name), name)
        if self.family_count == 0 and any(
            getattr(self, name) is not None for name in (
                "minimum_family_central", "maximum_family_central", "median_family_central",
                "central_spread", "central_spread_to_median", "envelope_width", "overlap_width",
            )
        ):
            raise ValueError("zero-family diagnostics cannot contain numeric values")
        if self.family_count > 0:
            if any(value is None for value in (
                self.minimum_family_central, self.maximum_family_central,
                self.median_family_central, self.central_spread, self.envelope_width,
            )):
                raise ValueError("positive-family diagnostics require central and envelope statistics")
            if not self.minimum_family_central <= self.median_family_central <= self.maximum_family_central:
                raise ValueError("central diagnostics must remain ordered")
            if not math.isclose(
                self.central_spread,
                self.maximum_family_central - self.minimum_family_central,
            ):
                raise ValueError("central_spread must equal maximum minus minimum")


@dataclass(frozen=True, slots=True)
class ValuationPublicationResult:
    publication_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    currency: str | None
    per_share_unit: str | None
    publication_status: AggregationStatus
    eligible_family_ids: tuple[ValuationFamily, ...]
    eligible_family_count: int
    family_evidence: tuple[FamilyValuationEvidence, ...]
    supplemental_evidence: tuple[PublicationSupplementalEvidence, ...]
    overall_central_value: float | None
    central_estimator: PublicationCentralEstimator | None
    envelope_lower: float | None
    envelope_upper: float | None
    envelope_semantics: PublicationRangeSemantics | None
    overlap_lower: float | None
    overlap_upper: float | None
    overlap_semantics: PublicationRangeSemantics | None
    diagnostics: PublicationDispersionDiagnostics
    expectation_evidence_ids: tuple[str, ...]
    scenario_evidence_ids: tuple[str, ...]
    reference_evidence_ids: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("publication_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency", required=False)
        if self.per_share_unit is not None:
            object.__setattr__(self, "per_share_unit", _require_text(self.per_share_unit, "per_share_unit"))
        if (self.currency is None) != (self.per_share_unit is None):
            raise ValueError("publication currency and per-share unit must be present or absent together")
        if not isinstance(self.publication_status, AggregationStatus):
            raise TypeError("publication_status must use AggregationStatus")
        families = tuple(self.eligible_family_ids)
        if any(value not in CENTRAL_VALUATION_FAMILIES for value in families) or len(families) != len(set(families)):
            raise ValueError("eligible_family_ids must contain unique approved central families")
        if (
            isinstance(self.eligible_family_count, bool)
            or not isinstance(self.eligible_family_count, int)
            or self.eligible_family_count != len(families)
            or self.diagnostics.family_count != len(families)
        ):
            raise ValueError("eligible family counts must agree")
        object.__setattr__(self, "eligible_family_ids", families)
        family_evidence = tuple(self.family_evidence)
        supplemental = tuple(self.supplemental_evidence)
        if any(not isinstance(value, FamilyValuationEvidence) for value in family_evidence):
            raise TypeError("family_evidence must contain FamilyValuationEvidence")
        if any(not isinstance(value, PublicationSupplementalEvidence) for value in supplemental):
            raise TypeError("supplemental_evidence must contain PublicationSupplementalEvidence")
        object.__setattr__(self, "family_evidence", family_evidence)
        object.__setattr__(self, "supplemental_evidence", supplemental)
        aligned_complete = tuple(
            value for value in family_evidence
            if (
                value.central_valuation_eligible
                and value.family_status is ValuationMethodStatus.VALID
                and value.target_security_id == self.target_security_id
                and value.target_issuer_id == self.target_issuer_id
                and value.analysis_as_of == self.analysis_as_of
                and value.currency == self.currency
                and value.per_share_unit == self.per_share_unit
            )
        )
        complete_family_ids = {
            value.valuation_family for value in aligned_complete
            if sum(
                other.valuation_family is value.valuation_family
                for other in aligned_complete
            ) == 1
        }
        if set(families) != complete_family_ids:
            raise ValueError("eligible family IDs must exactly match aligned complete family evidence")
        _optional_finite(self.overall_central_value, "overall_central_value", positive=True)
        for name in ("envelope_lower", "envelope_upper", "overlap_lower", "overlap_upper"):
            _optional_finite(getattr(self, name), name, positive=True)
        if (self.envelope_lower is None) != (self.envelope_upper is None):
            raise ValueError("envelope bounds must be present or absent together")
        if self.envelope_lower is not None and self.envelope_lower > self.envelope_upper:
            raise ValueError("envelope bounds must be ordered")
        if (self.overlap_lower is None) != (self.overlap_upper is None):
            raise ValueError("overlap bounds must be present or absent together")
        if self.overlap_lower is not None and self.overlap_lower > self.overlap_upper:
            raise ValueError("overlap bounds must be ordered")
        if self.publication_status is AggregationStatus.RESOLVED:
            if (
                self.overall_central_value is None
                or self.central_estimator is not PublicationCentralEstimator.MEDIAN_OF_FAMILY_CENTRALS
                or len(families) < 2
                or self.envelope_semantics is not PublicationRangeSemantics.FAMILY_ENVELOPE
                or self.overlap_semantics is not PublicationRangeSemantics.COMMON_OVERLAP
            ):
                raise ValueError("resolved publication requires a median central and both range concepts")
        elif self.overall_central_value is not None or self.central_estimator is not None:
            raise ValueError("non-resolved publications cannot retain a central value or estimator")
        if self.publication_status is AggregationStatus.UNAVAILABLE and families:
            raise ValueError("unavailable publication requires zero eligible families")
        if self.publication_status in {AggregationStatus.RESOLVED, AggregationStatus.WIDE} and len(families) < 2:
            raise ValueError("resolved or wide publication requires at least two eligible families")
        if self.publication_status is AggregationStatus.UNAVAILABLE and self.envelope_lower is not None:
            raise ValueError("unavailable publication cannot expose a range")
        if len(families) == 1 and self.envelope_semantics is not PublicationRangeSemantics.SINGLE_FAMILY_RANGE:
            raise ValueError("a one-family range must be explicitly labeled SINGLE_FAMILY_RANGE")
        if len(families) >= 2 and self.envelope_semantics is not PublicationRangeSemantics.FAMILY_ENVELOPE:
            raise ValueError("a multi-family envelope must be explicitly labeled FAMILY_ENVELOPE")
        if self.overlap_lower is not None and self.overlap_semantics is not PublicationRangeSemantics.COMMON_OVERLAP:
            raise ValueError("overlap bounds require COMMON_OVERLAP semantics")
        if self.overlap_lower is None and self.overlap_semantics is not None:
            raise ValueError("overlap semantics require overlap bounds")
        expectation_ids = _texts(self.expectation_evidence_ids, "expectation_evidence_id")
        scenario_ids = _texts(self.scenario_evidence_ids, "scenario_evidence_id")
        reference_ids = _texts(self.reference_evidence_ids, "reference_evidence_id")
        expected_expectations = {
            item.evidence_id for item in supplemental
            if item.evidence_type is PublicationEvidenceType.REVERSE_DCF_CANONICAL_EXPECTATION
        }
        expected_scenarios = {
            item.evidence_id for item in supplemental
            if item.evidence_type is PublicationEvidenceType.REVERSE_DCF_SCENARIO
        }
        expected_references = {
            item.evidence_id for item in supplemental
            if item.evidence_type in {
                PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
                PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
            }
        }
        if (
            set(expectation_ids) != expected_expectations
            or set(scenario_ids) != expected_scenarios
            or set(reference_ids) != expected_references
        ):
            raise ValueError("supplemental evidence IDs must exactly match their controlled categories")
        object.__setattr__(self, "expectation_evidence_ids", expectation_ids)
        object.__setattr__(self, "scenario_evidence_ids", scenario_ids)
        object.__setattr__(self, "reference_evidence_ids", reference_ids)
        object.__setattr__(self, "blocking_reasons", tuple(dict.fromkeys(_texts(self.blocking_reasons, "blocking_reason"))))
        object.__setattr__(self, "issues", tuple(dict.fromkeys(_texts(self.issues, "issue"))))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(_texts(self.warnings, "warning"))))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
