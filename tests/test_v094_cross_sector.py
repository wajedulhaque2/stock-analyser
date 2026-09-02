from types import SimpleNamespace
import math
import numpy as np
import pandas as pd

import stock_analyser.adaptive_valuation as av
from stock_analyser.adaptive_valuation import (
    ForecastFramework,
    build_adaptive_assumptions,
    dynamic_wacc,
    validate_forecast_assumptions,
)
from stock_analyser.valuation import derive_valuation
from stock_analyser.yahoo_data import normalize_price_currency


def _base_fundamentals(**updates):
    base = {
        "revenue": 100e9,
        "revenue_cagr_3y": 0.15,
        "operating_margin": 0.20,
        "historical_operating_margin": 0.18,
        "da_pct_revenue": 0.05,
        "historical_da_pct_revenue": 0.05,
        "capex_pct_revenue": 0.08,
        "historical_capex_pct_revenue": 0.08,
        "nwc_investment_pct_revenue": 0.01,
        "historical_nwc_investment_pct_revenue": 0.01,
        "other_noncash_pct_revenue": 0.01,
        "historical_other_noncash_pct_revenue": 0.01,
        "tax_rate": 0.21,
        "roic": 0.20,
        "debt": 20e9,
        "interest_expense": 1e9,
        "_sources": {},
    }
    base.update(updates)
    return base


def test_semiconductor_high_margin_is_not_rejected_by_generic_ceiling():
    fw = ForecastFramework("Semiconductors", True, "", [], 0.03, 0.18)
    assumptions = {
        "framework": fw,
        "growth_start": 0.44,
        "growth_end": 0.10,
        "margin_start": 0.6402,
        "margin_end": 0.6402,
        "da_pct_start": 0.0127,
        "da_pct_end": 0.0127,
        "capex_pct_end": 0.0264,
        "sources": {},
    }
    result = validate_forecast_assumptions(assumptions)
    assert result["pass"] is True


def test_energy_margin_history_near_zero_is_rejected(monkeypatch):
    monkeypatch.setattr(av, "financial_snapshot", lambda bundle: {
        "historical_operating_margin": 0.0043,
        "historical_da_pct_revenue": 0.1109,
        "historical_capex_pct_revenue": 0.07,
        "historical_nwc_investment_pct_revenue": -0.0002,
        "_history_warnings": [],
    })
    monkeypatch.setattr(av, "kpi_driver_candidates", lambda payload: [])
    bundle = SimpleNamespace(
        capabilities={"financials": True, "segments_and_kpis": False},
        segments_kpis={}, profile={},
    )
    data = SimpleNamespace(
        info={"sector": "Energy", "industry": "Integrated Oil & Gas", "beta": 0.7, "debtToEquity": 35.0},
        risk_free_rate=0.047,
    )
    f = _base_fundamentals(
        operating_margin=0.1279,
        historical_operating_margin=0.0043,
        da_pct_revenue=0.0847,
        historical_da_pct_revenue=0.1109,
        capex_pct_revenue=0.0593,
        historical_capex_pct_revenue=0.07,
        nwc_investment_pct_revenue=0.0219,
        historical_nwc_investment_pct_revenue=-0.0002,
        roic=0.12,
    )
    a = build_adaptive_assumptions(data, f, {"revenue_forward_growth": -0.11}, {"market_cap": 150e9}, bundle)
    assert a["framework"].name == "Energy"
    assert a["margin_end"] > 0.04
    assert not math.isclose(a["margin_end"], 0.0043)
    assert any("falls below" in w and "operating-margin" in w for w in a["assumption_warnings"])


def test_software_da_normalization_does_not_nearly_double(monkeypatch):
    monkeypatch.setattr(av, "financial_snapshot", lambda bundle: {
        "historical_operating_margin": 0.45,
        "historical_da_pct_revenue": 0.2105,
        "historical_capex_pct_revenue": 0.2053,
        "historical_nwc_investment_pct_revenue": 0.013,
        "_history_warnings": [],
    })
    monkeypatch.setattr(av, "kpi_driver_candidates", lambda payload: [])
    bundle = SimpleNamespace(capabilities={"financials": True, "segments_and_kpis": False}, segments_kpis={}, profile={})
    data = SimpleNamespace(
        info={"sector": "Technology", "industry": "Software - Infrastructure", "beta": 0.95, "debtToEquity": 30.0},
        risk_free_rate=0.047,
    )
    f = _base_fundamentals(
        operating_margin=0.4678,
        historical_operating_margin=0.4495,
        da_pct_revenue=0.1161,
        historical_da_pct_revenue=0.2105,
        capex_pct_revenue=0.3494,
        historical_capex_pct_revenue=0.2053,
    )
    a = build_adaptive_assumptions(data, f, {"revenue_forward_growth": 0.195}, {"market_cap": 3e12}, bundle)
    assert a["framework"].name == "Software / digital platform"
    assert a["da_pct_end"] <= 0.1161 * 1.5 + 1e-12
    assert any("D&A/revenue" in w for w in a["assumption_warnings"])


def test_dynamic_wacc_prefers_currency_robust_debt_to_equity_and_floor():
    data = SimpleNamespace(
        info={"beta": 0.15, "debtToEquity": 40.0},
        risk_free_rate=0.047,
    )
    f = {"debt": 100e9, "interest_expense": 1.8e9, "tax_rate": 0.22}
    result = dynamic_wacc(data, f, market_cap=100e9, erp=0.045)
    assert result["capital_weight_source"].startswith("Yahoo debt-to-equity")
    assert math.isclose(result["debt_weight"], 0.4 / 1.4, rel_tol=1e-9)
    assert result["wacc"] >= max(0.047 + 0.005, 0.055)
    assert result["wacc"] >= result["raw_wacc"]


def test_lse_subunit_scale_survives_empty_history_and_scales_fallback_price():
    empty = pd.DataFrame()
    _, scale, currency = normalize_price_currency(empty, "GBp", "GBP")
    assert scale == 0.01
    assert currency == "GBP"
    v = derive_valuation({"currentPrice": 1265.0, "marketCap": 100e9}, {"fcf": np.nan}, {}, price_scale=scale)
    assert math.isclose(v["price"], 12.65, rel_tol=1e-12)


def test_industrial_terminal_roic_is_capped_to_sustainable_framework_level(monkeypatch):
    monkeypatch.setattr(av, "financial_snapshot", lambda bundle: {})
    monkeypatch.setattr(av, "kpi_driver_candidates", lambda payload: [])
    data = SimpleNamespace(
        info={"sector": "Industrials", "industry": "Aerospace & Defense", "beta": 1.0, "debtToEquity": 50.0},
        risk_free_rate=0.047,
    )
    f = _base_fundamentals(roic=0.80)
    a = build_adaptive_assumptions(data, f, {"revenue_forward_growth": 0.10}, {"market_cap": 100e9})
    assert a["framework"].name == "Industrials"
    assert a["terminal_roic"] <= 0.25
