from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

BG = "#f3f1ec"
TEXT = "#202624"
GRID = "#d7d2c8"
ACCENT = "#1f4e5f"
NEG = "#8b3a3a"
POS = "#356247"
MUTED = "#6e716f"


def _layout(fig, title, ytitle=None, height=390, margin=None):
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "font": {"size": 17}},
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font={"family": "Arial, Helvetica Neue, sans-serif", "color": TEXT, "size": 12},
        margin=margin or {"l": 55, "r": 20, "t": 55, "b": 40},
        height=height,
        hovermode="x unified",
        showlegend=True,
        legend={"orientation": "h", "y": 1.08, "x": 1, "xanchor": "right"},
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, zeroline=False, title=ytitle)
    return fig


def price_chart(history: pd.DataFrame, currency: str | None = None):
    h = history.copy().dropna(subset=["Close"])
    close = h["Close"].astype(float)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=h.index, y=close, name="Price", line={"color": ACCENT, "width": 2}))
    if len(close) >= 50:
        fig.add_trace(go.Scatter(x=h.index, y=close.rolling(50).mean(), name="50D MA", line={"color": MUTED, "width": 1}))
    if len(close) >= 200:
        fig.add_trace(go.Scatter(x=h.index, y=close.rolling(200).mean(), name="200D MA", line={"color": NEG, "width": 1}))
    ylabel = f"Price ({currency})" if currency else "Price"
    return _layout(fig, "Price history", ylabel)


def revenue_chart(income: pd.DataFrame, row_getter):
    s = row_getter(income, "revenue")
    if s.empty:
        return None
    s = s.sort_index()
    labels = [pd.Timestamp(x).strftime("%Y") if not isinstance(x, str) else str(x) for x in s.index]
    fig = go.Figure(go.Bar(x=labels, y=s.values / 1e9, marker_color=ACCENT, name="Revenue"))
    return _layout(fig, "Revenue history", "Billions")


def earnings_surprise_chart(df: pd.DataFrame):
    if df is None or df.empty or "surprisePercent" not in df:
        return None
    h = df.sort_index() if hasattr(df.index, "dtype") else df
    s = pd.to_numeric(h["surprisePercent"], errors="coerce").dropna().tail(8)
    if s.empty:
        return None
    colors = [POS if x >= 0 else NEG for x in s.values]
    fig = go.Figure(go.Bar(x=s.index, y=s.values * 100, marker_color=colors, name="EPS surprise"))
    return _layout(fig, "EPS surprise history", "Surprise %")


def score_chart(scores: dict[str, float]):
    clean = [(k, float(v)) for k, v in scores.items() if v is not None and np.isfinite(v)]
    labels = [k for k, _ in clean]
    values = [v for _, v in clean]
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker_color=ACCENT, name="Score"))
    fig.update_xaxes(range=[0, 100], title=None)
    fig.update_yaxes(title=None, automargin=True)
    return _layout(fig, "Research scorecard", None, height=350, margin={"l": 155, "r": 20, "t": 55, "b": 40})


def dcf_sensitivity(revenue, margin, tax_rate, growth, end_growth, net_debt, shares, fcf_conversion, end_fcf_conversion, wacc_center, terminal_center, dcf_func):
    waccs = np.array([wacc_center - .02, wacc_center - .01, wacc_center, wacc_center + .01, wacc_center + .02])
    tgs = np.array([terminal_center - .01, terminal_center - .005, terminal_center, terminal_center + .005, terminal_center + .01])
    z = []
    for tg in tgs:
        row = []
        for w in waccs:
            if w <= tg:
                row.append(np.nan)
            else:
                row.append(dcf_func(revenue, margin, tax_rate, growth, w, tg, net_debt, shares, fcf_conversion, end_growth=end_growth, end_fcf_conversion=end_fcf_conversion).get("fair_value", np.nan))
        z.append(row)
    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[f"{x:.1%}" for x in waccs],
        y=[f"{x:.1%}" for x in tgs],
        colorscale=[[0, "#ded9cf"], [0.5, "#8fa6a9"], [1, "#1f4e5f"]],
        colorbar={"title": "Value"},
        text=[[f"{v:.2f}" if np.isfinite(v) else "N/A" for v in row] for row in z],
        texttemplate="%{text}",
        hovertemplate="WACC %{x}<br>Terminal growth %{y}<br>Fair value %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(xaxis_title="WACC", yaxis_title="Terminal growth")
    return _layout(fig, "DCF sensitivity", height=420)


def fcff_sensitivity(dcf_kwargs: dict, wacc_center: float, terminal_center: float, dcf_func):
    """WACC / terminal-growth sensitivity for the explicit FCFF model."""
    waccs = np.array([wacc_center - .02, wacc_center - .01, wacc_center, wacc_center + .01, wacc_center + .02])
    tgs = np.array([terminal_center - .01, terminal_center - .005, terminal_center, terminal_center + .005, terminal_center + .01])
    z = []
    for tg in tgs:
        row = []
        for w in waccs:
            if w <= tg:
                row.append(np.nan)
                continue
            kwargs = dict(dcf_kwargs)
            kwargs["wacc"] = float(w)
            kwargs["terminal_growth"] = float(tg)
            row.append(dcf_func(**kwargs).get("fair_value", np.nan))
        z.append(row)
    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[f"{x:.1%}" for x in waccs],
        y=[f"{x:.1%}" for x in tgs],
        colorscale=[[0, "#ded9cf"], [0.5, "#8fa6a9"], [1, "#1f4e5f"]],
        colorbar={"title": "Value"},
        text=[[f"{v:.0f}" if np.isfinite(v) else "N/A" for v in row] for row in z],
        texttemplate="%{text}",
        hovertemplate="WACC %{x}<br>Terminal growth %{y}<br>Fair value %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(xaxis_title="WACC", yaxis_title="Terminal growth")
    return _layout(fig, "Explicit FCFF sensitivity", height=420)
