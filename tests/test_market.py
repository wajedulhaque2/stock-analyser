import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_analyser.market import compute_market_metrics


def test_market_metrics_basic():
    idx = pd.bdate_range("2025-01-01", periods=260)
    close = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    volume = pd.Series(np.full(len(idx), 1_000_000), index=idx)
    df = pd.DataFrame({"Close": close, "Volume": volume})
    m = compute_market_metrics(df)
    assert m["current_price"] == 130
    assert m["return_1y"] > 0
    assert m["avg_daily_value_20"] > 100_000_000
    assert m["relative_volume"] == 1
