from __future__ import annotations

from dataclasses import asdict, replace
import ast
from pathlib import Path
import socket

import pytest

from stock_analyser.domain import ReportExpectationState
from stock_analyser.ui.v1_report import (
    _consensus_chart,
    _consensus_chart_rows,
    _consensus_rows,
    format_report_currency,
    format_report_date,
    format_report_datetime,
    format_report_integer,
    format_report_percent,
)
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from test_v1_report_ui import rendered, scenario_report
from test_v1_research_report import complete_report, partial_report, report


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
APP_SOURCE = ROOT / "app.py"


def report_with_missing_chart_metrics():
    source = complete_report()
    periods = list(source.consensus.periods)
    periods[1] = replace(periods[1], revenue_growth=None)
    periods[3] = replace(periods[3], ebit_margin=None)
    consensus = replace(source.consensus, periods=tuple(periods))
    return replace(source, consensus=consensus)


def test_consensus_is_explicitly_external_and_retains_safe_fmp_source():
    text = rendered(complete_report()).text
    assert "Forward analyst consensus" in text
    assert "External analyst consensus" in text
    assert "externally sourced, no internal forecast" in text
    assert "Source: FMP" in text


def test_consensus_rows_preserve_period_order_dates_and_supplied_values():
    source = complete_report()
    rows = _consensus_rows(source)
    assert [row["Fiscal year"] for row in rows] == [item.horizon_label for item in source.consensus.periods]
    for row, period in zip(rows, source.consensus.periods, strict=True):
        assert row["Period end"] == format_report_date(period.fiscal_period_end)
        assert row["Revenue"] == format_report_currency(period.revenue, period.currency, compact=True)
        assert row["Revenue growth"] == format_report_percent(period.revenue_growth, signed=True)
        assert row["EBIT"] == format_report_currency(period.ebit, period.currency, compact=True)
        assert row["EBIT margin"] == format_report_percent(period.ebit_margin)
        assert row["EBITDA"] == format_report_currency(period.ebitda, period.currency, compact=True)
        assert row["Revenue analysts"] == format_report_integer(period.revenue_analyst_count)


def test_consensus_table_is_dense_semantic_and_financial_columns_are_right_aligned():
    text = rendered(complete_report()).text
    assert 'class="v1-consensus-table"' in text
    assert "Fiscal year" in text and "Revenue analysts" in text
    assert 'class="v1-num"' in text
    assert "font-variant-numeric: tabular-nums" in text


def test_positive_revenue_growth_has_an_explicit_sign_without_judgment():
    rows = _consensus_rows(complete_report())
    assert all(row["Revenue growth"].startswith("+") for row in rows if row["Revenue growth"] != "—")
    text = rendered(complete_report()).text.lower()
    assert all(label not in text for label in ("high growth", "weak growth", "accelerating", "decelerating"))


def test_partial_consensus_keeps_missing_ebitda_and_analyst_count_as_dashes():
    source = partial_report()
    rows = _consensus_rows(source)
    assert all(row["EBITDA"] == "—" for row in rows)
    missing = next(index for index, period in enumerate(source.consensus.periods) if period.revenue_analyst_count is None)
    assert rows[missing]["Revenue analysts"] == "—"
    assert "0.00" not in {row["EBITDA"] for row in rows}


def test_estimate_snapshot_is_displayed_from_each_supplied_period_snapshot():
    source = complete_report()
    text = rendered(source).text
    assert "Estimate snapshot" in text
    for snapshot in dict.fromkeys(period.estimate_as_of for period in source.consensus.periods):
        assert format_report_datetime(snapshot) in text


def test_consensus_chart_uses_only_supplied_periods_and_percentage_metrics():
    source = complete_report()
    rows = _consensus_chart_rows(source)
    expected = {
        (period.horizon_label, metric, value)
        for period in source.consensus.periods
        for metric, value in (
            ("Revenue growth", period.revenue_growth),
            ("EBIT margin", period.ebit_margin),
        )
        if value is not None
    }
    assert {(row["Fiscal year"], row["Metric"], row["Value"]) for row in rows} == expected
    assert {row["Fiscal year"] for row in rows} == {period.horizon_label for period in source.consensus.periods}
    assert {row["Metric"] for row in rows} == {"Revenue growth", "EBIT margin"}


def test_missing_chart_metrics_are_omitted_and_never_zero_filled():
    source = report_with_missing_chart_metrics()
    rows = _consensus_chart_rows(source)
    assert not any(row["Fiscal year"] == "FY2" and row["Metric"] == "Revenue growth" for row in rows)
    assert not any(row["Fiscal year"] == "FY4" and row["Metric"] == "EBIT margin" for row in rows)
    assert all(row["Value"] is not None for row in rows)
    supplied_zeroes = {
        (period.horizon_label, metric)
        for period in source.consensus.periods
        for metric, value in (("Revenue growth", period.revenue_growth), ("EBIT margin", period.ebit_margin))
        if value == 0
    }
    assert {(row["Fiscal year"], row["Metric"]) for row in rows if row["Value"] == 0} == supplied_zeroes


def test_consensus_chart_has_no_calculation_transform_or_extrapolated_period():
    source = complete_report()
    specification = _consensus_chart(_consensus_chart_rows(source)).to_dict()
    assert "calculate" not in str(specification).lower()
    assert "transform" not in specification
    data = specification["data"]["values"]
    assert {row["Fiscal year"] for row in data} == {period.horizon_label for period in source.consensus.periods}
    assert len(data) == len(_consensus_chart_rows(source))


