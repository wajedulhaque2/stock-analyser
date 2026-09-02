"""Canonical historical valuation evidence; no distribution or fair-value logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math
import re

from .enums import (
    HistoricalDistributionUsability,
    HistoricalDuplicateHandling,
    HistoricalMultipleType,
    HistoricalQuantileConvention,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationSampling,
    HistoricalWindow,
    MetricUnit,
    ValuationBasis,
)
from .models import DataIssue, Provenance, _require_text, _validate_aware_datetime, _validate_currency


_HISTORICAL_ID_RE = re.compile(r"^histval:[0-9a-f]{32}$")
_DISTRIBUTION_ID_RE = re.compile(r"^histdist:[0-9a-f]{32}$")
_SEMANTICS = {
    HistoricalMultipleType.P_E: (
        ValuationBasis.EQUITY,
        HistoricalValuationDenominator.DILUTED_EPS,
    ),
    HistoricalMultipleType.EV_EBITDA: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.EBITDA,
    ),
    HistoricalMultipleType.EV_EBIT: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
    ),
}


def historical_multiple_semantics(
    multiple_type: HistoricalMultipleType,
) -> tuple[ValuationBasis, HistoricalValuationDenominator]:
    if not isinstance(multiple_type, HistoricalMultipleType):
        raise TypeError("multiple_type must use HistoricalMultipleType")
    return _SEMANTICS[multiple_type]


def stable_historical_valuation_id(
    *,
    security_id: str,
    issuer_id: str,
    provider: str,
    provider_symbol: str,
    multiple_type: HistoricalMultipleType,
    observation_date: date,
    period_end: date | None,
    sampling: HistoricalValuationSampling,
    source_metric: str,
) -> str:
    """Build identity from economic/source coordinates, independent of retrieval order."""
    parts = (
        security_id.strip(),
        issuer_id.strip(),
        provider.strip().lower(),
        provider_symbol.strip().upper(),
        multiple_type.value,
        observation_date.isoformat(),
        period_end.isoformat() if period_end is not None else "",
        sampling.value,
        source_metric.strip(),
    )
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"histval:{digest}"


def stable_historical_distribution_id(
    *,
    security_id: str,
    issuer_id: str,
    provider: str,
    multiple_type: HistoricalMultipleType,
    sampling: HistoricalValuationSampling,
    window: HistoricalWindow,
    analysis_as_of: datetime,
    policy_id: str,
) -> str:
    """Build a stable distribution identity without provider-symbol dependence."""
    parts = (
        security_id.strip(),
        issuer_id.strip(),
        provider.strip().lower(),
        multiple_type.value,
        sampling.value,
        window.value,
        analysis_as_of.isoformat(),
        policy_id.strip(),
    )
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"histdist:{digest}"


@dataclass(frozen=True, slots=True)
class HistoricalValuationObservation:
    """One dated provider-calculated multiple and its future-use eligibility."""

    observation_id: str
    security_id: str
    issuer_id: str
    provider: str
    provider_symbol: str
    multiple_type: HistoricalMultipleType
    valuation_basis: ValuationBasis
    denominator: HistoricalValuationDenominator
    value: float
    observation_date: date
    sampling: HistoricalValuationSampling
    as_of_at: datetime
    retrieved_at: datetime
    provenance: Provenance
    source_metric: str
    eligibility: HistoricalValuationEligibility
    eligibility_reason: str | None = None
    period_end: date | None = None
    quote_currency_context: str | None = None
    reporting_currency_context: str | None = None
    unit: MetricUnit = MetricUnit.RATIO

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, str) or not _HISTORICAL_ID_RE.fullmatch(self.observation_id):
            raise ValueError("observation_id must be a stable historical-valuation identifier")
        for name in ("security_id", "issuer_id", "provider", "provider_symbol", "source_metric"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.multiple_type, HistoricalMultipleType, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.denominator, HistoricalValuationDenominator, "denominator"),
            (self.sampling, HistoricalValuationSampling, "sampling"),
            (self.eligibility, HistoricalValuationEligibility, "eligibility"),
            (self.unit, MetricUnit, "unit"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use the controlled {enum_type.__name__} enum")
        if self.unit is not MetricUnit.RATIO:
            raise ValueError("historical valuation multiples must be dimensionless ratios")
        expected_basis, expected_denominator = _SEMANTICS[self.multiple_type]
        if self.valuation_basis is not expected_basis or self.denominator is not expected_denominator:
            raise ValueError("multiple basis and denominator must match the canonical economic definition")
        if isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("value must be finite; NaN and infinity are forbidden")
        if not isinstance(self.observation_date, date) or isinstance(self.observation_date, datetime):
            raise TypeError("observation_date must be a date distinct from retrieval time")
        if self.period_end is not None and (
            not isinstance(self.period_end, date) or isinstance(self.period_end, datetime)
        ):
            raise TypeError("period_end must be a date when supplied")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        if self.as_of_at != self.provenance.as_of_at or self.retrieved_at != self.provenance.retrieved_at:
            raise ValueError("observation timestamps must match provenance timestamps")
        if self.provenance.provider != self.provider or self.provenance.provider_symbol != self.provider_symbol:
            raise ValueError("provider identity must match provenance")
        if self.provenance.source_metric != self.source_metric:
            raise ValueError("source_metric must be present and match provenance")
        _validate_currency(self.quote_currency_context, "quote_currency_context", required=False)
        _validate_currency(self.reporting_currency_context, "reporting_currency_context", required=False)
        if self.eligibility is HistoricalValuationEligibility.ELIGIBLE:
            if self.value <= 0:
                raise ValueError("eligible historical multiples must be positive")
            if self.eligibility_reason is not None:
                raise ValueError("eligible observations cannot carry an ineligibility reason")
        else:
            object.__setattr__(
                self,
                "eligibility_reason",
                _require_text(self.eligibility_reason, "eligibility_reason"),
            )
        if self.value <= 0 and self.eligibility is not HistoricalValuationEligibility.INELIGIBLE:
            raise ValueError("non-positive multiples must be explicitly ineligible")


@dataclass(frozen=True, slots=True)
class HistoricalMultipleDistribution:
    """Auditable descriptive statistics for one explicit historical window."""

    distribution_id: str
    security_id: str
    issuer_id: str
    provider: str
    multiple_type: HistoricalMultipleType
    valuation_basis: ValuationBasis
    denominator: HistoricalValuationDenominator
    sampling: HistoricalValuationSampling
    window: HistoricalWindow
    analysis_as_of: datetime
    window_start: date
    window_end: date
    policy_id: str
    quantile_convention: HistoricalQuantileConvention
    duplicate_handling: HistoricalDuplicateHandling
    usability: HistoricalDistributionUsability
    total_candidate_count: int
    eligible_count: int
    ineligible_count: int
    sample_count: int
    duplicate_count: int
    conflicting_duplicate_count: int
    calendar_month_count: int
    requested_span_days: int
    actual_span_days: int | None
    span_coverage: float | None
    eligible_fraction: float | None
    earliest_observation: date | None
    latest_observation: date | None
    p25: float | None
    median: float | None
    p75: float | None
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None
    iqr: float | None
    independent_regime_count: int | None = None
    provider_symbols: tuple[str, ...] = ()
    supporting_observation_ids: tuple[str, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    unit: MetricUnit = MetricUnit.RATIO
    observations_altered: bool = False
    winsorization_applied: bool = False
    trimming_applied: bool = False
    interpolation_applied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.distribution_id, str) or not _DISTRIBUTION_ID_RE.fullmatch(self.distribution_id):
            raise ValueError("distribution_id must be a stable historical-distribution identifier")
        for name in ("security_id", "issuer_id", "provider", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.multiple_type, HistoricalMultipleType, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.denominator, HistoricalValuationDenominator, "denominator"),
            (self.sampling, HistoricalValuationSampling, "sampling"),
            (self.window, HistoricalWindow, "window"),
            (self.quantile_convention, HistoricalQuantileConvention, "quantile_convention"),
            (self.duplicate_handling, HistoricalDuplicateHandling, "duplicate_handling"),
            (self.usability, HistoricalDistributionUsability, "usability"),
            (self.unit, MetricUnit, "unit"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use the controlled {enum_type.__name__} enum")
        if self.unit is not MetricUnit.RATIO:
            raise ValueError("historical distributions must remain dimensionless ratios")
        expected_basis, expected_denominator = historical_multiple_semantics(self.multiple_type)
        if self.valuation_basis is not expected_basis or self.denominator is not expected_denominator:
            raise ValueError("distribution basis and denominator must preserve canonical multiple semantics")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if self.window_end != self.analysis_as_of.date():
            raise ValueError("window_end must equal the explicit analysis_as_of date")
        if self.window_start > self.window_end:
            raise ValueError("window_start must be on or before window_end")
        count_names = (
            "total_candidate_count", "eligible_count", "ineligible_count", "sample_count",
            "duplicate_count", "conflicting_duplicate_count", "calendar_month_count",
            "requested_span_days",
        )
        for name in count_names:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.actual_span_days is not None and (
            isinstance(self.actual_span_days, bool)
            or not isinstance(self.actual_span_days, int)
            or self.actual_span_days < 0
        ):
            raise ValueError("actual_span_days must be a non-negative integer when present")
        for name in ("span_coverage", "eligible_fraction"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1
            ):
                raise ValueError(f"{name} must be finite and between zero and one")
        if self.total_candidate_count != self.eligible_count + self.ineligible_count:
            raise ValueError("candidate counts must reconcile")
        if self.sample_count > self.eligible_count:
            raise ValueError("sample_count cannot exceed eligible_count")
        if self.eligible_fraction is None and self.total_candidate_count:
            raise ValueError("non-empty candidates require eligible_fraction")
        if self.eligible_fraction is not None and self.total_candidate_count:
            expected_fraction = self.eligible_count / self.total_candidate_count
            if not math.isclose(self.eligible_fraction, expected_fraction, rel_tol=0, abs_tol=1e-12):
                raise ValueError("eligible_fraction must match candidate counts")
        statistics = (
            self.p25, self.median, self.p75, self.minimum, self.maximum,
            self.mean, self.standard_deviation, self.iqr,
        )
        if self.sample_count:
            if any(value is None or not math.isfinite(float(value)) for value in statistics):
                raise ValueError("non-empty distributions require finite descriptive statistics")
            if self.earliest_observation is None or self.latest_observation is None:
                raise ValueError("non-empty distributions require earliest/latest dates")
            if self.actual_span_days is None or self.span_coverage is None:
                raise ValueError("non-empty distributions require span diagnostics")
            if not self.minimum <= self.p25 <= self.median <= self.p75 <= self.maximum:
                raise ValueError("distribution statistics must be ordered")
            if not math.isclose(self.iqr, self.p75 - self.p25, rel_tol=0, abs_tol=1e-12):
                raise ValueError("iqr must equal p75 minus p25")
        elif any(value is not None for value in statistics) or any(
            value is not None for value in (
                self.earliest_observation, self.latest_observation,
                self.actual_span_days, self.span_coverage,
            )
        ):
            raise ValueError("empty/failed distributions cannot carry descriptive statistics")
        if self.earliest_observation is not None and self.latest_observation is not None:
            if not self.window_start <= self.earliest_observation <= self.latest_observation <= self.window_end:
                raise ValueError("distribution dates must remain inside the selected window")
        if self.independent_regime_count is not None:
            raise ValueError("independent fundamental regime count is unknown in Milestone 7B")
        symbols = tuple(sorted({_require_text(value, "provider_symbol") for value in self.provider_symbols}))
        object.__setattr__(self, "provider_symbols", symbols)
        supporting = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(set(supporting)) != len(supporting):
            raise ValueError("supporting_observation_ids must be unique")
        if len(supporting) != self.sample_count:
            raise ValueError("supporting_observation_ids must identify every sampled observation")
        object.__setattr__(self, "supporting_observation_ids", supporting)
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.conflicting_duplicate_count:
            if self.usability is not HistoricalDistributionUsability.INSUFFICIENT or self.sample_count:
                raise ValueError("duplicate conflicts must fail the numeric distribution closed")
        if any((
            self.observations_altered,
            self.winsorization_applied,
            self.trimming_applied,
            self.interpolation_applied,
        )):
            raise ValueError("Milestone 7B cannot alter, trim, winsorize, or interpolate observations")
