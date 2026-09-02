from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date
import inspect
import math

import pytest

from stock_analyser.domain import (
    DiscountRateReadinessStatus,
    Frequency,
    MetricUnit,
    Provenance,
    ReverseDcfDiscountRateInput,
    ReverseDcfDiscountRateSource,
    ReverseDcfExecutionMode,
    ReverseDcfPublicationEligibility,
    ReverseDcfReadinessStatus,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfScenarioSourceCategory,
    ReverseDcfSolverStatus,
    WaccResult,
)
from stock_analyser.services import (
    configure_reverse_dcf_scenario_assumption,
    execute_reverse_dcf,
)

from test_v1_reverse_dcf_solver import (
    NOW,
    actual,
    anchor,
    forward_trajectory,
    known_root_inputs,
    marginal_tax,
    sales_to_capital,
    wacc,
)


def scenario_provenance(kind="scenario") -> tuple[Provenance, ...]:
    return (Provenance(
        provider="explicit-scenario",
        endpoint_or_dataset="synthetic-research-input",
        provider_symbol="SYN",
        retrieved_at=NOW,
        as_of_at=NOW,
        configuration_or_override_id=f"scenario:{kind}",
        source_metric=kind,
    ),)


def assumption(kind: ReverseDcfScenarioAssumptionType, value=None, **changes):
    defaults = {
        ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE: dict(
            value=90.0,
            unit=MetricUnit.CURRENCY,
            currency="USD",
            frequency=Frequency.ANNUAL,
            fiscal_year=2025,
            period_end=date(2025, 12, 31),
        ),
        ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL: dict(
            value=2.0,
            unit=MetricUnit.RATIO,
        ),
        ReverseDcfScenarioAssumptionType.WACC: dict(
            value=0.10,
            unit=MetricUnit.PERCENT_DECIMAL,
        ),
    }[kind]
    if value is not None:
        defaults["value"] = value
    defaults.update(dict(
        target_security_id="security:synthetic:10b",
        target_issuer_id="issuer:synthetic:10b",
        analysis_as_of=NOW,
        assumption_type=kind,
        source_category=ReverseDcfScenarioSourceCategory.USER_SUPPLIED,
        source_label="Fictitious analyst scenario",
        methodology_label="Explicit synthetic research input",
        rationale="Exercise execution provenance without production assumptions",
        entered_by="synthetic-test-analyst",
        provenance=scenario_provenance(kind.value),
    ))
    defaults.update(changes)
    return configure_reverse_dcf_scenario_assumption(**defaults)


def all_scenario_assumptions(*, wacc_value=0.10, sales_value=2.0, revenue=90.0):
    return (
        assumption(ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE, revenue),
        assumption(ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL, sales_value),
        assumption(ReverseDcfScenarioAssumptionType.WACC, wacc_value),
    )


def execute_scenario(growth=0.03, *, assumptions=None, actual_base=None, sales=None, rate=None):
    _, market, _, _, _, _ = known_root_inputs(growth)
    return execute_reverse_dcf(
        execution_mode=ReverseDcfExecutionMode.EXPLICIT_SCENARIO,
        trajectory=forward_trajectory(),
        actual_base=actual_base,
        market_anchor=market,
        operating_tax=marginal_tax(),
        canonical_sales_to_capital=sales,
        production_wacc=rate,
        scenario_assumptions=(
            all_scenario_assumptions() if assumptions is None else tuple(assumptions)
        ),
    )


def execute_canonical(growth=0.03, *, rate=None, assumptions=()):
    _, market, _, sales, ready_rate, _ = known_root_inputs(growth)
    return execute_reverse_dcf(
        execution_mode=ReverseDcfExecutionMode.CANONICAL_EVIDENCE,
        trajectory=forward_trajectory(),
        actual_base=actual(),
        market_anchor=market,
        operating_tax=marginal_tax(),
        canonical_sales_to_capital=sales,
        production_wacc=ready_rate if rate is None else rate,
        scenario_assumptions=tuple(assumptions),
    )


