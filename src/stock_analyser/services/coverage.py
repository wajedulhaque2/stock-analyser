"""Transparent, deterministic V1 coverage and evidence-readiness assessment.

This module classifies observable evidence only.  It performs no provider calls,
forecasting, pricing, method calculation, or legacy application wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Iterable

from stock_analyser.domain import (
    AnalystCoverageBand,
    AnalystHorizonCoverage,
    CapabilityResult,
    CapabilityStatus,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    CoverageAssessment,
    CoverageCount,
    CoverageDate,
    CoverageDimension,
    CoverageDimensionResult,
    CoverageLevel,
    DataAvailability,
    DataIssue,
    DefinitionVerificationStatus,
    DimensionStatus,
    EstimateCase,
    EvidenceConfidence,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    ObservationType,
    ReconciliationResult,
    ReconciliationStatus,
    SourceAgreementLevel,
    ValuationFamily,
    ValuationReadiness,
    ValuationReadinessResult,
)

from .macro import RiskFreeRateResult


def _positive_thresholds(values: tuple[int, ...], name: str) -> None:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise ValueError(f"{name} thresholds must be positive integers")


@dataclass(frozen=True, slots=True)
class HistoricalCoveragePolicy:
    strong_complete_periods: int = 5
    medium_complete_periods: int = 3
    limited_complete_periods: int = 1
    core_metric_groups: tuple[tuple[MetricId, ...], ...] = (
        (MetricId.REVENUE,),
        (MetricId.OPERATING_INCOME, MetricId.EBIT),
        (MetricId.NET_INCOME, MetricId.NET_INCOME_COMMON),
        (MetricId.OPERATING_CASH_FLOW,),
        (MetricId.CAPITAL_EXPENDITURE,),
    )

    def __post_init__(self) -> None:
        _positive_thresholds(
            (self.strong_complete_periods, self.medium_complete_periods, self.limited_complete_periods),
            "historical",
        )
        if not self.strong_complete_periods > self.medium_complete_periods > self.limited_complete_periods:
            raise ValueError("historical thresholds must descend from strong to limited")
        groups = tuple(tuple(group) for group in self.core_metric_groups)
        if not groups or any(not group for group in groups):
            raise ValueError("historical core metric groups must be non-empty")
        object.__setattr__(self, "core_metric_groups", groups)


@dataclass(frozen=True, slots=True)
class ForwardCoveragePolicy:
    strong_complete_periods: int = 3
    medium_complete_periods: int = 2
    limited_periods: int = 1
    revenue_metrics: tuple[MetricId, ...] = (MetricId.REVENUE,)
    profitability_metrics: tuple[MetricId, ...] = (
        MetricId.EBIT,
        MetricId.EBITDA,
        MetricId.NET_INCOME,
        MetricId.EPS,
    )

    def __post_init__(self) -> None:
        _positive_thresholds(
            (self.strong_complete_periods, self.medium_complete_periods, self.limited_periods),
            "forward",
        )
        if not self.strong_complete_periods > self.medium_complete_periods >= self.limited_periods:
            raise ValueError("forward thresholds must descend from strong to limited")
        for name in ("revenue_metrics", "profitability_metrics"):
            values = tuple(getattr(self, name))
            if not values:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class AnalystCoveragePolicy:
    strong_minimum: int = 20
    medium_minimum: int = 8
    limited_minimum: int = 3

    def __post_init__(self) -> None:
        _positive_thresholds(
            (self.strong_minimum, self.medium_minimum, self.limited_minimum), "analyst"
        )
        if not self.strong_minimum > self.medium_minimum > self.limited_minimum:
            raise ValueError("analyst thresholds must descend from strong to limited")


@dataclass(frozen=True, slots=True)
class CriticalIssuePolicy:
    identity_mismatch_fields: tuple[str, ...] = ("security_id", "issuer_id")
    near_term_conflict_metrics: tuple[MetricId, ...] = (MetricId.REVENUE, MetricId.EPS)
    blocking_severities: tuple[IssueSeverity, ...] = (IssueSeverity.BLOCKING,)

    def __post_init__(self) -> None:
        fields = tuple(value.strip().lower() for value in self.identity_mismatch_fields)
        if not fields or any(not value for value in fields):
            raise ValueError("identity mismatch fields must be non-empty")
        object.__setattr__(self, "identity_mismatch_fields", fields)
        object.__setattr__(self, "near_term_conflict_metrics", tuple(self.near_term_conflict_metrics))
        object.__setattr__(self, "blocking_severities", tuple(self.blocking_severities))


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    policy_id: str = "v1-coverage-policy-2026-08"
    historical: HistoricalCoveragePolicy = field(default_factory=HistoricalCoveragePolicy)
    forward: ForwardCoveragePolicy = field(default_factory=ForwardCoveragePolicy)
    analyst: AnalystCoveragePolicy = field(default_factory=AnalystCoveragePolicy)
    critical: CriticalIssuePolicy = field(default_factory=CriticalIssuePolicy)
    maximum_macro_age_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        if self.maximum_macro_age_days is not None and (
            isinstance(self.maximum_macro_age_days, bool)
            or not isinstance(self.maximum_macro_age_days, int)
            or self.maximum_macro_age_days < 0
        ):
            raise ValueError("maximum_macro_age_days must be non-negative when configured")


DEFAULT_COVERAGE_POLICY = CoveragePolicy()


@dataclass(frozen=True, slots=True)
class CoverageInputs:
    identity: CompanyIdentity
    canonical_observations: tuple[MetricObservation, ...] = ()
    cash_flow_observations: tuple[MetricObservation, ...] = ()
    reconciliations: tuple[ReconciliationResult, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    cash_flow_definitions: tuple[CashFlowDefinitionEvidence, ...] = ()
    risk_free_rate: RiskFreeRateResult | None = None
    issues: tuple[DataIssue, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "canonical_observations",
            "cash_flow_observations",
            "reconciliations",
            "capabilities",
            "cash_flow_definitions",
            "issues",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))


_MARKET_METRICS = {
    MetricId.SHARE_PRICE,
    MetricId.MARKET_CAP,
    MetricId.SHARES_BASIC,
    MetricId.SHARES_DILUTED,
    MetricId.SHARES_OUTSTANDING,
}
_SHARE_METRICS = {
    MetricId.SHARES_BASIC,
    MetricId.SHARES_DILUTED,
    MetricId.SHARES_OUTSTANDING,
}
_ANALYST_METRICS = {MetricId.REVENUE, MetricId.EPS}
_CASH_METRICS = {
    MetricId.OPERATING_CASH_FLOW,
    MetricId.CAPITAL_EXPENDITURE,
    MetricId.PROVIDER_DEFINED_FCF,
    MetricId.OCF_LESS_CAPEX,
    MetricId.FCFF,
    MetricId.FCFE,
}


def _eligible_observations(
    observations: Iterable[MetricObservation], analysis_as_of: datetime
) -> tuple[MetricObservation, ...]:
    return tuple(item for item in observations if item.as_of_at <= analysis_as_of)


def _reconciliation_id(result: ReconciliationResult) -> str:
    components = (
        result.canonical_observation_id,
        result.validator_observation_id or "none",
        result.stream.value,
        result.metric_id.value,
        result.comparison_as_of.isoformat(),
    )
    return "recon-" + sha256("|".join(components).encode("utf-8")).hexdigest()[:24]


def _macro_observation_id(result: RiskFreeRateResult) -> str | None:
    item = result.observation
    if item is None:
        return None
    components = (item.provider, item.series_id, item.observation_date.isoformat(), item.as_of_at.isoformat())
    return "macro-" + sha256("|".join(components).encode("utf-8")).hexdigest()[:24]


def _count(name: str, value: int) -> CoverageCount:
    return CoverageCount(name=name, value=value)


def _dimension_map(
    dimensions: tuple[CoverageDimensionResult, ...],
) -> dict[CoverageDimension, CoverageDimensionResult]:
    return {item.dimension: item for item in dimensions}


def _named_count(result: CoverageDimensionResult, name: str) -> int:
    return next((item.value for item in result.counts if item.name == name), 0)


def _band(count: int | None, policy: AnalystCoveragePolicy) -> AnalystCoverageBand:
    if count is None:
        return AnalystCoverageBand.UNKNOWN
    if count >= policy.strong_minimum:
        return AnalystCoverageBand.STRONG
    if count >= policy.medium_minimum:
        return AnalystCoverageBand.MEDIUM
    if count >= policy.limited_minimum:
        return AnalystCoverageBand.LIMITED
    return AnalystCoverageBand.WEAK


def _combined_band(*bands: AnalystCoverageBand) -> AnalystCoverageBand:
    known = tuple(item for item in bands if item is not AnalystCoverageBand.UNKNOWN)
    if not known:
        return AnalystCoverageBand.UNKNOWN
    rank = {
        AnalystCoverageBand.WEAK: 0,
        AnalystCoverageBand.LIMITED: 1,
        AnalystCoverageBand.MEDIUM: 2,
        AnalystCoverageBand.STRONG: 3,
    }
    return min(known, key=rank.__getitem__)


def _evaluate_market(
    observations: tuple[MetricObservation, ...], analysis_as_of: datetime
) -> CoverageDimensionResult:
    market = tuple(item for item in observations if item.metric_id in _MARKET_METRICS)
    prices = tuple(item for item in market if item.metric_id is MetricId.SHARE_PRICE)
    market_caps = tuple(item for item in market if item.metric_id is MetricId.MARKET_CAP)
    shares = tuple(item for item in market if item.metric_id in _SHARE_METRICS)
    if not prices:
        issue = DataIssue(
            severity=IssueSeverity.BLOCKING,
            metric=MetricId.SHARE_PRICE,
            provider="coverage_policy",
            reason="Missing canonical current price at the analysis snapshot.",
            action="Supply a canonical price observation whose as-of time is not after analysis_as_of.",
        )
        status = DimensionStatus.FAIL
        reason = "No canonical current-price observation is available at the analysis snapshot."
        issues = (issue,)
    elif market_caps or shares:
        status = DimensionStatus.PASS
        reason = "Current price and at least one capitalisation or share-count input are available."
        issues = ()
    else:
        status = DimensionStatus.PARTIAL
        reason = "Current price is available; capitalisation and share-count inputs are absent."
        issues = ()
    return CoverageDimensionResult(
        dimension=CoverageDimension.MARKET_DATA,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=tuple(item.observation_id for item in market),
        issues=issues,
        counts=(
            _count("current_price_observations", len(prices)),
            _count("market_cap_observations", len(market_caps)),
            _count("share_count_observations", len(shares)),
        ),
        available_metrics=tuple(dict.fromkeys(item.metric_id for item in market)),
    )


def _evaluate_history(
    observations: tuple[MetricObservation, ...],
    analysis_as_of: datetime,
    policy: HistoricalCoveragePolicy,
) -> CoverageDimensionResult:
    annual = tuple(
        item
        for item in observations
        if item.frequency is Frequency.ANNUAL
        and item.observation_type is ObservationType.ACTUAL
        and item.fiscal_year is not None
    )
    by_year: dict[int, list[MetricObservation]] = {}
    for item in annual:
        by_year.setdefault(item.fiscal_year, []).append(item)
    complete_years = tuple(
        year
        for year, items in sorted(by_year.items())
        if all(any(item.metric_id in group for item in items) for group in policy.core_metric_groups)
    )
    continuous = all(right - left == 1 for left, right in zip(complete_years, complete_years[1:]))
    if by_year:
        span_count = max(by_year) - min(by_year) + 1
        missing_periods = span_count - len(by_year)
    else:
        missing_periods = 0
    complete_count = len(complete_years)
    if complete_count >= policy.strong_complete_periods and continuous:
        status = DimensionStatus.PASS
        reason = f"{complete_count} complete, continuous annual actual periods satisfy the strong-history rule."
    elif annual:
        status = DimensionStatus.PARTIAL
        if complete_count >= policy.medium_complete_periods:
            label = "medium"
        elif complete_count >= policy.limited_complete_periods:
            label = "limited"
        else:
            label = "incomplete"
        continuity = "continuous" if continuous else "not continuous"
        reason = f"Annual actual history is {label}: {complete_count} complete periods; the complete-period series is {continuity}."
    else:
        status = DimensionStatus.NOT_AVAILABLE
        reason = "No canonical annual actual financial observations are available."
    available = tuple(dict.fromkeys(item.metric_id for item in annual))
    missing = tuple(
        group[0]
        for group in policy.core_metric_groups
        if not any(metric in available for metric in group)
    )
    dates: tuple[CoverageDate, ...] = ()
    if annual:
        period_ends = tuple(item.period_end for item in annual if item.period_end is not None)
        dates = (
            CoverageDate("earliest_annual_period_end", min(period_ends)),
            CoverageDate("latest_annual_period_end", max(period_ends)),
        )
    return CoverageDimensionResult(
        dimension=CoverageDimension.HISTORICAL_FINANCIALS,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=tuple(item.observation_id for item in annual),
        counts=(
            _count("annual_periods", len(by_year)),
            _count("complete_annual_periods", complete_count),
            _count("missing_periods", missing_periods),
        ),
        dates=dates,
        available_metrics=available,
        missing_metrics=missing,
    )


def _forward_observations(
    observations: tuple[MetricObservation, ...], analysis_as_of: datetime
) -> tuple[MetricObservation, ...]:
    return tuple(
        item
        for item in observations
        if item.provenance.provider.lower() == "fmp"
        and item.frequency is Frequency.ANNUAL
        and item.observation_type is ObservationType.ESTIMATE
        and item.estimate_case is EstimateCase.AVERAGE
        and item.period_end is not None
        and item.period_end > analysis_as_of.date()
    )


def _evaluate_forward(
    observations: tuple[MetricObservation, ...],
    analysis_as_of: datetime,
    policy: ForwardCoveragePolicy,
) -> tuple[CoverageDimensionResult, tuple[MetricObservation, ...]]:
    forward = _forward_observations(observations, analysis_as_of)
    by_period: dict[object, list[MetricObservation]] = {}
    for item in forward:
        by_period.setdefault(item.period_end, []).append(item)
    complete_periods = tuple(
        period
        for period, items in sorted(by_period.items())
        if any(item.metric_id in policy.revenue_metrics for item in items)
        and any(item.metric_id in policy.profitability_metrics for item in items)
    )
    revenue_only = tuple(
        period
        for period, items in sorted(by_period.items())
        if any(item.metric_id in policy.revenue_metrics for item in items)
        and not any(item.metric_id in policy.profitability_metrics for item in items)
    )
    if len(complete_periods) >= policy.strong_complete_periods:
        status = DimensionStatus.PASS
        reason = f"{len(complete_periods)} future annual periods contain average revenue plus a profitability or earnings metric."
    elif forward:
        status = DimensionStatus.PARTIAL
        if len(complete_periods) >= policy.medium_complete_periods:
            label = "medium"
        elif len(complete_periods) >= policy.limited_periods:
            label = "limited"
        else:
            label = "incomplete"
        reason = f"Forward annual consensus is {label}: {len(complete_periods)} core-complete periods across {len(by_period)} future periods."
    else:
        status = DimensionStatus.NOT_AVAILABLE
        reason = "No FMP canonical future annual average-consensus observations are available."
    dates: tuple[CoverageDate, ...] = ()
    if by_period:
        periods = tuple(by_period)
        dates = (
            CoverageDate("nearest_forward_period_end", min(periods)),
            CoverageDate("farthest_forward_period_end", max(periods)),
        )
    available = tuple(dict.fromkeys(item.metric_id for item in forward))
    required_candidates = policy.revenue_metrics + policy.profitability_metrics
    missing = tuple(metric for metric in required_candidates if metric not in available)
    result = CoverageDimensionResult(
        dimension=CoverageDimension.FORWARD_CONSENSUS,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=tuple(item.observation_id for item in forward),
        counts=(
            _count("future_annual_periods", len(by_period)),
            _count("core_complete_periods", len(complete_periods)),
            _count("revenue_only_periods", len(revenue_only)),
        ),
        dates=dates,
        available_metrics=available,
        missing_metrics=missing,
    )
    return result, forward


def _evaluate_analysts(
    forward: tuple[MetricObservation, ...],
    analysis_as_of: datetime,
    policy: AnalystCoveragePolicy,
) -> CoverageDimensionResult:
    by_period: dict[object, list[MetricObservation]] = {}
    for item in forward:
        if item.metric_id in _ANALYST_METRICS:
            by_period.setdefault(item.period_end, []).append(item)
    horizons: list[AnalystHorizonCoverage] = []
    for horizon, (period, items) in enumerate(sorted(by_period.items()), start=1):
        revenue_items = tuple(item for item in items if item.metric_id is MetricId.REVENUE)
        eps_items = tuple(item for item in items if item.metric_id is MetricId.EPS)
        revenue_count = next((item.analyst_count for item in revenue_items if item.analyst_count is not None), None)
        eps_count = next((item.analyst_count for item in eps_items if item.analyst_count is not None), None)
        revenue_band = _band(revenue_count, policy)
        eps_band = _band(eps_count, policy)
        horizons.append(
            AnalystHorizonCoverage(
                horizon=horizon,
                period_end=period,
                fiscal_year=next((item.fiscal_year for item in items if item.fiscal_year is not None), None),
                revenue_analyst_count=revenue_count,
                eps_analyst_count=eps_count,
                revenue_band=revenue_band,
                eps_band=eps_band,
                overall_band=_combined_band(revenue_band, eps_band),
                supporting_observation_ids=tuple(item.observation_id for item in items),
            )
        )
    known = tuple(
        item
        for item in horizons
        if item.revenue_analyst_count is not None or item.eps_analyst_count is not None
    )
    near = horizons[0].overall_band if horizons else AnalystCoverageBand.UNKNOWN
    if near is AnalystCoverageBand.STRONG:
        status = DimensionStatus.PASS
        reason = "The nearest future period has strong sourced analyst coverage; per-horizon decay remains visible."
    elif known:
        status = DimensionStatus.PARTIAL
        reason = f"Sourced analyst counts are available for {len(known)} horizons; nearest-horizon band is {near.value}."
    else:
        status = DimensionStatus.NOT_AVAILABLE
        reason = "Revenue and EPS analyst counts are missing; missing counts remain unknown rather than zero."
    counts = (
        _count("forward_horizons", len(horizons)),
        _count("horizons_with_known_counts", len(known)),
        _count("horizons_with_unknown_counts", len(horizons) - len(known)),
    )
    return CoverageDimensionResult(
        dimension=CoverageDimension.ANALYST_COVERAGE,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=tuple(
            dict.fromkeys(item_id for horizon in horizons for item_id in horizon.supporting_observation_ids)
        ),
        counts=counts,
        analyst_horizons=tuple(horizons),
    )


def _eligible_reconciliations(
    reconciliations: tuple[ReconciliationResult, ...], analysis_as_of: datetime
) -> tuple[ReconciliationResult, ...]:
    return tuple(
        item
        for item in reconciliations
        if item.comparison_as_of <= analysis_as_of and item.canonical_as_of_at <= analysis_as_of
    )


def _evaluate_agreement(
    reconciliations: tuple[ReconciliationResult, ...],
    capabilities: tuple[CapabilityResult, ...],
    analysis_as_of: datetime,
) -> CoverageDimensionResult:
    eligible = _eligible_reconciliations(reconciliations, analysis_as_of)
    compared = tuple(item for item in eligible if item.status is ReconciliationStatus.COMPARED)
    confirmed = tuple(item for item in compared if item.agreement_level is SourceAgreementLevel.CONFIRMED)
    warnings = tuple(item for item in compared if item.agreement_level is SourceAgreementLevel.WARNING)
    conflicts = tuple(item for item in compared if item.agreement_level is SourceAgreementLevel.CONFLICT)
    unscored = tuple(item for item in compared if item.agreement_level is SourceAgreementLevel.COMPARABLE_UNSCORED)
    not_comparable = tuple(item for item in eligible if item.status is ReconciliationStatus.NOT_COMPARABLE)
    no_validator = tuple(
        item
        for item in eligible
        if item.status in {ReconciliationStatus.NO_VALIDATOR, ReconciliationStatus.VALIDATOR_UNAVAILABLE}
    )
    locked = tuple(
        item
        for item in capabilities
        if item.checked_at <= analysis_as_of and item.status is CapabilityStatus.LOCKED
    )
    if conflicts:
        status = DimensionStatus.FAIL
        reason = f"{len(conflicts)} material source conflict(s) are present; other dimensions remain independently usable."
    elif warnings or unscored or not_comparable:
        status = DimensionStatus.PARTIAL
        reason = "Validation evidence contains warning, unscored, or not-comparable results."
    elif compared and len(confirmed) == len(compared):
        status = DimensionStatus.PASS
        reason = f"All {len(compared)} available validator comparisons are confirmed."
    else:
        status = DimensionStatus.NOT_AVAILABLE
        if locked:
            reason = "Validator capability is locked; no disagreement is inferred."
        elif no_validator:
            reason = "No validator observation is available; this is not a source conflict."
        else:
            reason = "No eligible reconciliation evidence exists at the analysis snapshot."
    return CoverageDimensionResult(
        dimension=CoverageDimension.SOURCE_AGREEMENT,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_reconciliation_ids=tuple(_reconciliation_id(item) for item in eligible),
        issues=tuple(issue for item in eligible for issue in item.issues),
        counts=(
            _count("confirmed", len(confirmed)),
            _count("warnings", len(warnings)),
            _count("conflicts", len(conflicts)),
            _count("comparable_unscored", len(unscored)),
            _count("not_comparable", len(not_comparable)),
            _count("no_validator", len(no_validator) + len(locked)),
        ),
    )


def _evaluate_cash_flow(
    observations: tuple[MetricObservation, ...],
    definitions: tuple[CashFlowDefinitionEvidence, ...],
    analysis_as_of: datetime,
) -> CoverageDimensionResult:
    cash = tuple(
        item
        for item in observations
        if item.metric_id in _CASH_METRICS
        and item.observation_type is ObservationType.ESTIMATE
        and item.period_end is not None
        and item.period_end > analysis_as_of.date()
    )
    cash_ids = {item.observation_id for item in cash}
    verified_definitions = tuple(
        item
        for item in definitions
        if item.verification_status is DefinitionVerificationStatus.VERIFIED
        and item.verified_at is not None
        and item.verified_at <= analysis_as_of
        and bool(cash_ids.intersection(item.observation_ids))
    )
    verified_fcff_ids = {
        observation_id
        for item in verified_definitions
        if item.is_verified_fcff
        for observation_id in item.observation_ids
        if observation_id in cash_ids
    }
    verified_fcfe_ids = {
        observation_id
        for item in verified_definitions
        if item.is_verified_fcfe
        for observation_id in item.observation_ids
        if observation_id in cash_ids
    }
    metric_counts = {metric: sum(item.metric_id is metric for item in cash) for metric in _CASH_METRICS}
    if verified_fcff_ids or verified_fcfe_ids:
        status = DimensionStatus.PASS
        reason = "Verified FCFF or FCFE definition evidence is linked to future cash-flow observations."
    elif cash:
        status = DimensionStatus.PARTIAL
        reason = "Future cash-flow observations exist, but no linked verified FCFF or FCFE definition is available."
    else:
        status = DimensionStatus.NOT_AVAILABLE
        reason = "No future cash-flow estimate observations are available."
    return CoverageDimensionResult(
        dimension=CoverageDimension.CASH_FLOW_EVIDENCE,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=tuple(item.observation_id for item in cash),
        counts=(
            _count("operating_cash_flow", metric_counts[MetricId.OPERATING_CASH_FLOW]),
            _count("capital_expenditure", metric_counts[MetricId.CAPITAL_EXPENDITURE]),
            _count("provider_defined_fcf", metric_counts[MetricId.PROVIDER_DEFINED_FCF]),
            _count("ocf_less_capex", metric_counts[MetricId.OCF_LESS_CAPEX]),
            _count("verified_fcff", len(verified_fcff_ids)),
            _count("verified_fcfe", len(verified_fcfe_ids)),
        ),
        available_metrics=tuple(dict.fromkeys(item.metric_id for item in cash)),
    )


def _evaluate_macro(
    identity: CompanyIdentity,
    risk_free_rate: RiskFreeRateResult | None,
    analysis_as_of: datetime,
    policy: CoveragePolicy,
) -> CoverageDimensionResult:
    supporting: tuple[str, ...] = ()
    dates: tuple[CoverageDate, ...] = ()
    if risk_free_rate is None:
        status = DimensionStatus.NOT_AVAILABLE
        reason = f"No approved {identity.reporting_currency} risk-free-rate result was supplied."
    elif risk_free_rate.currency != identity.reporting_currency:
        status = DimensionStatus.NOT_AVAILABLE
        reason = "Risk-free-rate currency does not match reporting currency; cross-currency fallback is forbidden."
    elif risk_free_rate.availability is not DataAvailability.AVAILABLE or risk_free_rate.observation is None:
        status = DimensionStatus.NOT_AVAILABLE
        reason = risk_free_rate.reason or "Currency-appropriate risk-free input is unavailable."
    elif (
        risk_free_rate.observation.observation_date > analysis_as_of.date()
        or risk_free_rate.observation.as_of_at > analysis_as_of
    ):
        status = DimensionStatus.NOT_AVAILABLE
        reason = "Risk-free observation is after analysis_as_of and was excluded."
    else:
        observation = risk_free_rate.observation
        identifier = _macro_observation_id(risk_free_rate)
        supporting = (identifier,) if identifier else ()
        dates = (CoverageDate("risk_free_observation_date", observation.observation_date),)
        age = (analysis_as_of.date() - observation.observation_date).days
        if policy.maximum_macro_age_days is not None and age > policy.maximum_macro_age_days:
            status = DimensionStatus.PARTIAL
            reason = f"Currency-appropriate risk-free input is {age} days old, beyond configured freshness."
        else:
            status = DimensionStatus.PASS
            reason = "Currency-appropriate risk-free input is available on or before analysis_as_of."
    return CoverageDimensionResult(
        dimension=CoverageDimension.MACRO_READINESS,
        status=status,
        reason=reason,
        as_of_at=analysis_as_of,
        supporting_observation_ids=supporting,
        dates=dates,
    )


def _not_evaluated_dimensions(analysis_as_of: datetime) -> tuple[CoverageDimensionResult, ...]:
    return (
        CoverageDimensionResult(
            dimension=CoverageDimension.HISTORICAL_VALUATION,
            status=DimensionStatus.NOT_EVALUATED,
            reason="The V1 historical valuation-distribution layer is not implemented.",
            as_of_at=analysis_as_of,
        ),
        CoverageDimensionResult(
            dimension=CoverageDimension.PEER_READINESS,
            status=DimensionStatus.NOT_EVALUATED,
            reason="The V1 peer-selection layer is not implemented.",
            as_of_at=analysis_as_of,
        ),
    )


def _readiness(
    dimensions: tuple[CoverageDimensionResult, ...], analysis_as_of: datetime
) -> tuple[ValuationReadinessResult, ...]:
    by_dimension = _dimension_map(dimensions)
    history = by_dimension[CoverageDimension.HISTORICAL_FINANCIALS]
    forward = by_dimension[CoverageDimension.FORWARD_CONSENSUS]
    cash = by_dimension[CoverageDimension.CASH_FLOW_EVIDENCE]
    macro = by_dimension[CoverageDimension.MACRO_READINESS]
    usable_history = history.status in {DimensionStatus.PASS, DimensionStatus.PARTIAL}
    usable_forward = forward.status in {DimensionStatus.PASS, DimensionStatus.PARTIAL}
    if usable_history and usable_forward:
        own_status = ValuationReadiness.PARTIAL
        own_reason = "Historical actuals and forward denominator evidence exist, but the V1 historical valuation-distribution layer is not implemented."
        own_missing = ("historical valuation-distribution service",)
    else:
        own_status = ValuationReadiness.NOT_READY
        own_reason = "Own-history prerequisites are incomplete and the V1 historical valuation-distribution layer is not implemented."
        own_missing = tuple(
            item
            for condition, item in (
                (usable_history, "usable historical annual actuals"),
                (usable_forward, "usable forward denominator consensus"),
            )
            if not condition
        ) + ("historical valuation-distribution service",)
    cash_inputs = cash.status is DimensionStatus.PASS and usable_forward and macro.status is DimensionStatus.PASS
    if cash_inputs:
        cash_status = ValuationReadiness.PARTIAL
        cash_reason = "Verified cash-flow, forward-horizon, and macro evidence exist; discount-rate and bridge machinery is absent."
        cash_missing = ("ERP", "WACC or cost of equity", "required bridge inputs")
    else:
        cash_status = ValuationReadiness.NOT_READY
        cash_reason = "One or more cash-flow prerequisites are absent, and discount-rate machinery is not implemented."
        cash_missing = tuple(
            item
            for condition, item in (
                (cash.status is DimensionStatus.PASS, "verified FCFF or FCFE definition"),
                (usable_forward, "sufficient aligned forward horizon"),
                (macro.status is DimensionStatus.PASS, "currency-appropriate risk-free input"),
            )
            if not condition
        ) + ("ERP", "WACC or cost of equity", "required bridge inputs")
    return (
        ValuationReadinessResult(
            family=ValuationFamily.OWN_HISTORY,
            readiness=own_status,
            reason=own_reason,
            as_of_at=analysis_as_of,
            supporting_dimensions=(
                CoverageDimension.HISTORICAL_FINANCIALS,
                CoverageDimension.FORWARD_CONSENSUS,
                CoverageDimension.HISTORICAL_VALUATION,
            ),
            missing_requirements=own_missing,
        ),
        ValuationReadinessResult(
            family=ValuationFamily.PEER,
            readiness=ValuationReadiness.NOT_READY,
            reason="The V1 peer-selection layer is not implemented.",
            as_of_at=analysis_as_of,
            supporting_dimensions=(CoverageDimension.PEER_READINESS,),
            missing_requirements=("peer-selection layer",),
        ),
        ValuationReadinessResult(
            family=ValuationFamily.CASH_FLOW,
            readiness=cash_status,
            reason=cash_reason,
            as_of_at=analysis_as_of,
            supporting_dimensions=(
                CoverageDimension.CASH_FLOW_EVIDENCE,
                CoverageDimension.FORWARD_CONSENSUS,
                CoverageDimension.MACRO_READINESS,
            ),
            missing_requirements=cash_missing,
        ),
        ValuationReadinessResult(
            family=ValuationFamily.REVERSE_CASH_FLOW,
            readiness=ValuationReadiness.NOT_READY,
            reason="The V1 reverse-cash-flow engine and its prerequisite contract are intentionally deferred.",
            as_of_at=analysis_as_of,
            missing_requirements=("reverse-cash-flow engine", "reverse-method prerequisite contract"),
        ),
    )


def _critical_and_warnings(
    inputs: CoverageInputs,
    dimensions: tuple[CoverageDimensionResult, ...],
    forward: tuple[MetricObservation, ...],
    analysis_as_of: datetime,
    policy: CoveragePolicy,
) -> tuple[tuple[DataIssue, ...], tuple[DataIssue, ...]]:
    critical: list[DataIssue] = []
    warnings: list[DataIssue] = []
    market = _dimension_map(dimensions)[CoverageDimension.MARKET_DATA]
    forward_result = _dimension_map(dimensions)[CoverageDimension.FORWARD_CONSENSUS]
    critical.extend(market.issues)
    if forward_result.status is DimensionStatus.NOT_AVAILABLE:
        critical.append(
            DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric="forward_consensus",
                provider="coverage_policy",
                reason="No canonical forward consensus exists at the analysis snapshot.",
                action="Supply eligible future annual FMP canonical consensus observations.",
            )
        )
    for issue in inputs.issues:
        if issue.severity in policy.critical.blocking_severities:
            critical.append(issue)
        else:
            warnings.append(issue)
    eligible_reconciliations = _eligible_reconciliations(inputs.reconciliations, analysis_as_of)
    nearest_period = min((item.period_end for item in forward if item.period_end is not None), default=None)
    for item in eligible_reconciliations:
        mismatches = " ".join(item.semantic_mismatches).lower()
        if any(field in mismatches for field in policy.critical.identity_mismatch_fields):
            critical.append(
                DataIssue(
                    severity=IssueSeverity.BLOCKING,
                    metric=item.metric_id,
                    provider="coverage_policy",
                    reason="Canonical and validator identity semantics do not match.",
                    expected=item.canonical_security_id,
                    observed=item.validator_security_id,
                    action="Resolve security and issuer identity before using the comparison.",
                )
            )
        if (
            item.agreement_level is SourceAgreementLevel.CONFLICT
            and item.metric_id in policy.critical.near_term_conflict_metrics
            and nearest_period is not None
            and item.period_end == nearest_period
        ):
            critical.append(
                DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric=item.metric_id,
                    provider="coverage_policy",
                    reason="Material near-term forward source conflict affects a critical consensus input.",
                    action="Review the canonical and validator evidence before relying on near-term consensus.",
                )
            )
        elif item.agreement_level is SourceAgreementLevel.CONFLICT:
            warnings.append(
                DataIssue(
                    severity=IssueSeverity.WARNING,
                    metric=item.metric_id,
                    provider="coverage_policy",
                    reason="A noncritical or distant source conflict is present.",
                )
            )
        elif item.status in {
            ReconciliationStatus.NO_VALIDATOR,
            ReconciliationStatus.VALIDATOR_UNAVAILABLE,
        }:
            warnings.append(
                DataIssue(
                    severity=IssueSeverity.WARNING,
                    metric=item.metric_id,
                    provider="coverage_policy",
                    reason="Optional validator evidence is unavailable; no conflict is inferred.",
                )
            )
    mismatched_consensus = tuple(
        item
        for item in forward
        if item.currency is not None and item.currency != inputs.identity.reporting_currency
    )
    if mismatched_consensus:
        critical.append(
            DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric="forward_consensus_currency",
                provider="coverage_policy",
                reason="Forward monetary consensus currency does not match reporting currency.",
                expected=inputs.identity.reporting_currency,
                observed=",".join(sorted({item.currency or "unknown" for item in mismatched_consensus})),
                action="Resolve currency semantics without implicit conversion or fallback.",
            )
        )
    return tuple(critical), tuple(warnings)


def _overall_and_confidence(
    dimensions: tuple[CoverageDimensionResult, ...],
    critical: tuple[DataIssue, ...],
) -> tuple[CoverageLevel, EvidenceConfidence, tuple[str, ...]]:
    by_dimension = _dimension_map(dimensions)
    market = by_dimension[CoverageDimension.MARKET_DATA]
    history = by_dimension[CoverageDimension.HISTORICAL_FINANCIALS]
    forward = by_dimension[CoverageDimension.FORWARD_CONSENSUS]
    analyst = by_dimension[CoverageDimension.ANALYST_COVERAGE]
    agreement = by_dimension[CoverageDimension.SOURCE_AGREEMENT]
    history_complete = _named_count(history, "complete_annual_periods")
    forward_complete = _named_count(forward, "core_complete_periods")
    nearest_band = (
        analyst.analyst_horizons[0].overall_band
        if analyst.analyst_horizons
        else AnalystCoverageBand.UNKNOWN
    )
    critical_reasons = " ".join(issue.reason.lower() for issue in critical)
    structurally_unusable = (
        market.status is DimensionStatus.FAIL
        or forward.status is DimensionStatus.NOT_AVAILABLE
        or "identity semantics" in critical_reasons
        or "currency does not match" in critical_reasons
        or any(issue.severity is IssueSeverity.BLOCKING and issue.provider != "coverage_policy" for issue in critical)
    )
    near_term_conflict = "near-term forward source conflict" in critical_reasons
    if structurally_unusable:
        overall = CoverageLevel.INSUFFICIENT
        overall_reason = "A critical market, forward, identity, currency, or supplied blocking issue makes the snapshot semantically unusable."
    elif (
        market.status is DimensionStatus.PASS
        and history.status is DimensionStatus.PASS
        and forward.status is DimensionStatus.PASS
        and analyst.status is DimensionStatus.PASS
        and nearest_band is AnalystCoverageBand.STRONG
        and agreement.status is DimensionStatus.PASS
        and not critical
    ):
        overall = CoverageLevel.HIGH
        overall_reason = "Market, complete history, forward horizon, near-term analyst depth, and independent agreement all satisfy strong rules."
    elif history_complete >= 3 and forward_complete >= 2 and not near_term_conflict:
        overall = CoverageLevel.MEDIUM
        overall_reason = "Usable core history and forward evidence exist, with at least one important dimension below the strong rule."
    elif market.status in {DimensionStatus.PASS, DimensionStatus.PARTIAL} and _named_count(
        forward, "future_annual_periods"
    ):
        overall = CoverageLevel.LIMITED
        overall_reason = "Some market and forward evidence exists, but horizon, completeness, or a critical near-term conflict limits coverage."
    else:
        overall = CoverageLevel.INSUFFICIENT
        overall_reason = "Too little usable market and forward evidence exists for meaningful forward-data coverage."
    conflicts = _named_count(agreement, "conflicts")
    known_analyst = _named_count(analyst, "horizons_with_known_counts")
    compared = sum(
        _named_count(agreement, name)
        for name in ("confirmed", "warnings", "conflicts", "comparable_unscored")
    )
    if critical or conflicts:
        confidence = EvidenceConfidence.LOW
        confidence_reason = "Critical issues or material source conflicts reduce evidence reliability."
    elif (
        agreement.status is DimensionStatus.PASS
        and nearest_band is AnalystCoverageBand.STRONG
        and history.status is DimensionStatus.PASS
        and forward.status is DimensionStatus.PASS
    ):
        confidence = EvidenceConfidence.HIGH
        confidence_reason = "Independent agreement, strong analyst depth, and complete core semantics support high evidence confidence."
    elif compared or known_analyst:
        confidence = EvidenceConfidence.MEDIUM
        confidence_reason = "Some reliability evidence exists, but agreement, analyst depth, or semantic completeness is partial."
    else:
        confidence = EvidenceConfidence.UNKNOWN
        confidence_reason = "No independent agreement or sourced analyst-count evidence is available to classify reliability."
    return overall, confidence, (overall_reason, confidence_reason)


def assess_coverage(
    inputs: CoverageInputs,
    *,
    analysis_as_of: datetime,
    policy: CoveragePolicy = DEFAULT_COVERAGE_POLICY,
) -> CoverageAssessment:
    """Classify evidence breadth, reliability, and future-family readiness.

    All inputs are immutable canonical contracts.  Evidence after ``analysis_as_of``
    is excluded, and no provider or system clock is consulted.
    """
    if not isinstance(analysis_as_of, datetime) or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    canonical = _eligible_observations(inputs.canonical_observations, analysis_as_of)
    cash_observations = _eligible_observations(inputs.cash_flow_observations, analysis_as_of)
    market = _evaluate_market(canonical, analysis_as_of)
    history = _evaluate_history(canonical, analysis_as_of, policy.historical)
    forward_result, forward = _evaluate_forward(canonical, analysis_as_of, policy.forward)
    analysts = _evaluate_analysts(forward, analysis_as_of, policy.analyst)
    agreement = _evaluate_agreement(inputs.reconciliations, inputs.capabilities, analysis_as_of)
    cash = _evaluate_cash_flow(cash_observations, inputs.cash_flow_definitions, analysis_as_of)
    macro = _evaluate_macro(inputs.identity, inputs.risk_free_rate, analysis_as_of, policy)
    dimensions = (
        market,
        history,
        *_not_evaluated_dimensions(analysis_as_of)[:1],
        forward_result,
        analysts,
        agreement,
        cash,
        macro,
        *_not_evaluated_dimensions(analysis_as_of)[1:],
    )
    critical, warnings = _critical_and_warnings(
        inputs, dimensions, forward, analysis_as_of, policy
    )
    overall, confidence, reasons = _overall_and_confidence(dimensions, critical)
    readiness = _readiness(dimensions, analysis_as_of)
    supporting_observations = tuple(
        dict.fromkeys(
            observation_id
            for dimension in dimensions
            for observation_id in dimension.supporting_observation_ids
        )
    )
    supporting_reconciliations = tuple(
        dict.fromkeys(
            reconciliation_id
            for dimension in dimensions
            for reconciliation_id in dimension.supporting_reconciliation_ids
        )
    )
    return CoverageAssessment(
        analysis_as_of=analysis_as_of,
        policy_id=policy.policy_id,
        overall_coverage=overall,
        evidence_confidence=confidence,
        dimension_results=dimensions,
        valuation_readiness_results=readiness,
        critical_issues=critical,
        warnings=warnings,
        supporting_observation_ids=supporting_observations,
        supporting_reconciliation_ids=supporting_reconciliations,
        reasons=reasons,
    )
