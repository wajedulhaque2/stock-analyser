from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import math

import pytest

from stock_analyser.domain import (
    CompanyIdentity,
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    EstimateCase,
    Frequency,
    MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    ProviderSymbol,
    ReverseDcfFormulation,
    ReverseDcfReadinessStage,
    ReverseDcfReadinessStatus,
    ReverseDcfSolverStatus,
    ReverseDcfTimingConvention,
    SalesToCapitalEvidence,
    SalesToCapitalSourceType,
    TerminalMarginPolicy,
    WaccResult,
)
from stock_analyser.services import (
    DEFAULT_REVERSE_DCF_SOLVER_POLICY,
    ReverseDcfSolverPolicy,
    assess_reverse_dcf_readiness,
    build_forward_consensus,
    build_forward_operating_trajectory,
    build_market_enterprise_value_anchor,
    build_reinvestment_readiness,
    build_reverse_dcf_actual_base,
    build_reverse_dcf_cash_flow_path,
    build_terminal_readiness,
    configure_sales_to_capital_evidence,
    evaluate_market_implied_terminal_growth,
    solve_market_implied_terminal_growth,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
ACTUAL_AS_OF = datetime(2026, 2, 1, tzinfo=timezone.utc)


def identity() -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol="SYN", security_id="security:synthetic:10b",
        issuer_id="issuer:synthetic:10b", company_name="Synthetic Inc.",
        issuer_domicile="US", listing_country="US", exchange="NASDAQ",
        sector="Industrials", industry="Synthetic equipment", security_type="Common stock",
        reporting_currency="USD", quote_currency="USD", quote_unit="USD", price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "SYN"), ProviderSymbol("fmp", "SYN")),
    )


def provenance(
    provider="fiscal", *, as_of=ACTUAL_AS_OF, source_metric="revenue",
    dataset="fiscal_standardized_financials",
) -> Provenance:
    return Provenance(
        provider=provider, endpoint_or_dataset=dataset, provider_symbol="SYN",
        retrieved_at=NOW, as_of_at=as_of, source_metric=source_metric,
    )


def observation(
    metric: MetricId,
    value: float,
    *,
    year: int,
    provider="fiscal",
    as_of=ACTUAL_AS_OF,
    observation_type=ObservationType.ACTUAL,
    case=EstimateCase.NOT_APPLICABLE,
    suffix="base",
    analyst_count=None,
) -> MetricObservation:
    p = provenance(
        provider, as_of=as_of, source_metric=metric.value,
        dataset="fmp_annual_analyst_estimates" if provider == "fmp" else "fiscal_standardized_financials",
    )
    return MetricObservation(
        observation_id=f"obs:10b:{provider}:{metric.value}:{year}:{case.value}:{suffix}",
        metric_id=metric, value=value, unit=MetricUnit.CURRENCY, currency="USD",
        frequency=Frequency.ANNUAL, observation_type=observation_type,
        estimate_case=case, period_start=date(year, 1, 1), period_end=date(year, 12, 31),
        fiscal_year=year, analyst_count=analyst_count, retrieved_at=NOW, as_of_at=as_of,
        provenance=p,
    )


def actual(revenue=90, *, year=2025):
    rows = (
        observation(MetricId.REVENUE, revenue, year=year, suffix="revenue"),
        observation(MetricId.EBIT, revenue * 0.2, year=year, suffix="ebit"),
    )
    return build_reverse_dcf_actual_base(identity(), rows, analysis_as_of=NOW, valuation_currency="USD")


def forward_trajectory(
    revenues=(100.0, 110.0, 121.0, 133.1, 146.41),
    margins=(0.20, 0.21, 0.22, 0.23, 0.24),
):
    rows = []
    for index, (revenue, margin) in enumerate(zip(revenues, margins), start=2026):
        rows.extend((
            observation(
                MetricId.REVENUE, revenue, year=index, provider="fmp", as_of=NOW,
                observation_type=ObservationType.ESTIMATE, case=EstimateCase.AVERAGE,
                suffix="revenue", analyst_count=10,
            ),
            observation(
                MetricId.EBIT, revenue * margin, year=index, provider="fmp", as_of=NOW,
                observation_type=ObservationType.ESTIMATE, case=EstimateCase.AVERAGE,
                suffix="ebit",
            ),
        ))
    consensus = build_forward_consensus(rows, as_of_at=NOW)
    return build_forward_operating_trajectory(
        identity(), consensus, actual(), analysis_as_of=NOW, valuation_currency="USD",
    )


