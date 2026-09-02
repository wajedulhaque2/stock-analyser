from __future__ import annotations

from dataclasses import asdict
import ast
from pathlib import Path

from stock_analyser.domain import DataAvailability, ValuationFamily, ValuationMethodStatus
from stock_analyser.services import compare_publication_to_market
from stock_analyser.ui.v1_report import (
    _family_rows,
    _valuation_range_chart,
    _valuation_range_rows,
    format_report_currency,
    format_report_datetime,
)
from test_v1_market_comparison import family, price, publication
from test_v1_report_ui import rendered, scenario_report
from test_v1_research_report import complete_report, partial_report, report


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"


def unresolved_with_market():
    source = publication(family())
    market = price(value=90)
    comparison = compare_publication_to_market(source, market)
    return report(publication_result=source, market=market, comparison=comparison)


def negative_gap_report():
    source = publication(family(lower=70, central=80, upper=90))
    market = price(value=90)
    comparison = compare_publication_to_market(source, market)
    return report(publication_result=source, market=market, comparison=comparison)


def test_compact_security_header_is_report_driven_and_keeps_analysis_time_visible():
    source = complete_report()
    ui = rendered(source)
    header = next(value for value in ui.markdowns if "v1-report-header" in value and source.identity.company_name in value)
    assert source.identity.company_name in header
    assert source.identity.display_symbol in header
    assert source.identity.exchange in header
    assert format_report_datetime(source.analysis_as_of) in header
    assert format_report_currency(
        source.market.normalized_market_price,
        source.market.normalized_currency,
        per_share=True,
    ) in header
    css = ui.markdowns[0]
    assert "font-size: 1.45rem" in css
    assert "font-size: 4rem" not in css


def test_unavailable_market_block_remains_visible_with_status_and_reason():
    source = partial_report()
    ui = rendered(source)
    header = next(value for value in ui.markdowns if "v1-report-header" in value and source.identity.company_name in value)
    assert "Market price" in header
    assert "Unavailable" in header
    assert all(reason in ui.text for reason in source.market.blocking_reasons)
    assert "$0.00" not in header


def test_summary_shows_approved_family_denominator_and_excludes_reverse_dcf():
    resolved_ui = rendered(complete_report())
    unresolved_ui = rendered(scenario_report())
    resolved_summary = next(value for value in resolved_ui.markdowns if "Eligible valuation families" in value)
    unresolved_summary = next(value for value in unresolved_ui.markdowns if "Eligible valuation families" in value)
    assert "2 of 2" in resolved_summary
    assert "1 of 2" in unresolved_summary
    assert "Reverse DCF" not in resolved_summary
    assert "Reverse DCF" not in unresolved_summary


def test_family_table_uses_human_labels_supplied_values_and_method_label():
    source = complete_report()
    rows = _family_rows(source)
    own, peer = rows
    assert own["Valuation family"] == "Own-history valuation"
    assert peer["Valuation family"] == "Peer valuation"
    assert own["Method"] == "EV / EBITDA"
    for row, family_section in zip(rows, source.valuation_families, strict=True):
        assert row["Lower"] == format_report_currency(
            family_section.lower_value, family_section.currency, per_share=True,
        )
        assert row["Central"] == format_report_currency(
            family_section.central_value, family_section.currency, per_share=True,
        )
        assert row["Upper"] == format_report_currency(
            family_section.upper_value, family_section.currency, per_share=True,
        )


def test_unavailable_peer_remains_a_factual_row_and_never_uses_zero():
    source = partial_report()
    row = _family_rows(source)[1]
    assert row["Valuation family"] == "Peer valuation"
    assert row["Status"] == "Unavailable"
    assert row["Lower"] == row["Central"] == row["Upper"] == "—"
    assert "0.00" not in str(row)
    assert source.valuation_families[1].blocking_reasons[0] == row["Evidence / blocker"]


def test_positive_and_negative_family_gap_signs_are_preserved_from_report():
    positive = unresolved_with_market()
    negative = negative_gap_report()
    assert positive.valuation_families[0].central_gap_percent > 0
    assert negative.valuation_families[0].central_gap_percent < 0
    assert _family_rows(positive)[0]["Central gap vs market"].startswith("+")
    assert _family_rows(negative)[0]["Central gap vs market"].startswith("-")