def test_execution_modes_are_exactly_canonical_and_explicit_scenario():
    assert {item.value for item in ReverseDcfExecutionMode} == {
        "canonical_evidence", "explicit_scenario",
    }


def test_no_auto_or_fallback_execution_mode_exists():
    names = {item.name for item in ReverseDcfExecutionMode}
    assert "AUTO" not in names and "FALLBACK" not in names


def test_authorized_assumption_types_are_exactly_the_three_current_blockers():
    assert {item.value for item in ReverseDcfScenarioAssumptionType} == {
        "preceding_annual_revenue", "sales_to_capital", "wacc",
    }


def test_scenario_source_categories_never_claim_canonical_verification():
    assert {item.value for item in ReverseDcfScenarioSourceCategory} == {
        "user_supplied", "external_research", "configured_scenario",
    }


def test_external_research_assumption_requires_a_nonfuture_source_date():
    with pytest.raises(ValueError):
        assumption(
            ReverseDcfScenarioAssumptionType.WACC,
            source_category=ReverseDcfScenarioSourceCategory.EXTERNAL_RESEARCH,
            source_date=None,
        )
    with pytest.raises(ValueError):
        assumption(
            ReverseDcfScenarioAssumptionType.WACC,
            source_category=ReverseDcfScenarioSourceCategory.EXTERNAL_RESEARCH,
            source_date=date(2026, 8, 30),
        )


def test_no_scenario_numeric_default_exists():
    signature = inspect.signature(configure_reverse_dcf_scenario_assumption)
    assert signature.parameters["value"].default is inspect.Parameter.empty
    result = execute_scenario(assumptions=())
    assert result.solver_result.status is ReverseDcfSolverStatus.NOT_READY
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.UNAVAILABLE


def test_fully_canonical_execution_is_solved_and_canonically_publishable():
    result = execute_canonical()
    assert result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.CANONICAL
    assert result.canonical_input_status is ReverseDcfReadinessStatus.READY
    assert result.scenario_assumption_ids == ()
    assert result.solver_result.discount_rate_source is ReverseDcfDiscountRateSource.PRODUCTION_WACC


def test_scenario_wacc_cannot_rescue_canonical_mode():
    unavailable = replace(
        wacc(), value=None, status=DiscountRateReadinessStatus.NOT_READY,
    )
    result = execute_canonical(
        rate=unavailable,
        assumptions=(assumption(ReverseDcfScenarioAssumptionType.WACC),),
    )
    assert result.solver_result.status is ReverseDcfSolverStatus.NOT_READY
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.UNAVAILABLE
    assert "PRODUCTION_WACC" in result.canonical_blockers


def test_explicit_scenario_execution_solves_without_production_wacc():
    result = execute_scenario()
    assert result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    assert result.execution_mode is ReverseDcfExecutionMode.EXPLICIT_SCENARIO
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert result.canonical_input_status is ReverseDcfReadinessStatus.NOT_READY
    assert set(result.canonical_blockers) == {
        "FY1_REINVESTMENT_BASE", "SALES_TO_CAPITAL", "PRODUCTION_WACC",
    }


def test_scenario_wacc_is_a_separate_input_not_a_fake_wacc_result():
    result = execute_scenario()
    discount = result.execution_inputs
    assert isinstance(result.solver_result.wacc_result_id, type(None))
    assert result.solver_result.discount_rate_source is ReverseDcfDiscountRateSource.SCENARIO_WACC
    assert discount.production_wacc_status is DiscountRateReadinessStatus.NOT_READY
    assert not isinstance(discount, WaccResult)


@pytest.mark.parametrize("value", [0, -0.01, 1, 10, float("inf"), float("nan")])
def test_scenario_wacc_enforces_positive_finite_decimal_rate_semantics(value):
    with pytest.raises(ValueError):
        assumption(ReverseDcfScenarioAssumptionType.WACC, value)


