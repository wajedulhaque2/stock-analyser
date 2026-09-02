"""Immutable provider-independent stock-research presentation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math

from .enums import (
    AggregationStatus,
    DataAvailability,
    IssueSeverity,
    PublicationCentralEstimator,
    PublicationEvidenceType,
    PublicationRangeSemantics,
    ReportDataQualityCategory,
    ReportExpectationState,
    ReportNumberSemantic,
    ReportSourceCategory,
    ReportStatus,
    ReverseDcfExecutionMode,
    ReverseDcfPublicationEligibility,
    ReverseDcfReadinessStatus,
    ReverseDcfScenarioAssumptionType,
    ValuationFamily,
    ValuationMethodStatus,
)
from .models import Provenance, _require_text, _validate_aware_datetime, _validate_currency


REPORT_SECTION_ORDER = (
    "identity_market",
    "valuation_summary",
    "valuation_families",
    "forward_consensus",
    "market_expectations",
    "external_references",
    "data_quality",
)


def stable_stock_research_report_id(*parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:32]
    return f"researchreport:{digest}"


def _texts(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} values must be unique")
    return normalized


def _provenance(values: tuple[Provenance, ...]) -> tuple[Provenance, ...]:
    normalized = tuple(dict.fromkeys(values))
    if any(not isinstance(value, Provenance) for value in normalized):
        raise TypeError("provenance must contain Provenance values")
    return normalized


def _finite(value: float | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ValueError(f"{name} must be finite when present")


@dataclass(frozen=True, slots=True)
class ReportIssue:
    message: str
    severity: IssueSeverity | None = None
    source_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _require_text(self.message, "message"))
        if self.severity is not None and not isinstance(self.severity, IssueSeverity):
            raise TypeError("severity must use IssueSeverity")
        if self.source_label is not None:
            object.__setattr__(self, "source_label", _require_text(self.source_label, "source_label"))


@dataclass(frozen=True, slots=True)
class ReportFieldSemantic:
    field_name: str
    semantic: ReportNumberSemantic

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_name", _require_text(self.field_name, "field_name"))
        if not isinstance(self.semantic, ReportNumberSemantic):
            raise TypeError("semantic must use ReportNumberSemantic")


@dataclass(frozen=True, slots=True)
class ReportSourceReference:
    display_name: str
    source_category: ReportSourceCategory
    evidence_ids: tuple[str, ...]
    observation_as_of: datetime | None
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", _require_text(self.display_name, "display_name"))
        if not isinstance(self.source_category, ReportSourceCategory):
            raise TypeError("source_category must use ReportSourceCategory")
        object.__setattr__(self, "evidence_ids", _texts(self.evidence_ids, "evidence_id"))
        if self.observation_as_of is not None:
            _validate_aware_datetime(self.observation_as_of, "observation_as_of")
        object.__setattr__(self, "provenance", _provenance(self.provenance))


def _section_common(instance) -> None:
    if not isinstance(instance.status, ReportStatus):
        raise TypeError("section status must use ReportStatus")
    if any(not isinstance(value, ReportSourceReference) for value in instance.source_references):
        raise TypeError("source_references must contain ReportSourceReference")
    if any(not isinstance(value, ReportIssue) for value in instance.issues):
        raise TypeError("issues must contain ReportIssue")
    if any(not isinstance(value, ReportFieldSemantic) for value in instance.display_semantics):
        raise TypeError("display_semantics must contain ReportFieldSemantic")
    object.__setattr__(instance, "source_references", tuple(dict.fromkeys(instance.source_references)))
    object.__setattr__(instance, "supporting_ids", _texts(instance.supporting_ids, "supporting_id"))
    object.__setattr__(instance, "issues", tuple(instance.issues))
    object.__setattr__(instance, "warnings", _texts(instance.warnings, "warning"))
    object.__setattr__(instance, "blocking_reasons", _texts(instance.blocking_reasons, "blocking_reason"))
    object.__setattr__(instance, "provenance", _provenance(instance.provenance))
    object.__setattr__(instance, "display_semantics", tuple(dict.fromkeys(instance.display_semantics)))


@dataclass(frozen=True, slots=True)
class ReportIdentitySection:
    status: ReportStatus
    canonical_security_id: str
    canonical_issuer_id: str
    display_symbol: str
    company_name: str
    exchange: str
    issuer_domicile: str
    listing_country: str
    reporting_currency: str
    quote_currency: str
    quote_unit: str
    quote_price_scale: float
    analysis_as_of: datetime
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "canonical_security_id", "canonical_issuer_id", "display_symbol", "company_name",
            "exchange", "issuer_domicile", "listing_country", "quote_unit",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.reporting_currency, "reporting_currency")
        _validate_currency(self.quote_currency, "quote_currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.quote_price_scale, "quote_price_scale")
        if self.quote_price_scale <= 0:
            raise ValueError("quote_price_scale must be positive")
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportMarketSection:
    status: ReportStatus
    availability: DataAvailability
    source_label: str
    normalized_market_price: float | None
    raw_market_quote: float | None
    normalized_currency: str | None
    normalized_per_share_unit: str | None
    quote_currency: str | None
    quote_unit: str | None
    quote_scale: float | None
    observation_timestamp: datetime | None
    freshness_label: str
    market_price_evidence_id: str | None
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.availability, DataAvailability):
            raise TypeError("availability must use DataAvailability")
        object.__setattr__(self, "source_label", _require_text(self.source_label, "source_label"))
        object.__setattr__(self, "freshness_label", _require_text(self.freshness_label, "freshness_label"))
        for name in ("normalized_market_price", "raw_market_quote", "quote_scale"):
            _finite(getattr(self, name), name)
        for name in ("normalized_currency", "quote_currency"):
            _validate_currency(getattr(self, name), name, required=False)
        for name in ("normalized_per_share_unit", "quote_unit", "market_price_evidence_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        if self.observation_timestamp is not None:
            _validate_aware_datetime(self.observation_timestamp, "observation_timestamp")
        if self.availability is not DataAvailability.AVAILABLE and self.normalized_market_price is not None:
            raise ValueError("unavailable market evidence cannot expose normalized price")
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportValuationSummarySection:
    status: ReportStatus
    publication_status: AggregationStatus
    publication_label: str
    eligible_family_count: int
    overall_value_label: str
    overall_central_value: float | None
    central_estimator: PublicationCentralEstimator | None
    envelope_lower: float | None
    envelope_upper: float | None
    envelope_semantics: PublicationRangeSemantics | None
    overlap_lower: float | None
    overlap_upper: float | None
    overlap_semantics: PublicationRangeSemantics | None
    currency: str | None
    per_share_unit: str | None
    overall_comparison_status: DataAvailability
    overall_market_price: float | None
    overall_lower_gap: float | None
    overall_central_gap: float | None
    overall_upper_gap: float | None
    overall_lower_gap_percent: float | None
    overall_central_gap_percent: float | None
    overall_upper_gap_percent: float | None
    publication_id: str
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.publication_status, AggregationStatus):
            raise TypeError("publication_status must use AggregationStatus")
        if not isinstance(self.overall_comparison_status, DataAvailability):
            raise TypeError("overall_comparison_status must use DataAvailability")
        for name in ("publication_label", "overall_value_label", "publication_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if isinstance(self.eligible_family_count, bool) or self.eligible_family_count < 0:
            raise ValueError("eligible_family_count must be non-negative")
        for name in (
            "overall_central_value", "envelope_lower", "envelope_upper", "overlap_lower",
            "overlap_upper", "overall_market_price", "overall_lower_gap", "overall_central_gap",
            "overall_upper_gap", "overall_lower_gap_percent", "overall_central_gap_percent",
            "overall_upper_gap_percent",
        ):
            _finite(getattr(self, name), name)
        _validate_currency(self.currency, "currency", required=False)
        if self.per_share_unit is not None:
            object.__setattr__(self, "per_share_unit", _require_text(self.per_share_unit, "per_share_unit"))
        if self.publication_status is AggregationStatus.RESOLVED:
            if self.overall_central_value is None or self.overall_value_label != "Overall fair value":
                raise ValueError("resolved summary requires the upstream overall fair value")
        elif self.overall_central_value is not None or self.overall_value_label not in {"Withheld", "Not published"}:
            raise ValueError("non-resolved summary must keep overall fair value withheld")
        if self.overall_comparison_status is not DataAvailability.AVAILABLE and any(
            value is not None for value in (
                self.overall_lower_gap, self.overall_central_gap, self.overall_upper_gap,
                self.overall_lower_gap_percent, self.overall_central_gap_percent,
                self.overall_upper_gap_percent,
            )
        ):
            raise ValueError("unavailable overall comparison cannot expose gaps")
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportValuationFamilySection:
    status: ReportStatus
    family: ValuationFamily
    family_status: ValuationMethodStatus
    method_label: str | None
    central_eligible: bool
    lower_value: float | None
    central_value: float | None
    upper_value: float | None
    currency: str | None
    per_share_unit: str | None
    source_result_id: str | None
    comparison_status: DataAvailability
    lower_gap: float | None
    central_gap: float | None
    upper_gap: float | None
    lower_gap_percent: float | None
    central_gap_percent: float | None
    upper_gap_percent: float | None
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        if self.family not in {ValuationFamily.OWN_HISTORY, ValuationFamily.PEER}:
            raise ValueError("report supports the approved valuation families only")
        if not isinstance(self.family_status, ValuationMethodStatus):
            raise TypeError("family_status must use ValuationMethodStatus")
        if not isinstance(self.comparison_status, DataAvailability):
            raise TypeError("comparison_status must use DataAvailability")
        for name in ("method_label", "per_share_unit", "source_result_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        _validate_currency(self.currency, "currency", required=False)
        for name in (
            "lower_value", "central_value", "upper_value", "lower_gap", "central_gap", "upper_gap",
            "lower_gap_percent", "central_gap_percent", "upper_gap_percent",
        ):
            _finite(getattr(self, name), name)
        if self.family_status is ValuationMethodStatus.UNAVAILABLE and any(
            value is not None for value in (self.lower_value, self.central_value, self.upper_value)
        ):
            raise ValueError("unavailable family cannot expose valuation points")
        if self.comparison_status is not DataAvailability.AVAILABLE and any(
            value is not None for value in (
                self.lower_gap, self.central_gap, self.upper_gap,
                self.lower_gap_percent, self.central_gap_percent, self.upper_gap_percent,
            )
        ):
            raise ValueError("unavailable family comparison cannot expose gaps")
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportConsensusPeriod:
    horizon_label: str
    fiscal_period_end: date
    revenue: float | None
    ebit: float | None
    ebitda: float | None
    ebit_margin: float | None
    revenue_growth: float | None
    revenue_analyst_count: int | None
    estimate_as_of: datetime
    currency: str
    status: ReverseDcfReadinessStatus
    supporting_ids: tuple[str, ...]
    issues: tuple[ReportIssue, ...]
    warnings: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    display_semantics: tuple[ReportFieldSemantic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "horizon_label", _require_text(self.horizon_label, "horizon_label"))
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.estimate_as_of, "estimate_as_of")
        for name in ("revenue", "ebit", "ebitda", "ebit_margin", "revenue_growth"):
            _finite(getattr(self, name), name)
        if self.revenue_analyst_count is not None and (
            isinstance(self.revenue_analyst_count, bool) or self.revenue_analyst_count < 0
        ):
            raise ValueError("revenue_analyst_count must be non-negative")
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        if any(not isinstance(value, ReportIssue) for value in self.issues):
            raise TypeError("issues must contain ReportIssue")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        object.__setattr__(self, "display_semantics", tuple(dict.fromkeys(self.display_semantics)))


@dataclass(frozen=True, slots=True)
class ReportConsensusSection:
    status: ReportStatus
    readiness_status: ReverseDcfReadinessStatus | None
    source_label: str
    period_count: int
    periods: tuple[ReportConsensusPeriod, ...]
    currency: str | None
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_label", _require_text(self.source_label, "source_label"))
        periods = tuple(self.periods)
        if self.period_count != len(periods):
            raise ValueError("period_count must match periods")
        if any(not isinstance(value, ReportConsensusPeriod) for value in periods):
            raise TypeError("periods must contain ReportConsensusPeriod")
        _validate_currency(self.currency, "currency", required=False)
        object.__setattr__(self, "periods", periods)
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportScenarioAssumption:
    assumption_id: str
    assumption_type: ReverseDcfScenarioAssumptionType
    value: float | None
    currency: str | None
    fiscal_year: int | None
    period_end: date | None
    source_label: str
    methodology_label: str
    rationale: str
    entered_by: str
    status: ReverseDcfReadinessStatus
    issues: tuple[ReportIssue, ...]
    warnings: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("assumption_id", "source_label", "methodology_label", "rationale", "entered_by"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _finite(self.value, "value")
        _validate_currency(self.currency, "currency", required=False)
        if any(not isinstance(value, ReportIssue) for value in self.issues):
            raise TypeError("issues must contain ReportIssue")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ReportExpectationEntry:
    state: ReportExpectationState
    state_label: str
    execution_mode: ReverseDcfExecutionMode
    publication_eligibility: ReverseDcfPublicationEligibility
    implied_terminal_growth: float | None
    final_consensus_revenue_growth: float | None
    final_consensus_ebit_margin: float | None
    growth_rate_difference: float | None
    scenario_assumptions: tuple[ReportScenarioAssumption, ...]
    reverse_dcf_result_id: str
    source_references: tuple[ReportSourceReference, ...]
    supporting_ids: tuple[str, ...]
    issues: tuple[ReportIssue, ...]
    warnings: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    display_semantics: tuple[ReportFieldSemantic, ...]

    def __post_init__(self) -> None:
        if self.state not in {
            ReportExpectationState.CANONICAL_EXPECTATIONS,
            ReportExpectationState.SCENARIO_EXPECTATIONS,
        }:
            raise ValueError("expectation entry must be canonical or scenario")
        object.__setattr__(self, "state_label", _require_text(self.state_label, "state_label"))
        object.__setattr__(self, "reverse_dcf_result_id", _require_text(self.reverse_dcf_result_id, "reverse_dcf_result_id"))
        for name in (
            "implied_terminal_growth", "final_consensus_revenue_growth",
            "final_consensus_ebit_margin", "growth_rate_difference",
        ):
            _finite(getattr(self, name), name)
        assumptions = tuple(self.scenario_assumptions)
        if any(not isinstance(value, ReportScenarioAssumption) for value in assumptions):
            raise TypeError("scenario_assumptions must contain ReportScenarioAssumption")
        if self.state is ReportExpectationState.SCENARIO_EXPECTATIONS and not assumptions:
            raise ValueError("scenario expectation must retain explicit assumption metadata")
        if self.state is ReportExpectationState.CANONICAL_EXPECTATIONS and assumptions:
            raise ValueError("canonical expectation cannot contain scenario assumptions")
        object.__setattr__(self, "scenario_assumptions", assumptions)
        if any(not isinstance(value, ReportSourceReference) for value in self.source_references):
            raise TypeError("source_references must contain ReportSourceReference")
        object.__setattr__(self, "source_references", tuple(dict.fromkeys(self.source_references)))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        if any(not isinstance(value, ReportIssue) for value in self.issues):
            raise TypeError("issues must contain ReportIssue")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        object.__setattr__(self, "display_semantics", tuple(dict.fromkeys(self.display_semantics)))


@dataclass(frozen=True, slots=True)
class ReportExpectationsSection:
    status: ReportStatus
    display_state: ReportExpectationState
    display_label: str
    entries: tuple[ReportExpectationEntry, ...]
    readiness_status: ReverseDcfReadinessStatus | None
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_label", _require_text(self.display_label, "display_label"))
        entries = tuple(self.entries)
        if any(not isinstance(value, ReportExpectationEntry) for value in entries):
            raise TypeError("entries must contain ReportExpectationEntry")
        if self.display_state in {ReportExpectationState.NOT_READY, ReportExpectationState.NOT_RUN} and entries:
            raise ValueError("not-ready/not-run expectations cannot contain execution entries")
        object.__setattr__(self, "entries", entries)
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportReferenceSection:
    status: ReportStatus
    reference_type: PublicationEvidenceType
    reference_only_label: str
    source_result_id: str
    evidence_as_of: datetime
    central_valuation_eligible: bool
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        if self.reference_type not in {
            PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
            PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
        }:
            raise ValueError("reference section requires external reference evidence")
        if self.central_valuation_eligible:
            raise ValueError("reference evidence is never central-valuation eligible")
        if self.reference_only_label != "REFERENCE ONLY":
            raise ValueError("reference evidence requires the controlled REFERENCE ONLY label")
        object.__setattr__(self, "source_result_id", _require_text(self.source_result_id, "source_result_id"))
        _validate_aware_datetime(self.evidence_as_of, "evidence_as_of")
        _section_common(self)


@dataclass(frozen=True, slots=True)
class ReportDataQualityRow:
    category: ReportDataQualityCategory
    status_code: str
    status_label: str
    blocking_reasons: tuple[str, ...]
    supporting_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.category, ReportDataQualityCategory):
            raise TypeError("category must use ReportDataQualityCategory")
        object.__setattr__(self, "status_code", _require_text(self.status_code, "status_code"))
        object.__setattr__(self, "status_label", _require_text(self.status_label, "status_label"))
        object.__setattr__(self, "blocking_reasons", _texts(self.blocking_reasons, "blocking_reason"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))


@dataclass(frozen=True, slots=True)
class ReportDataQualitySection:
    status: ReportStatus
    rows: tuple[ReportDataQualityRow, ...]
    source_references: tuple[ReportSourceReference, ...] = ()
    supporting_ids: tuple[str, ...] = ()
    issues: tuple[ReportIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    display_semantics: tuple[ReportFieldSemantic, ...] = ()

    def __post_init__(self) -> None:
        rows = tuple(self.rows)
        if any(not isinstance(value, ReportDataQualityRow) for value in rows):
            raise TypeError("rows must contain ReportDataQualityRow")
        if len({value.category for value in rows}) != len(rows):
            raise ValueError("data-quality categories must be unique")
        object.__setattr__(self, "rows", rows)
        _section_common(self)


@dataclass(frozen=True, slots=True)
class StockResearchReport:
    report_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    status: ReportStatus
    section_order: tuple[str, ...]
    identity: ReportIdentitySection
    market: ReportMarketSection
    valuation_summary: ReportValuationSummarySection
    valuation_families: tuple[ReportValuationFamilySection, ...]
    consensus: ReportConsensusSection
    expectations: ReportExpectationsSection
    references: tuple[ReportReferenceSection, ...]
    data_quality: ReportDataQualitySection
    source_references: tuple[ReportSourceReference, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    issues: tuple[ReportIssue, ...]
    warnings: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("report_id", "target_security_id", "target_issuer_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.status, ReportStatus):
            raise TypeError("status must use ReportStatus")
        order = _texts(self.section_order, "section_name")
        if order != REPORT_SECTION_ORDER:
            raise ValueError("section_order must use the deterministic report order")
        if (
            self.identity.canonical_security_id != self.target_security_id
            or self.identity.canonical_issuer_id != self.target_issuer_id
            or self.identity.analysis_as_of != self.analysis_as_of
        ):
            raise ValueError("report identity must match top-level target and snapshot")
        families = tuple(self.valuation_families)
        if {value.family for value in families} != {ValuationFamily.OWN_HISTORY, ValuationFamily.PEER}:
            raise ValueError("report must retain one row for each known valuation family")
        references = tuple(self.references)
        object.__setattr__(self, "section_order", order)
        object.__setattr__(self, "valuation_families", families)
        object.__setattr__(self, "references", references)
        if any(not isinstance(value, ReportSourceReference) for value in self.source_references):
            raise TypeError("source_references must contain ReportSourceReference")
        object.__setattr__(self, "source_references", tuple(dict.fromkeys(self.source_references)))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        if any(not isinstance(value, ReportIssue) for value in self.issues):
            raise TypeError("issues must contain ReportIssue")
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
