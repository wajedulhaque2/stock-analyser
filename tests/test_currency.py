import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Avoid requiring live network or yfinance internals for this pure transform.
import types
sys.modules.setdefault("yfinance", types.SimpleNamespace())
from stock_analyser.yahoo_data import normalize_price_currency


def test_gbp_pence_normalization():
    h = pd.DataFrame({"Close": [1450.0, 1500.0], "Open": [1440.0, 1490.0], "Volume": [100, 200]})
    out, scale, cur = normalize_price_currency(h, "GBp", "GBP")
    assert scale == 0.01
    assert cur == "GBP"
    assert out["Close"].iloc[-1] == 15.0
    assert out["Volume"].iloc[-1] == 200


def test_usd_no_scale():
    h = pd.DataFrame({"Close": [100.0]})
    out, scale, cur = normalize_price_currency(h, "USD", "USD")
    assert scale == 1.0
    assert cur == "USD"
    assert out["Close"].iloc[-1] == 100.0