def test_scenario_wacc_requires_decimal_rate_unit():
    with pytest.raises(ValueError):
        assumption(
            ReverseDcfScenarioAssumptionType.WACC,
            unit=MetricUnit.RATIO,
        )


@pytest.mark.parametrize(
    "missing, expected",
    [
        (ReverseDcfScenarioAssumptionType.WACC, "scenario WACC"),
        (ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL, "sales-to-capital"),
        (ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE, "preceding annual revenue"),
    ],
)
def test_missing_required_scenario_assumption_blocks_when_canonical_input_absent(missing, expected):
    supplied = tuple(item for item in all_scenario_assumptions() if item.assumption_type is not missing)
    result = execute_scenario(assumptions=supplied)
    assert result.solver_result.status is ReverseDcfSolverStatus.NOT_READY
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.UNAVAILABLE
    assert any(expected.lower() in issue.lower() for issue in result.issues)


def test_duplicate_scenario_assumption_type_fails_closed():
    duplicate = assumption(ReverseDcfScenarioAssumptionType.WACC)
    with pytest.raises(ValueError, match="duplicate"):
        execute_scenario(assumptions=(*all_scenario_assumptions(), duplicate))


def test_scenario_assumption_target_identity_and_snapshot_must_match():
    for changes in (
        {"target_security_id": "security:other"},
        {"analysis_as_of": NOW.replace(year=2025)},
    ):
        supplied = list(all_scenario_assumptions())
        supplied[-1] = assumption(ReverseDcfScenarioAssumptionType.WACC, **changes)
        with pytest.raises(ValueError, match="identity/snapshot"):
            execute_scenario(assumptions=supplied)


@pytest.mark.parametrize("kind", list(ReverseDcfScenarioAssumptionType))
def test_every_scenario_assumption_requires_explicit_provenance(kind):
    with pytest.raises(ValueError):
        assumption(kind, provenance=())


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_scenario_sales_to_capital_is_finite_positive_and_dimensionless(value):
    with pytest.raises(ValueError):
        assumption(ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL, value)


def test_scenario_sales_to_capital_requires_ratio_unit():
    with pytest.raises(ValueError):
        assumption(
            ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL,
            unit=MetricUnit.CURRENCY,
        )


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_scenario_preceding_revenue_is_finite_and_positive(value):
    with pytest.raises(ValueError):
        assumption(ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE, value)


def test_scenario_preceding_revenue_requires_currency_alignment():
    assumptions = list(all_scenario_assumptions())
    assumptions[0] = assumption(
        ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE,
        currency="GBP",
    )
    with pytest.raises(ValueError, match="aligned annual fiscal period"):
        execute_scenario(assumptions=assumptions)


@pytest.mark.parametrize(
    "changes",
    [
        {"fiscal_year": 2024},
        {"period_end": date(2025, 9, 30)},
    ],
)
def test_preceding_revenue_period_must_be_exactly_before_fy1(changes):
    assumptions = list(all_scenario_assumptions())
    assumptions[0] = assumption(
        ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE,
        **changes,
    )
    with pytest.raises(ValueError, match="aligned annual fiscal period"):
        execute_scenario(assumptions=assumptions)


def test_ltm_revenue_is_rejected_as_scenario_opening_base():
    with pytest.raises(ValueError):
        assumption(
            ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE,
            frequency=Frequency.LTM,
        )


def test_scenario_revenue_never_becomes_a_canonical_actual():
    result = execute_scenario()
    assert result.execution_inputs.canonical_actual_base_id is None
    assert result.execution_inputs.preceding_revenue_input_id is not None
    assert "CanonicalActualSelection" not in inspect.getsource(
        __import__("stock_analyser.services.reverse_dcf_execution", fromlist=["x"])
    )


