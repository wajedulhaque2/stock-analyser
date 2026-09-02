from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import numpy as np
import pandas as pd

from .fiscal_ai import transcript_topic_hits
from .fiscal_valuation import FiscalValuationBundle, financial_snapshot, kpi_driver_candidates, peer_multiple_table, ratio_context


def _finite(x: Any) -> bool:
    try:
        return np.isfinite(float(x))
    except Exception:
        return False


def _clip(value: Any, low: float, high: float, default: float) -> float:
    return float(np.clip(float(value), low, high)) if _finite(value) else float(default)


@dataclass
class ForecastFramework:
    name: str
    supported: bool
    rationale: str
    primary_drivers: list[str]
    terminal_growth_default: float
    terminal_roic_default: float = 0.12
    erp_default: float = 0.045


def classify_forecast_framework(sector: str = "", industry: str = "", reporting_template: str = "", kpis: list[dict[str, Any]] | None = None) -> ForecastFramework:
    text = f"{sector} {industry} {reporting_template}".lower()
    kpis = kpis or []
    if any(term in text for term in ["bank", "insurance", "financial services", "brokerage", "asset management"]):
        return ForecastFramework("Financial institution", False, "Banks and insurers require balance-sheet / book-value valuation rather than a conventional enterprise FCFF model.", ["ROE", "book value", "capital ratios"], 0.025)
    if any(term in text for term in ["reit", "real estate investment trust"]):
        return ForecastFramework("REIT", False, "REIT valuation should use FFO/AFFO and NAV rather than a conventional enterprise FCFF model.", ["FFO", "AFFO", "occupancy", "NAV"], 0.025)
    if any(term in text for term in ["semiconductor", "semiconductors"]):
        return ForecastFramework("Semiconductors", True, "Cyclical technology model with explicit margin and capital-intensity normalization.", ["segment revenue", "shipments / units", "gross margin", "CapEx"], 0.03, 0.18)
    if any(term in text for term in ["software", "internet", "interactive media", "platform", "digital advertising"]):
        return ForecastFramework("Software / digital platform", True, "Asset-light growth model with explicit monetisation, margin and infrastructure / CapEx drivers.", ["users / activity", "pricing / ARPU", "operating margin", "CapEx"], 0.03, 0.20)
    if any(term in text for term in ["energy", "oil", "gas", "integrated"]):
        return ForecastFramework("Energy", True, "Cyclical FCFF model; terminal assumptions should normalize commodity-cycle margins and reinvestment.", ["production / volumes", "realised pricing", "operating margin", "CapEx"], 0.02, 0.10)
    if any(term in text for term in ["industrial", "aerospace", "defense", "defence", "machinery", "transportation"]):
        return ForecastFramework("Industrials", True, "Operating-company FCFF model emphasizing backlog / volume, margin progression and capital intensity.", ["backlog / deliveries", "segment growth", "operating margin", "CapEx"], 0.025, 0.13)
    if any(term in text for term in ["consumer", "retail", "restaurant", "beverage", "apparel"]):
        return ForecastFramework("Consumer", True, "Consumer FCFF model emphasizing volume / store growth, pricing, margins and working capital.", ["same-store sales / volume", "pricing", "operating margin", "working capital"], 0.025, 0.15)
    if any(term in text for term in ["healthcare", "pharmaceutical", "biotechnology", "medical"]):
        return ForecastFramework("Healthcare", True, "Healthcare FCFF model emphasizing product / volume growth, R&D economics, margins and cash reinvestment.", ["product / patient volumes", "pipeline / launches", "operating margin", "R&D / CapEx"], 0.025, 0.15)
    if any(term in text for term in ["telecommunication", "telecom", "communication services", "media"]):
        return ForecastFramework("Telecom / media", True, "FCFF model with explicit subscriber / monetisation, margin and infrastructure reinvestment drivers.", ["subscribers / engagement", "ARPU / pricing", "operating margin", "CapEx"], 0.025, 0.12)
    return ForecastFramework("Universal operating company", True, "Generic operating-company FCFF framework with explicit growth, margin, D&A, CapEx and working-capital assumptions.", ["revenue growth", "operating margin", "D&A", "CapEx", "working capital"], 0.025)


def _currency_amount(text: str, labels: list[str]) -> tuple[float, str] | None:
    """Extract a labelled currency amount/range from one short guidance clause."""
    low = text.lower()
    if not any(label in low for label in labels):
        return None
    units = r"billion|million|trillion|bn|mn|m|b|tn"
    # Accept both "$130 to $145 billion" and "$130 billion to $145 billion".
    unit_match = re.search(
        rf"(?:\$|£|€)?\s*([0-9]+(?:\.[0-9]+)?)\s*({units})?\s*(?:-|–|to|and)\s*(?:\$|£|€)?\s*([0-9]+(?:\.[0-9]+)?)\s*({units})\b",
        low,
    )
    if unit_match:
        a, unit_a, b, unit_b = float(unit_match.group(1)), unit_match.group(2), float(unit_match.group(3)), unit_match.group(4)
        # Reject mixed scales rather than silently converting a malformed range.
        if unit_a and unit_a != unit_b:
            return None
        unit = unit_b
    else:
        single = re.search(rf"(?:\$|£|€)\s*([0-9]+(?:\.[0-9]+)?)\s*({units})\b", low)
        if not single:
            return None
        a = b = float(single.group(1)); unit = single.group(2)
    scale = 1.0
    if unit in {"billion", "bn", "b"}: scale = 1e9
    elif unit in {"million", "mn", "m"}: scale = 1e6
    elif unit in {"trillion", "tn"}: scale = 1e12
    return ((a + b) / 2.0 * scale, text)

