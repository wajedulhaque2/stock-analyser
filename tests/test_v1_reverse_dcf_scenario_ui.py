from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
import ast
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.application import (
    REVENUE_INPUT_SCALE,
    ReverseDcfScenarioExecutionContext,
    ReverseDcfScenarioFormInput,
    ReverseDcfScenarioRunStatus,
    V1ResearchCoordinator,
    normalize_reverse_dcf_scenario_input,
    run_v1_reverse_dcf_scenario,
)
from stock_analyser.domain import (
    ReverseDcfExecutionMode,
    ReverseDcfPublicationEligibility,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfScenarioSourceCategory,
    ReverseDcfSolverStatus,
)
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from stock_analyser.ui.v1_report import (
    _render_interactive_scenario_result,
    render_stock_research_report,
)
from stock_analyser.ui.v1_route import (
    V1_ANALYSIS_AS_OF_SESSION_KEY,
    V1_BUILD_RESULT_SESSION_KEY,
    V1_CURRENT_SYMBOL_SESSION_KEY,
    V1_REPORT_SESSION_KEY,
    V1_SCENARIO_REVENUE_INPUT_KEY,
    V1_SCENARIO_RESULT_SESSION_KEY,
    V1_SCENARIO_SALES_INPUT_KEY,
    V1_SCENARIO_TARGET_SESSION_KEY,
    V1_SCENARIO_WACC_INPUT_KEY,
    _clear_scenario_state,
    _render_scenario_workflow,
    _store_safe_result,
)
from test_v1_research_report import NOW, complete_report, partial_report, report, identity, trajectory
from test_v1_reverse_dcf_solver import anchor, known_root_inputs, marginal_tax
from test_v1_report_ui import CaptureStreamlit


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_scenario.py"
RESEARCH_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_research.py"
REPORT_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
LEGACY_SOURCE = ROOT / "app.py"


def scenario_context(*, growth=0.03, no_solution=False, canonical=False):
    source_report = complete_report() if canonical else partial_report()
    scale = REVENUE_INPUT_SCALE
    source_trajectory = trajectory()
    periods = tuple(replace(
        item,
        revenue=item.revenue * scale,
        ebit=item.ebit * scale,
        ebitda=(item.ebitda * scale if item.ebitda is not None else None),
    ) for item in source_trajectory.periods)
    scaled_trajectory = replace(
        source_trajectory,
        trajectory_id="trajectory:12h:scaled",
        periods=periods,
    )
    if no_solution:
        market = anchor(1.0)
    else:
        _, market, _, _, _, _ = known_root_inputs(growth)
    scaled_anchor = replace(
        market,
        anchor_id="anchor:12h:scaled",
        target_security_id=source_report.target_security_id,
        target_issuer_id=source_report.target_issuer_id,
        analysis_as_of=source_report.analysis_as_of,
        value=market.value * scale,
    )
    tax = replace(marginal_tax(), analysis_as_of=source_report.analysis_as_of)
    context = ReverseDcfScenarioExecutionContext(
        target_security_id=source_report.target_security_id,
        target_issuer_id=source_report.target_issuer_id,
        analysis_as_of=source_report.analysis_as_of,
        valuation_currency="USD",
        trajectory=scaled_trajectory,
        market_anchor=scaled_anchor,
        operating_tax=tax,
    )
    return source_report, context


