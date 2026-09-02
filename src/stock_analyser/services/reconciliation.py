"""Deterministic canonical-versus-validator reconciliation over domain records only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Iterable

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    DataIssue,
    DefinitionVerificationStatus,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricObservation,
    ObservationType,
    ReconciliationDatasetType,
    ReconciliationResult,
    ReconciliationStatus,
    ReconciliationStream,
    SourceAgreementLevel,
)


@dataclass(frozen=True, slots=True)
class ToleranceBand:
    policy_id: str
    confirmed_below: float
    warning_at_or_below: float

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        for name in ("confirmed_below", "warning_at_or_below"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.confirmed_below > self.warning_at_or_below:
            raise ValueError("confirmed_below must not exceed warning_at_or_below")

    def classify(self, relative_difference: float) -> SourceAgreementLevel:
        if relative_difference < self.confirmed_below:
            return SourceAgreementLevel.CONFIRMED
        if relative_difference <= self.warning_at_or_below:
            return SourceAgreementLevel.WARNING
        return SourceAgreementLevel.CONFLICT


@dataclass(frozen=True, slots=True)
class CanonicalSourceRule:
    stream: ReconciliationStream
    canonical_provider: str | None
    validator_providers: tuple[str, ...]
    canonical_dataset_type: ReconciliationDatasetType
    validator_dataset_types: tuple[ReconciliationDatasetType, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.canonical_provider is not None:
            canonical_provider = self.canonical_provider.strip().lower()
            if not canonical_provider:
                raise ValueError("canonical_provider must be non-empty when configured")
            object.__setattr__(self, "canonical_provider", canonical_provider)
        validators = tuple(value.strip().lower() for value in self.validator_providers)
        if any(not value for value in validators) or len(set(validators)) != len(validators):
            raise ValueError("validator_providers must be non-empty and unique")
        object.__setattr__(self, "validator_providers", validators)
        if not self.reason.strip():
            raise ValueError("canonical source rule requires a reason")


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    policy_id: str
    current_price: ToleranceBand
    reported_revenue: ToleranceBand
    forward_revenue: ToleranceBand
    forward_eps: ToleranceBand
    source_rules: tuple[CanonicalSourceRule, ...]
    maximum_as_of_gap: timedelta | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        rules = tuple(self.source_rules)
        if len({rule.stream for rule in rules}) != len(rules):
            raise ValueError("source_rules must contain at most one rule per stream")
        object.__setattr__(self, "source_rules", rules)
        if self.maximum_as_of_gap is not None and self.maximum_as_of_gap <= timedelta(0):
            raise ValueError("maximum_as_of_gap must be positive when configured")

    def source_rule(self, stream: ReconciliationStream) -> CanonicalSourceRule:
        rule = next((item for item in self.source_rules if item.stream is stream), None)
        if rule is None:
            raise ValueError(f"no canonical source rule configured for {stream.value}")
        return rule

    def tolerance_for(self, observation: MetricObservation) -> ToleranceBand | None:
        if observation.metric_id is MetricId.SHARE_PRICE:
            return self.current_price
        if observation.metric_id is MetricId.REVENUE:
            if observation.observation_type is ObservationType.ACTUAL:
                return self.reported_revenue
            if observation.observation_type is ObservationType.ESTIMATE:
                return self.forward_revenue
        if observation.metric_id is MetricId.EPS and observation.observation_type is ObservationType.ESTIMATE:
            return self.forward_eps
        return None


DEFAULT_RECONCILIATION_POLICY = ReconciliationPolicy(
    policy_id="source-agreement-v1",
    current_price=ToleranceBand("current-price-v1", 0.01, 0.03),
    reported_revenue=ToleranceBand("reported-revenue-v1", 0.01, 0.03),
    forward_revenue=ToleranceBand("forward-revenue-v1", 0.03, 0.10),
    forward_eps=ToleranceBand("forward-eps-v1", 0.05, 0.15),
    source_rules=(
        CanonicalSourceRule(
            stream=ReconciliationStream.CURRENT_MARKET_PRICE,
            canonical_provider="yahoo",
            validator_providers=("fmp", "finnhub"),
            canonical_dataset_type=ReconciliationDatasetType.MARKET,
            validator_dataset_types=(ReconciliationDatasetType.MARKET,),
            reason="Yahoo is the approved canonical current-market-price source.",
        ),
        CanonicalSourceRule(
            stream=ReconciliationStream.HISTORICAL_STANDARDIZED_FINANCIALS,
            canonical_provider="fiscal",
            validator_providers=("sec",),
            canonical_dataset_type=ReconciliationDatasetType.STANDARDIZED_ACTUAL,
            validator_dataset_types=(ReconciliationDatasetType.REPORTED_ACTUAL,),
            reason="Fiscal standardized actuals are canonical; comparable SEC reported actuals are validators.",
        ),
        CanonicalSourceRule(
            stream=ReconciliationStream.FORWARD_OPERATING_CONSENSUS,
            canonical_provider="fmp",
            validator_providers=("finnhub", "alpha_vantage"),
            canonical_dataset_type=ReconciliationDatasetType.FORWARD_CONSENSUS,
            validator_dataset_types=(ReconciliationDatasetType.VALIDATOR_ESTIMATE,),
            reason="FMP is the approved canonical annual forward operating-consensus source.",
        ),
        CanonicalSourceRule(
            stream=ReconciliationStream.FORWARD_CASH_FLOW,
            canonical_provider=None,
            validator_providers=("finnhub",),
            canonical_dataset_type=ReconciliationDatasetType.CASH_FLOW_ESTIMATE,
            validator_dataset_types=(ReconciliationDatasetType.CASH_FLOW_ESTIMATE,),
            reason="No canonical forward FCFF or FCFE source is approved yet.",
        ),
    ),
    # No default staleness window is invented. Callers may configure one explicitly.
    maximum_as_of_gap=None,
)


@dataclass(frozen=True, slots=True)
class ObservationSource:
    identity: CompanyIdentity
    provider: str
    dataset_type: ReconciliationDatasetType
    observations: tuple[MetricObservation, ...] = ()
    capability: CapabilityResult | None = None
    cash_flow_definitions: tuple[CashFlowDefinitionEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string")
        provider = self.provider.strip().lower()
        object.__setattr__(self, "provider", provider)
        observations = tuple(self.observations)
        if any(item.provenance.provider.lower() != provider for item in observations):
            raise ValueError("all observations must belong to the declared provider")
        object.__setattr__(self, "observations", observations)
        if self.capability is not None and self.capability.provider.lower() != provider:
            raise ValueError("capability provider must match the declared provider")
        definitions = tuple(self.cash_flow_definitions)
        if any(item.provider.lower() != provider for item in definitions):
            raise ValueError("cash-flow evidence provider must match the declared provider")
        object.__setattr__(self, "cash_flow_definitions", definitions)


@dataclass(frozen=True, slots=True)
class ReconciliationAudit:
    canonical_observation: MetricObservation
    canonical_security_id: str
    canonical_issuer_id: str
    canonical_reason: str
    stream: ReconciliationStream
    comparison_as_of: datetime
    comparisons: tuple[ReconciliationResult, ...]

    def __post_init__(self) -> None:
        if self.comparison_as_of.tzinfo is None or self.comparison_as_of.utcoffset() is None:
            raise ValueError("comparison_as_of must be timezone-aware")
        comparisons = tuple(self.comparisons)
        if any(item.canonical_observation_id != self.canonical_observation.observation_id for item in comparisons):
            raise ValueError("all audit comparisons must reference the selected canonical observation")
        if any(item.comparison_as_of != self.comparison_as_of for item in comparisons):
            raise ValueError("all audit comparisons must use the audit comparison_as_of")
        object.__setattr__(self, "comparisons", comparisons)

    @property
    def canonical_value(self) -> float:
        return self.canonical_observation.value

    @property
    def validator_count(self) -> int:
        return sum(item.validator_observation_id is not None for item in self.comparisons)

    @property
    def confirmed_count(self) -> int:
        return sum(item.agreement_level is SourceAgreementLevel.CONFIRMED for item in self.comparisons)

    @property
    def warning_count(self) -> int:
        return sum(item.agreement_level is SourceAgreementLevel.WARNING for item in self.comparisons)

    @property
    def conflict_count(self) -> int:
        return sum(item.agreement_level is SourceAgreementLevel.CONFLICT for item in self.comparisons)

    @property
    def issues(self) -> tuple[DataIssue, ...]:
        return tuple(issue for comparison in self.comparisons for issue in comparison.issues)


def _semantic_key(observation: MetricObservation) -> tuple[object, ...]:
    return (
        observation.metric_id, observation.frequency, observation.observation_type,
        observation.estimate_case, observation.currency, observation.unit,
        observation.period_start, observation.period_end, observation.fiscal_year,
        observation.fiscal_quarter,
    )


def _latest_eligible(observations: Iterable[MetricObservation], comparison_as_of: datetime) -> MetricObservation | None:
    eligible = tuple(item for item in observations if item.as_of_at <= comparison_as_of)
    return max(eligible, key=lambda item: (item.as_of_at, item.observation_id), default=None)


def _selected_canonical(source: ObservationSource, comparison_as_of: datetime) -> MetricObservation:
    if not source.observations:
        raise ValueError("canonical source requires at least one observation")
    keys = {_semantic_key(item) for item in source.observations}
    if len(keys) != 1:
        raise ValueError("canonical source observations must describe one semantic observation stream")
    selected = _latest_eligible(source.observations, comparison_as_of)
    if selected is None:
        raise ValueError("no canonical observation exists on or before comparison_as_of")
    return selected


def _verified_definition(source: ObservationSource, observation: MetricObservation) -> CashFlowDefinition | None:
    evidence = next(
        (
            item for item in source.cash_flow_definitions
            if observation.observation_id in item.observation_ids
            and item.verification_status is DefinitionVerificationStatus.VERIFIED
        ),
        None,
    )
    return evidence.definition if evidence is not None else None


def _economic_metric(source: ObservationSource, observation: MetricObservation) -> MetricId:
    if observation.metric_id is not MetricId.PROVIDER_DEFINED_FCF:
        return observation.metric_id
    definition = _verified_definition(source, observation)
    return {
        CashFlowDefinition.FCFF: MetricId.FCFF,
        CashFlowDefinition.FCFE: MetricId.FCFE,
        CashFlowDefinition.OCF_LESS_CAPEX: MetricId.OCF_LESS_CAPEX,
    }.get(definition, MetricId.PROVIDER_DEFINED_FCF)


def _semantic_mismatches(
    canonical_source: ObservationSource,
    canonical: MetricObservation,
    validator_source: ObservationSource,
    validator: MetricObservation,
) -> tuple[str, ...]:
    mismatches = []
    if canonical_source.identity.security_id != validator_source.identity.security_id:
        mismatches.append("security_id")
    if canonical_source.identity.issuer_id != validator_source.identity.issuer_id:
        mismatches.append("issuer_id")
    if _economic_metric(canonical_source, canonical) is not _economic_metric(validator_source, validator):
        mismatches.append("metric")
    else:
        provider_defined_items = (
            (canonical_source, canonical), (validator_source, validator),
        )
        if any(
            item.metric_id is MetricId.PROVIDER_DEFINED_FCF
            and _verified_definition(source, item) is None
            for source, item in provider_defined_items
        ):
            mismatches.append("unverified_cash_flow_definition")
    for name in (
        "observation_type", "frequency", "period_start", "period_end", "fiscal_year",
        "fiscal_quarter", "estimate_case", "currency", "unit",
    ):
        if getattr(canonical, name) != getattr(validator, name):
            mismatches.append(name)
    return tuple(mismatches)


def _policy_mismatches(
    canonical_source: ObservationSource,
    validator_source: ObservationSource,
    rule: CanonicalSourceRule,
    canonical: MetricObservation,
) -> tuple[str, ...]:
    mismatches = []
    if rule.canonical_provider is None:
        mismatches.append("canonical_provider_not_established")
    elif canonical_source.provider != rule.canonical_provider:
        mismatches.append("canonical_provider_policy")
    if validator_source.provider not in rule.validator_providers:
        mismatches.append("validator_provider_policy")
    if canonical_source.dataset_type is not rule.canonical_dataset_type:
        mismatches.append("canonical_dataset_type")
    if validator_source.dataset_type not in rule.validator_dataset_types:
        mismatches.append("validator_dataset_type")
    if rule.stream is ReconciliationStream.CURRENT_MARKET_PRICE and not (
        canonical.metric_id is MetricId.SHARE_PRICE
        and canonical.frequency is Frequency.POINT_IN_TIME
        and canonical.observation_type is ObservationType.ACTUAL
    ):
        mismatches.append("canonical_stream_semantics")
    elif rule.stream is ReconciliationStream.HISTORICAL_STANDARDIZED_FINANCIALS and (
        canonical.observation_type is not ObservationType.ACTUAL
    ):
        mismatches.append("canonical_stream_semantics")
    elif rule.stream is ReconciliationStream.FORWARD_OPERATING_CONSENSUS and not (
        canonical.frequency is Frequency.ANNUAL
        and canonical.observation_type is ObservationType.ESTIMATE
        and canonical.metric_id in {
            MetricId.REVENUE, MetricId.EBIT, MetricId.EBITDA, MetricId.NET_INCOME, MetricId.EPS,
        }
    ):
        mismatches.append("canonical_stream_semantics")
    if (
        rule.stream is ReconciliationStream.FORWARD_OPERATING_CONSENSUS
        and validator_source.provider == "alpha_vantage"
        and canonical.metric_id not in {MetricId.REVENUE, MetricId.EPS}
    ):
        mismatches.append("validator_metric_policy")
    return tuple(mismatches)


def _base_result(
    canonical_source: ObservationSource,
    canonical: MetricObservation,
    validator_source: ObservationSource,
    validator: MetricObservation | None,
    *,
    stream: ReconciliationStream,
    comparison_as_of: datetime,
    canonical_reason: str,
    agreement_level: SourceAgreementLevel,
    status: ReconciliationStatus,
    absolute_difference: float | None = None,
    relative_difference: float | None = None,
    tolerance: ToleranceBand | None = None,
    tolerance_policy_id: str | None = None,
    semantic_mismatches: tuple[str, ...] = (),
    issues: tuple[DataIssue, ...] = (),
) -> ReconciliationResult:
    capability_status = validator_source.capability.status if validator_source.capability is not None else None
    capability_reason = None
    if capability_status is not None and capability_status is not CapabilityStatus.AVAILABLE:
        capability_reason = f"validator capability is {capability_status.value}"
    return ReconciliationResult(
        canonical_observation_id=canonical.observation_id,
        validator_observation_id=validator.observation_id if validator is not None else None,
        canonical_security_id=canonical_source.identity.security_id,
        validator_security_id=validator_source.identity.security_id,
        canonical_issuer_id=canonical_source.identity.issuer_id,
        validator_issuer_id=validator_source.identity.issuer_id,
        canonical_provider=canonical_source.provider,
        validator_provider=validator_source.provider,
        canonical_provider_symbol=canonical.provenance.provider_symbol,
        validator_provider_symbol=validator.provenance.provider_symbol if validator is not None else None,
        canonical_dataset_type=canonical_source.dataset_type,
        validator_dataset_type=validator_source.dataset_type,
        stream=stream,
        metric_id=canonical.metric_id,
        frequency=canonical.frequency,
        observation_type=canonical.observation_type,
        estimate_case=canonical.estimate_case,
        unit=canonical.unit,
        currency=canonical.currency,
        period_start=canonical.period_start,
        period_end=canonical.period_end,
        fiscal_year=canonical.fiscal_year,
        fiscal_quarter=canonical.fiscal_quarter,
        comparison_as_of=comparison_as_of,
        canonical_as_of_at=canonical.as_of_at,
        canonical_retrieved_at=canonical.retrieved_at,
        canonical_value=canonical.value,
        validator_as_of_at=validator.as_of_at if validator is not None else None,
        validator_retrieved_at=validator.retrieved_at if validator is not None else None,
        validator_value=validator.value if validator is not None else None,
        absolute_difference=absolute_difference,
        relative_difference=relative_difference,
        agreement_level=agreement_level,
        status=status,
        canonical_reason=canonical_reason,
        tolerance_policy_id=tolerance.policy_id if tolerance is not None else tolerance_policy_id,
        confirmed_below=tolerance.confirmed_below if tolerance is not None else None,
        warning_at_or_below=tolerance.warning_at_or_below if tolerance is not None else None,
        semantic_mismatches=semantic_mismatches,
        validator_capability_status=capability_status,
        validator_capability_reason=capability_reason,
        issues=issues,
    )


def reconcile_sources(
    canonical_source: ObservationSource,
    validator_source: ObservationSource,
    *,
    stream: ReconciliationStream,
    comparison_as_of: datetime,
    policy: ReconciliationPolicy = DEFAULT_RECONCILIATION_POLICY,
) -> ReconciliationResult:
    if comparison_as_of.tzinfo is None or comparison_as_of.utcoffset() is None:
        raise ValueError("comparison_as_of must be timezone-aware")
    canonical = _selected_canonical(canonical_source, comparison_as_of)
    rule = policy.source_rule(stream)
    policy_mismatches = _policy_mismatches(canonical_source, validator_source, rule, canonical)

    eligible_validators = tuple(
        item for item in validator_source.observations if item.as_of_at <= comparison_as_of
    )
    if not eligible_validators:
        unavailable = (
            validator_source.capability is not None
            and validator_source.capability.status is not CapabilityStatus.AVAILABLE
        )
        return _base_result(
            canonical_source, canonical, validator_source, None,
            stream=stream, comparison_as_of=comparison_as_of, canonical_reason=rule.reason,
            agreement_level=SourceAgreementLevel.NO_VALIDATOR,
            status=(ReconciliationStatus.VALIDATOR_UNAVAILABLE if unavailable else ReconciliationStatus.NO_VALIDATOR),
        )

    exact = tuple(
        item for item in eligible_validators
        if not _semantic_mismatches(canonical_source, canonical, validator_source, item)
    )
    validator = _latest_eligible(exact or eligible_validators, comparison_as_of)
    assert validator is not None
    semantic_mismatches = _semantic_mismatches(canonical_source, canonical, validator_source, validator)
    all_mismatches = tuple(dict.fromkeys((*policy_mismatches, *semantic_mismatches)))
    if policy.maximum_as_of_gap is not None:
        if abs(canonical.as_of_at - validator.as_of_at) > policy.maximum_as_of_gap:
            all_mismatches = (*all_mismatches, "as_of_gap_exceeds_policy")
    if all_mismatches:
        issues = ()
        if "as_of_gap_exceeds_policy" in all_mismatches:
            issues = (DataIssue(
                severity=IssueSeverity.WARNING, metric=canonical.metric_id,
                provider=validator_source.provider,
                reason="Validator snapshot is outside the configured as-of alignment window.",
                action="Do not score this comparison; obtain an aligned validator snapshot.",
            ),)
        return _base_result(
            canonical_source, canonical, validator_source, validator,
            stream=stream, comparison_as_of=comparison_as_of, canonical_reason=rule.reason,
            agreement_level=SourceAgreementLevel.NOT_COMPARABLE,
            status=ReconciliationStatus.NOT_COMPARABLE,
            semantic_mismatches=all_mismatches,
            issues=issues,
        )

    absolute_difference = abs(float(validator.value) - float(canonical.value))
    tolerance = policy.tolerance_for(canonical)
    tolerance_policy_id = None
    applied_tolerance = tolerance
    if canonical.value == 0:
        applied_tolerance = None
        if validator.value == 0:
            relative_difference = 0.0
            agreement = SourceAgreementLevel.CONFIRMED
            tolerance_policy_id = "zero-base-exact-v1"
        else:
            relative_difference = None
            agreement = SourceAgreementLevel.CONFLICT
            tolerance_policy_id = "zero-base-nonzero-v1"
    else:
        relative_difference = absolute_difference / abs(float(canonical.value))
        agreement = (
            tolerance.classify(relative_difference)
            if tolerance is not None
            else SourceAgreementLevel.COMPARABLE_UNSCORED
        )

    issues = ()
    if agreement in {SourceAgreementLevel.WARNING, SourceAgreementLevel.CONFLICT}:
        severity = IssueSeverity.WARNING if agreement is SourceAgreementLevel.WARNING else IssueSeverity.ERROR
        observed = (
            f"relative_difference={relative_difference:.6f}"
            if relative_difference is not None
            else f"canonical_zero_validator_value={validator.value}"
        )
        period_label = canonical.period_end.isoformat() if canonical.period_end is not None else "unspecified-period"
        issues = (DataIssue(
            severity=severity,
            metric=canonical.metric_id,
            provider=validator_source.provider,
            reason=(
                f"{validator_source.provider} validator is {agreement.value} against "
                f"{canonical_source.provider} canonical data for aligned {canonical.metric_id.value} "
                f"ending {period_label}."
            ),
            expected=(tolerance.policy_id if tolerance is not None else tolerance_policy_id),
            observed=observed,
            action="Preserve the canonical observation and retain this reconciliation record for later policy use.",
        ),)
    return _base_result(
        canonical_source, canonical, validator_source, validator,
        stream=stream, comparison_as_of=comparison_as_of, canonical_reason=rule.reason,
        agreement_level=agreement, status=ReconciliationStatus.COMPARED,
        absolute_difference=absolute_difference, relative_difference=relative_difference,
        tolerance=applied_tolerance, tolerance_policy_id=tolerance_policy_id, issues=issues,
    )


def reconcile_many(
    canonical_source: ObservationSource,
    validator_sources: Iterable[ObservationSource],
    *,
    stream: ReconciliationStream,
    comparison_as_of: datetime,
    policy: ReconciliationPolicy = DEFAULT_RECONCILIATION_POLICY,
) -> ReconciliationAudit:
    if comparison_as_of.tzinfo is None or comparison_as_of.utcoffset() is None:
        raise ValueError("comparison_as_of must be timezone-aware")
    canonical = _selected_canonical(canonical_source, comparison_as_of)
    rule = policy.source_rule(stream)
    comparisons = tuple(
        reconcile_sources(
            canonical_source, validator_source, stream=stream,
            comparison_as_of=comparison_as_of, policy=policy,
        )
        for validator_source in validator_sources
    )
    return ReconciliationAudit(
        canonical_observation=canonical,
        canonical_security_id=canonical_source.identity.security_id,
        canonical_issuer_id=canonical_source.identity.issuer_id,
        canonical_reason=rule.reason,
        stream=stream,
        comparison_as_of=comparison_as_of,
        comparisons=comparisons,
    )
