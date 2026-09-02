import math
import numpy as np
import pandas as pd

from stock_analyser.adaptive_valuation import explicit_fcff_dcf, historical_multiple_valuation, peer_valuation
from stock_analyser.fiscal_valuation import FiscalValuationBundle
from stock_analyser.fiscal_ai import FiscalCompany


def test_terminal_value_uses_terminal_roic_not_year5_capex_forever():
    common = dict(
        revenue=100.0, growth_start=0.20, growth_end=0.08,
        margin_start=0.35, margin_end=0.32, tax_rate=0.22,
        da_pct_start=0.12, da_pct_end=0.10,
        capex_pct_start=0.35, capex_pct_end=0.24,
        nwc_pct_start=0.01, nwc_pct_end=0.01,
        other_noncash_pct_start=0.10, other_noncash_pct_end=0.06,
        wacc=0.09, terminal_growth=0.03, terminal_roic=0.20,
        net_debt=0.0, shares=1.0,
    )
    result = explicit_fcff_dcf(**common)
    assert result
    assert math.isclose(result['terminal_reinvestment_rate'], 0.15, rel_tol=1e-9)
    assert result['terminal_fcff'] > 0
    # High explicit CapEx can depress Year-5 FCFF without forcing that same capital
    # intensity into perpetuity.
    assert result['fair_value'] > 100.0


def test_higher_terminal_roic_increases_value():
    base = dict(
        revenue=100.0, growth_start=0.10, growth_end=0.05,
        margin_start=0.25, margin_end=0.25, tax_rate=0.22,
        da_pct_start=0.05, da_pct_end=0.05,
        capex_pct_start=0.08, capex_pct_end=0.07,
        nwc_pct_start=0.01, nwc_pct_end=0.01,
        wacc=0.09, terminal_growth=0.025, net_debt=0.0, shares=1.0,
    )
    low = explicit_fcff_dcf(**base, terminal_roic=0.10)['fair_value']
    high = explicit_fcff_dcf(**base, terminal_roic=0.20)['fair_value']
    assert high > low


def test_historical_valuation_falls_back_to_yahoo_when_fiscal_ratio_is_nan():
    company = FiscalCompany(company_key='NASDAQ_TEST', display_name='Test', ticker='TEST')
    bundle = FiscalValuationBundle(
        company=company, profile={}, financials={},
        ratios={'data': [{'ratioId': 'ratio_price_to_earnings', 'periodEndDate': '2026-01-01', 'value': None}]},
        segments_kpis={}, shares={}, peer_ratios=[],
        capabilities={'ratios': True},
    )
    result = historical_multiple_valuation(
        {'median_pe_history': 20.0, 'trailing_eps': 5.0},
        {'net_income': 50.0, 'ebitda': np.nan},
        shares=10.0, net_debt=0.0, fiscal_bundle=bundle,
    )
    assert math.isclose(result['fair_value'], 100.0)


def test_peer_valuation_requires_three_sensible_comparables():
    company = FiscalCompany(company_key='NASDAQ_TEST', display_name='Test', ticker='TEST')
    # Two peers is deliberately insufficient for a decision-driving peer median.
    bundle = FiscalValuationBundle(
        company=company, profile={}, financials={}, ratios={}, segments_kpis={}, shares={},
        peer_ratios=[
            {'ticker': 'A', 'ratio_price_to_earnings': 20.0},
            {'ticker': 'B', 'ratio_price_to_earnings': 25.0},
        ], capabilities={},
    )
    result = peer_valuation(bundle, {'net_income': 100.0}, shares=10.0, net_debt=0.0)
    assert not np.isfinite(result['fair_value'])