def solved_run(*, canonical=False):
    source_report, context = scenario_context(canonical=canonical)
    result = run_v1_reverse_dcf_scenario(
        source_report,
        context,
        ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    return source_report, context, result


class Scope(AbstractContextManager):
    def __init__(self, ui):
        self.ui = ui

    def __enter__(self):
        return self.ui

    def __exit__(self, exc_type, exc, traceback):
        return False


class ScenarioUI:
    def __init__(self, *, inputs=None, submits=None, session=None):
        self.inputs = dict(inputs or {})
        self.submits = dict(submits or {})
        self.session_state = {} if session is None else session
        self.markdowns = []
        self.tables = []
        self.expanders = []
        self.labels = []

    def markdown(self, body, **kwargs):
        self.markdowns.append(str(body))

    def dataframe(self, rows, **kwargs):
        self.tables.append([dict(row) for row in rows])

    def expander(self, label, *, expanded=False):
        self.expanders.append((label, expanded))
        return Scope(self)

    def form(self, key, **kwargs):
        return Scope(self)

    def columns(self, count):
        return tuple(Scope(self) for _ in range(count if isinstance(count, int) else len(count)))

    def text_input(self, label, *, key, **kwargs):
        self.labels.append((label, kwargs))
        value = self.inputs.get(key, self.session_state.get(key, ""))
        self.session_state[key] = value
        return value

    def form_submit_button(self, label, *, on_click=None, args=(), **kwargs):
        clicked = bool(self.submits.get(label, False))
        if clicked and on_click is not None:
            on_click(*args)
        return clicked

    @property
    def text(self):
        return "\n".join((*self.markdowns, *(str(item) for item in self.tables)))


def test_context_is_immutable_provider_independent_and_exactly_snapshot_bound():
    source, context = scenario_context()
    assert context.identity_key == (
        source.target_security_id, source.target_issuer_id, source.analysis_as_of.isoformat(),
    )
    assert context.required_preceding_fiscal_year == 2025
    assert context.required_preceding_period_end.isoformat() == "2025-12-31"
    names = {item.name.lower() for item in fields(context)}
    assert names.isdisjoint({"payload", "response", "headers", "credential", "api_key", "companykey", "url"})


def test_research_coordinator_retains_safe_scenario_context_with_report_and_cache():
    source, context = scenario_context()
    reverse = SimpleNamespace(
        trajectory=context.trajectory,
        market_anchor=context.market_anchor,
        operating_tax=context.operating_tax,
        actual_base=None,
        canonical_sales_to_capital=None,
        production_wacc=None,
        safe_issues=(),
    )
    outcome = SimpleNamespace(
        report=source, reverse_dcf_audit=reverse, market_audit=None, safe_notes=(),
    )
    coordinator = V1ResearchCoordinator(runner=lambda *args, **kwargs: outcome)
    first = coordinator.build("SYN", environment={}, analysis_as_of=source.analysis_as_of)
    second = coordinator.build("SYN", environment={}, analysis_as_of=source.analysis_as_of)
    assert first.scenario_context == context
    assert second.scenario_context == context


def test_context_is_absent_when_normalized_anchor_inputs_are_missing():
    source, _ = scenario_context()
    outcome = SimpleNamespace(
        report=source,
        reverse_dcf_audit=SimpleNamespace(trajectory=None, market_anchor=None, safe_issues=()),
        market_audit=None,
        safe_notes=(),
    )
    result = V1ResearchCoordinator(runner=lambda *args, **kwargs: outcome).build(
        "SYN", environment={}, analysis_as_of=source.analysis_as_of,
    )
    assert result.scenario_context is None


def test_empty_inputs_have_no_defaults_and_report_all_required_fields():
    normalized, errors = normalize_reverse_dcf_scenario_input(ReverseDcfScenarioFormInput())
    assert normalized is None
    assert errors == (
        "WACC is required.",
        "Sales-to-capital is required.",
        "Preceding annual revenue is required.",
    )


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_or_zero_wacc_is_rejected_safely(value):
    normalized, errors = normalize_reverse_dcf_scenario_input(
        ReverseDcfScenarioFormInput(value, "2", "90"),
    )
    assert normalized is None and any("WACC" in item for item in errors)


def test_wacc_percentage_sales_ratio_and_revenue_billions_normalize_once():
    normalized, errors = normalize_reverse_dcf_scenario_input(
        ReverseDcfScenarioFormInput("10", "2.0", "90"),
    )
    assert errors == ()
    assert normalized == (0.10, 2.0, 90 * REVENUE_INPUT_SCALE)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (ReverseDcfScenarioFormInput("10", "0", "90"), "Sales-to-capital"),
        (ReverseDcfScenarioFormInput("10", "2", "0"), "Preceding annual revenue"),
        (ReverseDcfScenarioFormInput("100", "2", "90"), "less than 100%"),
    ],
)
def test_other_basic_input_boundaries_fail_without_tracebacks(values, expected):
    normalized, errors = normalize_reverse_dcf_scenario_input(values)
    assert normalized is None and any(expected in item for item in errors)