def test_missing_family_gap_stays_blank_and_is_not_derived_from_values():
    source = partial_report()
    assert source.valuation_families[0].comparison_status is DataAvailability.UNAVAILABLE
    assert _family_rows(source)[0]["Central gap vs market"] == "—"


def test_range_rows_copy_family_points_and_market_marker_without_interpolation():
    source = complete_report()
    rows = _valuation_range_rows(source)
    family_rows = rows[:2]
    for row, family_section in zip(family_rows, source.valuation_families, strict=True):
        assert (row["Lower"], row["Central"], row["Upper"]) == (
            family_section.lower_value,
            family_section.central_value,
            family_section.upper_value,
        )
        assert row["Market"] == source.market.normalized_market_price


def test_unavailable_peer_has_no_chart_point_and_market_unavailable_has_no_marker():
    source = partial_report()
    rows = _valuation_range_rows(source)
    assert [row["Label"] for row in rows] == ["Own-history valuation"]
    assert rows[0]["Market"] is None
    specification = _valuation_range_chart(rows).to_dict()
    assert all(layer["mark"]["type"] != "tick" for layer in specification["layer"])


def test_unresolved_report_never_promotes_one_family_to_overall_range():
    source = unresolved_with_market()
    rows = _valuation_range_rows(source)
    assert source.valuation_summary.overall_central_value is None
    assert "Overall published valuation" not in {row["Label"] for row in rows}
    assert all(row["Kind"] == "Valuation family" for row in rows)


def test_resolved_overall_range_uses_only_upstream_envelope_and_central():
    source = complete_report()
    summary = source.valuation_summary
    overall = next(
        row for row in _valuation_range_rows(source)
        if row["Label"] == "Overall published valuation"
    )
    assert (overall["Lower"], overall["Central"], overall["Upper"]) == (
        summary.envelope_lower,
        summary.overall_central_value,
        summary.envelope_upper,
    )
    assert overall["Market"] == summary.overall_market_price


def test_rendered_range_chart_has_textual_key_and_no_calculation_transform():
    ui = rendered(complete_report())
    assert len(ui.charts) == 2
    specification = ui.charts[0].to_dict()
    assert "calculate" not in str(specification).lower()
    assert "Range line = supplied lower to upper" in ui.text
    assert "current market price" in ui.text.lower()


def test_methodology_states_publication_rule_and_reverse_dcf_separation():
    text = rendered(complete_report()).text
    assert "Target historical multiples" in text
    assert "Comparable-company multiples" in text
    assert "Minimum two eligible independent valuation families" in text
    assert "Market expectations only; not a valuation family" in text


def test_primary_view_keeps_safe_sources_but_not_internal_identifiers():
    source = complete_report()
    text = rendered(source).text
    assert source.market.source_label in text
    assert source.consensus.source_label in text
    assert source.target_security_id not in text
    assert source.target_issuer_id not in text
    assert source.report_id not in text


def test_presentation_does_not_mutate_report_or_add_valuation_arithmetic():
    source = unresolved_with_market()
    before = asdict(source)
    rendered(source)
    assert asdict(source) == before
    guarded = {
        "overall_central_value", "envelope_lower", "envelope_upper",
        "lower_value", "central_value", "upper_value",
        "central_gap", "central_gap_percent", "normalized_market_price",
    }
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            attributes = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
            assert attributes.isdisjoint(guarded)


def test_visual_language_remains_institutional_and_avoids_marketing_effects():
    source = UI_SOURCE.read_text(encoding="utf-8").lower()
    assert "arial" in source and "helvetica" in source and "segoe ui" in source
    assert all(token not in source for token in (
        "linear-gradient", "radial-gradient", "glassmorphism", "box-shadow",
        "inter,", "geist", "space grotesk", "✨",
    ))
    assert all(token not in source for token in (
        "buy", "hold", "sell", "overweight", "underweight", "cheap", "expensive",
    ))