def test_market_expectations_remain_separate_from_valuation_and_canonical_state_is_obvious():
    source = complete_report()
    text = rendered(source).text
    assert source.expectations.display_state is ReportExpectationState.CANONICAL_EXPECTATIONS
    assert "Market expectations" in text
    assert "CANONICAL EXPECTATIONS" in text
    assert "Execution · <strong>Canonical</strong>" in text
    assert "Publication eligibility · <strong>Canonical expectations</strong>" in text
    assert "Market-implied enterprise expectations" in text
    assert "not a valuation or price target" in text


def test_canonical_comparators_copy_all_supplied_growth_fields_only():
    source = complete_report()
    entry = source.expectations.entries[0]
    text = rendered(source).text
    assert format_report_percent(entry.implied_terminal_growth) in text
    assert format_report_percent(entry.final_consensus_revenue_growth) in text
    assert format_report_percent(entry.final_consensus_ebit_margin) in text
    assert format_report_percent(entry.growth_rate_difference, signed=True) in text
    assert "Implied terminal growth" in text
    assert "Final consensus revenue growth" in text
    assert "Final consensus EBIT margin" in text


def test_not_ready_is_withheld_with_all_upstream_safe_blockers_and_no_empty_comparator_grid():
    source = partial_report()
    text = rendered(source).text
    assert source.expectations.display_state is ReportExpectationState.NOT_READY
    assert "NOT READY" in text
    assert "Implied terminal growth" in text and "Withheld" in text
    assert "Final consensus revenue growth" not in text
    assert "Final consensus EBIT margin" not in text
    for blocker in source.expectations.blocking_reasons:
        assert blocker in text


def test_not_run_is_restrained_and_does_not_imply_an_error_or_fabricate_metrics():
    source = report()
    text = rendered(source).text
    assert source.expectations.display_state is ReportExpectationState.NOT_RUN
    assert "NOT RUN" in text
    assert "Reverse DCF not run for this snapshot" in text
    assert "Implied terminal growth" not in text
    assert "Final consensus revenue growth" not in text


def test_scenario_state_is_prominent_and_distinct_from_canonical():
    source = scenario_report()
    text = rendered(source).text
    assert source.expectations.display_state is ReportExpectationState.SCENARIO_EXPECTATIONS
    assert "SCENARIO EXPECTATIONS" in text
    assert 'class="v1-scenario-label">Scenario</div>' in text
    assert "Execution · <strong>Scenario</strong>" in text
    assert "Publication eligibility · <strong>Scenario expectations</strong>" in text
    assert "v1-expectation-head--scenario" in text
    assert "Execution · <strong>Canonical</strong>" not in text


def test_scenario_assumptions_are_complete_formatted_and_not_fabricated():
    source = scenario_report()
    rows = rendered(source).table("Assumption")
    assumptions = source.expectations.entries[0].scenario_assumptions
    assert len(rows) == len(assumptions) == 3
    assert {row["Assumption"] for row in rows} == {
        "Preceding annual revenue", "Sales-to-capital", "WACC",
    }
    assert {row["Value"] for row in rows} == {"$90.00", "2.0000", "10.00%"}
    assert {row["Source"] for row in rows} == {item.source_label for item in assumptions}
    assert {row["Method"] for row in rows} == {item.methodology_label for item in assumptions}


def test_scenario_has_no_controls_and_expectations_use_no_valuation_or_judgment_labels():
    source = UI_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in source for token in (
        "number_input", "slider(", "selectbox", "multiselect", "scenario_form",
        "expectation score", "forecast score", "consensus confidence score",
        "aggressive", "conservative", "optimistic", "pessimistic", "reasonable", "unreasonable",
        "buy", "hold", "sell",
    ))
    rendered_text = rendered(scenario_report()).text.lower()
    assert "fair value" not in rendered_text[rendered_text.index("market expectations"):]
    assert "target price" not in rendered_text
    assert "upside" not in rendered_text and "downside" not in rendered_text


def test_renderer_contains_no_consensus_or_reverse_dcf_economic_arithmetic():
    guarded = {
        "revenue", "revenue_growth", "ebit", "ebit_margin", "ebitda",
        "revenue_analyst_count", "implied_terminal_growth",
        "final_consensus_revenue_growth", "final_consensus_ebit_margin",
        "growth_rate_difference",
    }
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            attributes = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
            assert attributes.isdisjoint(guarded)
    call_names = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert call_names.isdisjoint({"sum", "mean", "median", "solve_reverse_dcf", "build_forward_consensus"})


def test_renderer_does_not_generate_cagr_trend_classification_or_narrative():
    rendered_text = rendered(complete_report()).text.lower()
    assert all(phrase not in rendered_text for phrase in (
        "cagr", "growth acceleration", "margin expansion", "margin compression",
        "beat expectations", "miss expectations", "revision momentum",
        "analysts expect strong", "we believe", "investment view",
    ))


def test_report_is_not_mutated_by_consensus_or_expectations_rendering():
    for source in (complete_report(), partial_report(), scenario_report(), report()):
        before = asdict(source)
        rendered(source)
        assert asdict(source) == before


def test_renderer_has_no_provider_or_application_coordinator_dependency():
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert all("providers" not in name and "application" not in name and "services" not in name for name in imported)
    text = UI_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in text for token in (
        "v1researchcoordinator", "researchbuildcontext", "run_live_", "requests.", "httpx.", "yfinance",
    ))


def test_default_v1_route_and_legacy_rollback_remain_available():
    assert is_v1_report_ui_enabled({}) is True
    assert is_v1_report_ui_enabled({"ENABLE_V1_REPORT_UI": "false"}) is True
    app = APP_SOURCE.read_text(encoding="utf-8")
    assert "render_v1_report_route()" in app
    assert "st.set_page_config" in app


def test_normal_renderer_execution_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network call"))
    rendered(complete_report())
    rendered(partial_report())
    rendered(scenario_report())