def test_solved_scenario_reuses_10c_and_recovers_known_root():
    _, _, result = solved_run()
    assert result.status is ReverseDcfScenarioRunStatus.EXECUTED
    assert result.execution_result.execution_mode is ReverseDcfExecutionMode.EXPLICIT_SCENARIO
    assert result.execution_result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    assert result.execution_result.solver_result.implied_terminal_growth == pytest.approx(0.03, abs=1e-8)


def test_exactly_three_authorized_assumptions_use_domain_units_and_user_provenance():
    _, _, result = solved_run()
    assumptions = result.assumptions
    assert len(assumptions) == 3
    assert {item.assumption_type for item in assumptions} == set(ReverseDcfScenarioAssumptionType)
    assert next(item for item in assumptions if item.assumption_type is ReverseDcfScenarioAssumptionType.WACC).value == 0.10
    assert all(item.source_category is ReverseDcfScenarioSourceCategory.USER_SUPPLIED for item in assumptions)
    assert {item.source_label for item in assumptions} == {"User-supplied V1 scenario"}
    assert {item.entered_by for item in assumptions} == {"interactive_user"}
    assert all(item.provenance and item.provenance[0].provider == "user-supplied" for item in assumptions)


def test_solved_interactive_result_is_scenario_only_and_centrally_ineligible():
    _, _, result = solved_run()
    execution = result.execution_result
    assert execution.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert execution.central_valuation_eligible is False


def test_scenario_execution_does_not_mutate_report_publication_or_families():
    source, context = scenario_context()
    before = asdict(source)
    result = run_v1_reverse_dcf_scenario(source, context, ReverseDcfScenarioFormInput("10", "2", "90"))
    assert result.status is ReverseDcfScenarioRunStatus.EXECUTED
    assert asdict(source) == before


def test_canonical_not_ready_remains_not_ready_after_scenario_success():
    source, _, result = solved_run()
    assert source.expectations.display_label == "NOT READY"
    assert result.execution_result.solver_result.status is ReverseDcfSolverStatus.SOLVED
    assert source.valuation_summary.publication_label == "Unresolved"


