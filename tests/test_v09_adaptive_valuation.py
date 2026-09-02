import math
from types import SimpleNamespace

import numpy as np

from stock_analyser.adaptive_valuation import (
    classify_forecast_framework,
    dynamic_wacc,
    explicit_fcff_dcf,
    reverse_fcff_growth,
    valuation_range,
    investment_stance,
)
from stock_analyser.fiscal_valuation import (
    FiscalValuationBundle,
    financial_snapshot,
    kpi_driver_candidates,
    ratio_context,
)
from stock_analyser.fiscal_ai import FiscalCompany


def test_framework_blocks_financials_and_recognises_digital_platform():
    bank = classify_forecast_framework("Financials", "Banks")
    assert bank.supported is False
    assert "Financial" in bank.name
    meta_like = classify_forecast_framework("Communication Services", "Interactive Media & Services")
    assert meta_like.supported is True
    assert meta_like.name in {"Software / digital platform", "Telecom / media"}


def test_explicit_fcff_dcf_uses_da_capex_and_nwc():
    result = explicit_fcff_dcf(
        revenue=100.0,
        growth_start=0.10,
        growth_end=0.10,
        margin_start=0.20,
        margin_end=0.20,
        tax_rate=0.25,
        da_pct_start=0.05,
        da_pct_end=0.05,
        capex_pct_start=0.08,
        capex_pct_end=0.08,
        nwc_pct_start=0.01,
        nwc_pct_end=0.01,
        wacc=0.10,
        terminal_growth=0.025,
        net_debt=0.0,
        shares=10.0,
        years=5,
    )
    assert result
    first = result["forecast"].iloc[0]
    assert math.isclose(first["revenue"], 110.0)
    assert math.isclose(first["ebit"], 22.0)
    assert math.isclose(first["nopat"], 16.5)
    assert math.isclose(first["d&a"], 5.5)
    assert math.isclose(first["capex"], 8.8)
    assert math.isclose(first["nwc_investment"], 1.1)
    assert math.isclose(first["fcff"], 12.1)
    assert result["fair_value"] > 0


def test_reverse_fcff_growth_reconciles_to_price():
    kwargs = dict(
        revenue=100.0, growth_start=0.08, growth_end=0.04,
        margin_start=0.22, margin_end=0.22, tax_rate=0.23,
        da_pct_start=0.04, da_pct_end=0.04,
        capex_pct_start=0.05, capex_pct_end=0.05,
        nwc_pct_start=0.01, nwc_pct_end=0.01,
        wacc=0.09, terminal_growth=0.025, net_debt=5.0, shares=10.0,
    )
    target = explicit_fcff_dcf(**kwargs)["fair_value"]
    implied = reverse_fcff_growth(target, kwargs)
    assert np.isfinite(implied)
    assert abs(implied - 0.08) < 1e-4


def test_dynamic_wacc_is_capital_structure_weighted():
    data = SimpleNamespace(risk_free_rate=0.04, info={"beta": 1.2})
    out = dynamic_wacc(
        data,
        {"debt": 25.0, "interest_expense": 1.25, "tax_rate": 0.20},
        market_cap=75.0,
        erp=0.05,
    )
    assert math.isclose(out["cost_of_equity"], 0.10)
    assert math.isclose(out["pretax_cost_of_debt"], 0.05)
    assert math.isclose(out["equity_weight"], 0.75)
    assert math.isclose(out["debt_weight"], 0.25)
    assert math.isclose(out["wacc"], 0.085)


def _bundle(financials=None, ratios=None, segments=None):
    return FiscalValuationBundle(
        company=FiscalCompany(company_key="NASDAQ_TEST", display_name="Test", ticker="TEST"),
        profile={}, financials=financials or {}, ratios=ratios or {},
        segments_kpis=segments or {}, shares={}, peer_ratios=[],
        capabilities={"financials": bool(financials), "ratios": bool(ratios), "segments_and_kpis": bool(segments)},
    )


