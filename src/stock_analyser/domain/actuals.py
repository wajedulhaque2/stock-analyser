"""Canonical contracts for deterministic reported-actual selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .enums import ActualSelectionReason, ActualSelectionStatus, Frequency, MetricId
from .models import MetricObservation, Provenance


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CanonicalActualSelection:
    metric_id: MetricId
    frequency: Frequency
    period_end: date
    analysis_as_of: datetime
    status: ActualSelectionStatus
    reason: ActualSelectionReason
    candidate_count: int
    candidate_observation_ids: tuple[str, ...]
    deduplicated_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    selected_observation: MetricObservation | None = None

    def __post_init__(self) -> None:
        _aware(self.analysis_as_of, "analysis_as_of")
        if isinstance(self.candidate_count, bool) or self.candidate_count < 0:
            raise ValueError("candidate_count must be non-negative")
        if self.candidate_count != len(self.candidate_observation_ids):
            raise ValueError("candidate_count must match candidate_observation_ids")
        object.__setattr__(self, "candidate_observation_ids", tuple(self.candidate_observation_ids))
        object.__setattr__(self, "deduplicated_observation_ids", tuple(dict.fromkeys(self.deduplicated_observation_ids)))
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        if self.status is ActualSelectionStatus.SELECTED:
            if self.selected_observation is None:
                raise ValueError("selected actual status requires an observation")
            selected = self.selected_observation
            if selected.metric_id is not self.metric_id or selected.frequency is not self.frequency:
                raise ValueError("selected observation must match metric and frequency")
            if selected.period_end != self.period_end:
                raise ValueError("selected observation must match period_end")
            if selected.observation_id not in self.candidate_observation_ids:
                raise ValueError("selected observation must be one of the candidates")
        elif self.selected_observation is not None:
            raise ValueError("unresolved/unavailable actual selection cannot carry an observation")

