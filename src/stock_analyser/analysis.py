from __future__ import annotations

from typing import Any

import numpy as np

from .earnings import compute_earnings_metrics
from .fundamentals import compute_fundamentals, latest
from .market import compute_market_metrics
from .scoring import aggregate_scores, confidence_label, research_view, valuation_view
from .valuation import derive_valuation, historical_multiple_context
from .adaptive_valuation import (
    build_adaptive_assumptions, explicit_fcff_dcf, reverse_fcff_growth,
    historical_multiple_valuation, peer_valuation, valuation_range, investment_stance,
    extract_management_guidance, fcff_bridge_diagnostic, validate_forecast_assumptions,
)
from .fiscal_valuation import financial_snapshot, shares_outstanding_value


def _finite(x: Any) -> bool:
    try:
        return np.isfinite(float(x))
    except Exception:
        return False


def _resolve_shares(data, current_price: float, market_cap: float) -> tuple[float, str | None, str]:
    """Resolve an economically consistent current share count.

    Yahoo's ``sharesOutstanding`` can represent only the quoted class for dual-class
    issuers while market capitalisation can represent the full economic equity. When
    current market-cap-implied shares diverge materially from the direct Yahoo field,
    use the market-cap bridge and flag the reconciliation explicitly.
    """
    market_implied = np.nan
    if _finite(market_cap) and _finite(current_price) and float(current_price) > 0:
        market_implied = float(market_cap) / float(current_price)

    direct_candidates = []
    for key, label in [
        ("impliedSharesOutstanding", "Yahoo company statistics: implied shares outstanding"),
        ("sharesOutstanding", "Yahoo company statistics: shares outstanding"),
    ]:
        value = data.info.get(key)
        if _finite(value) and float(value) > 0:
            direct_candidates.append((float(value), label))

    for frame, label in [
        (getattr(data, "ttm_income", None), "Yahoo TTM income statement: diluted average shares"),
        (data.income, "Yahoo annual income statement: diluted average shares"),
        (data.balance, "Yahoo annual balance sheet: ordinary shares"),
    ]:
        value = latest(frame, "shares") if frame is not None else np.nan
        if _finite(value) and float(value) > 0:
            direct_candidates.append((float(value), label))

    if _finite(market_implied):
        if direct_candidates:
            best_value, best_label = min(direct_candidates, key=lambda x: abs(x[0] / market_implied - 1))
            divergence = abs(best_value / market_implied - 1)
            if divergence <= 0.05:
                return best_value, best_label, f"Reconciles to market-cap-implied shares within {divergence:.1%}."
            return (
                market_implied,
                "Derived: current market capitalisation / current price",
                f"Used for per-share valuation because the closest Yahoo share-count field differs by {divergence:.1%}; this can occur with dual-class shares or timing differences.",
            )
        return market_implied, "Derived: current market capitalisation / current price", "No reliable direct current share-count field was available."

    if direct_candidates:
        return direct_candidates[0][0], direct_candidates[0][1], "Market-cap reconciliation unavailable."
    return np.nan, None, "No usable share count was available."

def _resolve_net_debt(f: dict, v: dict) -> tuple[float, str | None, str]:
    fundamental = float(f["net_debt"]) if _finite(f.get("net_debt")) else np.nan
    ev_bridge = (
        float(v["enterprise_value"]) - float(v["market_cap"])
        if _finite(v.get("enterprise_value")) and _finite(v.get("market_cap")) else np.nan
    )
    if _finite(fundamental):
        cash_src = f.get("_sources", {}).get("cash", "Yahoo data")
        debt_src = f.get("_sources", {}).get("debt", "Yahoo data")
        note = ""
        if _finite(ev_bridge):
            gap = fundamental - ev_bridge
            denom = max(abs(fundamental), abs(ev_bridge), 1.0)
            if abs(gap) / denom > 0.25:
                note = f"EV less market cap implies {ev_bridge:,.0f}; difference of {gap:,.0f} may reflect leases, non-controlling interests, other claims, or Yahoo timing differences."
            else:
                note = "Debt-less-cash bridge is broadly consistent with enterprise value less market capitalisation."
        return fundamental, f"Derived from debt less cash ({debt_src}; {cash_src})", note
    if _finite(ev_bridge):
        return ev_bridge, "Proxy: enterprise value less market capitalisation", "Debt/cash statement bridge unavailable; enterprise-value proxy used."
    return np.nan, None, "No usable net-debt bridge was available."

