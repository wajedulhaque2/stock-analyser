from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

import pandas as pd
import yfinance as yf

from .models import StockData


def _safe(label: str, fn: Callable[[], Any], errors: list[str], default: Any):
    try:
        value = fn()
        return default if value is None else value
    except Exception as exc:  # Yahoo endpoints can fail independently
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return default


def _df(value: Any) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _first_frame(label: str, candidates: list[tuple[str, Callable[[], Any]]], errors: list[str]) -> pd.DataFrame:
    """Return the first non-empty DataFrame from equivalent yfinance routes.

    Yahoo endpoints can be intermittently empty even when the equivalent Ticker
    property is populated. We therefore try the documented getter first and then
    its documented property aliases. Only exceptions are appended to diagnostics;
    an empty route simply falls through to the next equivalent representation.
    """
    local_errors: list[str] = []
    for route, fn in candidates:
        try:
            value = fn()
            frame = _df(value)
            if not frame.empty:
                return frame
        except Exception as exc:
            local_errors.append(f"{label} [{route}]: {type(exc).__name__}: {exc}")
    errors.extend(local_errors)
    return pd.DataFrame()


def normalize_price_currency(history: pd.DataFrame, price_currency: str | None, financial_currency: str | None):
    """Normalize common subunit quotes so price and per-share valuation use one currency.

    Yahoo commonly quotes London equities in GBp (pence) while company financials
    and market capitalization are in GBP. In that case OHLC/dividend values are
    converted to GBP. The transformation is explicit and returned as a scale factor.
    """
    # Determine the quote-unit scale even when history is unavailable. The fallback
    # current price from Yahoo info/fast_info is quoted in the same market unit, so
    # LSE GBp quotes still need the 0.01 conversion to GBP.
    scale = 1.0
    out_currency = price_currency or financial_currency
    if price_currency in {"GBp", "GBX"} and financial_currency == "GBP":
        scale = 0.01
        out_currency = "GBP"
    if history is None or history.empty:
        return history, scale, out_currency
    h = history.copy()
    if scale != 1.0:
        for col in ["Open", "High", "Low", "Close", "Dividends", "Capital Gains"]:
            if col in h.columns:
                h[col] = pd.to_numeric(h[col], errors="coerce") * scale
    return h, scale, out_currency



def _fetch_risk_free_rate(errors: list[str]) -> float | None:
    """Fetch the latest US 10Y Treasury yield from Yahoo's ^TNX index.

    ^TNX is quoted in percentage points (for example 4.25 for 4.25%), so the
    returned value is converted to a decimal rate. Failure is non-fatal.
    """
    try:
        rf = yf.Ticker("^TNX").history(period="5d", auto_adjust=False)
        if isinstance(rf, pd.DataFrame) and not rf.empty and "Close" in rf.columns:
            close = pd.to_numeric(rf["Close"], errors="coerce").dropna()
            if len(close):
                value = float(close.iloc[-1]) / 100.0
                if 0.0 < value < 0.20:
                    return value
    except Exception as exc:
        errors.append(f"risk_free_rate [^TNX]: {type(exc).__name__}: {exc}")
    return None

