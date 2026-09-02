from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
import os

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.analysis import build_analysis
from stock_analyser.charts import fcff_sensitivity, earnings_surprise_chart, price_chart, revenue_chart, score_chart
from stock_analyser.fundamentals import row
from stock_analyser.adaptive_valuation import explicit_fcff_dcf
from stock_analyser.yahoo_data import fetch_stock_data
from stock_analyser.official_data import fetch_official_earnings
from stock_analyser.fiscal_ai import (
    FiscalApiError, FiscalClient, latest_event_resources, load_earnings_bundle,
    qa_pairs, read_env_key, search_transcript, transcript_topic_hits, write_env_key,
)
from stock_analyser.fiscal_valuation import load_valuation_bundle
from stock_analyser.local_interpretation import (
    OllamaInterpretationError, interpretation_cache_key, interpret_management, interpret_qa_debates,
    list_ollama_models, select_management_evidence, select_material_qa,
)

st.set_page_config(page_title="Stock Analyser", page_icon=None, layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
:root {
    --bg: #f3f1ec;
    --panel: #ece9e2;
    --ink: #202624;
    --muted: #676d69;
    --line: #cbc6bb;
    --accent: #1f4e5f;
    --positive: #356247;
    --negative: #8b3a3a;
}
html, body, [class*="css"] { font-family: Arial, "Helvetica Neue", sans-serif; }
.stApp { background: var(--bg); color: var(--ink); }
.block-container { max-width: 1500px; padding-top: 1.25rem; padding-bottom: 4rem; }
header[data-testid="stHeader"] { background: var(--bg); }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
h1 { font-size: 1.55rem !important; letter-spacing: .04em; font-weight: 700 !important; margin-bottom: .15rem !important; }
h2 { font-size: 1.02rem !important; letter-spacing: .03em; text-transform: uppercase; border-bottom: 1px solid var(--line); padding-bottom: .42rem; }
h3 { font-size: .90rem !important; letter-spacing: .02em; text-transform: uppercase; }
p, li, label { font-size: .90rem; }
[data-testid="stMetric"] { background: transparent; border-top: 1px solid var(--line); padding-top: .42rem; }
[data-testid="stMetricLabel"] { color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .055em; }
[data-testid="stMetricValue"] { font-size: 1.18rem; color: var(--ink); font-variant-numeric: tabular-nums; }
.stButton > button, .stFormSubmitButton > button {
    border: 1px solid var(--accent); background: var(--accent); color: #f3f1ec;
    border-radius: 0; padding: .45rem 1rem; font-weight: 600;
}
.stButton > button:hover, .stFormSubmitButton > button:hover { border-color: #163946; background: #163946; color: #f3f1ec; }
.stTextInput input, .stNumberInput input {
    background: #f7f5f0; color: var(--ink); border: 1px solid var(--line); border-radius: 0;
}
div[data-testid="stExpander"] { border-radius: 0 !important; border-color: var(--line) !important; background: transparent; }
button[data-baseweb="tab"] { border-radius: 0 !important; font-size: .82rem; text-transform: uppercase; letter-spacing: .04em; }
button[data-baseweb="tab"][aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 0; }
.statusline { color: var(--muted); font-size: .76rem; letter-spacing: .025em; }
.decision { border-top: 2px solid var(--accent); border-bottom: 1px solid var(--line); padding: .70rem 0 .58rem 0; margin-bottom: .35rem; min-height: 74px; }
.decision .label { color: var(--muted); text-transform: uppercase; font-size: .69rem; letter-spacing: .07em; }
.decision .value { font-size: 1.10rem; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.note { border-left: 3px solid var(--line); padding: .42rem .72rem; color: var(--muted); font-size: .82rem; }
.debate { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: .75rem 0; margin: .20rem 0 .9rem 0; font-size: .91rem; line-height: 1.55; }
.section-kicker { color: var(--muted); text-transform: uppercase; font-size: .70rem; letter-spacing: .07em; margin-bottom: .25rem; }
hr { border-color: var(--line); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# The canonical research-report route is the normal 1.0 application. Only the
# explicit USE_LEGACY_UI rollback switch permits the retained legacy page.
from stock_analyser.ui import is_v1_report_ui_enabled

if is_v1_report_ui_enabled():
    from stock_analyser.ui.v1_route import render_v1_report_route

    render_v1_report_route()
    st.stop()


def fmt_money(value, currency="USD", compact=False):
    try:
        v = float(value)
        if not np.isfinite(v):
            return "N/A"
    except Exception:
        return "N/A"
    symbols = {"USD": "$", "GBP": "£", "GBp": "p", "EUR": "€", "JPY": "¥"}
    s = symbols.get(currency, f"{currency} ")
    if compact:
        av = abs(v)
        if av >= 1e12: return f"{s}{v/1e12:,.2f}tn"
        if av >= 1e9: return f"{s}{v/1e9:,.2f}bn"
        if av >= 1e6: return f"{s}{v/1e6:,.1f}m"
    return f"{s}{v:,.2f}"


def fmt_pct(value, digits=1):
    try:
        v = float(value)
        return f"{v*100:.{digits}f}%" if np.isfinite(v) else "N/A"
    except Exception:
        return "N/A"


def fmt_num(value, digits=1):
    try:
        v = float(value)
        return f"{v:.{digits}f}" if np.isfinite(v) else "N/A"
    except Exception:
        return "N/A"


def fmt_score(value):
    try:
        v = float(value)
        return f"{v:.0f} / 100" if np.isfinite(v) else "N/A"
    except Exception:
        return "N/A"


def fmt_fair(value, currency="USD"):
    try:
        v = float(value)
        if not np.isfinite(v):
            return "N/A"
    except Exception:
        return "N/A"
    symbols = {"USD": "$", "GBP": "£", "GBp": "p", "EUR": "€", "JPY": "¥"}
    symbol = symbols.get(currency, f"{currency} ")
    return f"{symbol}{v:,.0f}"


def fmt_range(low, high, currency="USD"):
    try:
        lo, hi = float(low), float(high)
        if not np.isfinite(lo) or not np.isfinite(hi):
            return "N/A"
    except Exception:
        return "N/A"
    if abs(lo - hi) < 0.5:
        return fmt_fair((lo + hi) / 2, currency)
    return f"{fmt_fair(lo, currency)} – {fmt_fair(hi, currency)}"


def status_value(label: str, value, currency: str):
    if label in {"Current price"}:
        return fmt_money(value, currency)
    if label in {"Revenue", "Free cash flow", "Cash", "Debt", "Net debt / (cash)", "Observed TTM FCFF", "Reconstructed current FCFF"}:
        return fmt_money(value, currency, compact=True)
    percentage_labels = {
        "Operating margin", "Tax rate", "Year 1 revenue growth assumption", "Year 5 revenue growth assumption",
        "Year 1 operating margin", "Year 5 operating margin", "Year 1 D&A / revenue", "Year 5 D&A / revenue",
        "Year 1 CapEx / revenue", "Year 5 CapEx / revenue", "Year 1 NWC investment / revenue",
        "Year 5 NWC investment / revenue", "Year 1 other non-cash / cash bridge", "Year 5 other non-cash / cash bridge", "WACC assumption", "Terminal growth assumption", "Terminal ROIC / RONIC",
    }
    if label in percentage_labels:
        return fmt_pct(value)
    if label == "Diluted / outstanding shares":
        try:
            v = float(value)
            if not np.isfinite(v): return "N/A"
            if abs(v) >= 1e9: return f"{v/1e9:,.2f}bn"
            if abs(v) >= 1e6: return f"{v/1e6:,.1f}m"
            return f"{v:,.0f}"
        except Exception:
            return "N/A"
    return fmt_num(value, 2)


@st.cache_data(ttl="30m", show_spinner=False)
def load_ticker(ticker: str):
    return fetch_stock_data(ticker)


@st.cache_data(ttl="60m", show_spinner=False)
def load_official_packet(ticker: str, contact_email: str):
    return fetch_official_earnings(ticker, contact_email)


@st.cache_data(ttl="6h", show_spinner=False)
def load_fiscal_packet(ticker: str, company_name: str, api_key: str):
    client = FiscalClient(api_key, timeout=20)
    return load_earnings_bundle(client, ticker, company_name)


@st.cache_data(ttl="6h", show_spinner=False)
def load_fiscal_valuation_packet(ticker: str, company_name: str, api_key: str):
    client = FiscalClient(api_key, timeout=25)
    return load_valuation_bundle(client, ticker, company_name)


def official_override_from_session(ticker: str):
    packet = st.session_state.get(f"official_packet::{ticker}")
    if packet is None:
        return None
    metrics = dict(getattr(packet, "metrics", {}) or {})
    metrics["source"] = "SEC XBRL / official filing"
    if np.isfinite(metrics.get("company_defined_fcf", np.nan)):
        metrics["source"] = "SEC XBRL + deterministic company-defined FCF from official earnings release"
    return metrics


st.title("STOCK ANALYSER")
st.caption("Legacy interface — enabled by the USE_LEGACY_UI rollback switch.")
st.markdown('<div class="statusline">Yahoo Finance market data | Independent analytical model | Decision support, not a brokerage connection</div>', unsafe_allow_html=True)

with st.form("ticker_form", border=False):
    a, b, c = st.columns([3, 1, 4])
    with a:
        ticker_input = st.text_input("Ticker", value=st.session_state.get("ticker", ""), placeholder="MSFT, SHEL.L, RR.L")
    with b:
        st.write("")
        st.write("")
        submitted = st.form_submit_button("Analyse", use_container_width=True)

if submitted and ticker_input.strip():
    st.session_state["ticker"] = ticker_input.strip().upper()

if "ticker" not in st.session_state:
    st.info("Enter a ticker and select Analyse. No provider request runs at startup.")
    st.stop()

ticker = st.session_state["ticker"]

try:
    with st.spinner(f"Loading {ticker} from Yahoo Finance..."):
        data = load_ticker(ticker)
except Exception as exc:
    st.error(f"Could not load {ticker}: {exc}")
    st.stop()

if data.history.empty and not data.info:
    st.error("Yahoo Finance returned no usable data. Check the ticker symbol or try again later.")
    if data.errors:
        with st.expander("Data diagnostics"):
            st.code("\n".join(data.errors))
    st.stop()

info = data.info
currency = data.financial_currency or data.price_currency or info.get("currency") or "USD"
company = info.get("longName") or info.get("shortName") or ticker
exchange = info.get("fullExchangeName") or info.get("exchange") or ""
sector = info.get("sector") or "Not classified"
industry = info.get("industry") or "Not classified"

official_override = official_override_from_session(ticker)

# Fiscal.ai standardized financials, ratios, shares, peers and company KPIs are the
# preferred valuation source when a local API key is available. Yahoo remains the
# market-price / consensus layer and a full fallback when Fiscal is unavailable.
fiscal_key_state = f"fiscal_key::{ticker}"
fiscal_api_key = (st.session_state.get(fiscal_key_state) or os.getenv("FISCAL_API_KEY", "") or read_env_key(ROOT) or "").strip()
fiscal_valuation_bundle = None
fiscal_valuation_error = ""
if fiscal_api_key:
    try:
        with st.spinner("Loading Fiscal.ai valuation data..."):
            fiscal_valuation_bundle = load_fiscal_valuation_packet(ticker, company, fiscal_api_key)
        st.session_state[f"fiscal_valuation_bundle::{ticker}"] = fiscal_valuation_bundle
    except Exception as exc:
        fiscal_valuation_error = str(exc)
        fiscal_valuation_bundle = st.session_state.get(f"fiscal_valuation_bundle::{ticker}")

fiscal_earnings_bundle = st.session_state.get(f"fiscal_bundle::{ticker}")
base = build_analysis(
    data, official_metrics=official_override,
    fiscal_valuation_bundle=fiscal_valuation_bundle,
    fiscal_earnings_bundle=fiscal_earnings_bundle,
)

st.markdown(f"## {company}  |  {ticker}")
st.markdown(f'<div class="statusline">{exchange} | {currency} | {sector} | {industry} | Refreshed {datetime.now().astimezone().strftime("%d %b %Y %H:%M %Z")}</div>', unsafe_allow_html=True)
if fiscal_valuation_bundle and fiscal_valuation_bundle.capabilities.get("financials"):
    st.markdown('<div class="statusline">Valuation data: Fiscal.ai standardized financials + Yahoo market/consensus data.</div>', unsafe_allow_html=True)
elif fiscal_api_key:
    st.markdown('<div class="statusline">Valuation data: Yahoo fallback; Fiscal.ai standardized financials were not available for this ticker/key.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="statusline">Valuation data: Yahoo fallback. Add a Fiscal.ai key in Earnings to enable standardized financials, ratios, peers and KPIs.</div>', unsafe_allow_html=True)
if fiscal_valuation_error:
    st.caption(f"Fiscal.ai valuation note: {fiscal_valuation_error}")

m = base["market"]
v = base["valuation"]
f = base["fundamentals"]
e = base["earnings"]

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Price", fmt_money(m.get("current_price"), currency))
c2.metric("Market cap", fmt_money(v.get("market_cap"), currency, compact=True))
c3.metric("Enterprise value", fmt_money(v.get("enterprise_value"), currency, compact=True))
c4.metric("1Y return", fmt_pct(m.get("return_1y")))
c5.metric("Forward P/E", fmt_num(v.get("forward_pe")))
c6.metric("20D traded value", fmt_money(m.get("avg_daily_value_20"), currency, compact=True))

st.markdown("<div style='height:.45rem'></div>", unsafe_allow_html=True)

with st.expander("Forecast and valuation assumptions", expanded=False):
    fw = base["forecast_framework"]
    st.markdown(f"**Framework: {fw.name}**")
    st.caption(fw.rationale)
    st.markdown('<div class="note">Defaults are sourced from management guidance when safely extractable, Fiscal.ai standardized financials/KPIs, Yahoo consensus and multi-year normalization. Editing a field marks only that assumption as a user override.</div>', unsafe_allow_html=True)
    ba = base["assumptions"]
    if ba.get("assumption_warnings"):
        st.warning("Automatic assumption QA adjusted or rejected one or more source values:\n\n" + "\n".join(f"• {x}" for x in ba["assumption_warnings"][:8]))
    if not (ba.get("validation") or {}).get("pass", True):
        st.error("Automatic forecast assumptions failed the valuation gate: " + "; ".join((ba.get("validation") or {}).get("issues", [])))
    r1 = st.columns(4)
    g1 = r1[0].number_input("Year 1 revenue growth %", value=float(ba["growth_start"] * 100), step=0.5, min_value=-30.0, max_value=60.0) / 100
    g5 = r1[1].number_input("Year 5 revenue growth %", value=float(ba["growth_end"] * 100), step=0.5, min_value=-20.0, max_value=30.0) / 100
    m1 = r1[2].number_input("Year 1 operating margin %", value=float(ba["margin_start"] * 100), step=0.5, min_value=-50.0, max_value=80.0) / 100
    m5 = r1[3].number_input("Year 5 operating margin %", value=float(ba["margin_end"] * 100), step=0.5, min_value=-50.0, max_value=80.0) / 100
    r2 = st.columns(4)
    da1 = r2[0].number_input("Year 1 D&A / revenue %", value=float(ba["da_pct_start"] * 100), step=0.25, min_value=0.0, max_value=40.0) / 100
    da5 = r2[1].number_input("Year 5 D&A / revenue %", value=float(ba["da_pct_end"] * 100), step=0.25, min_value=0.0, max_value=40.0) / 100
    cx1 = r2[2].number_input("Year 1 CapEx / revenue %", value=float(ba["capex_pct_start"] * 100), step=0.5, min_value=0.0, max_value=80.0) / 100
    cx5 = r2[3].number_input("Year 5 CapEx / revenue %", value=float(ba["capex_pct_end"] * 100), step=0.5, min_value=0.0, max_value=80.0) / 100
    r3 = st.columns(4)
    nwc1 = r3[0].number_input("Year 1 NWC investment / revenue %", value=float(ba["nwc_pct_start"] * 100), step=0.25, min_value=-20.0, max_value=20.0) / 100
    nwc5 = r3[1].number_input("Year 5 NWC investment / revenue %", value=float(ba["nwc_pct_end"] * 100), step=0.25, min_value=-20.0, max_value=20.0) / 100
    onc1 = r3[2].number_input("Year 1 other non-cash / cash bridge %", value=float(ba.get("other_noncash_pct_start", 0.0) * 100), step=0.25, min_value=-30.0, max_value=40.0) / 100
    onc5 = r3[3].number_input("Year 5 other non-cash / cash bridge %", value=float(ba.get("other_noncash_pct_end", 0.0) * 100), step=0.25, min_value=-30.0, max_value=40.0) / 100
    r4 = st.columns(4)
    wacc_val = r4[0].number_input("WACC %", value=float(ba["wacc"] * 100), step=0.25, min_value=3.0, max_value=25.0) / 100
    terminal_val = r4[1].number_input("Terminal growth %", value=float(ba["terminal_growth"] * 100), step=0.25, min_value=-2.0, max_value=6.0) / 100
    terminal_roic_val = r4[2].number_input("Terminal ROIC / RONIC %", value=float(ba.get("terminal_roic", 0.12) * 100), step=0.5, min_value=5.0, max_value=40.0) / 100
    r4[3].caption("Terminal reinvestment is derived as terminal growth ÷ terminal ROIC; Year-5 CapEx is not carried into perpetuity.")

# Preserve the model's provenance unless the displayed control was actually changed.
def _override_if_changed(overrides, key, value, default, tol=1e-10):
    if np.isfinite(float(value)) and np.isfinite(float(default)) and abs(float(value) - float(default)) > tol:
        overrides[key] = float(value)

overrides = {}
for key, val, default in [
    ("growth_start", g1, ba["growth_start"]), ("growth_end", g5, ba["growth_end"]),
    ("margin_start", m1, ba["margin_start"]), ("margin_end", m5, ba["margin_end"]),
    ("da_pct_start", da1, ba["da_pct_start"]), ("da_pct_end", da5, ba["da_pct_end"]),
    ("capex_pct_start", cx1, ba["capex_pct_start"]), ("capex_pct_end", cx5, ba["capex_pct_end"]),
    ("nwc_pct_start", nwc1, ba["nwc_pct_start"]), ("nwc_pct_end", nwc5, ba["nwc_pct_end"]),
    ("other_noncash_pct_start", onc1, ba.get("other_noncash_pct_start", 0.0)), ("other_noncash_pct_end", onc5, ba.get("other_noncash_pct_end", 0.0)),
    ("wacc", wacc_val, ba["wacc"]), ("terminal_growth", terminal_val, ba["terminal_growth"]), ("terminal_roic", terminal_roic_val, ba.get("terminal_roic", 0.12)),
]:
    _override_if_changed(overrides, key, val, default)

analysis = build_analysis(
    data, overrides or None, official_metrics=official_override,
    fiscal_valuation_bundle=fiscal_valuation_bundle, fiscal_earnings_bundle=fiscal_earnings_bundle,
)

scores = analysis["scores"]
dcf = analysis["dcf"]
fair = dcf.get("fair_value", np.nan)
upside = analysis["dcf_upside"]
f, e, v, m = analysis["fundamentals"], analysis["earnings"], analysis["valuation"], analysis["market"]

valuation_range_result = analysis.get("valuation_range", {})
central_value = valuation_range_result.get("central", np.nan)
central_upside = analysis.get("central_upside", np.nan)

x1, x2, x3, x4, x5, x6 = st.columns(6)
for col, label, value in [
    (x1, "Investment stance", analysis["investment_stance"]),
    (x2, "Valuation", analysis["valuation_view"]),
    (x3, "Central valuation", fmt_fair(central_value, currency)),
    (x4, "Central upside / downside", fmt_pct(central_upside)),
    (x5, "Fundamental score", fmt_score(analysis["overall_score"])),
    (x6, "Model confidence", analysis["model_confidence"]),
]:
    with col:
        st.markdown(f'<div class="decision"><div class="label">{label}</div><div class="value">{value}</div></div>', unsafe_allow_html=True)

st.markdown(
    f'<div class="statusline">Fundamental score is normalised across {analysis["score_coverage"]:.0%} of weighted data available. '
    f'Investment stance is separate and combines fundamental quality, earnings evidence, the triangulated valuation range and model confidence. '
    f'DCF base case: {fmt_fair(fair, currency)}.</div>',
    unsafe_allow_html=True,
)

if not analysis["dcf_available"]:
    st.warning("DCF blocked: " + "; ".join(analysis["valuation_blockers"]) + ". Open the Valuation tab for the source-by-source status table.")

tabs = st.tabs(["Overview", "Financials", "Valuation", "Earnings", "Market", "Methodology"], key="main_tabs", on_change="rerun")

with tabs[0]:
    st.subheader("Research snapshot")
    scenarios = analysis.get("scenarios", {})
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Current price", fmt_money(m.get("current_price"), currency))
    s2.metric("Bear value", fmt_fair(valuation_range_result.get("bear"), currency))
    s3.metric("Central valuation range" if valuation_range_result.get("resolved") else "Valuation method span", fmt_range(valuation_range_result.get("central_low"), valuation_range_result.get("central_high"), currency))
    s4.metric("Bull value", fmt_fair(valuation_range_result.get("bull"), currency))
    if valuation_range_result.get("resolved"):
        st.caption(f"Explicit FCFF DCF base case: {fmt_fair(fair, currency)}. Central valuation triangulates DCF, own-history multiples and peer multiples when available.")
    else:
        st.caption(f"Explicit FCFF DCF base case: {fmt_fair(fair, currency)}. Central valuation is withheld: {valuation_range_result.get('reason', 'independent methods are not sufficiently consistent.')}")

    st.markdown('<div class="section-kicker">Key debate</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="debate">{analysis["key_debate"]}</div>', unsafe_allow_html=True)
    if analysis.get("recent_quarter_flags"):
        st.markdown('<div class="section-kicker">Recent-quarter check</div>', unsafe_allow_html=True)
        for flag in analysis["recent_quarter_flags"]:
            st.warning(flag)
    missing_quarterly = [name for name, status in analysis.get("quarterly_data_status", {}).items() if status != "Available"]
    if missing_quarterly:
        st.info("Latest-quarter analytics are incomplete because Yahoo did not return: " + ", ".join(missing_quarterly) + ". The Financials tab shows the quarterly source status and endpoint diagnostics.")

    left, right = st.columns([1.05, 1])
    with left:
        st.markdown("### Market-implied expectations")
        expectations = pd.DataFrame([
            ["Market-implied Year 1 revenue growth", fmt_pct(analysis["implied_growth"])],
            ["Model Year 1 revenue growth", fmt_pct(analysis["assumptions"]["growth_start"])],
            ["Model Year 5 revenue growth", fmt_pct(analysis["assumptions"]["growth_end"])],
            ["Model Year 1 operating margin", fmt_pct(analysis["assumptions"]["margin_start"])],
            ["Model Year 5 operating margin", fmt_pct(analysis["assumptions"]["margin_end"])],
            ["Model Year 1 CapEx / revenue", fmt_pct(analysis["assumptions"]["capex_pct_start"])],
            ["Model Year 5 CapEx / revenue", fmt_pct(analysis["assumptions"]["capex_pct_end"])],
            ["Calculated WACC", fmt_pct(analysis["assumptions"]["wacc"])],
            ["Revenue CAGR, 3Y", fmt_pct(f.get("revenue_cagr_3y"))],
            ["Latest-quarter revenue growth YoY", fmt_pct(f.get("latest_quarter_revenue_yoy"))],
            ["30D EPS estimate revision", fmt_pct(e.get("eps_revision_30d"))],
            ["ROIC", fmt_pct(f.get("roic"))],
        ], columns=["Measure", "Value"])
        st.dataframe(expectations, hide_index=True, width="stretch", lazy=True)
        st.markdown('<div class="note">Reverse DCF uses the same explicit FCFF model and holds the Year-5 growth, margin, D&A, CapEx, working-capital, WACC and terminal-growth assumptions constant while solving for the Year-1 revenue growth rate implied by the current share price.</div>', unsafe_allow_html=True)
    with right:
        score_rows = []
        for name, score in scores.items():
            score_rows.append([name, fmt_score(score), fmt_pct(analysis["component_coverage"].get(name))])
        st.markdown("### Score evidence")
        st.dataframe(pd.DataFrame(score_rows, columns=["Component", "Score", "Data coverage"]), hide_index=True, width="stretch", lazy=True)

    st.subheader("Adaptive forecast framework")
    fw = analysis["forecast_framework"]
    st.markdown(f"**{fw.name}** — {fw.rationale}")
    driver_rows = analysis["assumptions"].get("kpi_drivers") or []
    if driver_rows:
        driver_table = pd.DataFrame([
            [x.get("name"), x.get("kind"), fmt_num(x.get("latest"), 2), fmt_pct(x.get("growth")), x.get("observations")]
            for x in driver_rows
        ], columns=["Fiscal KPI / segment driver", "Model role", "Latest", "Latest growth", "Observations"])
        st.dataframe(driver_table, hide_index=True, width="stretch", lazy=True)
    else:
        st.caption("No high-confidence company-specific KPI driver series were available; the model uses the sector-aware explicit FCFF framework and standardized financials.")
    guidance = analysis.get("guidance") or {}
    guidance_rows = []
    for label, key in [("Revenue guidance midpoint", "revenue_mid"), ("CapEx guidance midpoint", "capex_mid"), ("Margin guidance midpoint", "margin_mid")]:
        if np.isfinite(float(guidance.get(key, np.nan))):
            val = guidance[key]
            guidance_rows.append([label, fmt_pct(val) if "margin" in key else fmt_money(val, currency, compact=True), "Fiscal.ai transcript / deterministic extraction"])
    if guidance_rows:
        st.markdown("### Management guidance anchors")
        st.dataframe(pd.DataFrame(guidance_rows, columns=["Guidance", "Value", "Source"]), hide_index=True, width="stretch", lazy=True)

    chart_left, chart_right = st.columns([1.05, 1])
    with chart_left:
        st.plotly_chart(score_chart(scores), width="stretch", theme=None, config={"displayModeBar": False})
    with chart_right:
        if not data.history.empty:
            st.plotly_chart(price_chart(data.history.tail(756), currency), width="stretch", theme=None, config={"displayModeBar": False})

    st.subheader("Investment evidence")
    evidence = pd.DataFrame([
        ["Revenue growth, 3Y", fmt_pct(f.get("revenue_cagr_3y")), "Historical growth"],
        ["Latest-quarter revenue growth", fmt_pct(f.get("latest_quarter_revenue_yoy")), "Current operating trajectory"],
        ["Latest-quarter operating margin", fmt_pct(f.get("latest_quarter_operating_margin")), "Current profitability"],
        ["Latest-quarter FCF margin", fmt_pct(f.get("latest_quarter_fcf_margin")), "Current cash-generation run rate"],
        ["ROIC", fmt_pct(f.get("roic")), "Business quality"],
        ["Operating margin", fmt_pct(f.get("operating_margin")), "Profitability"],
        ["FCF margin", fmt_pct(f.get("fcf_margin")), "Cash generation"],
        ["EPS revision, 30D", fmt_pct(e.get("eps_revision_30d")), "Estimate momentum"],
        ["Average EPS surprise, 8Q", fmt_pct(e.get("avg_eps_surprise_8q")), "Earnings delivery"],
        ["Relative volume", f'{fmt_num(m.get("relative_volume"), 2)}x', "Current trading activity"],
        ["Annualized volatility", fmt_pct(m.get("annualized_volatility")), "Market risk"],
    ], columns=["Metric", "Value", "Interpretation"])
    st.dataframe(evidence, hide_index=True, width="stretch", lazy=True)

with tabs[1]:
    st.subheader("Fundamental profile")
    q_status = pd.DataFrame(
        [[name, status] for name, status in analysis.get("quarterly_data_status", {}).items()],
        columns=["Quarterly source", "Status"],
    )
    if not q_status.empty:
        st.dataframe(q_status, hide_index=True, width="stretch", lazy=True)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Revenue", fmt_money(f.get("revenue"), currency, compact=True))
    k2.metric("Operating margin", fmt_pct(f.get("operating_margin")))
    k3.metric("ROIC", fmt_pct(f.get("roic")))
    k4.metric("FCF margin", fmt_pct(f.get("fcf_margin")))
    k5.metric("Net debt", fmt_money(f.get("net_debt"), currency, compact=True))

    fig = revenue_chart(data.income, row)
    if fig:
        st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})

    metrics = pd.DataFrame([
        ["Gross margin", fmt_pct(f.get("gross_margin"))],
        ["Operating margin, TTM", fmt_pct(f.get("operating_margin"))],
        ["Operating margin, latest quarter", fmt_pct(f.get("latest_quarter_operating_margin"))],
        ["Revenue growth, latest quarter YoY", fmt_pct(f.get("latest_quarter_revenue_yoy"))],
        ["Free cash flow, latest quarter", fmt_money(f.get("latest_quarter_fcf"), currency, compact=True)],
        ["Net margin", fmt_pct(f.get("net_margin"))],
        ["FCF margin", fmt_pct(f.get("fcf_margin"))],
        ["ROA", fmt_pct(f.get("roa"))],
        ["ROE", fmt_pct(f.get("roe"))],
        ["ROIC", fmt_pct(f.get("roic"))],
        ["Current ratio", fmt_num(f.get("current_ratio"), 2)],
        ["Quick ratio", fmt_num(f.get("quick_ratio"), 2)],
        ["Debt / equity", fmt_num(f.get("debt_to_equity"), 2)],
        ["CFO / net income", fmt_num(f.get("cfo_to_net_income"), 2)],
        ["Revenue CAGR, 3Y", fmt_pct(f.get("revenue_cagr_3y"))],
        ["Net income CAGR, 3Y", fmt_pct(f.get("net_income_cagr_3y"))],
    ], columns=["Metric", "Value"])
    st.dataframe(metrics, hide_index=True, width="stretch", lazy=True)

    if not data.ttm_income.empty:
        with st.expander("Reported trailing-twelve-month income statement"):
            st.dataframe(data.ttm_income, width="stretch", lazy=True)
    with st.expander("Reported annual income statement"):
        st.dataframe(data.income, width="stretch", lazy=True)
    if not data.quarterly_balance.empty:
        with st.expander("Reported quarterly balance sheet"):
            st.dataframe(data.quarterly_balance, width="stretch", lazy=True)
    with st.expander("Reported annual balance sheet"):
        st.dataframe(data.balance, width="stretch", lazy=True)
    if not data.ttm_cashflow.empty:
        with st.expander("Reported trailing-twelve-month cash-flow statement"):
            st.dataframe(data.ttm_cashflow, width="stretch", lazy=True)
    with st.expander("Reported annual cash-flow statement"):
        st.dataframe(data.cashflow, width="stretch", lazy=True)

with tabs[2]:
    st.subheader("Valuation data status")
    status_display = []
    for r in analysis["valuation_status"]:
        status_display.append([
            r["Input"], status_value(r["Input"], r["value"], currency), r["Status"], r["Source"], r["Note"]
        ])
    st.dataframe(pd.DataFrame(status_display, columns=["Input", "Value", "Status", "Source", "Note"]), hide_index=True, width="stretch", lazy=True)
    reconciliation_notes = [r["Note"] for r in analysis["valuation_status"] if r.get("Note") and ("differs by" in r["Note"] or "difference of" in r["Note"])]
    if reconciliation_notes:
        st.warning("Valuation reconciliation flag: " + " ".join(reconciliation_notes))
    if analysis["dcf_available"]:
        st.success("Explicit FCFF DCF input gate: PASS. Revenue, margins, reinvestment, cost of capital, capital structure and share-count inputs are available or transparently derived.")
    else:
        st.error("DCF input gate: BLOCKED. Missing / unsupported: " + "; ".join(analysis["valuation_blockers"]))

    bridge = analysis.get("fcff_bridge") or {}
    if bridge.get("available"):
        message = (
            f"Current cash bridge {'PASS' if bridge.get('pass') else 'FAIL'}: observed TTM FCFF {fmt_money(bridge.get('observed_fcff'), currency, compact=True)} "
            f"vs reconstructed {fmt_money(bridge.get('reconstructed_fcff'), currency, compact=True)} "
            f"({fmt_pct(bridge.get('relative_gap'))} absolute gap versus observed FCFF)."
        )
        if bridge.get("pass"):
            st.info(message + " This prevents the explicit forecast from starting from a cash-flow bridge that contradicts the current business economics.")
        else:
            st.error(message)
    else:
        st.caption("Current FCFF bridge reconciliation is unavailable because one or more current cash-flow drivers use analytical fallbacks. This reduces model confidence but does not automatically block a generic fallback DCF.")
    if np.isfinite(float(f.get("sbc_pct_revenue", np.nan))) and float(f["sbc_pct_revenue"]) >= 0.03:
        st.warning(f"Stock-based compensation is {float(f['sbc_pct_revenue']):.1%} of TTM revenue. The DCF treats SBC as an economic expense rather than adding it back to FCFF; future share dilution/buyback offsets are not yet modeled explicitly, so model confidence is capped accordingly.")
    fvcheck = analysis.get("forecast_validation") or {}
    if not fvcheck.get("pass", True):
        st.error("Forecast assumption validation failed; DCF is withheld. " + "; ".join(fvcheck.get("issues", [])))
    elif analysis["assumptions"].get("assumption_warnings"):
        st.warning("Forecast assumption QA rejected or replaced questionable source values. Open the assumptions panel to review the provenance warnings.")

    st.subheader("Valuation triangulation")
    vr = analysis["valuation_range"]
    z1, z2, z3, z4, z5 = st.columns(5)
    z1.metric("Current price", fmt_money(m.get("current_price"), currency))
    z2.metric("Central valuation", fmt_fair(vr.get("central"), currency))
    z3.metric("Central range" if vr.get("resolved") else "Method span", fmt_range(vr.get("central_low"), vr.get("central_high"), currency))
    z4.metric("Explicit FCFF DCF", fmt_fair(fair, currency))
    z5.metric("Central upside / downside", fmt_pct(analysis.get("central_upside")))

    method_rows = []
    for name, value in vr.get("methods", []):
        method_rows.append([name, fmt_fair(value, currency), fmt_pct(value / m.get("current_price") - 1) if np.isfinite(float(m.get("current_price", np.nan))) and float(m.get("current_price")) else "N/A"])
    if method_rows:
        st.dataframe(pd.DataFrame(method_rows, columns=["Valuation method", "Implied value / share", "Upside / downside"]), hide_index=True, width="stretch", lazy=True)
    if not vr.get("resolved", False):
        st.warning("Central valuation withheld. " + str(vr.get("reason") or "Independent valuation methods are not sufficiently consistent."))
    elif np.isfinite(float(vr.get("dispersion", np.nan))) and float(vr["dispersion"]) > 0.50:
        st.warning(f"Valuation methods still have meaningful dispersion ({float(vr['dispersion']):.0%} max-to-min spread versus the median). Treat the central value as a research range, not a price target.")
    st.markdown('<div class="note">A central valuation is shown only when at least two independent methods are available and their dispersion passes the consistency gate. Otherwise each method remains visible but the headline valuation is withheld. Bear/Base/Bull DCF and method spans are analytical bounds, not price forecasts.</div>', unsafe_allow_html=True)

    if analysis["dcf_available"] and analysis.get("scenarios"):
        st.markdown("### Bear / Base / Bull operating cases")
        scenario_rows = []
        current = m.get("current_price", np.nan)
        scenarios = analysis.get("scenarios", {})
        for name in ["Bear", "Base", "Bull"]:
            c = scenarios.get(name, {})
            fv = c.get("fair_value", np.nan)
            scen_upside = fv / current - 1 if np.isfinite(float(fv)) and np.isfinite(float(current)) and float(current) else np.nan
            scenario_rows.append([
                name, fmt_pct(c.get("growth")), fmt_pct(c.get("end_growth")), fmt_pct(c.get("margin_start")),
                fmt_pct(c.get("margin_end")), fmt_pct(c.get("capex_pct_end")), fmt_pct(c.get("wacc")),
                fmt_pct(c.get("terminal_growth")), fmt_pct(c.get("terminal_roic")), fmt_fair(fv, currency), fmt_pct(scen_upside),
            ])
        st.dataframe(pd.DataFrame(scenario_rows, columns=[
            "Case", "Year 1 growth", "Year 5 growth", "Year 1 margin", "Year 5 margin",
            "Year 5 CapEx / revenue", "WACC", "Terminal growth", "Terminal ROIC", "Fair value", "Upside / downside"
        ]), hide_index=True, width="stretch", lazy=True)
        st.markdown('<div class="note">Operating cases vary growth, margins and normalized capital intensity while holding WACC and terminal growth fixed. Cost-of-capital and terminal-value uncertainty are isolated in the sensitivity matrix.</div>', unsafe_allow_html=True)

        aa = analysis["assumptions"]
        dcf_kwargs = {
            "revenue": float(aa["revenue"]),
            "growth_start": float(aa["growth_start"]), "growth_end": float(aa["growth_end"]),
            "margin_start": float(aa["margin_start"]), "margin_end": float(aa["margin_end"]),
            "tax_rate": float(aa["tax_rate"]),
            "da_pct_start": float(aa["da_pct_start"]), "da_pct_end": float(aa["da_pct_end"]),
            "capex_pct_start": float(aa["capex_pct_start"]), "capex_pct_end": float(aa["capex_pct_end"]),
            "nwc_pct_start": float(aa["nwc_pct_start"]), "nwc_pct_end": float(aa["nwc_pct_end"]),
            "other_noncash_pct_start": float(aa.get("other_noncash_pct_start", 0.0)), "other_noncash_pct_end": float(aa.get("other_noncash_pct_end", 0.0)),
            "terminal_roic": float(aa.get("terminal_roic", 0.12)),
            "wacc": float(aa["wacc"]), "terminal_growth": float(aa["terminal_growth"]),
            "net_debt": float(analysis["net_debt_for_dcf"]), "shares": float(analysis["shares"]),
        }
        sens = fcff_sensitivity(
            dcf_kwargs=dcf_kwargs, wacc_center=float(aa["wacc"]), terminal_center=float(aa["terminal_growth"]),
            dcf_func=explicit_fcff_dcf,
        )
        st.plotly_chart(sens, width="stretch", theme=None, config={"displayModeBar": False})

        if isinstance(dcf.get("forecast"), pd.DataFrame) and not dcf["forecast"].empty:
            with st.expander("Explicit FCFF forecast schedule", expanded=False):
                forecast = dcf["forecast"].copy()
                for col in ["growth", "operating_margin", "da_pct_revenue", "other_noncash_pct_revenue", "capex_pct_revenue", "nwc_pct_revenue"]:
                    if col in forecast.columns:
                        forecast[col] = forecast[col].map(fmt_pct)
                for col in ["revenue", "ebit", "nopat", "d&a", "other_noncash", "capex", "nwc_investment", "fcff", "pv_fcff"]:
                    if col in forecast.columns:
                        forecast[col] = forecast[col].map(lambda x: fmt_money(x, currency, compact=True))
                st.dataframe(forecast, hide_index=True, width="stretch", lazy=True)

    if analysis["dcf_available"] and not analysis.get("scenarios"):
        st.error("Bear/Base/Bull scenarios were withheld because the scenario ordering failed the valuation sanity check.")

    st.subheader("Company-specific WACC")
    wd = analysis["assumptions"].get("wacc_detail") or {}
    wacc_rows = [
        ["Risk-free rate", fmt_pct(wd.get("risk_free_rate")), wd.get("risk_free_source", "")],
        ["Beta", fmt_num(wd.get("beta"), 2), "Yahoo company statistics / fallback"],
        ["Equity risk premium", fmt_pct(wd.get("equity_risk_premium")), "Framework ERP assumption"],
        ["Cost of equity", fmt_pct(wd.get("cost_of_equity")), "Risk-free + beta × ERP"],
        ["Pre-tax cost of debt", fmt_pct(wd.get("pretax_cost_of_debt")), wd.get("cost_of_debt_source", "")],
        ["After-tax cost of debt", fmt_pct(wd.get("after_tax_cost_of_debt")), "Pre-tax cost × (1 − tax rate)"],
        ["Equity weight", fmt_pct(wd.get("equity_weight")), wd.get("capital_weight_source", "Market-value capital structure")],
        ["Debt weight", fmt_pct(wd.get("debt_weight")), wd.get("capital_weight_source", "Market-value capital structure")],
        ["Raw calculated WACC", fmt_pct(wd.get("raw_wacc")), "Before fail-safe floor"],
        ["WACC floor", fmt_pct(wd.get("wacc_floor")), "Risk-free rate + 0.5% (minimum 5.5%)"],
        ["Calculated WACC", fmt_pct(analysis["assumptions"].get("wacc")), analysis["assumptions"].get("sources", {}).get("wacc", "")],
    ]
    st.dataframe(pd.DataFrame(wacc_rows, columns=["Input", "Value", "Source / method"]), hide_index=True, width="stretch", lazy=True)
    if currency != "USD":
        st.warning("The automatic risk-free rate currently uses the US 10-year Treasury (^TNX) as a default market proxy. For non-USD cash flows, review or override WACC so the discount rate is currency-consistent.")

    st.subheader("Terminal economics")
    terminal_rows = [
        ["Terminal growth", fmt_pct(analysis["assumptions"].get("terminal_growth")), analysis["assumptions"].get("sources", {}).get("terminal_growth", "")],
        ["Terminal ROIC / RONIC", fmt_pct(analysis["assumptions"].get("terminal_roic")), analysis["assumptions"].get("sources", {}).get("terminal_roic", "")],
        ["Steady-state reinvestment rate", fmt_pct(dcf.get("terminal_reinvestment_rate")), "Terminal growth ÷ terminal ROIC"],
        ["Terminal FCFF", fmt_money(dcf.get("terminal_fcff"), currency, compact=True), "Terminal NOPAT × (1 − reinvestment rate)"],
    ]
    st.dataframe(pd.DataFrame(terminal_rows, columns=["Input", "Value", "Source / method"]), hide_index=True, width="stretch", lazy=True)
    st.markdown('<div class="note">Year-5 CapEx remains an explicit forecast driver, but it is not assumed to persist unchanged forever. The terminal stage uses steady-state reinvestment consistent with long-run growth and returns on new invested capital.</div>', unsafe_allow_html=True)

    st.subheader("Own-history multiple valuation")
    hist = analysis.get("historical_valuation", {})
    hist_methods = hist.get("methods") or []
    if hist_methods:
        st.dataframe(pd.DataFrame([
            [x.get("method"), fmt_num(x.get("multiple"), 1), fmt_fair(x.get("fair_value"), currency), x.get("source", "")]
            for x in hist_methods
        ], columns=["Method", "Historical median multiple", "Implied value / share", "Source"]), hide_index=True, width="stretch", lazy=True)
        ctx = hist.get("ratio_context") or {}
        ctx_rows = []
        labels = {
            "ratio_price_to_earnings": "P/E", "ratio_ev_to_ebitda": "EV/EBITDA", "ratio_ev_to_ebit": "EV/EBIT",
            "ratio_price_to_sales": "P/S", "ratio_fcf_yield": "FCF yield", "ratio_return_on_invested_capital": "ROIC",
        }
        for rid, label in labels.items():
            current_v = ctx.get(f"current_{rid}", np.nan); median_v = ctx.get(f"median_{rid}", np.nan); percentile_v = ctx.get(f"percentile_{rid}", np.nan)
            if np.isfinite(float(current_v)) or np.isfinite(float(median_v)):
                ctx_rows.append([label, fmt_pct(current_v) if rid in {"ratio_fcf_yield", "ratio_return_on_invested_capital"} else fmt_num(current_v, 1), fmt_pct(median_v) if rid in {"ratio_fcf_yield", "ratio_return_on_invested_capital"} else fmt_num(median_v, 1), fmt_pct(percentile_v)])
        if ctx_rows:
            st.dataframe(pd.DataFrame(ctx_rows, columns=["Ratio", "Current", "Historical median", "Historical percentile"]), hide_index=True, width="stretch", lazy=True)
    else:
        st.caption("No sufficiently complete own-history multiple valuation was available for this ticker.")

    st.subheader("Peer valuation")
    peer = analysis.get("peer_valuation", {})
    peer_table = peer.get("peer_table")
    if isinstance(peer_table, pd.DataFrame) and not peer_table.empty:
        display_peer = peer_table.copy()
        for col in ["P/E", "EV/EBITDA", "EV/EBIT", "P/S"]:
            if col in display_peer.columns:
                display_peer[col] = display_peer[col].map(lambda x: fmt_num(x, 1))
        if "FCF yield" in display_peer.columns:
            display_peer["FCF yield"] = display_peer["FCF yield"].map(fmt_pct)
        st.dataframe(display_peer, hide_index=True, width="stretch", lazy=True)
        peer_methods = peer.get("methods") or []
        if peer_methods:
            st.dataframe(pd.DataFrame([
                [x.get("method"), fmt_num(x.get("multiple"), 1), fmt_fair(x.get("fair_value"), currency)] for x in peer_methods
            ], columns=["Peer method", "Peer median", "Implied value / share"]), hide_index=True, width="stretch", lazy=True)
    else:
        st.caption("A peer valuation was not available. Fiscal.ai peer coverage or comparable ratio history may be insufficient for this ticker/key.")

    st.subheader("Current market multiples")
    val_table = pd.DataFrame([
        ["Trailing P/E", fmt_num(v.get("trailing_pe"), 1)],
        ["Forward P/E", fmt_num(v.get("forward_pe"), 1)],
        ["Forward P/E source", v.get("forward_pe_source", "Yahoo company statistics")],
        ["Price / book", fmt_num(v.get("price_to_book"), 1)],
        ["Price / sales", fmt_num(v.get("price_to_sales"), 1)],
        ["EV / EBITDA", fmt_num(v.get("ev_to_ebitda"), 1)],
        ["EV / revenue", fmt_num(v.get("ev_to_revenue"), 1)],
        ["PEG", fmt_num(v.get("peg"), 2)],
        ["FCF yield", fmt_pct(v.get("fcf_yield"))],
    ], columns=["Valuation measure", "Value"])
    st.dataframe(val_table, hide_index=True, width="stretch", lazy=True)

    if dcf:
        st.markdown(f'<div class="note">Terminal value represents {fmt_pct(dcf.get("pv_terminal_share"))} of modeled enterprise value. A high share means the DCF remains sensitive to cost of capital, terminal growth and terminal ROIC even though Year-5 CapEx is no longer carried into perpetuity.</div>', unsafe_allow_html=True)

with tabs[3]:
    st.subheader("Earnings intelligence")
    st.markdown(
        '<div class="note">Fiscal.ai is the structured earnings/transcript layer. Yahoo remains the live market and consensus layer. '
        'For US issuers, the SEC section below remains available as an independent primary-source cross-check. Optional Ollama interpretation is deliberately small: no embeddings, no RAG and no AI-generated financial numbers.</div>',
        unsafe_allow_html=True,
    )

    fiscal_key_state = f"fiscal_key::{ticker}"
    if fiscal_key_state not in st.session_state:
        st.session_state[fiscal_key_state] = os.getenv("FISCAL_API_KEY", "") or read_env_key(ROOT)

    fiscal_bundle = st.session_state.get(f"fiscal_bundle::{ticker}")
    if fiscal_bundle is None:
        st.markdown('<div class="statusline">Fiscal.ai status: waiting for an API request.</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="statusline">Fiscal.ai status: loaded {fiscal_bundle.company.company_key} | '
            f'transcript: {"available" if fiscal_bundle.capabilities.get("transcript") else "not available"}.</div>',
            unsafe_allow_html=True,
        )

    with st.form(key=f"fiscal_form::{ticker}", clear_on_submit=False, border=False):
        fiscal_key_input = st.text_input(
            "Fiscal.ai API key",
            type="password",
            key=fiscal_key_state,
            help="Sent only to api.fiscal.ai using the X-Api-Key header. Tick 'Save locally' to write it to .env; .env is excluded from Git.",
        )
        f1, f2, _ = st.columns([1.3, 1.15, 4])
        with f1:
            save_fiscal_key = st.checkbox("Save locally", value=bool(read_env_key(ROOT)), key=f"save_fiscal::{ticker}")
        with f2:
            load_fiscal = st.form_submit_button("Load Fiscal.ai earnings", use_container_width=True)

    fc1, fc2, _ = st.columns([1.15, 1.15, 4])
    with fc1:
        clear_fiscal = st.button("Clear Fiscal cache", key=f"clear_fiscal::{ticker}", use_container_width=True)
    with fc2:
        forget_fiscal = st.button("Forget saved key", key=f"forget_fiscal::{ticker}", use_container_width=True)

    if clear_fiscal:
        st.session_state.pop(f"fiscal_bundle::{ticker}", None)
        st.session_state.pop(f"fiscal_valuation_bundle::{ticker}", None)
        st.session_state.pop(f"management_interpretation::{ticker}", None)
        st.session_state.pop(f"qa_interpretation::{ticker}", None)
        load_fiscal_packet.clear()
        load_fiscal_valuation_packet.clear()
        st.rerun()

    if forget_fiscal:
        write_env_key(ROOT, "")
        st.session_state[fiscal_key_state] = ""
        st.session_state.pop(f"fiscal_bundle::{ticker}", None)
        st.session_state.pop(f"fiscal_valuation_bundle::{ticker}", None)
        st.session_state.pop(f"management_interpretation::{ticker}", None)
        st.session_state.pop(f"qa_interpretation::{ticker}", None)
        load_fiscal_packet.clear()
        load_fiscal_valuation_packet.clear()
        st.rerun()

    if load_fiscal:
        api_key = (fiscal_key_input or "").strip()
        if not api_key:
            st.error("Enter your Fiscal.ai API key first.")
        else:
            if save_fiscal_key:
                try:
                    write_env_key(ROOT, api_key)
                except OSError as exc:
                    st.warning(f"The API key works for this session, but could not be saved to .env: {exc}")
            try:
                with st.spinner("Loading Fiscal.ai company, earnings-event and transcript data..."):
                    fiscal_bundle = load_fiscal_packet(ticker, company, api_key)
                st.session_state[f"fiscal_bundle::{ticker}"] = fiscal_bundle
                st.session_state.pop(f"management_interpretation::{ticker}", None)
                st.session_state.pop(f"qa_interpretation::{ticker}", None)
                st.rerun()
            except FiscalApiError as exc:
                if exc.status_code == 403:
                    st.error(f"Fiscal.ai access denied: {exc}")
                else:
                    st.error(str(exc))
            except Exception as exc:
                st.error(f"Fiscal.ai retrieval failed: {exc}")

    fiscal_bundle = st.session_state.get(f"fiscal_bundle::{ticker}")
    if fiscal_bundle is not None:
        fiscal_company = fiscal_bundle.company
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fiscal company key", fiscal_company.company_key or "N/A")
        c2.metric("Reporting currency", fiscal_company.reporting_currency or "N/A")
        c3.metric("Sector", fiscal_company.sector or "N/A")
        c4.metric("Transcript", "AVAILABLE" if fiscal_bundle.capabilities.get("transcript") else "NOT AVAILABLE")
        datasets = fiscal_company.datasets or []
        st.caption("Available Fiscal datasets: " + (", ".join(datasets) if datasets else "not returned by profile"))

        capability_rows = [
            ["Company profile", "Available" if fiscal_bundle.capabilities.get("profile") else "Unavailable"],
            ["Reported earnings summary", "Available" if fiscal_bundle.capabilities.get("earnings_summary") else "Unavailable"],
            ["IR event resources", "Available" if fiscal_bundle.capabilities.get("investor_relations") else "Unavailable"],
            ["Structured transcript", "Available" if fiscal_bundle.capabilities.get("transcript") else "Unavailable"],
        ]
        st.markdown("### Fiscal.ai data status")
        st.dataframe(pd.DataFrame(capability_rows, columns=["Dataset", "Status"]), hide_index=True, width="stretch", lazy=True)

        if fiscal_bundle.earnings_summary:
            summary_rows = sorted(fiscal_bundle.earnings_summary, key=lambda x: str(x.get("date") or ""), reverse=True)
            display_rows = []
            report_ccy = fiscal_company.reporting_currency or currency
            for item in summary_rows[:8]:
                display_rows.append([
                    item.get("date") or "",
                    item.get("period") or "",
                    fmt_num(item.get("epsActual"), 2),
                    fmt_money(item.get("revenueActual"), report_ccy, compact=True),
                ])
            st.markdown("### Reported earnings actuals")
            st.dataframe(pd.DataFrame(display_rows, columns=["Reported", "Period", "EPS actual", "Revenue actual"]), hide_index=True, width="stretch", lazy=True)

        resources = latest_event_resources(fiscal_bundle.ir_events)
        if resources:
            latest = max(resources, key=lambda x: str(x.get("eventDate") or ""))
            r1, r2, r3 = st.columns(3)
            r1.metric("Latest Fiscal event", latest.get("eventDate") or "N/A")
            r2.metric("Fiscal period", f"Q{latest.get('fiscalQuarter')} {latest.get('fiscalYear')}" if latest.get("fiscalQuarter") and latest.get("fiscalYear") else "N/A")
            r3.metric("Event resources", len(resources))
            resource_rows = []
            source_links = []
            for item in resources:
                resource_rows.append([
                    str(item.get("role") or "").replace("_", " ").title(),
                    str(item.get("resourceType") or "").title(),
                    item.get("eventDate") or "",
                    item.get("resourceId") or "",
                ])
                if item.get("url"):
                    source_links.append(f"[{str(item.get('role') or 'resource').replace('_', ' ').title()}]({item.get('url')})")
            st.markdown("### Latest earnings-event resources")
            st.dataframe(pd.DataFrame(resource_rows, columns=["Role", "Type", "Event date", "Resource ID / event key"]), hide_index=True, width="stretch", lazy=True)
            if source_links:
                st.markdown("Fiscal sources: " + " | ".join(source_links))

        transcript = fiscal_bundle.transcript
        if isinstance(transcript, dict) and transcript:
            event = transcript.get("event") or {}
            stats = transcript.get("stats") or {}
            sections = transcript.get("sections") or []
            has_qa = any(str(x.get("sectionType") or "").lower() == "qa" for x in sections)
            t1, t2, t3, t4 = st.columns(4)
            t1.metric("Transcript event", event.get("eventKey") or fiscal_bundle.latest_event_key or "N/A")
            t2.metric("Speakers", stats.get("speakerCount") or "N/A")
            t3.metric("Paragraphs", stats.get("paragraphCount") or "N/A")
            t4.metric("Q&A section", "YES" if has_qa else "NO")

            topic_rows = transcript_topic_hits(transcript, per_topic=2)
            if topic_rows:
                st.markdown("### Management commentary by topic")
                st.caption("Deterministic keyword retrieval from Fiscal.ai's speaker-attributed transcript. These are direct transcript excerpts, not AI-generated summaries.")
                st.dataframe(
                    pd.DataFrame([[x["topic"], x["speaker"], x["excerpt"]] for x in topic_rows], columns=["Topic", "Speaker", "Transcript excerpt"]),
                    hide_index=True, width="stretch", lazy=True,
                )

            pairs = qa_pairs(transcript, limit=6)
            if pairs:
                st.markdown("### Analyst Q&A")
                qa_rows = [[x["analyst"], x["question"], x["executive"], x["answer"]] for x in pairs]
                st.dataframe(pd.DataFrame(qa_rows, columns=["Analyst", "Question", "Executive", "Management response"]), hide_index=True, width="stretch", lazy=True)

            st.markdown("### Optional local analyst interpretation")
            st.markdown(
                '<div class="note">Fiscal.ai remains the source of truth. This optional Ollama layer receives only a handful of already-selected transcript excerpts or Q&amp;A pairs. It performs no document retrieval, embeddings or RAG; it cannot change DCF inputs or generate financial numbers.</div>',
                unsafe_allow_html=True,
            )
            ollama_url_key = f"ollama_url::{ticker}"
            if ollama_url_key not in st.session_state:
                st.session_state[ollama_url_key] = "http://localhost:11434"
            o1, o2 = st.columns([4, 1])
            with o1:
                ollama_url = st.text_input("Ollama URL", key=ollama_url_key)
            with o2:
                st.write("")
                st.write("")
                check_ollama = st.button("Check Ollama", key=f"check_ollama::{ticker}", use_container_width=True)
            if check_ollama:
                try:
                    st.session_state[f"ollama_models::{ticker}"] = list_ollama_models(ollama_url)
                    st.session_state[f"ollama_error::{ticker}"] = ""
                except OllamaInterpretationError as exc:
                    st.session_state[f"ollama_models::{ticker}"] = []
                    st.session_state[f"ollama_error::{ticker}"] = str(exc)

            ollama_models = st.session_state.get(f"ollama_models::{ticker}", [])
            ollama_error = st.session_state.get(f"ollama_error::{ticker}", "")
            if ollama_error:
                st.warning(ollama_error)
            if ollama_models:
                preferred = next((m for m in ollama_models if m.lower().startswith("qwen3.5:2b")), None)
                chat_candidates = [m for m in ollama_models if "embed" not in m.lower()] or ollama_models
                if preferred not in chat_candidates:
                    preferred = chat_candidates[0]
                default_idx = chat_candidates.index(preferred) if preferred in chat_candidates else 0
                local_model = st.selectbox("Local interpretation model", chat_candidates, index=default_idx, key=f"ollama_model::{ticker}")
                st.caption("Recommended for this bounded task: qwen3.5:2b. Thinking is disabled and the source packet is intentionally small.")

                management_evidence = select_management_evidence(topic_rows, max_items=6)
                qa_evidence = select_material_qa(pairs, max_items=3) if pairs else []
                fiscal_period = f"Q{latest.get('fiscalQuarter')} {latest.get('fiscalYear')}" if resources and latest.get('fiscalQuarter') and latest.get('fiscalYear') else str(event.get("eventKey") or fiscal_bundle.latest_event_key or "latest period")
                model_facts = {
                    "Current market price": fmt_money(m.get("current_price"), currency),
                    "Base DCF fair value": fmt_money(fair, currency),
                    "Base DCF upside/downside": fmt_pct(upside),
                    "Year 1 revenue-growth assumption": fmt_pct(analysis["assumptions"].get("growth_start")),
                    "Year 5 revenue-growth assumption": fmt_pct(analysis["assumptions"].get("growth_end")),
                    "Year 1 operating-margin assumption": fmt_pct(analysis["assumptions"].get("margin_start")),
                    "Year 5 operating-margin assumption": fmt_pct(analysis["assumptions"].get("margin_end")),
                    "Year 1 CapEx / revenue assumption": fmt_pct(analysis["assumptions"].get("capex_pct_start")),
                    "Year 5 CapEx / revenue assumption": fmt_pct(analysis["assumptions"].get("capex_pct_end")),
                    "Calculated WACC": fmt_pct(analysis["assumptions"].get("wacc")),
                }

                ai1, ai2 = st.columns(2)
                with ai1:
                    run_management_interpretation = st.button("Interpret management signals", key=f"interpret_management::{ticker}", use_container_width=True)
                with ai2:
                    run_qa_interpretation = st.button("Map top analyst debates", key=f"interpret_qa::{ticker}", use_container_width=True, disabled=not bool(qa_evidence))

                if run_management_interpretation:
                    cache_key = interpretation_cache_key(fiscal_bundle.company.company_key, fiscal_bundle.latest_event_key, local_model, management_evidence, "management")
                    cached = st.session_state.get(f"management_interpretation::{ticker}")
                    if cached and cached[0] == cache_key:
                        st.info("Using the cached local interpretation for this filing, evidence packet and model.")
                    else:
                        try:
                            with st.spinner(f"Interpreting {len(management_evidence)} verified Fiscal.ai transcript excerpts with {local_model}..."):
                                result = interpret_management(ollama_url, local_model, str(data.name), ticker, fiscal_period, management_evidence, model_facts)
                            st.session_state[f"management_interpretation::{ticker}"] = (cache_key, result)
                        except OllamaInterpretationError as exc:
                            st.error(str(exc))

                if run_qa_interpretation:
                    cache_key = interpretation_cache_key(fiscal_bundle.company.company_key, fiscal_bundle.latest_event_key, local_model, qa_evidence, "qa")
                    cached = st.session_state.get(f"qa_interpretation::{ticker}")
                    if cached and cached[0] == cache_key:
                        st.info("Using the cached analyst-debate map for this filing and model.")
                    else:
                        try:
                            with st.spinner(f"Mapping {len(qa_evidence)} selected analyst Q&A debates with {local_model}..."):
                                result = interpret_qa_debates(ollama_url, local_model, str(data.name), ticker, fiscal_period, qa_evidence)
                            st.session_state[f"qa_interpretation::{ticker}"] = (cache_key, result)
                        except OllamaInterpretationError as exc:
                            st.error(str(exc))

                management_saved = st.session_state.get(f"management_interpretation::{ticker}")
                if management_saved:
                    _, result = management_saved
                    if result.grounded:
                        payload = result.payload
                        st.markdown("#### Management signals")
                        s1, s2, s3, s4 = st.columns(4)
                        s1.metric("Growth confidence", str(payload.get("growth_confidence") or "UNCLEAR").replace("_", " "))
                        s2.metric("Capital intensity", str(payload.get("capital_intensity") or "UNCLEAR").replace("_", " "))
                        s3.metric("Margin outlook", str(payload.get("margin_outlook") or "UNCLEAR").replace("_", " "))
                        s4.metric("Monetisation", str(payload.get("monetisation_progress") or "UNCLEAR").replace("_", " "))
                        debate = payload.get("key_debate") or {}
                        st.markdown("##### Key debate")
                        st.markdown(f"**{debate.get('title') or 'Unclear'}**")
                        st.write(debate.get("why_it_matters") or "")
                        bd1, bd2 = st.columns(2)
                        bd1.markdown(f"**Bull implication**  \n{debate.get('bull_implication') or 'Unclear'}")
                        bd2.markdown(f"**Bear implication**  \n{debate.get('bear_implication') or 'Unclear'}")
                        watch = payload.get("model_watch") or {}
                        st.markdown("##### DCF assumption to review")
                        st.write(f"**{str(watch.get('assumption') or 'NONE').replace('_', ' ').title()}** — {watch.get('commentary') or 'No specific model watch identified.'}")
                        st.markdown("##### Analyst read")
                        st.write(payload.get("analyst_read") or "")
                        st.caption(f"Grounding: {len(result.valid_evidence_ids)} direct Fiscal transcript evidence IDs referenced; Ollama cannot modify any calculation.")
                        evidence_by_id = {x["id"]: x for x in management_evidence}
                        cited_ids = sorted(set(result.valid_evidence_ids))
                        if cited_ids:
                            with st.expander("View supporting transcript evidence", expanded=False):
                                for eid in cited_ids:
                                    src = evidence_by_id.get(eid)
                                    if src:
                                        st.markdown(f"**{eid} · {src['topic']} · {src['speaker']}**")
                                        st.write(src["excerpt"])
                    else:
                        st.info("Local interpretation was withheld because it did not reference enough valid Fiscal.ai evidence IDs. The deterministic transcript tables above remain the source of truth.")

                qa_saved = st.session_state.get(f"qa_interpretation::{ticker}")
                if qa_saved:
                    _, qa_result = qa_saved
                    if qa_result.get("grounded") and qa_result.get("debates"):
                        st.markdown("#### Key analyst debates")
                        st.dataframe(
                            pd.DataFrame([
                                [x["analyst"], x["executive"], x["topic"], x["why_it_matters"], x["bull_implication"], x["bear_implication"]]
                                for x in qa_result["debates"]
                            ], columns=["Analyst", "Executive", "Debate", "Why it matters", "Bull implication", "Bear implication"]),
                            hide_index=True, width="stretch", lazy=True,
                        )
                    else:
                        st.info("No grounded analyst-debate interpretation was produced. The original Fiscal.ai Q&A remains available above.")
            else:
                st.caption("Press Check Ollama to enable the optional interpretation layer. Fiscal.ai transcript and Q&A data work independently of Ollama.")

            with st.form(key=f"transcript_search_form::{ticker}", border=False):
                sq1, sq2 = st.columns([4, 1])
                with sq1:
                    transcript_query = st.text_input("Search latest earnings transcript", placeholder="capex, margins, AI, demand, guidance...", key=f"fiscal_search_text::{ticker}")
                with sq2:
                    st.write("")
                    st.write("")
                    run_transcript_search = st.form_submit_button("Search transcript", use_container_width=True)
            if run_transcript_search and transcript_query.strip():
                st.session_state[f"fiscal_search_results::{ticker}"] = search_transcript(transcript, transcript_query, limit=10)
            search_rows = st.session_state.get(f"fiscal_search_results::{ticker}", [])
            if search_rows:
                st.dataframe(
                    pd.DataFrame([[x["speaker"], x["role"], x["text"]] for x in search_rows], columns=["Speaker", "Role", "Matching excerpt"]),
                    hide_index=True, width="stretch", lazy=True,
                )
        else:
            st.info("No structured transcript was returned for the latest Fiscal.ai event accessible to this API key. The IR-event and reported-earnings data above remain usable.")

        if fiscal_bundle.diagnostics:
            with st.expander("Fiscal.ai diagnostics"):
                st.code("\n".join(fiscal_bundle.diagnostics))
                if any("access" in x.lower() or "feature" in x.lower() or "403" in x.lower() for x in fiscal_bundle.diagnostics):
                    st.caption("A valid free-trial key can still have endpoint-level feature restrictions. The app keeps the accessible data and reports the restricted endpoint instead of failing the whole workflow.")

    with st.expander("Independent SEC reported-number cross-check", expanded=False):
        st.markdown('<div class="note">Optional US-only primary-source check. SEC data are used to reconcile reported revenue, operating margin and a standardized CFO-less-CapEx FCF proxy. This layer is independent of Fiscal.ai.</div>', unsafe_allow_html=True)
        sec_email_state_key = f"sec_contact::{ticker}"
        sec_email_input_key = f"sec_contact_input::{ticker}"
        if sec_email_state_key not in st.session_state:
            st.session_state[sec_email_state_key] = os.getenv("SEC_CONTACT_EMAIL", "")
        if sec_email_input_key not in st.session_state:
            st.session_state[sec_email_input_key] = st.session_state[sec_email_state_key]

        packet = st.session_state.get(f"official_packet::{ticker}")
        if packet is None:
            st.markdown('<div class="statusline">SEC connection status: waiting for a filing request.</div>', unsafe_allow_html=True)
        elif getattr(packet, "current", None):
            st.markdown(f'<div class="statusline">SEC connection status: loaded {packet.current.form or "filing"} for {packet.current.report_date or "period unavailable"}.</div>', unsafe_allow_html=True)
        elif getattr(packet, "errors", None):
            st.markdown('<div class="statusline">SEC connection status: request completed with an error.</div>', unsafe_allow_html=True)
        with st.form(key=f"sec_request_form::{ticker}", clear_on_submit=False, border=False):
            sec_email_input = st.text_input(
                "SEC contact email",
                placeholder="you@example.com",
                key=sec_email_input_key,
                help="Used only in the SEC request User-Agent for fair-access identification.",
            )
            s1, _, _ = st.columns([1.15, 1, 4])
            with s1:
                load_official = st.form_submit_button("Load filing", use_container_width=True)

        clear_sec = st.button("Clear SEC cache", key=f"clear_sec::{ticker}")
        if clear_sec:
            st.session_state.pop(f"official_packet::{ticker}", None)
            load_official_packet.clear()
            st.rerun()

        if load_official:
            sec_email = (sec_email_input or "").strip()
            if not sec_email or "@" not in sec_email or "." not in sec_email.split("@")[-1]:
                st.error("Enter a valid SEC contact email first.")
            else:
                st.session_state[sec_email_state_key] = sec_email
                try:
                    with st.spinner("Retrieving SEC XBRL and earnings documents..."):
                        packet = load_official_packet(ticker, sec_email)
                    st.session_state[f"official_packet::{ticker}"] = packet
                    st.rerun()
                except Exception as exc:
                    st.error(f"SEC retrieval failed: {exc}")

        packet = st.session_state.get(f"official_packet::{ticker}")
        if packet is not None and packet.current:
            p1, p2, p3 = st.columns(3)
            p1.metric("SEC period", packet.current.report_date or "N/A")
            p2.metric("Form", packet.current.form or "N/A")
            p3.metric("Filed", packet.current.filed or "N/A")
            secm = packet.metrics or {}
            yahoo_f = yahoo_reference["fundamentals"]
            sec_rows = [
                ["Revenue", fmt_money(yahoo_f.get("latest_quarter_revenue"), currency, compact=True), fmt_money(secm.get("revenue"), currency, compact=True), "SEC XBRL quarter fact"],
                ["Operating margin", fmt_pct(yahoo_f.get("latest_quarter_operating_margin")), fmt_pct(secm.get("operating_margin")), "SEC XBRL operating income / revenue"],
                ["FCF proxy", fmt_money(yahoo_f.get("latest_quarter_fcf"), currency, compact=True), fmt_money(secm.get("fcf_proxy"), currency, compact=True), "SEC CFO less PP&E CapEx"],
            ]
            if np.isfinite(secm.get("company_defined_fcf", np.nan)):
                sec_rows.append(["Company-defined FCF", "N/A", fmt_money(secm.get("company_defined_fcf"), currency, compact=True), "Explicit labelled amount in official earnings release"])
            st.dataframe(pd.DataFrame(sec_rows, columns=["Metric", "Yahoo", "SEC / official", "Definition"]), hide_index=True, width="stretch", lazy=True)
            release_highlights = secm.get("release_highlights", {}) or {}
            release_rows = []
            labels = {"guidance": "Guidance / outlook", "cash_flow_capex": "Cash flow / CapEx", "operations": "Operations", "risk": "Risk / headwind"}
            for category in ["guidance", "cash_flow_capex", "operations", "risk"]:
                for excerpt in (release_highlights.get(category) or [])[:2]:
                    release_rows.append([labels.get(category, category), excerpt])
            if release_rows:
                st.markdown("#### Official earnings-release anchors")
                st.dataframe(pd.DataFrame(release_rows, columns=["Area", "Official-source excerpt"]), hide_index=True, width="stretch", lazy=True)
        elif packet is not None and packet.errors:
            st.error(packet.errors[0])

    st.subheader("Yahoo earnings and estimate momentum")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Average EPS surprise, 8Q", fmt_pct(e.get("avg_eps_surprise_8q")))
    e2.metric("Beat rate, 8Q", fmt_pct(e.get("beat_rate_8q")))
    e3.metric("EPS estimate revision, 30D", fmt_pct(e.get("eps_revision_30d")))
    e4.metric("Forward EPS growth", fmt_pct(e.get("eps_forward_growth")))

    ef = earnings_surprise_chart(data.earnings_history)
    if ef:
        st.plotly_chart(ef, width="stretch", theme=None, config={"displayModeBar": False})

    if not data.eps_trend.empty:
        st.markdown("### EPS estimate trend")
        st.dataframe(data.eps_trend, width="stretch", lazy=True)

with tabs[4]:
    st.subheader("Price, volume and liquidity")
    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("1M return", fmt_pct(m.get("return_1m")))
    r2.metric("6M return", fmt_pct(m.get("return_6m")))
    r3.metric("RSI 14", fmt_num(m.get("rsi_14"), 1))
    r4.metric("Relative volume", f'{fmt_num(m.get("relative_volume"), 2)}x')
    r5.metric("Annualized volatility", fmt_pct(m.get("annualized_volatility")))
    if not data.history.empty:
        st.plotly_chart(price_chart(data.history, currency), width="stretch", theme=None, config={"displayModeBar": False})
    market_table = pd.DataFrame([
        ["50D moving average", fmt_money(m.get("sma_50"), currency)],
        ["200D moving average", fmt_money(m.get("sma_200"), currency)],
        ["52W low", fmt_money(m.get("low_52w"), currency)],
        ["52W high", fmt_money(m.get("high_52w"), currency)],
        ["Position in 52W range", fmt_pct(m.get("position_52w"))],
        ["20D average volume", f'{m.get("avg_volume_20", np.nan):,.0f}' if np.isfinite(m.get("avg_volume_20", np.nan)) else "N/A"],
        ["20D average daily value", fmt_money(m.get("avg_daily_value_20"), currency, compact=True)],
        ["5Y maximum drawdown", fmt_pct(m.get("max_drawdown_5y"))],
    ], columns=["Metric", "Value"])
    st.dataframe(market_table, hide_index=True, width="stretch", lazy=True)

with tabs[5]:
    st.subheader("Methodology and limitations")
    st.markdown("""
**Purpose.** The application turns one Yahoo Finance ticker into a structured equity-research view. It does not connect to a brokerage, store holdings, or make account-specific allocation decisions.

**Fundamental score.** The score is availability-aware. Missing metrics are excluded instead of receiving a neutral score, each component shows data coverage, and the overall score reweights only the data actually available. Data coverage is distinct from model confidence. Model confidence reflects DCF input reconciliation, source coverage, terminal-value dependence and how well the forecast assumptions are grounded.

**Fundamental source hierarchy.** Fiscal.ai standardized financials are preferred when the local key provides access. Yahoo TTM/company-statistics/annual fields remain transparent fallbacks. The Valuation tab exposes the source used for each material forecast and valuation input.

**Adaptive valuation.** Version 0.9 uses an explicit five-year FCFF model: Revenue → EBIT → NOPAT + D&A − CapEx − NWC investment. Growth, operating margin, D&A/revenue, CapEx/revenue and working-capital investment each fade independently from Year 1 to Year 5. Sector/business-model classification selects the forecast framework; Fiscal KPIs/segments are surfaced as company-specific driver evidence where available. Banks/insurers and REITs are blocked from the generic enterprise-FCFF route.

**Earnings intelligence.** Fiscal.ai is the structured earnings and transcript layer. The API provides company profiles, reported EPS/revenue, IR-event resources and speaker-attributed earnings transcripts where the user's key has access. The app retrieves transcript excerpts deterministically by topic and displays analyst questions with management responses. An optional local Ollama layer can classify a small set of already-selected excerpts and Q&A pairs; it performs no embeddings/RAG, generates no financial inputs, and cannot alter the valuation model.

**Independent primary-source check.** For SEC-reporting US issuers, EDGAR company facts and the latest 10-Q/10-K remain available as an optional cross-check. SEC-derived quarter FCF is explicitly labelled as a standardized CFO-minus-PP&E-CapEx proxy and is kept distinct from a company-defined non-GAAP FCF measure.

**WACC and reverse DCF.** WACC is calculated from the risk-free rate, beta, equity-risk premium, debt cost, tax rate and market-value capital structure, with every component visible and overrideable. The reverse model uses the same explicit-FCFF assumptions and solves for the Year-1 revenue growth rate consistent with the current market price.

**Valuation triangulation and scenarios.** The central valuation combines available DCF, own-history multiple and Fiscal peer-multiple methods. Bear/Base/Bull operating cases vary growth, margins and capital intensity while holding WACC and terminal growth constant; discount-rate uncertainty remains in the separate sensitivity matrix. Investment stance is distinct from the Fundamental Score and incorporates valuation range plus model confidence.

**Scope.** The adaptive engine is built for operating companies. Banks, insurers and REITs are intentionally blocked from the generic FCFF route; pre-revenue/binary-outcome companies still require specialist valuation. Cyclical companies require careful normalization of margins, reinvestment and terminal assumptions.
    """)
    st.markdown("### Data status")
    if data.price_scale_applied != 1.0:
        st.info(f"Price-unit normalization applied: Yahoo quote currency converted by {data.price_scale_applied:g} so share prices align with {currency} financial statements.")
    if data.errors:
        st.warning(f"{len(data.errors)} Yahoo endpoint(s) were unavailable. Core analysis may still be usable because the application uses documented fallback paths.")
        with st.expander("Endpoint diagnostics"):
            st.code("\n".join(data.errors))
    else:
        st.success("All requested Yahoo endpoints returned without an exception.")
    st.markdown("See `docs/METHODOLOGY.md`, `docs/PRIVACY.md`, and `docs/TERMS.md` in the repository for the full project notes.")
