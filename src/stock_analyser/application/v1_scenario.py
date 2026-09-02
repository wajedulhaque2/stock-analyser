"""Safe application boundary for one explicit V1 reverse-DCF scenario.

This module normalizes the three authorized human inputs, constructs the
existing provenance-bearing 10C assumptions, and delegates all economics to
``execute_reverse_dcf``.  It performs no provider access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math

from stock_analyser.domain import (
    ForwardOperatingTrajectory,
    Frequency,
    MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor,
    MetricUnit,
    Provenance,
    ReverseDcfActualBase,
    ReverseDcfExecutionMode,
    ReverseDcfExecutionResult,
    ReverseDcfScenarioAssumption,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfScenarioSourceCategory,
    SalesToCapitalEvidence,
    StockResearchReport,
    WaccResult,
)
from stock_analyser.services import (
    configure_reverse_dcf_scenario_assumption,
    execute_reverse_dcf,
)


SCENARIO_CONTEXT_POLICY_ID = "v1-12h-reverse-dcf-scenario-context"
SCENARIO_CONTROLLER_POLICY_ID = "v1-12h-explicit-scenario-controller"
SCENARIO_SOURCE_LABEL = "User-supplied V1 scenario"
SCENARIO_METHODOLOGY_LABEL = "Approved 10C explicit reverse-DCF scenario"
SCENARIO_RATIONALE = "Explicit interactive scenario assumption"
SCENARIO_ENTERED_BY = "interactive_user"
REVENUE_INPUT_SCALE = 1_000_000_000.0
REVENUE_INPUT_SCALE_LABEL = "billions"


class ReverseDcfScenarioRunStatus(str, Enum):
    VALIDATION_ERROR = "validation_error"
    EXECUTED = "executed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ReverseDcfScenarioExecutionContext:
    """Provider-independent canonical inputs retained for the current report."""

    target_security_id: str
    target_issuer_id: str
    analysis_as_of: datetime
    valuation_currency: str
    trajectory: ForwardOperatingTrajectory
    market_anchor: MarketEnterpriseValueAnchor
    operating_tax: MarginalTaxRateEvidence | None
    actual_base: ReverseDcfActualBase | None = None
    canonical_sales_to_capital: SalesToCapitalEvidence | None = None
    production_wacc: WaccResult | None = None
    policy_id: str = SCENARIO_CONTEXT_POLICY_ID

    def __post_init__(self) -> None:
        for name in ("target_security_id", "target_issuer_id", "valuation_currency", "policy_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.analysis_as_of.tzinfo is None or self.analysis_as_of.utcoffset() is None:
            raise ValueError("analysis_as_of must be timezone-aware")
        if not isinstance(self.trajectory, ForwardOperatingTrajectory):
            raise TypeError("trajectory must be ForwardOperatingTrajectory")
        if not isinstance(self.market_anchor, MarketEnterpriseValueAnchor):
            raise TypeError("market_anchor must be MarketEnterpriseValueAnchor")
        if not self.trajectory.periods:
            raise ValueError("scenario context requires at least one forward annual period")
        self._validate_bound_object(self.trajectory, currency_name="valuation_currency")
        self._validate_bound_object(self.market_anchor, currency_name="currency")
        if self.operating_tax is not None:
            if not isinstance(self.operating_tax, MarginalTaxRateEvidence):
                raise TypeError("operating_tax must be MarginalTaxRateEvidence")
            if self.operating_tax.analysis_as_of != self.analysis_as_of:
                raise ValueError("operating tax must match scenario analysis snapshot")
        if self.actual_base is not None:
            if not isinstance(self.actual_base, ReverseDcfActualBase):
                raise TypeError("actual_base must be ReverseDcfActualBase")
            self._validate_bound_object(self.actual_base, currency_name="currency")
        if self.canonical_sales_to_capital is not None:
            if not isinstance(self.canonical_sales_to_capital, SalesToCapitalEvidence):
                raise TypeError("canonical_sales_to_capital must be SalesToCapitalEvidence")
            self._validate_bound_object(self.canonical_sales_to_capital, currency_name="currency_scope", optional_currency=True)
        if self.production_wacc is not None:
            if not isinstance(self.production_wacc, WaccResult):
                raise TypeError("production_wacc must be WaccResult")
            if (
                self.production_wacc.security_id != self.target_security_id
                or self.production_wacc.issuer_id != self.target_issuer_id
                or self.production_wacc.analysis_as_of != self.analysis_as_of
                or self.production_wacc.valuation_currency != self.valuation_currency
            ):
                raise ValueError("production WACC must match scenario target, snapshot, and currency")
        # Validate that the required annual predecessor is representable.
        _ = self.required_preceding_period_end

    def _validate_bound_object(
        self,
        value: object,
        *,
        currency_name: str,
        optional_currency: bool = False,
    ) -> None:
        if (
            getattr(value, "target_security_id") != self.target_security_id
            or getattr(value, "target_issuer_id") != self.target_issuer_id
            or getattr(value, "analysis_as_of") != self.analysis_as_of
        ):
            raise ValueError("scenario context object must match target identity and snapshot")
        currency = getattr(value, currency_name)
        if currency is not None or not optional_currency:
            if currency != self.valuation_currency:
                raise ValueError("scenario context object must match valuation currency")

    @property
    def required_preceding_fiscal_year(self) -> int:
        return self.trajectory.periods[0].fiscal_year - 1

    @property
    def required_preceding_period_end(self) -> date:
        first = self.trajectory.periods[0].fiscal_period_end
        try:
            return date(self.required_preceding_fiscal_year, first.month, first.day)
        except ValueError as error:
            raise ValueError("required preceding annual fiscal period is not representable") from error

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return (
            self.target_security_id,
            self.target_issuer_id,
            self.analysis_as_of.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class ReverseDcfScenarioFormInput:
    wacc_percent: str = ""
    sales_to_capital: str = ""
    preceding_revenue_billions: str = ""


@dataclass(frozen=True, slots=True)
class ReverseDcfScenarioRunResult:
    status: ReverseDcfScenarioRunStatus
    context_key: tuple[str, str, str]
    assumptions: tuple[ReverseDcfScenarioAssumption, ...] = ()
    execution_result: ReverseDcfExecutionResult | None = None
    validation_errors: tuple[str, ...] = ()
    safe_message: str | None = None
    policy_id: str = SCENARIO_CONTROLLER_POLICY_ID

    def __post_init__(self) -> None:
        if len(self.context_key) != 3 or any(not str(item).strip() for item in self.context_key):
            raise ValueError("context_key must contain target security, issuer, and snapshot")
        if any(not isinstance(item, ReverseDcfScenarioAssumption) for item in self.assumptions):
            raise TypeError("assumptions must contain ReverseDcfScenarioAssumption")
        if self.status is ReverseDcfScenarioRunStatus.EXECUTED:
            if self.execution_result is None or len(self.assumptions) != 3:
                raise ValueError("executed scenario requires one result and exactly three assumptions")
            if self.execution_result.execution_mode is not ReverseDcfExecutionMode.EXPLICIT_SCENARIO:
                raise ValueError("interactive scenario must use explicit-scenario execution")
        elif self.execution_result is not None or self.assumptions:
            raise ValueError("non-executed scenario cannot retain assumptions or execution output")
        if self.validation_errors and self.status is not ReverseDcfScenarioRunStatus.VALIDATION_ERROR:
            raise ValueError("validation errors require validation-error status")
        if self.safe_message is not None and not self.safe_message.strip():
            raise ValueError("safe_message must be non-empty when supplied")


def _parse_required_positive(text: object, *, label: str) -> tuple[float | None, str | None]:
    value_text = str(text or "").strip()
    if not value_text:
        return None, f"{label} is required."
    try:
        value = float(value_text)
    except (TypeError, ValueError):
        return None, f"{label} must be a finite positive number."
    if not math.isfinite(value) or value <= 0:
        return None, f"{label} must be a finite positive number."
    return value, None


def normalize_reverse_dcf_scenario_input(
    values: ReverseDcfScenarioFormInput,
) -> tuple[tuple[float, float, float] | None, tuple[str, ...]]:
    """Normalize percent/billions display values; perform no economic calculation."""
    wacc_percent, wacc_error = _parse_required_positive(values.wacc_percent, label="WACC")
    sales_to_capital, sales_error = _parse_required_positive(
        values.sales_to_capital, label="Sales-to-capital",
    )
    revenue_billions, revenue_error = _parse_required_positive(
        values.preceding_revenue_billions, label="Preceding annual revenue",
    )
    errors = [item for item in (wacc_error, sales_error, revenue_error) if item]
    if wacc_percent is not None and wacc_percent >= 100:
        errors.append("WACC must be less than 100%.")
    if errors:
        return None, tuple(errors)
    return (
        wacc_percent / 100.0,
        sales_to_capital,
        revenue_billions * REVENUE_INPUT_SCALE,
    ), ()


def _scenario_provenance(
    report: StockResearchReport,
    assumption_type: ReverseDcfScenarioAssumptionType,
) -> tuple[Provenance, ...]:
    return (Provenance(
        provider="user-supplied",
        endpoint_or_dataset="v1-explicit-reverse-dcf-scenario",
        provider_symbol=report.identity.display_symbol,
        retrieved_at=report.analysis_as_of,
        as_of_at=report.analysis_as_of,
        transformation_steps=("interactive display-unit normalization",),
        configuration_or_override_id=f"v1-scenario:{assumption_type.value}",
        source_metric=assumption_type.value,
    ),)


def _assumption(
    report: StockResearchReport,
    context: ReverseDcfScenarioExecutionContext,
    *,
    assumption_type: ReverseDcfScenarioAssumptionType,
    value: float,
    unit: MetricUnit,
    currency: str | None = None,
    frequency: Frequency | None = None,
    fiscal_year: int | None = None,
    period_end: date | None = None,
) -> ReverseDcfScenarioAssumption:
    return configure_reverse_dcf_scenario_assumption(
        target_security_id=context.target_security_id,
        target_issuer_id=context.target_issuer_id,
        analysis_as_of=context.analysis_as_of,
        assumption_type=assumption_type,
        value=value,
        unit=unit,
        source_category=ReverseDcfScenarioSourceCategory.USER_SUPPLIED,
        source_label=SCENARIO_SOURCE_LABEL,
        methodology_label=SCENARIO_METHODOLOGY_LABEL,
        rationale=SCENARIO_RATIONALE,
        entered_by=SCENARIO_ENTERED_BY,
        provenance=_scenario_provenance(report, assumption_type),
        currency=currency,
        frequency=frequency,
        fiscal_year=fiscal_year,
        period_end=period_end,
    )


def run_v1_reverse_dcf_scenario(
    report: StockResearchReport,
    context: ReverseDcfScenarioExecutionContext,
    values: ReverseDcfScenarioFormInput,
) -> ReverseDcfScenarioRunResult:
    """Execute exactly one 10C explicit scenario over retained canonical inputs."""
    if not isinstance(report, StockResearchReport):
        raise TypeError("report must be StockResearchReport")
    if not isinstance(context, ReverseDcfScenarioExecutionContext):
        raise TypeError("context must be ReverseDcfScenarioExecutionContext")
    if not isinstance(values, ReverseDcfScenarioFormInput):
        raise TypeError("values must be ReverseDcfScenarioFormInput")
    if context.identity_key != (
        report.target_security_id,
        report.target_issuer_id,
        report.analysis_as_of.isoformat(),
    ):
        return ReverseDcfScenarioRunResult(
            ReverseDcfScenarioRunStatus.UNAVAILABLE,
            context.identity_key,
            safe_message="Scenario context did not match the current report identity and snapshot.",
        )
    normalized, errors = normalize_reverse_dcf_scenario_input(values)
    if normalized is None:
        return ReverseDcfScenarioRunResult(
            ReverseDcfScenarioRunStatus.VALIDATION_ERROR,
            context.identity_key,
            validation_errors=errors,
        )
    wacc, sales_to_capital, preceding_revenue = normalized
    try:
        assumptions = (
            _assumption(
                report, context,
                assumption_type=ReverseDcfScenarioAssumptionType.WACC,
                value=wacc,
                unit=MetricUnit.PERCENT_DECIMAL,
            ),
            _assumption(
                report, context,
                assumption_type=ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL,
                value=sales_to_capital,
                unit=MetricUnit.RATIO,
            ),
            _assumption(
                report, context,
                assumption_type=ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE,
                value=preceding_revenue,
                unit=MetricUnit.CURRENCY,
                currency=context.valuation_currency,
                frequency=Frequency.ANNUAL,
                fiscal_year=context.required_preceding_fiscal_year,
                period_end=context.required_preceding_period_end,
            ),
        )
        execution = execute_reverse_dcf(
            execution_mode=ReverseDcfExecutionMode.EXPLICIT_SCENARIO,
            trajectory=context.trajectory,
            actual_base=context.actual_base,
            market_anchor=context.market_anchor,
            operating_tax=context.operating_tax,
            canonical_sales_to_capital=context.canonical_sales_to_capital,
            production_wacc=context.production_wacc,
            scenario_assumptions=assumptions,
        )
    except (ArithmeticError, TypeError, ValueError):
        return ReverseDcfScenarioRunResult(
            ReverseDcfScenarioRunStatus.UNAVAILABLE,
            context.identity_key,
            safe_message="The approved explicit-scenario execution rejected the supplied inputs.",
        )
    return ReverseDcfScenarioRunResult(
        ReverseDcfScenarioRunStatus.EXECUTED,
        context.identity_key,
        assumptions=assumptions,
        execution_result=execution,
    )
