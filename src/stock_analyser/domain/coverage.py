"""Immutable audit contracts for V1 data coverage and future-method readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .enums import (
    AnalystCoverageBand,
    CoverageDimension,
    CoverageLevel,
    DimensionStatus,
    EvidenceConfidence,
    MetricId,
    ValuationFamily,
    ValuationReadiness,
)
from .models import DataIssue


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _unique_text(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} values must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class CoverageCount:
    name: str
    value: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError("coverage count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CoverageDate:
    name: str
    value: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        if not isinstance(self.value, date):
            raise ValueError("coverage date value must be a date")


@dataclass(frozen=True, slots=True)
class AnalystHorizonCoverage:
    horizon: int
    period_end: date
    fiscal_year: int | None
    revenue_analyst_count: int | None
    eps_analyst_count: int | None
    revenue_band: AnalystCoverageBand
    eps_band: AnalystCoverageBand
    overall_band: AnalystCoverageBand
    supporting_observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.horizon, bool) or not isinstance(self.horizon, int) or self.horizon < 1:
            raise ValueError("horizon must be a positive integer")
        for name in ("revenue_analyst_count", "eps_analyst_count"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when present")
        object.__setattr__(
            self,
            "supporting_observation_ids",
            _unique_text(tuple(self.supporting_observation_ids), "supporting_observation_id"),
        )


@dataclass(frozen=True, slots=True)
class CoverageDimensionResult:
    dimension: CoverageDimension
    status: DimensionStatus
    reason: str
    as_of_at: datetime
    supporting_observation_ids: tuple[str, ...] = ()
    supporting_reconciliation_ids: tuple[str, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    counts: tuple[CoverageCount, ...] = ()
    dates: tuple[CoverageDate, ...] = ()
    available_metrics: tuple[MetricId, ...] = ()
    missing_metrics: tuple[MetricId, ...] = ()
    analyst_horizons: tuple[AnalystHorizonCoverage, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        _aware(self.as_of_at, "as_of_at")
        object.__setattr__(
            self,
            "supporting_observation_ids",
            _unique_text(tuple(self.supporting_observation_ids), "supporting_observation_id"),
        )
        object.__setattr__(
            self,
            "supporting_reconciliation_ids",
            _unique_text(tuple(self.supporting_reconciliation_ids), "supporting_reconciliation_id"),
        )
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "counts", tuple(self.counts))
        object.__setattr__(self, "dates", tuple(self.dates))
        object.__setattr__(self, "available_metrics", tuple(dict.fromkeys(self.available_metrics)))
        object.__setattr__(self, "missing_metrics", tuple(dict.fromkeys(self.missing_metrics)))
        object.__setattr__(self, "analyst_horizons", tuple(self.analyst_horizons))
        if len({item.name for item in self.counts}) != len(self.counts):
            raise ValueError("coverage count names must be unique per dimension")
        if len({item.name for item in self.dates}) != len(self.dates):
            raise ValueError("coverage date names must be unique per dimension")


@dataclass(frozen=True, slots=True)
class ValuationReadinessResult:
    family: ValuationFamily
    readiness: ValuationReadiness
    reason: str
    as_of_at: datetime
    supporting_dimensions: tuple[CoverageDimension, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    issues: tuple[DataIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        _aware(self.as_of_at, "as_of_at")
        object.__setattr__(
            self, "supporting_dimensions", tuple(dict.fromkeys(self.supporting_dimensions))
        )
        object.__setattr__(
            self,
            "missing_requirements",
            _unique_text(tuple(self.missing_requirements), "missing_requirement"),
        )
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True, slots=True)
class CoverageAssessment:
    analysis_as_of: datetime
    policy_id: str
    overall_coverage: CoverageLevel
    evidence_confidence: EvidenceConfidence
    dimension_results: tuple[CoverageDimensionResult, ...]
    valuation_readiness_results: tuple[ValuationReadinessResult, ...]
    critical_issues: tuple[DataIssue, ...] = ()
    warnings: tuple[DataIssue, ...] = ()
    supporting_observation_ids: tuple[str, ...] = ()
    supporting_reconciliation_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _aware(self.analysis_as_of, "analysis_as_of")
        object.__setattr__(self, "policy_id", _text(self.policy_id, "policy_id"))
        object.__setattr__(self, "dimension_results", tuple(self.dimension_results))
        object.__setattr__(
            self, "valuation_readiness_results", tuple(self.valuation_readiness_results)
        )
        if len({item.dimension for item in self.dimension_results}) != len(self.dimension_results):
            raise ValueError("dimension_results must contain at most one result per dimension")
        if len({item.family for item in self.valuation_readiness_results}) != len(
            self.valuation_readiness_results
        ):
            raise ValueError("valuation_readiness_results must contain at most one result per family")
        object.__setattr__(self, "critical_issues", tuple(self.critical_issues))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(
            self,
            "supporting_observation_ids",
            _unique_text(tuple(self.supporting_observation_ids), "supporting_observation_id"),
        )
        object.__setattr__(
            self,
            "supporting_reconciliation_ids",
            _unique_text(tuple(self.supporting_reconciliation_ids), "supporting_reconciliation_id"),
        )
        object.__setattr__(self, "reasons", tuple(_text(value, "reason") for value in self.reasons))
