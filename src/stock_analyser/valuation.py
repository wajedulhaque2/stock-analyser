from __future__ import annotations

import numpy as np
import pandas as pd

from .fundamentals import safe_div


def derive_valuation(info: dict, fundamentals: dict, fast_info: dict | None = None, price_scale: float = 1.0) -> dict[str, float]:
    fast_info = fast_info or {}
    raw_price = info.get("currentPrice") or info.get("regularMarketPrice") or fast_info.get("last_price")
    try:
        price = float(raw_price) * float(price_scale) if raw_price is not None else np.nan
    except Exception:
        price = np.nan
    market_cap = info.get("marketCap") or fast_info.get("market_cap")
    enterprise_value = info.get("enterpriseValue")
    fcf = fundamentals.get("fcf", np.nan)

    return {
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "trailing_pe": info.get("trailingPE", np.nan),
        "forward_pe": info.get("forwardPE", np.nan),
        "forward_pe_info": info.get("forwardPE", np.nan),
        "price_to_book": info.get("priceToBook", np.nan),
        "price_to_sales": info.get("priceToSalesTrailing12Months", np.nan),
        "ev_to_ebitda": info.get("enterpriseToEbitda", np.nan),
        "ev_to_revenue": info.get("enterpriseToRevenue", np.nan),
        "peg": info.get("pegRatio", np.nan),
        "fcf_yield": safe_div(fcf, market_cap),
        "earnings_yield": safe_div(1, info.get("trailingPE", np.nan)),
        "analyst_upside": np.nan,
        "price": price,
    }


