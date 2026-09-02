import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.analysis import build_analysis
from stock_analyser.fundamentals import compute_fundamentals, row
from stock_analyser.models import StockData


def _history(price=100.0):
    idx = pd.bdate_range("2025-08-01", periods=260)
    return pd.DataFrame({"Close": np.full(len(idx), price), "Volume": np.full(len(idx), 2_000_000)}, index=idx)


def test_raw_camel_case_yfinance_labels_are_recognised():
    q_income = pd.DataFrame(
        {
            pd.Timestamp("2026-06-30"): [120.0, 36.0, 28.0],
            pd.Timestamp("2025-06-30"): [100.0, 25.0, 20.0],
        },
        index=["TotalRevenue", "OperatingIncome", "NetIncome"],
    )
    q_balance = pd.DataFrame(
        {pd.Timestamp("2026-06-30"): [500.0, 240.0, 80.0, 40.0, 150.0, 90.0]},
        index=["TotalAssets", "StockholdersEquity", "CashAndShortTermInvestments", "TotalDebt", "CurrentAssets", "CurrentLiabilities"],
    )
    q_cash = pd.DataFrame(
        {pd.Timestamp("2026-06-30"): [30.0, -10.0, 20.0]},
        index=["OperatingCashFlow", "CapitalExpenditure", "FreeCashFlow"],
    )
    assert row(q_income, "revenue").iloc[0] == 120.0
    f = compute_fundamentals(
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        info={"totalRevenue": 450.0, "operatingMargins": 0.28, "freeCashflow": 70.0, "totalCash": 80.0, "totalDebt": 40.0},
        quarterly_balance=q_balance,
        quarterly_income=q_income,
        quarterly_cashflow=q_cash,
    )
    assert np.isclose(f["latest_quarter_revenue_yoy"], 0.20)
    assert np.isclose(f["latest_quarter_operating_margin"], 0.30)
    assert np.isclose(f["latest_quarter_fcf_margin"], 20.0 / 120.0)
    assert np.isfinite(f["roic"])


def test_primary_dcf_and_base_scenario_are_identical():
    data = StockData(
        ticker="CONSIST",
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
    assert np.isfinite(analysis["dcf"]["fair_value"])
    assert np.isclose(analysis["dcf"]["fair_value"], analysis["scenarios"]["Base"]["fair_value"])
    assert np.isclose(analysis["dcf"]["forecast"].iloc[-1]["growth"], analysis["assumptions"]["end_growth"])


def test_quarterly_source_status_is_explicit():
    data = StockData(
        ticker="STATUS",
        info={
            "totalRevenue": 1_000.0,
            "operatingMargins": 0.25,
            "freeCashflow": 120.0,
            "totalCash": 50.0,
            "totalDebt": 20.0,
            "marketCap": 10_000.0,
            "enterpriseValue": 9_970.0,
            "sharesOutstanding": 100.0,
            "revenueGrowth": 0.10,
        },
        history=_history(),
    )
    analysis = build_analysis(data)
    assert analysis["quarterly_data_status"] == {
        "Income statement": "Missing",
        "Cash-flow statement": "Missing",
        "Balance sheet": "Missing",
    }
