import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.analysis import build_analysis
from stock_analyser.fundamentals import compute_fundamentals
from stock_analyser.models import StockData


def _history(price=100.0):
    idx = pd.bdate_range("2025-08-01", periods=260)
    return pd.DataFrame({"Close": np.full(len(idx), price), "Volume": np.full(len(idx), 2_000_000)}, index=idx)


def test_observed_fcf_conversion_is_derived_from_nopat():
    info = {
        "totalRevenue": 200.0,
        "operatingMargins": 0.25,
        "freeCashflow": 35.0,
    }
    f = compute_fundamentals(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), info=info)
    expected_nopat = 200.0 * 0.25 * (1 - 0.21)
    assert np.isclose(f["fcf_conversion_observed"], 35.0 / expected_nopat)
    assert "FCF" in f["_sources"]["fcf_conversion_observed"]


def test_market_cap_implied_shares_override_materially_inconsistent_direct_field():
    data = StockData(
        ticker="DUAL",
        info={
            "totalRevenue": 1_000.0,
            "operatingMargins": 0.20,
            "freeCashflow": 120.0,
            "totalCash": 50.0,
            "totalDebt": 20.0,
            "marketCap": 10_000.0,
            "enterpriseValue": 9_970.0,
            "sharesOutstanding": 80.0,
            "forwardPE": 20.0,
            "revenueGrowth": 0.10,
        },
        history=_history(100.0),
    )
    analysis = build_analysis(data)
    assert np.isclose(analysis["shares"], 100.0)
    shares_row = next(r for r in analysis["valuation_status"] if r["Input"] == "Diluted / outstanding shares")
    assert "market capitalisation / current price" in shares_row["Source"]
    assert "differs by" in shares_row["Note"]
    assert analysis["model_confidence"] in {"MEDIUM-HIGH", "MEDIUM"}


def test_dcf_default_cash_conversion_uses_observed_company_cash_conversion():
    data = StockData(
        ticker="CASH",
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
            "revenueGrowth": 0.10,
        },
        history=_history(100.0),
    )
    analysis = build_analysis(data)
    expected = 120.0 / (1_000.0 * 0.25 * (1 - 0.21))
    assert np.isclose(analysis["assumptions"]["fcf_conversion"], expected)
