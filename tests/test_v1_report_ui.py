from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, replace
import ast
from pathlib import Path
import socket

import pytest

from stock_analyser.domain import (
    AggregationStatus,
    ReverseDcfExecutionMode,
    ReverseDcfScenarioAssumptionType,
    StockResearchReport,
)
from stock_analyser.services import compare_publication_to_market
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from stock_analyser.ui.v1_report import (
    V1_REPORT_SESSION_KEY,
    _consensus_rows,
    _readiness_rows,
    _reference_rows,
    format_report_currency,
    format_report_datetime,
    format_report_integer,
    format_report_percent,
    render_stock_research_report,
    render_v1_report_empty_state,
)
from stock_analyser.ui import v1_report as report_ui
from test_v1_market_comparison import (
    expectation,
    price,
    publication,
    unresolved_publication,
    wide_publication,
)
from test_v1_research_report import (
    NOW,
    complete_report,
    identity,
    partial_report,
    report,
    scenario_assumption,
)


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
APP_SOURCE = ROOT / "app.py"


class _Scope(AbstractContextManager):
    def __init__(self, ui: "CaptureStreamlit") -> None:
        self.ui = ui

    def __enter__(self):
        return self.ui

    def __exit__(self, exc_type, exc, traceback):
        return False


class CaptureStreamlit:
    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.tables: list[list[dict[str, str]]] = []
        self.expanders: list[tuple[str, bool]] = []
        self.charts: list[object] = []

    def markdown(self, body, **kwargs) -> None:
        self.markdowns.append(str(body))

    def dataframe(self, rows, **kwargs) -> None:
        self.tables.append([dict(row) for row in rows])

    def altair_chart(self, chart, **kwargs) -> None:
        self.charts.append(chart)

    def expander(self, label, *, expanded=False):
        self.expanders.append((label, expanded))
        return _Scope(self)

    @property
    def text(self) -> str:
        return "\n".join((*self.markdowns, *(str(table) for table in self.tables)))

    def table(self, column: str) -> list[dict[str, str]]:
        return next(table for table in self.tables if table and column in table[0])


def rendered(source: StockResearchReport) -> CaptureStreamlit:
    ui = CaptureStreamlit()
    render_stock_research_report(source, streamlit_module=ui)
    return ui


def scenario_report() -> StockResearchReport:
    source_publication = unresolved_publication()
    market = price()
    comparison = compare_publication_to_market(source_publication, market)
    assumptions = tuple(scenario_assumption(kind) for kind in ReverseDcfScenarioAssumptionType)
    scenario = replace(
        expectation(ReverseDcfExecutionMode.EXPLICIT_SCENARIO),
        scenario_assumption_ids=tuple(item.assumption_id for item in assumptions),
    )
    comparison = replace(comparison, expectation_evidence=(scenario,))
    return report(
        publication_result=source_publication,
        market=market,
        comparison=comparison,
        assumptions=assumptions,
    )


def test_renderer_accepts_stock_research_report():
    source = complete_report()
    ui = rendered(source)
    assert source.identity.company_name in ui.text


def test_renderer_rejects_non_report_input():
    with pytest.raises(TypeError, match="StockResearchReport"):
        render_stock_research_report(object(), streamlit_module=CaptureStreamlit())


def test_renderer_accepts_exact_contract_identity_from_streamlit_hot_reload():
    source = complete_report()
    stale_type = type(
        "StockResearchReport",
        (),
        {"__module__": "stock_analyser.domain.research_report"},
    )
    stale = stale_type()
    for field_name in (
        "report_id", "target_security_id", "target_issuer_id", "analysis_as_of",
        "identity", "market", "valuation_summary", "valuation_families",
        "consensus", "expectations", "references", "data_quality",
        "source_references", "issues", "warnings",
    ):
        setattr(stale, field_name, getattr(source, field_name))
    ui = CaptureStreamlit()
    render_stock_research_report(stale, streamlit_module=ui)
    assert source.identity.company_name in ui.text


def test_renderer_uses_controlled_enum_values_across_streamlit_hot_reload():
    stale_own_history = type(
        "ValuationFamily",
        (),
        {"value": "own_history"},
    )()
    stale_available = type(
        "DataAvailability",
        (),
        {"value": "available"},
    )()
    assert report_ui._family_name(stale_own_history) == "Own-history valuation"
    assert report_ui._controlled_value(stale_available) == "available"


