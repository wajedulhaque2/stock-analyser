"""Canonical-only forward annual consensus selection and safe derivations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from stock_analyser.domain import (
    DataIssue,
    EstimateCase,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    stable_observation_id,
)


_CASE_ORDER = {EstimateCase.LOW: 0, EstimateCase.AVERAGE: 1, EstimateCase.HIGH: 2}
_SUPPORTED_LEVEL_METRICS = {
    MetricId.REVENUE, MetricId.EBIT, MetricId.EBITDA, MetricId.NET_INCOME, MetricId.EPS,
}


@dataclass(frozen=True, slots=True)
class ForwardConsensusPeriod:
    horizon_index: int
    fiscal_year: int
    period_start: date
    period_end: date
    observations: tuple[MetricObservation, ...]

    @property
    def horizon_label(self) -> str:
        return f"FY{self.horizon_index}"

    def observation(self, metric_id: MetricId, estimate_case: EstimateCase) -> MetricObservation | None:
        return next(
            (
                item for item in self.observations
                if item.metric_id is metric_id and item.estimate_case is estimate_case
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class ForwardConsensus:
    as_of_at: datetime
    provider_symbol: str | None
    periods: tuple[ForwardConsensusPeriod, ...] = ()
    historical_estimates: tuple[MetricObservation, ...] = ()
    derived_observations: tuple[MetricObservation, ...] = ()
    issues: tuple[DataIssue, ...] = ()

    @property
    def fy1(self) -> ForwardConsensusPeriod | None:
        return self.periods[0] if self.periods else None

    @property
    def fy2(self) -> ForwardConsensusPeriod | None:
        return self.periods[1] if len(self.periods) > 1 else None


def _group_key(observation: MetricObservation) -> tuple[object, ...]:
    return (
        observation.metric_id,
        observation.period_start,
        observation.period_end,
        observation.fiscal_year,
        observation.currency,
        observation.unit,
        observation.provenance.provider_symbol,
    )


def _derived_observation(
    metric_id: MetricId,
    value: float,
    inputs: tuple[MetricObservation, ...],
    *,
    source_metric: str,
    transformation: str,
) -> MetricObservation:
    period = inputs[-1]
    provider = period.provenance.provider
    provider_symbol = period.provenance.provider_symbol
    retrieved_at = max(item.retrieved_at for item in inputs)
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset="v1_forward_consensus_derived",
        provider_symbol=provider_symbol,
        retrieved_at=retrieved_at,
        as_of_at=period.as_of_at,
        transformation_steps=(transformation,),
        input_observation_ids=tuple(item.observation_id for item in inputs),
        source_metric=source_metric,
    )
    observation_id = stable_observation_id(
        metric_id=metric_id.value,
        provider=provider,
        provider_symbol=provider_symbol,
        frequency=Frequency.ANNUAL.value,
        observation_type=ObservationType.ESTIMATE.value,
        estimate_case=EstimateCase.AVERAGE.value,
        period_start=str(period.period_start),
        period_end=str(period.period_end),
        as_of_at=period.as_of_at.isoformat(),
    )
    return MetricObservation(
        observation_id=observation_id,
        metric_id=metric_id,
        value=value,
        unit=MetricUnit.PERCENT_DECIMAL,
        currency=None,
        frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ESTIMATE,
        estimate_case=EstimateCase.AVERAGE,
        period_start=period.period_start,
        period_end=period.period_end,
        fiscal_year=period.fiscal_year,
        analyst_count=None,
        retrieved_at=retrieved_at,
        as_of_at=period.as_of_at,
        provenance=provenance,
    )


def build_forward_consensus(
    observations: Iterable[MetricObservation],
    *,
    as_of_at: datetime,
) -> ForwardConsensus:
    """Select annual periods ending after as-of; FY1 is the first such period, never NTM."""
    if as_of_at.tzinfo is None or as_of_at.utcoffset() is None:
        raise ValueError("as_of_at must be timezone-aware")

    issues: list[DataIssue] = []
    semantically_valid: list[MetricObservation] = []
    for observation in observations:
        if observation.provenance.provider.lower() != "fmp":
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR, metric=observation.metric_id, provider="consensus_service",
                reason="forward consensus currently accepts canonical FMP observations only",
            ))
            continue
        if observation.observation_type is not ObservationType.ESTIMATE or observation.frequency is not Frequency.ANNUAL:
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR, metric=observation.metric_id, provider="fmp",
                reason="forward consensus requires annual estimate observations",
            ))
            continue
        if observation.metric_id not in _SUPPORTED_LEVEL_METRICS:
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR, metric=observation.metric_id, provider="fmp",
                reason="metric is not an approved FMP forward-consensus level",
            ))
            continue
        if observation.as_of_at != as_of_at:
            issues.append(DataIssue(
                severity=IssueSeverity.ERROR, metric=observation.metric_id, provider="fmp",
                reason="observation snapshot does not match the requested consensus as-of time",
                action="build each consensus snapshot from one explicit as-of time",
            ))
            continue
        semantically_valid.append(observation)

    symbols = {item.provenance.provider_symbol for item in semantically_valid}
    if len(symbols) > 1:
        issues.append(DataIssue(
            severity=IssueSeverity.BLOCKING, metric="forward_consensus", provider="fmp",
            reason="multiple FMP provider symbols cannot be combined into one consensus snapshot",
        ))
        return ForwardConsensus(as_of_at=as_of_at, provider_symbol=None, issues=tuple(issues))
    provider_symbol = next(iter(symbols), None)

    groups: dict[tuple[object, ...], list[MetricObservation]] = defaultdict(list)
    for observation in semantically_valid:
        groups[_group_key(observation)].append(observation)

    blocked_keys = set()
    for key, group in groups.items():
        cases: dict[EstimateCase, MetricObservation] = {}
        for observation in group:
            if observation.estimate_case in cases:
                blocked_keys.add(key)
                issues.append(DataIssue(
                    severity=IssueSeverity.BLOCKING, metric=observation.metric_id, provider="fmp",
                    reason=f"duplicate {observation.estimate_case.value} estimate case",
                ))
            cases[observation.estimate_case] = observation
        low = cases.get(EstimateCase.LOW)
        average = cases.get(EstimateCase.AVERAGE)
        high = cases.get(EstimateCase.HIGH)
        invalid = (
            (low is not None and average is not None and low.value > average.value)
            or (average is not None and high is not None and average.value > high.value)
            or (low is not None and high is not None and low.value > high.value)
        )
        if invalid:
            blocked_keys.add(key)
            issues.append(DataIssue(
                severity=IssueSeverity.BLOCKING, metric=group[0].metric_id, provider="fmp",
                reason="estimate cases violate low <= average <= high",
                action="withhold the metric/period; ranges are not reordered or converted into scenarios",
            ))

    accepted = tuple(item for item in semantically_valid if _group_key(item) not in blocked_keys)
    historical = tuple(sorted(
        (item for item in accepted if item.period_end is not None and item.period_end <= as_of_at.date()),
        key=lambda item: (item.period_end, item.metric_id.value, _CASE_ORDER[item.estimate_case]),
    ))
    forward = tuple(item for item in accepted if item.period_end is not None and item.period_end > as_of_at.date())

    period_groups: dict[tuple[object, ...], list[MetricObservation]] = defaultdict(list)
    for observation in forward:
        period_groups[(observation.period_end, observation.fiscal_year, observation.period_start)].append(observation)
    periods = []
    for index, ((period_end, fiscal_year, period_start), items) in enumerate(
        sorted(period_groups.items(), key=lambda pair: pair[0][0]), start=1,
    ):
        ordered = tuple(sorted(items, key=lambda item: (item.metric_id.value, _CASE_ORDER[item.estimate_case])))
        periods.append(ForwardConsensusPeriod(index, fiscal_year, period_start, period_end, ordered))

    derived: list[MetricObservation] = []
    for previous, current in zip(periods, periods[1:]):
        prior_revenue = previous.observation(MetricId.REVENUE, EstimateCase.AVERAGE)
        current_revenue = current.observation(MetricId.REVENUE, EstimateCase.AVERAGE)
        if (
            prior_revenue is not None and current_revenue is not None
            and current.fiscal_year == previous.fiscal_year + 1
            and prior_revenue.currency == current_revenue.currency
            and prior_revenue.unit is MetricUnit.CURRENCY
            and current_revenue.unit is MetricUnit.CURRENCY
        ):
            if prior_revenue.value != 0:
                derived.append(_derived_observation(
                    MetricId.REVENUE_GROWTH,
                    current_revenue.value / prior_revenue.value - 1,
                    (prior_revenue, current_revenue),
                    source_metric="derived:average_revenue_growth",
                    transformation="average forward revenue divided by prior adjacent average forward revenue minus one",
                ))
            else:
                issues.append(DataIssue(
                    severity=IssueSeverity.WARNING, metric=MetricId.REVENUE_GROWTH, provider="fmp",
                    reason="average revenue growth cannot be derived from a zero prior-period denominator",
                ))

    for period in periods:
        revenue = period.observation(MetricId.REVENUE, EstimateCase.AVERAGE)
        if revenue is None or revenue.value == 0:
            continue
        for numerator_metric, output_metric, source_metric, description in (
            (MetricId.EBIT, MetricId.EBIT_MARGIN, "derived:average_ebit_margin", "average EBIT divided by same-period average revenue"),
            (
                MetricId.EBITDA, MetricId.EBITDA_MARGIN, "derived:average_ebitda_margin",
                "average EBITDA divided by same-period average revenue",
            ),
        ):
            numerator = period.observation(numerator_metric, EstimateCase.AVERAGE)
            if numerator is None:
                continue
            if (
                numerator.currency != revenue.currency
                or numerator.unit is not MetricUnit.CURRENCY
                or revenue.unit is not MetricUnit.CURRENCY
            ):
                issues.append(DataIssue(
                    severity=IssueSeverity.WARNING, metric=output_metric, provider="fmp",
                    reason="average margin inputs have mismatched currency or unit semantics",
                ))
                continue
            derived.append(_derived_observation(
                output_metric,
                numerator.value / revenue.value,
                (numerator, revenue),
                source_metric=source_metric,
                transformation=description,
            ))

    return ForwardConsensus(
        as_of_at=as_of_at,
        provider_symbol=provider_symbol,
        periods=tuple(periods),
        historical_estimates=historical,
        derived_observations=tuple(derived),
        issues=tuple(issues),
    )