def fetch_stock_data(ticker: str, history_period: str = "5y") -> StockData:
    """Fetch a normalized bundle of Yahoo Finance data for one ticker.

    Individual endpoints are isolated so one missing analyst dataset does not make
    the entire research view fail.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Ticker cannot be blank")

    errors: list[str] = []
    t = yf.Ticker(ticker)

    info = _safe("info", t.get_info, errors, {})
    fast = _safe("fast_info", lambda: dict(t.fast_info), errors, {})
    history = _safe(
        "history",
        lambda: t.history(period=history_period, auto_adjust=True, repair=True),
        errors,
        pd.DataFrame(),
    )
    history_metadata = _safe("history_metadata", lambda: t.get_history_metadata(repair=True), errors, {})
    price_currency = history_metadata.get("currency") if isinstance(history_metadata, dict) else None
    financial_currency = info.get("financialCurrency") if isinstance(info, dict) else None
    normalized_history, price_scale, normalized_currency = normalize_price_currency(
        _df(history), price_currency, financial_currency
    )
    risk_free_rate = _fetch_risk_free_rate(errors)

    # Financial statements: use documented getters plus documented property aliases.
    # This is especially important for quarterly data, where one Yahoo route can
    # occasionally return an empty frame while the equivalent Ticker property works.
    income = _first_frame("income", [
        ("get_income_stmt(yearly)", lambda: t.get_income_stmt(freq="yearly")),
        ("income_stmt", lambda: t.income_stmt),
        ("financials", lambda: t.financials),
    ], errors)
    ttm_income = _first_frame("ttm_income", [
        ("get_income_stmt(trailing)", lambda: t.get_income_stmt(freq="trailing")),
        ("ttm_income_stmt", lambda: t.ttm_income_stmt),
        ("ttm_financials", lambda: t.ttm_financials),
    ], errors)
    balance = _first_frame("balance", [
        ("get_balance_sheet(yearly)", lambda: t.get_balance_sheet(freq="yearly")),
        ("balance_sheet", lambda: t.balance_sheet),
    ], errors)
    quarterly_balance = _first_frame("quarterly_balance", [
        ("get_balance_sheet(quarterly)", lambda: t.get_balance_sheet(freq="quarterly")),
        ("quarterly_balance_sheet", lambda: t.quarterly_balance_sheet),
        ("quarterly_balancesheet", lambda: t.quarterly_balancesheet),
    ], errors)
    cashflow = _first_frame("cashflow", [
        ("get_cash_flow(yearly)", lambda: t.get_cash_flow(freq="yearly")),
        ("cashflow", lambda: t.cashflow),
        ("cash_flow", lambda: t.cash_flow),
    ], errors)
    ttm_cashflow = _first_frame("ttm_cashflow", [
        ("get_cash_flow(trailing)", lambda: t.get_cash_flow(freq="trailing")),
        ("ttm_cashflow", lambda: t.ttm_cashflow),
        ("ttm_cash_flow", lambda: t.ttm_cash_flow),
    ], errors)
    quarterly_income = _first_frame("quarterly_income", [
        ("get_income_stmt(quarterly)", lambda: t.get_income_stmt(freq="quarterly")),
        ("quarterly_income_stmt", lambda: t.quarterly_income_stmt),
        ("quarterly_financials", lambda: t.quarterly_financials),
    ], errors)
    quarterly_cashflow = _first_frame("quarterly_cashflow", [
        ("get_cash_flow(quarterly)", lambda: t.get_cash_flow(freq="quarterly")),
        ("quarterly_cashflow", lambda: t.quarterly_cashflow),
        ("quarterly_cash_flow", lambda: t.quarterly_cash_flow),
    ], errors)

    data = StockData(
        ticker=ticker,
        info=info if isinstance(info, dict) else {},
        fast_info=fast if isinstance(fast, dict) else {},
        history_metadata=history_metadata if isinstance(history_metadata, dict) else {},
        price_currency=normalized_currency,
        financial_currency=financial_currency,
        price_scale_applied=price_scale,
        risk_free_rate=risk_free_rate,
        history=normalized_history,
        income=income,
        ttm_income=ttm_income,
        balance=balance,
        quarterly_balance=quarterly_balance,
        cashflow=cashflow,
        ttm_cashflow=ttm_cashflow,
        quarterly_income=quarterly_income,
        quarterly_cashflow=quarterly_cashflow,
        earnings_history=_df(_safe("earnings_history", t.get_earnings_history, errors, pd.DataFrame())),
        earnings_estimate=_df(_safe("earnings_estimate", t.get_earnings_estimate, errors, pd.DataFrame())),
        revenue_estimate=_df(_safe("revenue_estimate", t.get_revenue_estimate, errors, pd.DataFrame())),
        eps_trend=_df(_safe("eps_trend", t.get_eps_trend, errors, pd.DataFrame())),
        eps_revisions=_df(_safe("eps_revisions", t.get_eps_revisions, errors, pd.DataFrame())),
        growth_estimates=_df(_safe("growth_estimates", t.get_growth_estimates, errors, pd.DataFrame())),
        analyst_targets=_safe("analyst_targets", t.get_analyst_price_targets, errors, {}),
        recommendations=_df(_safe("recommendations", t.get_recommendations, errors, pd.DataFrame())),
        valuation_history=_df(_safe("valuation", lambda: t.get_valuation_measures(freq="quarterly", periods=12), errors, pd.DataFrame())),
        errors=errors,
    )
    return data


def stock_data_to_dict(data: StockData) -> dict[str, Any]:
    """Serializable helper useful for debug/export metadata."""
    payload = asdict(data)
    for key, value in list(payload.items()):
        if isinstance(value, pd.DataFrame):
            payload[key] = value.to_dict()
    return payload
