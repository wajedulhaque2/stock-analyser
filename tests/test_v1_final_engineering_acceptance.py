from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.application import (
    DEFAULT_REPORT_CACHE_POLICY,
    ResearchReportBuildStatus,
    ResearchReportPipelineStage,
    ReverseDcfScenarioFormInput,
    run_v1_reverse_dcf_scenario,
)
from stock_analyser.domain import (
    AggregationStatus,
    DataAvailability,
    ReportExpectationState,
    ValuationFamily,
)
from stock_analyser.services import compare_publication_to_market
from stock_analyser.ui import v1_route
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from stock_analyser.ui.v1_report import (
    _consensus_rows,
    _family_rows,
    _readiness_rows,
    _render_interactive_scenario_result,
    _valuation_range_rows,
    render_stock_research_report,
)
from stock_analyser.ui.v1_route import (
    V1_BUILD_RESULT_SESSION_KEY,
    V1_SCENARIO_RESULT_SESSION_KEY,
    V1_SCENARIO_REVENUE_INPUT_KEY,
    V1_SCENARIO_SALES_INPUT_KEY,
    V1_SCENARIO_TARGET_SESSION_KEY,
    V1_SCENARIO_WACC_INPUT_KEY,
    _render_scenario_workflow,
    _store_safe_result,
    render_v1_report_route,
)
from test_v1_market_comparison import (
    family,
    price,
    publication,
    resolved_publication,
    unresolved_publication,
    wide_publication,
)
from test_v1_report_forecast_expectations_ux import report_with_missing_chart_metrics
from test_v1_report_ui import CaptureStreamlit, rendered, scenario_report
from test_v1_research_integration import RouteUI, UIService, build_result
from test_v1_research_report import NOW, complete_report, identity, partial_report, report
from test_v1_reverse_dcf_scenario_ui import ScenarioUI, scenario_context


ROOT = Path(__file__).resolve().parents[1]
REPORT_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
SCENARIO_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_scenario.py"
COORDINATOR_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_research.py"
AUDIT_SOURCE = ROOT / "scripts" / "v1_identity_integration_audit.py"
APP_SOURCE = ROOT / "app.py"


def uk_gbpence_report():
    target = replace(
        identity(symbol="SYN.L"),
        reporting_currency="GBP",
        quote_currency="GBP",
        quote_unit="GBp",
        price_scale=0.01,
    )
    source_publication = publication(
        family(lower=8, central=9.5, upper=11, currency="GBP", unit="GBP/share"),
        family(
            ValuationFamily.PEER,
            lower=9,
            central=10.5,
            upper=12,
            currency="GBP",
            unit="GBP/share",
        ),
    )
    market = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    comparison = compare_publication_to_market(source_publication, market)
    return report(
        target_identity=target,
        publication_result=source_publication,
        market=market,
        comparison=comparison,
    )


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(value: str) -> float:
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        adjusted = [
            channel / 12.92 if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class AccessibleRouteUI(RouteUI):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.input_calls = []

    def text_input(self, label, **kwargs):
        self.input_calls.append((label, kwargs))
        return super().text_input(label, **kwargs)


def test_primary_ticker_input_has_visible_label_and_meaningful_actions(monkeypatch):
    ui = AccessibleRouteUI()
    service = UIService(build_result())
    monkeypatch.setattr(v1_route, "render_v1_report_empty_state", lambda **kwargs: None)
    render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert ui.input_calls[0][0] == "Ticker"
    assert ui.input_calls[0][1]["label_visibility"] == "visible"
    source = ROUTE_SOURCE.read_text(encoding="utf-8")
    assert all(label in source for label in ("Analyse", "Retry", "Rebuild report"))


def test_usd_report_preserves_scale_one_and_usd_per_share_rendering():
    source = complete_report()
    text = rendered(source).text
    assert source.identity.quote_currency == source.identity.quote_unit == "USD"
    assert source.identity.quote_price_scale == 1.0
    assert source.market.normalized_currency == "USD"
    assert "$90.00 / share" in text and "USD / USD" in text


def test_uk_gbpence_report_preserves_raw_quote_and_normalizes_once_to_gbp_per_share():
    source = uk_gbpence_report()
    text = rendered(source).text
    assert source.identity.display_symbol.endswith(".L")
    assert source.identity.quote_currency == "GBP"
    assert source.identity.quote_unit == "GBp"
    assert source.identity.quote_price_scale == 0.01
    assert source.market.raw_market_quote == 750
    assert source.market.normalized_market_price == 7.5
    assert source.market.normalized_currency == "GBP"
    assert source.market.normalized_per_share_unit == "GBP/share"
    assert all(item.currency == "GBP" for item in source.valuation_families)
    assert "GBP / GBp" in text and "£7.50 / share" in text


def test_no_london_suffix_arithmetic_or_currency_inference_exists_in_v1_path():
    production = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (ROUTE_SOURCE, REPORT_SOURCE, SCENARIO_SOURCE, COORDINATOR_SOURCE)
    )
    assert all(token not in production for token in (
        'endswith(".l")', "endswith('.l')", "split('.l')", 'split(".l")',
        "divide_by_100", "/ 100 if", "* 0.01 if",
    ))


