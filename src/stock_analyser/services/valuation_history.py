"""Deterministic own-history multiple distributions over canonical observations only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable

from .statistics import linear_quantile

from stock_analyser.domain import (
    DataIssue,
    HistoricalDistributionUsability,
    HistoricalDuplicateHandling,
    HistoricalMultipleDistribution,
    HistoricalMultipleType,
    HistoricalQuantileConvention,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    HistoricalWindow,
    IssueSeverity,
    historical_multiple_semantics,
    stable_historical_distribution_id,
)


_WINDOW_YEARS = {
    HistoricalWindow.THREE_YEAR: 3,
    HistoricalWindow.FIVE_YEAR: 5,
    HistoricalWindow.TEN_YEAR: 10,
}


@dataclass(frozen=True, slots=True)
class HistoricalWindowMinimum:
    window: HistoricalWindow
    minimum_observation_count: int
    minimum_span_coverage: float

    def __post_init__(self) -> None:
        if not isinstance(self.window, HistoricalWindow):
            raise TypeError("window must use HistoricalWindow")
        if (
            isinstance(self.minimum_observation_count, bool)
            or not isinstance(self.minimum_observation_count, int)
            or self.minimum_observation_count < 1
        ):
            raise ValueError("minimum_observation_count must be a positive integer")
        if (
            isinstance(self.minimum_span_coverage, bool)
            or not math.isfinite(float(self.minimum_span_coverage))
            or not 0 < float(self.minimum_span_coverage) <= 1
        ):
            raise ValueError("minimum_span_coverage must be finite and in (0, 1]")


@dataclass(frozen=True, slots=True)
class HistoricalDistributionPolicy:
    policy_id: str
    default_window: HistoricalWindow
    window_minimums: tuple[HistoricalWindowMinimum, ...]
    quantile_convention: HistoricalQuantileConvention = HistoricalQuantileConvention.LINEAR
    duplicate_handling: HistoricalDuplicateHandling = (
        HistoricalDuplicateHandling.DEDUPLICATE_IDENTICAL_FAIL_CONFLICT
    )
    minimum_eligible_fraction: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if not isinstance(self.default_window, HistoricalWindow):
            raise TypeError("default_window must use HistoricalWindow")
        minimums = tuple(self.window_minimums)
        if {item.window for item in minimums} != set(HistoricalWindow):
            raise ValueError("window_minimums must configure every supported window exactly once")
        if len(minimums) != len(HistoricalWindow):
            raise ValueError("window_minimums cannot contain duplicate windows")
        object.__setattr__(self, "window_minimums", minimums)
        if self.quantile_convention is not HistoricalQuantileConvention.LINEAR:
            raise ValueError("Milestone 7B supports only explicit linear quantiles")
        if self.duplicate_handling is not HistoricalDuplicateHandling.DEDUPLICATE_IDENTICAL_FAIL_CONFLICT:
            raise ValueError("Milestone 7B supports only exact deduplication with conflict failure")
        if self.minimum_eligible_fraction is not None:
            raise ValueError("Milestone 7B defines no automatic ineligible-share rejection threshold")

    def minimum_for(self, window: HistoricalWindow) -> HistoricalWindowMinimum:
        if not isinstance(window, HistoricalWindow):
            raise TypeError("window must use HistoricalWindow")
        return next(item for item in self.window_minimums if item.window is window)


DEFAULT_HISTORICAL_DISTRIBUTION_POLICY = HistoricalDistributionPolicy(
    policy_id="historical-distribution-v1-inclusive-linear-no-trim",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=(
        HistoricalWindowMinimum(HistoricalWindow.THREE_YEAR, 500, 0.80),
        HistoricalWindowMinimum(HistoricalWindow.FIVE_YEAR, 750, 0.80),
        HistoricalWindowMinimum(HistoricalWindow.TEN_YEAR, 1_250, 0.80),
    ),
)


def _subtract_calendar_years(value: date, years: int) -> date:
    """Subtract calendar years, mapping leap-day starts to February 28."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, month=2, day=28)


def _duplicate_signature(item: HistoricalValuationObservation) -> tuple[object, ...]:
    return (
        float(item.value),
        item.valuation_basis,
        item.denominator,
        item.sampling,
        item.period_end,
        item.unit,
        item.source_metric,
        item.eligibility,
        item.eligibility_reason,
        item.quote_currency_context,
        item.reporting_currency_context,
    )


def _semantic_date_key(item: HistoricalValuationObservation) -> tuple[object, ...]:
    return (
        item.security_id,
        item.issuer_id,
        item.provider.lower(),
        item.multiple_type,
        item.observation_date,
    )


