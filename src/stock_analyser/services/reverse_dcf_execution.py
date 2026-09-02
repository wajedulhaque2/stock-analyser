"""Explicit canonical-versus-scenario orchestration for Milestone 10C."""

from __future__ import annotations

from datetime import date, datetime

from stock_analyser.domain import (
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    ForwardOperatingTrajectory,
    Frequency,
    MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor,
    MetricUnit,
    Provenance,
    ReverseDcfActualBase,
    ReverseDcfDiscountRateInput,
    ReverseDcfDiscountRateSource,
    ReverseDcfExecutionInputSource,
    ReverseDcfExecutionInputs,
    ReverseDcfExecutionMode,
    ReverseDcfExecutionResult,
    ReverseDcfPrecedingRevenueInput,
    ReverseDcfPublicationEligibility,
    ReverseDcfReadinessStatus,
    ReverseDcfSalesToCapitalInput,
    ReverseDcfScenarioAssumption,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfScenarioSourceCategory,
    ReverseDcfSolverStatus,
    SalesToCapitalEvidence,
    TerminalMarginPolicy,
    WaccResult,
    stable_reverse_dcf_id,
)
from stock_analyser.services.reverse_dcf_cashflows import build_reverse_dcf_cash_flow_path
from stock_analyser.services.reverse_dcf_solver import (
    discount_rate_input_from_production_wacc,
    solve_market_implied_terminal_growth,
)


EXECUTION_POLICY_ID = "reverse-dcf-explicit-execution-mode-v1"
SCENARIO_ASSUMPTION_POLICY_ID = "reverse-dcf-explicit-scenario-assumption-v1"


def configure_reverse_dcf_scenario_assumption(
    *,
    target_security_id: str,
    target_issuer_id: str,
    analysis_as_of: datetime,
    assumption_type: ReverseDcfScenarioAssumptionType,
    value: float,
    unit: MetricUnit,
    source_category: ReverseDcfScenarioSourceCategory,
    source_label: str,
    methodology_label: str,
    rationale: str,
    entered_by: str,
    provenance: tuple[Provenance, ...],
    currency: str | None = None,
    frequency: Frequency | None = None,
    fiscal_year: int | None = None,
    period_end: date | None = None,
    source_date: date | None = None,
    policy_id: str = SCENARIO_ASSUMPTION_POLICY_ID,
) -> ReverseDcfScenarioAssumption:
    """Create one explicit assumption; no assumption type has a numeric default."""
    return ReverseDcfScenarioAssumption(
        assumption_id=stable_reverse_dcf_id(
            "reversedcfscenario", target_security_id, target_issuer_id, analysis_as_of,
            assumption_type.value, value, unit.value, currency, frequency, fiscal_year, period_end,
            source_category.value, source_label, source_date, methodology_label,
            rationale, entered_by, policy_id,
        ),
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=analysis_as_of,
        assumption_type=assumption_type,
        value=value,
        unit=unit,
        currency=currency,
        frequency=frequency,
        fiscal_year=fiscal_year,
        period_end=period_end,
        source_category=source_category,
        source_label=source_label,
        source_date=source_date,
        methodology_label=methodology_label,
        rationale=rationale,
        entered_by=entered_by,
        status=ReverseDcfReadinessStatus.READY,
        issues=(),
        warnings=(),
        policy_id=policy_id,
        provenance=provenance,
    )


def _scenario_map(
    assumptions: tuple[ReverseDcfScenarioAssumption, ...],
    trajectory: ForwardOperatingTrajectory,
) -> dict[ReverseDcfScenarioAssumptionType, ReverseDcfScenarioAssumption]:
    result: dict[ReverseDcfScenarioAssumptionType, ReverseDcfScenarioAssumption] = {}
    for item in assumptions:
        if item.assumption_type in result:
            raise ValueError(f"duplicate explicit scenario assumption: {item.assumption_type.value}")
        if (
            item.status is not ReverseDcfReadinessStatus.READY
            or item.target_security_id != trajectory.target_security_id
            or item.target_issuer_id != trajectory.target_issuer_id
            or item.analysis_as_of != trajectory.analysis_as_of
        ):
            raise ValueError("scenario assumptions must be ready and match target identity/snapshot")
        result[item.assumption_type] = item
    return result