def test_currency_mismatch_withholds_comparison_without_fx():
    source_publication = resolved_publication()
    gbp_market = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    comparison = compare_publication_to_market(source_publication, gbp_market)
    assert comparison.overall_comparison.status is DataAvailability.UNAVAILABLE
    assert all(item.status is DataAvailability.UNAVAILABLE for item in comparison.family_comparisons)
    sources = "\n".join(path.read_text(encoding="utf-8").lower() for path in (REPORT_SOURCE, ROUTE_SOURCE))
    assert all(term not in sources for term in ("fx_rate", "exchange_rate", "forex"))


def test_cross_security_price_evidence_fails_closed_for_share_class_or_adr():
    result = price(observation_security="security:alternate-share-class")
    assert result.status is DataAvailability.UNAVAILABLE
    assert result.normalized_price_per_share is None


@pytest.mark.parametrize(
    ("publication_factory", "expected"),
    [
        (resolved_publication, AggregationStatus.RESOLVED),
        (wide_publication, AggregationStatus.WIDE),
        (unresolved_publication, AggregationStatus.UNRESOLVED),
        (publication, AggregationStatus.UNAVAILABLE),
    ],
)
def test_all_publication_states_preserve_resolved_only_overall_central(publication_factory, expected):
    source = report(publication_result=publication_factory())
    assert source.valuation_summary.publication_status is expected
    if expected is AggregationStatus.RESOLVED:
        assert source.valuation_summary.overall_central_value is not None
    else:
        assert source.valuation_summary.overall_central_value is None
        assert source.valuation_summary.overall_value_label in {"Withheld", "Not published"}


def test_family_valid_unavailable_and_partial_evidence_remain_visible_without_zeroes():
    for source in (complete_report(), partial_report()):
        rows = _family_rows(source)
        assert [row["Valuation family"] for row in rows] == ["Own-history valuation", "Peer valuation"]
    unavailable = _family_rows(partial_report())[1]
    assert unavailable["Lower"] == unavailable["Central"] == unavailable["Upper"] == "—"
    assert "0.00" not in str(unavailable)


def test_market_available_unavailable_and_stale_states_have_no_fallback_or_zero_placeholder():
    assert complete_report().market.normalized_market_price is not None
    assert partial_report().market.normalized_market_price is None
    stale = price(as_of=NOW - timedelta(days=4))
    assert stale.status is DataAvailability.UNAVAILABLE and stale.normalized_price_per_share is None
    assert "$0.00" not in rendered(partial_report()).text


def test_consensus_ready_partial_and_missing_fields_are_not_fabricated():
    ready = _consensus_rows(complete_report())
    partial = _consensus_rows(partial_report())
    missing = _consensus_rows(report_with_missing_chart_metrics())
    assert len(ready) == 5
    assert any(row["Revenue analysts"] == "—" for row in partial)
    assert all(row["EBITDA"] == "—" for row in partial)
    assert any(row["Revenue growth"] == "—" for row in missing)
    assert any(row["EBIT margin"] == "—" for row in missing)


@pytest.mark.parametrize(
    ("source_factory", "expected_state"),
    [
        (complete_report, ReportExpectationState.CANONICAL_EXPECTATIONS),
        (scenario_report, ReportExpectationState.SCENARIO_EXPECTATIONS),
        (partial_report, ReportExpectationState.NOT_READY),
        (report, ReportExpectationState.NOT_RUN),
    ],
)
def test_all_report_expectation_states_remain_structurally_distinct(source_factory, expected_state):
    source = source_factory()
    assert source.expectations.display_state is expected_state
    assert source.expectations.display_label in rendered(source).text