def _status_row(label: str, value: Any, source: str | None, required: bool = True, note: str = "") -> dict[str, Any]:
    ok = _finite(value)
    return {
        "Input": label,
        "Status": "Available" if ok else ("Missing" if required else "Optional"),
        "Source": source or "Not available",
        "Required": required,
        "Note": note,
        "value": value,
    }


def _scenario_values(revenue, net_debt, shares, a):
    """Operating Bear/Base/Bull cases using the explicit FCFF driver model.

    Discount-rate and terminal-growth assumptions remain fixed across cases so the
    scenario table does not double-count the separate WACC/terminal sensitivity.
    """
    if not all(_finite(x) for x in [revenue, net_debt, shares]) or not a.get("framework").supported:
        return {}
    cases = {
        "Bear": {
            "growth": max(-0.15, a["growth_start"] - 0.03),
            "end_growth": max(-0.05, a["growth_end"] - 0.02),
            "margin_start": max(-0.30, a["margin_start"] - 0.02),
            "margin_end": max(-0.30, a["margin_end"] - 0.02),
            "capex_pct_end": min(0.80, a["capex_pct_end"] + 0.02),
        },
        "Base": {
            "growth": a["growth_start"],
            "end_growth": a["growth_end"],
            "margin_start": a["margin_start"],
            "margin_end": a["margin_end"],
            "capex_pct_end": a["capex_pct_end"],
        },
        "Bull": {
            "growth": min(0.60, a["growth_start"] + 0.03),
            "end_growth": min(0.20, a["growth_end"] + 0.02),
            "margin_start": min(0.80, a["margin_start"] + 0.02),
            "margin_end": min(0.80, a["margin_end"] + 0.02),
            "capex_pct_end": max(0.0, a["capex_pct_end"] - 0.02),
        },
    }
    out = {}
    for name, c in cases.items():
        result = explicit_fcff_dcf(
            revenue=float(revenue), growth_start=float(c["growth"]), growth_end=float(c["end_growth"]),
            margin_start=float(c["margin_start"]), margin_end=float(c["margin_end"]), tax_rate=float(a["tax_rate"]),
            da_pct_start=float(a["da_pct_start"]), da_pct_end=float(a["da_pct_end"]),
            capex_pct_start=float(a["capex_pct_start"]), capex_pct_end=float(c["capex_pct_end"]),
            nwc_pct_start=float(a["nwc_pct_start"]), nwc_pct_end=float(a["nwc_pct_end"]),
            other_noncash_pct_start=float(a.get("other_noncash_pct_start", 0.0)), other_noncash_pct_end=float(a.get("other_noncash_pct_end", 0.0)),
            terminal_roic=float(np.clip(a.get("terminal_roic", 0.12) + ({"Bear": -0.02, "Base": 0.0, "Bull": 0.02}[name]), a["terminal_growth"] + 0.02, 0.40)),
            wacc=float(a["wacc"]), terminal_growth=float(a["terminal_growth"]),
            net_debt=float(net_debt), shares=float(shares),
        )
        out[name] = {
            **c, "margin": c["margin_end"], "wacc": a["wacc"], "terminal_growth": a["terminal_growth"],
            "terminal_roic": float(np.clip(a.get("terminal_roic", 0.12) + ({"Bear": -0.02, "Base": 0.0, "Bull": 0.02}[name]), a["terminal_growth"] + 0.02, 0.40)),
            "end_fcf_conversion": a.get("end_fcf_conversion", np.nan),
            "fair_value": result.get("fair_value", np.nan),
        }
    return out

