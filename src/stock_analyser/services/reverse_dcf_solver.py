"""Deterministic one-variable market-implied terminal-growth solver."""

from __future__ import annotations

from dataclasses import dataclass
import math

from stock_analyser.domain import (
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor,
    MarketImpliedTerminalGrowthResult,
    ReverseDcfCashFlowPath,
    ReverseDcfDiscountRateInput,
    ReverseDcfDiscountRateSource,
    ReverseDcfFormulation,
    ReverseDcfReadinessStatus,
    ReverseDcfSolverStatus,
    SalesToCapitalEvidence,
    ReverseDcfSalesToCapitalInput,
    TerminalMarginPolicy,
    WaccResult,
    stable_reverse_dcf_id,
)


@dataclass(frozen=True, slots=True)
class ReverseDcfSolverPolicy:
    search_lower_bound: float = -0.20
    wacc_minus_growth_epsilon: float = 1e-6
    growth_tolerance: float = 1e-10
    ev_residual_relative_tolerance: float = 1e-10
    ev_residual_absolute_tolerance: float = 1e-6
    maximum_iterations: int = 256
    monotonicity_sample_count: int = 33
    terminal_margin_policy: TerminalMarginPolicy = TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN
    policy_id: str = "market-implied-terminal-growth-bisection-v1"

    def __post_init__(self) -> None:
        for name in (
            "search_lower_bound", "wacc_minus_growth_epsilon", "growth_tolerance",
            "ev_residual_relative_tolerance", "ev_residual_absolute_tolerance",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.search_lower_bound <= -1:
            raise ValueError("search lower bound must preserve positive terminal revenue")
        if self.wacc_minus_growth_epsilon <= 0 or self.growth_tolerance <= 0:
            raise ValueError("solver epsilon and growth tolerance must be positive")
        if self.ev_residual_relative_tolerance <= 0 or self.ev_residual_absolute_tolerance <= 0:
            raise ValueError("EV residual tolerances must be positive")
        if self.maximum_iterations < 1 or isinstance(self.maximum_iterations, bool):
            raise ValueError("maximum_iterations must be positive")
        if self.monotonicity_sample_count < 3 or isinstance(self.monotonicity_sample_count, bool):
            raise ValueError("monotonicity_sample_count must be at least three")
        if self.terminal_margin_policy is not TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN:
            raise ValueError("10B supports only the final-consensus-margin policy")


DEFAULT_REVERSE_DCF_SOLVER_POLICY = ReverseDcfSolverPolicy()


@dataclass(frozen=True, slots=True)
class TerminalGrowthEvaluation:
    terminal_growth: float
    terminal_margin: float
    terminal_revenue: float
    terminal_ebit: float
    terminal_nopat: float
    terminal_reinvestment: float
    terminal_fcff: float
    terminal_value: float
    explicit_fcff_present_value: float
    terminal_value_present_value: float
    modeled_enterprise_value: float


def discount_rate_input_from_production_wacc(wacc: WaccResult) -> ReverseDcfDiscountRateInput:
    """Bind a READY production WACC without changing the frozen WACC contract."""
    if wacc.status is not DiscountRateReadinessStatus.READY or wacc.value is None:
        raise ValueError("ready production WACC is required")
    return ReverseDcfDiscountRateInput(
        input_id=stable_reverse_dcf_id("reversedcfdiscountrate", wacc.result_id),
        target_security_id=wacc.security_id,
        target_issuer_id=wacc.issuer_id,
        analysis_as_of=wacc.analysis_as_of,
        valuation_currency=wacc.valuation_currency,
        value=wacc.value,
        source=ReverseDcfDiscountRateSource.PRODUCTION_WACC,
        production_wacc_result_id=wacc.result_id,
        scenario_assumption_id=None,
        production_wacc_status=wacc.status,
        status=ReverseDcfReadinessStatus.READY,
        policy_id="reverse-dcf-production-wacc-input-v1",
        provenance=wacc.provenance,
    )


def _normalized_discount_rate(
    value: WaccResult | ReverseDcfDiscountRateInput | None,
) -> ReverseDcfDiscountRateInput | None:
    if isinstance(value, ReverseDcfDiscountRateInput):
        return value if value.status is ReverseDcfReadinessStatus.READY else None
    if isinstance(value, WaccResult):
        if value.status is not DiscountRateReadinessStatus.READY or value.value is None:
            return None
        return discount_rate_input_from_production_wacc(value)
    return None


def evaluate_market_implied_terminal_growth(
    cash_flow_path: ReverseDcfCashFlowPath,
    operating_tax: MarginalTaxRateEvidence,
    sales_to_capital: SalesToCapitalEvidence | ReverseDcfSalesToCapitalInput,
    wacc: WaccResult | ReverseDcfDiscountRateInput,
    terminal_growth: float,
    *,
    policy: ReverseDcfSolverPolicy = DEFAULT_REVERSE_DCF_SOLVER_POLICY,
) -> TerminalGrowthEvaluation:
    """Evaluate the one authorized formulation at one candidate growth rate."""
    if cash_flow_path.status is not ReverseDcfReadinessStatus.READY or not cash_flow_path.periods:
        raise ValueError("complete explicit FCFF path is required")
    if operating_tax.status is not DiscountRateEvidenceStatus.ELIGIBLE or operating_tax.value is None:
        raise ValueError("eligible marginal tax evidence is required")
    if sales_to_capital.status is not ReverseDcfReadinessStatus.READY or sales_to_capital.value is None:
        raise ValueError("eligible sales-to-capital evidence is required")
    discount_rate = _normalized_discount_rate(wacc)
    if discount_rate is None:
        raise ValueError("ready production WACC is required")
    if not math.isfinite(terminal_growth):
        raise ValueError("terminal growth must be finite")
    if terminal_growth >= discount_rate.value:
        raise ValueError("WACC must exceed candidate terminal growth")
    if terminal_growth < policy.search_lower_bound:
        raise ValueError("candidate terminal growth is outside the configured numerical domain")
    if not (
        cash_flow_path.target_security_id == discount_rate.target_security_id == sales_to_capital.target_security_id
        and cash_flow_path.target_issuer_id == discount_rate.target_issuer_id == sales_to_capital.target_issuer_id
        and cash_flow_path.analysis_as_of == discount_rate.analysis_as_of == operating_tax.analysis_as_of
        == sales_to_capital.analysis_as_of
        and cash_flow_path.valuation_currency == discount_rate.valuation_currency
    ):
        raise ValueError("cash flow, tax, reinvestment, and WACC evidence must align")

    explicit_pv = sum(
        period.fcff / ((1 + discount_rate.value) ** period.discount_period_index)
        for period in cash_flow_path.periods
    )
    final = cash_flow_path.periods[-1]
    terminal_margin = final.ebit_margin
    terminal_revenue = final.revenue * (1 + terminal_growth)
    terminal_ebit = terminal_revenue * terminal_margin
    terminal_nopat = terminal_ebit * (1 - operating_tax.value)
    terminal_reinvestment = (terminal_revenue - final.revenue) / sales_to_capital.value
    terminal_fcff = terminal_nopat - terminal_reinvestment
    terminal_value = terminal_fcff / (discount_rate.value - terminal_growth)
    terminal_pv = terminal_value / ((1 + discount_rate.value) ** final.discount_period_index)
    modeled_ev = explicit_pv + terminal_pv
    values = (
        explicit_pv, terminal_margin, terminal_revenue, terminal_ebit, terminal_nopat,
        terminal_reinvestment, terminal_fcff, terminal_value, terminal_pv, modeled_ev,
    )
    if not all(math.isfinite(value) for value in values):
        raise ArithmeticError("terminal-growth evaluation produced non-finite arithmetic")
    return TerminalGrowthEvaluation(
        terminal_growth=terminal_growth,
        terminal_margin=terminal_margin,
        terminal_revenue=terminal_revenue,
        terminal_ebit=terminal_ebit,
        terminal_nopat=terminal_nopat,
        terminal_reinvestment=terminal_reinvestment,
        terminal_fcff=terminal_fcff,
        terminal_value=terminal_value,
        explicit_fcff_present_value=explicit_pv,
        terminal_value_present_value=terminal_pv,
        modeled_enterprise_value=modeled_ev,
    )


def _evidence_ready(
    cash_flow_path: ReverseDcfCashFlowPath,
    market_anchor: MarketEnterpriseValueAnchor | None,
    operating_tax: MarginalTaxRateEvidence | None,
    sales_to_capital: SalesToCapitalEvidence | ReverseDcfSalesToCapitalInput | None,
    wacc: WaccResult | ReverseDcfDiscountRateInput | None,
) -> tuple[bool, tuple[str, ...]]:
    issues: list[str] = []
    if cash_flow_path.status is not ReverseDcfReadinessStatus.READY:
        issues.extend(cash_flow_path.issues or ("Complete explicit FY1+ FCFF path is required.",))
    if cash_flow_path.fy1_reinvestment_base_status is not ReverseDcfReadinessStatus.READY:
        issues.append("FY1_REINVESTMENT_BASE is required by the sales-to-capital methodology.")
    if market_anchor is None or market_anchor.status is not ReverseDcfReadinessStatus.READY:
        issues.append("Ready canonical Fiscal market-enterprise-value anchor is required.")
    if operating_tax is None or operating_tax.status is not DiscountRateEvidenceStatus.ELIGIBLE:
        issues.append("Eligible canonical marginal-tax evidence is required.")
    if sales_to_capital is None or sales_to_capital.status is not ReverseDcfReadinessStatus.READY:
        issues.append("Eligible explicit sales-to-capital evidence is required; no default was used.")
    discount_rate = _normalized_discount_rate(wacc)
    if discount_rate is None:
        issues.append("Ready production WACC is required; cost of equity cannot substitute.")
    if not issues and not (
        market_anchor.target_security_id == cash_flow_path.target_security_id == discount_rate.target_security_id
        == sales_to_capital.target_security_id
        and market_anchor.target_issuer_id == cash_flow_path.target_issuer_id == discount_rate.target_issuer_id
        == sales_to_capital.target_issuer_id
        and market_anchor.analysis_as_of == cash_flow_path.analysis_as_of == discount_rate.analysis_as_of
        == operating_tax.analysis_as_of == sales_to_capital.analysis_as_of
        and market_anchor.currency == cash_flow_path.valuation_currency == discount_rate.valuation_currency
        and market_anchor.value is not None
    ):
        issues.append("Target identity, snapshot, currency, or evidence bindings do not align.")
    return not issues, tuple(dict.fromkeys(issues))


def _result(
    *,
    cash_flow_path: ReverseDcfCashFlowPath,
    market_anchor: MarketEnterpriseValueAnchor | None,
    operating_tax: MarginalTaxRateEvidence | None,
    sales_to_capital: SalesToCapitalEvidence | ReverseDcfSalesToCapitalInput | None,
    wacc: WaccResult | ReverseDcfDiscountRateInput | None,
    policy: ReverseDcfSolverPolicy,
    status: ReverseDcfSolverStatus,
    issues: tuple[str, ...],
    upper: float | None,
    initial_low_value: float | None = None,
    initial_high_value: float | None = None,
    final_low: float | None = None,
    final_high: float | None = None,
    evaluation: TerminalGrowthEvaluation | None = None,
    residual: float | None = None,
    iterations: int = 0,
) -> MarketImpliedTerminalGrowthResult:
    supporting = tuple(dict.fromkeys((
        cash_flow_path.trajectory_id,
        cash_flow_path.path_id,
        *((market_anchor.anchor_id,) if market_anchor else ()),
        *((operating_tax.evidence_id,) if operating_tax else ()),
        *((getattr(sales_to_capital, "evidence_id", None) or getattr(sales_to_capital, "input_id", None),) if sales_to_capital else ()),
        *((getattr(wacc, "result_id", None) or getattr(wacc, "input_id", None),) if wacc else ()),
        *cash_flow_path.supporting_ids,
    )))
    provenance = tuple(dict.fromkeys((
        *cash_flow_path.provenance,
        *((market_anchor.provenance) if market_anchor else ()),
        *((operating_tax.provenance) if operating_tax else ()),
        *((sales_to_capital.provenance) if sales_to_capital else ()),
        *((wacc.provenance) if wacc else ()),
    )))
    return MarketImpliedTerminalGrowthResult(
        result_id=stable_reverse_dcf_id(
            "marketimpliedterminalgrowth", cash_flow_path.path_id,
            getattr(market_anchor, "anchor_id", None),
            getattr(operating_tax, "evidence_id", None),
            getattr(sales_to_capital, "evidence_id", None) or getattr(sales_to_capital, "input_id", None),
            getattr(wacc, "result_id", None) or getattr(wacc, "input_id", None), policy.policy_id,
        ),
        target_security_id=cash_flow_path.target_security_id,
        target_issuer_id=cash_flow_path.target_issuer_id,
        analysis_as_of=cash_flow_path.analysis_as_of,
        valuation_currency=cash_flow_path.valuation_currency,
        formulation=ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_GROWTH,
        trajectory_id=cash_flow_path.trajectory_id,
        cash_flow_path_id=cash_flow_path.path_id,
        market_anchor_id=market_anchor.anchor_id if market_anchor else None,
        tax_evidence_id=operating_tax.evidence_id if operating_tax else None,
        sales_to_capital_evidence_id=(
            getattr(sales_to_capital, "evidence_id", None)
            or getattr(sales_to_capital, "input_id", None)
        ) if sales_to_capital else None,
        wacc_result_id=(
            wacc.result_id if isinstance(wacc, WaccResult)
            else wacc.production_wacc_result_id if isinstance(wacc, ReverseDcfDiscountRateInput) else None
        ),
        discount_rate_input_id=(
            discount_rate_input_from_production_wacc(wacc).input_id if isinstance(wacc, WaccResult)
            and wacc.status is DiscountRateReadinessStatus.READY and wacc.value is not None
            else wacc.input_id if isinstance(wacc, ReverseDcfDiscountRateInput) else None
        ),
        discount_rate_source=(
            ReverseDcfDiscountRateSource.PRODUCTION_WACC if isinstance(wacc, WaccResult)
            else wacc.source if isinstance(wacc, ReverseDcfDiscountRateInput) else None
        ),
        scenario_discount_rate_assumption_id=(
            wacc.scenario_assumption_id if isinstance(wacc, ReverseDcfDiscountRateInput) else None
        ),
        terminal_margin_policy=policy.terminal_margin_policy,
        timing_convention=cash_flow_path.timing_convention,
        search_lower_bound=policy.search_lower_bound,
        search_upper_bound=upper,
        final_bracket_lower=final_low,
        final_bracket_upper=final_high,
        lower_bound_function_value=initial_low_value,
        upper_bound_function_value=initial_high_value,
        implied_terminal_growth=evaluation.terminal_growth if evaluation and status is ReverseDcfSolverStatus.SOLVED else None,
        terminal_margin=evaluation.terminal_margin if evaluation else None,
        terminal_revenue=evaluation.terminal_revenue if evaluation else None,
        terminal_ebit=evaluation.terminal_ebit if evaluation else None,
        terminal_nopat=evaluation.terminal_nopat if evaluation else None,
        terminal_reinvestment=evaluation.terminal_reinvestment if evaluation else None,
        terminal_fcff=evaluation.terminal_fcff if evaluation else None,
        terminal_value=evaluation.terminal_value if evaluation else None,
        explicit_fcff_present_value=evaluation.explicit_fcff_present_value if evaluation else None,
        terminal_value_present_value=evaluation.terminal_value_present_value if evaluation else None,
        modeled_enterprise_value=evaluation.modeled_enterprise_value if evaluation else None,
        observed_market_enterprise_value=market_anchor.value if market_anchor else None,
        residual=residual,
        iterations=iterations,
        status=status,
        issues=issues,
        warnings=(),
        supporting_ids=supporting,
        policy_ids=tuple(dict.fromkeys((*cash_flow_path.policy_ids, policy.policy_id))),
        provenance=provenance,
    )


def solve_market_implied_terminal_growth(
    cash_flow_path: ReverseDcfCashFlowPath,
    market_anchor: MarketEnterpriseValueAnchor | None,
    operating_tax: MarginalTaxRateEvidence | None,
    sales_to_capital: SalesToCapitalEvidence | ReverseDcfSalesToCapitalInput | None,
    wacc: WaccResult | ReverseDcfDiscountRateInput | None,
    *,
    policy: ReverseDcfSolverPolicy = DEFAULT_REVERSE_DCF_SOLVER_POLICY,
) -> MarketImpliedTerminalGrowthResult:
    """Solve only terminal growth with deterministic bounded bisection."""
    ready, issues = _evidence_ready(
        cash_flow_path, market_anchor, operating_tax, sales_to_capital, wacc,
    )
    upper = wacc.value - policy.wacc_minus_growth_epsilon if wacc and wacc.value is not None else None
    if not ready:
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.NOT_READY, issues=issues, upper=upper,
        )
    if upper is None or upper <= policy.search_lower_bound:
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.NUMERICAL_FAILURE,
            issues=("Configured numerical growth domain is empty.",), upper=upper,
        )

    observed = market_anchor.value
    residual_tolerance = max(
        policy.ev_residual_absolute_tolerance,
        abs(observed) * policy.ev_residual_relative_tolerance,
    )

    def evaluate(growth: float) -> tuple[TerminalGrowthEvaluation, float]:
        item = evaluate_market_implied_terminal_growth(
            cash_flow_path, operating_tax, sales_to_capital, wacc, growth, policy=policy,
        )
        return item, item.modeled_enterprise_value - observed

    try:
        low = policy.search_lower_bound
        high = upper
        low_evaluation, low_value = evaluate(low)
        high_evaluation, high_value = evaluate(high)
        sample_values = []
        for index in range(policy.monotonicity_sample_count):
            growth = low + (high - low) * index / (policy.monotonicity_sample_count - 1)
            sample_values.append(evaluate(growth)[1])
        differences = tuple(right - left for left, right in zip(sample_values, sample_values[1:]))
        increasing = all(value >= 0 for value in differences) and any(value > 0 for value in differences)
        decreasing = all(value <= 0 for value in differences) and any(value < 0 for value in differences)
        if not (increasing or decreasing):
            return _result(
                cash_flow_path=cash_flow_path, market_anchor=market_anchor,
                operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
                policy=policy, status=ReverseDcfSolverStatus.NUMERICAL_FAILURE,
                issues=("Terminal-growth mapping was not strictly monotonic across the fixed domain.",),
                upper=upper, initial_low_value=low_value, initial_high_value=high_value,
                final_low=low, final_high=high,
            )
    except (ArithmeticError, OverflowError, ValueError, ZeroDivisionError):
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.NUMERICAL_FAILURE,
            issues=("Terminal-growth domain evaluation failed finite-arithmetic checks.",), upper=upper,
        )

    initial_low_value, initial_high_value = low_value, high_value
    if abs(low_value) <= residual_tolerance:
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.SOLVED, issues=(), upper=upper,
            initial_low_value=initial_low_value, initial_high_value=initial_high_value,
            final_low=low, final_high=low, evaluation=low_evaluation,
            residual=low_value, iterations=0,
        )
    if abs(high_value) <= residual_tolerance:
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.SOLVED, issues=(), upper=upper,
            initial_low_value=initial_low_value, initial_high_value=initial_high_value,
            final_low=high, final_high=high, evaluation=high_evaluation,
            residual=high_value, iterations=0,
        )
    if low_value * high_value > 0:
        return _result(
            cash_flow_path=cash_flow_path, market_anchor=market_anchor,
            operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
            policy=policy, status=ReverseDcfSolverStatus.NO_SOLUTION_IN_DOMAIN,
            issues=("Observed market TEV is not bracketed by the fixed numerical growth domain.",),
            upper=upper, initial_low_value=initial_low_value,
            initial_high_value=initial_high_value, final_low=low, final_high=high,
        )

    best_evaluation = low_evaluation
    best_residual = low_value
    for iteration in range(1, policy.maximum_iterations + 1):
        midpoint = (low + high) / 2
        midpoint_evaluation, midpoint_value = evaluate(midpoint)
        if abs(midpoint_value) < abs(best_residual):
            best_evaluation, best_residual = midpoint_evaluation, midpoint_value
        if abs(midpoint_value) <= residual_tolerance or high - low <= policy.growth_tolerance:
            return _result(
                cash_flow_path=cash_flow_path, market_anchor=market_anchor,
                operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
                policy=policy, status=ReverseDcfSolverStatus.SOLVED, issues=(), upper=upper,
                initial_low_value=initial_low_value, initial_high_value=initial_high_value,
                final_low=low, final_high=high, evaluation=midpoint_evaluation,
                residual=midpoint_value, iterations=iteration,
            )
        if low_value * midpoint_value <= 0:
            high, high_value = midpoint, midpoint_value
        else:
            low, low_value = midpoint, midpoint_value

    return _result(
        cash_flow_path=cash_flow_path, market_anchor=market_anchor,
        operating_tax=operating_tax, sales_to_capital=sales_to_capital, wacc=wacc,
        policy=policy, status=ReverseDcfSolverStatus.NUMERICAL_FAILURE,
        issues=("Bisection reached the centralized iteration limit without convergence.",),
        upper=upper, initial_low_value=initial_low_value, initial_high_value=initial_high_value,
        final_low=low, final_high=high, evaluation=best_evaluation,
        residual=best_residual, iterations=policy.maximum_iterations,
    )
