"""Streamlit input and execution-state shell for the V1 report renderer."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from html import escape
import os
from typing import Any, Callable

import streamlit as st

from stock_analyser.application import (
    REVENUE_INPUT_SCALE_LABEL,
    ReverseDcfScenarioExecutionContext,
    ReverseDcfScenarioFormInput,
    ReverseDcfScenarioRunResult,
    ResearchReportBuildResult,
    ResearchReportBuildStatus,
    V1ResearchCoordinator,
    run_v1_reverse_dcf_scenario,
)
from stock_analyser.ui.v1_report import (
    V1_REPORT_SESSION_KEY,
    _render_interactive_scenario_result,
    format_report_currency,
    format_report_datetime,
    render_stock_research_report,
    render_v1_report_empty_state,
)


V1_BUILD_RESULT_SESSION_KEY = "v1_research_report_build_result"
V1_CURRENT_SYMBOL_SESSION_KEY = "v1_current_symbol"
V1_ANALYSIS_AS_OF_SESSION_KEY = "v1_analysis_as_of"
V1_TICKER_INPUT_SESSION_KEY = "v1_ticker_input"
V1_SCENARIO_RESULT_SESSION_KEY = "v1_reverse_dcf_scenario_result"
V1_SCENARIO_TARGET_SESSION_KEY = "v1_reverse_dcf_scenario_target"
V1_SCENARIO_WACC_INPUT_KEY = "v1_scenario_wacc_percent"
V1_SCENARIO_SALES_INPUT_KEY = "v1_scenario_sales_to_capital"
V1_SCENARIO_REVENUE_INPUT_KEY = "v1_scenario_preceding_revenue_billions"

V1_ROUTE_CSS = """
<style>
.v1-input-shell { border-bottom: 1px solid #c9c7c0; margin: 0 0 .75rem; padding: .15rem 0 .7rem; }
.v1-input-title { color: #243a4d; font-size: .76rem; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
.v1-input-note { color: #667078; font-size: .72rem; margin-top: .15rem; }
.v1-failure { background: #fbfaf7; border-left: 3px solid #7a4944; margin: .8rem 0; padding: .65rem .8rem; }
.v1-failure-symbol { color: #667078; font-size: .72rem; margin-bottom: .2rem; }
.v1-failure-stage { color: #7a4944; font-size: .68rem; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
.v1-failure-reason { color: #20272d; font-size: .88rem; margin-top: .2rem; }
.v1-diagnostic-note { color: #667078; font-size: .72rem; line-height: 1.4; margin: 0 0 .45rem; }
.v1-diagnostic-table { border-collapse: collapse; font-size: .74rem; width: 100%; }
.v1-diagnostic-table th { background: #f0eee8; color: #667078; font-size: .62rem; letter-spacing: .04em; padding: .35rem .42rem; text-align: left; text-transform: uppercase; }
.v1-diagnostic-table td { border-top: 1px solid #dedbd3; color: #20272d; padding: .37rem .42rem; vertical-align: top; }
.v1-diagnostic-table-wrap { border: 1px solid #c9c7c0; overflow-x: auto; }
.v1-scenario-disclosure { border-left: 3px solid #806733; color: #4f4b43; font-size: .76rem; margin: .25rem 0 .75rem; padding: .42rem .62rem; }
.v1-scenario-context { border-bottom: 1px solid #c9c7c0; color: #667078; font-size: .72rem; line-height: 1.45; margin-bottom: .7rem; padding-bottom: .55rem; }
.v1-scenario-period { color: #667078; font-size: .7rem; margin: -.3rem 0 .55rem; }
</style>
"""

_COORDINATOR = V1ResearchCoordinator()


def _execute(
    coordinator: V1ResearchCoordinator,
    symbol: object,
    *,
    environment: Mapping[str, str],
    snapshot: datetime,
    retry: bool,
) -> ResearchReportBuildResult:
    method = coordinator.retry if retry else coordinator.build
    return method(symbol, environment=environment, analysis_as_of=snapshot)


def _store_safe_result(session_state: Any, result: ResearchReportBuildResult) -> None:
    _clear_scenario_state(session_state)
    session_state[V1_BUILD_RESULT_SESSION_KEY] = result
    session_state[V1_CURRENT_SYMBOL_SESSION_KEY] = result.requested_symbol
    session_state[V1_ANALYSIS_AS_OF_SESSION_KEY] = result.analysis_as_of
    if result.report is None:
        session_state.pop(V1_REPORT_SESSION_KEY, None)
    else:
        session_state[V1_REPORT_SESSION_KEY] = result.report


def _clear_scenario_state(session_state: Any) -> None:
    for key in (
        V1_SCENARIO_RESULT_SESSION_KEY,
        V1_SCENARIO_TARGET_SESSION_KEY,
        V1_SCENARIO_WACC_INPUT_KEY,
        V1_SCENARIO_SALES_INPUT_KEY,
        V1_SCENARIO_REVENUE_INPUT_KEY,
    ):
        session_state.pop(key, None)


def _scenario_target_key(report: Any) -> tuple[str, str, str]:
    return (
        report.target_security_id,
        report.target_issuer_id,
        report.analysis_as_of.isoformat(),
    )


def _canonical_scenario_context_text(context: ReverseDcfScenarioExecutionContext) -> str:
    actual = context.actual_base
    sales = context.canonical_sales_to_capital
    wacc = context.production_wacc
    actual_text = (
        format_report_currency(actual.revenue, actual.currency, compact=True)
        if actual is not None and actual.revenue is not None else "Unavailable"
    )
    sales_text = (
        f"{sales.value:.4g}x" if sales is not None and sales.value is not None else "Unavailable"
    )
    wacc_text = (
        f"{wacc.value:.2%}" if wacc is not None and wacc.value is not None else "Unavailable"
    )
    return (
        "Canonical evidence remains unchanged — "
        f"preceding annual revenue: {actual_text}; sales-to-capital: {sales_text}; production WACC: {wacc_text}. "
        "Values entered below are explicit scenario overrides."
    )


def _render_scenario_workflow(
    ui: Any,
    report: Any,
    context: ReverseDcfScenarioExecutionContext | None,
) -> None:
    """Collect three inputs and call only the application scenario controller."""
    with ui.expander("Scenario analysis", expanded=False):
        ui.markdown(
            '<div class="v1-scenario-disclosure">Scenario inputs are user-supplied and do not become canonical valuation evidence.</div>',
            unsafe_allow_html=True,
        )
        if context is None:
            ui.markdown(
                '<div class="v1-note">The provider-independent scenario execution context is unavailable for this report snapshot. No provider request was made.</div>',
                unsafe_allow_html=True,
            )
            return
        target_key = _scenario_target_key(report)
        if context.identity_key != target_key:
            _clear_scenario_state(ui.session_state)
            ui.markdown(
                '<div class="v1-note">Scenario context did not match the current report identity and snapshot.</div>',
                unsafe_allow_html=True,
            )
            return
        if ui.session_state.get(V1_SCENARIO_TARGET_SESSION_KEY) not in (None, target_key):
            _clear_scenario_state(ui.session_state)
        ui.markdown(
            f'<div class="v1-scenario-context">{escape(_canonical_scenario_context_text(context), quote=True)}</div>',
            unsafe_allow_html=True,
        )
        ui.markdown(
            '<div class="v1-scenario-period">Required preceding annual period · '
            f'{context.required_preceding_period_end.strftime("%d %b %Y")} · '
            f'input unit {escape(context.valuation_currency, quote=True)} {REVENUE_INPUT_SCALE_LABEL}</div>',
            unsafe_allow_html=True,
        )
        with ui.form("v1_reverse_dcf_scenario_form", clear_on_submit=False):
            wacc_column, sales_column, revenue_column = ui.columns(3)
            with wacc_column:
                wacc_percent = ui.text_input(
                    "WACC (%)",
                    key=V1_SCENARIO_WACC_INPUT_KEY,
                    placeholder="Required",
                    help="Enter a percentage greater than 0 and less than 100.",
                )
            with sales_column:
                sales_to_capital = ui.text_input(
                    "Sales-to-capital (x)",
                    key=V1_SCENARIO_SALES_INPUT_KEY,
                    placeholder="Required",
                    help="Revenue generated per unit of invested capital used by the approved reinvestment model.",
                )
            with revenue_column:
                preceding_revenue = ui.text_input(
                    f"Preceding annual revenue ({context.valuation_currency} {REVENUE_INPUT_SCALE_LABEL})",
                    key=V1_SCENARIO_REVENUE_INPUT_KEY,
                    placeholder="Required",
                    help="Annual revenue for the exact preceding fiscal period shown above; no FX conversion is applied.",
                )
            run_submitted = ui.form_submit_button("Run scenario", type="secondary")
            reset_submitted = ui.form_submit_button(
                "Reset scenario",
                on_click=_clear_scenario_state,
                args=(ui.session_state,),
            )
        if reset_submitted:
            return
        if run_submitted:
            scenario_result = run_v1_reverse_dcf_scenario(
                report,
                context,
                ReverseDcfScenarioFormInput(
                    wacc_percent=wacc_percent,
                    sales_to_capital=sales_to_capital,
                    preceding_revenue_billions=preceding_revenue,
                ),
            )
            ui.session_state[V1_SCENARIO_RESULT_SESSION_KEY] = scenario_result
            ui.session_state[V1_SCENARIO_TARGET_SESSION_KEY] = target_key
        scenario_result = ui.session_state.get(V1_SCENARIO_RESULT_SESSION_KEY)
        if isinstance(scenario_result, ReverseDcfScenarioRunResult):
            _render_interactive_scenario_result(ui, scenario_result)


def _render_failure(ui: Any, result: ResearchReportBuildResult) -> None:
    symbol = escape(result.requested_symbol, quote=True)
    stage = escape(result.stage.value.replace("_", " ").upper(), quote=True)
    reason = escape(result.blocking_reason or "The report is unavailable.", quote=True)
    ui.markdown(
        '<div class="v1-failure">'
        f'<div class="v1-failure-symbol">Requested symbol: {symbol}</div>'
        f'<div class="v1-failure-stage">Stopped at {stage}</div>'
        f'<div class="v1-failure-reason">{reason}</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    safe_details = tuple(dict.fromkeys((*result.issues, *result.warnings)))
    if safe_details:
        with ui.expander("Safe technical details", expanded=False):
            for item in safe_details:
                ui.markdown(f"- {item}")


def _render_build_diagnostics(ui: Any, result: ResearchReportBuildResult) -> None:
    """Render only controlled stage metadata; never request or provider details."""
    events = getattr(result, "stage_events", ())
    if not events:
        return
    with ui.expander("Technical build diagnostics", expanded=False):
        ui.markdown(
            '<div class="v1-diagnostic-note">Operational metadata only. Stage duration and reuse are not research-quality measures. '
            'Cancellation is not supported while a provider call is already in progress.</div>',
            unsafe_allow_html=True,
        )
        rows = _build_diagnostic_rows(result)
        body = "".join(
            '<tr>'
            f'<td>{escape(row["Stage"], quote=True)}</td>'
            f'<td>{escape(row["Status"], quote=True)}</td>'
            f'<td>{escape(row["Duration"], quote=True)}</td>'
            f'<td>{escape(row["Safe blocker"], quote=True)}</td>'
            '</tr>'
            for row in rows
        )
        ui.markdown(
            '<div class="v1-diagnostic-table-wrap"><table class="v1-diagnostic-table">'
            '<thead><tr><th>Stage</th><th>Status</th><th>Duration</th><th>Safe blocker</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )


def _build_diagnostic_rows(result: ResearchReportBuildResult) -> tuple[dict[str, str], ...]:
    return tuple({
        "Stage": event.stage.value.replace("_", " ").title(),
        "Status": event.status.value.title(),
        "Duration": f"{event.duration_seconds:.3f}s",
        "Safe blocker": event.safe_blocker or "—",
    } for event in getattr(result, "stage_events", ()))


def render_v1_report_route(
    *,
    coordinator: V1ResearchCoordinator | None = None,
    environment: Mapping[str, str] | None = None,
    streamlit_module: Any | None = None,
    now: Callable[[], datetime] | None = None,
) -> ResearchReportBuildResult | None:
    """Collect one ticker, run only on submit/retry, and render one report."""
    ui = st if streamlit_module is None else streamlit_module
    service = coordinator or _COORDINATOR
    values = os.environ if environment is None else environment
    clock = now or (lambda: datetime.now(timezone.utc))
    session = ui.session_state

    ui.markdown(V1_ROUTE_CSS, unsafe_allow_html=True)
    ui.markdown(
        '<div class="v1-input-shell"><div class="v1-input-title">Stock research report</div>'
        '<div class="v1-input-note">Enter one listed ticker. Analysis runs only when submitted.</div></div>',
        unsafe_allow_html=True,
    )
    input_column, action_column = ui.columns([5, 1])
    with input_column:
        symbol = ui.text_input(
            "Ticker",
            key=V1_TICKER_INPUT_SESSION_KEY,
            placeholder="Enter listed symbol",
            label_visibility="visible",
        )
    with action_column:
        submitted = ui.button("Analyse", key="v1_analyse", use_container_width=True)

    if submitted:
        snapshot = clock()
        with ui.spinner("Building research report..."):
            result = _execute(service, symbol, environment=values, snapshot=snapshot, retry=False)
        _store_safe_result(session, result)

    result = session.get(V1_BUILD_RESULT_SESSION_KEY)
    if result is None:
        render_v1_report_empty_state(streamlit_module=ui)
        return None

    retry_label = "Retry" if result.report is None else "Rebuild report"
    if ui.button(retry_label, key="v1_retry", type="secondary"):
        retry_symbol = session.get(V1_CURRENT_SYMBOL_SESSION_KEY, symbol)
        snapshot = clock()
        with ui.spinner("Building research report..."):
            result = _execute(service, retry_symbol, environment=values, snapshot=snapshot, retry=True)
        _store_safe_result(session, result)

    if result.report is None:
        _render_failure(ui, result)
    else:
        ui.caption(f"Analysis timestamp: {format_report_datetime(result.analysis_as_of)}")
        render_stock_research_report(
            result.report,
            streamlit_module=ui,
            expectations_extension=(
                lambda scenario_ui, scenario_report: _render_scenario_workflow(
                    scenario_ui, scenario_report, getattr(result, "scenario_context", None),
                )
            ),
        )
    _render_build_diagnostics(ui, result)
    return result