def test_streamlit_import_is_confined_to_ui_and_legacy_entry_layers():
    offenders = []
    for folder in (ROOT / "src" / "stock_analyser" / "domain", ROOT / "src" / "stock_analyser" / "services"):
        for path in folder.glob("*.py"):
            if "streamlit" in path.read_text(encoding="utf-8").lower():
                offenders.append(path.name)
    assert offenders == []
    assert "import streamlit as st" in UI_SOURCE.read_text(encoding="utf-8")


def test_renderer_imports_no_provider_or_financial_service_module():
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert all("providers" not in name for name in imported)
    assert all("services" not in name for name in imported)
    assert all(name.split(".")[0] not in {"requests", "httpx", "yfinance"} for name in imported)


@pytest.mark.parametrize("forbidden", [
    "build_stock_research_report", "compare_publication_to_market", "publish_valuation",
    "solve_reverse_dcf", "run_live_", "fetch_", "providerclient",
])
def test_renderer_does_not_call_backend_calculation_or_provider_functions(forbidden):
    assert forbidden not in UI_SOURCE.read_text(encoding="utf-8").lower()


def test_renderer_performs_no_arithmetic_on_valuation_gap_or_reverse_dcf_fields():
    guarded = {
        "overall_central_value", "envelope_lower", "envelope_upper", "overlap_lower", "overlap_upper",
        "lower_value", "central_value", "upper_value", "lower_gap", "central_gap", "upper_gap",
        "lower_gap_percent", "central_gap_percent", "upper_gap_percent", "implied_terminal_growth",
        "final_consensus_revenue_growth", "final_consensus_ebit_margin", "growth_rate_difference",
    }
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        attributes = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
        assert attributes.isdisjoint(guarded)


def test_renderer_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    rendered(complete_report())
    rendered(partial_report())


def test_renderer_does_not_mutate_complete_report():
    source = complete_report()
    before = asdict(source)
    rendered(source)
    assert asdict(source) == before


def test_resolved_overall_central_is_displayed_from_report():
    source = complete_report()
    assert source.valuation_summary.publication_status is AggregationStatus.RESOLVED
    ui = rendered(source)
    assert "Overall fair value" in ui.text
    assert "$100.00 / share" in ui.text


@pytest.mark.parametrize(
    "source,expected",
    [
        (report(), "Unresolved"),
        (report(publication_result=wide_publication()), "Wide"),
        (report(publication_result=publication()), "Unavailable"),
    ],
)
def test_nonresolved_overall_central_is_withheld(source, expected):
    ui = rendered(source)
    summary_index = next(index for index, value in enumerate(ui.markdowns) if "Valuation summary" in value)
    summary_grid = ui.markdowns[summary_index + 1]
    assert expected in summary_grid
    assert "Overall fair value" in summary_grid
    assert "Withheld" in summary_grid


def test_family_central_is_not_promoted_to_overall_slot():
    source = report()
    own_central = format_report_currency(
        source.valuation_families[0].central_value,
        source.valuation_families[0].currency,
        per_share=True,
    )
    ui = rendered(source)
    summary_index = next(index for index, value in enumerate(ui.markdowns) if "Valuation summary" in value)
    assert own_central not in ui.markdowns[summary_index + 1]
    assert own_central in str(ui.table("Valuation family"))


def test_known_family_rows_are_always_rendered_in_order():
    rows = rendered(partial_report()).table("Valuation family")
    assert [row["Valuation family"] for row in rows] == [
        "Own-history valuation", "Peer valuation",
    ]


def test_unavailable_peer_row_retains_upstream_blocker():
    source = partial_report()
    peer = source.valuation_families[1]
    ui = rendered(source)
    row = ui.table("Valuation family")[1]
    assert row["Status"] == "Unavailable"
    assert row["Lower"] == row["Central"] == row["Upper"] == "—"
    assert f"Peer valuation: {peer.blocking_reasons[0]}" in ui.text


def test_family_level_gap_is_shown_only_when_supplied():
    complete_rows = rendered(complete_report()).table("Valuation family")
    partial_rows = rendered(partial_report()).table("Valuation family")
    assert complete_rows[0]["Central gap vs market"] != "—"
    assert partial_rows[0]["Central gap vs market"] == "—"


