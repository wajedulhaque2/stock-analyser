from __future__ import annotations

import numpy as np
import pandas as pd


def _returns(close: pd.Series) -> pd.Series:
    return pd.to_numeric(close, errors="coerce").pct_change().dropna()


def rsi(close: pd.Series, window: int = 14) -> float:
    delta = pd.to_numeric(close, errors="coerce").diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return float(out.iloc[-1]) if len(out.dropna()) else np.nan


def compute_market_metrics(history: pd.DataFrame) -> dict[str, float]:
    if history is None or history.empty or "Close" not in history:
        return {}
    h = history.dropna(subset=["Close"]).copy()
    close = h["Close"].astype(float)
    volume = h["Volume"].astype(float) if "Volume" in h else pd.Series(index=h.index, dtype=float)
    rets = _returns(close)
    current = float(close.iloc[-1])

    def ret_n(n):
        return current / float(close.iloc[-n]) - 1 if len(close) >= n else np.nan

    def ret_calendar(days: int):
        if not isinstance(close.index, pd.DatetimeIndex) or close.empty:
            return np.nan
        target = close.index[-1] - pd.Timedelta(days=days)
        prior = close.loc[close.index <= target]
        if prior.empty:
            # A 260-business-day synthetic/sample history can span just under 365
            # calendar days. Use the earliest observation when the sample is still
            # effectively a full trailing year; otherwise report unavailable.
            span = close.index[-1] - close.index[0]
            if span.days >= 350:
                return current / float(close.iloc[0]) - 1
            return np.nan
        return current / float(prior.iloc[-1]) - 1

    rolling_max = close.cummax()
    drawdown = close / rolling_max - 1
    avg_vol_20 = float(volume.tail(20).mean()) if len(volume.dropna()) else np.nan
    avg_vol_60 = float(volume.tail(60).mean()) if len(volume.dropna()) else np.nan
    current_vol = float(volume.iloc[-1]) if len(volume.dropna()) else np.nan
    adv20 = float((close * volume).tail(20).mean()) if len(volume.dropna()) else np.nan
    low52 = float(close.tail(252).min()) if len(close) else np.nan
    high52 = float(close.tail(252).max()) if len(close) else np.nan
    pos52 = (current - low52) / (high52 - low52) if high52 > low52 else np.nan

    return {
        "current_price": current,
        "return_1w": ret_n(6),
        "return_1m": ret_n(22),
        "return_3m": ret_n(64),
        "return_6m": ret_n(127),
        "return_1y": ret_calendar(365),
        "sma_50": float(close.tail(50).mean()) if len(close) >= 50 else np.nan,
        "sma_200": float(close.tail(200).mean()) if len(close) >= 200 else np.nan,
        "rsi_14": rsi(close),
        "annualized_volatility": float(rets.tail(252).std() * np.sqrt(252)) if len(rets) else np.nan,
        "max_drawdown_5y": float(drawdown.min()) if len(drawdown) else np.nan,
        "avg_volume_20": avg_vol_20,
        "avg_volume_60": avg_vol_60,
        "relative_volume": current_vol / avg_vol_20 if avg_vol_20 and not np.isnan(avg_vol_20) else np.nan,
        "avg_daily_value_20": adv20,
        "low_52w": low52,
        "high_52w": high52,
        "position_52w": pos52,
    }