def test_identity_mismatch_and_snapshot_mismatch_fail_closed():
    source, context = scenario_context()
    other_security = "security:other:12h"
    other_issuer = "issuer:other:12h"
    wrong_identity_context = replace(
        context,
        target_security_id=other_security,
        target_issuer_id=other_issuer,
        trajectory=replace(
            context.trajectory,
            target_security_id=other_security,
            target_issuer_id=other_issuer,
        ),
        market_anchor=replace(
            context.market_anchor,
            target_security_id=other_security,
            target_issuer_id=other_issuer,
        ),
    )
    identity_result = run_v1_reverse_dcf_scenario(
        source, wrong_identity_context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    with pytest.raises(ValueError, match="identity and snapshot"):
        replace(context, analysis_as_of=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert identity_result.status is ReverseDcfScenarioRunStatus.UNAVAILABLE
    assert "identity and snapshot" in identity_result.safe_message


def test_no_solution_is_controlled_and_search_domain_is_not_widened():
    source, context = scenario_context(no_solution=True)
    result = run_v1_reverse_dcf_scenario(source, context, ReverseDcfScenarioFormInput("10", "2", "90"))
    solver = result.execution_result.solver_result
    assert solver.status is ReverseDcfSolverStatus.NO_SOLUTION_IN_DOMAIN
    assert solver.search_lower_bound is not None and solver.search_upper_bound is not None


def test_default_scenario_form_is_closed_empty_and_not_executed(monkeypatch):
    source, context = scenario_context()
    ui = ScenarioUI()
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.run_v1_reverse_dcf_scenario",
        lambda *args, **kwargs: pytest.fail("scenario auto-executed"),
    )
    _render_scenario_workflow(ui, source, context)
    assert ui.expanders == [("Scenario analysis", False)]
    assert [label for label, _ in ui.labels] == [
        "WACC (%)", "Sales-to-capital (x)", "Preceding annual revenue (USD billions)",
    ]
    assert all(ui.session_state[key] == "" for key in (
        V1_SCENARIO_WACC_INPUT_KEY, V1_SCENARIO_SALES_INPUT_KEY, V1_SCENARIO_REVENUE_INPUT_KEY,
    ))
    assert V1_SCENARIO_RESULT_SESSION_KEY not in ui.session_state


def test_form_exposes_only_three_economic_inputs_and_required_period_and_units():
    source, context = scenario_context()
    ui = ScenarioUI()
    _render_scenario_workflow(ui, source, context)
    labels = " ".join(label for label, _ in ui.labels).lower()
    assert len(ui.labels) == 3
    assert "wacc" in labels and "sales-to-capital" in labels and "preceding annual revenue" in labels
    assert all(term not in labels for term in ("terminal growth", "terminal margin", "tax", "share price", "fy1 revenue"))
    assert "31 Dec 2025" in ui.text and "USD billions" in ui.text


def test_widget_changes_do_not_execute_without_form_submit(monkeypatch):
    source, context = scenario_context()
    ui = ScenarioUI(inputs={
        V1_SCENARIO_WACC_INPUT_KEY: "10",
        V1_SCENARIO_SALES_INPUT_KEY: "2",
        V1_SCENARIO_REVENUE_INPUT_KEY: "90",
    })
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.run_v1_reverse_dcf_scenario",
        lambda *args, **kwargs: pytest.fail("scenario executed without submit"),
    )
    _render_scenario_workflow(ui, source, context)


def test_explicit_submit_executes_once_and_disclosure_is_persistent(monkeypatch):
    source, context = scenario_context()
    calls = []
    expected = run_v1_reverse_dcf_scenario(source, context, ReverseDcfScenarioFormInput("10", "2", "90"))
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.run_v1_reverse_dcf_scenario",
        lambda *args: calls.append(args) or expected,
    )
    ui = ScenarioUI(inputs={
        V1_SCENARIO_WACC_INPUT_KEY: "10",
        V1_SCENARIO_SALES_INPUT_KEY: "2",
        V1_SCENARIO_REVENUE_INPUT_KEY: "90",
    }, submits={"Run scenario": True})
    _render_scenario_workflow(ui, source, context)
    assert len(calls) == 1
    assert ui.session_state[V1_SCENARIO_RESULT_SESSION_KEY] is expected
    assert "do not become canonical valuation evidence" in ui.text


def test_validation_errors_render_as_safe_factual_labels():
    source, context = scenario_context()
    ui = ScenarioUI(submits={"Run scenario": True})
    _render_scenario_workflow(ui, source, context)
    assert "Scenario input validation" in ui.text
    assert "WACC is required" in ui.text
    assert "Traceback" not in ui.text


def test_assumptions_display_from_executed_result_not_changed_widgets():
    source, context, expected = solved_run()
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: expected,
        V1_SCENARIO_TARGET_SESSION_KEY: context.identity_key,
    }
    ui = ScenarioUI(inputs={
        V1_SCENARIO_WACC_INPUT_KEY: "55",
        V1_SCENARIO_SALES_INPUT_KEY: "8",
        V1_SCENARIO_REVENUE_INPUT_KEY: "999",
    }, session=session)
    _render_scenario_workflow(ui, source, context)
    rows = next(table for table in ui.tables if table and "Assumption" in table[0])
    assert {row["Value"] for row in rows} == {"10.00%", "2.0000", "$90.0bn"}