def marginal_tax(value=0.25, *, status=DiscountRateEvidenceStatus.ELIGIBLE, as_of=NOW):
    p = Provenance(
        provider="configured-tax", endpoint_or_dataset="marginal-tax",
        provider_symbol="US", retrieved_at=NOW, as_of_at=as_of,
        configuration_or_override_id="tax:test", source_metric="marginal_tax_rate",
    )
    return MarginalTaxRateEvidence(
        evidence_id="tax:synthetic:10b", jurisdiction="US" if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        value=value if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        source_name="Synthetic marginal tax", source_date=NOW.date() if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        analysis_as_of=as_of, methodology="explicit marginal rate",
        underlying_source="synthetic public schedule", status=status, issues=(),
        policy_id="tax:test", provenance=(p,) if status is DiscountRateEvidenceStatus.ELIGIBLE else (),
    )


def sales_to_capital(value=2.0, **changes):
    values = dict(
        target_security_id=identity().security_id,
        target_issuer_id=identity().issuer_id,
        value=value,
        source_name="Synthetic external capital study",
        source_date=NOW.date(),
        analysis_as_of=NOW,
        methodology="audited revenue divided by invested capital",
        provenance=(Provenance(
            provider="configured", endpoint_or_dataset="synthetic-sales-to-capital",
            provider_symbol="SYN", retrieved_at=NOW, as_of_at=NOW,
            configuration_or_override_id="sales-to-capital:test",
            source_metric="sales_to_capital",
        ),),
        company_scope="Synthetic Inc.",
        currency_scope="USD",
    )
    values.update(changes)
    return configure_sales_to_capital_evidence(**values)


def wacc(value=0.10, **changes):
    values = dict(
        result_id="wacc:synthetic:10b", security_id=identity().security_id,
        issuer_id=identity().issuer_id, analysis_as_of=NOW, valuation_currency="USD",
        cost_of_equity_result_id="coe:synthetic:10b", equity_value_evidence_id="equity:10b",
        debt_value_evidence_id="debt:10b", interest_coverage_evidence_id="coverage:10b",
        cost_of_debt_evidence_id="cod:10b", marginal_tax_evidence_id="tax:synthetic:10b",
        capital_structure_weights_id="weights:10b", other_claims_evidence_id="claims:10b",
        cost_of_equity=0.11, pretax_cost_of_debt=0.05, marginal_tax_rate=0.25,
        after_tax_cost_of_debt=0.0375, equity_weight=0.85, debt_weight=0.15,
        value=value, status=DiscountRateReadinessStatus.READY, issues=(), warnings=(),
        supporting_ids=("coe:synthetic:10b", "weights:10b"), policy_ids=("wacc:test",),
        provenance=(), readiness_id="wacc-readiness:10b",
    )
    values.update(changes)
    return WaccResult(**values)


def cash_flow_path(*, base=None, trajectory=None, tax=None, sales=None):
    return build_reverse_dcf_cash_flow_path(
        trajectory or forward_trajectory(),
        actual() if base is None else base,
        marginal_tax() if tax is None else tax,
        sales_to_capital() if sales is None else sales,
    )


def anchor(value=1_000.0, **changes):
    p = provenance(
        "fiscal", as_of=NOW, source_metric="calculated_tev",
        dataset="fiscal_enterprise_bridge_metrics",
    )
    values = dict(
        anchor_id="market-anchor:synthetic:10b",
        target_security_id=identity().security_id, target_issuer_id=identity().issuer_id,
        analysis_as_of=NOW, value=value, currency="USD", observation_date=NOW.date(),
        source_observation_id="obs:market-anchor:10b", status=ReverseDcfReadinessStatus.READY,
        issues=(), policy_id="market-anchor:test", provenance=(p,),
    )
    values.update(changes)
    return MarketEnterpriseValueAnchor(**values)


def known_root_inputs(growth=0.03, *, sales_value=2.0, wacc_value=0.10):
    tax = marginal_tax()
    sales = sales_to_capital(sales_value)
    rate = wacc(wacc_value)
    path = cash_flow_path(tax=tax, sales=sales)
    evaluation = evaluate_market_implied_terminal_growth(path, tax, sales, rate, growth)
    return path, anchor(evaluation.modeled_enterprise_value), tax, sales, rate, evaluation