def _key_debate(f, e, dcf_upside, implied_growth, assumptions) -> str:
    quality_bits = []
    if _finite(f.get("roic")):
        quality_bits.append(f"ROIC is {float(f['roic']):.1%}")
    if _finite(f.get("fcf_margin")):
        quality_bits.append(f"FCF margin is {float(f['fcf_margin']):.1%}")
    quality = ", while ".join(quality_bits) if quality_bits else "operating quality is only partially observable"
    val = (f"the explicit FCFF DCF implies {abs(float(dcf_upside)):.1%} {'upside' if dcf_upside >= 0 else 'downside'}" if _finite(dcf_upside) else "the DCF is not currently decision-ready")
    expectations = (f"The current price implies roughly {float(implied_growth):.1%} Year-1 revenue growth under the displayed operating and cost-of-capital assumptions." if _finite(implied_growth) else "A market-implied Year-1 growth rate cannot be solved reliably with the current inputs.")
    capex = ""
    if _finite(assumptions.get("capex_pct_start")) and _finite(assumptions.get("capex_pct_end")):
        capex = f" CapEx is modeled at {float(assumptions['capex_pct_start']):.1%} of revenue in Year 1, normalizing to {float(assumptions['capex_pct_end']):.1%} by Year 5."
    revision = e.get("eps_revision_30d")
    revisions = f" Thirty-day EPS estimate momentum is {float(revision):+.1%}." if _finite(revision) else ""
    return f"The central debate is whether growth and reinvestment justify the current valuation: {quality}; {val}. {expectations}{capex}{revisions}"


def _recent_quarter_flags(f: dict) -> list[str]:
    flags: list[str] = []
    ttm_margin = f.get("operating_margin")
    q_margin = f.get("latest_quarter_operating_margin")
    if _finite(ttm_margin) and _finite(q_margin):
        gap = float(q_margin) - float(ttm_margin)
        if gap <= -0.05:
            flags.append(f"Latest-quarter operating margin is {abs(gap):.1%} below the TTM margin.")
        elif gap >= 0.05:
            flags.append(f"Latest-quarter operating margin is {gap:.1%} above the TTM margin.")

    ttm_fcf_margin = f.get("fcf_margin")
    q_fcf_margin = f.get("latest_quarter_fcf_margin")
    if _finite(ttm_fcf_margin) and _finite(q_fcf_margin):
        gap = float(q_fcf_margin) - float(ttm_fcf_margin)
        if gap <= -0.05:
            flags.append(f"Latest-quarter FCF margin is {abs(gap):.1%} below the TTM FCF margin; check whether CapEx or working capital has changed the cash-flow run rate.")
        elif gap >= 0.05:
            flags.append(f"Latest-quarter FCF margin is {gap:.1%} above the TTM FCF margin.")
    return flags


def _model_confidence(
    dcf_available: bool, shares_note: str, net_debt_note: str, observed_conversion,
    growth_source_available: bool, terminal_share=np.nan, ttm_margin=np.nan, latest_q_margin=np.nan,
) -> str:
    if not dcf_available:
        return "LOW"
    score = 100
    if "differs by" in (shares_note or ""):
        score -= 15
    if "difference of" in (net_debt_note or ""):
        score -= 10
    if not _finite(observed_conversion):
        score -= 15
    if not growth_source_available:
        score -= 10
    if _finite(terminal_share) and float(terminal_share) >= 0.75:
        score -= 10
    if _finite(ttm_margin) and _finite(latest_q_margin) and abs(float(ttm_margin) - float(latest_q_margin)) >= 0.05:
        score -= 10
    if score >= 90:
        return "HIGH"
    if score >= 70:
        return "MEDIUM-HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"