def test_reset_clears_inputs_and_result_without_execution(monkeypatch):
    source, context, result = solved_run()
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: result,
        V1_SCENARIO_TARGET_SESSION_KEY: context.identity_key,
        V1_SCENARIO_WACC_INPUT_KEY: "10",
        V1_SCENARIO_SALES_INPUT_KEY: "2",
        V1_SCENARIO_REVENUE_INPUT_KEY: "90",
    }
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.run_v1_reverse_dcf_scenario",
        lambda *args, **kwargs: pytest.fail("reset executed scenario"),
    )
    _render_scenario_workflow(ScenarioUI(submits={"Reset scenario": True}, session=session), source, context)
    assert all(key not in session for key in (
        V1_SCENARIO_RESULT_SESSION_KEY, V1_SCENARIO_TARGET_SESSION_KEY,
        V1_SCENARIO_WACC_INPUT_KEY, V1_SCENARIO_SALES_INPUT_KEY, V1_SCENARIO_REVENUE_INPUT_KEY,
    ))


def test_new_report_store_and_rebuild_clear_prior_scenario_state():
    source, context, scenario = solved_run()
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: scenario,
        V1_SCENARIO_TARGET_SESSION_KEY: context.identity_key,
        V1_SCENARIO_WACC_INPUT_KEY: "10",
    }
    result = SimpleNamespace(
        requested_symbol="SYN", analysis_as_of=source.analysis_as_of, report=source,
    )
    _store_safe_result(session, result)
    assert V1_SCENARIO_RESULT_SESSION_KEY not in session
    assert session[V1_REPORT_SESSION_KEY] is source
    assert session[V1_CURRENT_SYMBOL_SESSION_KEY] == "SYN"
    assert session[V1_ANALYSIS_AS_OF_SESSION_KEY] == source.analysis_as_of


def test_stale_scenario_target_cannot_leak_across_tickers():
    source, context, scenario = solved_run()
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: scenario,
        V1_SCENARIO_TARGET_SESSION_KEY: ("security:old", "issuer:old", NOW.isoformat()),
    }
    ui = ScenarioUI(session=session)
    _render_scenario_workflow(ui, source, context)
    assert V1_SCENARIO_RESULT_SESSION_KEY not in session


def test_missing_context_is_factual_and_never_attempts_provider_or_execution(monkeypatch):
    source = partial_report()
    monkeypatch.setattr(
        "stock_analyser.ui.v1_route.run_v1_reverse_dcf_scenario",
        lambda *args, **kwargs: pytest.fail("execution attempted without context"),
    )
    ui = ScenarioUI()
    _render_scenario_workflow(ui, source, None)
    assert "execution context is unavailable" in ui.text
    assert "No provider request was made" in ui.text
    assert ui.labels == []


def test_canonical_and_scenario_results_are_both_visible_and_distinct():
    source, _, result = solved_run(canonical=True)
    ui = CaptureStreamlit()
    render_stock_research_report(
        source,
        streamlit_module=ui,
        expectations_extension=lambda target, report: _render_interactive_scenario_result(target, result),
    )
    text = ui.text
    assert "Execution · <strong>Canonical</strong>" in text
    assert "Execution · <strong>Explicit scenario</strong>" in text
    assert text.index("Execution · <strong>Canonical</strong>") < text.index("Execution · <strong>Explicit scenario</strong>")


def test_canonical_not_ready_blockers_remain_visible_beside_solved_scenario():
    source, _, result = solved_run()
    ui = CaptureStreamlit()
    render_stock_research_report(
        source,
        streamlit_module=ui,
        expectations_extension=lambda target, report: _render_interactive_scenario_result(target, result),
    )
    assert "NOT READY" in ui.text
    assert all(blocker in ui.text for blocker in source.expectations.blocking_reasons)
    assert "Market-implied terminal growth" in ui.text and "3.00%" in ui.text