def build_historical_distribution(
    observations: Iterable[HistoricalValuationObservation],
    *,
    security_id: str,
    issuer_id: str,
    provider: str,
    multiple_type: HistoricalMultipleType,
    analysis_as_of: datetime,
    window: HistoricalWindow | None = None,
    sampling: HistoricalValuationSampling = HistoricalValuationSampling.DAILY,
    policy: HistoricalDistributionPolicy = DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
) -> HistoricalMultipleDistribution:
    """Build one unaltered distribution for one explicit provider/window/multiple."""
    if not isinstance(analysis_as_of, datetime) or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be a timezone-aware datetime")
    if not isinstance(multiple_type, HistoricalMultipleType):
        raise TypeError("multiple_type must use HistoricalMultipleType")
    if not isinstance(sampling, HistoricalValuationSampling):
        raise TypeError("sampling must use HistoricalValuationSampling")
    selected_window = policy.default_window if window is None else window
    if not isinstance(selected_window, HistoricalWindow):
        raise TypeError("window must use HistoricalWindow")
    security_id = security_id.strip()
    issuer_id = issuer_id.strip()
    selected_provider = provider.strip().lower()
    if not security_id or not issuer_id or not selected_provider:
        raise ValueError("security_id, issuer_id, and provider must be non-empty")

    window_end = analysis_as_of.date()
    window_start = _subtract_calendar_years(window_end, _WINDOW_YEARS[selected_window])
    requested_span_days = (window_end - window_start).days
    basis, denominator = historical_multiple_semantics(multiple_type)
    all_items = tuple(observations)
    matching_identity = tuple(
        item for item in all_items
        if item.security_id == security_id
        and item.issuer_id == issuer_id
        and item.multiple_type is multiple_type
        and item.sampling is sampling
    )
    future_count = sum(
        item.provider.lower() == selected_provider and item.observation_date > window_end
        for item in matching_identity
    )
    other_provider_count = sum(
        window_start <= item.observation_date <= window_end
        and item.provider.lower() != selected_provider
        for item in matching_identity
    )
    candidates = tuple(
        item for item in matching_identity
        if item.provider.lower() == selected_provider
        and window_start <= item.observation_date <= window_end
    )
    provider_symbols = tuple(sorted({item.provider_symbol for item in candidates}))
    issues: list[DataIssue] = []
    if future_count:
        issues.append(DataIssue(
            severity=IssueSeverity.INFO,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=f"excluded {future_count} observations after analysis_as_of",
        ))
    if other_provider_count:
        issues.append(DataIssue(
            severity=IssueSeverity.INFO,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=f"excluded {other_provider_count} observations from other providers",
            action="reconcile providers before constructing a provider-specific distribution",
        ))

    grouped: dict[tuple[object, ...], list[HistoricalValuationObservation]] = defaultdict(list)
    for item in candidates:
        grouped[_semantic_date_key(item)].append(item)
    deduplicated: list[HistoricalValuationObservation] = []
    duplicate_count = 0
    conflicting_duplicate_count = 0
    for key in sorted(grouped, key=lambda value: value[-1]):
        group = sorted(grouped[key], key=lambda item: (item.observation_id, item.provider_symbol))
        duplicate_count += len(group) - 1
        signatures = {_duplicate_signature(item) for item in group}
        if len(signatures) > 1:
            conflicting_duplicate_count += 1
            continue
        deduplicated.append(group[0])
    if duplicate_count and not conflicting_duplicate_count:
        issues.append(DataIssue(
            severity=IssueSeverity.INFO,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=f"deduplicated {duplicate_count} identical canonical date observations",
        ))
    if conflicting_duplicate_count:
        issues.append(DataIssue(
            severity=IssueSeverity.BLOCKING,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=f"found {conflicting_duplicate_count} conflicting duplicate observation dates",
            action="reconcile conflicting canonical observations before distribution construction",
        ))

    eligible = tuple(
        item for item in deduplicated
        if item.eligibility is HistoricalValuationEligibility.ELIGIBLE
        and math.isfinite(float(item.value))
        and item.value > 0
    )
    ineligible_count = len(deduplicated) - len(eligible) + conflicting_duplicate_count
    total_candidate_count = len(deduplicated) + conflicting_duplicate_count
    eligible_count = len(eligible)
    eligible_fraction = (
        eligible_count / total_candidate_count if total_candidate_count else None
    )
    distribution_id = stable_historical_distribution_id(
        security_id=security_id,
        issuer_id=issuer_id,
        provider=selected_provider,
        multiple_type=multiple_type,
        sampling=sampling,
        window=selected_window,
        analysis_as_of=analysis_as_of,
        policy_id=policy.policy_id,
    )
    common = dict(
        distribution_id=distribution_id,
        security_id=security_id,
        issuer_id=issuer_id,
        provider=selected_provider,
        multiple_type=multiple_type,
        valuation_basis=basis,
        denominator=denominator,
        sampling=sampling,
        window=selected_window,
        analysis_as_of=analysis_as_of,
        window_start=window_start,
        window_end=window_end,
        policy_id=policy.policy_id,
        quantile_convention=policy.quantile_convention,
        duplicate_handling=policy.duplicate_handling,
        total_candidate_count=total_candidate_count,
        eligible_count=eligible_count,
        ineligible_count=ineligible_count,
        duplicate_count=duplicate_count,
        conflicting_duplicate_count=conflicting_duplicate_count,
        eligible_fraction=eligible_fraction,
        provider_symbols=provider_symbols,
    )
    if conflicting_duplicate_count:
        return HistoricalMultipleDistribution(
            **common,
            usability=HistoricalDistributionUsability.INSUFFICIENT,
            sample_count=0,
            calendar_month_count=0,
            requested_span_days=requested_span_days,
            actual_span_days=None,
            span_coverage=None,
            earliest_observation=None,
            latest_observation=None,
            p25=None,
            median=None,
            p75=None,
            minimum=None,
            maximum=None,
            mean=None,
            standard_deviation=None,
            iqr=None,
            issues=tuple(issues),
        )

    values = tuple(sorted(float(item.value) for item in eligible))
    if not values:
        issues.append(DataIssue(
            severity=IssueSeverity.WARNING,
            metric=multiple_type.value,
            provider=selected_provider,
            reason="selected window has no eligible historical multiple observations",
        ))
        return HistoricalMultipleDistribution(
            **common,
            usability=HistoricalDistributionUsability.INSUFFICIENT,
            sample_count=0,
            calendar_month_count=0,
            requested_span_days=requested_span_days,
            actual_span_days=None,
            span_coverage=None,
            earliest_observation=None,
            latest_observation=None,
            p25=None,
            median=None,
            p75=None,
            minimum=None,
            maximum=None,
            mean=None,
            standard_deviation=None,
            iqr=None,
            issues=tuple(issues),
        )

    earliest = min(item.observation_date for item in eligible)
    latest = max(item.observation_date for item in eligible)
    actual_span_days = (latest - earliest).days
    span_coverage = actual_span_days / requested_span_days if requested_span_days else 1.0
    minimum_policy = policy.minimum_for(selected_window)
    count_pass = len(values) >= minimum_policy.minimum_observation_count
    span_pass = span_coverage >= minimum_policy.minimum_span_coverage
    if count_pass and span_pass:
        usability = HistoricalDistributionUsability.USABLE
    elif count_pass or span_pass:
        usability = HistoricalDistributionUsability.PARTIAL
    else:
        usability = HistoricalDistributionUsability.INSUFFICIENT
    if not count_pass:
        issues.append(DataIssue(
            severity=IssueSeverity.WARNING,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=(
                f"eligible sample count {len(values)} is below the configured "
                f"{selected_window.value} minimum of {minimum_policy.minimum_observation_count}"
            ),
        ))
    if not span_pass:
        issues.append(DataIssue(
            severity=IssueSeverity.WARNING,
            metric=multiple_type.value,
            provider=selected_provider,
            reason=(
                f"eligible span coverage {span_coverage:.6f} is below the configured "
                f"minimum of {minimum_policy.minimum_span_coverage:.6f}"
            ),
        ))
    p25 = linear_quantile(values, 0.25)
    median = linear_quantile(values, 0.50)
    p75 = linear_quantile(values, 0.75)
    mean = math.fsum(values) / len(values)
    population_variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    return HistoricalMultipleDistribution(
        **common,
        usability=usability,
        sample_count=len(values),
        calendar_month_count=len({(item.observation_date.year, item.observation_date.month) for item in eligible}),
        requested_span_days=requested_span_days,
        actual_span_days=actual_span_days,
        span_coverage=span_coverage,
        earliest_observation=earliest,
        latest_observation=latest,
        p25=p25,
        median=median,
        p75=p75,
        minimum=values[0],
        maximum=values[-1],
        mean=mean,
        standard_deviation=math.sqrt(population_variance),
        iqr=p75 - p25,
        supporting_observation_ids=tuple(item.observation_id for item in sorted(
            eligible, key=lambda item: (item.observation_date, item.observation_id),
        )),
        issues=tuple(issues),
    )


def build_historical_window_availability(
    observations: Iterable[HistoricalValuationObservation],
    *,
    security_id: str,
    issuer_id: str,
    provider: str,
    multiple_type: HistoricalMultipleType,
    analysis_as_of: datetime,
    sampling: HistoricalValuationSampling = HistoricalValuationSampling.DAILY,
    policy: HistoricalDistributionPolicy = DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
) -> tuple[HistoricalMultipleDistribution, ...]:
    """Report 3Y/5Y/10Y independently; never choose or blend a window."""
    items = tuple(observations)
    return tuple(
        build_historical_distribution(
            items,
            security_id=security_id,
            issuer_id=issuer_id,
            provider=provider,
            multiple_type=multiple_type,
            analysis_as_of=analysis_as_of,
            window=window,
            sampling=sampling,
            policy=policy,
        )
        for window in (
            HistoricalWindow.THREE_YEAR,
            HistoricalWindow.FIVE_YEAR,
            HistoricalWindow.TEN_YEAR,
        )
    )
