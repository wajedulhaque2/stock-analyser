"""Collection-level canonical domain validation."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Iterable

from .enums import EstimateCase, IssueSeverity, ObservationType
from .models import DataIssue, MetricObservation, Provenance


def stable_observation_id(
    *,
    metric_id: str,
    provider: str,
    provider_symbol: str,
    frequency: str,
    observation_type: str,
    estimate_case: str,
    period_start: str,
    period_end: str,
    as_of_at: str,
) -> str:
    """Build a deterministic identifier from semantic identity, never retrieval order."""
    parts = (
        metric_id.strip().lower(), provider.strip().lower(), provider_symbol.strip().upper(), frequency.strip().lower(),
        observation_type.strip().lower(), estimate_case.strip().lower(), period_start,
        period_end, as_of_at,
    )
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"obs:{digest}"


def validate_estimate_case_collection(observations: Iterable[MetricObservation]) -> tuple[DataIssue, ...]:
    """Return blocking issues for inverted comparable low/average/high triplets."""
    groups: dict[tuple[object, ...], dict[EstimateCase, MetricObservation]] = defaultdict(dict)
    issues: list[DataIssue] = []
    for observation in observations:
        if observation.observation_type is not ObservationType.ESTIMATE:
            continue
        key = (
            observation.metric_id,
            observation.frequency,
            observation.period_start,
            observation.period_end,
            observation.fiscal_year,
            observation.fiscal_quarter,
            observation.currency,
            observation.unit,
            observation.as_of_at,
            observation.provenance.provider,
            observation.provenance.provider_symbol,
        )
        if observation.estimate_case in groups[key]:
            issues.append(DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric=observation.metric_id,
                provider=observation.provenance.provider,
                reason=f"duplicate {observation.estimate_case.value} estimate case",
                action="withhold the estimate collection until duplicates are reconciled",
            ))
        groups[key][observation.estimate_case] = observation

    required = {EstimateCase.LOW, EstimateCase.AVERAGE, EstimateCase.HIGH}
    for cases in groups.values():
        if required.issubset(cases):
            low = cases[EstimateCase.LOW]
            average = cases[EstimateCase.AVERAGE]
            high = cases[EstimateCase.HIGH]
            if not low.value <= average.value <= high.value:
                issues.append(DataIssue(
                    severity=IssueSeverity.BLOCKING,
                    metric=low.metric_id,
                    provider=low.provenance.provider,
                    reason="estimate cases violate low <= average <= high",
                    expected="low <= average <= high",
                    observed=f"{low.value} <= {average.value} <= {high.value}",
                    action="withhold the estimate collection and flag the provider data",
                ))
    return tuple(issues)


def validate_derived_provenance(provenance: Provenance) -> None:
    """Require derived data to identify at least one source observation."""
    if provenance.transformation_steps and not provenance.input_observation_ids:
        raise ValueError("derived provenance requires input_observation_ids")
