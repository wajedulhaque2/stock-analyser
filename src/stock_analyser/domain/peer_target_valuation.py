"""Immutable target-side application contracts for one peer EV/EBITDA distribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math

from .enums import (
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardPeriodSelection,
    PeerMethod,
    PeerValuationStatistic,
    ShareCountBasis,
    ValuationBasis,
    ValuationMethodStatus,
)
from .models import (
    DataIssue,
    Provenance,
    ValuationResult,
    _require_text,
    _validate_aware_datetime,
    _validate_currency,
)


def _stable_id(prefix: str, *parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def stable_peer_target_selection_id(*parts: object) -> str:
    return _stable_id("peertargetsel", *parts)


def stable_peer_target_point_id(*parts: object) -> str:
    return _stable_id("peertargetpoint", *parts)


def stable_peer_target_valuation_id(*parts: object) -> str:
    return _stable_id("peertargetvalue", *parts)


@dataclass(frozen=True, slots=True)
class PeerTargetValuationSelection:
    """Explicit target denominator and already-approved bridge/share selection."""

    selection_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    distribution_id: str
    peer_set_id: str
    peer_valuation_subset_id: str
    selected_forward_period: ForwardPeriodSelection
    target_forward_observation_id: str
    target_forward_period_end: date
    estimate_case: EstimateCase
    bridge_method: EnterpriseBridgeMethod
    bridge_id: str
    share_basis: ShareCountBasis
    share_observation_id: str
    policy_id: str

    def __post_init__(self) -> None:
        for name in (
            "selection_id", "target_security_id", "target_issuer_id", "distribution_id",
            "peer_set_id", "peer_valuation_subset_id", "target_forward_observation_id",
            "bridge_id", "share_observation_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        for value, enum_type, name in (
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.bridge_method, EnterpriseBridgeMethod, "bridge_method"),
            (self.share_basis, ShareCountBasis, "share_basis"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer target valuation requires the AVERAGE target estimate")
        if not isinstance(self.target_forward_period_end, date):
            raise TypeError("target_forward_period_end must use date")
        if self.target_forward_period_end <= self.analysis_as_of.date():
            raise ValueError("target forward period must end after analysis_as_of")


@dataclass(frozen=True, slots=True)
class PeerTargetValuationPoint:
    """One peer-statistic application with a complete enterprise-to-equity trace."""

    point_id: str
    statistic: PeerValuationStatistic
    target_security_id: str
    target_issuer_id: str
    multiple_type: PeerMethod
    valuation_basis: ValuationBasis
    analysis_as_of: datetime
    distribution_id: str
    peer_set_id: str
    peer_valuation_subset_id: str
    selected_forward_period: ForwardPeriodSelection
    target_forward_period_end: date
    estimate_case: EstimateCase
    peer_multiple: float
    target_forward_ebitda: float
    target_forward_observation_id: str
    implied_enterprise_value: float
    enterprise_equity_adjustment: float
    implied_equity_value: float
    share_count: float
    share_basis: ShareCountBasis
    share_observation_id: str
    per_share_value: float | None
    currency: str
    bridge_method: EnterpriseBridgeMethod
    bridge_id: str
    status: ValuationMethodStatus
    supporting_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    issues: tuple[DataIssue, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in (
            "point_id", "target_security_id", "target_issuer_id", "distribution_id",
            "peer_set_id", "peer_valuation_subset_id", "target_forward_observation_id",
            "share_observation_id", "bridge_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.statistic, PeerValuationStatistic, "statistic"),
            (self.multiple_type, PeerMethod, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.share_basis, ShareCountBasis, "share_basis"),
            (self.bridge_method, EnterpriseBridgeMethod, "bridge_method"),
            (self.status, ValuationMethodStatus, "status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.multiple_type is not PeerMethod.EV_EBITDA or self.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("Milestone 8D supports enterprise-basis EV_EBITDA only")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer target points require AVERAGE target EBITDA")
        if self.status is ValuationMethodStatus.PARTIAL:
            raise ValueError("individual peer valuation points are valid or unavailable")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        if self.target_forward_period_end <= self.analysis_as_of.date():
            raise ValueError("target forward period must end after analysis_as_of")
        for name in (
            "peer_multiple", "target_forward_ebitda", "implied_enterprise_value",
            "enterprise_equity_adjustment", "implied_equity_value", "share_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.peer_multiple <= 0 or self.target_forward_ebitda <= 0 or self.share_count <= 0:
            raise ValueError("peer multiple, target EBITDA, and approved shares must be positive")
        if not math.isclose(
            self.implied_enterprise_value,
            self.peer_multiple * self.target_forward_ebitda,
        ):
            raise ValueError("implied enterprise value must equal peer multiple times target EBITDA")
        if not math.isclose(
            self.implied_equity_value,
            self.implied_enterprise_value - self.enterprise_equity_adjustment,
        ):
            raise ValueError("implied equity value must equal enterprise value minus adjustment")
        if self.per_share_value is not None and (
            isinstance(self.per_share_value, bool) or not math.isfinite(float(self.per_share_value))
        ):
            raise ValueError("per_share_value must be finite when present")
        if self.status is ValuationMethodStatus.VALID:
            if self.implied_equity_value <= 0 or self.per_share_value is None or self.per_share_value <= 0:
                raise ValueError("valid peer point requires positive equity and per-share value")
            if not math.isclose(self.per_share_value, self.implied_equity_value / self.share_count):
                raise ValueError("per-share value must equal implied equity divided by approved shares")
            if self.issues:
                raise ValueError("valid peer point cannot carry blocking issues")
        else:
            if self.per_share_value is not None:
                raise ValueError("unavailable peer point cannot expose per-share value")
            if not self.issues:
                raise ValueError("unavailable peer point requires a structured issue")
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting observation IDs must be unique")
        for required in (self.target_forward_observation_id, self.share_observation_id):
            if required not in ids:
                raise ValueError("point support must include target forward and share observations")
        object.__setattr__(self, "supporting_observation_ids", ids)
        provenance = tuple(self.provenance)
        if not provenance or any(not isinstance(value, Provenance) for value in provenance):
            raise TypeError("peer valuation point requires provenance")
        object.__setattr__(self, "provenance", provenance)
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        object.__setattr__(self, "issues", issues)


@dataclass(frozen=True, slots=True)
class PeerTargetValuationResult:
    """One non-aggregated target peer method result and optional generic V1 view."""

    result_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    multiple_type: PeerMethod
    valuation_basis: ValuationBasis
    distribution_id: str
    peer_set_id: str
    peer_valuation_subset_id: str
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    lower_point: PeerTargetValuationPoint | None
    central_point: PeerTargetValuationPoint | None
    upper_point: PeerTargetValuationPoint | None
    currency: str | None
    bridge_method: EnterpriseBridgeMethod | None
    bridge_id: str | None
    share_basis: ShareCountBasis | None
    share_observation_id: str | None
    target_forward_observation_id: str | None
    target_forward_period_end: date | None
    status: ValuationMethodStatus
    issues: tuple[DataIssue, ...]
    warnings: tuple[str, ...]
    supporting_observation_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]
    valuation_result: ValuationResult | None

    def __post_init__(self) -> None:
        for name in (
            "result_id", "target_security_id", "target_issuer_id", "distribution_id",
            "peer_set_id", "peer_valuation_subset_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        for value, enum_type, name in (
            (self.multiple_type, PeerMethod, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.status, ValuationMethodStatus, "status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.multiple_type is not PeerMethod.EV_EBITDA or self.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("Milestone 8D supports enterprise-basis EV_EBITDA only")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("peer target result requires AVERAGE target EBITDA")
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "warnings", tuple(_require_text(value, "warning") for value in self.warnings))
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting observation IDs must be unique")
        object.__setattr__(self, "supporting_observation_ids", ids)
        provenance = tuple(self.provenance)
        if any(not isinstance(value, Provenance) for value in provenance):
            raise TypeError("provenance must contain Provenance values")
        object.__setattr__(self, "provenance", provenance)
        points = (self.lower_point, self.central_point, self.upper_point)
        if all(point is None for point in points):
            if self.status is not ValuationMethodStatus.UNAVAILABLE or not issues:
                raise ValueError("a point-free peer result must be unavailable with a structured issue")
            if self.valuation_result is not None:
                raise ValueError("a point-free peer result cannot expose a generic monetary result")
            return
        if any(point is None for point in points):
            raise ValueError("peer valuation trace must contain all three points or none")
        if self.currency is None:
            raise ValueError("a traced peer result requires explicit currency")
        _validate_currency(self.currency, "currency")
        for name in ("bridge_id", "share_observation_id", "target_forward_observation_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if self.bridge_method is None or self.share_basis is None or self.target_forward_period_end is None:
            raise ValueError("a traced peer result requires bridge, share, and target-period evidence")
        expected_statistics = (
            PeerValuationStatistic.P25, PeerValuationStatistic.MEDIAN, PeerValuationStatistic.P75,
        )
        if tuple(point.statistic for point in points) != expected_statistics:
            raise ValueError("peer points must remain ordered P25, median, P75")
        for point in points:
            if (
                point.target_security_id != self.target_security_id
                or point.target_issuer_id != self.target_issuer_id
                or point.distribution_id != self.distribution_id
                or point.peer_set_id != self.peer_set_id
                or point.peer_valuation_subset_id != self.peer_valuation_subset_id
                or point.analysis_as_of != self.analysis_as_of
                or point.selected_forward_period is not self.selected_forward_period
                or point.currency != self.currency
                or point.bridge_method is not self.bridge_method
                or point.bridge_id != self.bridge_id
                or point.share_basis is not self.share_basis
                or point.share_observation_id != self.share_observation_id
                or point.target_forward_observation_id != self.target_forward_observation_id
                or point.target_forward_period_end != self.target_forward_period_end
            ):
                raise ValueError("peer points must match all result dimensions")
        valid = tuple(point.status is ValuationMethodStatus.VALID for point in points)
        expected_status = (
            ValuationMethodStatus.VALID if all(valid)
            else ValuationMethodStatus.PARTIAL if any(valid)
            else ValuationMethodStatus.UNAVAILABLE
        )
        if self.status is not expected_status:
            raise ValueError("peer result status must reflect its independently valid points")
        if not isinstance(self.valuation_result, ValuationResult):
            raise TypeError("a traced peer result requires the generic ValuationResult view")
        values = tuple(point.per_share_value if ok else None for point, ok in zip(points, valid))
        generic = self.valuation_result
        if (
            generic.method != PeerMethod.EV_EBITDA.value
            or generic.valuation_family != "peer"
            or generic.currency != self.currency
            or generic.status is not self.status
            or (generic.low, generic.central, generic.high) != values
            or generic.input_observation_ids != ids
            or generic.provenance != provenance
            or generic.issues != issues
            or generic.warnings != self.warnings
        ):
            raise ValueError("generic valuation result must exactly mirror the peer result")
