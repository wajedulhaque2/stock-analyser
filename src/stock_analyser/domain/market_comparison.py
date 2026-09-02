"""Provider-independent current-price, comparison, and expectation-gap contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import math

from .enums import (
    AggregationStatus,
    DataAvailability,
    MarketComparisonScope,
    MarketPriceSemantic,
    ReverseDcfExecutionMode,
    ReverseDcfPublicationEligibility,
    ValuationFamily,
)
from .models import DataIssue, Provenance, _require_text, _validate_aware_datetime, _validate_currency


def _stable_id(prefix: str, *parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def stable_market_price_evidence_id(*parts: object) -> str:
    return _stable_id("marketprice", *parts)


def stable_valuation_price_comparison_id(*parts: object) -> str:
    return _stable_id("pricecompare", *parts)


def stable_expectation_gap_evidence_id(*parts: object) -> str:
    return _stable_id("expectationgap", *parts)


def stable_market_comparison_result_id(*parts: object) -> str:
    return _stable_id("marketcomparison", *parts)


def _finite(value: float | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ValueError(f"{name} must be finite when present")


def _texts(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} values must be unique")
    return normalized


def _provenance(values: tuple[Provenance, ...]) -> tuple[Provenance, ...]:
    normalized = tuple(values)
    if any(not isinstance(value, Provenance) for value in normalized):
        raise TypeError("provenance must contain Provenance values")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class MarketPriceEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    price_semantic: MarketPriceSemantic
    observation_timestamp: datetime | None
    raw_quote_value: float | None
    quote_currency: str | None
    quote_unit: str | None
    quote_unit_scale: float | None
    normalized_price_per_share: float | None
    normalized_currency: str | None
    normalized_per_share_unit: str | None
    status: DataAvailability
    issues: tuple[DataIssue, ...]
    warnings: tuple[str, ...]
    source_observation_id: str | None
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("evidence_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if not isinstance(self.price_semantic, MarketPriceSemantic):
            raise TypeError("price_semantic must use MarketPriceSemantic")
        if not isinstance(self.status, DataAvailability):
            raise TypeError("status must use DataAvailability")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if self.observation_timestamp is not None:
            _validate_aware_datetime(self.observation_timestamp, "observation_timestamp")
            if self.status is DataAvailability.AVAILABLE and self.observation_timestamp > self.analysis_as_of:
                raise ValueError("market price observation cannot be after analysis_as_of")
        for name in ("raw_quote_value", "quote_unit_scale", "normalized_price_per_share"):
            _finite(getattr(self, name), name)
        _validate_currency(self.quote_currency, "quote_currency", required=False)
        _validate_currency(self.normalized_currency, "normalized_currency", required=False)
        if self.quote_unit is not None:
            object.__setattr__(self, "quote_unit", _require_text(self.quote_unit, "quote_unit"))
        if self.normalized_per_share_unit is not None:
            object.__setattr__(
                self, "normalized_per_share_unit",
                _require_text(self.normalized_per_share_unit, "normalized_per_share_unit"),
            )
        if self.quote_unit_scale is not None and self.quote_unit_scale <= 0:
            raise ValueError("quote_unit_scale must be positive when present")
        if self.source_observation_id is not None:
            object.__setattr__(
                self, "source_observation_id",
                _require_text(self.source_observation_id, "source_observation_id"),
            )
        if self.status is DataAvailability.AVAILABLE:
            required = (
                self.observation_timestamp, self.raw_quote_value, self.quote_currency,
                self.quote_unit, self.quote_unit_scale, self.normalized_price_per_share,
                self.normalized_currency, self.normalized_per_share_unit,
                self.source_observation_id,
            )
            if any(value is None for value in required):
                raise ValueError("available market-price evidence requires complete normalized semantics")
            if self.raw_quote_value <= 0 or self.normalized_price_per_share <= 0:
                raise ValueError("available market-price values must be positive")
            if self.normalized_currency != self.quote_currency:
                raise ValueError("quote-unit normalization cannot change currency")
            if self.normalized_per_share_unit != f"{self.normalized_currency}/share":
                raise ValueError("normalized price unit must be explicit currency/share")
            if not math.isclose(
                self.normalized_price_per_share,
                self.raw_quote_value * self.quote_unit_scale,
            ):
                raise ValueError("normalized price must equal raw quote times verified scale")
        elif self.normalized_price_per_share is not None or self.normalized_currency is not None:
            raise ValueError("non-available price evidence cannot expose a normalized price")
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        if self.status is not DataAvailability.AVAILABLE and not issues:
            raise ValueError("partial or unavailable market-price evidence requires an issue")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ValuationPriceComparison:
    comparison_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    market_price_evidence_id: str
    comparison_scope: MarketComparisonScope
    valuation_source_id: str
    valuation_family: ValuationFamily | None
    publication_status: AggregationStatus | None
    valuation_currency: str | None
    per_share_unit: str | None
    market_price: float | None
    lower_value: float | None
    central_value: float | None
    upper_value: float | None
    lower_gap: float | None
    central_gap: float | None
    upper_gap: float | None
    lower_gap_percent: float | None
    central_gap_percent: float | None
    upper_gap_percent: float | None
    status: DataAvailability
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "comparison_id", "target_security_id", "target_issuer_id",
            "market_price_evidence_id", "valuation_source_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.comparison_scope, MarketComparisonScope):
            raise TypeError("comparison_scope must use MarketComparisonScope")
        if not isinstance(self.status, DataAvailability):
            raise TypeError("status must use DataAvailability")
        if self.comparison_scope is MarketComparisonScope.OVERALL_RESOLVED:
            if self.valuation_family is not None or self.publication_status is None:
                raise ValueError("overall comparison requires publication status and no family")
        elif self.comparison_scope is MarketComparisonScope.INDIVIDUAL_FAMILY:
            if self.valuation_family not in {ValuationFamily.OWN_HISTORY, ValuationFamily.PEER}:
                raise ValueError("individual comparison requires an approved valuation family")
        _validate_currency(self.valuation_currency, "valuation_currency", required=False)
        if self.per_share_unit is not None:
            object.__setattr__(self, "per_share_unit", _require_text(self.per_share_unit, "per_share_unit"))
        numeric_names = (
            "market_price", "lower_value", "central_value", "upper_value",
            "lower_gap", "central_gap", "upper_gap", "lower_gap_percent",
            "central_gap_percent", "upper_gap_percent",
        )
        for name in numeric_names:
            _finite(getattr(self, name), name)
        values = (self.lower_value, self.central_value, self.upper_value)
        gaps = (self.lower_gap, self.central_gap, self.upper_gap)
        percents = (self.lower_gap_percent, self.central_gap_percent, self.upper_gap_percent)
        if self.status is DataAvailability.AVAILABLE:
            if any(value is None for value in (self.market_price, *values, *gaps, *percents)):
                raise ValueError("available comparison requires complete three-point arithmetic")
            if self.market_price <= 0 or any(value <= 0 for value in values):
                raise ValueError("available comparison requires positive price and valuation points")
            if not self.lower_value <= self.central_value <= self.upper_value:
                raise ValueError("comparison valuation range must remain ordered")
            for value, gap, percent in zip(values, gaps, percents):
                if not math.isclose(gap, value - self.market_price):
                    raise ValueError("absolute valuation gap arithmetic is inconsistent")
                if not math.isclose(percent, value / self.market_price - 1):
                    raise ValueError("percentage valuation gap arithmetic is inconsistent")
            if self.valuation_currency is None or self.per_share_unit != f"{self.valuation_currency}/share":
                raise ValueError("available comparison requires explicit currency/share semantics")
        elif any(value is not None for value in (*gaps, *percents)):
            raise ValueError("partial or unavailable comparison cannot expose gap arithmetic")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        if self.status is not DataAvailability.AVAILABLE and not self.issues:
            raise ValueError("partial or unavailable comparison requires an issue")
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ExpectationGapEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    reverse_dcf_result_id: str
    execution_mode: ReverseDcfExecutionMode
    publication_eligibility: ReverseDcfPublicationEligibility
    central_valuation_eligible: bool
    implied_terminal_growth: float | None
    final_consensus_revenue_growth: float | None
    final_consensus_ebit_margin: float | None
    growth_rate_difference: float | None
    scenario_assumption_ids: tuple[str, ...]
    status: DataAvailability
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("evidence_id", "target_security_id", "target_issuer_id", "reverse_dcf_result_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.execution_mode, ReverseDcfExecutionMode):
            raise TypeError("execution_mode must use ReverseDcfExecutionMode")
        if not isinstance(self.publication_eligibility, ReverseDcfPublicationEligibility):
            raise TypeError("publication_eligibility must use ReverseDcfPublicationEligibility")
        if not isinstance(self.status, DataAvailability):
            raise TypeError("status must use DataAvailability")
        if self.central_valuation_eligible:
            raise ValueError("reverse-DCF expectation evidence is never central-valuation eligible")
        for name in (
            "implied_terminal_growth", "final_consensus_revenue_growth",
            "final_consensus_ebit_margin", "growth_rate_difference",
        ):
            _finite(getattr(self, name), name)
        if self.growth_rate_difference is not None:
            if self.implied_terminal_growth is None or self.final_consensus_revenue_growth is None:
                raise ValueError("growth-rate difference requires implied and consensus growth")
            if not math.isclose(
                self.growth_rate_difference,
                self.implied_terminal_growth - self.final_consensus_revenue_growth,
            ):
                raise ValueError("growth-rate difference arithmetic is inconsistent")
        if self.status is DataAvailability.AVAILABLE and self.implied_terminal_growth is None:
            raise ValueError("available expectation evidence requires a solved implied growth")
        object.__setattr__(self, "scenario_assumption_ids", _texts(self.scenario_assumption_ids, "assumption_id"))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class MarketComparisonResult:
    result_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    market_price_evidence: MarketPriceEvidence
    overall_comparison: ValuationPriceComparison
    family_comparisons: tuple[ValuationPriceComparison, ...]
    expectation_evidence: tuple[ExpectationGapEvidence, ...]
    reference_comparisons: tuple[ValuationPriceComparison, ...]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("result_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.market_price_evidence, MarketPriceEvidence):
            raise TypeError("market_price_evidence must use MarketPriceEvidence")
        if not isinstance(self.overall_comparison, ValuationPriceComparison):
            raise TypeError("overall_comparison must use ValuationPriceComparison")
        if (
            self.market_price_evidence.target_security_id != self.target_security_id
            or self.market_price_evidence.target_issuer_id != self.target_issuer_id
            or self.market_price_evidence.analysis_as_of != self.analysis_as_of
            or self.overall_comparison.target_security_id != self.target_security_id
            or self.overall_comparison.target_issuer_id != self.target_issuer_id
            or self.overall_comparison.analysis_as_of != self.analysis_as_of
        ):
            raise ValueError("market comparison target/snapshot dimensions must agree")
        families = tuple(self.family_comparisons)
        expectations = tuple(self.expectation_evidence)
        references = tuple(self.reference_comparisons)
        if any(not isinstance(value, ValuationPriceComparison) for value in (*families, *references)):
            raise TypeError("comparison collections must contain ValuationPriceComparison")
        if any(not isinstance(value, ExpectationGapEvidence) for value in expectations):
            raise TypeError("expectation_evidence must contain ExpectationGapEvidence")
        nested = (*families, *expectations, *references)
        if any(
            value.target_security_id != self.target_security_id
            or value.target_issuer_id != self.target_issuer_id
            or value.analysis_as_of != self.analysis_as_of
            for value in nested
        ):
            raise ValueError("all comparison evidence must share target and analysis snapshot")
        comparisons = (*families, *references)
        if any(value.market_price_evidence_id != self.market_price_evidence.evidence_id for value in comparisons):
            raise ValueError("all valuation comparisons must reference the bundled market-price evidence")
        if self.overall_comparison.market_price_evidence_id != self.market_price_evidence.evidence_id:
            raise ValueError("overall comparison must reference the bundled market-price evidence")
        if self.overall_comparison.comparison_scope is not MarketComparisonScope.OVERALL_RESOLVED:
            raise ValueError("overall_comparison must use OVERALL_RESOLVED scope")
        if any(value.comparison_scope is not MarketComparisonScope.INDIVIDUAL_FAMILY for value in families):
            raise ValueError("family comparisons must use INDIVIDUAL_FAMILY scope")
        if any(value.comparison_scope is not MarketComparisonScope.EXPECTATION_REFERENCE for value in references):
            raise ValueError("reference comparisons must use EXPECTATION_REFERENCE scope")
        object.__setattr__(self, "family_comparisons", families)
        object.__setattr__(self, "expectation_evidence", expectations)
        object.__setattr__(self, "reference_comparisons", references)
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
