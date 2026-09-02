import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.analysis import build_analysis
from stock_analyser.fundamentals import compute_fundamentals
from stock_analyser.models import StockData
from stock_analyser.valuation import dcf_model


def _history(price=100.0):
    idx = pd.bdate_range("2025-08-01", periods=260)
    return pd.DataFrame({"Close": np.full(len(idx), price), "Volume": np.full(len(idx), 2_000_000)}, index=idx)


def test_fading_growth_dcf_is_more_conservative_than_flat_high_growth():
    flat = dcf_model(1000, 0.25, 0.21, 0.20, 0.095, 0.025, 0, 100, 0.70)
    fade = dcf_model(1000, 0.25, 0.21, 0.20, 0.095, 0.025, 0, 100, 0.70, end_growth=0.08)
    assert fade["fair_value"] < flat["fair_value"]
    assert np.isclose(fade["forecast"].iloc[0]["growth"], 0.20)
    assert np.isclose(fade["forecast"].iloc[-1]["growth"], 0.08)


def test_operating_scenarios_hold_discount_assumptions_constant():
    data = StockData(
        ticker="CASE",
        info={
            "totalRevenue": 1_000.0,
            "operatingMargins": 0.25,
            "freeCashflow": 120.0,
            "totalCash": 50.0,
            "totalDebt": 20.0,
            "marketCap": 10_000.0,
            "enterpriseValue": 9_970.0,
            "sharesOutstanding": 100.0,
            "forwardPE": 20.0,
            "revenueGrowth": 0.16,
        },
        history=_history(),
    )
    analysis = build_analysis(data)
    cases = analysis["scenarios"]
    assert cases["Bear"]["wacc"] == cases["Base"]["wacc"] == cases["Bull"]["wacc"]
    assert cases["Bear"]["terminal_growth"] == cases["Base"]["terminal_growth"] == cases["Bull"]["terminal_growth"]
    assert cases["Bear"]["growth"] < cases["Base"]["growth"] < cases["Bull"]["growth"]


def test_latest_quarter_balance_enables_roic_and_recent_operating_checks():
    qdate = pd.Timestamp("2026-06-30")
    quarterly_balance = pd.DataFrame(
        {qdate: [500.0, 240.0, 80.0, 40.0, 150.0, 90.0]},
        index=["Total Assets", "Stockholders Equity", "Cash And Short Term Investments", "Total Debt", "Current Assets", "Current Liabilities"],
    )
    quarterly_income = pd.DataFrame(
        {
            pd.Timestamp("2026-06-30"): [120.0, 36.0, 28.0],
            pd.Timestamp("2025-06-30"): [100.0, 25.0, 20.0],
        },
        index=["Total Revenue", "Operating Income", "Net Income"],
    )
    f = compute_fundamentals(
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        info={"totalRevenue": 450.0, "operatingMargins": 0.28, "freeCashflow": 70.0, "totalCash": 80.0, "totalDebt": 40.0},
        quarterly_balance=quarterly_balance,
        quarterly_income=quarterly_income,
    )
    assert np.isfinite(f["roic"])
    assert np.isclose(f["latest_quarter_operating_margin"], 0.30)
    assert np.isclose(f["latest_quarter_revenue_yoy"], 0.20)


def test_dated_valuation_history_can_override_generic_forward_pe():
    vh = pd.DataFrame(
        {
            pd.Timestamp("2026-08-21"): [18.4],
            pd.Timestamp("2026-05-21"): [21.0],
        },
        index=["Forward P/E"],
    )
    data = StockData(
        ticker="MULT",
        info={
            "totalRevenue": 1_000.0,
            "operatingMargins": 0.25,
            "freeCashflow": 120.0,
            "totalCash": 50.0,
            "totalDebt": 20.0,
            "marketCap": 10_000.0,
            "enterpriseValue": 9_970.0,
            "sharesOutstanding": 100.0,
            "forwardPE": 16.2,
            "revenueGrowth": 0.10,
        },
        history=_history(),
        valuation_history=vh,
    )
    analysis = build_analysis(data)
    assert np.isclose(analysis["valuation"]["forward_pe"], 18.4)
    assert analysis["valuation"]["forward_pe_source"] == "Yahoo dated valuation-measures history"
