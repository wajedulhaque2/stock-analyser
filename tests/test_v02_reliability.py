import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.analysis import build_analysis
from stock_analyser.fundamentals import compute_fundamentals
from stock_analyser.models import StockData
from stock_analyser.scoring import aggregate_scores


def test_fundamentals_fall_back_to_yahoo_info():
    info = {
        "totalRevenue": 200.0,
        "operatingMargins": 0.25,
        "grossMargins": 0.70,
        "profitMargins": 0.20,
        "totalCash": 30.0,
        "totalDebt": 10.0,
        "freeCashflow": 35.0,
        "operatingCashflow": 45.0,
        "currentRatio": 1.5,
        "quickRatio": 1.3,
        "returnOnAssets": 0.18,
        "returnOnEquity": 0.28,
        "debtToEquity": 15.0,
        "revenueGrowth": 0.12,
        "earningsGrowth": 0.20,
    }
    f = compute_fundamentals(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), info=info)
    assert f["revenue"] == 200.0
    assert f["operating_margin"] == 0.25
    assert f["net_debt"] == -20.0
    assert f["fcf"] == 35.0
    assert np.isclose(f["debt_to_equity"], 0.15)
    assert "company statistics" in f["_sources"]["revenue"].lower()


def test_ttm_statement_is_preferred_over_info_and_annual():
    col = [pd.Timestamp("2026-06-30")]
    ttm_income = pd.DataFrame({col[0]: [220.0, 55.0]}, index=["Total Revenue", "Operating Income"])
    annual = pd.DataFrame({pd.Timestamp("2025-12-31"): [180.0, 36.0]}, index=["Total Revenue", "Operating Income"])
    info = {"totalRevenue": 200.0, "operatingMargins": 0.22}
    f = compute_fundamentals(annual, pd.DataFrame(), pd.DataFrame(), info=info, ttm_income=ttm_income)
    assert f["revenue"] == 220.0
    assert np.isclose(f["operating_margin"], 0.25)
    assert "TTM income" in f["_sources"]["revenue"]


def test_analysis_can_build_dcf_from_info_and_derived_shares():
    idx = pd.bdate_range("2025-08-01", periods=260)
    history = pd.DataFrame({"Close": np.full(len(idx), 100.0), "Volume": np.full(len(idx), 2_000_000)}, index=idx)
    data = StockData(
        ticker="TEST",
        info={
            "totalRevenue": 1_000.0,
            "operatingMargins": 0.20,
            "profitMargins": 0.15,
            "grossMargins": 0.50,
            "freeCashflow": 150.0,
            "operatingCashflow": 180.0,
            "totalCash": 50.0,
            "totalDebt": 20.0,
            "marketCap": 10_000.0,
            "enterpriseValue": 9_970.0,
            "forwardPE": 20.0,
            "currentRatio": 1.5,
            "quickRatio": 1.3,
            "returnOnAssets": 0.15,
            "returnOnEquity": 0.25,
            "revenueGrowth": 0.10,
        },
        history=history,
    )
    analysis = build_analysis(data)
    assert analysis["dcf_available"] is True
    assert analysis["valuation_blockers"] == []
    assert np.isclose(analysis["shares"], 100.0)
    assert np.isclose(analysis["net_debt_for_dcf"], -30.0)
    assert np.isfinite(analysis["dcf"]["fair_value"])


def test_missing_metrics_do_not_receive_neutral_scores():
    scores, overall, coverage, component_coverage = aggregate_scores({}, {}, {}, {})
    assert np.isnan(overall)
    assert coverage == 0.0
    assert all(np.isnan(x) for x in scores.values())
