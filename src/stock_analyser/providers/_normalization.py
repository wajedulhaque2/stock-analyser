"""Provider-neutral helpers for constructing canonical actual observations."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from stock_analyser.domain import (
    EstimateCase,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    stable_observation_id,
)


def aware_datetime(value: Any, *, fallback: datetime | None = None) -> tuple[datetime, bool]:
    """Return an aware datetime and whether the fallback was used."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=timezone.utc)
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    elif fallback is not None:
        return fallback, True
    else:
        raise ValueError("a source datetime is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source datetime must be timezone-aware")
    return parsed, False


def calendar_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip()[:10])
    raise ValueError("a source calendar date is required")


def annual_period(raw_period_end: Any, fiscal_year_end: str) -> tuple[date, date]:
    """Construct an annual start only when period end aligns with explicit MM-DD metadata."""
    period_end = calendar_date(raw_period_end)
    if not isinstance(fiscal_year_end, str):
        raise ValueError("fiscal-year-end metadata is unavailable")
    month, day = (int(part) for part in fiscal_year_end.split("-"))
    expected_day = min(day, calendar.monthrange(period_end.year, month)[1])
    if (period_end.month, period_end.day) != (month, expected_day):
        raise ValueError("period end does not align with canonical fiscal-year-end metadata")
    prior_day = min(day, calendar.monthrange(period_end.year - 1, month)[1])
    prior_end = date(period_end.year - 1, month, prior_day)
    return prior_end + timedelta(days=1), period_end


def actual_observation(
    *,
    provider: str,
    dataset: str,
    provider_symbol: str,
    source_metric: str,
    metric_id: MetricId,
    value: float,
    unit: MetricUnit,
    currency: str | None,
    frequency: Frequency,
    retrieved_at: datetime,
    as_of_at: datetime,
    period_start: date | None,
    period_end: date,
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
    transformations: tuple[str, ...] = (),
) -> MetricObservation:
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset=dataset,
        provider_symbol=provider_symbol,
        retrieved_at=retrieved_at,
        as_of_at=as_of_at,
        transformation_steps=transformations,
        source_metric=source_metric,
    )
    observation_id = stable_observation_id(
        metric_id=metric_id.value,
        provider=provider,
        provider_symbol=provider_symbol,
        frequency=frequency.value,
        observation_type=ObservationType.ACTUAL.value,
        estimate_case=EstimateCase.NOT_APPLICABLE.value,
        period_start=str(period_start or ""),
        period_end=str(period_end),
        as_of_at=as_of_at.isoformat(),
    )
    return MetricObservation(
        observation_id=observation_id,
        metric_id=metric_id,
        value=float(value),
        unit=unit,
        currency=currency,
        frequency=frequency,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        retrieved_at=retrieved_at,
        as_of_at=as_of_at,
        provenance=provenance,
    )


def estimate_observation(
    *,
    provider: str,
    dataset: str,
    provider_symbol: str,
    source_metric: str,
    metric_id: MetricId,
    value: float,
    unit: MetricUnit,
    currency: str,
    estimate_case: EstimateCase,
    analyst_count: int | None,
    retrieved_at: datetime,
    as_of_at: datetime,
    period_start: date,
    period_end: date,
    fiscal_year: int,
    frequency: Frequency = Frequency.ANNUAL,
    fiscal_quarter: int | None = None,
    transformations: tuple[str, ...] = (),
) -> MetricObservation:
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset=dataset,
        provider_symbol=provider_symbol,
        retrieved_at=retrieved_at,
        as_of_at=as_of_at,
        transformation_steps=transformations,
        source_metric=source_metric,
    )
    observation_id = stable_observation_id(
        metric_id=metric_id.value,
        provider=provider,
        provider_symbol=provider_symbol,
        frequency=frequency.value,
        observation_type=ObservationType.ESTIMATE.value,
        estimate_case=estimate_case.value,
        period_start=str(period_start),
        period_end=str(period_end),
        as_of_at=as_of_at.isoformat(),
    )
    return MetricObservation(
        observation_id=observation_id,
        metric_id=metric_id,
        value=float(value),
        unit=unit,
        currency=currency,
        frequency=frequency,
        observation_type=ObservationType.ESTIMATE,
        estimate_case=estimate_case,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        analyst_count=analyst_count,
        retrieved_at=retrieved_at,
        as_of_at=as_of_at,
        provenance=provenance,
    )