def test_solved_scenario_result_has_required_labels_and_no_valuation_language():
    _, _, result = solved_run()
    ui = CaptureStreamlit()
    _render_interactive_scenario_result(ui, result)
    text = ui.text
    assert 'class="v1-scenario-label">Scenario' in text
    assert "Publication · <strong>Scenario only</strong>" in text
    assert "Central valuation eligibility · <strong>Ineligible</strong>" in text
    assert "Assumptions used" in text
    assert all(term not in text.lower() for term in ("fair value", "target price", "upside", "downside", "margin of safety"))


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (ReverseDcfSolverStatus.NOT_READY, "not ready for the approved solver"),
        (ReverseDcfSolverStatus.NO_SOLUTION_IN_DOMAIN, "No terminal-growth solution was found"),
        (ReverseDcfSolverStatus.NUMERICAL_FAILURE, "approved numerical solver did not complete"),
        (ReverseDcfSolverStatus.UNAVAILABLE, "unavailable for this snapshot"),
    ],
)
def test_controlled_unsolved_states_render_without_judgment(status, message):
    _, _, solved = solved_run()
    synthetic = SimpleNamespace(
        status=ReverseDcfScenarioRunStatus.EXECUTED,
        assumptions=solved.assumptions,
        execution_result=SimpleNamespace(
            solver_result=SimpleNamespace(status=status, implied_terminal_growth=None),
            final_consensus_revenue_growth=None,
            final_consensus_ebit_margin=None,
            publication_eligibility=ReverseDcfPublicationEligibility.UNAVAILABLE,
            issues=("Controlled synthetic solver state.",),
        ),
    )
    ui = CaptureStreamlit()
    _render_interactive_scenario_result(ui, synthetic)
    assert message in ui.text
    assert "unreasonable" not in ui.text.lower() and "bad scenario" not in ui.text.lower()


def test_application_reuses_existing_execution_service_and_defines_no_solver():
    source = APP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "execute_reverse_dcf" in source
    assert not any(name.startswith("solve_") for name in function_names)
    assert "bisection" not in source.lower() and "search_lower_bound" not in source


def test_streamlit_sources_contain_no_reverse_dcf_economic_arithmetic_or_provider_calls():
    for path in (REPORT_SOURCE, ROUTE_SOURCE):
        source = path.read_text(encoding="utf-8").lower()
        assert "stock_analyser.providers" not in source
        assert "execute_reverse_dcf(" not in source
        assert all(token not in source for token in (
            "terminal_value =", "nopat =", "fcff =", "reinvestment =", "present_value =",
            "requests.", "httpx.", "yfinance.", "run_live_",
        ))


def test_exactly_one_active_scenario_and_no_presets_sensitivity_or_history():
    source = "\n".join((APP_SOURCE.read_text(encoding="utf-8"), ROUTE_SOURCE.read_text(encoding="utf-8"))).lower()
    assert all(term not in source for term in (
        "sensitivity", "heatmap", "scenario library", "saved scenario", "scenario history",
        "bull case", "base case", "bear case", "optimistic", "conservative",
    ))
    assert ROUTE_SOURCE.read_text(encoding="utf-8").count(
        'V1_SCENARIO_RESULT_SESSION_KEY = "v1_reverse_dcf_scenario_result"'
    ) == 1


def test_no_recommendation_or_generated_narrative_after_default_cutover():
    production = "\n".join((APP_SOURCE.read_text(encoding="utf-8"), ROUTE_SOURCE.read_text(encoding="utf-8"), REPORT_SOURCE.read_text(encoding="utf-8"))).lower()
    assert all(token not in production for token in ('"buy"', '"hold"', '"sell"', "investment thesis", "executive summary"))
    assert is_v1_report_ui_enabled({}) is True
    legacy = LEGACY_SOURCE.read_text(encoding="utf-8")
    assert "render_v1_report_route()" in legacy and 'st.title("STOCK ANALYSER")' in legacy


def test_scenario_run_and_reset_make_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network call"))
    source, context = scenario_context()
    result = run_v1_reverse_dcf_scenario(source, context, ReverseDcfScenarioFormInput("10", "2", "90"))
    assert result.status is ReverseDcfScenarioRunStatus.EXECUTED
    session = {V1_SCENARIO_RESULT_SESSION_KEY: result}
    _clear_scenario_state(session)
    assert session == {}