def test_only_market_implied_terminal_growth_has_an_implemented_solver():
    import stock_analyser.services.reverse_dcf_solver as module

    solvers = {
        name for name, value in inspect.getmembers(module, inspect.isfunction)
        if value.__module__ == module.__name__ and name.startswith("solve_")
    }
    assert solvers == {"solve_market_implied_terminal_growth"}


def test_consensus_revenue_and_ebit_flow_unchanged_into_cash_flow_path():
    trajectory = forward_trajectory()
    path = cash_flow_path(trajectory=trajectory)
    assert [item.revenue for item in path.periods] == [item.revenue for item in trajectory.periods]
    assert [item.ebit for item in path.periods] == [item.ebit for item in trajectory.periods]
    assert not hasattr(path, "solved_revenue_growth") and not hasattr(path, "solved_margin")


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_sales_to_capital_requires_finite_positive_value(value):
    with pytest.raises(ValueError):
        sales_to_capital(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance": ()},
        {"company_scope": None, "industry_scope": None},
        {"source_date": NOW.date() + timedelta(days=1)},
    ],
)
def test_configured_sales_to_capital_requires_provenance_scope_and_eligible_date(changes):
    with pytest.raises(ValueError):
        sales_to_capital(**changes)


def test_sales_to_capital_has_no_numeric_default():
    signature = inspect.signature(configure_sales_to_capital_evidence)
    assert signature.parameters["value"].default is inspect.Parameter.empty


def test_unverified_sales_to_capital_cannot_be_ready():
    configured = sales_to_capital()
    with pytest.raises(ValueError):
        replace(configured, source_type=SalesToCapitalSourceType.UNVERIFIED)


def test_nopat_reinvestment_and_fcff_arithmetic_are_exact():
    path = cash_flow_path()
    fy1 = path.periods[0]
    assert fy1.nopat == pytest.approx(20 * 0.75)
    assert fy1.revenue_change == pytest.approx(10)
    assert fy1.reinvestment == pytest.approx(5)
    assert fy1.fcff == pytest.approx(10)
    assert fy1.ebit_margin == pytest.approx(0.20)


def test_negative_fcff_is_retained_without_floor():
    path = cash_flow_path(
        trajectory=forward_trajectory(
            revenues=(100, 300, 600, 1_000, 1_500),
            margins=(0.01, 0.01, 0.01, 0.01, 0.01),
        ),
        sales=sales_to_capital(0.5),
    )
    assert any(item.fcff < 0 for item in path.periods)


def test_missing_fy1_base_does_not_assume_zero_reinvestment():
    missing = replace(
        actual(), status=ReverseDcfReadinessStatus.UNAVAILABLE,
        revenue=None, revenue_observation_id=None,
    )
    path = cash_flow_path(base=missing)
    assert path.fy1_reinvestment_base_status is ReverseDcfReadinessStatus.NOT_READY
    assert path.periods[0].revenue_change is None
    assert path.periods[0].reinvestment is None and path.periods[0].fcff is None
    assert path.periods[1].reinvestment is not None
    assert path.status is ReverseDcfReadinessStatus.PARTIAL


@pytest.mark.parametrize("base", [None, actual(year=2024)])
def test_fy1_reinvestment_requires_exact_preceding_canonical_revenue(base):
    missing = replace(actual(), status=ReverseDcfReadinessStatus.UNAVAILABLE, revenue=None, revenue_observation_id=None)
    selected = missing if base is None else base
    path = cash_flow_path(base=selected)
    assert path.fy1_reinvestment_base_status is ReverseDcfReadinessStatus.NOT_READY


def test_missing_fy1_reinvestment_blocks_solver_with_specific_reason():
    missing = replace(actual(), status=ReverseDcfReadinessStatus.UNAVAILABLE, revenue=None, revenue_observation_id=None)
    path = cash_flow_path(base=missing)
    result = solve_market_implied_terminal_growth(
        path, anchor(), marginal_tax(), sales_to_capital(), wacc(),
    )
    assert result.status is ReverseDcfSolverStatus.NOT_READY
    assert any("FY1_REINVESTMENT_BASE" in issue for issue in result.issues)


