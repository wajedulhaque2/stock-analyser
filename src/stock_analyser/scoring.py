from __future__ import annotations

import numpy as np


def _band(value, bands):
    try:
        v = float(value)
        if not np.isfinite(v):
            return np.nan
    except Exception:
        return np.nan
    score = bands[-1][1]
    for threshold, candidate in bands:
        if v <= threshold:
            score = candidate
            break
    return float(score)


def _trend_score(price, ma, above=82.0, below=38.0):
    try:
        p, m = float(price), float(ma)
        if not np.isfinite(p) or not np.isfinite(m):
            return np.nan
        return above if p > m else below
    except Exception:
        return np.nan


def _component(parts: list[float]) -> tuple[float, float]:
    vals = [float(x) for x in parts if x is not None and np.isfinite(x)]
    coverage = len(vals) / len(parts) if parts else 0.0
    return (float(np.mean(vals)) if vals else np.nan, coverage)


def quality_score(f):
    return _component([
        _band(f.get("roic"), [(0, 15), (0.08, 40), (0.15, 65), (0.25, 85), (9, 95)]),
        _band(f.get("operating_margin"), [(0, 15), (0.08, 45), (0.18, 70), (0.30, 90), (9, 95)]),
        _band(f.get("fcf_margin"), [(0, 20), (0.05, 45), (0.12, 70), (0.20, 90), (9, 95)]),
        _band(f.get("cfo_to_net_income"), [(0.5, 20), (0.8, 50), (1.1, 80), (9, 90)]),
    ])


def growth_score(f, e):
    return _component([
        _band(f.get("revenue_cagr_3y"), [(-0.05, 15), (0, 35), (0.05, 55), (0.12, 80), (9, 95)]),
        _band(f.get("net_income_cagr_3y"), [(-0.05, 15), (0, 35), (0.08, 60), (0.18, 85), (9, 95)]),
        _band(e.get("eps_forward_growth"), [(0, 25), (0.05, 45), (0.12, 70), (0.25, 90), (9, 95)]),
    ])


def health_score(f):
    return _component([
        _band(f.get("current_ratio"), [(0.7, 20), (1.0, 45), (1.5, 75), (3, 90), (99, 75)]),
        _band(f.get("debt_to_equity"), [(0.3, 90), (0.8, 75), (1.5, 50), (3, 25), (99, 10)]),
        _band(f.get("cfo_to_net_income"), [(0.5, 20), (0.8, 50), (1.1, 80), (99, 90)]),
    ])


def earnings_score(e):
    return _component([
        _band(e.get("avg_eps_surprise_8q"), [(-0.05, 20), (0, 45), (0.05, 70), (0.15, 90), (9, 95)]),
        _band(e.get("beat_rate_8q"), [(0.25, 20), (0.5, 50), (0.75, 80), (1.0, 95)]),
        _band(e.get("eps_revision_30d"), [(-0.05, 20), (0, 45), (0.03, 70), (0.10, 90), (9, 95)]),
    ])


def valuation_score(v, dcf_upside=np.nan):
    return _component([
        _band(v.get("forward_pe"), [(10, 95), (18, 80), (25, 65), (35, 45), (60, 25), (999, 15)]),
        _band(v.get("fcf_yield"), [(0, 20), (0.02, 35), (0.04, 55), (0.07, 80), (9, 95)]),
        _band(dcf_upside, [(-0.30, 15), (-0.10, 30), (0.10, 55), (0.25, 80), (9, 95)]),
    ])


def market_score(m):
    return _component([
        _trend_score(m.get("current_price"), m.get("sma_50"), 80, 40),
        _trend_score(m.get("current_price"), m.get("sma_200"), 85, 35),
        _band(m.get("avg_daily_value_20"), [(1e6, 20), (5e6, 40), (25e6, 65), (100e6, 85), (1e15, 95)]),
        _band(m.get("annualized_volatility"), [(0.20, 90), (0.35, 75), (0.50, 55), (0.80, 30), (9, 15)]),
    ])


def aggregate_scores(f, e, v, m, dcf_upside=np.nan):
    raw = {
        "Quality": quality_score(f),
        "Growth": growth_score(f, e),
        "Valuation": valuation_score(v, dcf_upside),
        "Earnings": earnings_score(e),
        "Financial health": health_score(f),
        "Market / liquidity": market_score(m),
    }
    weights = {
        "Valuation": 0.30,
        "Quality": 0.20,
        "Growth": 0.15,
        "Earnings": 0.15,
        "Financial health": 0.10,
        "Market / liquidity": 0.10,
    }
    scores = {k: score for k, (score, coverage) in raw.items()}
    component_coverage = {k: coverage for k, (score, coverage) in raw.items()}

    effective = {
        k: weights[k] * component_coverage[k]
        for k in weights
        if np.isfinite(scores.get(k, np.nan)) and component_coverage[k] > 0
    }
    available_weight = sum(effective.values())
    overall = (
        sum(scores[k] * effective[k] for k in effective) / available_weight
        if available_weight > 0 else np.nan
    )
    return scores, float(overall) if np.isfinite(overall) else np.nan, float(available_weight), component_coverage


def confidence_label(coverage: float, dcf_available: bool = True) -> str:
    try:
        c = float(coverage)
    except Exception:
        return "VERY LOW"
    if c >= 0.88:
        label = "HIGH"
    elif c >= 0.74:
        label = "MEDIUM-HIGH"
    elif c >= 0.58:
        label = "MEDIUM"
    elif c >= 0.42:
        label = "LOW"
    else:
        label = "VERY LOW"
    if not dcf_available and label in {"HIGH", "MEDIUM-HIGH"}:
        return "MEDIUM"
    return label


def research_view(score: float, coverage: float = 1.0) -> str:
    if not np.isfinite(score) or coverage < 0.40:
        return "PRELIMINARY"
    if score >= 80:
        return "ATTRACTIVE"
    if score >= 65:
        return "POSITIVE"
    if score >= 50:
        return "NEUTRAL"
    if score >= 35:
        return "WEAK"
    return "UNATTRACTIVE"


def valuation_view(upside: float) -> str:
    if not np.isfinite(upside):
        return "INSUFFICIENT DATA"
    if upside >= 0.30:
        return "SIGNIFICANTLY UNDERVALUED"
    if upside >= 0.10:
        return "MODERATELY UNDERVALUED"
    if upside > -0.10:
        return "FAIRLY VALUED"
    if upside > -0.30:
        return "MODERATELY OVERVALUED"
    return "SIGNIFICANTLY OVERVALUED"
