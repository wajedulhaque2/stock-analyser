from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
import ast
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.application import ResearchBuildStageEvent, ResearchBuildStageStatus, ResearchReportPipelineStage
from stock_analyser.domain import ReportIssue
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from stock_analyser.ui.v1_report import (
    _readiness_rows,
    _reference_rows,
    _report_source_rows,
    format_report_datetime,
)
from stock_analyser.ui.v1_route import _build_diagnostic_rows, _render_build_diagnostics
from test_v1_report_ui import rendered
from test_v1_research_integration import RouteUI
from test_v1_research_report import NOW, complete_report, partial_report


ROOT = Path(__file__).resolve().parents[1]
REPORT_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
APP_SOURCE = ROOT / "app.py"


def complete_with_messages():
    return replace(
        complete_report(),
        issues=(ReportIssue("Synthetic evidence issue", source_label="FMP"),),
        warnings=("Synthetic evidence warning",),
    )


def safe_diagnostic_result():
    events = (
        ResearchBuildStageEvent(
            stage=ResearchReportPipelineStage.IDENTITY,
            status=ResearchBuildStageStatus.COMPLETED,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=0.125),
            duration_seconds=0.125,
        ),
        ResearchBuildStageEvent(
            stage=ResearchReportPipelineStage.REPORT_BUILD,
            status=ResearchBuildStageStatus.FAILED,
            started_at=NOW + timedelta(seconds=1),
            finished_at=NOW + timedelta(seconds=1.75),
            duration_seconds=0.75,
            safe_blocker="Canonical report contract unavailable.",
        ),
    )
    return SimpleNamespace(stage_events=events, issues=("raw exception must stay hidden",), warnings=())


def test_reference_only_section_is_separate_and_every_row_is_explicitly_noncentral():
    source = complete_report()
    rows = _reference_rows(source)
    text = rendered(source).text
    assert "Reference-only evidence" in text
    assert len(rows) == 2
    assert {row["Reference type"] for row in rows} == {
        "External Analyst Target", "External Provider Dcf Reference",
    }
    assert {row["Eligibility"] for row in rows} == {"REFERENCE ONLY"}
    assert "External context only" in text
    assert "excluded from valuation families and overall fair value" in text


def test_reference_evidence_is_not_rendered_as_a_family_or_overall_value():
    source = complete_report()
    text = rendered(source).text
    reference_index = text.index("Reference-only evidence")
    reference_text = text[reference_index:]
    assert "Own-history valuation" not in reference_text.split("Data quality and readiness")[0]
    assert "Peer valuation" not in reference_text.split("Data quality and readiness")[0]
    assert "$100.00 / share" not in reference_text


def test_reference_rows_copy_status_source_and_evidence_date_unchanged():
    source = complete_report()
    for row, item in zip(_reference_rows(source), source.references, strict=True):
        assert row["Status"] == item.status.value.title()
        assert row["Source"] == ", ".join(dict.fromkeys(ref.display_name for ref in item.source_references))
        assert row["Observation / snapshot date"] == format_report_datetime(item.evidence_as_of)
        assert row["Eligibility"] == item.reference_only_label


def test_empty_reference_state_remains_visible_and_compact():
    text = rendered(partial_report()).text
    assert "Reference-only evidence" in text
    assert "No reference-only evidence available for this snapshot" in text


def test_all_controlled_readiness_rows_remain_visible_in_contract_order():
    source = partial_report()
    rows = _readiness_rows(source)
    assert [row["Evidence area"] for row in rows] == [
        item.category.value.replace("_", " ").title() for item in source.data_quality.rows
    ]
    assert len(rows) == 6
    assert [row["Status"] for row in rows] == [item.status_label for item in source.data_quality.rows]