def _annual_guidance_context(text: str, metric_labels: list[str]) -> bool:
    """Require the annual period language to be close to the metric itself.

    Earnings-call paragraphs often contain a quarterly revenue guide and a separate
    full-year expense/CapEx guide in the same paragraph. Treating any paragraph that
    contains both ``full year`` and ``revenue`` as annual revenue guidance can turn a
    quarterly dollar range into an absurd negative full-year growth rate.
    """
    low = re.sub(r"\s+", " ", text.lower())
    annual = r"(?:full[- ]year|fiscal year|fy\s*20\d{2}|20\d{2}\s+full[- ]year)"
    metrics = "|".join(re.escape(x.lower()) for x in metric_labels)
    return bool(
        re.search(rf"{annual}.{{0,70}}(?:{metrics})", low)
        or re.search(rf"(?:{metrics}).{{0,70}}{annual}", low)
    )


def extract_management_guidance(transcript: dict[str, Any] | None) -> dict[str, Any]:
    if not transcript:
        return {}
    hits = transcript_topic_hits(transcript, per_topic=10)
    texts = [str(h.get("excerpt") or "") for h in hits if h.get("topic") in {"Guidance / outlook", "Cash flow / CapEx", "Margins / costs"}]
    out: dict[str, Any] = {"evidence": []}
    for text in texts:
        # Work sentence-by-sentence. Earnings paragraphs often combine Q3 revenue
        # guidance with separate full-year expense/CapEx guidance; annual context must
        # not leak across those sentence boundaries.
        clauses = [c.strip() for c in re.split(r"(?<=[.!?;])\s+", text) if c.strip()] or [text]
        for clause in clauses:
            low = clause.lower()
            if _annual_guidance_context(clause, ["revenue", "sales"]):
                amount = _currency_amount(clause, ["revenue", "sales"])
                if amount and "revenue_mid" not in out:
                    out["revenue_mid"], evidence = amount
                    out["evidence"].append({"metric": "Full-year revenue guidance", "excerpt": evidence})

            if _annual_guidance_context(clause, ["capex", "capital expenditure", "capital expenditures"]):
                amount = _currency_amount(clause, ["capex", "capital expenditure", "capital expenditures"])
                if amount and "capex_mid" not in out:
                    out["capex_mid"], evidence = amount
                    out["evidence"].append({"metric": "Full-year CapEx guidance", "excerpt": evidence})

            # Explicit margin ranges are safe only if the same sentence contains the
            # margin label and percent range.
            if "margin" in low:
                pct = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:-|–|to|and)\s*([0-9]+(?:\.[0-9]+)?)\s*%", low)
                if pct and "margin_mid" not in out:
                    out["margin_mid"] = (float(pct.group(1)) + float(pct.group(2))) / 200.0
                    out["evidence"].append({"metric": "Margin guidance", "excerpt": clause})
    return out


def _framework_normalization_bounds(framework: ForecastFramework) -> dict[str, tuple[float, float]]:
    name = framework.name
    if name == "Software / digital platform":
        return {"growth_end": (0.03, 0.12), "margin_end": (-0.05, 0.65), "da_end": (0.0, 0.25)}
    if name == "Semiconductors":
        # Leading semiconductor companies can sustain unusually high operating
        # margins. A hard 60% ceiling incorrectly rejected NVDA-like economics.
        return {"growth_end": (0.02, 0.12), "margin_end": (-0.05, 0.75), "da_end": (0.0, 0.20)}
    if name == "Energy":
        return {"growth_end": (-0.02, 0.05), "margin_end": (-0.10, 0.45), "da_end": (0.0, 0.30)}
    return {"growth_end": (0.01, 0.09), "margin_end": (-0.10, 0.55), "da_end": (0.0, 0.25)}


def _normalized_candidate(
    candidates: list[tuple[Any, str]], current: float, low: float, high: float,
    max_abs_delta: float | None = None, max_ratio: float | None = None,
    min_ratio: float | None = None,
) -> tuple[float, str, list[str]]:
    """Choose a defensible long-run driver and reject pathological history values."""
    warnings: list[str] = []
    valid: list[tuple[float, str]] = []
    for raw, source in candidates:
        if not _finite(raw):
            continue
        value = float(raw)
        reason = None
        if not low <= value <= high:
            reason = f"outside {low:.1%}–{high:.1%} sanity bounds"
        elif max_abs_delta is not None and _finite(current) and abs(value - float(current)) > max_abs_delta:
            reason = f"moves {abs(value-float(current)):.1%} from the current ratio"
        elif max_ratio is not None and _finite(current) and float(current) > 0 and value > float(current) * max_ratio:
            reason = f"exceeds {max_ratio:.1f}× the current ratio"
        elif min_ratio is not None and _finite(current) and float(current) > 0 and value < float(current) * min_ratio:
            reason = f"falls below {min_ratio:.1f}× the current ratio"
        if reason:
            warnings.append(f"Rejected {source}: {value:.1%} ({reason}).")
        else:
            valid.append((value, source))
    if valid:
        # Prefer the candidate closest to current economics when providers conflict.
        valid.sort(key=lambda x: abs(x[0] - float(current)) if _finite(current) else 0.0)
        return valid[0][0], valid[0][1], warnings
    return float(current), "Held near current level because historical normalization failed sanity checks", warnings


