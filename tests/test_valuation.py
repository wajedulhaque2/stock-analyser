import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.valuation import dcf_model, reverse_dcf_growth


def test_dcf_positive_and_consistent():
    result = dcf_model(
        revenue=1000,
        operating_margin=0.20,
        tax_rate=0.21,
        growth=0.08,
        wacc=0.09,
        terminal_growth=0.025,
        net_debt=100,
        shares=100,
        fcf_conversion=0.75,
    )
    assert result["fair_value"] > 0
    assert 0 < result["pv_terminal_share"] < 1
    assert len(result["forecast"]) == 5


def test_reverse_dcf_recovers_growth():
    target = dcf_model(1000, 0.20, 0.21, 0.075, 0.09, 0.025, 100, 100, 0.75)["fair_value"]
    implied = reverse_dcf_growth(target, 1000, 0.20, 0.21, 0.09, 0.025, 100, 100, 0.75)
    assert math.isclose(implied, 0.075, rel_tol=1e-4, abs_tol=1e-4)


def test_invalid_wacc_terminal_returns_empty():
    assert dcf_model(1000, .2, .21, .05, .02, .03, 0, 100) == {}