def test_fiscal_standardized_snapshot_builds_explicit_fcff_drivers():
    income = {"data": [
        {"metricId": "revenue", "metricName": "Revenue", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 200.0},
        {"metricId": "operating_income", "metricName": "Operating Income", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 60.0},
        {"metricId": "pretax", "metricName": "Pretax Income", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 55.0},
        {"metricId": "tax", "metricName": "Tax Provision", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 11.0},
    ]}
    cashflow = {"data": [
        {"metricId": "da", "metricName": "Depreciation and Amortization", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 20.0},
        {"metricId": "capex", "metricName": "Capital Expenditures", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": -30.0},
        {"metricId": "wc", "metricName": "Change in Working Capital", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": -4.0},
    ]}
    snap = financial_snapshot(_bundle(financials={"income-statement": income, "balance-sheet": {}, "cash-flow-statement": cashflow}))
    assert math.isclose(snap["revenue"], 200.0)
    assert math.isclose(snap["operating_margin"], 0.30)
    assert math.isclose(snap["tax_rate"], 0.20)
    assert math.isclose(snap["da_pct_revenue"], 0.10)
    assert math.isclose(snap["capex_pct_revenue"], 0.15)
    assert math.isclose(snap["nwc_investment_pct_revenue"], 0.02)


def test_ratio_context_sorts_latest_period_not_payload_order():
    payload = {"data": [
        {"ratioId": "ratio_price_to_earnings", "ratioName": "P/E", "periodType": "annual", "periodEndDate": "2024-12-31", "value": 20.0},
        {"ratioId": "ratio_price_to_earnings", "ratioName": "P/E", "periodType": "latest", "periodEndDate": "2026-08-25", "value": 25.0},
        {"ratioId": "ratio_price_to_earnings", "ratioName": "P/E", "periodType": "annual", "periodEndDate": "2025-12-31", "value": 22.0},
    ]}
    ctx = ratio_context(payload)
    assert math.isclose(ctx["current_ratio_price_to_earnings"], 25.0)
    assert math.isclose(ctx["median_ratio_price_to_earnings"], 21.0)


def test_kpi_driver_candidates_classifies_activity_and_pricing():
    payload = {"data": [
        {"metricId": "users", "metricName": "Daily Active Users", "periodEndDate": "2026-06-30", "value": 110},
        {"metricId": "users", "metricName": "Daily Active Users", "periodEndDate": "2025-06-30", "value": 100},
        {"metricId": "arpu", "metricName": "Average Revenue Per User", "periodEndDate": "2026-06-30", "value": 12},
        {"metricId": "arpu", "metricName": "Average Revenue Per User", "periodEndDate": "2025-06-30", "value": 10},
    ]}
    candidates = kpi_driver_candidates(payload)
    kinds = {c["metric_id"]: c["kind"] for c in candidates}
    assert kinds["users"] == "Volume / activity driver"
    assert kinds["arpu"] == "Pricing / monetisation driver"


def test_valuation_range_and_investment_stance_are_separate():
    rng = valuation_range(100.0, 120.0, 110.0, {"Bear": {"fair_value": 80.0}, "Bull": {"fair_value": 140.0}})
    assert math.isclose(rng["central"], 110.0)
    assert rng["central_low"] <= rng["central"] <= rng["central_high"]
    assert investment_stance(75.0, rng, current_price=100.0, earnings_score=70.0, model_confidence="MEDIUM") == "WATCH / FAIR VALUE"
    assert investment_stance(80.0, valuation_range(140, 140, 140), current_price=100.0, earnings_score=70, model_confidence="HIGH") == "ATTRACTIVE"


def test_fcff_bridge_reconciles_stock_comp_heavy_business():
    from stock_analyser.adaptive_valuation import fcff_bridge_diagnostic
    fundamentals = {
        "revenue": 200.0,
        "operating_income": 70.0,
        "tax_rate": 0.20,
        "fcf": 36.0,
        "interest_expense": 1.0,
        "observed_fcff": 36.8,
    }
    # NOPAT 56 + D&A 20 + other cash bridge 30 - CapEx 66 - NWC 3.2 = 36.8.
    assumptions = {
        "da_pct_start": 0.10,
        "capex_pct_start": 0.33,
        "nwc_pct_start": 0.016,
        "other_noncash_pct_start": 0.15,
        "sources": {
            "da_pct_start": "Yahoo TTM cash flow",
            "capex_pct_start": "Yahoo TTM cash flow",
            "nwc_pct_start": "Yahoo TTM cash flow",
            "other_noncash_pct_start": "TTM FCFF reconciliation residual",
        },
    }
    diag = fcff_bridge_diagnostic(fundamentals, assumptions)
    assert diag["available"] is True
    assert diag["pass"] is True
    assert math.isclose(diag["reconstructed_fcff"], 36.8, rel_tol=1e-9)
    assert math.isclose(diag["observed_fcff"], 36.8, rel_tol=1e-9)


def test_fcff_bridge_does_not_fail_generic_analytical_fallbacks():
    from stock_analyser.adaptive_valuation import fcff_bridge_diagnostic
    diag = fcff_bridge_diagnostic(
        {"revenue": 100.0, "operating_income": 20.0, "tax_rate": 0.20, "fcf": 8.0},
        {
            "da_pct_start": 0.04,
            "capex_pct_start": 0.05,
            "nwc_pct_start": 0.0,
            "other_noncash_pct_start": 0.0,
            "sources": {
                "da_pct_start": "Analytical fallback",
                "capex_pct_start": "Analytical fallback",
                "nwc_pct_start": "Analytical fallback",
                "other_noncash_pct_start": "Analytical fallback: no reconciled operating cash-flow bridge available",
            },
        },
    )
    assert diag["available"] is False
    assert "fallback" in diag["reason"].lower()


def test_adaptive_assumptions_use_reconciled_current_fundamentals_not_raw_fiscal_current():
    from stock_analyser.adaptive_valuation import build_adaptive_assumptions
    # Deliberately give Fiscal a nonsensical current CapEx ratio. The canonical
    # fundamentals have already been reconciled to 10%; that is what the DCF must use.
    current_income = {"data": [
        {"metricId": "revenue", "metricName": "Revenue", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 100.0},
        {"metricId": "operating_income", "metricName": "Operating Income", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": 25.0},
    ]}
    current_cash = {"data": [
        {"metricId": "capex", "metricName": "Capital Expenditures", "periodType": "ltm", "periodEndDate": "2026-06-30", "value": -50.0},
    ]}
    annual_cash = {"data": [
        {"metricId": "capex", "metricName": "Capital Expenditures", "periodType": "annual", "periodEndDate": "2025-12-31", "value": -12.0},
        {"metricId": "capex", "metricName": "Capital Expenditures", "periodType": "annual", "periodEndDate": "2024-12-31", "value": -10.0},
    ]}
    bundle = _bundle(financials={
        "income-statement": {"current": current_income, "annual": {}, "current_period": "ltm"},
        "balance-sheet": {"current": {}, "annual": {}, "current_period": "latest"},
        "cash-flow-statement": {"current": current_cash, "annual": annual_cash, "current_period": "ltm"},
    })
    data = SimpleNamespace(info={"sector": "Communication Services", "industry": "Interactive Media & Services", "beta": 1.0}, risk_free_rate=0.04)
    fundamentals = {
        "revenue": 100.0,
        "operating_margin": 0.25,
        "tax_rate": 0.20,
        "da_pct_revenue": 0.08,
        "capex_pct_revenue": 0.10,
        "nwc_investment_pct_revenue": 0.01,
        "other_noncash_pct_revenue": 0.05,
        "historical_capex_pct_revenue": 0.11,
        "historical_da_pct_revenue": 0.08,
        "historical_nwc_investment_pct_revenue": 0.01,
        "historical_other_noncash_pct_revenue": 0.04,
        "historical_operating_margin": 0.24,
        "revenue_cagr_3y": 0.15,
        "debt": 5.0,
        "interest_expense": 0.25,
        "_sources": {
            "revenue": "Reconciled Yahoo/Fiscal LTM",
            "operating_margin": "Reconciled Yahoo/Fiscal LTM",
            "da_pct_revenue": "Reconciled Yahoo TTM",
            "capex_pct_revenue": "Reconciled Yahoo TTM",
            "nwc_investment_pct_revenue": "Reconciled Yahoo TTM",
            "other_noncash_pct_revenue": "TTM FCFF reconciliation residual",
        },
    }
    assumptions = build_adaptive_assumptions(data, fundamentals, {"revenue_forward_growth": 0.12}, {"market_cap": 1000.0}, bundle)
    assert math.isclose(assumptions["capex_pct_start"], 0.10)
    assert assumptions["sources"]["capex_pct_start"] == "Reconciled Yahoo TTM"
    # Fiscal annual history remains useful for normalization, but must not overwrite
    # the reconciled current-period driver.
    assert assumptions["capex_pct_end"] >= 0


def test_meta_like_fcff_scenarios_have_sane_ordering_and_nontrivial_value():
    base_kwargs = dict(
        revenue=228.25,
        growth_start=0.20,
        growth_end=0.08,
        margin_start=0.39,
        margin_end=0.36,
        tax_rate=0.22,
        da_pct_start=0.14,
        da_pct_end=0.12,
        capex_pct_start=0.34,
        capex_pct_end=0.20,
        nwc_pct_start=0.01,
        nwc_pct_end=0.01,
        other_noncash_pct_start=0.16,
        other_noncash_pct_end=0.10,
        wacc=0.09,
        terminal_growth=0.03,
        net_debt=-20.0,
        shares=2.55,
    )
    base = explicit_fcff_dcf(**base_kwargs)
    bear = explicit_fcff_dcf(**{**base_kwargs, "growth_start": 0.17, "growth_end": 0.06, "margin_start": 0.37, "margin_end": 0.34, "capex_pct_end": 0.22})
    bull = explicit_fcff_dcf(**{**base_kwargs, "growth_start": 0.23, "growth_end": 0.10, "margin_start": 0.41, "margin_end": 0.38, "capex_pct_end": 0.18})
    assert bear["fair_value"] < base["fair_value"] < bull["fair_value"]
    assert base["fair_value"] > 100.0


def test_meta_like_end_to_end_dcf_no_longer_collapses_from_period_or_sbc_bridge_errors():
    import pandas as pd
    from stock_analyser.analysis import build_analysis
    from stock_analyser.models import StockData

    ttm_col = pd.Timestamp("2026-06-30")
    annual_cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]
    ttm_income = pd.DataFrame({ttm_col: {
        "Total Revenue": 228.25e9,
        "Operating Income": 89.47e9,
        "Pretax Income": 91.0e9,
        "Tax Provision": 20.2e9,
        "Net Income": 70.0e9,
        "EBITDA": 120.0e9,
        "Interest Expense": 1.0e9,
    }})
    ttm_cash = pd.DataFrame({ttm_col: {
        "Free Cash Flow": 40.98e9,
        "Operating Cash Flow": 118.98e9,
        "Depreciation And Amortization": 31.0e9,
        "Capital Expenditure": -78.0e9,
        "Change In Working Capital": -2.28e9,
        "Stock Based Compensation": 20.0e9,
    }})
    income = pd.DataFrame({
        annual_cols[0]: {"Total Revenue": 200e9, "Operating Income": 76e9, "Pretax Income": 78e9, "Tax Provision": 16e9, "Net Income": 60e9, "EBITDA": 100e9, "Interest Expense": 0.9e9},
        annual_cols[1]: {"Total Revenue": 165e9, "Operating Income": 62e9, "Pretax Income": 64e9, "Tax Provision": 13e9, "Net Income": 49e9, "EBITDA": 84e9, "Interest Expense": 0.8e9},
        annual_cols[2]: {"Total Revenue": 135e9, "Operating Income": 47e9, "Pretax Income": 49e9, "Tax Provision": 10e9, "Net Income": 38e9, "EBITDA": 67e9, "Interest Expense": 0.7e9},
    })
    cashflow = pd.DataFrame({
        annual_cols[0]: {"Free Cash Flow": 45e9, "Depreciation And Amortization": 25e9, "Capital Expenditure": -50e9, "Change In Working Capital": -1e9, "Stock Based Compensation": 18e9},
        annual_cols[1]: {"Free Cash Flow": 43e9, "Depreciation And Amortization": 21e9, "Capital Expenditure": -39e9, "Change In Working Capital": -1e9, "Stock Based Compensation": 15e9},
        annual_cols[2]: {"Free Cash Flow": 34e9, "Depreciation And Amortization": 18e9, "Capital Expenditure": -32e9, "Change In Working Capital": -0.5e9, "Stock Based Compensation": 13e9},
    })
    balance = pd.DataFrame({annual_cols[0]: {
        "Total Assets": 350e9, "Stockholders Equity": 190e9,
        "Cash Cash Equivalents And Short Term Investments": 90e9,
        "Total Debt": 22e9, "Current Assets": 120e9, "Current Liabilities": 40e9,
    }})
    idx = pd.date_range("2025-01-01", periods=420, freq="D")
    history = pd.DataFrame({"Close": np.linspace(450.0, 570.0, len(idx)), "Volume": 18_000_000}, index=idx)
    data = StockData(
        ticker="META",
        info={
            "longName": "Meta Platforms, Inc.", "sector": "Communication Services", "industry": "Internet Content & Information",
            "marketCap": 1.45e12, "enterpriseValue": 1.47e12, "sharesOutstanding": 2.55e9,
            "totalCash": 90e9, "totalDebt": 22e9, "beta": 1.20,
            "forwardPE": 18.0, "revenueGrowth": 0.20, "freeCashflow": 40.98e9,
        },
        risk_free_rate=0.047,
        history=history,
        income=income,
        ttm_income=ttm_income,
        balance=balance,
        cashflow=cashflow,
        ttm_cashflow=ttm_cash,
    )
    analysis = build_analysis(data)
    assert analysis["dcf_available"] is True
    assert analysis["fcff_bridge"]["pass"] is True
    fair = analysis["dcf"]["fair_value"]
    assert 200.0 < fair < 900.0
    assert analysis["scenarios"]["Bear"]["fair_value"] < analysis["scenarios"]["Base"]["fair_value"] < analysis["scenarios"]["Bull"]["fair_value"]
    # SBC should remain an economic expense; the residual bridge should not simply
    # add the full stock-comp add-back into forecast FCFF.
    assert abs(analysis["assumptions"]["other_noncash_pct_start"]) < 0.10