def validate_forecast_assumptions(a: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when automatically generated forecast assumptions are implausible."""
    fw = a.get("framework")
    name = getattr(fw, "name", "Universal operating company")
    bounds = _framework_normalization_bounds(fw) if fw else _framework_normalization_bounds(ForecastFramework("Universal operating company", True, "", [], 0.025))
    sources = a.get("sources", {}) or {}
    issues: list[str] = []

    def auto(key: str) -> bool:
        return str(sources.get(key, "")) != "User override"

    if auto("growth_start"):
        g1 = float(a.get("growth_start", np.nan))
        floor = -0.10 if name in {"Software / digital platform", "Semiconductors"} else -0.20
        if not _finite(g1) or not floor <= g1 <= 0.50:
            issues.append(f"Auto Year-1 revenue growth {g1:.1%} failed the {name} sanity range.")
    if auto("growth_end"):
        g5 = float(a.get("growth_end", np.nan)); lo, hi = bounds["growth_end"]
        if not _finite(g5) or not lo <= g5 <= hi:
            issues.append(f"Auto Year-5 revenue growth {g5:.1%} failed the {name} normalization range {lo:.1%}–{hi:.1%}.")
    if auto("margin_end"):
        m1, m5 = float(a.get("margin_start", np.nan)), float(a.get("margin_end", np.nan)); lo, hi = bounds["margin_end"]
        if not _finite(m5) or not lo <= m5 <= hi or (_finite(m1) and abs(m5-m1) > 0.15):
            issues.append(f"Auto Year-5 operating margin {m5:.1%} is not a defensible normalization from Year-1 {m1:.1%}.")
    if auto("da_pct_end"):
        d1, d5 = float(a.get("da_pct_start", np.nan)), float(a.get("da_pct_end", np.nan)); lo, hi = bounds["da_end"]
        relative_cap = max(0.20, d1 * 2.0 + 0.02) if _finite(d1) else hi
        if not _finite(d5) or not lo <= d5 <= hi or d5 > relative_cap:
            issues.append(f"Auto Year-5 D&A/revenue {d5:.1%} failed the normalization sanity check versus Year-1 {d1:.1%}.")
    if auto("capex_pct_end") and (not _finite(a.get("capex_pct_end")) or not 0 <= float(a["capex_pct_end"]) <= 0.65):
        issues.append("Auto Year-5 CapEx/revenue failed sanity bounds.")
    return {"pass": not issues, "issues": issues}

def dynamic_wacc(data, fundamentals: dict[str, Any], market_cap: float, erp: float = 0.045, risk_free_override: float | None = None, beta_override: float | None = None) -> dict[str, Any]:
    rf = float(risk_free_override) if _finite(risk_free_override) else (float(data.risk_free_rate) if _finite(getattr(data, "risk_free_rate", np.nan)) else 0.045)
    beta = float(beta_override) if _finite(beta_override) else (float(data.info.get("beta")) if _finite(data.info.get("beta")) else 1.0)
    cost_equity = rf + beta * float(erp)
    debt = float(fundamentals.get("debt")) if _finite(fundamentals.get("debt")) else 0.0
    interest = float(fundamentals.get("interest_expense")) if _finite(fundamentals.get("interest_expense")) else np.nan
    observed_cost_debt = interest / debt if debt > 0 and np.isfinite(interest) else np.nan
    if not np.isfinite(observed_cost_debt) or not 0.005 <= observed_cost_debt <= 0.20:
        pretax_cost_debt = min(0.15, rf + 0.015)
        debt_source = "Fallback: risk-free rate + 1.5% credit spread"
    else:
        pretax_cost_debt = float(observed_cost_debt)
        debt_source = "Derived from TTM interest expense / gross debt"
    tax = _clip(fundamentals.get("tax_rate"), 0.0, 0.40, 0.21)
    after_tax_debt = pretax_cost_debt * (1 - tax)
    # Prefer Yahoo's dimensionless debt-to-equity ratio for capital weights when
    # available. This avoids mixing a local-market market cap (for example GBP) with
    # debt reported in another currency (for example USD), a common issue for ADR/LSE
    # and multinational issuers. Yahoo reports debtToEquity in percentage points.
    d2e_raw = data.info.get("debtToEquity") if isinstance(getattr(data, "info", None), dict) else np.nan
    d2e = np.nan
    if _finite(d2e_raw):
        d2e = float(d2e_raw) / 100.0 if abs(float(d2e_raw)) > 5 else float(d2e_raw)
        if not 0.0 <= d2e <= 5.0:
            d2e = np.nan
    if _finite(d2e):
        ew = 1.0 / (1.0 + float(d2e))
        dw = 1.0 - ew
        weight_source = "Yahoo debt-to-equity ratio (currency-robust capital weights)"
    else:
        equity_value = float(market_cap) if _finite(market_cap) and float(market_cap) > 0 else 0.0
        total = equity_value + max(debt, 0.0)
        ew = equity_value / total if total else 1.0
        dw = max(debt, 0.0) / total if total else 0.0
        weight_source = "Market-value capital structure"
    raw_wacc = ew * cost_equity + dw * after_tax_debt
    # A normal operating company's nominal WACC should not collapse below the
    # sovereign risk-free rate simply because debt weights/currency bases are noisy.
    # Keep a modest 50bp spread above the risk-free rate as a fail-safe floor.
    wacc_floor = max(rf + 0.005, 0.055)
    wacc = max(raw_wacc, wacc_floor)
    return {
        "risk_free_rate": rf,
        "risk_free_source": "Yahoo ^TNX US 10Y yield" if _finite(getattr(data, "risk_free_rate", np.nan)) and risk_free_override is None else "Analytical / user override",
        "equity_risk_premium": float(erp),
        "beta": beta,
        "cost_of_equity": cost_equity,
        "pretax_cost_of_debt": pretax_cost_debt,
        "after_tax_cost_of_debt": after_tax_debt,
        "cost_of_debt_source": debt_source,
        "equity_weight": ew,
        "debt_weight": dw,
        "capital_weight_source": weight_source,
        "raw_wacc": float(raw_wacc),
        "wacc_floor": float(wacc_floor),
        "wacc": float(wacc),
    }


def _schedule(start: float, end: float, years: int) -> np.ndarray:
    return np.linspace(float(start), float(end), int(years))


def explicit_fcff_dcf(
    revenue: float,
    growth_start: float,
    growth_end: float,
    margin_start: float,
    margin_end: float,
    tax_rate: float,
    da_pct_start: float,
    da_pct_end: float,
    capex_pct_start: float,
    capex_pct_end: float,
    nwc_pct_start: float,
    nwc_pct_end: float,
    wacc: float,
    terminal_growth: float,
    net_debt: float,
    shares: float,
    other_noncash_pct_start: float = 0.0,
    other_noncash_pct_end: float = 0.0,
    terminal_roic: float | None = None,
    years: int = 5,
) -> dict[str, Any]:
    required = [revenue, growth_start, growth_end, margin_start, margin_end, tax_rate, da_pct_start, da_pct_end, capex_pct_start, capex_pct_end, nwc_pct_start, nwc_pct_end, other_noncash_pct_start, other_noncash_pct_end, wacc, terminal_growth, shares]
    if not all(_finite(x) for x in required) or float(shares) <= 0 or float(wacc) <= float(terminal_growth):
        return {}
    rev = float(revenue)
    rows = []
    pv_sum = 0.0
    schedules = {
        "growth": _schedule(growth_start, growth_end, years),
        "margin": _schedule(margin_start, margin_end, years),
        "da_pct": _schedule(da_pct_start, da_pct_end, years),
        "capex_pct": _schedule(capex_pct_start, capex_pct_end, years),
        "nwc_pct": _schedule(nwc_pct_start, nwc_pct_end, years),
        "other_noncash_pct": _schedule(other_noncash_pct_start, other_noncash_pct_end, years),
    }
    for i in range(years):
        year = i + 1
        growth = schedules["growth"][i]
        margin = schedules["margin"][i]
        da_pct = schedules["da_pct"][i]
        capex_pct = schedules["capex_pct"][i]
        nwc_pct = schedules["nwc_pct"][i]
        other_noncash_pct = schedules["other_noncash_pct"][i]
        rev *= 1 + growth
        ebit = rev * margin
        nopat = ebit * (1 - tax_rate)
        da = rev * da_pct
        capex = rev * capex_pct
        nwc_investment = rev * nwc_pct
        other_noncash = rev * other_noncash_pct
        fcff = nopat + da + other_noncash - capex - nwc_investment
        pv = fcff / ((1 + wacc) ** year)
        pv_sum += pv
        rows.append({"year": year, "growth": growth, "operating_margin": margin, "revenue": rev, "ebit": ebit, "nopat": nopat, "d&a": da, "other_noncash": other_noncash, "capex": capex, "nwc_investment": nwc_investment, "fcff": fcff, "pv_fcff": pv, "da_pct_revenue": da_pct, "other_noncash_pct_revenue": other_noncash_pct, "capex_pct_revenue": capex_pct, "nwc_pct_revenue": nwc_pct})
    if not rows:
        return {}
    # Terminal value must use steady-state economics rather than carrying Year-5
    # CapEx / revenue into perpetuity. In stable growth, reinvestment is tied to
    # terminal growth and the return earned on new invested capital (RONIC/ROIC).
    # This is especially important for companies in temporary investment cycles.
    troic = float(terminal_roic) if _finite(terminal_roic) else max(0.12, float(terminal_growth) + 0.05)
    if troic <= float(terminal_growth) + 0.005:
        return {}
    terminal_reinvestment_rate = float(terminal_growth) / troic
    if not 0.0 <= terminal_reinvestment_rate < 0.95:
        return {}
    terminal_revenue = rows[-1]["revenue"] * (1 + terminal_growth)
    terminal_ebit = terminal_revenue * float(margin_end)
    terminal_nopat = terminal_ebit * (1 - float(tax_rate))
    terminal_fcff = terminal_nopat * (1 - terminal_reinvestment_rate)
    if terminal_fcff <= 0:
        return {}
    terminal_value = terminal_fcff / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1 + wacc) ** years)
    enterprise_value = pv_sum + pv_terminal
    equity_value = enterprise_value - (float(net_debt) if _finite(net_debt) else 0.0)
    fair_value = equity_value / float(shares)
    return {
        "fair_value": fair_value,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "pv_terminal_share": pv_terminal / enterprise_value if enterprise_value else np.nan,
        "terminal_roic": troic,
        "terminal_reinvestment_rate": terminal_reinvestment_rate,
        "terminal_fcff": terminal_fcff,
        "terminal_nopat": terminal_nopat,
        "forecast": pd.DataFrame(rows),
    }


def reverse_fcff_growth(current_price: float, dcf_kwargs: dict[str, Any], lower: float = -0.15, upper: float = 0.60) -> float:
    if not _finite(current_price):
        return np.nan
    def value(g: float) -> float:
        kw = dict(dcf_kwargs); kw["growth_start"] = g
        result = explicit_fcff_dcf(**kw)
        return result.get("fair_value", np.nan)
    lo_v, hi_v = value(lower), value(upper)
    if not _finite(lo_v) or not _finite(hi_v) or float(current_price) < min(lo_v, hi_v) or float(current_price) > max(lo_v, hi_v):
        return np.nan
    lo, hi = lower, upper
    for _ in range(80):
        mid = (lo + hi) / 2
        v = value(mid)
        if not _finite(v): return np.nan
        if abs(v - current_price) < 1e-5: return mid
        if v < current_price: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def build_adaptive_assumptions(data, fundamentals: dict[str, Any], earnings: dict[str, Any], valuation: dict[str, Any], fiscal_bundle: FiscalValuationBundle | None = None, guidance: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a sourced FCFF forecast and sanitize all automatic normalization inputs."""
    fiscal_snapshot = financial_snapshot(fiscal_bundle) if fiscal_bundle and fiscal_bundle.capabilities.get("financials") else {}
    kpis = kpi_driver_candidates(fiscal_bundle.segments_kpis) if fiscal_bundle and fiscal_bundle.capabilities.get("segments_and_kpis") else []
    profile = fiscal_bundle.profile if fiscal_bundle else {}
    framework = classify_forecast_framework(data.info.get("sector", ""), data.info.get("industry", ""), profile.get("reportingTemplate", ""), kpis)
    guidance = guidance or {}
    fsources = fundamentals.get("_sources", {}) or {}
    warnings: list[str] = list(fiscal_snapshot.get("_history_warnings", []) or [])

    def current_value(key: str, fallback: float, low: float, high: float) -> tuple[float, str]:
        value = fundamentals.get(key, np.nan)
        if _finite(value):
            return _clip(value, low, high, fallback), fsources.get(key, f"Reconciled current-period {key}")
        return fallback, "Analytical fallback"

    revenue = fundamentals.get("revenue", np.nan)
    revenue_source = fsources.get("revenue", "Yahoo / reconciled current-period financials")
    hist_growth = fundamentals.get("revenue_cagr_3y", np.nan)

    # Guidance gets first priority only when it produces a plausible annual growth
    # rate. This guards against accidentally treating a quarterly dollar guide as a
    # full-year guide.
    guidance_growth = np.nan
    if _finite(guidance.get("revenue_mid")) and _finite(revenue) and float(revenue) > 0:
        candidate = float(guidance["revenue_mid"]) / float(revenue) - 1
        if -0.10 <= candidate <= 0.50:
            guidance_growth = candidate
        else:
            warnings.append(f"Rejected management revenue guidance anchor because implied annual growth was {candidate:.1%}; falling back to consensus/history.")

    if _finite(guidance_growth):
        y1_growth = float(guidance_growth); growth_source = "Management full-year revenue guidance midpoint from Fiscal transcript"
    elif _finite(earnings.get("revenue_forward_growth")) and -0.10 <= float(earnings["revenue_forward_growth"]) <= 0.60:
        y1_growth = float(earnings["revenue_forward_growth"]); growth_source = "Yahoo analyst +1Y revenue growth"
    else:
        driver_growth = [d["growth"] for d in kpis if d["kind"] in {"Volume / activity driver", "Pricing / monetisation driver"} and _finite(d.get("growth")) and -0.30 < float(d["growth"]) < 1.0]
        if driver_growth and _finite(hist_growth):
            y1_growth = 0.65 * float(hist_growth) + 0.35 * float(np.median(driver_growth)); growth_source = "Blend of historical revenue CAGR and recognized Fiscal KPI growth"
        elif _finite(hist_growth):
            y1_growth = float(hist_growth); growth_source = "Historical 3Y revenue CAGR"
        else:
            y1_growth = 0.06; growth_source = "Analytical fallback"
    y1_growth = _clip(y1_growth, -0.10 if framework.name in {"Software / digital platform", "Semiconductors"} else -0.20, 0.45, 0.06)

    gb = _framework_normalization_bounds(framework)["growth_end"]
    if _finite(hist_growth) and float(hist_growth) > 0:
        normalized_growth = float(hist_growth) * 0.35
    else:
        normalized_growth = max(framework.terminal_growth_default + 0.01, gb[0])
    normalized_growth = float(np.clip(normalized_growth, gb[0], gb[1]))
    # The long-run endpoint is independently normalized. A temporary near-term
    # contraction is allowed to recover rather than being carried into Year 5.
    y5_growth = min(y1_growth, normalized_growth) if y1_growth >= 0 else normalized_growth
    growth_end_source = "Sector-aware mature-growth endpoint constrained by historical revenue growth"

    margin_start, margin_src = current_value("operating_margin", 0.15, -0.30, 0.80)
    if _finite(guidance.get("margin_mid")) and abs(float(guidance["margin_mid"]) - margin_start) <= 0.15:
        margin_start = _clip(guidance["margin_mid"], -0.30, 0.80, margin_start); margin_src = "Management margin guidance midpoint from Fiscal transcript"
    mb = _framework_normalization_bounds(framework)["margin_end"]
    margin_min_ratio = {
        "Semiconductors": 0.55,
        "Software / digital platform": 0.50,
        "Energy": 0.35,
        "Industrials": 0.35,
    }.get(framework.name, 0.30)
    margin_end, margin_end_src, w = _normalized_candidate([
        (fiscal_snapshot.get("historical_operating_margin"), "Fiscal.ai annual operating-margin history"),
        (fundamentals.get("historical_operating_margin"), "Yahoo annual operating-margin history"),
    ], margin_start, mb[0], mb[1], max_abs_delta=0.20 if framework.name == "Semiconductors" else 0.15, min_ratio=margin_min_ratio)
    warnings.extend(w)

    da_start, da_src = current_value("da_pct_revenue", 0.04, 0.0, 0.30)
    db = _framework_normalization_bounds(framework)["da_end"]
    da_ratio_cap = 1.50 if framework.name == "Software / digital platform" else 2.0
    da_abs_cap = 0.07 if framework.name in {"Software / digital platform", "Semiconductors"} else 0.10
    da_end, da_end_src, w = _normalized_candidate([
        (fiscal_snapshot.get("historical_da_pct_revenue"), "Fiscal.ai annual D&A/revenue history"),
        (fundamentals.get("historical_da_pct_revenue"), "Yahoo annual D&A/revenue history"),
    ], da_start, db[0], db[1], max_abs_delta=da_abs_cap, max_ratio=da_ratio_cap)
    warnings.extend(w)

    capex_start, capex_src = current_value("capex_pct_revenue", 0.05, 0.0, 0.65)
    capex_end, capex_end_src, w = _normalized_candidate([
        (fiscal_snapshot.get("historical_capex_pct_revenue"), "Fiscal.ai annual CapEx/revenue history"),
        (fundamentals.get("historical_capex_pct_revenue"), "Yahoo annual CapEx/revenue history"),
    ], capex_start, 0.0, 0.65, max_abs_delta=0.30)
    warnings.extend(w)

    nwc_start, nwc_src = current_value("nwc_investment_pct_revenue", 0.0, -0.15, 0.15)
    nwc_end, nwc_end_src, w = _normalized_candidate([
        (fiscal_snapshot.get("historical_nwc_investment_pct_revenue"), "Fiscal.ai annual NWC/revenue history"),
        (fundamentals.get("historical_nwc_investment_pct_revenue"), "Yahoo annual NWC/revenue history"),
    ], nwc_start, -0.15, 0.15, max_abs_delta=0.12)
    warnings.extend(w)

    other_start = fundamentals.get("other_noncash_pct_revenue", np.nan)
    if not _finite(other_start):
        other_start = 0.0; other_src = "Analytical fallback: no reconciled operating cash-flow bridge available"
    else:
        other_start = _clip(other_start, -0.30, 0.40, 0.0); other_src = fsources.get("other_noncash_pct_revenue", "Derived TTM operating cash-flow bridge")
    other_end, other_end_src, w = _normalized_candidate([
        (fundamentals.get("historical_other_noncash_pct_revenue"), "Yahoo annual residual cash-bridge history"),
    ], other_start, -0.20, 0.30, max_abs_delta=0.20)
    warnings.extend(w)

    tax = _clip(fundamentals.get("tax_rate"), 0.0, 0.45, 0.21)
    current_roic = fundamentals.get("roic", np.nan)
    if _finite(current_roic) and float(current_roic) > 0:
        terminal_roic = 0.60 * float(current_roic) + 0.40 * float(framework.terminal_roic_default)
        terminal_roic_source = "Blend of current ROIC and sector-aware sustainable ROIC anchor"
    else:
        terminal_roic = float(framework.terminal_roic_default); terminal_roic_source = f"{framework.name} sustainable ROIC default"
    terminal_roic_cap = {
        "Industrials": 0.25,
        "Energy": 0.20,
        "Consumer": 0.25,
        "Telecom / media": 0.22,
    }.get(framework.name, 0.35)
    terminal_roic = _clip(terminal_roic, max(framework.terminal_growth_default + 0.02, 0.07), terminal_roic_cap, framework.terminal_roic_default)
    wacc_info = dynamic_wacc(data, fundamentals, valuation.get("market_cap", np.nan), erp=framework.erp_default)

    a = {
        "revenue": float(revenue) if _finite(revenue) else np.nan,
        "growth_start": y1_growth, "growth_end": y5_growth,
        "margin_start": margin_start, "margin_end": margin_end, "tax_rate": tax,
        "da_pct_start": da_start, "da_pct_end": da_end,
        "capex_pct_start": capex_start, "capex_pct_end": capex_end,
        "nwc_pct_start": nwc_start, "nwc_pct_end": nwc_end,
        "other_noncash_pct_start": other_start, "other_noncash_pct_end": other_end,
        "wacc": wacc_info["wacc"], "terminal_growth": framework.terminal_growth_default, "terminal_roic": terminal_roic,
        "framework": framework, "wacc_detail": wacc_info, "kpi_drivers": kpis, "fiscal_snapshot": fiscal_snapshot,
        "assumption_warnings": warnings,
        "sources": {
            "revenue": revenue_source, "growth_start": growth_source, "growth_end": growth_end_source,
            "margin_start": margin_src, "margin_end": margin_end_src,
            "da_pct_start": da_src, "da_pct_end": da_end_src,
            "capex_pct_start": capex_src, "capex_pct_end": capex_end_src,
            "nwc_pct_start": nwc_src, "nwc_pct_end": nwc_end_src,
            "other_noncash_pct_start": other_src, "other_noncash_pct_end": other_end_src,
            "tax_rate": fsources.get("tax_rate", "Reconciled current-period tax rate"),
            "wacc": "Calculated company-specific WACC", "terminal_growth": f"{framework.name} terminal-growth default", "terminal_roic": terminal_roic_source,
        },
    }
    if overrides:
        for key, value in overrides.items():
            if key in a and _finite(value):
                a[key] = float(value); a["sources"][key] = "User override"
    a["validation"] = validate_forecast_assumptions(a)
    return a

def fcff_bridge_diagnostic(fundamentals: dict[str, Any], assumptions: dict[str, Any]) -> dict[str, Any]:
    """Tie the explicit FCFF driver bridge to observed current cash generation.

    A DCF should not become decision-driving if its Year-0 cash bridge cannot reproduce
    observed TTM FCFF reasonably. The residual ``other_noncash`` driver is intentionally
    visible and captures SBC, deferred tax and other operating non-cash adjustments not
    represented by the simplified D&A / NWC lines.
    """
    required_f = ["revenue", "operating_income", "tax_rate"]
    required_a = ["da_pct_start", "capex_pct_start", "nwc_pct_start", "other_noncash_pct_start"]
    if not all(_finite(fundamentals.get(k)) for k in required_f) or not all(_finite(assumptions.get(k)) for k in required_a):
        return {"available": False, "pass": False, "reason": "Current FCFF bridge inputs are incomplete."}
    # A reconciliation test is meaningful only when the current cash-flow bridge is
    # based on observed/reconciled data. Do not fail a generic fallback DCF merely
    # because D&A, CapEx or NWC had to use analytical defaults.
    sources = assumptions.get("sources", {}) or {}
    bridge_keys = ["da_pct_start", "capex_pct_start", "nwc_pct_start", "other_noncash_pct_start"]
    if any(str(sources.get(k, "")).startswith("Analytical fallback") for k in bridge_keys):
        return {"available": False, "pass": False, "reason": "Current FCFF bridge uses analytical fallback drivers; reconciliation is informational only."}
    revenue = float(fundamentals["revenue"])
    if revenue <= 0:
        return {"available": False, "pass": False, "reason": "Current revenue is not positive."}
    nopat = float(fundamentals["operating_income"]) * (1 - float(fundamentals["tax_rate"]))
    reconstructed = (
        nopat
        + revenue * float(assumptions["da_pct_start"])
        + revenue * float(assumptions["other_noncash_pct_start"])
        - revenue * float(assumptions["capex_pct_start"])
        - revenue * float(assumptions["nwc_pct_start"])
    )
    observed = fundamentals.get("observed_fcff", np.nan)
    if not _finite(observed):
        fcf = fundamentals.get("fcf", np.nan)
        if _finite(fcf):
            interest = abs(float(fundamentals.get("interest_expense", 0.0))) if _finite(fundamentals.get("interest_expense")) else 0.0
            sbc = abs(float(fundamentals.get("stock_based_compensation", 0.0))) if _finite(fundamentals.get("stock_based_compensation")) else 0.0
            observed = float(fcf) + interest * (1 - float(fundamentals["tax_rate"])) - sbc
    if not _finite(observed):
        return {"available": False, "pass": False, "reason": "Observed TTM FCFF is unavailable.", "reconstructed_fcff": reconstructed}
    observed = float(observed)
    gap = reconstructed - observed
    rel_gap = abs(gap) / max(abs(observed), 1.0)
    revenue_gap = abs(gap) / revenue
    passed = bool(rel_gap <= 0.15 or revenue_gap <= 0.03)
    return {
        "available": True,
        "pass": passed,
        "observed_fcff": observed,
        "reconstructed_fcff": reconstructed,
        "gap": gap,
        "relative_gap": rel_gap,
        "gap_pct_revenue": revenue_gap,
        "reason": "PASS" if passed else "Explicit FCFF bridge does not reconcile to observed TTM FCFF within tolerance.",
    }

def historical_multiple_valuation(current: dict[str, Any], fundamentals: dict[str, Any], shares: float, net_debt: float, fiscal_bundle: FiscalValuationBundle | None = None) -> dict[str, Any]:
    methods: list[dict[str, Any]] = []
    fiscal_ctx = ratio_context(fiscal_bundle.ratios) if fiscal_bundle and fiscal_bundle.capabilities.get("ratios") else {}

    trailing_eps = current.get("trailing_eps", np.nan)
    if not _finite(trailing_eps) and _finite(fundamentals.get("net_income")) and _finite(shares) and float(shares) > 0:
        trailing_eps = float(fundamentals["net_income"]) / float(shares)
    pe_med = fiscal_ctx.get("median_ratio_price_to_earnings", np.nan)
    if not _finite(pe_med):
        pe_med = current.get("median_pe_history", np.nan)
    if _finite(pe_med) and _finite(trailing_eps):
        methods.append({"method": "Own-history P/E", "fair_value": float(pe_med) * float(trailing_eps), "multiple": float(pe_med), "source": "Fiscal.ai ratio history" if "median_ratio_price_to_earnings" in fiscal_ctx else "Yahoo valuation history"})

    ebitda = fundamentals.get("ebitda", np.nan)
    ev_ebitda_med = fiscal_ctx.get("median_ratio_ev_to_ebitda", np.nan)
    if not _finite(ev_ebitda_med):
        ev_ebitda_med = current.get("median_ev_to_ebitda_history", np.nan)
    if _finite(ev_ebitda_med) and _finite(ebitda) and _finite(shares) and float(shares) > 0:
        equity = float(ev_ebitda_med) * float(ebitda) - (float(net_debt) if _finite(net_debt) else 0.0)
        methods.append({"method": "Own-history EV/EBITDA", "fair_value": equity / float(shares), "multiple": float(ev_ebitda_med), "source": "Fiscal.ai ratio history" if "median_ratio_ev_to_ebitda" in fiscal_ctx else "Yahoo valuation history"})

    return {"methods": methods, "fair_value": float(np.median([m["fair_value"] for m in methods])) if methods else np.nan, "ratio_context": fiscal_ctx}


def peer_valuation(fiscal_bundle: FiscalValuationBundle | None, fundamentals: dict[str, Any], shares: float, net_debt: float) -> dict[str, Any]:
    if not fiscal_bundle or not fiscal_bundle.peer_ratios or not _finite(shares) or float(shares) <= 0:
        return {"fair_value": np.nan, "peer_table": pd.DataFrame(), "methods": []}
    table = peer_multiple_table(fiscal_bundle)
    methods = []
    pe = pd.to_numeric(table.get("P/E", pd.Series(dtype=float)), errors="coerce").dropna()
    pe = pe[(pe >= 5.0) & (pe <= 60.0)]
    eps = float(fundamentals.get("net_income")) / float(shares) if _finite(fundamentals.get("net_income")) else np.nan
    if len(pe) >= 3 and _finite(eps):
        multiple = float(pe.median()); methods.append({"method": "Peer P/E", "multiple": multiple, "fair_value": multiple * eps})
    ev_ebitda = pd.to_numeric(table.get("EV/EBITDA", pd.Series(dtype=float)), errors="coerce").dropna()
    ev_ebitda = ev_ebitda[(ev_ebitda >= 3.0) & (ev_ebitda <= 35.0)]
    if len(ev_ebitda) >= 3 and _finite(fundamentals.get("ebitda")):
        multiple = float(ev_ebitda.median()); equity = multiple * float(fundamentals["ebitda"]) - (float(net_debt) if _finite(net_debt) else 0.0); methods.append({"method": "Peer EV/EBITDA", "multiple": multiple, "fair_value": equity / float(shares)})
    ev_ebit = pd.to_numeric(table.get("EV/EBIT", pd.Series(dtype=float)), errors="coerce").dropna()
    ev_ebit = ev_ebit[(ev_ebit >= 5.0) & (ev_ebit <= 50.0)]
    if len(ev_ebit) >= 3 and _finite(fundamentals.get("operating_income")):
        multiple = float(ev_ebit.median()); equity = multiple * float(fundamentals["operating_income"]) - (float(net_debt) if _finite(net_debt) else 0.0); methods.append({"method": "Peer EV/EBIT", "multiple": multiple, "fair_value": equity / float(shares)})
    values = [m["fair_value"] for m in methods if _finite(m.get("fair_value")) and m["fair_value"] > 0]
    return {"fair_value": float(np.median(values)) if values else np.nan, "peer_table": table, "methods": methods}


def valuation_range(dcf_value: float, historical_value: float, peer_value: float, scenarios: dict[str, Any] | None = None, dispersion_limit: float = 0.50) -> dict[str, Any]:
    methods = [("DCF", dcf_value), ("Own history", historical_value), ("Peers", peer_value)]
    available = [(name, float(v)) for name, v in methods if _finite(v) and float(v) > 0]
    values = [v for _, v in available]
    central = low = high = np.nan
    resolved = False
    reason = "No valuation methods available."
    dispersion = np.nan
    if values:
        low, high = min(values), max(values)
    if len(values) >= 2:
        provisional = float(np.median(values))
        dispersion = (max(values) - min(values)) / max(provisional, 1e-9)
        if dispersion <= dispersion_limit:
            central = provisional
            resolved = True
            reason = "Independent valuation methods are sufficiently consistent for a central research range."
            if len(values) >= 3:
                low, high = float(np.percentile(values, 25)), float(np.percentile(values, 75))
        else:
            reason = f"Independent valuation methods are too dispersed ({dispersion:.0%}) for a defensible central valuation."
    elif len(values) == 1:
        reason = "Only one independent valuation method is available; central valuation is withheld."
    bear = (scenarios or {}).get("Bear", {}).get("fair_value", np.nan)
    bull = (scenarios or {}).get("Bull", {}).get("fair_value", np.nan)
    return {"central": central, "central_low": low, "central_high": high, "bear": bear, "bull": bull, "methods": available, "resolved": resolved, "reason": reason, "dispersion": dispersion}


def investment_stance(fundamental_score: float, valuation_range_result: dict[str, Any], current_price: float, earnings_score: float, model_confidence: str) -> str:
    if not valuation_range_result.get("resolved", False):
        return "WATCH / VALUATION UNRESOLVED"
    if not _finite(current_price) or not _finite(fundamental_score) or not _finite(valuation_range_result.get("central")):
        return "PRELIMINARY"
    upside = float(valuation_range_result["central"]) / float(current_price) - 1
    conf_low = str(model_confidence).upper() in {"LOW", "VERY LOW"}
    if conf_low:
        return "WATCH / LOW CONFIDENCE"
    if fundamental_score >= 70 and upside >= 0.15 and (not _finite(earnings_score) or earnings_score >= 50):
        return "ATTRACTIVE"
    if fundamental_score >= 60 and -0.10 <= upside < 0.15:
        return "WATCH / FAIR VALUE"
    if upside <= -0.15 and fundamental_score >= 65:
        return "QUALITY / EXPENSIVE"
    if fundamental_score < 50:
        return "CAUTION"
    return "WATCH"

