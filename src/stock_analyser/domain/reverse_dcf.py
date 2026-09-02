"""Immutable, non-valuing evidence contracts for reverse-DCF readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math

from .enums import (
    DiscountRateReadinessStatus, EstimateCase, Frequency, MetricUnit, ReverseDcfDiscountRateSource,
    ReverseDcfExecutionInputSource, ReverseDcfExecutionMode, ReverseDcfFormulation,
    ReverseDcfPublicationEligibility, ReverseDcfReadinessStage, ReverseDcfReadinessStatus,
    ReverseDcfScenarioAssumptionType, ReverseDcfScenarioSourceCategory,
    ReverseDcfSolverStatus, ReverseDcfTimingConvention, SalesToCapitalSourceType,
    TerminalMarginPolicy,
)
from .models import Provenance, _require_text, _validate_aware_datetime, _validate_currency


def stable_reverse_dcf_id(prefix: str, *parts: object) -> str:
    return f"{prefix}:" + sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def _finite(value: float | None, name: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")


def _texts(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_require_text(item, name) for item in values))


def _provenance(values: tuple[Provenance, ...]) -> tuple[Provenance, ...]:
    result = tuple(dict.fromkeys(values))
    if any(not isinstance(item, Provenance) for item in result):
        raise ValueError("provenance must contain Provenance records")
    return result


@dataclass(frozen=True, slots=True)
class ForwardOperatingPeriod:
    period_id: str
    target_security_id: str
    target_issuer_id: str
    fiscal_period: str
    fiscal_year: int
    fiscal_period_start: date
    fiscal_period_end: date
    estimate_case: EstimateCase
    revenue: float | None
    revenue_observation_id: str | None
    ebit: float | None
    ebit_observation_id: str | None
    ebitda: float | None
    ebitda_observation_id: str | None
    operating_margin: float | None
    revenue_growth: float | None
    growth_base_observation_id: str | None
    estimate_as_of: datetime
    analysis_as_of: datetime
    currency: str
    unit: MetricUnit
    revenue_analyst_count: int | None
    ebit_analyst_count: int | None
    ebitda_analyst_count: int | None
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("period_id", "target_security_id", "target_issuer_id", "fiscal_period"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if not self.fiscal_period.startswith("FY") or not self.fiscal_period[2:].isdigit():
            raise ValueError("fiscal_period must be an explicit FY horizon label")
        if self.fiscal_period_start > self.fiscal_period_end:
            raise ValueError("fiscal period start must not follow its end")
        _validate_aware_datetime(self.estimate_as_of, "estimate_as_of")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if self.estimate_as_of > self.analysis_as_of:
            raise ValueError("estimate snapshot cannot be after analysis_as_of")
        if self.fiscal_period_end <= self.analysis_as_of.date():
            raise ValueError("forward operating period must end after analysis_as_of")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("reverse-DCF operating periods use AVERAGE consensus only")
        if self.unit is not MetricUnit.CURRENCY:
            raise ValueError("forward operating levels require base currency units")
        _validate_currency(self.currency, "currency")
        _finite(self.revenue, "revenue", positive=self.revenue is not None)
        _finite(self.ebit, "ebit")
        _finite(self.ebitda, "ebitda")
        _finite(self.operating_margin, "operating_margin")
        _finite(self.revenue_growth, "revenue_growth")
        for value, observation_id, name in (
            (self.revenue, self.revenue_observation_id, "revenue"),
            (self.ebit, self.ebit_observation_id, "ebit"),
            (self.ebitda, self.ebitda_observation_id, "ebitda"),
        ):
            if (value is None) != (observation_id is None):
                raise ValueError(f"{name} value and observation ID must be present together")
        if self.operating_margin is not None and (self.revenue is None or self.ebit is None):
            raise ValueError("operating margin requires revenue and EBIT")
        if self.revenue_growth is not None and (self.revenue is None or not self.growth_base_observation_id):
            raise ValueError("revenue growth requires current and base revenue evidence")
        for name in ("revenue_analyst_count", "ebit_analyst_count", "ebitda_analyst_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and (
            self.revenue is None or self.ebit is None or self.operating_margin is None
        ):
            raise ValueError("ready period requires revenue, EBIT, and derived margin")


@dataclass(frozen=True, slots=True)
class ForwardOperatingTrajectory:
    trajectory_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    estimate_case: EstimateCase
    periods: tuple[ForwardOperatingPeriod, ...]
    period_count: int
    first_period: str | None
    last_period: str | None
    missing_fiscal_years: tuple[int, ...]
    revenue_status: ReverseDcfReadinessStatus
    ebit_status: ReverseDcfReadinessStatus
    margin_status: ReverseDcfReadinessStatus
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("trajectory_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("forward operating trajectory requires AVERAGE consensus")
        periods = tuple(self.periods)
        if periods != tuple(sorted(periods, key=lambda item: item.fiscal_period_end)):
            raise ValueError("forward periods must be ordered by fiscal period end")
        if self.period_count != len(periods):
            raise ValueError("period_count must match periods")
        if periods:
            if self.first_period != periods[0].fiscal_period or self.last_period != periods[-1].fiscal_period:
                raise ValueError("trajectory boundary labels must match its periods")
        elif self.first_period is not None or self.last_period is not None:
            raise ValueError("empty trajectory cannot expose boundary labels")
        object.__setattr__(self, "periods", periods)
        object.__setattr__(self, "missing_fiscal_years", tuple(dict.fromkeys(self.missing_fiscal_years)))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and (len(periods) < 2 or self.missing_fiscal_years):
            raise ValueError("ready trajectory requires at least two consecutive periods")


@dataclass(frozen=True, slots=True)
class ReverseDcfActualBase:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    fiscal_year: int | None
    period_start: date | None
    period_end: date | None
    currency: str
    revenue: float | None
    revenue_observation_id: str | None
    ebit: float | None
    ebit_observation_id: str | None
    operating_income: float | None
    operating_income_observation_id: str | None
    ebitda: float | None
    ebitda_observation_id: str | None
    status: ReverseDcfReadinessStatus
    ebit_continuity_status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("evidence_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        for name in ("revenue", "ebit", "operating_income", "ebitda"):
            _finite(getattr(self, name), name, positive=name == "revenue" and getattr(self, name) is not None)
        if self.status is ReverseDcfReadinessStatus.READY and (
            self.revenue is None or self.period_end is None or not self.revenue_observation_id
        ):
            raise ValueError("ready actual base requires canonical annual revenue")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class MarketEnterpriseValueAnchor:
    anchor_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    value: float | None
    currency: str
    observation_date: date | None
    source_observation_id: str | None
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("anchor_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        _finite(self.value, "enterprise value", positive=self.value is not None)
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and (
            self.value is None or self.observation_date is None or not self.source_observation_id or not self.provenance
        ):
            raise ValueError("ready market anchor requires complete canonical evidence")


@dataclass(frozen=True, slots=True)
class ReverseDcfReinvestmentReadiness:
    readiness_id: str
    analysis_as_of: datetime
    forward_capex_status: ReverseDcfReadinessStatus
    forward_da_status: ReverseDcfReadinessStatus
    forward_change_nwc_status: ReverseDcfReadinessStatus
    external_fcff_status: ReverseDcfReadinessStatus
    external_fcfe_status: ReverseDcfReadinessStatus
    generic_fcf_status: ReverseDcfReadinessStatus
    historical_reinvestment_status: ReverseDcfReadinessStatus
    sales_to_capital_status: ReverseDcfReadinessStatus
    methodology_status: ReverseDcfReadinessStatus
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("readiness_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and self.methodology_status is not ReverseDcfReadinessStatus.READY:
            raise ValueError("ready reinvestment requires an approved methodology")


@dataclass(frozen=True, slots=True)
class ReverseDcfTerminalReadiness:
    readiness_id: str
    valuation_currency: str
    analysis_as_of: datetime
    terminal_growth_policy_status: ReverseDcfReadinessStatus
    terminal_margin_policy_status: ReverseDcfReadinessStatus
    steady_state_reinvestment_status: ReverseDcfReadinessStatus
    discount_rate_compatibility_status: ReverseDcfReadinessStatus
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "readiness_id", _require_text(self.readiness_id, "readiness_id"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        _validate_currency(self.valuation_currency, "valuation_currency")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))


@dataclass(frozen=True, slots=True)
class ReverseDcfOperatingReadiness:
    readiness_id: str
    actual_base_status: ReverseDcfReadinessStatus
    forward_revenue_status: ReverseDcfReadinessStatus
    forward_ebit_status: ReverseDcfReadinessStatus
    forward_margin_status: ReverseDcfReadinessStatus
    actual_to_forward_ebit_continuity_status: ReverseDcfReadinessStatus
    trajectory_status: ReverseDcfReadinessStatus
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for name in ("readiness_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))


@dataclass(frozen=True, slots=True)
class ReverseDcfFormulationReadiness:
    formulation: ReverseDcfFormulation
    status: ReverseDcfReadinessStatus
    blocking_stages: tuple[ReverseDcfReadinessStage, ...]
    issues: tuple[str, ...]
    supporting_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocking_stages", tuple(dict.fromkeys(self.blocking_stages)))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        if self.status is ReverseDcfReadinessStatus.READY and self.blocking_stages:
            raise ValueError("ready formulation cannot have blocking stages")


@dataclass(frozen=True, slots=True)
class ReverseDcfReadiness:
    readiness_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    actual_base_period: date | None
    actual_base_status: ReverseDcfReadinessStatus
    forward_trajectory_id: str | None
    forward_period_count: int
    forward_trajectory_status: ReverseDcfReadinessStatus
    market_enterprise_value_anchor_id: str | None
    market_anchor_status: ReverseDcfReadinessStatus
    operating_tax_status: ReverseDcfReadinessStatus
    reinvestment_status: ReverseDcfReadinessStatus
    discount_rate_status: ReverseDcfReadinessStatus
    terminal_policy_status: ReverseDcfReadinessStatus
    formulation_readiness: tuple[ReverseDcfFormulationReadiness, ...]
    supported_formulations: tuple[ReverseDcfFormulation, ...]
    blocked_formulations: tuple[ReverseDcfFormulation, ...]
    earliest_blocking_stage: ReverseDcfReadinessStage
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("readiness_id", "target_security_id", "target_issuer_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        if isinstance(self.forward_period_count, bool) or self.forward_period_count < 0:
            raise ValueError("forward_period_count must be non-negative")
        formulations = tuple(self.formulation_readiness)
        if len({item.formulation for item in formulations}) != len(formulations):
            raise ValueError("formulation readiness must be unique")
        supported = tuple(dict.fromkeys(self.supported_formulations))
        blocked = tuple(dict.fromkeys(self.blocked_formulations))
        if set(supported) & set(blocked):
            raise ValueError("a formulation cannot be both supported and blocked")
        object.__setattr__(self, "formulation_readiness", formulations)
        object.__setattr__(self, "supported_formulations", supported)
        object.__setattr__(self, "blocked_formulations", blocked)
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and not supported:
            raise ValueError("ready reverse DCF requires at least one solver-ready formulation")


@dataclass(frozen=True, slots=True)
class SalesToCapitalEvidence:
    evidence_id: str
    target_security_id: str
    target_issuer_id: str
    value: float | None
    economic_definition: str
    source_type: SalesToCapitalSourceType
    source_name: str
    source_date: date | None
    analysis_as_of: datetime
    methodology: str
    currency_scope: str | None
    industry_scope: str | None
    company_scope: str | None
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "evidence_id", "target_security_id", "target_issuer_id", "economic_definition",
            "source_name", "methodology", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.value, "sales-to-capital", positive=self.value is not None)
        if self.currency_scope is not None:
            _validate_currency(self.currency_scope, "currency_scope")
        for name in ("industry_scope", "company_scope"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        eligible_types = {
            SalesToCapitalSourceType.VERIFIED_EXTERNAL,
            SalesToCapitalSourceType.VERIFIED_COMPANY_DERIVED,
            SalesToCapitalSourceType.CONFIGURED_EXTERNAL,
        }
        if self.status is ReverseDcfReadinessStatus.READY:
            if (
                self.value is None or self.source_type not in eligible_types
                or self.source_date is None or self.source_date > self.analysis_as_of.date()
                or not self.provenance
            ):
                raise ValueError("ready sales-to-capital evidence requires eligible dated provenance")
        if self.source_type is SalesToCapitalSourceType.CONFIGURED_EXTERNAL and (
            self.value is None or self.source_date is None or not self.provenance
            or not (self.company_scope or self.industry_scope)
        ):
            raise ValueError("configured external sales-to-capital requires value, date, scope, and provenance")
        if self.source_type in {SalesToCapitalSourceType.UNVERIFIED, SalesToCapitalSourceType.UNAVAILABLE} and (
            self.status is ReverseDcfReadinessStatus.READY
        ):
            raise ValueError("unverified or unavailable sales-to-capital cannot be ready")


@dataclass(frozen=True, slots=True)
class ReverseDcfCashFlowPeriod:
    period_id: str
    target_security_id: str
    target_issuer_id: str
    fiscal_period: str
    fiscal_year: int
    period_end: date
    discount_period_index: int
    revenue: float
    ebit: float
    ebit_margin: float
    tax_rate: float
    nopat: float
    revenue_change: float | None
    sales_to_capital: float | None
    reinvestment: float | None
    fcff: float | None
    revenue_observation_id: str
    ebit_observation_id: str
    revenue_change_base_observation_id: str | None
    tax_evidence_id: str
    sales_to_capital_evidence_id: str | None
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "period_id", "target_security_id", "target_issuer_id", "fiscal_period",
            "revenue_observation_id", "ebit_observation_id", "tax_evidence_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if self.discount_period_index < 1 or isinstance(self.discount_period_index, bool):
            raise ValueError("discount period index must be a positive integer")
        _finite(self.revenue, "revenue", positive=True)
        for name in ("ebit", "ebit_margin", "tax_rate", "nopat", "revenue_change", "reinvestment", "fcff"):
            _finite(getattr(self, name), name)
        _finite(self.sales_to_capital, "sales_to_capital", positive=self.sales_to_capital is not None)
        if not 0 <= self.tax_rate <= 1:
            raise ValueError("tax rate must be between zero and one")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and any(
            value is None for value in (
                self.revenue_change, self.sales_to_capital, self.reinvestment, self.fcff,
                self.revenue_change_base_observation_id, self.sales_to_capital_evidence_id,
            )
        ):
            raise ValueError("ready cash-flow period requires complete reinvestment and FCFF evidence")


@dataclass(frozen=True, slots=True)
class ReverseDcfCashFlowPath:
    path_id: str
    target_security_id: str
    target_issuer_id: str
    trajectory_id: str
    analysis_as_of: datetime
    valuation_currency: str
    timing_convention: ReverseDcfTimingConvention
    periods: tuple[ReverseDcfCashFlowPeriod, ...]
    period_count: int
    fy1_reinvestment_base_status: ReverseDcfReadinessStatus
    tax_status: ReverseDcfReadinessStatus
    sales_to_capital_status: ReverseDcfReadinessStatus
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("path_id", "target_security_id", "target_issuer_id", "trajectory_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        periods = tuple(self.periods)
        if self.period_count != len(periods):
            raise ValueError("cash-flow period_count must match periods")
        if tuple(item.discount_period_index for item in periods) != tuple(range(1, len(periods) + 1)):
            raise ValueError("cash-flow periods must use consecutive one-based annual indices")
        object.__setattr__(self, "periods", periods)
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfReadinessStatus.READY and (
            not periods or any(item.status is not ReverseDcfReadinessStatus.READY for item in periods)
            or self.fy1_reinvestment_base_status is not ReverseDcfReadinessStatus.READY
            or self.tax_status is not ReverseDcfReadinessStatus.READY
            or self.sales_to_capital_status is not ReverseDcfReadinessStatus.READY
        ):
            raise ValueError("ready cash-flow path requires every explicit annual FCFF period")


@dataclass(frozen=True, slots=True)
class MarketImpliedTerminalGrowthResult:
    result_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    formulation: ReverseDcfFormulation
    trajectory_id: str
    cash_flow_path_id: str
    market_anchor_id: str | None
    tax_evidence_id: str | None
    sales_to_capital_evidence_id: str | None
    wacc_result_id: str | None
    discount_rate_input_id: str | None
    discount_rate_source: ReverseDcfDiscountRateSource | None
    scenario_discount_rate_assumption_id: str | None
    terminal_margin_policy: TerminalMarginPolicy
    timing_convention: ReverseDcfTimingConvention
    search_lower_bound: float
    search_upper_bound: float | None
    final_bracket_lower: float | None
    final_bracket_upper: float | None
    lower_bound_function_value: float | None
    upper_bound_function_value: float | None
    implied_terminal_growth: float | None
    terminal_margin: float | None
    terminal_revenue: float | None
    terminal_ebit: float | None
    terminal_nopat: float | None
    terminal_reinvestment: float | None
    terminal_fcff: float | None
    terminal_value: float | None
    explicit_fcff_present_value: float | None
    terminal_value_present_value: float | None
    modeled_enterprise_value: float | None
    observed_market_enterprise_value: float | None
    residual: float | None
    iterations: int
    status: ReverseDcfSolverStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "result_id", "target_security_id", "target_issuer_id", "trajectory_id",
            "cash_flow_path_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for name in (
            "market_anchor_id", "tax_evidence_id", "sales_to_capital_evidence_id",
            "wacc_result_id", "discount_rate_input_id", "scenario_discount_rate_assumption_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_text(value, name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        if self.formulation is not ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_GROWTH:
            raise ValueError("10B implements only market-implied terminal growth")
        for name in (
            "search_lower_bound", "search_upper_bound", "final_bracket_lower",
            "final_bracket_upper", "lower_bound_function_value", "upper_bound_function_value",
            "implied_terminal_growth", "terminal_margin", "terminal_revenue", "terminal_ebit",
            "terminal_nopat", "terminal_reinvestment", "terminal_fcff", "terminal_value",
            "explicit_fcff_present_value", "terminal_value_present_value",
            "modeled_enterprise_value", "observed_market_enterprise_value", "residual",
        ):
            _finite(getattr(self, name), name)
        if isinstance(self.iterations, bool) or self.iterations < 0:
            raise ValueError("iterations must be a non-negative integer")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "supporting_ids", _texts(self.supporting_ids, "supporting_id"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.status is ReverseDcfSolverStatus.SOLVED:
            required = (
                self.search_upper_bound, self.implied_terminal_growth, self.terminal_margin,
                self.terminal_revenue, self.terminal_ebit, self.terminal_nopat,
                self.terminal_reinvestment, self.terminal_fcff, self.terminal_value,
                self.explicit_fcff_present_value, self.terminal_value_present_value,
                self.modeled_enterprise_value, self.observed_market_enterprise_value, self.residual,
            )
            if any(value is None for value in required):
                raise ValueError("solved terminal growth requires complete numerical audit fields")
            if any(value is None for value in (
                self.market_anchor_id, self.tax_evidence_id,
                self.sales_to_capital_evidence_id, self.discount_rate_input_id,
            )):
                raise ValueError("solved terminal growth requires every source evidence ID")
            if self.discount_rate_source is ReverseDcfDiscountRateSource.PRODUCTION_WACC:
                if self.wacc_result_id is None or self.scenario_discount_rate_assumption_id is not None:
                    raise ValueError("production discount rate requires only a WACC result ID")
            elif self.discount_rate_source is ReverseDcfDiscountRateSource.SCENARIO_WACC:
                if self.scenario_discount_rate_assumption_id is None or self.wacc_result_id is not None:
                    raise ValueError("scenario discount rate requires only a scenario assumption ID")
            else:
                raise ValueError("solved terminal growth requires a controlled discount-rate source")
        elif self.implied_terminal_growth is not None:
            raise ValueError("non-solved result cannot expose implied terminal growth")


@dataclass(frozen=True, slots=True)
class ReverseDcfScenarioAssumption:
    assumption_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    assumption_type: ReverseDcfScenarioAssumptionType
    value: float | None
    unit: MetricUnit
    currency: str | None
    frequency: Frequency | None
    fiscal_year: int | None
    period_end: date | None
    source_category: ReverseDcfScenarioSourceCategory
    source_label: str
    source_date: date | None
    methodology_label: str
    rationale: str
    entered_by: str
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "assumption_id", "target_security_id", "target_issuer_id", "source_label",
            "methodology_label", "rationale", "entered_by", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.value, "scenario assumption")
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.source_date is not None and self.source_date > self.analysis_as_of.date():
            raise ValueError("scenario source date cannot be after analysis_as_of")
        if self.source_category is ReverseDcfScenarioSourceCategory.EXTERNAL_RESEARCH and self.source_date is None:
            raise ValueError("external-research scenario assumptions require source_date")
        if self.status is ReverseDcfReadinessStatus.READY and (
            self.value is None or not self.provenance
        ):
            raise ValueError("ready scenario assumptions require a value and explicit provenance")
        if self.assumption_type is ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE:
            if (
                self.value is None or self.value <= 0 or self.unit is not MetricUnit.CURRENCY
                or self.currency is None or self.frequency is not Frequency.ANNUAL
                or self.fiscal_year is None or self.period_end is None
            ):
                raise ValueError("preceding annual revenue requires positive base-currency annual-period evidence")
            _validate_currency(self.currency, "currency")
        elif self.assumption_type is ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL:
            if self.value is None or self.value <= 0 or self.unit is not MetricUnit.RATIO:
                raise ValueError("scenario sales-to-capital must be a positive dimensionless ratio")
            if self.currency is not None or self.frequency is not None or self.fiscal_year is not None or self.period_end is not None:
                raise ValueError("scenario sales-to-capital cannot carry monetary-period semantics")
        elif self.assumption_type is ReverseDcfScenarioAssumptionType.WACC:
            if (
                self.value is None or not 0 < self.value < 1
                or self.unit is not MetricUnit.PERCENT_DECIMAL
            ):
                raise ValueError("scenario WACC must be a positive decimal rate below one")
            if self.currency is not None or self.frequency is not None or self.fiscal_year is not None or self.period_end is not None:
                raise ValueError("scenario WACC cannot carry monetary-period semantics")


@dataclass(frozen=True, slots=True)
class ReverseDcfPrecedingRevenueInput:
    input_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    value: float
    currency: str
    fiscal_year: int
    period_end: date
    source: ReverseDcfExecutionInputSource
    canonical_actual_base_id: str | None
    scenario_assumption_id: str | None
    status: ReverseDcfReadinessStatus
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("input_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        _finite(self.value, "preceding revenue", positive=True)
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.source is ReverseDcfExecutionInputSource.CANONICAL_EVIDENCE:
            if self.canonical_actual_base_id is None or self.scenario_assumption_id is not None:
                raise ValueError("canonical preceding revenue requires only an actual-base ID")
        elif self.canonical_actual_base_id is not None or self.scenario_assumption_id is None:
            raise ValueError("scenario preceding revenue requires only an assumption ID")


@dataclass(frozen=True, slots=True)
class ReverseDcfSalesToCapitalInput:
    input_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    value: float
    source: ReverseDcfExecutionInputSource
    canonical_evidence_id: str | None
    scenario_assumption_id: str | None
    status: ReverseDcfReadinessStatus
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("input_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _finite(self.value, "sales-to-capital", positive=True)
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.source is ReverseDcfExecutionInputSource.CANONICAL_EVIDENCE:
            if self.canonical_evidence_id is None or self.scenario_assumption_id is not None:
                raise ValueError("canonical sales-to-capital requires only a canonical evidence ID")
        elif self.canonical_evidence_id is not None or self.scenario_assumption_id is None:
            raise ValueError("scenario sales-to-capital requires only an assumption ID")


@dataclass(frozen=True, slots=True)
class ReverseDcfDiscountRateInput:
    input_id: str
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    value: float
    source: ReverseDcfDiscountRateSource
    production_wacc_result_id: str | None
    scenario_assumption_id: str | None
    production_wacc_status: DiscountRateReadinessStatus
    status: ReverseDcfReadinessStatus
    policy_id: str
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("input_id", "target_security_id", "target_issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        _finite(self.value, "discount rate", positive=True)
        if self.value >= 1:
            raise ValueError("discount rate must use decimal-rate semantics below one")
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        if self.source is ReverseDcfDiscountRateSource.PRODUCTION_WACC:
            if (
                self.production_wacc_result_id is None or self.scenario_assumption_id is not None
                or self.production_wacc_status is not DiscountRateReadinessStatus.READY
            ):
                raise ValueError("production discount rate requires a READY WACC result")
        elif self.production_wacc_result_id is not None or self.scenario_assumption_id is None:
            raise ValueError("scenario discount rate requires only a scenario assumption ID")


@dataclass(frozen=True, slots=True)
class ReverseDcfExecutionInputs:
    execution_input_id: str
    execution_mode: ReverseDcfExecutionMode
    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    forward_trajectory_id: str
    market_anchor_id: str
    tax_evidence_id: str | None
    terminal_margin_policy: TerminalMarginPolicy
    preceding_revenue_input_id: str | None
    sales_to_capital_input_id: str | None
    discount_rate_input_id: str | None
    canonical_actual_base_id: str | None
    canonical_sales_to_capital_evidence_id: str | None
    production_wacc_result_id: str | None
    canonical_actual_base_status: ReverseDcfReadinessStatus
    canonical_sales_to_capital_status: ReverseDcfReadinessStatus
    production_wacc_status: DiscountRateReadinessStatus
    canonical_input_status: ReverseDcfReadinessStatus
    scenario_assumption_ids: tuple[str, ...]
    status: ReverseDcfReadinessStatus
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in (
            "execution_input_id", "target_security_id", "target_issuer_id",
            "forward_trajectory_id", "market_anchor_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if self.tax_evidence_id is not None:
            object.__setattr__(self, "tax_evidence_id", _require_text(self.tax_evidence_id, "tax_evidence_id"))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.valuation_currency, "valuation_currency")
        object.__setattr__(self, "scenario_assumption_ids", _texts(self.scenario_assumption_ids, "assumption_id"))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))


@dataclass(frozen=True, slots=True)
class ReverseDcfExecutionResult:
    execution_result_id: str
    execution_inputs_id: str
    execution_inputs: ReverseDcfExecutionInputs
    execution_mode: ReverseDcfExecutionMode
    solver_result: MarketImpliedTerminalGrowthResult
    canonical_input_status: ReverseDcfReadinessStatus
    scenario_assumption_ids: tuple[str, ...]
    publication_eligibility: ReverseDcfPublicationEligibility
    central_valuation_eligible: bool
    canonical_blockers: tuple[str, ...]
    final_consensus_revenue_growth: float | None
    final_consensus_ebit_margin: float | None
    scenario_assumption_labels: tuple[str, ...]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]

    def __post_init__(self) -> None:
        for name in ("execution_result_id", "execution_inputs_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if self.execution_inputs.execution_input_id != self.execution_inputs_id:
            raise ValueError("execution input reference must match the embedded contract")
        object.__setattr__(self, "scenario_assumption_ids", _texts(self.scenario_assumption_ids, "assumption_id"))
        object.__setattr__(self, "canonical_blockers", _texts(self.canonical_blockers, "canonical_blocker"))
        object.__setattr__(self, "scenario_assumption_labels", _texts(self.scenario_assumption_labels, "scenario label"))
        object.__setattr__(self, "issues", _texts(self.issues, "issue"))
        object.__setattr__(self, "warnings", _texts(self.warnings, "warning"))
        object.__setattr__(self, "policy_ids", _texts(self.policy_ids, "policy_id"))
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        _finite(self.final_consensus_revenue_growth, "final consensus revenue growth")
        _finite(self.final_consensus_ebit_margin, "final consensus EBIT margin")
        if self.central_valuation_eligible:
            raise ValueError("reverse-DCF expectation results are not central-valuation eligible")
        if self.publication_eligibility is ReverseDcfPublicationEligibility.CANONICAL and (
            self.execution_mode is not ReverseDcfExecutionMode.CANONICAL_EVIDENCE
            or self.scenario_assumption_ids
            or self.solver_result.status is not ReverseDcfSolverStatus.SOLVED
        ):
            raise ValueError("canonical publication requires a solved all-canonical execution")
        if self.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY and (
            self.execution_mode is not ReverseDcfExecutionMode.EXPLICIT_SCENARIO
            or self.solver_result.status is not ReverseDcfSolverStatus.SOLVED
        ):
            raise ValueError("scenario publication requires a solved explicit-scenario execution")