def test_overall_gap_is_shown_only_when_supplied():
    complete = rendered(complete_report()).text
    unresolved = rendered(partial_report()).text
    assert "Overall valuation gap" in complete and "+" in complete
    summary_index = next(index for index, value in enumerate(rendered(partial_report()).markdowns) if "Valuation summary" in value)
    partial_ui = rendered(partial_report())
    summary_index = next(index for index, value in enumerate(partial_ui.markdowns) if "Valuation summary" in value)
    assert "Overall valuation gap" in partial_ui.markdowns[summary_index + 1]
    assert "Withheld" in partial_ui.markdowns[summary_index + 1]


def test_forward_consensus_row_order_is_preserved():
    source = complete_report()
    rows = _consensus_rows(source)
    assert [row["Fiscal year"] for row in rows] == [item.horizon_label for item in source.consensus.periods]


def test_forward_consensus_values_are_formatted_not_recomputed():
    source = complete_report()
    first = source.consensus.periods[0]
    row = _consensus_rows(source)[0]
    assert row["Revenue"] == format_report_currency(first.revenue, first.currency, compact=True)
    assert row["EBIT"] == format_report_currency(first.ebit, first.currency, compact=True)
    assert row["EBITDA"] == format_report_currency(first.ebitda, first.currency, compact=True)
    assert row["EBIT margin"] == format_report_percent(first.ebit_margin)
    assert row["Revenue growth"] == format_report_percent(first.revenue_growth, signed=True)


def test_missing_analyst_count_renders_as_unavailable_not_zero():
    source = partial_report()
    missing_index = next(index for index, item in enumerate(source.consensus.periods) if item.revenue_analyst_count is None)
    assert _consensus_rows(source)[missing_index]["Revenue analysts"] == "—"


def test_consensus_is_labeled_as_forward_analyst_consensus_from_fmp():
    text = rendered(complete_report()).text
    assert "Forward analyst consensus" in text
    assert "Source: FMP" in text
    assert "Stock Analyser forecast" not in text


def test_canonical_expectations_label_is_preserved():
    text = rendered(complete_report()).text
    assert "CANONICAL EXPECTATIONS" in text
    assert "Implied terminal growth" in text


def test_scenario_expectations_and_assumptions_are_read_only_and_preserved():
    source = scenario_report()
    ui = rendered(source)
    assert "SCENARIO EXPECTATIONS" in ui.text
    assumptions = ui.table("Assumption")
    assert len(assumptions) == len(source.expectations.entries[0].scenario_assumptions) == 3
    assert {row["Value"] for row in assumptions} == {"$90.00", "2.0000", "10.00%"}


def test_not_ready_expectations_retain_withheld_growth_and_blockers():
    source = partial_report()
    text = rendered(source).text
    assert "NOT READY" in text
    assert "Implied terminal growth" in text
    assert "Withheld" in text
    for blocker in source.expectations.blocking_reasons:
        assert blocker in text


def test_not_run_expectations_do_not_fabricate_scenario():
    source = report()
    ui = rendered(source)
    assert "NOT RUN" in ui.text
    assert all(not table or "Assumption" not in table[0] for table in ui.tables)


def test_no_scenario_controls_exist():
    source = UI_SOURCE.read_text(encoding="utf-8").lower()
    assert all(token not in source for token in ("number_input", "slider(", "text_input", "selectbox", "multiselect"))


def test_references_remain_reference_only():
    source = complete_report()
    rows = _reference_rows(source)
    assert len(rows) == len(source.references) == 2
    assert {row["Eligibility"] for row in rows} == {"REFERENCE ONLY"}
    assert all("Overall" not in row["Eligibility"] for row in rows)


def test_empty_reference_section_remains_visible():
    text = rendered(partial_report()).text
    assert "Reference-only evidence" in text
    assert "No reference-only evidence available for this snapshot" in text


def test_data_quality_rows_render_in_existing_order_without_score():
    source = partial_report()
    rows = _readiness_rows(source)
    assert [row["Status"] for row in rows] == [row.status_label for row in source.data_quality.rows]
    assert all("Score" not in row for row in rows)
    assert "No aggregate quality or confidence score is calculated" in rendered(source).text


