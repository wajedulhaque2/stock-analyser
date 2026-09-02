import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.fundamentals import compute_fundamentals


def _df(rows):
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31")]
    return pd.DataFrame(rows, index=cols).T


def test_fundamental_ratios():
    income = _df({
        "Total Revenue": [130, 120, 110, 100],
        "Gross Profit": [65, 58, 51, 45],
        "Operating Income": [26, 22, 18, 15],
        "Net Income": [18, 15, 12, 10],
        "Pretax Income": [24, 20, 16, 13],
        "Tax Provision": [5, 4, 3, 2.5],
    })
    balance = _df({
        "Total Assets": [250, 235, 220, 200],
        "Stockholders Equity": [120, 110, 100, 90],
        "Cash And Cash Equivalents": [25, 22, 20, 18],
        "Total Debt": [45, 48, 50, 52],
        "Current Assets": [90, 85, 80, 75],
        "Current Liabilities": [60, 58, 55, 53],
        "Inventory": [15, 14, 13, 12],
    })
    cashflow = _df({
        "Operating Cash Flow": [24, 20, 17, 14],
        "Capital Expenditure": [-6, -5, -5, -4],
        "Free Cash Flow": [18, 15, 12, 10],
    })
    f = compute_fundamentals(income, balance, cashflow)
    assert round(f["gross_margin"], 3) == 0.5
    assert round(f["operating_margin"], 3) == 0.2
    assert round(f["current_ratio"], 2) == 1.5
    assert f["revenue_cagr_3y"] > 0