def test_each_readiness_status_is_copied_without_collapsing_semantics():
    source = partial_report()
    rows = {row["Evidence area"]: row for row in _readiness_rows(source)}
    assert rows["Market Price"]["Status"] == source.data_quality.rows[0].status_label
    assert rows["Own History Valuation"]["Status"] == source.data_quality.rows[1].status_label
    assert rows["Peer Valuation"]["Status"] == source.data_quality.rows[2].status_label
    assert rows["Forward Consensus"]["Status"] == source.data_quality.rows[3].status_label
    assert rows["Reverse Dcf"]["Status"] == source.data_quality.rows[4].status_label
    assert rows["Overall Valuation Publication"]["Status"] == source.data_quality.rows[5].status_label
    assert len({row["Status"] for row in rows.values()}) > 1


def test_readiness_primary_and_full_blockers_are_retained_without_reinterpretation():
    source = partial_report()
    rows = _readiness_rows(source)
    for row, contract in zip(rows, source.data_quality.rows, strict=True):
        assert row["Primary reason / blocker"] == (contract.blocking_reasons[0] if contract.blocking_reasons else "—")
    text = rendered(source).text
    for contract in source.data_quality.rows:
        for blocker in contract.blocking_reasons:
            assert blocker in text


def test_readiness_source_and_observation_context_copy_supplied_evidence():
    source = complete_report()
    rows = {row["Evidence area"]: row for row in _readiness_rows(source)}
    assert source.market.source_label in rows["Market Price"]["Source"]
    assert format_report_datetime(source.market.observation_timestamp) == rows["Market Price"]["Observation / snapshot context"]
    assert "FMP" in rows["Forward Consensus"]["Source"]
    for period in source.consensus.periods:
        assert format_report_datetime(period.estimate_as_of) in rows["Forward Consensus"]["Observation / snapshot context"]


def test_readiness_has_no_numeric_score_percentage_grade_or_gauge():
    text = rendered(partial_report()).text
    assert "No aggregate quality or confidence score is calculated" in text
    assert "no percentage, grade, or model-confidence measure" in text
    assert "<progress" not in text and "gauge" not in text.lower()
    rows = _readiness_rows(partial_report())
    assert all(set(row) == {
        "Evidence area", "Status", "Primary reason / blocker", "Source", "Observation / snapshot context",
    } for row in rows)


def test_issues_and_warnings_have_distinct_appendix_headings():
    text = rendered(complete_with_messages()).text
    assert '<div class="v1-appendix-heading">Issues</div>' in text
    assert '<div class="v1-appendix-heading">Warnings</div>' in text
    assert "FMP: Synthetic evidence issue" in text
    assert "Synthetic evidence warning" in text


def test_static_methodology_is_factual_and_reverse_dcf_is_expectations_only():
    text = rendered(complete_report()).text
    assert "Methodology &amp; evidence" not in text  # expander labels are captured separately
    assert "Target historical multiples: market-multiple distributions applied to aligned external forward denominators" in text
    assert "Comparable-company multiples: cross-sectional evidence used when sufficient eligible peers exist" in text
    assert "Minimum two eligible independent valuation families" in text
    assert "Market expectations only; not a valuation family" in text


def test_methodology_expander_is_one_restrained_research_appendix():
    ui = rendered(complete_report())
    assert ("Methodology & evidence", False) in ui.expanders
    assert sum(label.startswith("Methodology") for label, _ in ui.expanders) == 1
    assert "Analysis snapshot" in ui.text and "Valuation methodology" in ui.text and "Sources" in ui.text


def test_report_sources_are_safely_deduplicated_and_dates_retained():
    source = complete_report()
    rows = _report_source_rows(source)
    names = [row["Source"] for row in rows]
    assert len(names) == len(set(names))
    assert set(names) == {item.display_name for item in source.source_references}
    for source_ref in source.source_references:
        row = next(item for item in rows if item["Source"] == source_ref.display_name)
        assert source_ref.source_category.value.replace("_", " ").title() in row["Evidence areas"]
        if source_ref.observation_as_of is not None:
            assert format_report_datetime(source_ref.observation_as_of) in row["Observation / as-of"]