def test_actual_base_not_universal_market_implied_terminal_growth_blocker_in_10a_readiness():
    trajectory = forward_trajectory()
    unavailable_actual = replace(
        actual(), status=ReverseDcfReadinessStatus.UNAVAILABLE,
        revenue=None, revenue_observation_id=None,
    )
    reinvestment = build_reinvestment_readiness(
        (), (), analysis_as_of=NOW,
        approved_methodology_id="explicit-method:test",
        sales_to_capital_evidence_ids=(sales_to_capital().evidence_id,),
    )
    terminal = build_terminal_readiness(
        "USD", analysis_as_of=NOW, terminal_growth_policy_id="solve:g",
        terminal_margin_policy_id="hold-final-consensus-margin",
        steady_state_reinvestment_policy_id="sales-to-capital",
        discount_rate_compatible=True,
    )
    result = assess_reverse_dcf_readiness(
        identity(), unavailable_actual, trajectory, anchor(), reinvestment, terminal,
        analysis_as_of=NOW, valuation_currency="USD", operating_tax=marginal_tax(), wacc=wacc(),
    )
    formulation = next(
        item for item in result.formulation_readiness
        if item.formulation is ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_GROWTH
    )
    assert ReverseDcfReadinessStage.ACTUAL_BASE not in formulation.blocking_stages


@pytest.mark.parametrize(
    ("tax", "sales", "tax_status", "sales_status"),
    [
        (marginal_tax(status=DiscountRateEvidenceStatus.UNAVAILABLE), sales_to_capital(), ReverseDcfReadinessStatus.NOT_READY, ReverseDcfReadinessStatus.READY),
        (marginal_tax(), None, ReverseDcfReadinessStatus.READY, ReverseDcfReadinessStatus.NOT_READY),
    ],
)
def test_cash_flow_path_requires_explicit_tax_and_sales_evidence(tax, sales, tax_status, sales_status):
    path = build_reverse_dcf_cash_flow_path(forward_trajectory(), actual(), tax, sales)
    assert path.tax_status is tax_status and path.sales_to_capital_status is sales_status
    assert path.status is not ReverseDcfReadinessStatus.READY


def test_cash_flow_contract_contains_no_da_capex_nwc_or_generic_fcf_forecast():
    names = set(cash_flow_path().periods[0].__dataclass_fields__)
    assert not names & {"depreciation", "da", "capex", "working_capital", "nwc", "generic_fcf"}


@pytest.mark.parametrize(
    "rate",
    [None, wacc(status=DiscountRateReadinessStatus.NOT_READY, value=None), wacc(valuation_currency="EUR")],
)
def test_solver_requires_matching_ready_production_wacc(rate):
    path = cash_flow_path()
    result = solve_market_implied_terminal_growth(
        path, anchor(), marginal_tax(), sales_to_capital(), rate,
    )
    assert result.status is ReverseDcfSolverStatus.NOT_READY
    assert any("WACC" in issue or "align" in issue for issue in result.issues)


def test_explicit_annual_discounting_uses_one_based_fiscal_indices():
    path = cash_flow_path()
    evaluation = evaluate_market_implied_terminal_growth(
        path, marginal_tax(), sales_to_capital(), wacc(), 0.03,
    )
    expected = sum(item.fcff / (1.1 ** index) for index, item in enumerate(path.periods, start=1))
    assert evaluation.explicit_fcff_present_value == pytest.approx(expected)
    assert path.timing_convention is ReverseDcfTimingConvention.DISCRETE_ANNUAL_FISCAL_INDEX


def test_terminal_uses_final_consensus_margin_without_convergence():
    path = cash_flow_path()
    evaluation = evaluate_market_implied_terminal_growth(
        path, marginal_tax(), sales_to_capital(), wacc(), 0.03,
    )
    assert evaluation.terminal_margin == path.periods[-1].ebit_margin == pytest.approx(0.24)
    assert DEFAULT_REVERSE_DCF_SOLVER_POLICY.terminal_margin_policy is TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN


def test_terminal_revenue_ebit_nopat_reinvestment_fcff_and_value_formulas():
    path = cash_flow_path()
    growth = 0.03
    tax = marginal_tax()
    sales = sales_to_capital()
    rate = wacc()
    result = evaluate_market_implied_terminal_growth(path, tax, sales, rate, growth)
    final = path.periods[-1]
    revenue = final.revenue * (1 + growth)
    ebit = revenue * final.ebit_margin
    nopat = ebit * (1 - tax.value)
    reinvestment = (revenue - final.revenue) / sales.value
    fcff = nopat - reinvestment
    terminal_value = fcff / (rate.value - growth)
    assert result.terminal_revenue == pytest.approx(revenue)
    assert result.terminal_ebit == pytest.approx(ebit)
    assert result.terminal_nopat == pytest.approx(nopat)
    assert result.terminal_reinvestment == pytest.approx(reinvestment)
    assert result.terminal_fcff == pytest.approx(fcff)
    assert result.terminal_value == pytest.approx(terminal_value)