def test_observed_trajectory_market_identity_and_tax_cannot_be_overridden():
    result = execute_scenario()
    assert result.solver_result.trajectory_id == forward_trajectory().trajectory_id
    assert result.solver_result.market_anchor_id == known_root_inputs(0.03)[1].anchor_id
    assert result.solver_result.tax_evidence_id == marginal_tax().evidence_id
    assert result.solver_result.target_security_id == "security:synthetic:10b"
    assert not hasattr(ReverseDcfScenarioAssumptionType, "REVENUE")
    assert not hasattr(ReverseDcfScenarioAssumptionType, "EBIT")
    assert not hasattr(ReverseDcfScenarioAssumptionType, "MARKET_TEV")
    assert not hasattr(ReverseDcfScenarioAssumptionType, "TAX")


def test_final_consensus_margin_and_growth_are_retained_without_interpretation():
    result = execute_scenario()
    final = forward_trajectory().periods[-1]
    assert result.final_consensus_revenue_growth == final.revenue_growth
    assert result.final_consensus_ebit_margin == final.operating_margin
    assert result.solver_result.terminal_margin == final.operating_margin
    assert not hasattr(result, "expectation_gap_judgment")
    assert not hasattr(result, "expectation_score")


def test_scenario_and_canonical_modes_reuse_identical_10b_mathematics():
    canonical = execute_canonical()
    scenario = execute_scenario()
    for name in (
        "implied_terminal_growth", "terminal_revenue", "terminal_ebit", "terminal_nopat",
        "terminal_reinvestment", "terminal_fcff", "terminal_value",
        "explicit_fcff_present_value", "terminal_value_present_value",
        "modeled_enterprise_value", "observed_market_enterprise_value",
    ):
        assert getattr(scenario.solver_result, name) == pytest.approx(
            getattr(canonical.solver_result, name)
        )


def test_execution_module_reuses_one_solver_and_defines_no_second_root_solver():
    import stock_analyser.services.reverse_dcf_execution as module

    source = inspect.getsource(module)
    assert "solve_market_implied_terminal_growth(" in source
    assert "bisection" not in source.lower()
    assert not any(
        name.startswith("solve_") for name, value in inspect.getmembers(module, inspect.isfunction)
        if value.__module__ == module.__name__
    )


def test_one_scenario_override_is_scenario_only_while_canonical_evidence_is_retained():
    result = execute_scenario(
        assumptions=(assumption(ReverseDcfScenarioAssumptionType.WACC),),
        actual_base=actual(),
        sales=sales_to_capital(),
        rate=wacc(),
    )
    assert result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert len(result.scenario_assumption_ids) == 1
    assert result.execution_inputs.canonical_actual_base_id == actual().evidence_id
    assert result.execution_inputs.canonical_sales_to_capital_evidence_id == sales_to_capital().evidence_id
    assert result.execution_inputs.production_wacc_result_id == wacc().result_id


def test_three_scenario_overrides_remain_scenario_only_and_centrally_ineligible():
    result = execute_scenario()
    assert len(result.scenario_assumption_ids) == 3
    assert result.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert result.central_valuation_eligible is False


def test_canonical_expectation_result_is_also_not_fair_value_or_central_valuation():
    result = execute_canonical()
    assert result.central_valuation_eligible is False
    for name in ("fair_value", "intrinsic_value", "target_price", "upside", "downside"):
        assert not hasattr(result, name)
        assert not hasattr(result.solver_result, name)


def test_scenario_assumptions_and_safe_labels_are_retained_in_provenance():
    supplied = all_scenario_assumptions()
    result = execute_scenario(assumptions=supplied)
    assert result.scenario_assumption_ids == tuple(item.assumption_id for item in supplied)
    assert len(result.scenario_assumption_labels) == 3
    assert any("10%" in label for label in result.scenario_assumption_labels)
    assert any("2x" in label for label in result.scenario_assumption_labels)
    assert any("90 USD FY2025" in label for label in result.scenario_assumption_labels)
    assert all(item.provenance[0] in result.provenance for item in supplied)