def _scenario_preceding_revenue(
    item: ReverseDcfScenarioAssumption,
    trajectory: ForwardOperatingTrajectory,
) -> ReverseDcfPrecedingRevenueInput:
    first = trajectory.periods[0] if trajectory.periods else None
    if (
        first is None or item.currency != trajectory.valuation_currency
        or item.fiscal_year != first.fiscal_year - 1
        or item.period_end is None
        or (item.period_end.month, item.period_end.day)
        != (first.fiscal_period_end.month, first.fiscal_period_end.day)
    ):
        raise ValueError("scenario preceding revenue must be the aligned annual fiscal period immediately before FY1")
    return ReverseDcfPrecedingRevenueInput(
        input_id=stable_reverse_dcf_id("reversedcfprecedingrevenue", item.assumption_id),
        target_security_id=item.target_security_id,
        target_issuer_id=item.target_issuer_id,
        analysis_as_of=item.analysis_as_of,
        value=item.value,
        currency=item.currency,
        fiscal_year=item.fiscal_year,
        period_end=item.period_end,
        source=ReverseDcfExecutionInputSource.SCENARIO_ASSUMPTION,
        canonical_actual_base_id=None,
        scenario_assumption_id=item.assumption_id,
        status=ReverseDcfReadinessStatus.READY,
        policy_id=EXECUTION_POLICY_ID,
        provenance=item.provenance,
    )


def _scenario_sales_to_capital(item: ReverseDcfScenarioAssumption) -> ReverseDcfSalesToCapitalInput:
    return ReverseDcfSalesToCapitalInput(
        input_id=stable_reverse_dcf_id("reversedcfsalestocapitalinput", item.assumption_id),
        target_security_id=item.target_security_id,
        target_issuer_id=item.target_issuer_id,
        analysis_as_of=item.analysis_as_of,
        value=item.value,
        source=ReverseDcfExecutionInputSource.SCENARIO_ASSUMPTION,
        canonical_evidence_id=None,
        scenario_assumption_id=item.assumption_id,
        status=ReverseDcfReadinessStatus.READY,
        policy_id=EXECUTION_POLICY_ID,
        provenance=item.provenance,
    )


def _scenario_discount_rate(
    item: ReverseDcfScenarioAssumption,
    trajectory: ForwardOperatingTrajectory,
    production_wacc: WaccResult | None,
) -> ReverseDcfDiscountRateInput:
    return ReverseDcfDiscountRateInput(
        input_id=stable_reverse_dcf_id("reversedcfdiscountrate", item.assumption_id),
        target_security_id=item.target_security_id,
        target_issuer_id=item.target_issuer_id,
        analysis_as_of=item.analysis_as_of,
        valuation_currency=trajectory.valuation_currency,
        value=item.value,
        source=ReverseDcfDiscountRateSource.SCENARIO_WACC,
        production_wacc_result_id=None,
        scenario_assumption_id=item.assumption_id,
        production_wacc_status=(
            production_wacc.status if production_wacc is not None
            else DiscountRateReadinessStatus.NOT_READY
        ),
        status=ReverseDcfReadinessStatus.READY,
        policy_id=EXECUTION_POLICY_ID,
        provenance=item.provenance,
    )


