"""Fail-closed canonical selection for repeated reported actual observations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from stock_analyser.domain.actuals import CanonicalActualSelection
from stock_analyser.domain.enums import (
    ActualSelectionReason,
    ActualSelectionStatus,
    Frequency,
    MetricId,
    ObservationType,
)
from stock_analyser.domain.models import MetricObservation


_SUBSTITUTED_AS_OF_MARKER = "source as-of unavailable"


def _source_semantics(item: MetricObservation) -> tuple[object, ...]:
    provenance = item.provenance
    return (
        provenance.provider.lower(),
        provenance.endpoint_or_dataset,
        provenance.provider_symbol,
        provenance.source_metric,
        item.metric_id,
        item.frequency,
        item.period_start,
        item.period_end,
        item.fiscal_year,
        item.fiscal_quarter,
        item.unit,
        item.currency,
    )


def _explicit_source_chronology(item: MetricObservation) -> bool:
    return not any(
        _SUBSTITUTED_AS_OF_MARKER in step.lower()
        for step in item.provenance.transformation_steps
    )


def select_canonical_actual(
    observations: Iterable[MetricObservation],
    *,
    metric_id: MetricId,
    frequency: Frequency,
    period_end: date,
    analysis_as_of: datetime,
) -> CanonicalActualSelection:
    """Select only exact evidence or a uniquely later verified source revision.

    Retrieval order/time is never an ordering rule. Different providers, datasets,
    provider symbols, or source metrics are distinct source semantics and therefore
    cannot silently replace one another at this boundary.
    """
    if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    rows = tuple(
        item for item in observations
        if item.metric_id is metric_id
        and item.frequency is frequency
        and item.observation_type is ObservationType.ACTUAL
        and item.period_end == period_end
        and item.as_of_at <= analysis_as_of
    )
    ids = tuple(item.observation_id for item in rows)
    provenance = tuple(item.provenance for item in rows)

    def result(
        status: ActualSelectionStatus,
        reason: ActualSelectionReason,
        *,
        selected: MetricObservation | None = None,
        deduplicated: tuple[str, ...] = (),
    ) -> CanonicalActualSelection:
        return CanonicalActualSelection(
            metric_id=metric_id,
            frequency=frequency,
            period_end=period_end,
            analysis_as_of=analysis_as_of,
            status=status,
            reason=reason,
            candidate_count=len(rows),
            candidate_observation_ids=ids,
            deduplicated_observation_ids=deduplicated,
            provenance=provenance,
            selected_observation=selected,
        )

    if not rows:
        return result(ActualSelectionStatus.UNAVAILABLE, ActualSelectionReason.NO_ELIGIBLE_OBSERVATION)

    by_id: dict[str, list[MetricObservation]] = {}
    for item in rows:
        by_id.setdefault(item.observation_id, []).append(item)
    if any(
        len({(_source_semantics(item), item.as_of_at, item.value) for item in group}) > 1
        for group in by_id.values()
    ):
        return result(ActualSelectionStatus.UNRESOLVED, ActualSelectionReason.OBSERVATION_ID_COLLISION)

    unique: list[MetricObservation] = []
    duplicate_ids: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for item in sorted(rows, key=lambda value: value.observation_id):
        exact_key = (_source_semantics(item), item.as_of_at, item.value)
        if exact_key in seen:
            duplicate_ids.append(item.observation_id)
            continue
        seen.add(exact_key)
        unique.append(item)

    if len(unique) == 1:
        reason = (
            ActualSelectionReason.IDENTICAL_DUPLICATES_DEDUPLICATED
            if duplicate_ids else ActualSelectionReason.SINGLE_OBSERVATION
        )
        return result(
            ActualSelectionStatus.SELECTED,
            reason,
            selected=unique[0],
            deduplicated=tuple(duplicate_ids),
        )

    semantic_groups = {_source_semantics(item) for item in unique}
    if len(semantic_groups) != 1:
        return result(
            ActualSelectionStatus.UNRESOLVED,
            ActualSelectionReason.INCOMPATIBLE_SOURCE_SEMANTICS,
            deduplicated=tuple(duplicate_ids),
        )

    if len({item.value for item in unique}) == 1:
        selected = min(unique, key=lambda item: item.observation_id)
        dropped = tuple(
            item.observation_id for item in unique
            if item is not selected
        )
        return result(
            ActualSelectionStatus.SELECTED,
            ActualSelectionReason.IDENTICAL_DUPLICATES_DEDUPLICATED,
            selected=selected,
            deduplicated=(*tuple(duplicate_ids), *dropped),
        )

    if all(_explicit_source_chronology(item) for item in unique):
        latest_as_of = max(item.as_of_at for item in unique)
        latest = tuple(item for item in unique if item.as_of_at == latest_as_of)
        if len(latest) == 1:
            return result(
                ActualSelectionStatus.SELECTED,
                ActualSelectionReason.LATEST_VERIFIED_REVISION,
                selected=latest[0],
                deduplicated=tuple(duplicate_ids),
            )

    return result(
        ActualSelectionStatus.UNRESOLVED,
        ActualSelectionReason.CONFLICTING_VALUES_WITHOUT_VERIFIED_CHRONOLOGY,
        deduplicated=tuple(duplicate_ids),
    )