@pytest.mark.parametrize("growth", [0.10, 0.11])
def test_wacc_must_exceed_candidate_growth_without_denominator_repair(growth):
    with pytest.raises(ValueError, match="WACC must exceed"):
        evaluate_market_implied_terminal_growth(
            cash_flow_path(), marginal_tax(), sales_to_capital(), wacc(), growth,
        )


def test_modeled_ev_is_explicit_pv_plus_terminal_pv():
    evaluation = evaluate_market_implied_terminal_growth(
        cash_flow_path(), marginal_tax(), sales_to_capital(), wacc(), 0.03,
    )
    assert evaluation.modeled_enterprise_value == pytest.approx(
        evaluation.explicit_fcff_present_value + evaluation.terminal_value_present_value
    )


@pytest.mark.parametrize("known_growth", [0.03, -0.05, 0.08])
def test_bisection_recovers_known_growth_including_negative_and_high_positive(known_growth):
    path, market, tax, sales, rate, expected = known_root_inputs(known_growth)
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate)
    assert result.status is ReverseDcfSolverStatus.SOLVED
    assert result.implied_terminal_growth == pytest.approx(known_growth, abs=1e-8)
    assert result.modeled_enterprise_value == pytest.approx(expected.modeled_enterprise_value, rel=1e-8)
    assert abs(result.residual) <= max(
        DEFAULT_REVERSE_DCF_SOLVER_POLICY.ev_residual_absolute_tolerance,
        market.value * DEFAULT_REVERSE_DCF_SOLVER_POLICY.ev_residual_relative_tolerance,
    )


@pytest.mark.parametrize("boundary", ["lower", "upper"])
def test_exact_boundary_root_is_deterministic(boundary):
    policy = DEFAULT_REVERSE_DCF_SOLVER_POLICY
    rate = wacc()
    growth = policy.search_lower_bound if boundary == "lower" else rate.value - policy.wacc_minus_growth_epsilon
    path, market, tax, sales, rate, _ = known_root_inputs(growth)
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate)
    assert result.status is ReverseDcfSolverStatus.SOLVED
    assert result.implied_terminal_growth == growth
    assert result.iterations == 0


def test_no_bracket_returns_no_solution_without_widening_domain():
    path = cash_flow_path()
    result = solve_market_implied_terminal_growth(
        path, anchor(1.0), marginal_tax(), sales_to_capital(), wacc(),
    )
    assert result.status is ReverseDcfSolverStatus.NO_SOLUTION_IN_DOMAIN
    assert result.search_lower_bound == DEFAULT_REVERSE_DCF_SOLVER_POLICY.search_lower_bound
    assert result.search_upper_bound == pytest.approx(wacc().value - DEFAULT_REVERSE_DCF_SOLVER_POLICY.wacc_minus_growth_epsilon)
    assert result.final_bracket_lower == result.search_lower_bound
    assert result.final_bracket_upper == result.search_upper_bound


def test_iteration_limit_and_tolerances_are_centralized_and_audited():
    path, market, tax, sales, rate, _ = known_root_inputs(0.037)
    policy = ReverseDcfSolverPolicy(
        growth_tolerance=1e-30, ev_residual_relative_tolerance=1e-30,
        ev_residual_absolute_tolerance=1e-30, maximum_iterations=1,
    )
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate, policy=policy)
    assert result.status is ReverseDcfSolverStatus.NUMERICAL_FAILURE
    assert result.iterations == 1
    assert result.residual is not None and result.final_bracket_lower is not None


def test_structurally_flat_terminal_mapping_fails_monotonicity_check():
    margin = 0.24
    tax_rate = 0.25
    rate = 0.10
    flat_sales_to_capital = rate / (margin * (1 - tax_rate) * (1 + rate))
    sales = sales_to_capital(flat_sales_to_capital)
    path = cash_flow_path(sales=sales)
    result = solve_market_implied_terminal_growth(
        path, anchor(1_000), marginal_tax(tax_rate), sales, wacc(rate),
    )
    assert result.status is ReverseDcfSolverStatus.NUMERICAL_FAILURE
    assert "monotonic" in result.issues[0]


