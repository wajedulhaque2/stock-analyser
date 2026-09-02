from __future__ import annotations

import numpy as np
import pandas as pd


def _at(df: pd.DataFrame, idx: str, col: str):
    try:
        value = df.loc[idx, col]
        return float(value) if pd.notna(value) else np.nan
    except Exception:
        return np.nan


def compute_earnings_metrics(
    history: pd.DataFrame,
    eps_trend: pd.DataFrame,
    eps_revisions: pd.DataFrame,
    earnings_estimate: pd.DataFrame,
    revenue_estimate: pd.DataFrame,
) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(history, pd.DataFrame) and not history.empty:
        h = history.copy()
        if "surprisePercent" in h:
            s = pd.to_numeric(h["surprisePercent"], errors="coerce").dropna().tail(8)
            out["avg_eps_surprise_8q"] = float(s.mean()) if len(s) else np.nan
            out["beat_rate_8q"] = float((s > 0).mean()) if len(s) else np.nan
            out["last_eps_surprise"] = float(s.iloc[-1]) if len(s) else np.nan

    current = _at(eps_trend, "0y", "current")
    ago30 = _at(eps_trend, "0y", "30daysAgo")
    out["eps_revision_30d"] = (current / ago30 - 1) if ago30 and not np.isnan(ago30) else np.nan
    out["eps_estimate_current_year"] = _at(earnings_estimate, "0y", "avg")
    out["eps_estimate_next_year"] = _at(earnings_estimate, "+1y", "avg")
    out["eps_forward_growth"] = _at(earnings_estimate, "+1y", "growth")
    out["revenue_forward_growth"] = _at(revenue_estimate, "+1y", "growth")

    ups = _at(eps_revisions, "0y", "upLast30days")
    downs = _at(eps_revisions, "0y", "downLast30days")
    if not np.isnan(ups) or not np.isnan(downs):
        ups = 0 if np.isnan(ups) else ups
        downs = 0 if np.isnan(downs) else downs
        out["revision_breadth_30d"] = ups - downs
    return out
