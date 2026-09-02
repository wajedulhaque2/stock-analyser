from types import SimpleNamespace
import math
import numpy as np

import stock_analyser.adaptive_valuation as av
from stock_analyser.adaptive_valuation import (
    extract_management_guidance,
    build_adaptive_assumptions,
    validate_forecast_assumptions,
    valuation_range,
    explicit_fcff_dcf,
)
from stock_analyser.fiscal_ai import FiscalCompany
from stock_analyser.fiscal_valuation import FiscalValuationBundle, _metric_matches


def _transcript(text: str):
    return {
        "speakers": [{"speaker": 1, "name": "CFO", "speakerType": "company_management"}],
        "transcript": {"paragraphs": [{"speaker": 1, "start": 1, "end": 2, "text": text}]},
    }


def test_quarterly_revenue_guide_is_not_misread_as_full_year_revenue():
    text = (
        "We expect third quarter 2026 total revenue to be in the range of $61 billion to $64 billion. "
        "For full-year 2026, we expect total expenses of $165 billion to $169 billion. "
        "We expect full-year 2026 capital expenditures to be $130 billion to $145 billion."
    )
    result = extract_management_guidance(_transcript(text))
    assert "revenue_mid" not in result
    assert math.isclose(result["capex_mid"], 137.5e9)


def test_meta_like_bad_history_and_bad_guidance_are_repaired(monkeypatch):
    # Reproduce the pathological live inputs from the v0.9.2 screenshot.
    fiscal_snapshot = {
        "historical_operating_margin": 0.1146,
        "historical_da_pct_revenue": 0.40,
        "historical_capex_pct_revenue": 0.247,
        "historical_nwc_investment_pct_revenue": -0.0174,
        "_history_warnings": [],
    }
    monkeypatch.setattr(av, "financial_snapshot", lambda bundle: fiscal_snapshot)
    monkeypatch.setattr(av, "kpi_driver_candidates", lambda payload: [])

    company = FiscalCompany(company_key="NASDAQ_META", display_name="Meta Platforms", ticker="META")
    bundle = FiscalValuationBundle(
        company=company, profile={}, financials={"income-statement": {}},
        ratios={}, segments_kpis={}, shares={}, peer_ratios=[],
        capabilities={"financials": True, "segments_and_kpis": False},
    )
    data = SimpleNamespace(
        info={"sector": "Communication Services", "industry": "Internet Content & Information", "beta": 1.24},
        risk_free_rate=0.047,
    )
    fundamentals = {
        "revenue": 228.25e9,
        "revenue_cagr_3y": 0.199,
        "operating_margin": 0.3808,
        "historical_operating_margin": 0.375,
        "da_pct_revenue": 0.0996,
        "historical_da_pct_revenue": 0.105,
        "capex_pct_revenue": 0.3914,
        "historical_capex_pct_revenue": 0.247,
        "nwc_investment_pct_revenue": 0.0339,
        "historical_nwc_investment_pct_revenue": -0.0174,
        "other_noncash_pct_revenue": 0.1057,
        "historical_other_noncash_pct_revenue": 0.0149,
        "tax_rate": 0.222,
        "roic": 0.246,
        "debt": 112e9,
        "interest_expense": 2.0e9,
        "_sources": {},
    }
    # This is the false quarterly-as-annual guidance that previously clipped to -20%.
    bad_guidance = {"revenue_mid": 62.5e9}
    assumptions = build_adaptive_assumptions(
        data, fundamentals,
        earnings={"revenue_forward_growth": 0.201},
        valuation={"market_cap": 1.45e12},
        fiscal_bundle=bundle,
        guidance=bad_guidance,
    )

    assert math.isclose(assumptions["growth_start"], 0.201, rel_tol=1e-9)
    assert 0.03 <= assumptions["growth_end"] <= 0.12
    assert assumptions["growth_end"] > 0
    assert assumptions["margin_end"] > 0.30
    assert assumptions["da_pct_end"] < 0.20
    assert math.isclose(assumptions["capex_pct_end"], 0.247, rel_tol=1e-9)
    assert assumptions["validation"]["pass"] is True
    assert any("Rejected management revenue guidance" in x for x in assumptions["assumption_warnings"])
    assert any("Fiscal.ai annual operating-margin history" in x for x in assumptions["assumption_warnings"])
    assert any("Fiscal.ai annual D&A/revenue history" in x for x in assumptions["assumption_warnings"])

    # The repaired assumptions should no longer mechanically collapse the META-like
    # DCF into the double digits merely because the source layer returned bad
    # normalization values.
    dcf = explicit_fcff_dcf(
        revenue=228.25e9, growth_start=assumptions["growth_start"], growth_end=assumptions["growth_end"],
        margin_start=assumptions["margin_start"], margin_end=assumptions["margin_end"],
        tax_rate=assumptions["tax_rate"], da_pct_start=assumptions["da_pct_start"], da_pct_end=assumptions["da_pct_end"],
        capex_pct_start=assumptions["capex_pct_start"], capex_pct_end=assumptions["capex_pct_end"],
        nwc_pct_start=assumptions["nwc_pct_start"], nwc_pct_end=assumptions["nwc_pct_end"],
        other_noncash_pct_start=assumptions["other_noncash_pct_start"], other_noncash_pct_end=assumptions["other_noncash_pct_end"],
        wacc=assumptions["wacc"], terminal_growth=assumptions["terminal_growth"], terminal_roic=assumptions["terminal_roic"],
        net_debt=22.06e9, shares=2.55e9,
    )
    assert dcf["fair_value"] > 250.0


def test_pathological_automatic_forecast_fails_closed():
    fw = av.ForecastFramework("Software / digital platform", True, "", [], 0.03, 0.20)
    a = {
        "framework": fw,
        "growth_start": -0.20,
        "growth_end": -0.20,
        "margin_start": 0.38,
        "margin_end": 0.1146,
        "da_pct_start": 0.10,
        "da_pct_end": 0.40,
        "capex_pct_end": 0.247,
        "sources": {},
    }
    result = validate_forecast_assumptions(a)
    assert result["pass"] is False
    assert len(result["issues"]) >= 3


def test_extreme_method_dispersion_withholds_central_valuation():
    result = valuation_range(42.0, 825.0, np.nan)
    assert result["resolved"] is False
    assert not np.isfinite(result["central"])
    assert math.isclose(result["central_low"], 42.0)
    assert math.isclose(result["central_high"], 825.0)
    assert result["dispersion"] > 1.0


def test_single_method_is_reference_not_central_target():
    result = valuation_range(np.nan, 825.0, np.nan)
    assert result["resolved"] is False
    assert not np.isfinite(result["central"])
    assert "Only one" in result["reason"]


def test_fiscal_metric_match_does_not_accept_ratio_or_margin_collision():
    row = {"metric_id": "income_statement_operating_income_margin", "metric_name": "Operating Income Margin"}
    assert not _metric_matches(row, ["operating income", "operating profit", "ebit"])
    real = {"metric_id": "income_statement_operating_income", "metric_name": "Operating Income"}
    assert _metric_matches(real, ["operating income", "operating profit", "ebit"])