def _canonical_blockers(
    trajectory: ForwardOperatingTrajectory,
    actual_base: ReverseDcfActualBase | None,
    market_anchor: MarketEnterpriseValueAnchor,
    operating_tax: MarginalTaxRateEvidence | None,
    sales_to_capital: SalesToCapitalEvidence | None,
    production_wacc: WaccResult | None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    first = trajectory.periods[0] if trajectory.periods else None
    actual_ready = bool(
        actual_base is not None and first is not None
        and actual_base.status is ReverseDcfReadinessStatus.READY
        and actual_base.target_security_id == trajectory.target_security_id
        and actual_base.target_issuer_id == trajectory.target_issuer_id
        and actual_base.analysis_as_of == trajectory.analysis_as_of
        and actual_base.currency == trajectory.valuation_currency
        and actual_base.fiscal_year == first.fiscal_year - 1
        and actual_base.period_end is not None
        and (actual_base.period_end.month, actual_base.period_end.day)
        == (first.fiscal_period_end.month, first.fiscal_period_end.day)
    )
    if trajectory.status is not ReverseDcfReadinessStatus.READY:
        blockers.append("FORWARD_TRAJECTORY")
    if not actual_ready:
        blockers.append("FY1_REINVESTMENT_BASE")
    if (
        operating_tax is None
        or operating_tax.status is not DiscountRateEvidenceStatus.ELIGIBLE
        or operating_tax.analysis_as_of != trajectory.analysis_as_of
    ):
        blockers.append("OPERATING_TAX")
    if (
        sales_to_capital is None
        or sales_to_capital.status is not ReverseDcfReadinessStatus.READY
        or sales_to_capital.target_security_id != trajectory.target_security_id
        or sales_to_capital.target_issuer_id != trajectory.target_issuer_id
        or sales_to_capital.analysis_as_of != trajectory.analysis_as_of
        or (
            sales_to_capital.currency_scope is not None
            and sales_to_capital.currency_scope != trajectory.valuation_currency
        )
    ):
        blockers.append("SALES_TO_CAPITAL")
    if (
        market_anchor.status is not ReverseDcfReadinessStatus.READY
        or market_anchor.target_security_id != trajectory.target_security_id
        or market_anchor.target_issuer_id != trajectory.target_issuer_id
        or market_anchor.analysis_as_of != trajectory.analysis_as_of
        or market_anchor.currency != trajectory.valuation_currency
    ):
        blockers.append("MARKET_ENTERPRISE_VALUE")
    if (
        production_wacc is None
        or production_wacc.status is not DiscountRateReadinessStatus.READY
        or production_wacc.security_id != trajectory.target_security_id
        or production_wacc.issuer_id != trajectory.target_issuer_id
        or production_wacc.analysis_as_of != trajectory.analysis_as_of
        or production_wacc.valuation_currency != trajectory.valuation_currency
    ):
        blockers.append("PRODUCTION_WACC")
    if first is None or trajectory.periods[-1].operating_margin is None:
        blockers.append("TERMINAL_MARGIN_POLICY")
    return tuple(blockers)


def _scenario_label(item: ReverseDcfScenarioAssumption) -> str:
    if item.assumption_type is ReverseDcfScenarioAssumptionType.WACC:
        value = f"{item.value * 100:.6g}%"
    elif item.assumption_type is ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL:
        value = f"{item.value:.15g}x"
    else:
        value = f"{item.value:.15g} {item.currency} FY{item.fiscal_year}"
    return f"{item.assumption_type.value}: {value}; source={item.source_label}"


def execute_reverse_dcf(
    *,
    execution_mode: ReverseDcfExecutionMode,
    trajectory: ForwardOperatingTrajectory,
    actual_base: ReverseDcfActualBase | None,
    market_anchor: MarketEnterpriseValueAnchor,
    operating_tax: MarginalTaxRateEvidence | None,
    canonical_sales_to_capital: SalesToCapitalEvidence | None,
    production_wacc: WaccResult | None,
    scenario_assumptions: tuple[ReverseDcfScenarioAssumption, ...] = (),
) -> ReverseDcfExecutionResult:
    """Execute one explicit mode; never fall back between canonical and scenario inputs."""
    assumptions = tuple(scenario_assumptions)
    by_type = _scenario_map(assumptions, trajectory)
    canonical_blockers = _canonical_blockers(
        trajectory, actual_base, market_anchor, operating_tax,
        canonical_sales_to_capital, production_wacc,
    )
    canonical_status = (
        ReverseDcfReadinessStatus.READY if not canonical_blockers
        else ReverseDcfReadinessStatus.NOT_READY
    )
    issues: list[str] = []

    selected_actual = actual_base
    preceding_input = None
    selected_sales = canonical_sales_to_capital
    sales_input = None
    selected_discount: WaccResult | ReverseDcfDiscountRateInput | None = production_wacc

    if execution_mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE:
        if assumptions:
            issues.append("Scenario assumptions are not accepted by CANONICAL_EVIDENCE execution.")
        if canonical_blockers:
            issues.extend(f"Canonical blocker: {item}." for item in canonical_blockers)
    else:
        revenue_assumption = by_type.get(ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE)
        sales_assumption = by_type.get(ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL)
        wacc_assumption = by_type.get(ReverseDcfScenarioAssumptionType.WACC)
        if revenue_assumption is not None:
            preceding_input = _scenario_preceding_revenue(revenue_assumption, trajectory)
            selected_actual = None
        if sales_assumption is not None:
            sales_input = _scenario_sales_to_capital(sales_assumption)
            selected_sales = None
        if wacc_assumption is not None:
            selected_discount = _scenario_discount_rate(wacc_assumption, trajectory, production_wacc)
        if selected_actual is None and preceding_input is None:
            issues.append("Explicit preceding annual revenue assumption is required.")
        if selected_sales is None and sales_input is None:
            issues.append("Explicit sales-to-capital assumption is required.")
        if selected_discount is None or (
            isinstance(selected_discount, WaccResult)
            and selected_discount.status is not DiscountRateReadinessStatus.READY
        ):
            issues.append("Explicit scenario WACC assumption is required.")

    path = build_reverse_dcf_cash_flow_path(
        trajectory,
        selected_actual,
        operating_tax,
        selected_sales,
        preceding_revenue_input=preceding_input,
        sales_to_capital_input=sales_input,
    )
    solver = solve_market_implied_terminal_growth(
        path, market_anchor, operating_tax,
        sales_input or selected_sales,
        selected_discount,
    )
    if issues and solver.status is ReverseDcfSolverStatus.SOLVED:
        raise ValueError("execution policy rejected inputs that unexpectedly reached the solver")

    selected_discount_input = (
        discount_rate_input_from_production_wacc(selected_discount)
        if isinstance(selected_discount, WaccResult)
        and selected_discount.status is DiscountRateReadinessStatus.READY
        else selected_discount if isinstance(selected_discount, ReverseDcfDiscountRateInput) else None
    )
    execution_status = (
        ReverseDcfReadinessStatus.READY
        if solver.status is ReverseDcfSolverStatus.SOLVED and not issues
        else ReverseDcfReadinessStatus.NOT_READY
    )
    input_contract = ReverseDcfExecutionInputs(
        execution_input_id=stable_reverse_dcf_id(
            "reversedcfexecutioninputs", execution_mode.value, trajectory.trajectory_id,
            market_anchor.anchor_id, getattr(operating_tax, "evidence_id", None),
            getattr(preceding_input, "input_id", None) or getattr(selected_actual, "evidence_id", None),
            getattr(sales_input, "input_id", None) or getattr(selected_sales, "evidence_id", None),
            getattr(selected_discount_input, "input_id", None),
            *(item.assumption_id for item in assumptions),
        ),
        execution_mode=execution_mode,
        target_security_id=trajectory.target_security_id,
        target_issuer_id=trajectory.target_issuer_id,
        analysis_as_of=trajectory.analysis_as_of,
        valuation_currency=trajectory.valuation_currency,
        forward_trajectory_id=trajectory.trajectory_id,
        market_anchor_id=market_anchor.anchor_id,
        tax_evidence_id=operating_tax.evidence_id if operating_tax else None,
        terminal_margin_policy=TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN,
        preceding_revenue_input_id=(
            preceding_input.input_id if preceding_input else getattr(selected_actual, "evidence_id", None)
        ),
        sales_to_capital_input_id=(
            sales_input.input_id if sales_input else getattr(selected_sales, "evidence_id", None)
        ),
        discount_rate_input_id=selected_discount_input.input_id if selected_discount_input else None,
        canonical_actual_base_id=actual_base.evidence_id if actual_base else None,
        canonical_sales_to_capital_evidence_id=(
            canonical_sales_to_capital.evidence_id if canonical_sales_to_capital else None
        ),
        production_wacc_result_id=production_wacc.result_id if production_wacc else None,
        canonical_actual_base_status=(
            actual_base.status if actual_base else ReverseDcfReadinessStatus.UNAVAILABLE
        ),
        canonical_sales_to_capital_status=(
            canonical_sales_to_capital.status
            if canonical_sales_to_capital else ReverseDcfReadinessStatus.UNAVAILABLE
        ),
        production_wacc_status=(
            production_wacc.status if production_wacc else DiscountRateReadinessStatus.NOT_READY
        ),
        canonical_input_status=canonical_status,
        scenario_assumption_ids=tuple(item.assumption_id for item in assumptions),
        status=execution_status,
        issues=tuple(dict.fromkeys((*issues, *solver.issues))),
        warnings=(),
        policy_ids=(EXECUTION_POLICY_ID,),
        provenance=tuple(dict.fromkeys((
            *trajectory.provenance, *market_anchor.provenance,
            *((operating_tax.provenance) if operating_tax else ()),
            *((canonical_sales_to_capital.provenance) if canonical_sales_to_capital else ()),
            *((production_wacc.provenance) if production_wacc else ()),
            *(item for assumption in assumptions for item in assumption.provenance),
        ))),
    )
    publication = ReverseDcfPublicationEligibility.UNAVAILABLE
    if solver.status is ReverseDcfSolverStatus.SOLVED and not issues:
        publication = (
            ReverseDcfPublicationEligibility.CANONICAL
            if execution_mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE
            and canonical_status is ReverseDcfReadinessStatus.READY and not assumptions
            else ReverseDcfPublicationEligibility.SCENARIO_ONLY
        )
    final = trajectory.periods[-1] if trajectory.periods else None
    return ReverseDcfExecutionResult(
        execution_result_id=stable_reverse_dcf_id(
            "reversedcfexecutionresult", input_contract.execution_input_id,
            solver.result_id, publication.value,
        ),
        execution_inputs_id=input_contract.execution_input_id,
        execution_inputs=input_contract,
        execution_mode=execution_mode,
        solver_result=solver,
        canonical_input_status=canonical_status,
        scenario_assumption_ids=tuple(item.assumption_id for item in assumptions),
        publication_eligibility=publication,
        central_valuation_eligible=False,
        canonical_blockers=canonical_blockers,
        final_consensus_revenue_growth=final.revenue_growth if final else None,
        final_consensus_ebit_margin=final.operating_margin if final else None,
        scenario_assumption_labels=tuple(_scenario_label(item) for item in assumptions),
        issues=input_contract.issues,
        warnings=(),
        policy_ids=(EXECUTION_POLICY_ID,),
        provenance=input_contract.provenance,
    )