def test_result_retains_root_equation_inputs_bracket_residual_and_provenance():
    path, market, tax, sales, rate, _ = known_root_inputs(0.03)
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate)
    assert result.market_anchor_id == market.anchor_id
    assert result.cash_flow_path_id == path.path_id
    assert result.tax_evidence_id == tax.evidence_id
    assert result.sales_to_capital_evidence_id == sales.evidence_id
    assert result.wacc_result_id == rate.result_id
    assert result.lower_bound_function_value is not None and result.upper_bound_function_value is not None
    assert result.supporting_ids and result.policy_ids and result.provenance


def test_market_objective_is_canonical_tev_not_equity_or_share_price():
    path, market, tax, sales, rate, _ = known_root_inputs(0.03)
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate)
    assert result.observed_market_enterprise_value == market.value
    fields = set(result.__dataclass_fields__)
    assert not fields & {"current_price", "share_price", "market_cap", "equity_value", "fair_value"}


def test_result_is_immutable_and_does_not_classify_plausibility():
    path, market, tax, sales, rate, _ = known_root_inputs(0.03)
    result = solve_market_implied_terminal_growth(path, market, tax, sales, rate)
    with pytest.raises(FrozenInstanceError):
        result.implied_terminal_growth = 0.02
    fields = set(result.__dataclass_fields__)
    assert not fields & {"plausibility", "reasonable", "gdp_comparison", "stance"}


@pytest.mark.parametrize(
    "forbidden",
    [
        "newton", "brent", "scipy", "optimize", "fair_value", "target_price",
        "upside", "downside", "streamlit", "current_price", "GOOG", "GOOGL",
        "solve_wacc", "solve_margin", "solve_reinvestment",
    ],
)
def test_solver_source_has_no_forbidden_algorithm_interpretation_ui_or_ticker_logic(forbidden):
    import stock_analyser.services.reverse_dcf_solver as module

    assert forbidden not in inspect.getsource(module)


def test_cashflow_source_has_no_old_forecast_or_generic_fcf_fallback():
    import stock_analyser.services.reverse_dcf_cashflows as module

    source = inspect.getsource(module).lower()
    assert "capital_expenditure" not in source and "depreciation_amortization" not in source
    assert "working_capital" not in source and "provider_defined_fcf" not in source
    assert "requests" not in source and "streamlit" not in source


def test_solver_is_pure_and_default_pytest_path_has_no_network_dependency():
    import stock_analyser.services.reverse_dcf_solver as module

    source = inspect.getsource(module)
    assert "requests" not in source and "urllib" not in source and "socket" not in source


def test_10b_live_audit_missing_credentials_makes_zero_network_calls(monkeypatch):
    import socket
    from stock_analyser.live_reverse_dcf_10b_audit import run_live_reverse_dcf_10b_audit

    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("normal pytest attempted a network call"),
    )
    outcome = run_live_reverse_dcf_10b_audit("META", environment={}, analysis_as_of=NOW)
    assert outcome.cash_flow_path is None
    assert outcome.solver_result is None
    assert outcome.terminal_margin_policy_status is ReverseDcfReadinessStatus.UNAVAILABLE


def test_10b_live_audit_renderer_is_safe_and_exposes_each_readiness_gate():
    from stock_analyser.live_reverse_dcf_10b_audit import (
        render_live_reverse_dcf_10b_audit,
        run_live_reverse_dcf_10b_audit,
    )

    output = render_live_reverse_dcf_10b_audit(
        run_live_reverse_dcf_10b_audit("META", environment={}, analysis_as_of=NOW)
    )
    for label in (
        "Forward trajectory:", "FY1 reinvestment base:", "Operating marginal tax:",
        "Sales-to-capital:", "Production WACC:", "Market TEV anchor:",
        "Terminal margin policy:", "Solver:",
    ):
        assert label in output
    assert "api_key" not in output.lower()
    assert "current price" in output.lower()


def test_numerical_policy_has_no_terminal_growth_starting_assumption():
    fields = set(DEFAULT_REVERSE_DCF_SOLVER_POLICY.__dataclass_fields__)
    assert "terminal_growth" not in fields and "starting_growth" not in fields
    assert "search_upper_bound" not in fields
