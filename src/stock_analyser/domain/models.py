"""Immutable canonical domain records for the V1 migration boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import math
import re
from typing import Iterable

from .enums import (
    CashFlowDefinition,
    CapabilityStatus,
    CoverageLevel,
    DataAvailability,
    DefinitionVerificationStatus,
    EstimateCase,
    ExternalReferenceType,
    Frequency,
    IssueSeverity,
    MacroFrequency,
    MacroMetric,
    MetricId,
    MetricUnit,
    ObservationType,
    ReconciliationDatasetType,
    ReconciliationStatus,
    ReconciliationStream,
    SourceAgreementLevel,
    ValuationMethodStatus,
)


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_OBSERVATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_SENSITIVE_RE = re.compile(r"(?:authorization|api[_ -]?key|bearer\s+)", re.IGNORECASE)
_MONETARY_UNITS = {MetricUnit.CURRENCY, MetricUnit.CURRENCY_PER_SHARE}
_FLOW_FREQUENCIES = {Frequency.ANNUAL, Frequency.QUARTERLY, Frequency.LTM, Frequency.NTM}


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_currency(value: str | None, field_name: str, *, required: bool = True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be an uppercase ISO-style three-letter code")


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


def _finite_optional(value: float | None, field_name: str) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
        raise ValueError(f"{field_name} must be finite when present")


@dataclass(frozen=True, slots=True)
class ProviderSymbol:
    provider: str
    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_text(self.provider, "provider").lower())
        object.__setattr__(self, "symbol", _require_text(self.symbol, "symbol"))


@dataclass(frozen=True, slots=True)
class CompanyIdentity:
    canonical_symbol: str
    security_id: str
    issuer_id: str
    company_name: str
    issuer_domicile: str
    listing_country: str
    exchange: str
    sector: str
    industry: str
    security_type: str
    reporting_currency: str
    quote_currency: str
    quote_unit: str
    price_scale: float
    fiscal_year_end: str
    provider_symbols: tuple[ProviderSymbol, ...] = ()
    underlying_security_id: str | None = None
    adr_ratio: float | None = None
    identity_availability: DataAvailability = DataAvailability.AVAILABLE
    company_type: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "canonical_symbol", "security_id", "issuer_id", "company_name",
            "issuer_domicile", "listing_country", "exchange", "sector", "industry",
            "security_type", "quote_unit",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.reporting_currency, "reporting_currency")
        _validate_currency(self.quote_currency, "quote_currency")
        if isinstance(self.price_scale, bool) or not math.isfinite(float(self.price_scale)) or self.price_scale <= 0:
            raise ValueError("price_scale must be finite and positive")
        if not re.fullmatch(r"(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])", self.fiscal_year_end):
            raise ValueError("fiscal_year_end must use MM-DD format")
        providers = tuple(self.provider_symbols)
        if len({x.provider for x in providers}) != len(providers):
            raise ValueError("provider_symbols must contain at most one symbol per provider")
        object.__setattr__(self, "provider_symbols", providers)
        if self.underlying_security_id is not None:
            object.__setattr__(self, "underlying_security_id", _require_text(self.underlying_security_id, "underlying_security_id"))
        _finite_optional(self.adr_ratio, "adr_ratio")
        if self.adr_ratio is not None and self.adr_ratio <= 0:
            raise ValueError("adr_ratio must be positive when present")
        is_depositary = "adr" in self.security_type.lower() or "depositary" in self.security_type.lower()
        if is_depositary and self.underlying_security_id is None:
            raise ValueError("depositary receipts require underlying_security_id")
        if is_depositary and self.adr_ratio is None and self.identity_availability is not DataAvailability.PARTIAL:
            raise ValueError("depositary receipts require explicit adr_ratio or partial identity availability")
        if self.adr_ratio is not None and self.underlying_security_id is None:
            raise ValueError("adr_ratio requires underlying_security_id")
        if self.company_type is not None:
            object.__setattr__(self, "company_type", _require_text(self.company_type, "company_type"))

    def normalize_quote_price(self, raw_quote_price: float) -> float:
        """Apply quote-unit scale to a traded quote price and nothing else."""
        if isinstance(raw_quote_price, bool) or not math.isfinite(float(raw_quote_price)):
            raise ValueError("raw_quote_price must be finite")
        return float(raw_quote_price) * float(self.price_scale)


@dataclass(frozen=True, slots=True)
class Provenance:
    provider: str
    endpoint_or_dataset: str
    provider_symbol: str
    retrieved_at: datetime
    as_of_at: datetime
    transformation_steps: tuple[str, ...] = ()
    input_observation_ids: tuple[str, ...] = ()
    configuration_or_override_id: str | None = None
    source_metric: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "endpoint_or_dataset", "provider_symbol"):
            value = _require_text(getattr(self, name), name)
            if _SENSITIVE_RE.search(value):
                raise ValueError(f"{name} must not contain secret-bearing content")
            object.__setattr__(self, name, value)
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        steps = tuple(_require_text(x, "transformation_step") for x in self.transformation_steps)
        if any(_SENSITIVE_RE.search(x) for x in steps):
            raise ValueError("transformation_steps must not contain secret-bearing content")
        object.__setattr__(self, "transformation_steps", steps)
        ids = tuple(_require_text(x, "input_observation_id") for x in self.input_observation_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("input_observation_ids must be unique")
        object.__setattr__(self, "input_observation_ids", ids)
        if self.configuration_or_override_id is not None:
            value = _require_text(self.configuration_or_override_id, "configuration_or_override_id")
            if _SENSITIVE_RE.search(value):
                raise ValueError("configuration_or_override_id must not contain secret-bearing content")
            object.__setattr__(self, "configuration_or_override_id", value)
        if self.source_metric is not None:
            value = _require_text(self.source_metric, "source_metric")
            if _SENSITIVE_RE.search(value):
                raise ValueError("source_metric must not contain secret-bearing content")
            object.__setattr__(self, "source_metric", value)


@dataclass(frozen=True, slots=True)
class MetricObservation:
    observation_id: str
    metric_id: MetricId
    value: float
    unit: MetricUnit
    frequency: Frequency
    observation_type: ObservationType
    estimate_case: EstimateCase
    retrieved_at: datetime
    as_of_at: datetime
    provenance: Provenance
    currency: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    analyst_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, str) or not _OBSERVATION_ID_RE.fullmatch(self.observation_id):
            raise ValueError("observation_id must be a stable 8-128 character identifier")
        if isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("value must be finite; NaN and infinity are forbidden")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        if self.retrieved_at != self.provenance.retrieved_at or self.as_of_at != self.provenance.as_of_at:
            raise ValueError("observation timestamps must match provenance timestamps")
        if self.unit in _MONETARY_UNITS:
            _validate_currency(self.currency, "currency")
        else:
            _validate_currency(self.currency, "currency", required=False)
        if self.period_start is not None and self.period_end is not None and self.period_start > self.period_end:
            raise ValueError("period_start must be on or before period_end")
        if self.frequency in _FLOW_FREQUENCIES:
            if self.period_start is None or self.period_end is None:
                raise ValueError("flow observations require period_start and period_end")
        elif self.frequency is Frequency.POINT_IN_TIME:
            if self.period_start is not None or self.period_end is None:
                raise ValueError("point-in-time observations require period_end and no period_start")
        if self.frequency is Frequency.ANNUAL:
            if self.fiscal_year is None or self.fiscal_quarter is not None:
                raise ValueError("annual observations require fiscal_year and no fiscal_quarter")
        elif self.frequency is Frequency.QUARTERLY:
            if self.fiscal_year is None or self.fiscal_quarter not in {1, 2, 3, 4}:
                raise ValueError("quarterly observations require fiscal_year and fiscal_quarter 1-4")
        elif self.fiscal_quarter is not None:
            raise ValueError("fiscal_quarter is only valid for quarterly observations")
        if self.frequency is Frequency.NTM and self.observation_type is not ObservationType.ESTIMATE:
            raise ValueError("NTM observations must be estimates")
        if self.observation_type is ObservationType.ACTUAL:
            if self.estimate_case is not EstimateCase.NOT_APPLICABLE:
                raise ValueError("actual observations cannot carry estimate cases")
            if self.analyst_count is not None:
                raise ValueError("actual observations cannot carry analyst_count")
        elif self.estimate_case is EstimateCase.NOT_APPLICABLE:
            raise ValueError("estimate observations require an explicit low, average, or high case")
        if self.analyst_count is not None:
            if isinstance(self.analyst_count, bool) or not isinstance(self.analyst_count, int) or self.analyst_count < 0:
                raise ValueError("analyst_count must be a non-negative integer when supplied")

    @property
    def is_forward_as_of(self) -> bool:
        """True only for an estimate whose fiscal period ends after its as-of time."""
        return (
            self.observation_type is ObservationType.ESTIMATE
            and self.period_end is not None
            and self.period_end > self.as_of_at.date()
        )


@dataclass(frozen=True, slots=True)
class DataIssue:
    severity: IssueSeverity
    metric: MetricId | str
    provider: str
    reason: str
    expected: str | None = None
    observed: str | None = None
    action: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    provider: str
    capability: str
    status: CapabilityStatus
    checked_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "capability", _require_text(self.capability, "capability"))
        _validate_aware_datetime(self.checked_at, "checked_at")
        if self.status is not CapabilityStatus.AVAILABLE and not self.reason:
            raise ValueError("non-available capabilities require a reason")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    canonical_observation_id: str
    validator_observation_id: str | None
    canonical_security_id: str
    validator_security_id: str
    canonical_issuer_id: str
    validator_issuer_id: str
    canonical_provider: str
    validator_provider: str
    canonical_provider_symbol: str
    validator_provider_symbol: str | None
    canonical_dataset_type: ReconciliationDatasetType
    validator_dataset_type: ReconciliationDatasetType
    stream: ReconciliationStream
    metric_id: MetricId
    frequency: Frequency
    observation_type: ObservationType
    estimate_case: EstimateCase
    unit: MetricUnit
    currency: str | None
    period_start: date | None
    period_end: date | None
    fiscal_year: int | None
    fiscal_quarter: int | None
    comparison_as_of: datetime
    canonical_as_of_at: datetime
    canonical_retrieved_at: datetime
    canonical_value: float
    validator_as_of_at: datetime | None
    validator_retrieved_at: datetime | None
    validator_value: float | None
    absolute_difference: float | None
    relative_difference: float | None
    agreement_level: SourceAgreementLevel
    status: ReconciliationStatus
    canonical_reason: str
    tolerance_policy_id: str | None = None
    confirmed_below: float | None = None
    warning_at_or_below: float | None = None
    semantic_mismatches: tuple[str, ...] = ()
    validator_capability_status: CapabilityStatus | None = None
    validator_capability_reason: str | None = None
    issues: tuple[DataIssue, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "canonical_observation_id", "canonical_security_id", "validator_security_id",
            "canonical_issuer_id", "validator_issuer_id", "canonical_provider",
            "validator_provider", "canonical_provider_symbol", "canonical_reason",
        ):
            value = _require_text(getattr(self, name), name)
            if _SENSITIVE_RE.search(value):
                raise ValueError(f"{name} must not contain secret-bearing content")
            object.__setattr__(self, name, value)
        for name in (
            "validator_observation_id", "validator_provider_symbol", "tolerance_policy_id",
            "validator_capability_reason",
        ):
            value = getattr(self, name)
            if value is not None:
                value = _require_text(value, name)
                if _SENSITIVE_RE.search(value):
                    raise ValueError(f"{name} must not contain secret-bearing content")
                object.__setattr__(self, name, value)
        _validate_aware_datetime(self.comparison_as_of, "comparison_as_of")
        _validate_aware_datetime(self.canonical_as_of_at, "canonical_as_of_at")
        _validate_aware_datetime(self.canonical_retrieved_at, "canonical_retrieved_at")
        if self.validator_as_of_at is not None:
            _validate_aware_datetime(self.validator_as_of_at, "validator_as_of_at")
        if self.validator_retrieved_at is not None:
            _validate_aware_datetime(self.validator_retrieved_at, "validator_retrieved_at")
        _finite_optional(self.canonical_value, "canonical_value")
        _finite_optional(self.validator_value, "validator_value")
        for name in ("absolute_difference", "relative_difference", "confirmed_below", "warning_at_or_below"):
            value = getattr(self, name)
            _finite_optional(value, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative when present")
        if self.confirmed_below is not None and self.warning_at_or_below is not None:
            if self.confirmed_below > self.warning_at_or_below:
                raise ValueError("confirmed_below must not exceed warning_at_or_below")
        _validate_currency(self.currency, "currency", required=False)
        mismatches = tuple(_require_text(value, "semantic_mismatch") for value in self.semantic_mismatches)
        if any(_SENSITIVE_RE.search(value) for value in mismatches):
            raise ValueError("semantic_mismatches must not contain secret-bearing content")
        object.__setattr__(self, "semantic_mismatches", mismatches)
        object.__setattr__(self, "issues", tuple(self.issues))
        compared_levels = {
            SourceAgreementLevel.CONFIRMED, SourceAgreementLevel.WARNING,
            SourceAgreementLevel.CONFLICT, SourceAgreementLevel.COMPARABLE_UNSCORED,
        }
        if self.status is ReconciliationStatus.COMPARED:
            if self.validator_observation_id is None or self.validator_value is None:
                raise ValueError("compared reconciliation requires a validator observation and value")
            if self.absolute_difference is None or self.agreement_level not in compared_levels:
                raise ValueError("compared reconciliation requires differences and a scored/unscored agreement level")
        elif self.status is ReconciliationStatus.NOT_COMPARABLE:
            if self.agreement_level is not SourceAgreementLevel.NOT_COMPARABLE or not mismatches:
                raise ValueError("not-comparable reconciliation requires controlled mismatch reasons")
            if self.absolute_difference is not None or self.relative_difference is not None:
                raise ValueError("not-comparable reconciliation cannot contain numeric differences")
        elif self.agreement_level is not SourceAgreementLevel.NO_VALIDATOR:
            raise ValueError("missing/unavailable validators require NO_VALIDATOR agreement level")
        if self.validator_observation_id is None and any(
            value is not None for value in (
                self.validator_as_of_at, self.validator_retrieved_at, self.validator_value,
                self.absolute_difference, self.relative_difference,
            )
        ):
            raise ValueError("missing validator observation cannot carry validator values or differences")


@dataclass(frozen=True, slots=True)
class ValuationResult:
    method: str
    valuation_family: str
    currency: str
    status: ValuationMethodStatus
    low: float | None = None
    central: float | None = None
    high: float | None = None
    confidence: CoverageLevel | None = None
    input_observation_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _require_text(self.method, "method"))
        object.__setattr__(self, "valuation_family", _require_text(self.valuation_family, "valuation_family"))
        _validate_currency(self.currency, "currency")
        for name in ("low", "central", "high"):
            _finite_optional(getattr(self, name), name)
        values = (self.low, self.central, self.high)
        if self.status is ValuationMethodStatus.UNAVAILABLE:
            if any(value is not None for value in values):
                raise ValueError("unavailable valuation results cannot contain monetary values")
            if not self.reason:
                raise ValueError("unavailable valuation results require a reason")
        elif self.status is ValuationMethodStatus.VALID and self.central is None:
            raise ValueError("valid valuation results require a central value")
        elif self.status is ValuationMethodStatus.PARTIAL and all(value is None for value in values):
            raise ValueError("partial valuation results require at least one monetary value")
        if all(value is not None for value in values) and not self.low <= self.central <= self.high:
            raise ValueError("valuation range must satisfy low <= central <= high")
        ids = tuple(_require_text(x, "input_observation_id") for x in self.input_observation_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("input_observation_ids must be unique")
        object.__setattr__(self, "input_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "warnings", tuple(_require_text(x, "warning") for x in self.warnings))


@dataclass(frozen=True, slots=True)
class ExternalValuationReference:
    reference_type: ExternalReferenceType
    currency: str
    provider: str
    as_of_at: datetime
    provenance: Provenance
    value: float | None = None
    low: float | None = None
    median: float | None = None
    consensus: float | None = None
    high: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        if self.provider.lower() != self.provenance.provider.lower():
            raise ValueError("external reference provider must match provenance provider")
        if self.as_of_at != self.provenance.as_of_at:
            raise ValueError("external reference as_of_at must match provenance")
        values = (self.value, self.low, self.median, self.consensus, self.high)
        for name, item in zip(("value", "low", "median", "consensus", "high"), values):
            _finite_optional(item, name)
        if all(item is None for item in values):
            raise ValueError("external valuation references require at least one value")
        ordered = [item for item in (self.low, self.median, self.consensus, self.high) if item is not None]
        if ordered != sorted(ordered):
            raise ValueError("external reference range must be ordered")


def ensure_internal_valuation_results(items: Iterable[ValuationResult]) -> tuple[ValuationResult, ...]:
    """Reject external references and arbitrary lookalikes at the aggregation boundary."""
    results = tuple(items)
    if any(type(item) is not ValuationResult for item in results):
        raise TypeError("internal valuation collections accept ValuationResult objects only")
    return results


@dataclass(frozen=True, slots=True)
class CashFlowDefinitionEvidence:
    provider: str
    provider_metric: str
    endpoint_or_dataset: str
    definition: CashFlowDefinition
    verification_status: DefinitionVerificationStatus
    definition_reference: str | None = None
    verified_at: datetime | None = None
    notes: str | None = None
    observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("provider", "provider_metric", "endpoint_or_dataset"):
            value = _require_text(getattr(self, name), name)
            if _SENSITIVE_RE.search(value):
                raise ValueError(f"{name} must not contain secret-bearing content")
            object.__setattr__(self, name, value)
        for name in ("definition_reference", "notes"):
            value = getattr(self, name)
            if value is not None:
                value = _require_text(value, name)
                if _SENSITIVE_RE.search(value):
                    raise ValueError(f"{name} must not contain secret-bearing content")
                object.__setattr__(self, name, value)
        if self.verified_at is not None:
            _validate_aware_datetime(self.verified_at, "verified_at")
        if self.verification_status is DefinitionVerificationStatus.VERIFIED:
            if self.verified_at is None or self.definition_reference is None:
                raise ValueError("verified cash-flow definitions require verified_at and definition_reference")
        ids = tuple(_require_text(value, "observation_id") for value in self.observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("observation_ids must be unique")
        object.__setattr__(self, "observation_ids", ids)

    @property
    def is_verified_fcff(self) -> bool:
        return (
            self.definition is CashFlowDefinition.FCFF
            and self.verification_status is DefinitionVerificationStatus.VERIFIED
        )

    @property
    def is_verified_fcfe(self) -> bool:
        return (
            self.definition is CashFlowDefinition.FCFE
            and self.verification_status is DefinitionVerificationStatus.VERIFIED
        )


@dataclass(frozen=True, slots=True)
class EstimateRevision:
    metric_id: MetricId
    frequency: Frequency
    period_start: date
    period_end: date
    fiscal_year: int
    revision_window: str
    unit: MetricUnit
    currency: str
    provider: str
    retrieved_at: datetime
    as_of_at: datetime
    provenance: Provenance
    fiscal_quarter: int | None = None
    current_estimate: float | None = None
    prior_estimate: float | None = None
    up_revisions: int | None = None
    down_revisions: int | None = None
    analyst_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_window", _require_text(self.revision_window, "revision_window"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        if self.provider.lower() != self.provenance.provider.lower():
            raise ValueError("revision provider must match provenance provider")
        if self.retrieved_at != self.provenance.retrieved_at or self.as_of_at != self.provenance.as_of_at:
            raise ValueError("revision timestamps must match provenance timestamps")
        if self.period_start > self.period_end:
            raise ValueError("period_start must be on or before period_end")
        if self.frequency is Frequency.ANNUAL:
            if self.fiscal_quarter is not None:
                raise ValueError("annual revisions cannot carry fiscal_quarter")
        elif self.frequency is Frequency.QUARTERLY:
            if self.fiscal_quarter not in {1, 2, 3, 4}:
                raise ValueError("quarterly revisions require fiscal_quarter 1-4")
        else:
            raise ValueError("estimate revisions support annual or quarterly frequency only")
        for name in ("current_estimate", "prior_estimate"):
            _finite_optional(getattr(self, name), name)
        for name in ("up_revisions", "down_revisions", "analyst_count"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when supplied")
        if all(
            value is None
            for value in (self.current_estimate, self.prior_estimate, self.up_revisions, self.down_revisions)
        ):
            raise ValueError("estimate revision requires at least one supplied revision evidence field")


@dataclass(frozen=True, slots=True)
class MacroObservation:
    series_id: str
    metric: MacroMetric
    value: float
    unit: MetricUnit
    currency: str
    observation_date: date
    frequency: MacroFrequency
    as_of_at: datetime
    retrieved_at: datetime
    provider: str
    provenance: Provenance
    realtime_start: date | None = None
    realtime_end: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _require_text(self.series_id, "series_id"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        if isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("macro value must be finite")
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        if self.unit is not MetricUnit.PERCENT_DECIMAL:
            raise ValueError("initial macro rate observations require percent_decimal unit")
        if self.provider.lower() != self.provenance.provider.lower():
            raise ValueError("macro provider must match provenance provider")
        if self.retrieved_at != self.provenance.retrieved_at or self.as_of_at != self.provenance.as_of_at:
            raise ValueError("macro timestamps must match provenance timestamps")
        if self.realtime_start is not None and self.realtime_end is not None:
            if self.realtime_start > self.realtime_end:
                raise ValueError("realtime_start must be on or before realtime_end")