def test_scenario_solved_and_no_solution_use_existing_controlled_states():
    solved_report, solved_context = scenario_context()
    solved = run_v1_reverse_dcf_scenario(
        solved_report, solved_context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    no_report, no_context = scenario_context(no_solution=True)
    no_solution = run_v1_reverse_dcf_scenario(
        no_report, no_context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    solved_ui, no_solution_ui = CaptureStreamlit(), CaptureStreamlit()
    _render_interactive_scenario_result(solved_ui, solved)
    _render_interactive_scenario_result(no_solution_ui, no_solution)
    assert "Market-implied terminal growth" in solved_ui.text and "3.00%" in solved_ui.text
    assert "No terminal-growth solution was found" in no_solution_ui.text
    assert "fair value" not in solved_ui.text.lower() and "fair value" not in no_solution_ui.text.lower()


def test_cross_ticker_report_store_clears_every_scenario_state_key():
    source, context = scenario_context()
    scenario = run_v1_reverse_dcf_scenario(
        source, context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: scenario,
        V1_SCENARIO_TARGET_SESSION_KEY: context.identity_key,
        V1_SCENARIO_WACC_INPUT_KEY: "10",
        V1_SCENARIO_SALES_INPUT_KEY: "2",
        V1_SCENARIO_REVENUE_INPUT_KEY: "90",
    }
    _store_safe_result(session, SimpleNamespace(
        requested_symbol="MSFT",
        analysis_as_of=source.analysis_as_of,
        report=partial_report(),
    ))
    assert all(key not in session for key in (
        V1_SCENARIO_RESULT_SESSION_KEY, V1_SCENARIO_TARGET_SESSION_KEY,
        V1_SCENARIO_WACC_INPUT_KEY, V1_SCENARIO_SALES_INPUT_KEY,
        V1_SCENARIO_REVENUE_INPUT_KEY,
    ))


def test_snapshot_change_rejects_and_clears_stale_scenario_before_render():
    source, context = scenario_context()
    result = run_v1_reverse_dcf_scenario(
        source, context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    session = {
        V1_SCENARIO_RESULT_SESSION_KEY: result,
        V1_SCENARIO_TARGET_SESSION_KEY: context.identity_key,
    }
    different_snapshot = SimpleNamespace(
        target_security_id=source.target_security_id,
        target_issuer_id=source.target_issuer_id,
        analysis_as_of=source.analysis_as_of + timedelta(minutes=5),
    )
    ui = ScenarioUI(session=session)
    _render_scenario_workflow(ui, different_snapshot, context)
    assert V1_SCENARIO_RESULT_SESSION_KEY not in session
    assert "did not match the current report identity and snapshot" in ui.text


def test_one_submit_rerun_new_ticker_retry_and_scenario_do_not_duplicate_builds(monkeypatch):
    expected = build_result()
    service = UIService(expected)
    session = {}
    monkeypatch.setattr(v1_route, "render_stock_research_report", lambda *args, **kwargs: None)
    render_v1_report_route(
        coordinator=service,
        environment={},
        streamlit_module=RouteUI(symbol="META", buttons={"Analyse": [True]}, session=session),
        now=lambda: NOW,
    )
    render_v1_report_route(
        coordinator=service,
        environment={},
        streamlit_module=RouteUI(symbol="META", session=session),
        now=lambda: NOW,
    )
    render_v1_report_route(
        coordinator=service,
        environment={},
        streamlit_module=RouteUI(symbol="MSFT", buttons={"Analyse": [True]}, session=session),
        now=lambda: NOW,
    )
    assert [call[0] for call in service.build_calls] == ["META", "MSFT"]
    assert service.retry_calls == []


def test_default_cache_acceptance_contract_remains_unchanged():
    policy = DEFAULT_REPORT_CACHE_POLICY
    assert policy.max_entries == 8
    assert policy.ttl_seconds == 300
    assert policy.snapshot_bucket_seconds == 300


@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        (ResearchReportPipelineStage.IDENTITY, "Canonical identity was unavailable."),
        (ResearchReportPipelineStage.REPORT_BUILD, "The report contract failed closed."),
        (ResearchReportPipelineStage.REPORT_BUILD, "The canonical report pipeline did not complete."),
    ],
)
def test_fatal_failure_ui_exposes_only_safe_stage_reason_retry_and_details(stage, reason):
    failed = build_result(None, status=ResearchReportBuildStatus.UNAVAILABLE, stage=stage)
    failed = SimpleNamespace(**{
        **failed.__dict__,
        "blocking_reason": reason,
        "issues": ("Safe bounded detail.",),
    })
    ui = RouteUI(symbol="SYN", session={V1_BUILD_RESULT_SESSION_KEY: failed})
    render_v1_report_route(
        coordinator=UIService(failed), environment={}, streamlit_module=ui, now=lambda: NOW,
    )
    text = "\n".join(ui.markdowns)
    assert f"Stopped at {stage.value.upper().replace('_', ' ')}" in text
    assert reason in text
    assert "Safe technical details" in ui.expanders
    assert "Traceback" not in text


def test_coherent_partial_report_renders_all_primary_sections_instead_of_failing():
    source = partial_report()
    text = rendered(source).text
    assert source.status.value.title() in text
    assert all(label in text for label in (
        "Valuation summary", "Valuation families", "Forward analyst consensus",
        "Market expectations", "Reference-only evidence", "Data quality and readiness",
    ))
    assert "Unavailable" in text and "Withheld" in text


def test_structural_accessibility_has_text_labels_headers_and_chart_equivalents():
    text = rendered(complete_report()).text
    assert all(header in text for header in (
        "Fiscal year", "Revenue", "Revenue growth", "EBIT margin", "Revenue analysts",
        "Reference type", "Status", "Source", "Eligibility",
    ))
    assert "REFERENCE ONLY" in text
    assert "Range line = supplied lower to upper" in text
    assert "External analyst consensus" in text
    assert "Withheld" in rendered(partial_report()).text


def test_established_text_and_semantic_accents_meet_normal_text_contrast_threshold():
    backgrounds = ("#f4f2ed", "#fbfaf7")
    foregrounds = (
        "#20272d", "#243a4d", "#667078", "#31596b",
        "#456650", "#806733", "#7a4944", "#4f4b43",
    )
    assert min(_contrast_ratio(foreground, background) for foreground in foregrounds for background in backgrounds) >= 4.5


def test_responsive_css_retains_laptop_layout_and_one_narrow_structural_fallback():
    css = REPORT_SOURCE.read_text(encoding="utf-8")
    assert "max-width: 1400px" in css
    assert "@media (max-width: 900px)" in css
    assert ".v1-report-header { grid-template-columns: 1fr; }" in css
    assert ".v1-metric-grid { grid-template-columns: repeat(2" in css
    assert "overflow-x: auto" in css


def test_security_redaction_and_acceptance_cli_expose_only_allowlisted_fields():
    production = "\n".join(path.read_text(encoding="utf-8").lower() for path in (
        REPORT_SOURCE, ROUTE_SOURCE, SCENARIO_SOURCE, COORDINATOR_SOURCE, AUDIT_SOURCE,
    ))
    rendered_text = rendered(complete_report()).text.lower()
    assert all(token not in rendered_text for token in (
        "api_key", "authorization", "bearer ", "companykey", "raw payload", "http://", "https://",
    ))
    assert all(token not in production for token in (
        "print(os.environ", "print(environment", "result.payload", "result.headers",
        "result.authorization", "result.companykey", "repr(error)", "repr(exception)",
    ))
    audit = AUDIT_SOURCE.read_text(encoding="utf-8")
    assert all(label in audit for label in (
        "requested symbol", "canonical identity", "build status", "market",
        "reporting currency", "quote currency", "quote unit", "publication",
        "overall central", "consensus", "reverse DCF", "scenario context",
    ))


def test_static_v1_architecture_has_no_business_arithmetic_provider_parsing_or_ticker_branches():
    guarded = {
        "overall_central_value", "central_value", "central_gap", "central_gap_percent",
        "implied_terminal_growth", "normalized_market_price",
    }
    for path in (REPORT_SOURCE, ROUTE_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                attributes = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
                assert attributes.isdisjoint(guarded)
    sources = "\n".join(path.read_text(encoding="utf-8").lower() for path in (
        REPORT_SOURCE, ROUTE_SOURCE, SCENARIO_SOURCE, COORDINATOR_SOURCE,
    ))
    assert "stock_analyser.providers" not in sources
    string_literals = {
        node.value.lower()
        for path in (REPORT_SOURCE, ROUTE_SOURCE, SCENARIO_SOURCE, COORDINATOR_SOURCE)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert string_literals.isdisjoint({"meta", "msft", "nvda", "shel", "shel.l", "rr", "rr.l", "goog", "googl"})


def test_default_v1_route_keeps_legacy_rollback_and_no_stance_or_narrative():
    assert is_v1_report_ui_enabled({}) is True
    app = APP_SOURCE.read_text(encoding="utf-8")
    assert "render_v1_report_route()" in app and 'st.title("STOCK ANALYSER")' in app
    production = "\n".join(path.read_text(encoding="utf-8").lower() for path in (REPORT_SOURCE, ROUTE_SOURCE, SCENARIO_SOURCE))
    assert all(token not in production for token in (
        '"buy"', '"hold"', '"sell"', "investment thesis", "executive summary", "generated narrative",
    ))


def test_acceptance_render_and_scenario_controls_make_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network call"))
    render_stock_research_report(complete_report(), streamlit_module=CaptureStreamlit())
    source, context = scenario_context()
    result = run_v1_reverse_dcf_scenario(
        source, context, ReverseDcfScenarioFormInput("10", "2", "90"),
    )
    assert result.execution_result is not None
    _render_scenario_workflow(ScenarioUI(), source, context)