def _finite(x, default=np.nan):
    try:
        x = float(x)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _sorted_numeric_measure(frame: pd.DataFrame, idx) -> pd.Series:
    vals = pd.to_numeric(frame.loc[idx], errors="coerce").dropna()
    if vals.empty:
        return vals
    try:
        parsed = pd.to_datetime(vals.index, errors="coerce")
        if parsed.notna().sum() >= max(1, len(vals) // 2):
            order = np.argsort(parsed.fillna(pd.Timestamp.min).values)[::-1]
            return vals.iloc[order]
    except Exception:
        pass
    return vals


def historical_multiple_context(valuation_history: pd.DataFrame) -> dict[str, float]:
    """Extract current and historical multiple context from Yahoo's valuation table.

    The table is useful because Yahoo's page-level valuation measure can sometimes be
    fresher than the generic ``info`` field. Exact measure families are kept separate
    so a trailing multiple is never accidentally substituted for a forward multiple.
    """
    if valuation_history is None or valuation_history.empty:
        return {}
    out: dict[str, float] = {}
    patterns = {
        "forward_pe": ("forward p/e", "forward pe"),
        "trailing_pe": ("trailing p/e", "trailing pe", "pe ratio"),
        "ev_to_ebitda": ("enterprise value/ebitda", "ev/ebitda", "enterprise to ebitda"),
    }
    for idx in valuation_history.index:
        label = str(idx).strip().lower()
        for key, keys in patterns.items():
            if any(k in label for k in keys):
                vals = _sorted_numeric_measure(valuation_history, idx)
                if vals.empty:
                    continue
                current = _finite(vals.iloc[0])
                hist = vals.iloc[1:] if len(vals) > 1 else vals
                out[f"current_{key}_history"] = current
                out[f"median_{key}_history"] = float(hist.median()) if len(hist) else np.nan
    # Backward-compatible display key.
    if "median_trailing_pe_history" in out:
        out["median_pe_history"] = out["median_trailing_pe_history"]
    elif "median_forward_pe_history" in out:
        out["median_pe_history"] = out["median_forward_pe_history"]
    return out


def growth_schedule(start_growth: float, end_growth: float | None, years: int) -> np.ndarray:
    if end_growth is None or not np.isfinite(float(end_growth)):
        return np.repeat(float(start_growth), years)
    return np.linspace(float(start_growth), float(end_growth), years)


def conversion_schedule(start_conversion: float, end_conversion: float | None, years: int) -> np.ndarray:
    if end_conversion is None or not np.isfinite(float(end_conversion)):
        return np.repeat(float(start_conversion), years)
    return np.linspace(float(start_conversion), float(end_conversion), years)


def dcf_model(
    revenue: float,
    operating_margin: float,
    tax_rate: float,
    growth: float,
    wacc: float,
    terminal_growth: float,
    net_debt: float,
    shares: float,
    fcf_conversion: float = 0.75,
    years: int = 5,
    end_growth: float | None = None,
    end_fcf_conversion: float | None = None,
) -> dict:
    """Simplified FCFF-style DCF with an optional fading explicit growth path.

    ``growth`` is Year-1 revenue growth. When ``end_growth`` is supplied, revenue
    growth fades linearly to that rate by the final explicit forecast year. Holding a
    single high near-term analyst growth rate flat for five years can materially
    overstate value, so the application uses the fading path by default from v0.4.
    """
    required = [revenue, operating_margin, tax_rate, growth, wacc, terminal_growth, shares]
    if any(not np.isfinite(float(x)) for x in required if x is not None) or shares <= 0:
        return {}
    if wacc <= terminal_growth:
        return {}

    rows = []
    rev = float(revenue)
    pv_sum = 0.0
    schedule = growth_schedule(growth, end_growth, years)
    conversion = conversion_schedule(fcf_conversion, end_fcf_conversion, years)
    for year, (year_growth, year_conversion) in enumerate(zip(schedule, conversion), start=1):
        rev *= (1 + year_growth)
        ebit = rev * operating_margin
        nopat = ebit * (1 - tax_rate)
        fcf = nopat * year_conversion
        pv = fcf / ((1 + wacc) ** year)
        pv_sum += pv
        rows.append({"year": year, "growth": year_growth, "fcf_conversion": year_conversion, "revenue": rev, "ebit": ebit, "fcf": fcf, "pv_fcf": pv})

    terminal_fcf = rows[-1]["fcf"] * (1 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1 + wacc) ** years)
    enterprise_value = pv_sum + pv_terminal
    equity_value = enterprise_value - (0 if np.isnan(net_debt) else net_debt)
    fair_value = equity_value / shares
    return {
        "fair_value": fair_value,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "pv_terminal_share": pv_terminal / enterprise_value if enterprise_value else np.nan,
        "forecast": pd.DataFrame(rows),
    }


def reverse_dcf_growth(
    current_price: float,
    revenue: float,
    operating_margin: float,
    tax_rate: float,
    wacc: float,
    terminal_growth: float,
    net_debt: float,
    shares: float,
    fcf_conversion: float = 0.75,
    lower: float = -0.10,
    upper: float = 0.50,
    end_growth: float | None = None,
    end_fcf_conversion: float | None = None,
) -> float:
    """Back-solve Year-1 growth required to match price under the displayed fade path."""
    if not all(np.isfinite(float(x)) for x in [current_price, revenue, operating_margin, tax_rate, wacc, terminal_growth, shares]):
        return np.nan

    def value(g):
        result = dcf_model(
            revenue, operating_margin, tax_rate, g, wacc, terminal_growth,
            net_debt, shares, fcf_conversion, end_growth=end_growth, end_fcf_conversion=end_fcf_conversion,
        )
        return result.get("fair_value", np.nan)

    lo_v, hi_v = value(lower), value(upper)
    if not np.isfinite(lo_v) or not np.isfinite(hi_v) or current_price < min(lo_v, hi_v) or current_price > max(lo_v, hi_v):
        return np.nan
    lo, hi = lower, upper
    for _ in range(80):
        mid = (lo + hi) / 2
        mid_v = value(mid)
        if abs(mid_v - current_price) < 1e-5:
            return mid
        if mid_v < current_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
