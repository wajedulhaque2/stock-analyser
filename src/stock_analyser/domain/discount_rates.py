"""Immutable evidence contracts for future currency-aware discount rates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math

from .enums import (
    AnnualizationBasis,
    BetaDefinition,
    BetaSourceMethod,
    CurrencyApplicability,
    DebtValueDefinition,
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    MarketReturnFrequency,
    MetricUnit,
    PriceReturnSemantics,
    RateEvidenceType,
    ReturnConvention,
    WaccComponentStatus,
)
from .models import Provenance, _require_text, _validate_aware_datetime, _validate_currency


def _stable_id(prefix: str, *parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def stable_risk_free_evidence_id(*parts: object) -> str:
    return _stable_id("riskfree", *parts)


def stable_erp_evidence_id(*parts: object) -> str:
    return _stable_id("erp", *parts)


def stable_erp_observation_id(*parts: object) -> str:
    return _stable_id("erpobs", *parts)


def stable_adjusted_price_observation_id(*parts: object) -> str:
    return _stable_id("adjustedprice", *parts)


def stable_beta_evidence_id(*parts: object) -> str:
    return _stable_id("beta", *parts)


def stable_cost_of_equity_id(*parts: object) -> str:
    return _stable_id("costequity", *parts)


def stable_discount_readiness_id(*parts: object) -> str:
    return _stable_id("discountready", *parts)


def stable_wacc_readiness_id(*parts: object) -> str:
    return _stable_id("waccready", *parts)


def _rate(
    value: float | None, field_name: str, *, positive: bool = False,
    bounded_decimal_input: bool = True,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
    if (bounded_decimal_input and not -1 < float(value) < 1) or (positive and value <= 0):
        raise ValueError(f"{field_name} must be a canonical decimal rate")


@dataclass(frozen=True, slots=True)
class EquityRiskPremiumObservation:
    """One normalized dated external-market ERP observation before policy selection."""

    observation_id: str
    value: float
    unit: MetricUnit
    applicable_currency: str
    market_scope: str
    source_name: str
    source_dataset: str
    source_date: date
    source_as_of: datetime
    retrieved_at: datetime
    methodology_label: str
    risk_free_convention: str
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in (
            "observation_id", "market_scope", "source_name", "source_dataset",
            "methodology_label", "risk_free_convention",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.applicable_currency, "applicable_currency")
        _validate_aware_datetime(self.source_as_of, "source_as_of")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        _rate(self.value, "ERP observation value", positive=True)
        if self.unit is not MetricUnit.PERCENT_DECIMAL:
            raise ValueError("ERP observations require percent_decimal unit")
        if self.source_date > self.source_as_of.date():
            raise ValueError("ERP source date cannot be after source_as_of")
        if self.provenance.retrieved_at != self.retrieved_at or self.provenance.as_of_at != self.source_as_of:
            raise ValueError("ERP observation timestamps must match provenance")


@dataclass(frozen=True, slots=True)
class MarketBenchmark:
    benchmark_id: str
    symbol: str
    name: str
    market: str
    currency: str
    return_semantics: PriceReturnSemantics

    def __post_init__(self) -> None:
        for name in ("benchmark_id", "symbol", "name", "market"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.currency, "currency")
        if not isinstance(self.return_semantics, PriceReturnSemantics):
            raise TypeError("return_semantics must use PriceReturnSemantics")


@dataclass(frozen=True, slots=True)
class AdjustedPriceObservation:
    """One adjusted historical close suitable for a declared total-return calculation."""

    observation_id: str
    instrument_id: str
    provider_symbol: str
    observation_date: date
    adjusted_close: float
    currency: str
    return_semantics: PriceReturnSemantics
    retrieved_at: datetime
    as_of_at: datetime
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("observation_id", "instrument_id", "provider_symbol"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.retrieved_at, "retrieved_at")
        _validate_aware_datetime(self.as_of_at, "as_of_at")
        if isinstance(self.adjusted_close, bool) or not math.isfinite(float(self.adjusted_close)) or self.adjusted_close <= 0:
            raise ValueError("adjusted_close must be finite and positive")
        if not isinstance(self.return_semantics, PriceReturnSemantics):
            raise TypeError("return_semantics must use PriceReturnSemantics")
        if self.observation_date > self.as_of_at.date():
            raise ValueError("adjusted price observation cannot be after as_of_at")
        if self.provenance.retrieved_at != self.retrieved_at or self.provenance.as_of_at != self.as_of_at:
            raise ValueError("adjusted price timestamps must match provenance")


@dataclass(frozen=True, slots=True)
class RiskFreeRateEvidence:
    evidence_id: str
    valuation_currency: str
    evidence_currency: str | None
    analysis_as_of: datetime
    evidence_type: RateEvidenceType
    value: float | None
    unit: MetricUnit
    annualization_basis: AnnualizationBasis
    observation_date: date | None
    source_as_of: datetime | None
    source_name: str
    source_dataset: str
    methodology_label: str
    currency_applicability: CurrencyApplicability
    status: DiscountRateEvidenceStatus
    reason: str
    provenance: tuple[Provenance, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_name", "source_dataset", "methodology_label", "reason", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.valuation_currency, "valuation_currency")
        _validate_currency(self.evidence_currency, "evidence_currency", required=False)
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if self.source_as_of is not None:
            _validate_aware_datetime(self.source_as_of, "source_as_of")
        if self.unit is not MetricUnit.PERCENT_DECIMAL:
            raise ValueError("risk-free rate requires percent_decimal unit")
        if self.annualization_basis is not AnnualizationBasis.ANNUAL_PERCENT_DECIMAL:
            raise ValueError("risk-free rate requires annual decimal-rate semantics")
        _rate(self.value, "risk-free value")
        provenance = tuple(dict.fromkeys(self.provenance))
        if any(not isinstance(item, Provenance) for item in provenance):
            raise TypeError("provenance must contain Provenance values")
        object.__setattr__(self, "provenance", provenance)
        if self.status in {DiscountRateEvidenceStatus.ELIGIBLE, DiscountRateEvidenceStatus.STALE}:
            if self.value is None or self.observation_date is None or self.source_as_of is None or not provenance:
                raise ValueError("selected risk-free evidence requires value, dates, and provenance")
            if self.currency_applicability is not CurrencyApplicability.MATCHED:
                raise ValueError("selected risk-free evidence must match valuation currency")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and self.observation_date > self.analysis_as_of.date():
            raise ValueError("eligible risk-free evidence cannot be future dated")


@dataclass(frozen=True, slots=True)
class EquityRiskPremiumEvidence:
    evidence_id: str
    value: float
    unit: MetricUnit
    applicable_currency: str
    market_scope: str
    source_name: str
    source_date: date
    source_as_of: datetime
    analysis_as_of: datetime
    methodology_label: str
    evidence_type: RateEvidenceType
    annualization_basis: AnnualizationBasis
    status: DiscountRateEvidenceStatus
    provenance: tuple[Provenance, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("evidence_id", "market_scope", "source_name", "methodology_label", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.applicable_currency, "applicable_currency")
        _validate_aware_datetime(self.source_as_of, "source_as_of")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _rate(self.value, "ERP value", positive=True)
        if self.unit is not MetricUnit.PERCENT_DECIMAL:
            raise ValueError("ERP requires percent_decimal unit")
        if self.evidence_type not in {
            RateEvidenceType.CONFIGURED_EXTERNAL_EQUITY_RISK_PREMIUM,
            RateEvidenceType.SOURCED_US_IMPLIED_EQUITY_RISK_PREMIUM,
        }:
            raise ValueError("ERP evidence must use an approved explicit or sourced evidence type")
        if self.annualization_basis is not AnnualizationBasis.ANNUAL_PERCENT_DECIMAL:
            raise ValueError("ERP requires annual decimal-rate semantics")
        if self.source_date > self.analysis_as_of.date() or self.source_as_of > self.analysis_as_of:
            raise ValueError("ERP evidence cannot be future dated")
        provenance = tuple(dict.fromkeys(self.provenance))
        if not provenance or any(not isinstance(item, Provenance) for item in provenance):
            raise ValueError("ERP requires explicit provenance")
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class BetaEvidence:
    evidence_id: str
    security_id: str
    issuer_id: str
    value: float | None
    unit: MetricUnit
    definition: BetaDefinition
    source_method: BetaSourceMethod
    provider: str
    provider_symbol: str
    observation_date: date | None
    source_as_of: datetime | None
    analysis_as_of: datetime
    benchmark: str | None
    lookback: str | None
    return_frequency: str | None
    methodology_label: str
    status: DiscountRateEvidenceStatus
    reason: str
    provenance: tuple[Provenance, ...]
    policy_id: str
    benchmark_id: str | None = None
    benchmark_symbol: str | None = None
    benchmark_name: str | None = None
    benchmark_market: str | None = None
    benchmark_currency: str | None = None
    return_convention: ReturnConvention | None = None
    return_semantics: PriceReturnSemantics | None = None
    sample_count: int | None = None
    regression_start: date | None = None
    regression_end: date | None = None
    alpha: float | None = None
    r_squared: float | None = None
    residual_standard_error: float | None = None
    beta_standard_error: float | None = None
    benchmark_variance: float | None = None
    source_observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "security_id", "issuer_id", "provider", "provider_symbol",
            "methodology_label", "reason", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if self.source_as_of is not None:
            _validate_aware_datetime(self.source_as_of, "source_as_of")
        if self.unit is not MetricUnit.RATIO:
            raise ValueError("beta requires ratio unit")
        if self.value is not None and (isinstance(self.value, bool) or not math.isfinite(float(self.value))):
            raise ValueError("beta must be finite when present")
        for name in ("benchmark", "lookback", "return_frequency"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        for name in ("benchmark_id", "benchmark_symbol", "benchmark_name", "benchmark_market"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        _validate_currency(self.benchmark_currency, "benchmark_currency", required=False)
        if self.sample_count is not None and (
            isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0
        ):
            raise ValueError("sample_count must be a non-negative integer")
        for name in ("alpha", "r_squared", "residual_standard_error", "beta_standard_error", "benchmark_variance"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite when present")
        if self.regression_start is not None and self.regression_end is not None and self.regression_start > self.regression_end:
            raise ValueError("regression_start must be on or before regression_end")
        source_ids = tuple(dict.fromkeys(_require_text(item, "source_observation_id") for item in self.source_observation_ids))
        object.__setattr__(self, "source_observation_ids", source_ids)
        provenance = tuple(dict.fromkeys(self.provenance))
        object.__setattr__(self, "provenance", provenance)
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE:
            if self.value is None or not provenance:
                raise ValueError("eligible beta requires value and provenance")
            if self.definition is BetaDefinition.VERIFIED_METHOD and not all((self.benchmark, self.lookback, self.return_frequency)):
                raise ValueError("verified-method beta requires explicit methodology evidence")
            if self.observation_date is None or self.source_as_of is None:
                raise ValueError("eligible beta requires source dates")
        if self.definition is BetaDefinition.REGRESSION_EQUITY_BETA:
            required = (
                self.benchmark_id, self.benchmark_symbol, self.benchmark_name, self.benchmark_market,
                self.benchmark_currency, self.return_convention, self.return_semantics, self.sample_count,
                self.regression_start, self.regression_end, self.alpha, self.r_squared,
                self.benchmark_variance,
            )
            if any(item is None for item in required) or not source_ids:
                raise ValueError("regression beta requires complete benchmark, return, sample, diagnostic, and source-ID evidence")
            if self.source_method is not BetaSourceMethod.US_5Y_MONTHLY_MARKET_REGRESSION:
                raise ValueError("regression beta requires the controlled US five-year monthly method")
            if self.return_frequency != MarketReturnFrequency.MONTHLY.value:
                raise ValueError("initial regression beta requires monthly return frequency")
        if self.observation_date is not None and self.observation_date > self.analysis_as_of.date():
            raise ValueError("beta observation cannot be future dated")


@dataclass(frozen=True, slots=True)
class CostOfEquityResult:
    result_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    risk_free_evidence_id: str | None
    risk_free_rate: float | None
    erp_evidence_id: str | None
    equity_risk_premium: float | None
    beta_evidence_id: str | None
    beta: float | None
    cost_of_equity: float | None
    status: DiscountRateReadinessStatus
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("result_id", "security_id", "issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        for name in ("risk_free_rate", "equity_risk_premium", "cost_of_equity"):
            _rate(
                getattr(self, name), name, positive=name == "equity_risk_premium",
                bounded_decimal_input=name != "cost_of_equity",
            )
        if self.beta is not None and (isinstance(self.beta, bool) or not math.isfinite(float(self.beta))):
            raise ValueError("beta must be finite")
        reasons = tuple(_require_text(item, "blocking_reason") for item in self.blocking_reasons)
        object.__setattr__(self, "blocking_reasons", tuple(dict.fromkeys(reasons)))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(_require_text(item, "warning") for item in self.warnings)))
        object.__setattr__(self, "supporting_ids", tuple(dict.fromkeys(_require_text(item, "supporting_id") for item in self.supporting_ids)))
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        if self.status is DiscountRateReadinessStatus.READY:
            if any(value is None for value in (self.risk_free_rate, self.equity_risk_premium, self.beta, self.cost_of_equity)):
                raise ValueError("ready cost of equity requires all CAPM inputs and output")
            if reasons:
                raise ValueError("ready cost of equity cannot have blocking reasons")
            expected = self.risk_free_rate + self.beta * self.equity_risk_premium
            if not math.isclose(self.cost_of_equity, expected, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("cost of equity must equal risk-free rate plus beta times ERP")
        elif self.cost_of_equity is not None or not reasons:
            raise ValueError("non-ready cost of equity must be withheld with reasons")


@dataclass(frozen=True, slots=True)
class WaccInputReadiness:
    readiness_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    cost_of_equity_status: DiscountRateReadinessStatus
    equity_market_value_status: WaccComponentStatus
    debt_value_status: WaccComponentStatus
    debt_value_definition: DebtValueDefinition
    pretax_cost_of_debt_status: WaccComponentStatus
    tax_rate_status: WaccComponentStatus
    capital_structure_weights_status: WaccComponentStatus
    preferred_equity_treatment_status: WaccComponentStatus
    nci_treatment_status: WaccComponentStatus
    currency_alignment_status: WaccComponentStatus
    status: DiscountRateReadinessStatus
    missing_requirements: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("readiness_id", "security_id", "issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        missing = tuple(dict.fromkeys(_require_text(item, "missing_requirement") for item in self.missing_requirements))
        object.__setattr__(self, "missing_requirements", missing)
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(_require_text(item, "warning") for item in self.warnings)))
        object.__setattr__(self, "supporting_ids", tuple(dict.fromkeys(_require_text(item, "supporting_id") for item in self.supporting_ids)))
        if self.status is DiscountRateReadinessStatus.READY and missing:
            raise ValueError("ready WACC inputs cannot have missing requirements")
        if self.status is not DiscountRateReadinessStatus.READY and not missing:
            raise ValueError("non-ready WACC inputs require missing requirements")


@dataclass(frozen=True, slots=True)
class DiscountRateReadiness:
    readiness_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    risk_free_status: DiscountRateEvidenceStatus
    erp_status: DiscountRateEvidenceStatus
    beta_status: DiscountRateEvidenceStatus
    cost_of_equity_status: DiscountRateReadinessStatus
    wacc_status: DiscountRateReadinessStatus
    status: DiscountRateReadinessStatus
    missing_requirements: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("readiness_id", "security_id", "issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        missing = tuple(dict.fromkeys(_require_text(item, "missing_requirement") for item in self.missing_requirements))
        object.__setattr__(self, "missing_requirements", missing)
        object.__setattr__(self, "supporting_ids", tuple(dict.fromkeys(_require_text(item, "supporting_id") for item in self.supporting_ids)))
        if self.status is DiscountRateReadinessStatus.READY and missing:
            raise ValueError("ready discount-rate evidence cannot have missing requirements")
