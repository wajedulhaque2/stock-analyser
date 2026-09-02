"""Immutable evidence contracts for the bounded USD production-WACC path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math

from .enums import (
    CapitalComponent, CompanyClassScope, CoverageNumeratorDefinition, DebtValueDefinition,
    DiscountRateEvidenceStatus, DiscountRateReadinessStatus, MetricUnit,
    OtherClaimsStatus,
)
from .models import Provenance, _require_text, _validate_aware_datetime, _validate_currency


def _id(prefix: str, *parts: object) -> str:
    return f"{prefix}:" + sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def stable_wacc_evidence_id(prefix: str, *parts: object) -> str:
    return _id(prefix, *parts)


def _finite(value: float | None, name: str, *, nonnegative: bool = False, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if nonnegative and value < 0 or positive and value <= 0:
        raise ValueError(f"{name} has an invalid sign")


def _rate(value: float | None, name: str) -> None:
    _finite(value, name)
    if value is not None and not 0 <= value < 1:
        raise ValueError(f"{name} must be a decimal rate in [0, 1)")


@dataclass(frozen=True, slots=True)
class SyntheticRatingBand:
    evidence_id: str
    coverage_lower_bound: float
    coverage_upper_bound: float
    lower_inclusive: bool
    upper_inclusive: bool
    synthetic_rating: str
    default_spread: float
    source_date: date
    company_class_scope: CompanyClassScope
    source_name: str
    source_dataset: str
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("evidence_id", "synthetic_rating", "source_name", "source_dataset"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _finite(self.coverage_lower_bound, "coverage_lower_bound")
        _finite(self.coverage_upper_bound, "coverage_upper_bound")
        if self.coverage_lower_bound >= self.coverage_upper_bound:
            raise ValueError("rating bounds must increase")
        _rate(self.default_spread, "default_spread")
        if self.provenance.as_of_at.date() != self.source_date:
            raise ValueError("rating source date must match provenance")


@dataclass(frozen=True, slots=True)
class MarginalTaxRateObservation:
    observation_id: str
    jurisdiction: str
    value: float
    source_date: date
    source_name: str
    source_dataset: str
    underlying_source: str
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("observation_id", "jurisdiction", "source_name", "source_dataset", "underlying_source"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _rate(self.value, "marginal tax rate")
        if self.provenance.as_of_at.date() != self.source_date:
            raise ValueError("tax source date must match provenance")


@dataclass(frozen=True, slots=True)
class CapitalValueEvidence:
    evidence_id: str
    component: CapitalComponent
    definition: DebtValueDefinition
    value: float | None
    currency: str
    observation_date: date | None
    analysis_as_of: datetime
    source_observation_id: str | None
    provider: str
    status: DiscountRateEvidenceStatus
    issues: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("evidence_id", "provider", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.value, "capital value", nonnegative=self.component is CapitalComponent.DEBT,
                positive=self.component is CapitalComponent.EQUITY)
        object.__setattr__(self, "issues", tuple(dict.fromkeys(_require_text(x, "issue") for x in self.issues)))
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE:
            if self.value is None or self.observation_date is None or not self.source_observation_id or not self.provenance:
                raise ValueError("eligible capital value requires complete source evidence")
            if self.observation_date > self.analysis_as_of.date():
                raise ValueError("eligible capital value cannot be future dated")
            if self.component is CapitalComponent.EQUITY and self.definition is not DebtValueDefinition.MARKET_VALUE:
                raise ValueError("equity evidence requires market value")


@dataclass(frozen=True, slots=True)
class CompanyClassEligibilityEvidence:
    evidence_id: str
    scope: CompanyClassScope
    status: DiscountRateEvidenceStatus
    issuer_domicile: str | None
    sector: str | None
    industry: str | None
    company_type: str | None
    market_cap_evidence_id: str | None
    market_cap_value: float | None
    market_cap_currency: str | None
    analysis_as_of: datetime
    issues: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("evidence_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.market_cap_value, "market_cap_value", nonnegative=True)
        _validate_currency(self.market_cap_currency, "market_cap_currency", required=False)
        object.__setattr__(self, "issues", tuple(dict.fromkeys(_require_text(x, "issue") for x in self.issues)))
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE:
            if self.scope is not CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING:
                raise ValueError("eligible company class requires the approved scope")
            if not all((self.issuer_domicile, self.sector, self.industry, self.company_type,
                        self.market_cap_evidence_id)) or self.market_cap_value is None:
                raise ValueError("eligible company class requires complete canonical evidence")


@dataclass(frozen=True, slots=True)
class CoverageNumeratorEligibilityEvidence:
    evidence_id: str
    definition: CoverageNumeratorDefinition
    status: DiscountRateEvidenceStatus
    provider_metric: str
    provider_definition: str
    methodology_name: str
    methodology_definition: str
    issues: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "provider_metric", "provider_definition",
            "methodology_name", "methodology_definition", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        object.__setattr__(self, "issues", tuple(dict.fromkeys(_require_text(x, "issue") for x in self.issues)))
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and (
            self.definition is not CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT
        ):
            raise ValueError("eligible equivalence evidence must remain method specific")


@dataclass(frozen=True, slots=True)
class InterestCoverageEvidence:
    evidence_id: str
    ebit_observation_id: str | None
    interest_expense_observation_id: str | None
    fiscal_period_end: date | None
    ebit: float | None
    interest_expense: float | None
    ratio: float | None
    currency: str
    analysis_as_of: datetime
    status: DiscountRateEvidenceStatus
    issues: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    policy_id: str
    numerator_observation_id: str | None = None
    numerator_metric: str | None = None
    numerator_definition: CoverageNumeratorDefinition = CoverageNumeratorDefinition.UNAVAILABLE
    numerator_value: float | None = None
    numerator_eligibility_evidence_id: str | None = None

    def __post_init__(self) -> None:
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        for name in ("ebit", "ratio"):
            _finite(getattr(self, name), name)
        _finite(self.interest_expense, "interest_expense")
        _finite(self.numerator_value, "numerator_value")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and (
            self.ratio is None or self.fiscal_period_end is None
            or not (self.ebit_observation_id or self.numerator_observation_id)
            or not self.interest_expense_observation_id or not self.provenance
        ):
            raise ValueError("eligible coverage requires aligned source observations")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and self.interest_expense <= 0:
            raise ValueError("eligible coverage requires positive interest-expense magnitude")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and self.numerator_definition is CoverageNumeratorDefinition.UNAVAILABLE:
            raise ValueError("eligible coverage requires an explicit numerator definition")
        if self.numerator_definition is CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT and not self.numerator_eligibility_evidence_id:
            raise ValueError("method-specific operating-profit coverage requires eligibility evidence")


@dataclass(frozen=True, slots=True)
class CostOfDebtEvidence:
    evidence_id: str
    risk_free_evidence_id: str | None
    interest_coverage_evidence_id: str | None
    synthetic_rating_band_id: str | None
    synthetic_rating: str | None
    default_spread: float | None
    pretax_cost_of_debt: float | None
    currency: str
    analysis_as_of: datetime
    status: DiscountRateEvidenceStatus
    issues: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    policy_id: str

    def __post_init__(self) -> None:
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _rate(self.default_spread, "default_spread")
        _rate(self.pretax_cost_of_debt, "pretax_cost_of_debt")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and None in (
            self.risk_free_evidence_id, self.interest_coverage_evidence_id,
            self.synthetic_rating_band_id, self.synthetic_rating,
            self.default_spread, self.pretax_cost_of_debt,
        ):
            raise ValueError("eligible cost of debt requires the complete sourced chain")


@dataclass(frozen=True, slots=True)
class MarginalTaxRateEvidence:
    evidence_id: str
    jurisdiction: str | None
    value: float | None
    source_name: str
    source_date: date | None
    analysis_as_of: datetime
    methodology: str
    underlying_source: str
    status: DiscountRateEvidenceStatus
    issues: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_name", "methodology", "underlying_source", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _rate(self.value, "marginal tax rate")
        if self.status is DiscountRateEvidenceStatus.ELIGIBLE and (
            not self.jurisdiction or self.value is None or self.source_date is None or not self.provenance
        ):
            raise ValueError("eligible marginal tax evidence requires complete source evidence")


@dataclass(frozen=True, slots=True)
class OtherEnterpriseClaimsEvidence:
    evidence_id: str
    value: float | None
    currency: str
    observation_date: date | None
    analysis_as_of: datetime
    status: OtherClaimsStatus
    source_observation_ids: tuple[str, ...]
    issues: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    policy_id: str

    def __post_init__(self) -> None:
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.value, "other enterprise claims")
        if self.status is OtherClaimsStatus.VERIFIED_ZERO and self.value != 0:
            raise ValueError("verified-zero claims require exact zero")
        if self.status is OtherClaimsStatus.UNRESOLVED_NONZERO and (self.value is None or self.value == 0):
            raise ValueError("unresolved nonzero claims require a nonzero residual")


@dataclass(frozen=True, slots=True)
class CapitalStructureWeights:
    evidence_id: str
    equity_value_evidence_id: str
    debt_value_evidence_id: str
    equity_value: float
    debt_value: float
    equity_weight: float
    debt_weight: float
    currency: str
    analysis_as_of: datetime
    debt_definition: DebtValueDefinition
    status: DiscountRateReadinessStatus
    issues: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        _validate_currency(self.currency, "currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.equity_value, "equity_value", positive=True)
        _finite(self.debt_value, "debt_value", nonnegative=True)
        for name in ("equity_weight", "debt_weight"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if not math.isclose(self.equity_weight + self.debt_weight, 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError("capital weights must sum to one")


@dataclass(frozen=True, slots=True)
class WaccResult:
    result_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    cost_of_equity_result_id: str
    equity_value_evidence_id: str | None
    debt_value_evidence_id: str | None
    interest_coverage_evidence_id: str | None
    cost_of_debt_evidence_id: str | None
    marginal_tax_evidence_id: str | None
    capital_structure_weights_id: str | None
    other_claims_evidence_id: str | None
    cost_of_equity: float | None
    pretax_cost_of_debt: float | None
    marginal_tax_rate: float | None
    after_tax_cost_of_debt: float | None
    equity_weight: float | None
    debt_weight: float | None
    value: float | None
    status: DiscountRateReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    readiness_id: str

    def __post_init__(self) -> None:
        for name in ("result_id", "security_id", "issuer_id", "cost_of_equity_result_id", "readiness_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        for name in ("cost_of_equity", "pretax_cost_of_debt", "marginal_tax_rate", "after_tax_cost_of_debt", "value"):
            _rate(getattr(self, name), name)
        if self.status is DiscountRateReadinessStatus.READY and self.value is None:
            raise ValueError("ready WACC requires a value")
        if self.status is not DiscountRateReadinessStatus.READY and self.value is not None:
            raise ValueError("non-ready WACC cannot expose a value")