def test_explicit_override_semantics_are_stable_and_do_not_mutate_canonical_evidence():
    canonical_sales = sales_to_capital(3.0)
    production = wacc(0.12)
    supplied = all_scenario_assumptions(wacc_value=0.10, sales_value=2.0)
    result = execute_scenario(
        assumptions=supplied, actual_base=actual(80), sales=canonical_sales, rate=production,
    )
    assert result.solver_result.discount_rate_source is ReverseDcfDiscountRateSource.SCENARIO_WACC
    assert result.execution_inputs.canonical_sales_to_capital_evidence_id == canonical_sales.evidence_id
    assert result.execution_inputs.production_wacc_result_id == production.result_id
    assert canonical_sales.value == 3.0 and production.value == 0.12


def test_scenario_result_is_never_promoted_and_requires_new_canonical_execution():
    scenario = execute_scenario()
    canonical = execute_canonical()
    assert scenario.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert canonical.publication_eligibility is ReverseDcfPublicationEligibility.CANONICAL
    assert scenario.execution_result_id != canonical.execution_result_id


@pytest.mark.parametrize("growth", [-0.05, 0.08])
def test_scenario_retains_negative_and_high_solved_growth_without_judgment(growth):
    result = execute_scenario(growth)
    assert result.solver_result.implied_terminal_growth == pytest.approx(growth, abs=1e-8)
    assert result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    joined = " ".join((*result.issues, *result.warnings)).lower()
    assert not any(word in joined for word in ("reasonable", "unreasonable", "aggressive", "conservative", "gdp"))


def test_scenario_contracts_are_immutable():
    item = assumption(ReverseDcfScenarioAssumptionType.WACC)
    result = execute_scenario()
    with pytest.raises(FrozenInstanceError):
        item.value = 0.2
    with pytest.raises(FrozenInstanceError):
        result.publication_eligibility = ReverseDcfPublicationEligibility.CANONICAL


def test_execution_has_no_sensitivity_grid_provider_price_family_aggregation_or_ui_path():
    import stock_analyser.services.reverse_dcf_execution as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "sensitivity", "heatmap", "current_price", "share_price", "fair_value",
        "target_price", "own_history", "peer_family", "streamlit", "ticker",
        "buy", "sell", "provider adapter", "requests", "httpx", "urllib",
    )
    assert all(token not in source for token in forbidden)


def test_default_execution_path_makes_zero_network_calls(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "create_connection",
        lambda *args, **kwargs: pytest.fail("reverse-DCF execution attempted network access"),
    )
    assert execute_scenario().solver_result.status is ReverseDcfSolverStatus.SOLVED


def test_10c_audit_missing_credentials_makes_zero_network_calls(monkeypatch):
    import socket
    from stock_analyser.live_reverse_dcf_10c_audit import run_live_reverse_dcf_10c_audit

    monkeypatch.setattr(
        socket, "create_connection",
        lambda *args, **kwargs: pytest.fail("10C readiness audit attempted network access"),
    )
    outcome = run_live_reverse_dcf_10c_audit("META", environment={}, analysis_as_of=NOW)
    assert outcome.canonical_execution is None
    assert outcome.scenario_execution is None


def test_10c_audit_renderer_reports_assumption_free_waiting_state():
    from stock_analyser.live_reverse_dcf_10c_audit import (
        render_live_reverse_dcf_10c_audit,
        run_live_reverse_dcf_10c_audit,
    )

    output = render_live_reverse_dcf_10c_audit(
        run_live_reverse_dcf_10c_audit("META", environment={}, analysis_as_of=NOW)
    )
    assert "Canonical mode: unavailable" in output
    assert "Scenario mode: unavailable" in output
    assert "Implied terminal growth: unavailable" in output
    assert "api_key" not in output.lower()
