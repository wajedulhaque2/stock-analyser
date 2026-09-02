from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


ALIASES = {
    "revenue": ["Total Revenue", "Operating Revenue", "Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "EBIT", "Total Operating Income As Reported"],
    "ebit": ["EBIT", "Operating Income", "Total Operating Income As Reported"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "net_income": ["Net Income", "Net Income Common Stockholders", "Net Income Including Noncontrolling Interests"],
    "pretax_income": ["Pretax Income", "Pre Tax Income"],
    "tax_provision": ["Tax Provision", "Income Tax Expense"],
    "diluted_eps": ["Diluted EPS", "Basic EPS"],
    "total_assets": ["Total Assets"],
    "total_liabilities": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
    "equity": ["Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"],
    "cash": [
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash Financial",
    ],
    "debt": ["Total Debt", "Total Debt And Capital Lease Obligation"],
    "current_assets": ["Current Assets", "Total Current Assets"],
    "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
    "inventory": ["Inventory"],
    "cfo": ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Capital Expenditures", "Purchase Of PPE"],
    "fcf": ["Free Cash Flow"],
    "depreciation": ["Depreciation And Amortization", "Depreciation", "Reconciled Depreciation"],
    "stock_based_compensation": ["Stock Based Compensation", "Share Based Compensation", "Stock Compensation"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating", "Net Non Operating Interest Income Expense"],
    "change_working_capital": ["Change In Working Capital", "Change In Net Working Capital", "Changes In Working Capital"],
    "shares": ["Diluted Average Shares", "Basic Average Shares", "Ordinary Shares Number", "Share Issued"],
}


def _clean_number(x: Any) -> float:
    try:
        if x is None or pd.isna(x):
            return np.nan
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def _canonical_label(value: Any) -> str:
    """Normalize Yahoo statement labels across pretty and raw/camel-case forms.

    yfinance financial-statement getters default to ``pretty=False`` and can therefore
    return labels such as ``TotalRevenue`` and ``StockholdersEquity`` rather than
    ``Total Revenue`` and ``Stockholders Equity``. Matching on a punctuation/spacing-
    agnostic canonical form keeps annual, TTM and quarterly extraction consistent.
    """
    return ''.join(ch.lower() for ch in str(value) if ch.isalnum())


def row(df: pd.DataFrame, key: str) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    aliases = ALIASES.get(key, [key])
    # Fast exact match first for readability-preserving / pretty frames.
    for label in aliases:
        if label in df.index:
            s = pd.to_numeric(df.loc[label], errors="coerce").dropna()
            try:
                return s.sort_index(ascending=False)
            except Exception:
                return s

    # Raw yfinance frames often use camel-case statement keys. Canonical matching
    # makes ``TotalRevenue`` equivalent to ``Total Revenue`` without maintaining a
    # second alias dictionary for every field.
    wanted = {_canonical_label(label) for label in aliases}
    wanted.add(_canonical_label(key))
    for idx in df.index:
        if _canonical_label(idx) in wanted:
            s = pd.to_numeric(df.loc[idx], errors="coerce").dropna()
            try:
                return s.sort_index(ascending=False)
            except Exception:
                return s
    return pd.Series(dtype=float)


def latest(df: pd.DataFrame, key: str, default=np.nan) -> float:
    s = row(df, key)
    return _clean_number(s.iloc[0]) if len(s) else default


def cagr(series: pd.Series, years: int | None = None) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return np.nan
    try:
        s = s.sort_index()
    except Exception:
        s = s.iloc[::-1]
    if years is not None and len(s) > years + 1:
        s = s.iloc[-(years + 1):]
    start, end = float(s.iloc[0]), float(s.iloc[-1])
    periods = len(s) - 1
    if periods <= 0 or start <= 0 or end <= 0:
        return np.nan
    return (end / start) ** (1 / periods) - 1


def safe_div(a: Any, b: Any) -> float:
    a, b = _clean_number(a), _clean_number(b)
    if pd.isna(a) or pd.isna(b) or abs(b) < 1e-12:
        return np.nan
    return a / b


def _first_value(candidates: list[tuple[Any, str]]) -> tuple[float, str | None]:
    for value, source in candidates:
        v = _clean_number(value)
        if np.isfinite(v):
            return v, source
    return np.nan, None


def _info_number(info: dict[str, Any] | None, key: str) -> float:
    return _clean_number((info or {}).get(key))


def _normalise_debt_to_equity(value: float) -> float:
    """Yahoo info often expresses debtToEquity as a percentage (e.g. 26.4)."""
    if not np.isfinite(value):
        return np.nan
    return value / 100.0 if value > 10 else value


def compute_fundamentals(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    info: dict[str, Any] | None = None,
    ttm_income: pd.DataFrame | None = None,
    ttm_cashflow: pd.DataFrame | None = None,
    quarterly_balance: pd.DataFrame | None = None,
    quarterly_income: pd.DataFrame | None = None,
    quarterly_cashflow: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build a resilient fundamental snapshot with explicit source provenance.

    TTM statement data is preferred for operating metrics. Yahoo ``info`` fields are
    used as a documented fallback, followed by the latest annual statement. This
    prevents one financial-statement endpoint or row-name variation from silently
    disabling the whole valuation model.
    """
    info = info or {}
    ttm_income = ttm_income if isinstance(ttm_income, pd.DataFrame) else pd.DataFrame()
    ttm_cashflow = ttm_cashflow if isinstance(ttm_cashflow, pd.DataFrame) else pd.DataFrame()
    quarterly_balance = quarterly_balance if isinstance(quarterly_balance, pd.DataFrame) else pd.DataFrame()
    quarterly_income = quarterly_income if isinstance(quarterly_income, pd.DataFrame) else pd.DataFrame()
    quarterly_cashflow = quarterly_cashflow if isinstance(quarterly_cashflow, pd.DataFrame) else pd.DataFrame()
    sources: dict[str, str] = {}

    revenue, src = _first_value([
        (latest(ttm_income, "revenue"), "Yahoo TTM income statement"),
        (_info_number(info, "totalRevenue"), "Yahoo company statistics (TTM)"),
        (latest(income, "revenue"), "Yahoo annual income statement"),
    ])
    if src: sources["revenue"] = src

    gross_profit, src = _first_value([
        (latest(ttm_income, "gross_profit"), "Yahoo TTM income statement"),
        (latest(income, "gross_profit"), "Yahoo annual income statement"),
    ])
    if not np.isfinite(gross_profit) and np.isfinite(revenue):
        gm = _info_number(info, "grossMargins")
        if np.isfinite(gm):
            gross_profit, src = revenue * gm, "Derived from Yahoo TTM revenue and gross margin"
    if src: sources["gross_profit"] = src

    op_income, src = _first_value([
        (latest(ttm_income, "operating_income"), "Yahoo TTM income statement"),
        (latest(income, "operating_income"), "Yahoo annual income statement"),
    ])
    if not np.isfinite(op_income) and np.isfinite(revenue):
        om = _info_number(info, "operatingMargins")
        if np.isfinite(om):
            op_income, src = revenue * om, "Derived from Yahoo TTM revenue and operating margin"
    if src: sources["operating_income"] = src

    ebitda, src = _first_value([
        (latest(ttm_income, "ebitda"), "Yahoo TTM income statement"),
        (_info_number(info, "ebitda"), "Yahoo company statistics (TTM)"),
        (latest(income, "ebitda"), "Yahoo annual income statement"),
    ])
    if src: sources["ebitda"] = src

    net_income, src = _first_value([
        (latest(ttm_income, "net_income"), "Yahoo TTM income statement"),
        (_info_number(info, "netIncomeToCommon"), "Yahoo company statistics (TTM)"),
        (latest(income, "net_income"), "Yahoo annual income statement"),
    ])
    if not np.isfinite(net_income) and np.isfinite(revenue):
        nm = _info_number(info, "profitMargins")
        if np.isfinite(nm):
            net_income, src = revenue * nm, "Derived from Yahoo TTM revenue and net margin"
    if src: sources["net_income"] = src

    assets, src = _first_value([
        (latest(quarterly_balance, "total_assets"), "Yahoo latest quarterly balance sheet"),
        (latest(balance, "total_assets"), "Yahoo annual balance sheet"),
    ])
    if src: sources["total_assets"] = src
    equity, src = _first_value([
        (latest(quarterly_balance, "equity"), "Yahoo latest quarterly balance sheet"),
        (latest(balance, "equity"), "Yahoo annual balance sheet"),
    ])
    if not np.isfinite(equity):
        liabilities, liab_src = _first_value([
            (latest(quarterly_balance, "total_liabilities"), "Yahoo latest quarterly balance sheet"),
            (latest(balance, "total_liabilities"), "Yahoo annual balance sheet"),
        ])
        if np.isfinite(assets) and np.isfinite(liabilities):
            equity, src = assets - liabilities, f"Derived from total assets less total liabilities ({liab_src})"
    if src: sources["equity"] = src

    cash, src = _first_value([
        (_info_number(info, "totalCash"), "Yahoo company statistics"),
        (latest(quarterly_balance, "cash"), "Yahoo latest quarterly balance sheet"),
        (latest(balance, "cash"), "Yahoo annual balance sheet"),
    ])
    if src: sources["cash"] = src
    debt, src = _first_value([
        (_info_number(info, "totalDebt"), "Yahoo company statistics"),
        (latest(quarterly_balance, "debt"), "Yahoo latest quarterly balance sheet"),
        (latest(balance, "debt"), "Yahoo annual balance sheet"),
    ])
    if src: sources["debt"] = src

    current_assets = latest(quarterly_balance, "current_assets")
    current_liab = latest(quarterly_balance, "current_liabilities")
    inventory = latest(quarterly_balance, "inventory", 0.0)
    if not np.isfinite(current_assets): current_assets = latest(balance, "current_assets")
    if not np.isfinite(current_liab): current_liab = latest(balance, "current_liabilities")
    if not np.isfinite(inventory): inventory = latest(balance, "inventory", 0.0)

    cfo, src = _first_value([
        (latest(ttm_cashflow, "cfo"), "Yahoo TTM cash-flow statement"),
        (_info_number(info, "operatingCashflow"), "Yahoo company statistics (TTM)"),
        (latest(cashflow, "cfo"), "Yahoo annual cash-flow statement"),
    ])
    if src: sources["cfo"] = src

    fcf, src = _first_value([
        (latest(ttm_cashflow, "fcf"), "Yahoo TTM cash-flow statement"),
        (_info_number(info, "freeCashflow"), "Yahoo company statistics (TTM)"),
        (latest(cashflow, "fcf"), "Yahoo annual cash-flow statement"),
    ])
    if not np.isfinite(fcf):
        capex = latest(ttm_cashflow, "capex")
        capex_source = "Yahoo TTM cash-flow statement"
        if not np.isfinite(capex):
            capex = latest(cashflow, "capex")
            capex_source = "Yahoo annual cash-flow statement"
        if np.isfinite(cfo) and np.isfinite(capex):
            fcf = cfo + capex if capex < 0 else cfo - capex
            src = f"Derived from CFO and CapEx ({capex_source})"
    if src: sources["fcf"] = src

    depreciation, dep_src = _first_value([
        (latest(ttm_cashflow, "depreciation"), "Yahoo TTM cash-flow statement"),
        (latest(cashflow, "depreciation"), "Yahoo annual cash-flow statement"),
    ])
    if dep_src: sources["depreciation"] = dep_src

    stock_based_compensation, sbc_src = _first_value([
        (latest(ttm_cashflow, "stock_based_compensation"), "Yahoo TTM cash-flow statement"),
        (latest(cashflow, "stock_based_compensation"), "Yahoo annual cash-flow statement"),
    ])
    stock_based_compensation = abs(stock_based_compensation) if np.isfinite(stock_based_compensation) else np.nan
    if sbc_src: sources["stock_based_compensation"] = sbc_src

    capex_raw, capex_src = _first_value([
        (latest(ttm_cashflow, "capex"), "Yahoo TTM cash-flow statement"),
        (latest(cashflow, "capex"), "Yahoo annual cash-flow statement"),
    ])
    capex = abs(capex_raw) if np.isfinite(capex_raw) else np.nan
    if capex_src: sources["capex"] = capex_src

    wc_cf, wc_src = _first_value([
        (latest(ttm_cashflow, "change_working_capital"), "Yahoo TTM cash-flow statement"),
        (latest(cashflow, "change_working_capital"), "Yahoo annual cash-flow statement"),
    ])
    # Yahoo cash-flow statements generally express Change in Working Capital as the
    # cash-flow effect. Economic investment in NWC therefore has the opposite sign.
    nwc_investment = -wc_cf if np.isfinite(wc_cf) else np.nan
    if wc_src: sources["nwc_investment"] = f"Derived as negative of Change in Working Capital ({wc_src})"

    interest_expense, int_src = _first_value([
        (latest(ttm_income, "interest_expense"), "Yahoo TTM income statement"),
        (latest(income, "interest_expense"), "Yahoo annual income statement"),
    ])
    interest_expense = abs(interest_expense) if np.isfinite(interest_expense) else np.nan
    if int_src: sources["interest_expense"] = int_src

    pretax = latest(ttm_income, "pretax_income")
    tax = latest(ttm_income, "tax_provision")
    tax_source = "Yahoo TTM income statement"
    if not np.isfinite(pretax) or not np.isfinite(tax):
        pretax = latest(income, "pretax_income")
        tax = latest(income, "tax_provision")
        tax_source = "Yahoo annual income statement"
    tax_rate = safe_div(tax, pretax)
    if not np.isfinite(tax_rate) or tax_rate < 0 or tax_rate > 0.45:
        tax_rate = 0.21
        tax_source = "Default analytical assumption"
    sources["tax_rate"] = tax_source

    operating_margin = safe_div(op_income, revenue)
    if not np.isfinite(operating_margin):
        operating_margin = _info_number(info, "operatingMargins")
        if np.isfinite(operating_margin): sources["operating_margin"] = "Yahoo company statistics (TTM)"
    else:
        sources["operating_margin"] = "Derived from operating income / revenue"

    current_ratio = safe_div(current_assets, current_liab)
    if not np.isfinite(current_ratio):
        current_ratio = _info_number(info, "currentRatio")
    quick_ratio = safe_div(current_assets - (0 if pd.isna(inventory) else inventory), current_liab)
    if not np.isfinite(quick_ratio):
        quick_ratio = _info_number(info, "quickRatio")

    roa = safe_div(net_income, assets)
    if not np.isfinite(roa): roa = _info_number(info, "returnOnAssets")
    roe = safe_div(net_income, equity)
    if not np.isfinite(roe): roe = _info_number(info, "returnOnEquity")

    debt_to_equity = safe_div(debt, equity)
    if not np.isfinite(debt_to_equity):
        debt_to_equity = _normalise_debt_to_equity(_info_number(info, "debtToEquity"))

    nopat = op_income * (1 - tax_rate) if np.isfinite(op_income) else np.nan
    invested_capital = debt + equity - cash if all(np.isfinite(x) for x in [debt, equity, cash]) else np.nan
    roic = safe_div(nopat, invested_capital)

    observed_fcf_conversion = safe_div(fcf, nopat)
    if np.isfinite(observed_fcf_conversion) and 0 < observed_fcf_conversion <= 2.0:
        sources["fcf_conversion_observed"] = "Derived from TTM FCF / TTM NOPAT"
    else:
        observed_fcf_conversion = np.nan

    # Long-run cash conversion provides a more defensible normalization anchor than
    # assuming today's investment-cycle conversion persists forever. Match annual
    # FCF and operating profit by fiscal column, derive annual NOPAT using the
    # corresponding reported tax rate where available, then use the median of up to
    # five valid observations.
    historical_fcf_conversion = np.nan
    try:
        op_series = row(income, "operating_income")
        fcf_series = row(cashflow, "fcf")
        pretax_series = row(income, "pretax_income")
        tax_series = row(income, "tax_provision")
        values = []
        common = [c for c in op_series.index if c in fcf_series.index]
        try:
            common = sorted(common, key=lambda x: pd.Timestamp(x), reverse=True)
        except Exception:
            pass
        for col in common[:5]:
            opv = _clean_number(op_series.get(col))
            fcfv = _clean_number(fcf_series.get(col))
            if not np.isfinite(opv) or not np.isfinite(fcfv) or opv <= 0:
                continue
            tr = safe_div(_clean_number(tax_series.get(col)), _clean_number(pretax_series.get(col)))
            if not np.isfinite(tr) or tr < 0 or tr > 0.45:
                tr = tax_rate
            annual_nopat = opv * (1 - tr)
            conv = safe_div(fcfv, annual_nopat)
            if np.isfinite(conv) and 0.10 <= conv <= 1.50:
                values.append(float(conv))
        if values:
            historical_fcf_conversion = float(np.median(values))
            sources["fcf_conversion_historical"] = f"Median of {len(values)} annual FCF / NOPAT observations from Yahoo statements"
    except Exception:
        historical_fcf_conversion = np.nan

    # Normalize operating drivers from annual history for explicit FCFF forecasts.
    historical_driver_rows = []
    try:
        rev_series = row(income, "revenue")
        op_series = row(income, "operating_income")
        dep_series = row(cashflow, "depreciation")
        sbc_series = row(cashflow, "stock_based_compensation")
        capex_series = row(cashflow, "capex")
        wc_series = row(cashflow, "change_working_capital")
        fcf_series = row(cashflow, "fcf")
        interest_series = row(income, "interest_expense")
        tax_series = row(income, "tax_provision")
        pretax_series = row(income, "pretax_income")
        common = list(rev_series.index)
        try:
            common = sorted(common, key=lambda x: pd.Timestamp(x), reverse=True)
        except Exception:
            pass
        for col in common[:5]:
            rv = _clean_number(rev_series.get(col))
            if not np.isfinite(rv) or rv <= 0:
                continue
            ov = _clean_number(op_series.get(col))
            dv = _clean_number(dep_series.get(col))
            sbcv = abs(_clean_number(sbc_series.get(col)))
            cv = _clean_number(capex_series.get(col))
            wv = _clean_number(wc_series.get(col))
            fcfv = _clean_number(fcf_series.get(col))
            intv = abs(_clean_number(interest_series.get(col)))
            tr = safe_div(_clean_number(tax_series.get(col)), _clean_number(pretax_series.get(col)))
            if not np.isfinite(tr) or tr < 0 or tr > 0.45:
                tr = tax_rate
            nwc_i = -wv if np.isfinite(wv) else np.nan
            nopat_i = ov * (1 - tr) if np.isfinite(ov) else np.nan
            # Treat SBC as an economic expense rather than a free non-cash add-back.
            # CFO/FCF adds SBC back; subtract it again here unless future dilution is
            # explicitly modeled. The residual then captures other operating non-cash
            # / deferred-tax cash-flow bridges without making share dilution free.
            sbc_adjustment_i = sbcv if np.isfinite(sbcv) else 0.0
            observed_fcff_i = fcfv + intv * (1 - tr) - sbc_adjustment_i if np.isfinite(fcfv) else np.nan
            base_fcff_i = (nopat_i + abs(dv) - abs(cv) - nwc_i) if all(np.isfinite(x) for x in [nopat_i, dv, cv, nwc_i]) else np.nan
            other_noncash_pct = safe_div(observed_fcff_i - base_fcff_i, rv) if np.isfinite(observed_fcff_i) and np.isfinite(base_fcff_i) else np.nan
            historical_driver_rows.append({
                "period": col,
                "operating_margin": safe_div(ov, rv),
                "da_pct_revenue": safe_div(abs(dv), rv),
                "sbc_pct_revenue": safe_div(sbcv, rv),
                "capex_pct_revenue": safe_div(abs(cv), rv),
                "nwc_investment_pct_revenue": safe_div(nwc_i, rv),
                "other_noncash_pct_revenue": other_noncash_pct,
            })
    except Exception:
        historical_driver_rows = []

    def _driver_median(key: str, low: float, high: float) -> float:
        vals = []
        for item in historical_driver_rows:
            value = _clean_number(item.get(key))
            if np.isfinite(value) and low <= value <= high:
                vals.append(value)
        return float(np.median(vals)) if vals else np.nan

    historical_operating_margin = _driver_median("operating_margin", -0.50, 0.80)
    historical_da_pct = _driver_median("da_pct_revenue", 0.0, 0.50)
    historical_capex_pct = _driver_median("capex_pct_revenue", 0.0, 0.80)
    historical_nwc_pct = _driver_median("nwc_investment_pct_revenue", -0.20, 0.20)
    historical_other_noncash_pct = _driver_median("other_noncash_pct_revenue", -0.30, 0.40)
    current_da_pct = safe_div(depreciation, revenue)
    current_capex_pct = safe_div(capex, revenue)
    current_nwc_pct = safe_div(nwc_investment, revenue)
    sbc_adjustment = stock_based_compensation if np.isfinite(stock_based_compensation) else 0.0
    observed_fcff = fcf + (interest_expense * (1 - tax_rate) if np.isfinite(interest_expense) else 0.0) - sbc_adjustment if np.isfinite(fcf) else np.nan
    base_current_fcff = nopat + depreciation - capex - nwc_investment if all(np.isfinite(x) for x in [nopat, depreciation, capex, nwc_investment]) else np.nan
    current_other_noncash_pct = safe_div(observed_fcff - base_current_fcff, revenue) if np.isfinite(observed_fcff) and np.isfinite(base_current_fcff) else np.nan
    if np.isfinite(historical_operating_margin): sources["historical_operating_margin"] = f"Median of {len(historical_driver_rows)} annual operating-margin observations"
    if np.isfinite(historical_da_pct): sources["historical_da_pct"] = f"Median of annual D&A / revenue observations"
    if np.isfinite(historical_capex_pct): sources["historical_capex_pct"] = f"Median of annual CapEx / revenue observations"
    if np.isfinite(historical_nwc_pct): sources["historical_nwc_pct"] = f"Median of annual NWC investment / revenue observations"
    if np.isfinite(historical_other_noncash_pct): sources["historical_other_noncash_pct"] = "Median of annual residual non-cash / working-capital cash bridge observations"
    if np.isfinite(current_other_noncash_pct): sources["other_noncash_pct_revenue"] = "Derived to reconcile SBC-adjusted TTM FCFF to NOPAT + D&A - CapEx - NWC"
    if np.isfinite(observed_fcff): sources["observed_fcff"] = "TTM FCF plus after-tax interest less stock-based compensation"

    revenue_cagr_3y = cagr(row(income, "revenue"), 3)
    revenue_cagr_5y = cagr(row(income, "revenue"), 5)
    if not np.isfinite(revenue_cagr_3y):
        revenue_cagr_3y = _info_number(info, "revenueGrowth")
        if np.isfinite(revenue_cagr_3y): sources["revenue_cagr_3y"] = "Yahoo company statistics (latest YoY fallback)"
    else:
        sources["revenue_cagr_3y"] = "Derived from Yahoo annual income statement"

    net_income_cagr_3y = cagr(row(income, "net_income"), 3)
    if not np.isfinite(net_income_cagr_3y):
        net_income_cagr_3y = _info_number(info, "earningsGrowth")

    fcf_cagr_3y = cagr(row(cashflow, "fcf"), 3)

    latest_q_revenue = latest(quarterly_income, "revenue")
    latest_q_op_income = latest(quarterly_income, "operating_income")
    latest_q_net_income = latest(quarterly_income, "net_income")
    latest_q_fcf = latest(quarterly_cashflow, "fcf")
    if not np.isfinite(latest_q_fcf):
        q_cfo = latest(quarterly_cashflow, "cfo")
        q_capex = latest(quarterly_cashflow, "capex")
        if np.isfinite(q_cfo) and np.isfinite(q_capex):
            latest_q_fcf = q_cfo + q_capex if q_capex < 0 else q_cfo - q_capex

    q_rev_series = row(quarterly_income, "revenue")
    latest_q_revenue_yoy = np.nan
    if len(q_rev_series) >= 2:
        try:
            qs = q_rev_series.sort_index(ascending=False)
            latest_date = pd.Timestamp(qs.index[0])
            target = latest_date - pd.DateOffset(years=1)
            dated = [(pd.Timestamp(idx), float(val)) for idx, val in qs.items()]
            prior_date, prior_value = min(dated[1:], key=lambda x: abs((x[0] - target).days))
            if abs((prior_date - target).days) <= 60:
                latest_q_revenue_yoy = safe_div(float(qs.iloc[0]), prior_value) - 1
        except Exception:
            pass

    return {
        "revenue": revenue,
        "operating_income": op_income,
        "ebitda": ebitda,
        "cash": cash,
        "debt": debt,
        "gross_margin": safe_div(gross_profit, revenue) if np.isfinite(gross_profit) else _info_number(info, "grossMargins"),
        "operating_margin": operating_margin,
        "net_margin": safe_div(net_income, revenue) if np.isfinite(net_income) else _info_number(info, "profitMargins"),
        "fcf_margin": safe_div(fcf, revenue),
        "roa": roa,
        "roe": roe,
        "roic": roic,
        "current_ratio": current_ratio,
        "quick_ratio": quick_ratio,
        "net_debt": (debt - cash) if np.isfinite(debt) and np.isfinite(cash) else np.nan,
        "debt_to_equity": debt_to_equity,
        "cfo_to_net_income": safe_div(cfo, net_income),
        "fcf": fcf,
        "cfo": cfo,
        "depreciation": depreciation,
        "stock_based_compensation": stock_based_compensation,
        "sbc_pct_revenue": safe_div(stock_based_compensation, revenue),
        "capex": capex,
        "nwc_investment": nwc_investment,
        "interest_expense": interest_expense,
        "da_pct_revenue": current_da_pct,
        "capex_pct_revenue": current_capex_pct,
        "nwc_investment_pct_revenue": current_nwc_pct,
        "historical_operating_margin": historical_operating_margin,
        "historical_da_pct_revenue": historical_da_pct,
        "historical_capex_pct_revenue": historical_capex_pct,
        "historical_nwc_investment_pct_revenue": historical_nwc_pct,
        "other_noncash_pct_revenue": current_other_noncash_pct,
        "historical_other_noncash_pct_revenue": historical_other_noncash_pct,
        "historical_driver_rows": historical_driver_rows,
        "observed_fcff": observed_fcff,
        "net_income": net_income,
        "revenue_cagr_3y": revenue_cagr_3y,
        "revenue_cagr_5y": revenue_cagr_5y,
        "net_income_cagr_3y": net_income_cagr_3y,
        "fcf_cagr_3y": fcf_cagr_3y,
        "tax_rate": tax_rate,
        "nopat": nopat,
        "fcf_conversion_observed": observed_fcf_conversion,
        "fcf_conversion_historical": historical_fcf_conversion,
        "latest_quarter_revenue": latest_q_revenue,
        "latest_quarter_revenue_yoy": latest_q_revenue_yoy,
        "latest_quarter_operating_margin": safe_div(latest_q_op_income, latest_q_revenue),
        "latest_quarter_net_margin": safe_div(latest_q_net_income, latest_q_revenue),
        "latest_quarter_fcf": latest_q_fcf,
        "latest_quarter_fcf_margin": safe_div(latest_q_fcf, latest_q_revenue),
        "_sources": sources,
    }
