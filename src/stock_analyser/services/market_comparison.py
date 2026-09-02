"""Pure post-publication market-price normalization and quantitative comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from stock_analyser.domain.enums import (
    AggregationStatus,
    DataAvailability,
    Frequency,
    IssueSeverity,
    MarketComparisonScope,
    MarketPriceSemantic,
    MetricId,
    MetricUnit,
    ObservationType,
    ReverseDcfSolverStatus,
    ValuationMethodStatus,
)
from stock_analyser.domain.market_comparison import (
    ExpectationGapEvidence,
    MarketComparisonResult,
    MarketPriceEvidence,
    ValuationPriceComparison,
    stable_expectation_gap_evidence_id,
    stable_market_comparison_result_id,
    stable_market_price_evidence_id,
    stable_valuation_price_comparison_id,
)
from stock_analyser.domain.models import DataIssue, MetricObservation, Provenance, _require_text, _validate_aware_datetime
from stock_analyser.domain.publication import FamilyValuationEvidence, ValuationPublicationResult
from stock_analyser.domain.reverse_dcf import ReverseDcfExecutionResult


@dataclass(frozen=True, slots=True)
class MarketPricePolicy:
    policy_id: str = "v1-11b-yahoo-regular-market-price-3-calendar-day"
    maximum_age_calendar_days: int = 3
    price_semantic: MarketPriceSemantic = MarketPriceSemantic.YAHOO_REGULAR_MARKET_PRICE

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        if self.maximum_age_calendar_days != 3:
            raise ValueError("Milestone 11B uses one three-calendar-day market-price freshness policy")
        if self.price_semantic is not MarketPriceSemantic.YAHOO_REGULAR_MARKET_PRICE:
            raise ValueError("Milestone 11B supports Yahoo regular market price only")


@dataclass(frozen=True, slots=True)
class MarketComparisonPolicy:
    policy_id: str = "v1-11b-post-publication-price-comparison"

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))


DEFAULT_MARKET_PRICE_POLICY = MarketPricePolicy()
DEFAULT_MARKET_COMPARISON_POLICY = MarketComparisonPolicy()


def _issue(reason: str, *, action: str | None = None) -> DataIssue:
    return DataIssue(
        severity=IssueSeverity.BLOCKING,
        metric=MetricId.SHARE_PRICE,
        provider="market_comparison",
        reason=reason,
        action=action,
    )


def _price_evidence(
    *,
    target_security_id: str,
    target_issuer_id: str,
    analysis_as_of: datetime,
    observation: MetricObservation | None,
    raw_quote_value: float | None,
    quote_currency: str | None,
    quote_unit: str | None,
    quote_unit_scale: float | None,
    status: DataAvailability,
    issues: tuple[DataIssue, ...],
    policy: MarketPricePolicy,
    normalized_price: float | None = None,
) -> MarketPriceEvidence:
    source_id = observation.observation_id if observation is not None else None
    timestamp = observation.as_of_at if observation is not None else None
    provenance = (observation.provenance,) if observation is not None else ()
    evidence_id = stable_market_price_evidence_id(
        target_security_id, target_issuer_id, analysis_as_of.isoformat(),
        source_id, raw_quote_value, quote_currency, quote_unit, quote_unit_scale,
        status.value, policy.policy_id,
    )
    return MarketPriceEvidence(
        evidence_id=evidence_id,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        price_semantic=policy.price_semantic,
        observation_timestamp=timestamp,
        raw_quote_value=raw_quote_value,
        quote_currency=quote_currency,
        quote_unit=quote_unit,
        quote_unit_scale=quote_unit_scale,
        normalized_price_per_share=normalized_price,
        normalized_currency=quote_currency if normalized_price is not None else None,
        normalized_per_share_unit=f"{quote_currency}/share" if normalized_price is not None else None,
        status=status,
        issues=issues,
        warnings=(),
        source_observation_id=source_id,
        policy_id=policy.policy_id,
        provenance=provenance,
    )


def build_market_price_evidence(
    *,
    target_security_id: str,
    target_issuer_id: str,
    observation_security_id: str,
    observation_issuer_id: str,
    analysis_as_of: datetime,
    observation: MetricObservation | None,
    quote_currency: str | None,
    quote_unit: str | None,
    quote_unit_scale: float | None,
    raw_quote_value: float | None = None,
    policy: MarketPricePolicy = DEFAULT_MARKET_PRICE_POLICY,
) -> MarketPriceEvidence:
    """Wrap one canonical Yahoo observation and verify security/unit/freshness semantics."""
    for value, name in (
        (target_security_id, "target_security_id"),
        (target_issuer_id, "target_issuer_id"),
        (observation_security_id, "observation_security_id"),
        (observation_issuer_id, "observation_issuer_id"),
    ):
        _require_text(value, name)
    _validate_aware_datetime(analysis_as_of, "analysis_as_of")
    if not isinstance(policy, MarketPricePolicy):
        raise TypeError("policy must be MarketPricePolicy")
    if observation is None:
        return _price_evidence(
            target_security_id=target_security_id,
            target_issuer_id=target_issuer_id,
            analysis_as_of=analysis_as_of,
            observation=None,
            raw_quote_value=raw_quote_value,
            quote_currency=quote_currency,
            quote_unit=quote_unit,
            quote_unit_scale=quote_unit_scale,
            status=DataAvailability.UNAVAILABLE,
            issues=(_issue("Yahoo regular market price observation is unavailable"),),
            policy=policy,
        )

    problems = []
    if target_security_id != observation_security_id:
        problems.append("market-price security identity does not match the valued security")
    if target_issuer_id != observation_issuer_id:
        problems.append("market-price issuer identity does not match the valued issuer")
    if (
        observation.metric_id is not MetricId.SHARE_PRICE
        or observation.unit is not MetricUnit.CURRENCY_PER_SHARE
        or observation.frequency is not Frequency.POINT_IN_TIME
        or observation.observation_type is not ObservationType.ACTUAL
        or observation.provenance.provider.lower() != "yahoo"
    ):
        problems.append("observation is not canonical Yahoo market-price evidence")
    if observation.as_of_at > analysis_as_of:
        problems.append("market-price observation is after the analysis snapshot")
    elif (analysis_as_of.date() - observation.as_of_at.date()).days > policy.maximum_age_calendar_days:
        problems.append("market-price observation exceeds the three-calendar-day freshness window")
    if observation.currency != quote_currency:
        problems.append("canonical observation currency does not match verified quote currency")
    if isinstance(observation.value, bool) or not math.isfinite(float(observation.value)) or observation.value <= 0:
        problems.append("canonical market price must be finite and positive")

    raw = raw_quote_value
    if raw is not None and (isinstance(raw, bool) or not math.isfinite(float(raw)) or raw <= 0):
        problems.append("raw Yahoo quote must be finite and positive")
        raw = None
    missing_metadata = quote_currency is None or quote_unit is None or quote_unit_scale is None
    if missing_metadata:
        return _price_evidence(
            target_security_id=target_security_id,
            target_issuer_id=target_issuer_id,
            analysis_as_of=analysis_as_of,
            observation=observation,
            raw_quote_value=raw,
            quote_currency=quote_currency,
            quote_unit=quote_unit,
            quote_unit_scale=quote_unit_scale,
            status=DataAvailability.PARTIAL,
            issues=tuple(_issue(reason) for reason in (
                *problems,
                "verified quote currency, quote unit, and quote scale are required",
            )),
            policy=policy,
        )

    if (
        (quote_unit == quote_currency and not math.isclose(quote_unit_scale, 1.0))
        or (quote_unit in {"GBp", "GBX"} and (
            quote_currency != "GBP" or not math.isclose(quote_unit_scale, 0.01)
        ))
        or (quote_unit != quote_currency and quote_unit not in {"GBp", "GBX"})
    ):
        problems.append("quote-unit and scale semantics are unsupported or inconsistent")
    if raw is None and quote_unit_scale > 0:
        raw = observation.value / quote_unit_scale
    normalized = raw * quote_unit_scale if raw is not None else None
    if normalized is None or not math.isclose(normalized, observation.value):
        problems.append("raw quote and verified scale do not reconstruct the canonical normalized price")
    if problems:
        return _price_evidence(
            target_security_id=target_security_id,
            target_issuer_id=target_issuer_id,
            analysis_as_of=analysis_as_of,
            observation=observation,
            raw_quote_value=raw,
            quote_currency=quote_currency,
            quote_unit=quote_unit,
            quote_unit_scale=quote_unit_scale,
            status=DataAvailability.UNAVAILABLE,
            issues=tuple(_issue(reason) for reason in dict.fromkeys(problems)),
            policy=policy,
        )
    return _price_evidence(
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        observation=observation,
        raw_quote_value=raw,
        quote_currency=quote_currency,
        quote_unit=quote_unit,
        quote_unit_scale=quote_unit_scale,
        status=DataAvailability.AVAILABLE,
        issues=(),
        policy=policy,
        normalized_price=normalized,
    )


def _comparison(
    *,
    target_security_id: str,
    target_issuer_id: str,
    analysis_as_of: datetime,
    price: MarketPriceEvidence,
    scope: MarketComparisonScope,
    source_id: str,
    family,
    publication_status: AggregationStatus | None,
    currency: str | None,
    unit: str | None,
    lower: float | None,
    central: float | None,
    upper: float | None,
    status: DataAvailability,
    issues: tuple[str, ...],
    supporting_ids: tuple[str, ...],
    provenance: tuple[Provenance, ...],
    policy: MarketComparisonPolicy,
) -> ValuationPriceComparison:
    market_price = price.normalized_price_per_share
    available = status is DataAvailability.AVAILABLE
    gaps = tuple(value - market_price for value in (lower, central, upper)) if available else (None, None, None)
    percentages = tuple(value / market_price - 1 for value in (lower, central, upper)) if available else (None, None, None)
    comparison_id = stable_valuation_price_comparison_id(
        target_security_id, target_issuer_id, analysis_as_of.isoformat(),
        price.evidence_id, scope.value, source_id, status.value, policy.policy_id,
    )
    return ValuationPriceComparison(
        comparison_id=comparison_id,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        market_price_evidence_id=price.evidence_id,
        comparison_scope=scope,
        valuation_source_id=source_id,
        valuation_family=family,
        publication_status=publication_status,
        valuation_currency=currency,
        per_share_unit=unit,
        market_price=market_price,
        lower_value=lower,
        central_value=central,
        upper_value=upper,
        lower_gap=gaps[0],
        central_gap=gaps[1],
        upper_gap=gaps[2],
        lower_gap_percent=percentages[0],
        central_gap_percent=percentages[1],
        upper_gap_percent=percentages[2],
        status=status,
        issues=issues,
        warnings=(),
        supporting_ids=tuple(dict.fromkeys(supporting_ids)),
        policy_id=policy.policy_id,
        provenance=tuple(dict.fromkeys(provenance)),
    )


def _alignment_issues(
    *,
    price: MarketPriceEvidence,
    target_security_id: str,
    target_issuer_id: str,
    analysis_as_of: datetime,
    currency: str | None,
    unit: str | None,
) -> tuple[str, ...]:
    issues = []
    if price.status is not DataAvailability.AVAILABLE:
        issues.append("normalized market price is unavailable")
    if price.target_security_id != target_security_id:
        issues.append("market-price security identity does not match valuation")
    if price.target_issuer_id != target_issuer_id:
        issues.append("market-price issuer identity does not match valuation")
    if price.analysis_as_of != analysis_as_of:
        issues.append("market-price analysis snapshot does not match valuation")
    if currency is None or price.normalized_currency != currency:
        issues.append("valuation and normalized market-price currencies do not match; no FX is permitted")
    if unit is None or price.normalized_per_share_unit != unit:
        issues.append("valuation and normalized market-price per-share units do not match")
    return tuple(dict.fromkeys(issues))


def expectation_gap_from_reverse_dcf(result: ReverseDcfExecutionResult) -> ExpectationGapEvidence:
    """Publish reverse-DCF growth comparators without creating a price comparison."""
    if not isinstance(result, ReverseDcfExecutionResult):
        raise TypeError("result must be ReverseDcfExecutionResult")
    inputs = result.execution_inputs
    implied = result.solver_result.implied_terminal_growth
    consensus = result.final_consensus_revenue_growth
    difference = implied - consensus if implied is not None and consensus is not None else None
    status = (
        DataAvailability.AVAILABLE if result.solver_result.status is ReverseDcfSolverStatus.SOLVED
        else DataAvailability.PARTIAL if any(value is not None for value in (
            consensus, result.final_consensus_ebit_margin,
        ))
        else DataAvailability.UNAVAILABLE
    )
    evidence_id = stable_expectation_gap_evidence_id(
        inputs.target_security_id, inputs.target_issuer_id, result.execution_result_id,
        inputs.analysis_as_of.isoformat(), result.execution_mode.value,
    )
    return ExpectationGapEvidence(
        evidence_id=evidence_id,
        target_security_id=inputs.target_security_id,
        target_issuer_id=inputs.target_issuer_id,
        analysis_as_of=inputs.analysis_as_of,
        reverse_dcf_result_id=result.execution_result_id,
        execution_mode=result.execution_mode,
        publication_eligibility=result.publication_eligibility,
        central_valuation_eligible=False,
        implied_terminal_growth=implied,
        final_consensus_revenue_growth=consensus,
        final_consensus_ebit_margin=result.final_consensus_ebit_margin,
        growth_rate_difference=difference,
        scenario_assumption_ids=result.scenario_assumption_ids,
        status=status,
        issues=result.issues,
        warnings=result.warnings,
        supporting_ids=(result.execution_inputs_id, result.solver_result.result_id),
        policy_ids=result.policy_ids,
        provenance=result.provenance,
    )


def compare_publication_to_market(
    publication: ValuationPublicationResult,
    market_price: MarketPriceEvidence,
    *,
    reverse_dcf_results: tuple[ReverseDcfExecutionResult, ...] = (),
    policy: MarketComparisonPolicy = DEFAULT_MARKET_COMPARISON_POLICY,
) -> MarketComparisonResult:
    """Compare exact 11A values after publication; never feed price back into valuation."""
    if not isinstance(publication, ValuationPublicationResult):
        raise TypeError("publication must be ValuationPublicationResult")
    if not isinstance(market_price, MarketPriceEvidence):
        raise TypeError("market_price must be MarketPriceEvidence")
    if not isinstance(policy, MarketComparisonPolicy):
        raise TypeError("policy must be MarketComparisonPolicy")
    target_security_id = publication.target_security_id
    target_issuer_id = publication.target_issuer_id
    analysis_as_of = publication.analysis_as_of
    overall_issues = list(_alignment_issues(
        price=market_price,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        currency=publication.currency,
        unit=publication.per_share_unit,
    ))
    if publication.publication_status is not AggregationStatus.RESOLVED:
        overall_issues.append("overall price comparison requires a RESOLVED valuation publication")
    if publication.overall_central_value is None:
        overall_issues.append("overall published central value is unavailable")
    if publication.envelope_lower is None or publication.envelope_upper is None:
        overall_issues.append("overall published family envelope is unavailable")
    overall_available = not overall_issues
    overall = _comparison(
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        price=market_price,
        scope=MarketComparisonScope.OVERALL_RESOLVED,
        source_id=publication.publication_id,
        family=None,
        publication_status=publication.publication_status,
        currency=publication.currency,
        unit=publication.per_share_unit,
        lower=publication.envelope_lower if overall_available else None,
        central=publication.overall_central_value if overall_available else None,
        upper=publication.envelope_upper if overall_available else None,
        status=DataAvailability.AVAILABLE if overall_available else DataAvailability.UNAVAILABLE,
        issues=tuple(dict.fromkeys(overall_issues)),
        supporting_ids=(publication.publication_id, market_price.evidence_id),
        provenance=(*publication.provenance, *market_price.provenance),
        policy=policy,
    )

    eligible_ids = set(publication.eligible_family_ids)
    family_comparisons = []
    for item in publication.family_evidence:
        family_issues = list(_alignment_issues(
            price=market_price,
            target_security_id=item.target_security_id,
            target_issuer_id=item.target_issuer_id,
            analysis_as_of=item.analysis_as_of,
            currency=item.currency,
            unit=item.per_share_unit,
        ))
        complete = (
            item.valuation_family in eligible_ids
            and item.central_valuation_eligible
            and item.family_status is ValuationMethodStatus.VALID
            and all(value is not None for value in (
                item.lower_value, item.central_value, item.upper_value,
            ))
        )
        if not complete:
            family_issues.append("family result is not one complete unambiguous eligible publication contribution")
        available = not family_issues
        source_status = (
            DataAvailability.PARTIAL
            if not available and item.family_status is ValuationMethodStatus.PARTIAL
            else DataAvailability.UNAVAILABLE
        )
        family_comparisons.append(_comparison(
            target_security_id=item.target_security_id,
            target_issuer_id=item.target_issuer_id,
            analysis_as_of=item.analysis_as_of,
            price=market_price,
            scope=MarketComparisonScope.INDIVIDUAL_FAMILY,
            source_id=item.source_result_id,
            family=item.valuation_family,
            publication_status=publication.publication_status,
            currency=item.currency,
            unit=item.per_share_unit,
            lower=item.lower_value,
            central=item.central_value,
            upper=item.upper_value,
            status=DataAvailability.AVAILABLE if available else source_status,
            issues=tuple(dict.fromkeys(family_issues)),
            supporting_ids=(item.evidence_id, item.source_result_id, market_price.evidence_id, *item.supporting_ids),
            provenance=(*item.provenance, *market_price.provenance),
            policy=policy,
        ))

    expectations = []
    result_issues = []
    for item in reverse_dcf_results:
        evidence = expectation_gap_from_reverse_dcf(item)
        if (
            evidence.target_security_id != target_security_id
            or evidence.target_issuer_id != target_issuer_id
            or evidence.analysis_as_of != analysis_as_of
        ):
            result_issues.append("reverse-DCF expectation target/snapshot does not match market comparison")
            continue
        expectations.append(evidence)
    provenance = tuple(dict.fromkeys((
        *publication.provenance,
        *market_price.provenance,
        *(value for item in expectations for value in item.provenance),
    )))
    result_id = stable_market_comparison_result_id(
        target_security_id, target_issuer_id, analysis_as_of.isoformat(),
        publication.publication_id, market_price.evidence_id,
        *(item.evidence_id for item in expectations), policy.policy_id,
    )
    return MarketComparisonResult(
        result_id=result_id,
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        market_price_evidence=market_price,
        overall_comparison=overall,
        family_comparisons=tuple(family_comparisons),
        expectation_evidence=tuple(expectations),
        reference_comparisons=(),
        issues=tuple(dict.fromkeys(result_issues)),
        warnings=(),
        policy_id=policy.policy_id,
        provenance=provenance,
    )