def test_analysis_market_and_consensus_timestamps_remain_distinct_fields():
    source = complete_report()
    text = rendered(source).text
    assert "Report analysis timestamp" in text
    assert format_report_datetime(source.analysis_as_of) in text
    assert "Market observation time" in text
    assert format_report_datetime(source.market.observation_timestamp) in text
    assert "Consensus estimate snapshot" in text
    assert all(format_report_datetime(item.estimate_as_of) in text for item in source.consensus.periods)


def test_supporting_ids_and_unsafe_metadata_are_not_prominent_or_rendered():
    source = complete_report()
    text = rendered(source).text
    assert all(identifier not in text for identifier in source.supporting_ids)
    lowered = text.lower()
    assert all(token not in lowered for token in (
        "companykey", "api_key", "authorization header", "bearer ",
        "provider request url", "raw payload", "environment variable",
    ))


def test_safe_build_diagnostics_copy_only_stage_status_duration_and_blocker():
    result = safe_diagnostic_result()
    rows = _build_diagnostic_rows(result)
    assert rows == (
        {"Stage": "Identity", "Status": "Completed", "Duration": "0.125s", "Safe blocker": "—"},
        {"Stage": "Report Build", "Status": "Failed", "Duration": "0.750s", "Safe blocker": "Canonical report contract unavailable."},
    )
    ui = RouteUI()
    _render_build_diagnostics(ui, result)
    text = "\n".join(ui.markdowns)
    assert "Technical build diagnostics" in ui.expanders
    assert "Operational metadata only" in text
    assert "Stage duration and reuse are not research-quality measures" in text
    assert "raw exception must stay hidden" not in text


def test_technical_diagnostics_never_render_request_or_secret_fields():
    text = "\n".join(rendered(partial_report()).markdowns)
    route = ROUTE_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in text.lower() for token in ("http://", "https://", "api_key", "companykey", "authorization"))
    assert all(token not in route for token in ("event.url", "event.headers", "event.payload", "event.exception"))


def test_stage_duration_is_formatting_only_and_creates_no_score():
    rows = _build_diagnostic_rows(safe_diagnostic_result())
    assert [row["Duration"] for row in rows] == ["0.125s", "0.750s"]
    route = ROUTE_SOURCE.read_text(encoding="utf-8").lower()
    assert "quality_score" not in route and "confidence_score" not in route and "performance_score" not in route


def test_renderer_contains_no_dynamic_economic_diagnosis_or_generated_narrative():
    text = rendered(complete_report()).text.lower()
    assert all(phrase not in text for phrase in (
        "because peers disagree", "investment thesis", "executive summary", "bull case", "bear case",
        "meta appears", "we believe", "analysts expect strong",
    ))


def test_no_recommendation_stance_or_scenario_controls_are_added():
    report_source = REPORT_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in report_source for token in (
        '"buy"', '"hold"', '"sell"', "overweight", "underweight",
        "number_input", "slider(", "selectbox", "multiselect", "scenario_form",
    ))
    text = rendered(complete_report()).text.lower()
    assert "cheap" not in text and "expensive" not in text and "attractive" not in text


def test_renderer_and_route_add_no_provider_or_valuation_arithmetic():
    for path in (REPORT_SOURCE, ROUTE_SOURCE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        if path is REPORT_SOURCE:
            assert all("providers" not in name and "services" not in name and "application" not in name for name in imported)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                attrs = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
                assert attrs.isdisjoint({
                    "central_value", "overall_central_value", "implied_terminal_growth",
                    "revenue_growth", "ebit_margin", "duration_seconds",
                })


def test_default_v1_route_keeps_legacy_rollback_available():
    assert is_v1_report_ui_enabled({}) is True
    app = APP_SOURCE.read_text(encoding="utf-8")
    assert "render_v1_report_route()" in app
    assert 'st.title("STOCK ANALYSER")' in app


def test_report_rendering_does_not_mutate_evidence_contracts():
    for source in (complete_with_messages(), partial_report()):
        before = asdict(source)
        rendered(source)
        assert asdict(source) == before


def test_normal_evidence_and_diagnostic_rendering_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network call"))
    rendered(complete_with_messages())
    rendered(partial_report())
    _render_build_diagnostics(RouteUI(), safe_diagnostic_result())