def build_analysis(
    data,
    assumptions: dict | None = None,
    official_metrics: dict | None = None,
    fiscal_valuation_bundle=None,
    fiscal_earnings_bundle=None,
):
    f = compute_fundamentals(
        data.income, data.balance, data.cashflow, info=data.info,
        ttm_income=getattr(data, "ttm_income", None), ttm_cashflow=getattr(data, "ttm_cashflow", None),
        quarterly_balance=getattr(data, "quarterly_balance", None), quarterly_income=getattr(data, "quarterly_income", None),
        quarterly_cashflow=getattr(data, "quarterly_cashflow", None),
    )
    # Official filings only override current-quarter diagnostics; they do not rewrite the
    # multi-year forecast base without a standardized Fiscal financial source.
    if official_metrics:
        if _finite(official_metrics.get("revenue")): f["latest_quarter_revenue"] = float(official_metrics["revenue"])
        if _finite(official_metrics.get("operating_margin")): f["latest_quarter_operating_margin"] = float(official_metrics["operating_margin"])
        official_fcf = official_metrics.get("company_defined_fcf", official_metrics.get("fcf_proxy"))
        if _finite(official_fcf):
            f["latest_quarter_fcf"] = float(official_fcf)
            if _finite(f.get("latest_quarter_revenue")) and float(f["latest_quarter_revenue"]):
                f["latest_quarter_fcf_margin"] = float(official_fcf) / float(f["latest_quarter_revenue"])
        f["_official_quarter_source"] = official_metrics.get("source", "SEC filing / company release")

    # Fiscal standardized financials become the primary model source when available.
    fiscal_snapshot = financial_snapshot(fiscal_valuation_bundle) if fiscal_valuation_bundle and fiscal_valuation_bundle.capabilities.get("financials") else {}
    if fiscal_snapshot:
        # Fiscal is requested on explicit LTM/latest periods from v0.9.1 onward. Even then,
        # reconcile current figures to the already-working Yahoo TTM bundle before
        # replacing them. Historical Fiscal rows can be used independently.
        fsources = fiscal_snapshot.get("_sources", {})
        f.setdefault("_sources", {})

        def close_enough(new_value, old_value, rel=0.20, abs_tol=None):
            if not _finite(new_value):
                return False
            if not _finite(old_value):
                return True
            nv, ov = float(new_value), float(old_value)
            if abs_tol is not None and abs(nv - ov) <= abs_tol:
                return True
            denom = max(abs(ov), 1e-9)
            return abs(nv - ov) / denom <= rel

        # Income-statement/current balance items. Revenue is the period anchor: if it
        # does not reconcile, do not mix the rest of that current Fiscal snapshot into
        # Yahoo's TTM economics.
        fiscal_revenue_ok = close_enough(fiscal_snapshot.get("revenue"), f.get("revenue"), rel=0.20)
        if fiscal_revenue_ok:
            for key in ["revenue", "operating_income", "operating_margin", "ebitda", "net_income", "interest_expense"]:
                if _finite(fiscal_snapshot.get(key)) and close_enough(fiscal_snapshot.get(key), f.get(key), rel=0.35, abs_tol=0.03 if "margin" in key else None):
                    f[key] = float(fiscal_snapshot[key]); f["_sources"][key] = fsources.get(key, "Fiscal.ai standardized LTM")
            if _finite(fiscal_snapshot.get("tax_rate")) and 0 <= float(fiscal_snapshot["tax_rate"]) <= 0.45 and close_enough(fiscal_snapshot.get("tax_rate"), f.get("tax_rate"), rel=0.50, abs_tol=0.05):
                f["tax_rate"] = float(fiscal_snapshot["tax_rate"]); f["_sources"]["tax_rate"] = fsources.get("tax_rate", "Fiscal.ai standardized LTM")

            # Cash-flow drivers must reconcile particularly tightly because mixing a
            # quarterly/annual CapEx figure with LTM revenue can collapse FCFF.
            for key in ["depreciation", "capex", "nwc_investment"]:
                if _finite(fiscal_snapshot.get(key)) and close_enough(fiscal_snapshot.get(key), f.get(key), rel=0.50):
                    f[key] = float(fiscal_snapshot[key]); f["_sources"][key] = fsources.get(key, "Fiscal.ai standardized LTM")
            for key in ["da_pct_revenue", "capex_pct_revenue", "nwc_investment_pct_revenue"]:
                if _finite(fiscal_snapshot.get(key)) and close_enough(fiscal_snapshot.get(key), f.get(key), rel=0.50, abs_tol=0.05):
                    f[key] = float(fiscal_snapshot[key]); f["_sources"][key] = fsources.get(key, "Fiscal.ai standardized LTM")

        for key in ["cash", "debt"]:
            if _finite(fiscal_snapshot.get(key)) and close_enough(fiscal_snapshot.get(key), f.get(key), rel=0.35):
                f[key] = float(fiscal_snapshot[key]); f["_sources"][key] = fsources.get(key, "Fiscal.ai standardized latest balance sheet")

        # Historical normalization is independent of the current-period reconciliation.
        for key in ["historical_operating_margin", "historical_da_pct_revenue", "historical_capex_pct_revenue", "historical_nwc_investment_pct_revenue"]:
            if _finite(fiscal_snapshot.get(key)):
                f[key] = float(fiscal_snapshot[key]); f["_sources"][key] = fsources.get(key, f"Fiscal.ai annual history: {key}")

        f["net_debt"] = f["debt"] - f["cash"] if _finite(f.get("debt")) and _finite(f.get("cash")) else f.get("net_debt", np.nan)

    # Reconcile the explicit FCFF cash bridge after any accepted Fiscal current-period
    # substitutions. CFO/FCF contain operating non-cash add-backs that a simplified
    # NOPAT + D&A - CapEx - NWC bridge does not fully represent. SBC is deliberately
    # deducted from the observed cash-flow anchor so dilution is not treated as free;
    # the remaining residual captures other operating/deferred-tax cash-flow bridges.
    if all(_finite(f.get(k)) for k in ["revenue", "operating_income", "tax_rate", "depreciation", "capex", "nwc_investment", "fcf"]):
        tax_now = float(f["tax_rate"])
        nopat_now = float(f["operating_income"]) * (1 - tax_now)
        interest_now = float(f.get("interest_expense")) if _finite(f.get("interest_expense")) else 0.0
        sbc_now = float(f.get("stock_based_compensation")) if _finite(f.get("stock_based_compensation")) else 0.0
        observed_fcff_now = float(f["fcf"]) + abs(interest_now) * (1 - tax_now) - sbc_now
        f["observed_fcff"] = observed_fcff_now
        f.setdefault("_sources", {})["observed_fcff"] = "TTM FCF plus after-tax interest less stock-based compensation"
        base_fcff_now = nopat_now + float(f["depreciation"]) - float(f["capex"]) - float(f["nwc_investment"])
        residual = (observed_fcff_now - base_fcff_now) / float(f["revenue"]) if float(f["revenue"]) else np.nan
        if _finite(residual) and -0.30 <= float(residual) <= 0.40:
            f["other_noncash_pct_revenue"] = float(residual)
            f["nopat"] = nopat_now
            f.setdefault("_sources", {})["other_noncash_pct_revenue"] = "TTM FCFF reconciliation residual after treating SBC as an economic expense"

    m = compute_market_metrics(data.history)
    e = compute_earnings_metrics(data.earnings_history, data.eps_trend, data.eps_revisions, data.earnings_estimate, data.revenue_estimate)
    v = derive_valuation(data.info, f, getattr(data, "fast_info", {}), getattr(data, "price_scale_applied", 1.0))
    v.update(historical_multiple_context(data.valuation_history))
    if _finite(v.get("current_forward_pe_history")):
        v["forward_pe"] = float(v["current_forward_pe_history"]); v["forward_pe_source"] = "Yahoo dated valuation-measures history"
    else:
        v["forward_pe_source"] = "Yahoo company statistics"
    if _finite(v.get("current_trailing_pe_history")): v["trailing_pe"] = float(v["current_trailing_pe_history"])
    if _finite(v.get("current_ev_to_ebitda_history")): v["ev_to_ebitda"] = float(v["current_ev_to_ebitda_history"])
    v["trailing_eps"] = data.info.get("trailingEps", np.nan)

    current_price = m.get("current_price", v.get("price", np.nan))
    shares, shares_source, shares_note = _resolve_shares(data, current_price, v.get("market_cap", np.nan))
    if fiscal_valuation_bundle and fiscal_valuation_bundle.capabilities.get("shares"):
        fiscal_shares = shares_outstanding_value(fiscal_valuation_bundle.shares)
        if _finite(fiscal_shares) and float(fiscal_shares) > 0:
            market_implied = float(v["market_cap"]) / float(current_price) if _finite(v.get("market_cap")) and _finite(current_price) and float(current_price) else np.nan
            if not _finite(market_implied) or abs(float(fiscal_shares) / float(market_implied) - 1) <= 0.08:
                shares = float(fiscal_shares); shares_source = "Fiscal.ai shares outstanding"; shares_note = "Fiscal share count accepted after market-cap reconciliation."
    net_debt, net_debt_source, net_debt_note = _resolve_net_debt(f, v)

    transcript = getattr(fiscal_earnings_bundle, "transcript", None) if fiscal_earnings_bundle else None
    guidance = extract_management_guidance(transcript)

    # Map legacy override names into the explicit FCFF engine for backward compatibility.
    override_map = dict(assumptions or {})
    aliases = {
        "growth": "growth_start", "end_growth": "growth_end", "operating_margin": "margin_start",
    }
    for old, new in aliases.items():
        if old in override_map and new not in override_map: override_map[new] = override_map[old]
    if "operating_margin" in override_map and "margin_end" not in override_map:
        override_map["margin_end"] = override_map["operating_margin"]

    a = build_adaptive_assumptions(data, f, e, v, fiscal_valuation_bundle, guidance, override_map)
    # Diagnostic aliases retained for older saved notebooks/tests. They are no longer the
    # primary DCF mechanism in v0.9.
    observed_conversion = f.get("fcf_conversion_observed", np.nan)
    historical_conversion = f.get("fcf_conversion_historical", np.nan)
    a["growth"] = a["growth_start"]; a["end_growth"] = a["growth_end"]; a["operating_margin"] = a["margin_start"]
    a["fcf_conversion"] = float(np.clip(observed_conversion, 0.25, 1.25)) if _finite(observed_conversion) else 0.65
    a["end_fcf_conversion"] = float(np.clip(historical_conversion, 0.25, 1.25)) if _finite(historical_conversion) else a["fcf_conversion"]
    if assumptions:
        if "wacc" in assumptions and _finite(assumptions["wacc"]): a["wacc"] = float(assumptions["wacc"]); a["sources"]["wacc"] = "User override"
        if "terminal_growth" in assumptions and _finite(assumptions["terminal_growth"]): a["terminal_growth"] = float(assumptions["terminal_growth"]); a["sources"]["terminal_growth"] = "User override"

    revenue = a.get("revenue", f.get("revenue", np.nan))
    sources = f.get("_sources", {})
    fcff_bridge = fcff_bridge_diagnostic(f, a)
    forecast_validation = validate_forecast_assumptions(a)
    a["validation"] = forecast_validation
    valuation_status = [
        _status_row("Current price", current_price, "Yahoo repaired/normalised price history"),
        _status_row("Revenue", revenue, a["sources"].get("revenue")),
        _status_row("Year 1 revenue growth assumption", a["growth_start"], a["sources"].get("growth_start"), note="Year-1 forecast anchor; management guidance is preferred when a full-year figure can be extracted deterministically."),
        _status_row("Year 5 revenue growth assumption", a["growth_end"], a["sources"].get("growth_end")),
        _status_row("Year 1 operating margin", a["margin_start"], a["sources"].get("margin_start")),
        _status_row("Year 5 operating margin", a["margin_end"], a["sources"].get("margin_end")),
        _status_row("Tax rate", a["tax_rate"], a["sources"].get("tax_rate")),
        _status_row("Year 1 D&A / revenue", a["da_pct_start"], a["sources"].get("da_pct_start")),
        _status_row("Year 5 D&A / revenue", a["da_pct_end"], a["sources"].get("da_pct_end")),
        _status_row("Year 1 CapEx / revenue", a["capex_pct_start"], a["sources"].get("capex_pct_start")),
        _status_row("Year 5 CapEx / revenue", a["capex_pct_end"], a["sources"].get("capex_pct_end")),
        _status_row("Year 1 NWC investment / revenue", a["nwc_pct_start"], a["sources"].get("nwc_pct_start")),
        _status_row("Year 5 NWC investment / revenue", a["nwc_pct_end"], a["sources"].get("nwc_pct_end")),
        _status_row("Year 1 other non-cash / cash bridge", a.get("other_noncash_pct_start"), a["sources"].get("other_noncash_pct_start"), note="Residual bridge reconciles SBC-adjusted observed FCFF to NOPAT + D&A - CapEx - NWC; SBC remains an economic expense unless dilution is explicitly modeled."),
        _status_row("Year 5 other non-cash / cash bridge", a.get("other_noncash_pct_end"), a["sources"].get("other_noncash_pct_end")),
        _status_row("Observed TTM FCFF", fcff_bridge.get("observed_fcff"), sources.get("observed_fcff"), required=False, note="Independent Year-0 cash-flow anchor used to validate the explicit FCFF bridge. Stock-based compensation is deducted so dilution is not treated as free cash flow."),
        _status_row("Reconstructed current FCFF", fcff_bridge.get("reconstructed_fcff"), "Explicit current driver bridge", required=False, note=(f"Bridge gap {float(fcff_bridge.get('relative_gap')):.1%} of observed FCFF." if _finite(fcff_bridge.get("relative_gap")) else fcff_bridge.get("reason", ""))),
        _status_row("WACC assumption", a["wacc"], a["sources"].get("wacc")),
        _status_row("Terminal growth assumption", a["terminal_growth"], a["sources"].get("terminal_growth")),
        _status_row("Terminal ROIC / RONIC", a.get("terminal_roic"), a["sources"].get("terminal_roic"), note="Used to derive steady-state reinvestment: terminal reinvestment rate = terminal growth / terminal ROIC."),
        _status_row("Net debt / (cash)", net_debt, net_debt_source, note=net_debt_note),
        _status_row("Diluted / outstanding shares", shares, shares_source, note=shares_note),
    ]
    blockers = [r["Input"] for r in valuation_status if r["Required"] and r["Status"] == "Missing"]
    framework_supported = bool(a["framework"].supported)
    if not framework_supported: blockers.append(a["framework"].rationale)
    if a["wacc"] <= a["terminal_growth"]: blockers.append("WACC must exceed terminal growth")
    if not _finite(a.get("terminal_roic")) or float(a["terminal_roic"]) <= float(a["terminal_growth"]) + 0.005:
        blockers.append("Terminal ROIC must exceed terminal growth by a reasonable spread")
    if fcff_bridge.get("available") and not fcff_bridge.get("pass"):
        blockers.append("Current explicit FCFF cash bridge does not reconcile to observed TTM FCFF")
    for issue in forecast_validation.get("issues", []):
        blockers.append("Forecast assumption validation: " + str(issue))
    dcf_available = not blockers

    dcf_kwargs = {
        "revenue": float(revenue) if _finite(revenue) else np.nan,
        "growth_start": a["growth_start"], "growth_end": a["growth_end"],
        "margin_start": a["margin_start"], "margin_end": a["margin_end"], "tax_rate": a["tax_rate"],
        "da_pct_start": a["da_pct_start"], "da_pct_end": a["da_pct_end"],
        "capex_pct_start": a["capex_pct_start"], "capex_pct_end": a["capex_pct_end"],
        "nwc_pct_start": a["nwc_pct_start"], "nwc_pct_end": a["nwc_pct_end"],
        "other_noncash_pct_start": a.get("other_noncash_pct_start", 0.0), "other_noncash_pct_end": a.get("other_noncash_pct_end", 0.0),
        "terminal_roic": a.get("terminal_roic", np.nan),
        "wacc": a["wacc"], "terminal_growth": a["terminal_growth"], "net_debt": float(net_debt) if _finite(net_debt) else np.nan, "shares": float(shares) if _finite(shares) else np.nan,
    }
    dcf = explicit_fcff_dcf(**dcf_kwargs) if dcf_available else {}
    implied_growth = reverse_fcff_growth(float(current_price), dcf_kwargs) if dcf_available and _finite(current_price) else np.nan
    fair_value = dcf.get("fair_value", np.nan)
    dcf_upside = fair_value / current_price - 1 if _finite(fair_value) and _finite(current_price) and float(current_price) else np.nan

    scenarios = _scenario_values(revenue, net_debt, shares, a) if dcf_available else {}
    scenario_values = [scenarios.get(k, {}).get("fair_value", np.nan) for k in ["Bear", "Base", "Bull"]]
    scenario_order_valid = bool(all(_finite(x) for x in scenario_values) and float(scenario_values[0]) <= float(scenario_values[1]) <= float(scenario_values[2]))
    if scenarios and not scenario_order_valid:
        scenarios = {}
    hist_val = historical_multiple_valuation(v, f, shares, net_debt, fiscal_valuation_bundle)
    peer_val = peer_valuation(fiscal_valuation_bundle, f, shares, net_debt)
    range_result = valuation_range(fair_value, hist_val.get("fair_value", np.nan), peer_val.get("fair_value", np.nan), scenarios)
    central_upside = range_result.get("central", np.nan) / current_price - 1 if range_result.get("resolved") and _finite(range_result.get("central")) and _finite(current_price) and float(current_price) else np.nan

    # Do not substitute one disputed DCF for an unresolved triangulated valuation.
    # The Fundamental Score can still be calculated from the remaining evidence, but
    # the valuation component should not masquerade as resolved.
    valuation_signal = central_upside if range_result.get("resolved") else np.nan
    scores, overall, coverage, component_coverage = aggregate_scores(f, e, v, m, valuation_signal)
    data_coverage_label = confidence_label(coverage, dcf_available=dcf_available)
    growth_source_available = not str(a["sources"].get("growth_start", "")).startswith("Analytical")
    model_confidence = _model_confidence(dcf_available, shares_note, net_debt_note, f.get("fcf_conversion_observed"), growth_source_available, terminal_share=dcf.get("pv_terminal_share", np.nan), ttm_margin=f.get("operating_margin"), latest_q_margin=f.get("latest_quarter_operating_margin"))
    # Confidence cannot be upgraded merely because a provider is available. Require a
    # clean cash bridge, sane scenario ordering and reasonable cross-method dispersion.
    if fcff_bridge.get("available") and not fcff_bridge.get("pass"):
        model_confidence = "LOW"
    elif not fcff_bridge.get("available") and model_confidence == "HIGH":
        model_confidence = "MEDIUM-HIGH"
    if dcf_available and not scenario_order_valid:
        model_confidence = "LOW"
    sbc_pct = f.get("sbc_pct_revenue", np.nan)
    if _finite(sbc_pct):
        if float(sbc_pct) >= 0.08 and model_confidence in {"HIGH", "MEDIUM-HIGH"}:
            model_confidence = "MEDIUM"
        elif float(sbc_pct) >= 0.03 and model_confidence == "HIGH":
            model_confidence = "MEDIUM-HIGH"
    method_values = [float(vv) for _, vv in range_result.get("methods", []) if _finite(vv) and float(vv) > 0]
    if not range_result.get("resolved", False) and _finite(range_result.get("dispersion")) and float(range_result["dispersion"]) > 0.75:
        # Extreme disagreement between independently valid methods is a model-risk
        # signal. A single available method, however, is a valuation-coverage issue
        # rather than evidence that the DCF mechanics themselves are low quality.
        model_confidence = "LOW"
    elif len(method_values) >= 2:
        dispersion = float(range_result.get("dispersion", np.nan))
        if _finite(dispersion) and dispersion > 0.50 and model_confidence in {"HIGH", "MEDIUM-HIGH"}:
            model_confidence = "MEDIUM"
    earnings_component = scores.get("Earnings", np.nan)
    stance = investment_stance(overall, range_result, current_price, earnings_component, model_confidence)

    quarterly_data_status = {
        "Income statement": "Available" if getattr(data, "quarterly_income", None) is not None and not data.quarterly_income.empty else "Missing",
        "Cash-flow statement": "Available" if getattr(data, "quarterly_cashflow", None) is not None and not data.quarterly_cashflow.empty else "Missing",
        "Balance sheet": "Available" if getattr(data, "quarterly_balance", None) is not None and not data.quarterly_balance.empty else "Missing",
    }

    return {
        "fundamentals": f, "market": m, "earnings": e, "valuation": v,
        "assumptions": a, "forecast_framework": a["framework"], "guidance": guidance,
        "dcf": dcf, "dcf_upside": dcf_upside, "implied_growth": implied_growth,
        "historical_valuation": hist_val, "peer_valuation": peer_val, "valuation_range": range_result,
        "central_upside": central_upside,
        "scores": scores, "overall_score": overall, "score_coverage": coverage, "component_coverage": component_coverage,
        "confidence": data_coverage_label, "model_confidence": model_confidence,
        "research_view": research_view(overall, coverage), "fundamental_view": research_view(overall, coverage),
        "investment_stance": stance,
        "valuation_view": (valuation_view(central_upside) if range_result.get("resolved") else "UNRESOLVED"),
        "valuation_status": valuation_status, "valuation_blockers": blockers, "dcf_available": dcf_available,
        "fcff_bridge": fcff_bridge, "forecast_validation": forecast_validation, "scenario_order_valid": scenario_order_valid,
        "shares": shares, "net_debt_for_dcf": net_debt, "scenarios": scenarios,
        "key_debate": _key_debate(f, e, dcf_upside, implied_growth, a),
        "recent_quarter_flags": _recent_quarter_flags(f), "quarterly_data_status": quarterly_data_status,
        "fiscal_valuation_available": bool(fiscal_valuation_bundle),
    }
