"""Faithful Streamlit rendering for a pre-built :class:`StockResearchReport`.

This module owns visual formatting and layout only. It deliberately imports no
provider, report-builder, valuation, comparison, or reverse-DCF service.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape
import math
from typing import Any, Callable

import altair as alt
import streamlit as st

from stock_analyser.domain import (
    AggregationStatus,
    DataAvailability,
    ReportDataQualityCategory,
    ReportExpectationState,
    ReverseDcfPublicationEligibility,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfSolverStatus,
    StockResearchReport,
    ValuationFamily,
)


V1_REPORT_SESSION_KEY = "v1_stock_research_report"
_APPROVED_CENTRAL_FAMILY_COUNT = 2

V1_REPORT_CSS = """
<style>
:root {
  --v1-bg: #f4f2ed;
  --v1-panel: #fbfaf7;
  --v1-ink: #20272d;
  --v1-navy: #243a4d;
  --v1-muted: #667078;
  --v1-line: #c9c7c0;
  --v1-accent: #31596b;
  --v1-positive: #456650;
  --v1-warning: #806733;
  --v1-negative: #7a4944;
}
.stApp { background: var(--v1-bg); color: var(--v1-ink); }
.block-container { max-width: 1400px; padding-top: 3.35rem; padding-bottom: 3rem; }
header[data-testid="stHeader"] { background: var(--v1-bg); }
.v1-report, .v1-report button, .v1-report input { font-family: Arial, Helvetica, "Segoe UI", sans-serif; }
.v1-report, .v1-report * { box-sizing: border-box; }
.v1-report-header { align-items: stretch; border-top: 3px solid var(--v1-navy); border-bottom: 1px solid var(--v1-line); display: grid; grid-template-columns: minmax(0, 1fr) minmax(250px, 340px); margin-bottom: 0; }
.v1-security-identity { padding: .8rem .85rem .75rem 0; }
.v1-market-panel { background: var(--v1-panel); border-left: 1px solid var(--v1-line); padding: .65rem .8rem .6rem; }
.v1-kicker { color: var(--v1-muted); font-size: .68rem; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
.v1-report-title { color: var(--v1-ink); font-size: 1.45rem; font-weight: 700; letter-spacing: .005em; line-height: 1.15; margin: .2rem 0 .25rem; }
.v1-report-meta { color: var(--v1-muted); font-size: .79rem; line-height: 1.45; }
.v1-market-price { color: var(--v1-ink); font-size: 1.55rem; font-variant-numeric: tabular-nums; font-weight: 750; letter-spacing: -.015em; line-height: 1.15; margin: .2rem 0 .32rem; }
.v1-market-detail { color: var(--v1-muted); font-size: .7rem; line-height: 1.4; margin-top: .32rem; }
.v1-identity-strip { background: var(--v1-panel); border-bottom: 1px solid var(--v1-line); display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: .7rem; }
.v1-identity-item { border-right: 1px solid var(--v1-line); padding: .42rem .6rem; }
.v1-identity-item:last-child { border-right: 0; }
.v1-identity-label { color: var(--v1-muted); font-size: .61rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
.v1-identity-value { color: var(--v1-ink); font-size: .76rem; font-variant-numeric: tabular-nums; margin-top: .08rem; }
.v1-section-title { border-bottom: 1px solid var(--v1-line); color: var(--v1-navy); font-size: .82rem; font-weight: 800; letter-spacing: .075em; margin: 1.25rem 0 .65rem; padding-bottom: .36rem; text-transform: uppercase; }
.v1-metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0; border-left: 1px solid var(--v1-line); border-top: 1px solid var(--v1-line); margin-bottom: .65rem; }
.v1-metric { background: var(--v1-panel); border-bottom: 1px solid var(--v1-line); border-right: 1px solid var(--v1-line); min-height: 74px; padding: .55rem .65rem; }
.v1-metric--primary { border-top: 2px solid var(--v1-navy); padding-top: calc(.55rem - 2px); }
.v1-metric-label { color: var(--v1-muted); font-size: .65rem; font-weight: 700; letter-spacing: .055em; text-transform: uppercase; }
.v1-metric-value { color: var(--v1-ink); font-size: 1rem; font-variant-numeric: tabular-nums; font-weight: 650; line-height: 1.25; margin-top: .22rem; }
.v1-metric--primary .v1-metric-value { font-size: 1.12rem; font-weight: 750; }
.v1-chip { border: 1px solid var(--v1-line); color: var(--v1-ink); display: inline-block; font-size: .67rem; font-weight: 750; letter-spacing: .04em; padding: .15rem .38rem; text-transform: uppercase; }
.v1-chip--ready { border-color: #8fa092; color: var(--v1-positive); }
.v1-chip--partial { border-color: #aa9a77; color: var(--v1-warning); }
.v1-chip--unavailable { border-color: #a9918e; color: var(--v1-negative); }
.v1-note { border-left: 3px solid var(--v1-line); color: var(--v1-muted); font-size: .78rem; line-height: 1.45; margin: .45rem 0; padding: .35rem .65rem; }
.v1-blocker-list { color: var(--v1-ink); font-size: .79rem; line-height: 1.45; margin: .35rem 0 .6rem 1.1rem; padding: 0; }
.v1-source-line { color: var(--v1-muted); font-size: .72rem; margin: .25rem 0 .5rem; }
.v1-method-grid { border-left: 1px solid var(--v1-line); border-top: 1px solid var(--v1-line); display: grid; grid-template-columns: minmax(155px, .7fr) minmax(240px, 1.3fr); margin: .35rem 0 .7rem; }
.v1-method-grid > div { border-bottom: 1px solid var(--v1-line); border-right: 1px solid var(--v1-line); font-size: .77rem; line-height: 1.35; padding: .38rem .5rem; }
.v1-method-label { background: #f0eee8; color: var(--v1-navy); font-weight: 700; }
.v1-chart-key { color: var(--v1-muted); font-size: .72rem; line-height: 1.4; margin: -.2rem 0 .45rem; }
.v1-consensus-intro { align-items: center; display: flex; flex-wrap: wrap; gap: .45rem .65rem; margin: 0 0 .5rem; }
.v1-consensus-context { color: var(--v1-ink); font-size: .78rem; font-weight: 650; }
.v1-consensus-snapshot { color: var(--v1-muted); font-size: .7rem; margin-left: auto; }
.v1-consensus-table-wrap { background: var(--v1-panel); border: 1px solid var(--v1-line); overflow-x: auto; }
.v1-consensus-table { border-collapse: collapse; font-size: .75rem; min-width: 900px; width: 100%; }
.v1-consensus-table th { background: #f0eee8; color: var(--v1-muted); font-size: .62rem; font-weight: 750; letter-spacing: .035em; padding: .38rem .45rem; text-align: left; text-transform: uppercase; white-space: nowrap; }
.v1-consensus-table td { border-top: 1px solid #dedbd3; color: var(--v1-ink); padding: .37rem .45rem; white-space: nowrap; }
.v1-consensus-table .v1-num { font-variant-numeric: tabular-nums; text-align: right; }
.v1-consensus-table .v1-period { color: var(--v1-navy); font-weight: 750; }
.v1-consensus-table .v1-growth-positive { color: var(--v1-positive); }
.v1-consensus-table .v1-growth-negative { color: var(--v1-negative); }
.v1-expectation-head { align-items: center; background: var(--v1-panel); border: 1px solid var(--v1-line); border-left: 3px solid var(--v1-navy); display: flex; flex-wrap: wrap; gap: .5rem .7rem; justify-content: space-between; margin-bottom: .55rem; padding: .55rem .65rem; }
.v1-expectation-head--scenario { background: #f6f1e7; border-left-color: var(--v1-warning); }
.v1-expectation-title { color: var(--v1-ink); font-size: .82rem; font-weight: 750; }
.v1-expectation-context { color: var(--v1-muted); font-size: .72rem; }
.v1-expectation-meta { border-bottom: 1px solid var(--v1-line); border-top: 1px solid var(--v1-line); color: var(--v1-muted); display: flex; flex-wrap: wrap; font-size: .72rem; gap: .4rem 1rem; margin: -.1rem 0 .55rem; padding: .35rem .05rem; }
.v1-expectation-meta strong { color: var(--v1-ink); }
.v1-withheld-panel { background: var(--v1-panel); border: 1px solid var(--v1-line); border-left: 3px solid var(--v1-warning); margin-bottom: .45rem; padding: .65rem .75rem; }
.v1-withheld-value { color: var(--v1-ink); font-size: 1.16rem; font-weight: 750; margin-top: .18rem; }
.v1-scenario-label { color: var(--v1-warning); font-size: .68rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
.v1-evidence-table-wrap { background: var(--v1-panel); border: 1px solid var(--v1-line); overflow-x: auto; }
.v1-evidence-table { border-collapse: collapse; font-size: .75rem; table-layout: fixed; width: 100%; }
.v1-evidence-table th { background: #f0eee8; color: var(--v1-muted); font-size: .62rem; font-weight: 750; letter-spacing: .035em; padding: .38rem .45rem; text-align: left; text-transform: uppercase; }
.v1-evidence-table td { border-top: 1px solid #dedbd3; color: var(--v1-ink); line-height: 1.35; overflow-wrap: anywhere; padding: .4rem .45rem; vertical-align: top; }
.v1-evidence-area { color: var(--v1-navy); font-weight: 750; }
.v1-reference-tag { border: 1px solid #a9918e; color: var(--v1-negative); display: inline-block; font-size: .64rem; font-weight: 800; letter-spacing: .045em; padding: .13rem .3rem; text-transform: uppercase; }
.v1-readiness-notes { border-left: 2px solid var(--v1-line); margin: .55rem 0 .7rem; padding: .08rem 0 .08rem .65rem; }
.v1-readiness-note { color: var(--v1-ink); font-size: .76rem; line-height: 1.42; margin: .22rem 0; }
.v1-readiness-note strong { color: var(--v1-navy); }
.v1-appendix-heading { border-bottom: 1px solid var(--v1-line); color: var(--v1-navy); font-size: .69rem; font-weight: 800; letter-spacing: .06em; margin: .75rem 0 .35rem; padding-bottom: .25rem; text-transform: uppercase; }
.v1-snapshot-grid { background: var(--v1-panel); border-left: 1px solid var(--v1-line); border-top: 1px solid var(--v1-line); display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.v1-snapshot-item { border-bottom: 1px solid var(--v1-line); border-right: 1px solid var(--v1-line); padding: .42rem .5rem; }
.v1-snapshot-label { color: var(--v1-muted); font-size: .61rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }
.v1-snapshot-value { color: var(--v1-ink); font-size: .75rem; font-variant-numeric: tabular-nums; margin-top: .12rem; }
[data-testid="stDataFrame"] { border: 1px solid var(--v1-line); border-radius: 0; }
div[data-testid="stExpander"] { background: var(--v1-panel); border-color: var(--v1-line) !important; border-radius: 2px !important; }
@media (max-width: 900px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .v1-report-header { grid-template-columns: 1fr; }
  .v1-security-identity { padding-right: 0; }
  .v1-market-panel { border-left: 0; border-top: 1px solid var(--v1-line); }
  .v1-identity-strip { grid-template-columns: 1fr; }
  .v1-identity-item { border-bottom: 1px solid var(--v1-line); border-right: 0; }
  .v1-identity-item:last-child { border-bottom: 0; }
  .v1-metric-grid { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
  .v1-consensus-snapshot { margin-left: 0; width: 100%; }
  .v1-snapshot-grid { grid-template-columns: 1fr; }
  .v1-evidence-table { min-width: 720px; table-layout: auto; }
}
</style>
"""

_REPORT_CONTRACT_FIELDS = (
    "report_id", "target_security_id", "target_issuer_id", "analysis_as_of",
    "identity", "market", "valuation_summary", "valuation_families",
    "consensus", "expectations", "references", "data_quality",
    "source_references", "issues", "warnings",
)


def _finite(value: float | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _is_stock_research_report(value: object) -> bool:
    """Allow the exact immutable contract across Streamlit hot reloads."""
    if isinstance(value, StockResearchReport):
        return True
    value_type = type(value)
    return (
        value_type.__module__ == StockResearchReport.__module__
        and value_type.__qualname__ == StockResearchReport.__qualname__
        and all(hasattr(value, field_name) for field_name in _REPORT_CONTRACT_FIELDS)
    )


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def format_report_datetime(value: datetime | None) -> str:
    return value.astimezone().strftime("%d %b %Y, %H:%M %Z") if value is not None else "—"


def format_report_date(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value is not None else "—"


def format_report_number(value: float | int | None, *, digits: int = 2) -> str:
    numeric = _finite(value)
    return "—" if numeric is None else f"{numeric:,.{digits}f}"


def format_report_integer(value: int | None) -> str:
    return "—" if value is None else f"{value:,d}"


def format_report_percent(value: float | None, *, signed: bool = False) -> str:
    numeric = _finite(value)
    if numeric is None:
        return "—"
    prefix = "+" if signed and numeric > 0 else ""
    return f"{prefix}{numeric * 100:,.2f}%"


def _compact_number(value: float) -> tuple[float, str]:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000_000:
        return value / 1_000_000_000_000, "tn"
    if magnitude >= 1_000_000_000:
        return value / 1_000_000_000, "bn"
    if magnitude >= 1_000_000:
        return value / 1_000_000, "m"
    return value, ""


def format_report_currency(
    value: float | None,
    currency: str | None,
    *,
    compact: bool = False,
    per_share: bool = False,
) -> str:
    numeric = _finite(value)
    if numeric is None:
        return "—"
    symbols = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥"}
    prefix = symbols.get(currency or "", f"{currency} " if currency else "")
    display_value, suffix = _compact_number(numeric) if compact else (numeric, "")
    decimals = 1 if compact and suffix else 2
    basis = " / share" if per_share else ""
    return f"{prefix}{display_value:,.{decimals}f}{suffix}{basis}"


def _controlled_label(value: object) -> str:
    return str(_controlled_value(value)).replace("_", " ").strip().title()


def _controlled_value(value: object) -> object:
    return getattr(value, "value", value)


def _status_tone(label: str) -> str:
    normalized = label.lower()
    if any(token in normalized for token in ("ready", "resolved", "valid", "available")) and not any(
        token in normalized for token in ("not ready", "unavailable", "unresolved")
    ):
        return "ready"
    if any(token in normalized for token in ("partial", "wide", "unresolved", "not ready")):
        return "partial"
    return "unavailable"


def _chip(label: str) -> str:
    return f'<span class="v1-chip v1-chip--{_status_tone(label)}">{_text(label)}</span>'


def _section_title(ui: Any, label: str) -> None:
    ui.markdown(f'<div class="v1-section-title">{_text(label)}</div>', unsafe_allow_html=True)


def _metric_grid(
    ui: Any,
    values: tuple[tuple[str, str], ...],
    *,
    primary_labels: tuple[str, ...] = (),
) -> None:
    panels = "".join(
        f'<div class="v1-metric{" v1-metric--primary" if label in primary_labels else ""}">'
        f'<div class="v1-metric-label">{_text(label)}</div>'
        f'<div class="v1-metric-value">{value}</div>'
        "</div>"
        for label, value in values
    )
    ui.markdown(f'<div class="v1-metric-grid">{panels}</div>', unsafe_allow_html=True)


def _source_names(values: tuple[object, ...]) -> str:
    names = tuple(dict.fromkeys(str(value.display_name) for value in values))
    return ", ".join(names) if names else "No source label supplied"


def _source_line(ui: Any, values: tuple[object, ...]) -> None:
    ui.markdown(
        f'<div class="v1-source-line">Sources: {_text(_source_names(values))}</div>',
        unsafe_allow_html=True,
    )


def _blockers(ui: Any, values: tuple[str, ...], *, empty: str | None = None) -> None:
    if values:
        items = "".join(f"<li>{_text(value)}</li>" for value in values)
        ui.markdown(f'<ul class="v1-blocker-list">{items}</ul>', unsafe_allow_html=True)
    elif empty:
        ui.markdown(f'<div class="v1-note">{_text(empty)}</div>', unsafe_allow_html=True)


def _render_header(ui: Any, report: StockResearchReport) -> None:
    identity = report.identity
    market_price = format_report_currency(
        report.market.normalized_market_price,
        report.market.normalized_currency,
        per_share=True,
    ) if report.market.normalized_market_price is not None else "Unavailable"
    ui.markdown(
        '<div class="v1-report v1-report-header">'
        '<div class="v1-security-identity">'
        '<div class="v1-kicker">Equity research snapshot</div>'
        f'<div class="v1-report-title">{_text(identity.company_name)}</div>'
        f'<div class="v1-report-meta"><strong>{_text(identity.display_symbol)}</strong> · {_text(identity.exchange)}</div>'
        f'<div class="v1-report-meta">Analysis timestamp · {_text(format_report_datetime(report.analysis_as_of))}</div>'
        '</div>'
        '<div class="v1-market-panel">'
        '<div class="v1-metric-label">Market price</div>'
        f'<div class="v1-market-price">{_text(market_price)}</div>'
        f'{_chip(_controlled_label(report.market.availability))}'
        f'<div class="v1-market-detail">Observed {_text(format_report_datetime(report.market.observation_timestamp))}<br>'
        f'Source · {_text(report.market.source_label)}</div>'
        '</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    ui.markdown(
        '<div class="v1-report v1-identity-strip">'
        '<div class="v1-identity-item"><div class="v1-identity-label">Reporting currency</div>'
        f'<div class="v1-identity-value">{_text(identity.reporting_currency)}</div></div>'
        '<div class="v1-identity-item"><div class="v1-identity-label">Quote currency / unit</div>'
        f'<div class="v1-identity-value">{_text(identity.quote_currency)} / {_text(identity.quote_unit)}</div></div>'
        '<div class="v1-identity-item"><div class="v1-identity-label">Quote scale</div>'
        f'<div class="v1-identity-value">{_text(f"{identity.quote_price_scale:g}")}</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    _blockers(ui, report.market.blocking_reasons)


def _render_summary(ui: Any, report: StockResearchReport) -> None:
    summary = report.valuation_summary
    central = (
        format_report_currency(summary.overall_central_value, summary.currency, per_share=True)
        if summary.overall_central_value is not None else summary.overall_value_label
    )
    market_price = (
        format_report_currency(
            report.market.normalized_market_price,
            report.market.normalized_currency,
            per_share=True,
        )
        if report.market.normalized_market_price is not None else "Unavailable"
    )
    comparison = (
        format_report_percent(summary.overall_central_gap_percent, signed=True)
        if _controlled_value(summary.overall_comparison_status) == DataAvailability.AVAILABLE.value
        and summary.overall_central_gap_percent is not None else "Withheld"
    )
    _section_title(ui, "Valuation summary")
    _metric_grid(ui, (
        ("Publication status", _chip(summary.publication_label)),
        ("Overall fair value", _text(central)),
        ("Market price", _text(market_price)),
        ("Eligible valuation families", _text(f"{summary.eligible_family_count} of {_APPROVED_CENTRAL_FAMILY_COUNT}")),
        ("Overall valuation gap", _text(comparison)),
    ), primary_labels=("Publication status", "Overall fair value", "Market price"))
    _blockers(ui, summary.blocking_reasons)
    _source_line(ui, summary.source_references)


def _family_name(family: ValuationFamily) -> str:
    return (
        "Own-history valuation"
        if _controlled_value(family) == ValuationFamily.OWN_HISTORY.value
        else "Peer valuation"
    )


def _method_name(value: str | None) -> str:
    if value is None:
        return "—"
    return {
        "EV_EBITDA": "EV / EBITDA",
        "EV_EBIT": "EV / EBIT",
        "P_E": "P / E",
    }.get(value, value.replace("_", " ").title())


def _family_rows(report: StockResearchReport) -> tuple[dict[str, str], ...]:
    rows = []
    for family in report.valuation_families:
        source_or_blocker = (
            family.blocking_reasons[0]
            if family.blocking_reasons
            else f"Source: {_source_names(family.source_references)}"
        )
        rows.append({
            "Valuation family": _family_name(family.family),
            "Status": _controlled_label(family.family_status),
            "Method": _method_name(family.method_label),
            "Lower": format_report_currency(family.lower_value, family.currency, per_share=True),
            "Central": format_report_currency(family.central_value, family.currency, per_share=True),
            "Upper": format_report_currency(family.upper_value, family.currency, per_share=True),
            "Central gap vs market": (
                format_report_percent(family.central_gap_percent, signed=True)
                if _controlled_value(family.comparison_status) == DataAvailability.AVAILABLE.value
                else "—"
            ),
            "Currency / share": (
                family.per_share_unit
                if family.per_share_unit is not None
                else f"{family.currency} / share"
                if family.currency is not None
                else "—"
            ),
            "Evidence / blocker": source_or_blocker,
        })
    return tuple(rows)


def _valuation_range_rows(report: StockResearchReport) -> tuple[dict[str, object], ...]:
    """Return supplied chart points only; never derive valuation values."""
    rows: list[dict[str, object]] = []
    market_value = (
        report.market.normalized_market_price
        if _controlled_value(report.market.availability) == DataAvailability.AVAILABLE.value
        else None
    )
    for family in report.valuation_families:
        if any(value is None for value in (
            family.lower_value, family.central_value, family.upper_value,
        )):
            continue
        rows.append({
            "Label": _family_name(family.family),
            "Kind": "Valuation family",
            "Lower": family.lower_value,
            "Central": family.central_value,
            "Upper": family.upper_value,
            "Market": (
                market_value
                if family.currency == report.market.normalized_currency else None
            ),
            "Currency": family.currency,
            "Per-share unit": family.per_share_unit,
        })
    summary = report.valuation_summary
    if (
        _controlled_value(summary.publication_status) == AggregationStatus.RESOLVED.value
        and all(value is not None for value in (
            summary.envelope_lower, summary.overall_central_value, summary.envelope_upper,
        ))
    ):
        rows.append({
            "Label": "Overall published valuation",
            "Kind": "Published overall",
            "Lower": summary.envelope_lower,
            "Central": summary.overall_central_value,
            "Upper": summary.envelope_upper,
            "Market": (
                summary.overall_market_price
                if summary.currency == report.market.normalized_currency else None
            ),
            "Currency": summary.currency,
            "Per-share unit": summary.per_share_unit,
        })
    return tuple(rows)


def _valuation_range_chart(rows: tuple[dict[str, object], ...]) -> alt.Chart:
    labels = [str(row["Label"]) for row in rows]
    currency = str(rows[0]["Currency"] or "native currency")
    y = alt.Y("Label:N", sort=labels, title=None, axis=alt.Axis(labelLimit=190))
    tooltip = [
        alt.Tooltip("Label:N", title="Series"),
        alt.Tooltip("Lower:Q", title="Lower", format=",.2f"),
        alt.Tooltip("Central:Q", title="Central", format=",.2f"),
        alt.Tooltip("Upper:Q", title="Upper", format=",.2f"),
        alt.Tooltip("Market:Q", title="Market", format=",.2f"),
        alt.Tooltip("Currency:N", title="Currency"),
    ]
    base = alt.Chart(alt.Data(values=list(rows))).encode(y=y)
    ranges = base.mark_rule(color="#31596b", strokeWidth=4).encode(
        x=alt.X("Lower:Q", title=f"Supplied value ({currency} / share)", scale=alt.Scale(zero=False)),
        x2="Upper:Q",
        tooltip=tooltip,
    )
    central = base.mark_point(
        color="#243a4d", filled=True, shape="square", size=85,
    ).encode(x="Central:Q", tooltip=tooltip)
    market_rows = tuple(row for row in rows if row["Market"] is not None)
    layers = ranges + central
    if market_rows:
        market = alt.Chart(alt.Data(values=list(market_rows))).mark_tick(
            color="#806733", thickness=2, size=22,
        ).encode(x="Market:Q", y=y, tooltip=tooltip)
        layers += market
    return layers.properties(
        height=max(95, 42 * len(rows)),
        title=alt.TitleParams(
            "Valuation range",
            subtitle="Supplied lower–upper range, central estimate, and current market price",
            anchor="start",
            color="#243a4d",
            font="Arial",
            fontSize=13,
            subtitleColor="#667078",
            subtitleFont="Arial",
            subtitleFontSize=10,
        ),
    ).configure_view(stroke="#c9c7c0").configure_axis(
        gridColor="#dedbd3", labelColor="#20272d", titleColor="#667078",
        labelFont="Arial", titleFont="Arial",
    )


def _render_valuation_ranges(ui: Any, report: StockResearchReport) -> None:
    rows = _valuation_range_rows(report)
    if not rows:
        ui.markdown(
            '<div class="v1-note">No supplied valuation range is available to plot.</div>',
            unsafe_allow_html=True,
        )
        return
    currencies = {row["Currency"] for row in rows}
    if None in currencies or len(currencies) != 1:
        ui.markdown(
            '<div class="v1-note">Valuation ranges remain in the table because their supplied currencies are not directly comparable.</div>',
            unsafe_allow_html=True,
        )
        return
    ui.altair_chart(_valuation_range_chart(rows), width="stretch", theme=None)
    ui.markdown(
        '<div class="v1-chart-key">Range line = supplied lower to upper · square = supplied central estimate · '
        'vertical tick = supplied current market price. Missing methods are not plotted; exact textual values remain above.</div>',
        unsafe_allow_html=True,
    )


def _render_families(ui: Any, report: StockResearchReport) -> None:
    _section_title(ui, "Valuation families")
    ui.dataframe(_family_rows(report), width="stretch", hide_index=True)
    family_blockers = tuple(
        f"{_family_name(family.family)}: {reason}"
        for family in report.valuation_families
        for reason in family.blocking_reasons
    )
    _blockers(ui, family_blockers)
    _render_valuation_ranges(ui, report)
    ui.markdown(
        '<div class="v1-note">Family values and family price gaps are method-level evidence. '
        'They are not the overall fair value unless the upstream publication separately resolves one.</div>',
        unsafe_allow_html=True,
    )


def _render_consensus(ui: Any, report: StockResearchReport) -> None:
    consensus = report.consensus
    _section_title(ui, "Forward analyst consensus")
    source_label = (
        _source_names(consensus.source_references)
        if consensus.source_references else consensus.source_label
    )
    snapshots = tuple(dict.fromkeys(
        format_report_datetime(period.estimate_as_of) for period in consensus.periods
    ))
    snapshot_label = " · ".join(snapshots) if snapshots else "—"
    ui.markdown(
        '<div class="v1-consensus-intro">'
        f'{_chip(_controlled_label(consensus.readiness_status or consensus.status))}'
        '<span class="v1-consensus-context">External analyst consensus · externally sourced, no internal forecast</span>'
        f'<span class="v1-source-line">Source: {_text(source_label)} · Evidence: {_text(consensus.source_label)} · {consensus.period_count} periods</span>'
        f'<span class="v1-consensus-snapshot">Estimate snapshot · {_text(snapshot_label)}</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    rows = _consensus_rows(report)
    if rows:
        table_rows = "".join(
            '<tr>'
            f'<td class="v1-period">{_text(row["Fiscal year"])}</td>'
            f'<td>{_text(row["Period end"])}</td>'
            f'<td class="v1-num">{_text(row["Revenue"])}</td>'
            f'<td class="v1-num {row["Growth tone"]}">{_text(row["Revenue growth"])}</td>'
            f'<td class="v1-num">{_text(row["EBIT"])}</td>'
            f'<td class="v1-num">{_text(row["EBIT margin"])}</td>'
            f'<td class="v1-num">{_text(row["EBITDA"])}</td>'
            f'<td class="v1-num">{_text(row["Revenue analysts"])}</td>'
            '</tr>'
            for row in rows
        )
        ui.markdown(
            '<div class="v1-consensus-table-wrap"><table class="v1-consensus-table">'
            '<thead><tr><th>Fiscal year</th><th>Period end</th><th class="v1-num">Revenue</th>'
            '<th class="v1-num">Revenue growth</th><th class="v1-num">EBIT</th>'
            '<th class="v1-num">EBIT margin</th><th class="v1-num">EBITDA</th>'
            '<th class="v1-num">Revenue analysts</th></tr></thead>'
            f'<tbody>{table_rows}</tbody></table></div>',
            unsafe_allow_html=True,
        )
        chart_rows = _consensus_chart_rows(report)
        if chart_rows:
            ui.altair_chart(_consensus_chart(chart_rows), width="stretch", theme=None)
            ui.markdown(
                '<div class="v1-chart-key">Supplied revenue-growth and EBIT-margin points only. '
                'Missing metrics are omitted; no interpolation or extrapolation is applied.</div>',
                unsafe_allow_html=True,
            )
    else:
        ui.markdown('<div class="v1-note">No forward consensus periods are available for this snapshot.</div>', unsafe_allow_html=True)
    _blockers(ui, consensus.blocking_reasons)


def _consensus_rows(report: StockResearchReport) -> tuple[dict[str, str], ...]:
    """Return formatted supplied consensus values in their existing order."""
    rows = []
    for period in report.consensus.periods:
        growth = _finite(period.revenue_growth)
        tone = (
            "v1-growth-positive" if growth is not None and growth > 0
            else "v1-growth-negative" if growth is not None and growth < 0
            else ""
        )
        rows.append({
            "Fiscal year": period.horizon_label,
            "Period end": format_report_date(period.fiscal_period_end),
            "Revenue": format_report_currency(period.revenue, period.currency, compact=True),
            "Revenue growth": format_report_percent(period.revenue_growth, signed=True),
            "EBIT": format_report_currency(period.ebit, period.currency, compact=True),
            "EBIT margin": format_report_percent(period.ebit_margin),
            "EBITDA": format_report_currency(period.ebitda, period.currency, compact=True),
            "Revenue analysts": format_report_integer(period.revenue_analyst_count),
            "Growth tone": tone,
        })
    return tuple(rows)


def _consensus_chart_rows(report: StockResearchReport) -> tuple[dict[str, object], ...]:
    """Return supplied percentage points only; absent values never become zero."""
    rows: list[dict[str, object]] = []
    for period in report.consensus.periods:
        for metric, value in (
            ("Revenue growth", period.revenue_growth),
            ("EBIT margin", period.ebit_margin),
        ):
            if value is None:
                continue
            rows.append({
                "Fiscal year": period.horizon_label,
                "Period end": format_report_date(period.fiscal_period_end),
                "Metric": metric,
                "Value": value,
                "Estimate snapshot": format_report_datetime(period.estimate_as_of),
            })
    return tuple(rows)


def _consensus_chart(rows: tuple[dict[str, object], ...]) -> alt.Chart:
    periods = tuple(dict.fromkeys(str(row["Fiscal year"]) for row in rows))
    return alt.Chart(alt.Data(values=list(rows))).mark_point(
        filled=True, size=72,
    ).encode(
        x=alt.X("Fiscal year:N", sort=list(periods), title=None),
        y=alt.Y(
            "Value:Q", title="Supplied percentage", axis=alt.Axis(format=".0%"),
            scale=alt.Scale(zero=False),
        ),
        color=alt.Color(
            "Metric:N", sort=["Revenue growth", "EBIT margin"],
            scale=alt.Scale(domain=["Revenue growth", "EBIT margin"], range=["#31596b", "#806733"]),
            legend=alt.Legend(orient="top", title=None),
        ),
        shape=alt.Shape(
            "Metric:N", sort=["Revenue growth", "EBIT margin"],
            scale=alt.Scale(domain=["Revenue growth", "EBIT margin"], range=["circle", "square"]),
            legend=None,
        ),
        tooltip=(
            alt.Tooltip("Fiscal year:N", title="Fiscal year"),
            alt.Tooltip("Period end:N", title="Period end"),
            alt.Tooltip("Metric:N", title="Metric"),
            alt.Tooltip("Value:Q", title="Supplied value", format=".2%"),
            alt.Tooltip("Estimate snapshot:N", title="Estimate snapshot"),
        ),
    ).properties(
        height=185,
        title=alt.TitleParams(
            "Consensus growth and margin",
            subtitle="External analyst consensus · supplied percentage points",
            anchor="start", color="#243a4d", font="Arial", fontSize=13,
            subtitleColor="#667078", subtitleFont="Arial", subtitleFontSize=10,
        ),
    ).configure_view(stroke="#c9c7c0").configure_axis(
        gridColor="#dedbd3", labelColor="#20272d", titleColor="#667078",
        labelFont="Arial", titleFont="Arial",
    )


def _scenario_assumption_value(item: Any) -> str:
    assumption_type = _controlled_value(item.assumption_type)
    if assumption_type == ReverseDcfScenarioAssumptionType.WACC.value:
        return format_report_percent(item.value)
    if assumption_type == ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE.value:
        return format_report_currency(item.value, item.currency, compact=True)
    return format_report_number(item.value, digits=4)


def _scenario_assumption_name(item: Any) -> str:
    return {
        ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE.value: "Preceding annual revenue",
        ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL.value: "Sales-to-capital",
        ReverseDcfScenarioAssumptionType.WACC.value: "WACC",
    }.get(_controlled_value(item.assumption_type), _controlled_label(item.assumption_type))


def _render_expectations(ui: Any, report: StockResearchReport) -> None:
    section = report.expectations
    _section_title(ui, "Market expectations")
    is_scenario = _controlled_value(section.display_state) == ReportExpectationState.SCENARIO_EXPECTATIONS.value
    ui.markdown(
        f'<div class="v1-expectation-head{" v1-expectation-head--scenario" if is_scenario else ""}">'
        f'<div><div class="v1-expectation-title">Reverse DCF</div>{_chip(section.display_label)}</div>'
        '<div class="v1-expectation-context">Market-implied enterprise expectations · not a valuation or price target</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    if _controlled_value(section.display_state) == ReportExpectationState.NOT_RUN.value:
        ui.markdown(
            '<div class="v1-note">Reverse DCF not run for this snapshot.</div>',
            unsafe_allow_html=True,
        )
    elif _controlled_value(section.display_state) == ReportExpectationState.NOT_READY.value:
        ui.markdown(
            '<div class="v1-withheld-panel"><div class="v1-metric-label">Implied terminal growth</div>'
            '<div class="v1-withheld-value">Withheld</div></div>',
            unsafe_allow_html=True,
        )
    for entry in section.entries:
        entry_is_scenario = _controlled_value(entry.state) == ReportExpectationState.SCENARIO_EXPECTATIONS.value
        if entry_is_scenario:
            ui.markdown(
                '<div class="v1-scenario-label">Scenario</div>'
                '<div class="v1-note"><strong>Scenario expectations</strong> · explicit read-only assumptions supplied upstream.</div>',
                unsafe_allow_html=True,
            )
        metrics = [
            ("Implied terminal growth", _text(format_report_percent(entry.implied_terminal_growth))),
            ("Final consensus revenue growth", _text(format_report_percent(entry.final_consensus_revenue_growth))),
            ("Final consensus EBIT margin", _text(format_report_percent(entry.final_consensus_ebit_margin))),
        ]
        if entry.growth_rate_difference is not None:
            metrics.append((
                "Supplied growth-rate difference",
                _text(format_report_percent(entry.growth_rate_difference, signed=True)),
            ))
        _metric_grid(ui, tuple(metrics), primary_labels=("Implied terminal growth",))
        ui.markdown(
            '<div class="v1-expectation-meta">'
            f'<span>Execution · <strong>{"Scenario" if entry_is_scenario else "Canonical"}</strong></span>'
            f'<span>Publication eligibility · <strong>{"Scenario expectations" if entry_is_scenario else "Canonical expectations"}</strong></span>'
            '</div>',
            unsafe_allow_html=True,
            )
        if entry_is_scenario:
            assumption_rows = [{
                "Assumption": _scenario_assumption_name(item),
                "Value": _scenario_assumption_value(item),
                "Currency": item.currency or "—",
                "Period": format_report_date(item.period_end),
                "Source": item.source_label,
                "Method": item.methodology_label,
            } for item in entry.scenario_assumptions]
            ui.dataframe(assumption_rows, width="stretch", hide_index=True)
    _blockers(ui, section.blocking_reasons)
    if section.source_references:
        _source_line(ui, section.source_references)


def _render_interactive_scenario_result(ui: Any, result: Any) -> None:
    """Render one application-owned scenario result without recalculation."""
    if result is None:
        return
    status = _controlled_value(result.status)
    if status == "validation_error":
        ui.markdown('<div class="v1-appendix-heading">Scenario input validation</div>', unsafe_allow_html=True)
        _blockers(ui, result.validation_errors)
        return
    if status == "unavailable":
        ui.markdown(
            '<div class="v1-note">'
            f'{escape(result.safe_message or "Scenario execution is unavailable for this snapshot.", quote=True)}'
            '</div>',
            unsafe_allow_html=True,
        )
        return
    execution = result.execution_result
    solver = execution.solver_result
    solver_status = _controlled_value(solver.status)
    ui.markdown(
        '<div class="v1-scenario-label">Scenario</div>'
        '<div class="v1-note"><strong>Scenario expectations</strong> · executed from explicit user-supplied assumptions.</div>',
        unsafe_allow_html=True,
    )
    if solver_status == ReverseDcfSolverStatus.SOLVED.value:
        _metric_grid(ui, (
            ("Market-implied terminal growth", _text(format_report_percent(solver.implied_terminal_growth))),
            ("Final consensus revenue growth", _text(format_report_percent(execution.final_consensus_revenue_growth))),
            ("Final consensus EBIT margin", _text(format_report_percent(execution.final_consensus_ebit_margin))),
        ), primary_labels=("Market-implied terminal growth",))
    else:
        ui.markdown(
            '<div class="v1-withheld-panel"><div class="v1-metric-label">Market-implied terminal growth</div>'
            '<div class="v1-withheld-value">Withheld</div></div>',
            unsafe_allow_html=True,
        )
        message = {
            ReverseDcfSolverStatus.NO_SOLUTION_IN_DOMAIN.value:
                "No terminal-growth solution was found within the approved numerical search domain.",
            ReverseDcfSolverStatus.NUMERICAL_FAILURE.value:
                "The approved numerical solver did not complete this scenario.",
            ReverseDcfSolverStatus.NOT_READY.value:
                "The explicit scenario was not ready for the approved solver.",
            ReverseDcfSolverStatus.UNAVAILABLE.value:
                "The explicit scenario result is unavailable for this snapshot.",
        }.get(solver_status, "The explicit scenario did not produce a terminal-growth result.")
        ui.markdown(f'<div class="v1-note">{escape(message, quote=True)}</div>', unsafe_allow_html=True)
    publication = (
        "Scenario only"
        if _controlled_value(execution.publication_eligibility)
        == ReverseDcfPublicationEligibility.SCENARIO_ONLY.value
        else _controlled_label(execution.publication_eligibility)
    )
    ui.markdown(
        '<div class="v1-expectation-meta">'
        '<span>Execution · <strong>Explicit scenario</strong></span>'
        f'<span>Publication · <strong>{escape(publication, quote=True)}</strong></span>'
        '<span>Central valuation eligibility · <strong>Ineligible</strong></span>'
        '</div>',
        unsafe_allow_html=True,
    )
    assumption_rows = tuple({
        "Assumption": _scenario_assumption_name(item),
        "Value": _scenario_assumption_value(item),
        "Unit": (
            "% decimal" if _controlled_value(item.assumption_type) == ReverseDcfScenarioAssumptionType.WACC.value
            else "x" if _controlled_value(item.assumption_type) == ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL.value
            else item.currency or "—"
        ),
        "Period": format_report_date(item.period_end),
        "Source": item.source_label,
        "Method": item.methodology_label,
    } for item in result.assumptions)
    ui.markdown('<div class="v1-appendix-heading">Assumptions used</div>', unsafe_allow_html=True)
    ui.dataframe(assumption_rows, width="stretch", hide_index=True)
    _blockers(ui, execution.issues)


def _render_references(ui: Any, report: StockResearchReport) -> None:
    _section_title(ui, "Reference-only evidence")
    if not report.references:
        ui.markdown(
            '<div class="v1-note">No reference-only evidence available for this snapshot.</div>',
            unsafe_allow_html=True,
        )
        return
    rows = _reference_rows(report)
    body = "".join(
        '<tr>'
        f'<td class="v1-evidence-area">{_text(row["Reference type"])}</td>'
        f'<td>{_text(row["Status"])}</td>'
        f'<td>{_text(row["Source"])}</td>'
        f'<td>{_text(row["Observation / snapshot date"])}</td>'
        f'<td><span class="v1-reference-tag">{_text(row["Eligibility"])}</span></td>'
        '</tr>'
        for row in rows
    )
    ui.markdown(
        '<div class="v1-evidence-table-wrap"><table class="v1-evidence-table">'
        '<thead><tr><th>Reference type</th><th>Status</th><th>Source</th>'
        '<th>Observation / snapshot date</th><th>Eligibility</th></tr></thead>'
        f'<tbody>{body}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    ui.markdown(
        '<div class="v1-note">External context only. Reference evidence is excluded from valuation families and overall fair value.</div>',
        unsafe_allow_html=True,
    )


def _reference_rows(report: StockResearchReport) -> tuple[dict[str, str], ...]:
    return tuple({
        "Reference type": _controlled_label(item.reference_type),
        "Status": _controlled_label(item.status),
        "Source": _source_names(item.source_references),
        "Observation / snapshot date": format_report_datetime(item.evidence_as_of),
        "Eligibility": item.reference_only_label,
    } for item in report.references)


def _source_context(source_references: tuple[object, ...]) -> tuple[str, str]:
    if not source_references:
        return "—", "—"
    names = tuple(dict.fromkeys(str(item.display_name) for item in source_references))
    dates = tuple(dict.fromkeys(
        format_report_datetime(item.observation_as_of)
        for item in source_references
        if item.observation_as_of is not None
    ))
    return ", ".join(names) or "—", " · ".join(dates) or "—"


def _readiness_evidence(report: StockResearchReport, category: object) -> tuple[str, str]:
    category_value = _controlled_value(category)
    section: object
    if category_value == ReportDataQualityCategory.MARKET_PRICE.value:
        source = report.market.source_label or "—"
        return source, format_report_datetime(report.market.observation_timestamp)
    if category_value == ReportDataQualityCategory.OWN_HISTORY_VALUATION.value:
        section = next(item for item in report.valuation_families if _controlled_value(item.family) == ValuationFamily.OWN_HISTORY.value)
    elif category_value == ReportDataQualityCategory.PEER_VALUATION.value:
        section = next(item for item in report.valuation_families if _controlled_value(item.family) == ValuationFamily.PEER.value)
    elif category_value == ReportDataQualityCategory.FORWARD_CONSENSUS.value:
        section = report.consensus
        sources, _ = _source_context(section.source_references)
        snapshots = tuple(dict.fromkeys(format_report_datetime(item.estimate_as_of) for item in section.periods))
        return sources if sources != "—" else section.source_label, " · ".join(snapshots) or "—"
    elif category_value == ReportDataQualityCategory.REVERSE_DCF.value:
        section = report.expectations
    else:
        section = report.valuation_summary
    return _source_context(section.source_references)


def _readiness_rows(report: StockResearchReport) -> tuple[dict[str, str], ...]:
    rows = []
    for item in report.data_quality.rows:
        source, context = _readiness_evidence(report, item.category)
        rows.append({
            "Evidence area": _controlled_label(item.category),
            "Status": item.status_label,
            "Primary reason / blocker": item.blocking_reasons[0] if item.blocking_reasons else "—",
            "Source": source,
            "Observation / snapshot context": context,
        })
    return tuple(rows)


def _render_data_quality(ui: Any, report: StockResearchReport) -> None:
    _section_title(ui, "Data quality and readiness")
    rows = _readiness_rows(report)
    body = "".join(
        '<tr>'
        f'<td class="v1-evidence-area">{_text(row["Evidence area"])}</td>'
        f'<td>{_chip(row["Status"])}</td>'
        f'<td>{_text(row["Primary reason / blocker"])}</td>'
        f'<td>{_text(row["Source"])}</td>'
        f'<td>{_text(row["Observation / snapshot context"])}</td>'
        '</tr>'
        for row in rows
    )
    ui.markdown(
        '<div class="v1-evidence-table-wrap"><table class="v1-evidence-table">'
        '<thead><tr><th>Evidence area</th><th>Status</th><th>Primary reason / blocker</th>'
        '<th>Source</th><th>Observation / snapshot context</th></tr></thead>'
        f'<tbody>{body}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    blocker_groups = tuple(
        (_controlled_label(item.category), item.blocking_reasons)
        for item in report.data_quality.rows if item.blocking_reasons
    )
    if blocker_groups:
        notes = "".join(
            f'<div class="v1-readiness-note"><strong>{_text(label)}:</strong> '
            f'{_text(" · ".join(reasons))}</div>'
            for label, reasons in blocker_groups
        )
        ui.markdown(f'<div class="v1-readiness-notes">{notes}</div>', unsafe_allow_html=True)
    ui.markdown(
        '<div class="v1-note">Readiness is shown component by component. No aggregate quality or confidence score is calculated; no percentage, grade, or model-confidence measure is produced.</div>',
        unsafe_allow_html=True,
    )


def _report_source_rows(report: StockResearchReport) -> tuple[dict[str, str], ...]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for source in report.source_references:
        group = grouped.setdefault(source.display_name, {"categories": [], "dates": []})
        category = _controlled_label(source.source_category)
        if category not in group["categories"]:
            group["categories"].append(category)
        if source.observation_as_of is not None:
            observed = format_report_datetime(source.observation_as_of)
            if observed not in group["dates"]:
                group["dates"].append(observed)
    return tuple({
        "Source": name,
        "Evidence areas": ", ".join(values["categories"]) or "—",
        "Observation / as-of": " · ".join(values["dates"]) or "—",
    } for name, values in grouped.items())


def _render_methodology(ui: Any, report: StockResearchReport) -> None:
    with ui.expander("Methodology & evidence", expanded=False):
        consensus_snapshots = tuple(dict.fromkeys(
            format_report_datetime(item.estimate_as_of) for item in report.consensus.periods
        ))
        ui.markdown(
            '<div class="v1-appendix-heading">Analysis snapshot</div>'
            '<div class="v1-snapshot-grid">'
            '<div class="v1-snapshot-item"><div class="v1-snapshot-label">Report analysis timestamp</div>'
            f'<div class="v1-snapshot-value">{_text(format_report_datetime(report.analysis_as_of))}</div></div>'
            '<div class="v1-snapshot-item"><div class="v1-snapshot-label">Market observation time</div>'
            f'<div class="v1-snapshot-value">{_text(format_report_datetime(report.market.observation_timestamp))}</div></div>'
            '<div class="v1-snapshot-item"><div class="v1-snapshot-label">Consensus estimate snapshot</div>'
            f'<div class="v1-snapshot-value">{_text(" · ".join(consensus_snapshots) or "—")}</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )
        ui.markdown(
            '<div class="v1-appendix-heading">Valuation methodology</div>'
            '<div class="v1-method-grid">'
            '<div class="v1-method-label">Own-history valuation</div><div>Target historical multiples: market-multiple distributions applied to aligned external forward denominators.</div>'
            '<div class="v1-method-label">Peer valuation</div><div>Comparable-company multiples: cross-sectional evidence used when sufficient eligible peers exist.</div>'
            '<div class="v1-method-label">Overall valuation</div><div>Minimum two eligible independent valuation families; approved publication rules apply.</div>'
            '<div class="v1-method-label">Reverse DCF</div><div>Market expectations only; not a valuation family. This is market-implied expectations evidence, not a central valuation estimate.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        source_rows = _report_source_rows(report)
        ui.markdown('<div class="v1-appendix-heading">Sources</div>', unsafe_allow_html=True)
        if source_rows:
            source_body = "".join(
                f'<tr><td class="v1-evidence-area">{_text(row["Source"])}</td>'
                f'<td>{_text(row["Evidence areas"])}</td><td>{_text(row["Observation / as-of"])}</td></tr>'
                for row in source_rows
            )
            ui.markdown(
                '<div class="v1-evidence-table-wrap"><table class="v1-evidence-table">'
                '<thead><tr><th>Source</th><th>Evidence areas</th><th>Observation / as-of</th></tr></thead>'
                f'<tbody>{source_body}</tbody></table></div>',
                unsafe_allow_html=True,
            )
        else:
            ui.markdown('<div class="v1-note">No safe source references supplied.</div>', unsafe_allow_html=True)
        if report.issues:
            ui.markdown('<div class="v1-appendix-heading">Issues</div>', unsafe_allow_html=True)
            _blockers(ui, tuple(
                f"{item.source_label}: {item.message}" if item.source_label else item.message
                for item in report.issues
            ))
        if report.warnings:
            ui.markdown('<div class="v1-appendix-heading">Warnings</div>', unsafe_allow_html=True)
            _blockers(ui, report.warnings)
        all_blockers = tuple(dict.fromkeys(
            reason
            for section in (
                report.market,
                report.valuation_summary,
                *report.valuation_families,
                report.consensus,
                report.expectations,
                report.data_quality,
            )
            for reason in section.blocking_reasons
        ))
        if all_blockers:
            ui.markdown('<div class="v1-appendix-heading">Blocking reasons</div>', unsafe_allow_html=True)
            _blockers(ui, all_blockers)


def render_stock_research_report(
    report: StockResearchReport,
    *,
    streamlit_module: Any | None = None,
    expectations_extension: Callable[[Any, StockResearchReport], None] | None = None,
) -> None:
    """Render one immutable report without fetching or recalculating evidence."""
    if not _is_stock_research_report(report):
        raise TypeError("report must be a StockResearchReport")
    ui = st if streamlit_module is None else streamlit_module
    ui.markdown(V1_REPORT_CSS, unsafe_allow_html=True)
    _render_header(ui, report)
    _render_summary(ui, report)
    _render_families(ui, report)
    _render_consensus(ui, report)
    _render_expectations(ui, report)
    if expectations_extension is not None:
        expectations_extension(ui, report)
    _render_references(ui, report)
    _render_data_quality(ui, report)
    _render_methodology(ui, report)


def render_v1_report_empty_state(*, streamlit_module: Any | None = None) -> None:
    """Render the isolated route before the user submits a ticker."""
    ui = st if streamlit_module is None else streamlit_module
    ui.markdown(V1_REPORT_CSS, unsafe_allow_html=True)
    ui.markdown(
        '<div class="v1-report v1-report-header">'
        '<div class="v1-kicker">Equity research snapshot</div>'
        '<div class="v1-report-title">Enter a ticker to begin</div>'
        '<div class="v1-report-meta">No analysis runs until you select Analyse. '
        'One canonical research snapshot will appear here.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