def test_safe_source_labels_render_and_internal_metadata_is_not_accessed():
    text = rendered(complete_report()).text
    assert "Yahoo" in text and "FMP" in text
    source = UI_SOURCE.read_text(encoding="utf-8").lower()
    assert "companykey" not in source
    assert "api_key" not in source
    assert "authenticated_url" not in source


def test_null_market_price_renders_unavailable_not_numeric_zero():
    source = partial_report()
    assert source.market.normalized_market_price is None
    ui = rendered(source)
    header_metric = next(value for value in ui.markdowns if "Market price" in value)
    assert "Unavailable" in header_metric
    assert ">0<" not in header_metric and ">0.00<" not in header_metric


def test_gbp_and_gbp_quote_unit_remain_visibly_distinct():
    target = replace(identity(symbol="SYN.L"), quote_currency="GBP", quote_unit="GBp", price_scale=0.01)
    source = report(target_identity=target)
    ui = rendered(source)
    assert "GBP / GBp" in ui.text
    assert "SYN.L" in ui.text


def test_renderer_has_no_ticker_based_unit_conversion_branch():
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            names = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
            assert names.isdisjoint({"display_symbol", "canonical_symbol", "ticker"})


@pytest.mark.parametrize(
    "value,currency,expected",
    [
        (578.02, "USD", "$578.02 / share"),
        (7.5, "GBP", "£7.50 / share"),
        (254_122_887_348, "USD", "$254.1bn"),
    ],
)
def test_number_formatting_is_visual_only(value, currency, expected):
    assert format_report_currency(value, currency, compact=value > 1_000_000, per_share=value < 1_000) == expected
    assert value in {578.02, 7.5, 254_122_887_348}


def test_percentage_integer_and_timestamp_formatters_preserve_nulls():
    assert format_report_percent(None) == "—"
    assert format_report_integer(None) == "—"
    assert format_report_datetime(None) == "—"
    assert format_report_percent(0.392394) == "39.24%"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_feature_flag_accepts_only_explicit_enabled_values(value):
    assert is_v1_report_ui_enabled({"ENABLE_V1_REPORT_UI": value}) is True


@pytest.mark.parametrize("environment", [{}, {"ENABLE_V1_REPORT_UI": ""}, {"ENABLE_V1_REPORT_UI": "0"}, {"ENABLE_V1_REPORT_UI": "false"}])
def test_release_route_defaults_to_v1_and_ignores_deprecated_flag(environment):
    assert is_v1_report_ui_enabled(environment) is True


def test_feature_flag_route_is_before_legacy_page_execution():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert source.index("if is_v1_report_ui_enabled():") < source.index('st.title("STOCK ANALYSER")')
    assert "render_v1_report_route()" in source
    assert "st.stop()" in source[source.index("if is_v1_report_ui_enabled():"):source.index('st.title("STOCK ANALYSER")')]


def test_legacy_entrypoint_and_title_remain_present():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert 'st.title("STOCK ANALYSER")' in source
    assert "load_ticker(ticker)" in source
    assert "build_analysis(" in source


def test_isolated_route_delegates_to_the_v1_integration_shell():
    source = APP_SOURCE.read_text(encoding="utf-8")
    route_start = source.index("if is_v1_report_ui_enabled():")
    route_end = source.index("    st.stop()", route_start) + len("    st.stop()")
    route = source[route_start:route_end]
    assert "render_v1_report_route()" in route
    assert V1_REPORT_SESSION_KEY not in route
    assert all(token not in route for token in ("fetch_", "run_live_", "build_stock_research_report", "compare_publication"))


def test_empty_v1_route_waits_for_explicit_ticker_submission():
    ui = CaptureStreamlit()
    render_v1_report_empty_state(streamlit_module=ui)
    assert "Enter a ticker to begin" in ui.text
    assert "No analysis runs until you select Analyse" in ui.text


@pytest.mark.parametrize("forbidden", [
    "BUY", "HOLD", "SELL", "OVERWEIGHT", "UNDERWEIGHT", "cheap", "expensive",
    "appears undervalued", "appears overvalued", "investment stance",
])
def test_renderer_contains_no_stance_judgment_or_generated_narrative(forbidden):
    assert forbidden.lower() not in UI_SOURCE.read_text(encoding="utf-8").lower()
