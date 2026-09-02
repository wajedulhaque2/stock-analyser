from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import math
import re

import numpy as np
import pandas as pd

from .fiscal_ai import FiscalApiError, FiscalClient, FiscalCompany, resolve_company, _company_from_profile


RATIO_IDS = [
    "ratio_price_to_earnings",
    "ratio_price_to_sales",
    "ratio_price_to_book",
    "ratio_ev_to_ebitda",
    "ratio_ev_to_ebit",
    "ratio_ev_to_sales",
    "ratio_ev_to_fcf",
    "ratio_fcf_yield",
    "ratio_return_on_invested_capital",
]


@dataclass
class FiscalValuationBundle:
    company: FiscalCompany
    profile: dict[str, Any] = field(default_factory=dict)
    financials: dict[str, Any] = field(default_factory=dict)
    ratios: Any = field(default_factory=dict)
    segments_kpis: Any = field(default_factory=dict)
    shares: Any = field(default_factory=dict)
    peer_ratios: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)


def _client_methods():
    """Marker used by regression tests; methods are installed onto FiscalClient below."""


def _standardized_financials(self: FiscalClient, company_key: str, statement_type: str, period_type: str = "annual,ltm,latest") -> Any:
    path = f"/v1/company/financials/{statement_type}/standardized"
    return self.get(path, {"companyKey": company_key, "periodType": period_type})


def _company_ratios(self: FiscalClient, company_key: str, period_type: str = "annual,quarterly,latest", ratio_ids: list[str] | None = None) -> Any:
    params: dict[str, Any] = {"companyKey": company_key, "periodType": period_type}
    if ratio_ids:
        params["ratioId"] = ",".join(ratio_ids)
    return self.get("/v1/company/ratios", params)


def _segments_kpis(self: FiscalClient, company_key: str, period_type: str = "annual,quarterly,latest") -> Any:
    return self.get("/v2/company/segments-and-kpis", {"companyKey": company_key, "periodType": period_type})


def _shares_outstanding(self: FiscalClient, company_key: str) -> Any:
    return self.get("/v1/company/shares-outstanding", {"companyKey": company_key})


# Keep the established client small while exposing the new endpoint family in v0.9.
if not hasattr(FiscalClient, "standardized_financials"):
    FiscalClient.standardized_financials = _standardized_financials  # type: ignore[attr-defined]
if not hasattr(FiscalClient, "company_ratios"):
    FiscalClient.company_ratios = _company_ratios  # type: ignore[attr-defined]
if not hasattr(FiscalClient, "segments_kpis"):
    FiscalClient.segments_kpis = _segments_kpis  # type: ignore[attr-defined]
if not hasattr(FiscalClient, "shares_outstanding"):
    FiscalClient.shares_outstanding = _shares_outstanding  # type: ignore[attr-defined]


def load_valuation_bundle(client: FiscalClient, ticker: str, company_name: str = "", max_peers: int = 5) -> FiscalValuationBundle:
    company = resolve_company(client, ticker, company_name)
    diagnostics: list[str] = []
    capabilities = {"profile": False, "financials": False, "ratios": False, "segments_and_kpis": False, "shares": False, "peers": False}

    try:
        profile = client.profile(company.company_key)
        company = _company_from_profile(company, profile)
        capabilities["profile"] = True
    except FiscalApiError as exc:
        profile = {}
        diagnostics.append(f"Profile: {exc}")

    financials: dict[str, Any] = {}
    for statement in ["income-statement", "balance-sheet", "cash-flow-statement"]:
        current_period = "latest" if statement == "balance-sheet" else "ltm"
        current_payload = {}
        annual_payload = {}
        try:
            current_payload = client.standardized_financials(company.company_key, statement, current_period)  # type: ignore[attr-defined]
        except FiscalApiError as exc:
            diagnostics.append(f"{statement} ({current_period}): {exc}")
        try:
            annual_payload = client.standardized_financials(company.company_key, statement, "annual")  # type: ignore[attr-defined]
        except FiscalApiError as exc:
            diagnostics.append(f"{statement} (annual): {exc}")
        if current_payload or annual_payload:
            financials[statement] = {"current": current_payload, "annual": annual_payload, "current_period": current_period}
    capabilities["financials"] = bool(financials)

    try:
        ratios = client.company_ratios(company.company_key, "annual", RATIO_IDS)  # type: ignore[attr-defined]
        capabilities["ratios"] = bool(ratios)
    except FiscalApiError as exc:
        ratios = {}
        diagnostics.append(f"Ratios: {exc}")

    if "segments_and_kpis" in set(company.datasets or []):
        try:
            segments = client.segments_kpis(company.company_key, "annual,quarterly,latest")  # type: ignore[attr-defined]
            capabilities["segments_and_kpis"] = bool(segments)
        except FiscalApiError as exc:
            segments = {}
            diagnostics.append(f"Segments & KPIs: {exc}")
    else:
        segments = {}

    try:
        shares = client.shares_outstanding(company.company_key)  # type: ignore[attr-defined]
        capabilities["shares"] = bool(shares)
    except FiscalApiError as exc:
        shares = {}
        diagnostics.append(f"Shares: {exc}")

    peer_rows: list[dict[str, Any]] = []
    for peer in (profile.get("peers") or [])[:max_peers] if isinstance(profile, dict) else []:
        peer_key = str(peer.get("companyKey") or "")
        if not peer_key:
            continue
        try:
            payload = client.company_ratios(peer_key, "latest", RATIO_IDS)  # type: ignore[attr-defined]
            peer_rows.append({"profile": peer, "ratios": payload})
        except FiscalApiError as exc:
            diagnostics.append(f"Peer {peer_key}: {exc}")
    capabilities["peers"] = bool(peer_rows)

    return FiscalValuationBundle(
        company=company,
        profile=profile,
        financials=financials,
        ratios=ratios,
        segments_kpis=segments,
        shares=shares,
        peer_ratios=peer_rows,
        diagnostics=diagnostics,
        capabilities=capabilities,
    )


def _canon(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return np.nan
    if isinstance(value, dict):
        for key in ["value", "amount", "standardizedValue", "reportedValue", "numericValue"]:
            if key in value:
                return _number(value.get(key))
        return np.nan
    try:
        v = float(value)
        return v if math.isfinite(v) else np.nan
    except Exception:
        return np.nan


def _period_context(node: dict[str, Any], inherited: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(inherited or {})
    key_map = {
        "periodType": "period_type", "fiscalYear": "fiscal_year", "fiscalQuarter": "fiscal_quarter",
        "periodStartDate": "period_start", "startDate": "period_start", "periodStart": "period_start",
        "periodEndDate": "period_end", "endDate": "period_end", "date": "period_end",
        "asOf": "as_of", "filedAt": "as_of", "filingDate": "as_of",
        "currency": "currency",
        "fiscalPeriod": "fiscal_period", "calendarYear": "calendar_year",
    }
    for source, dest in key_map.items():
        if source in node and node.get(source) is not None:
            ctx[dest] = node.get(source)
    return ctx


def flatten_metric_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize Fiscal's metric-oriented endpoints into tolerant long-form rows.

    Fiscal has evolved several endpoint schemas. This parser deliberately accepts
    metric nodes with nested values, period-centric rows containing ``metrics`` or
    ``metricsValues``, and ratio rows. Unknown fields are ignored rather than making
    valuation availability depend on one response-shape revision.
    """
    rows: list[dict[str, Any]] = []
    metric_meta: dict[str, str] = {}

    def register_meta(node: Any):
        if isinstance(node, dict):
            mid = node.get("metricId") or node.get("ratioId") or node.get("id")
            name = node.get("metricName") or node.get("ratioName") or node.get("displayName") or node.get("name")
            if mid is not None and name:
                metric_meta[str(mid)] = str(name)
            for value in node.values():
                register_meta(value)
        elif isinstance(node, list):
            for item in node:
                register_meta(item)

    register_meta(payload)

    seen: set[tuple[str, str, str, float]] = set()

    def emit(metric_id: Any, metric_name: Any, value: Any, ctx: dict[str, Any]):
        v = _number(value)
        if not np.isfinite(v):
            return
        mid = str(metric_id or metric_name or "")
        name = str(metric_name or metric_meta.get(mid) or mid)
        if not mid and not name:
            return
        period = str(ctx.get("period_end") or ctx.get("fiscal_period") or ctx.get("fiscal_year") or "")
        ptype = str(ctx.get("period_type") or "")
        key = (mid, period, ptype, round(v, 8))
        if key in seen:
            return
        seen.add(key)
        rows.append({"metric_id": mid, "metric_name": name, "value": v, **ctx})

    def walk(node: Any, ctx: dict[str, Any] | None = None, metric_id: Any = None, metric_name: Any = None):
        if isinstance(node, list):
            for item in node:
                walk(item, ctx, metric_id, metric_name)
            return
        if not isinstance(node, dict):
            if metric_id is not None or metric_name is not None:
                emit(metric_id, metric_name, node, ctx or {})
            return

        local_ctx = _period_context(node, ctx)
        local_id = node.get("metricId") or node.get("ratioId") or node.get("metric_id") or metric_id
        local_name = node.get("metricName") or node.get("ratioName") or node.get("displayName") or node.get("metric") or metric_name

        for key in ["value", "amount", "standardizedValue", "reportedValue", "numericValue"]:
            if key in node and (local_id is not None or local_name is not None):
                emit(local_id, local_name, node.get(key), local_ctx)

        for map_key in ["metrics", "metricsValues", "valuesByMetric"]:
            metric_map = node.get(map_key)
            if isinstance(metric_map, dict):
                for key, value in metric_map.items():
                    if isinstance(value, (dict, list)):
                        walk(value, local_ctx, key, metric_meta.get(str(key), str(key)))
                    else:
                        emit(key, metric_meta.get(str(key), str(key)), value, local_ctx)

        # Metric node with a list of period values.
        for values_key in ["values", "data", "periods", "timeSeries", "series"]:
            child = node.get(values_key)
            if child is not None:
                # Some Fiscal endpoints wrap independent metric/ratio rows in a
                # top-level ``data`` array, while metric-oriented endpoints put
                # period rows under ``values``. Preserve inherited metric identity
                # when present, otherwise treat the container as independent rows.
                if local_id is not None or local_name is not None:
                    walk(child, local_ctx, local_id, local_name)
                else:
                    walk(child, local_ctx)

        skip = {"value", "amount", "standardizedValue", "reportedValue", "numericValue", "metrics", "metricsValues", "valuesByMetric", "values", "data", "periods", "timeSeries", "series"}
        for key, child in node.items():
            if key in skip:
                continue
            if isinstance(child, (dict, list)):
                walk(child, local_ctx, local_id, local_name)
            elif (key.startswith("ratio_") or key.startswith("metric_")):
                emit(key, metric_meta.get(key, key), child, local_ctx)

    walk(payload, {})
    return rows


FINANCIAL_ALIASES: dict[str, list[str]] = {
    "revenue": ["revenue", "total revenue", "revenues", "sales"],
    "operating_income": ["operating income", "operating profit", "ebit"],
    "ebitda": ["ebitda"],
    "net_income": ["net income", "net income attributable to common"],
    "depreciation": ["depreciation and amortization", "depreciation", "d&a"],
    "capex": ["capital expenditures", "capital expenditure", "purchase of property plant and equipment", "purchases of property plant equipment"],
    "change_working_capital": ["change in working capital", "changes in working capital", "change in net working capital"],
    "cash": ["cash and cash equivalents", "cash cash equivalents and short term investments", "cash and short term investments"],
    "debt": ["total debt", "debt and finance lease obligations", "total debt and capital lease obligation"],
    "interest_expense": ["interest expense", "net interest expense"],
    "tax_provision": ["income tax expense", "tax provision"],
    "pretax_income": ["pretax income", "pre tax income", "income before taxes"],
    "shares": ["diluted weighted average shares outstanding", "weighted average shares diluted", "total shares outstanding"],
}


def _metric_matches(row: dict[str, Any], aliases: list[str]) -> bool:
    """Match standardized financial metrics without broad substring collisions.

    Fiscal metric IDs often include statement prefixes (for example
    ``cash_flow_depreciation_and_amortization``). Exact display-name matching or an
    ID suffix match is sufficient and avoids accidentally treating similarly named
    ratios/adjustments as the core accounting metric.
    """
    mid = _canon(row.get("metric_id", ""))
    name = _canon(row.get("metric_name", ""))
    for alias in aliases:
        a = _canon(alias)
        if not a:
            continue
        if name == a or mid == a or mid.endswith(a):
            return True
    return False


def metric_series(payload: Any, aliases: list[str]) -> list[dict[str, Any]]:
    rows = [row for row in flatten_metric_payload(payload) if _metric_matches(row, aliases)]
    def sort_key(row: dict[str, Any]):
        date = pd.to_datetime(row.get("period_end"), errors="coerce")
        fy = row.get("fiscal_year")
        try: fy_num = int(fy)
        except Exception: fy_num = -9999
        return (date if pd.notna(date) else pd.Timestamp.min, fy_num)
    return sorted(rows, key=sort_key, reverse=True)


def latest_metric(payload: Any, aliases: list[str], prefer_periods: tuple[str, ...] = ("ltm", "latest", "annual", "quarterly")) -> tuple[float, dict[str, Any] | None]:
    rows = metric_series(payload, aliases)
    if not rows:
        return np.nan, None
    for ptype in prefer_periods:
        for row in rows:
            if str(row.get("period_type") or "").lower() == ptype:
                return float(row["value"]), row
    return float(rows[0]["value"]), rows[0]


def financial_snapshot(bundle: FiscalValuationBundle) -> dict[str, Any]:
    payloads = bundle.financials or {}

    def split_payload(statement_key: str) -> tuple[Any, Any, str]:
        raw = payloads.get(statement_key, {})
        if isinstance(raw, dict) and ("current" in raw or "annual" in raw):
            return raw.get("current", {}), raw.get("annual", {}), str(raw.get("current_period") or "current")
        # Backward-compatible path for tests / older cached bundles.
        return raw, raw, "mixed"

    income_current, income_annual, income_basis = split_payload("income-statement")
    balance_current, balance_annual, balance_basis = split_payload("balance-sheet")
    cash_current, cash_annual, cash_basis = split_payload("cash-flow-statement")
    combined_current = {"income": income_current, "balance": balance_current, "cashflow": cash_current}
    combined_annual = {"income": income_annual, "balance": balance_annual, "cashflow": cash_annual}
    out: dict[str, Any] = {"_sources": {}, "_current_basis": {"income": income_basis, "balance": balance_basis, "cashflow": cash_basis}}
    statement_for = {
        "revenue": "income", "operating_income": "income", "ebitda": "income", "net_income": "income", "interest_expense": "income", "tax_provision": "income", "pretax_income": "income", "shares": "income",
        "cash": "balance", "debt": "balance",
        "depreciation": "cashflow", "capex": "cashflow", "change_working_capital": "cashflow",
    }
    for key, aliases in FINANCIAL_ALIASES.items():
        statement = statement_for[key]
        value, meta = latest_metric(combined_current[statement], aliases)
        if key == "capex" and np.isfinite(value): value = abs(value)
        if key == "depreciation" and np.isfinite(value): value = abs(value)
        if key == "interest_expense" and np.isfinite(value): value = abs(value)
        if key == "change_working_capital" and np.isfinite(value):
            out["nwc_investment"] = -float(value)
            out["_sources"]["nwc_investment"] = "Fiscal.ai standardized cash flow: negative of Change in Working Capital"
        out[key] = value
        if np.isfinite(value):
            period = str((meta or {}).get("period_type") or "latest")
            basis = out.get("_current_basis", {}).get(statement, period)
            out["_sources"][key] = f"Fiscal.ai standardized {statement} ({basis})"

    revenue = out.get("revenue", np.nan)
    op = out.get("operating_income", np.nan)
    pretax = out.get("pretax_income", np.nan)
    tax = out.get("tax_provision", np.nan)
    out["operating_margin"] = op / revenue if np.isfinite(op) and np.isfinite(revenue) and revenue else np.nan
    out["tax_rate"] = tax / pretax if np.isfinite(tax) and np.isfinite(pretax) and pretax else np.nan
    for key, source_key in [("da_pct_revenue", "depreciation"), ("capex_pct_revenue", "capex"), ("nwc_investment_pct_revenue", "nwc_investment")]:
        value = out.get(source_key, np.nan)
        out[key] = value / revenue if np.isfinite(value) and np.isfinite(revenue) and revenue else np.nan

    # Historical annual medians for driver normalization.
    histories: dict[str, list[dict[str, Any]]] = {}
    for key, aliases in FINANCIAL_ALIASES.items():
        statement = statement_for[key]
        rows = metric_series(combined_annual[statement], aliases)
        # Annual payloads are requested separately; tolerate APIs that omit periodType.
        annual_only = [r for r in rows if str(r.get("period_type") or "").lower() == "annual"]
        rows = annual_only or rows
        histories[key] = rows[:8]
    out["histories"] = histories
    annual_periods: dict[str, dict[str, float]] = {}
    for key, rows in histories.items():
        for r in rows:
            period = str(r.get("period_end") or r.get("fiscal_year") or "")
            if not period: continue
            annual_periods.setdefault(period, {})[key] = float(r["value"])
    driver_rows = []
    for period, vals in annual_periods.items():
        rv = vals.get("revenue", np.nan)
        if not np.isfinite(rv) or rv <= 0: continue
        driver_rows.append({
            "period": period,
            "operating_margin": vals.get("operating_income", np.nan) / rv if np.isfinite(vals.get("operating_income", np.nan)) else np.nan,
            "da_pct_revenue": abs(vals.get("depreciation", np.nan)) / rv if np.isfinite(vals.get("depreciation", np.nan)) else np.nan,
            "capex_pct_revenue": abs(vals.get("capex", np.nan)) / rv if np.isfinite(vals.get("capex", np.nan)) else np.nan,
            "nwc_investment_pct_revenue": -vals.get("change_working_capital", np.nan) / rv if np.isfinite(vals.get("change_working_capital", np.nan)) else np.nan,
        })
    # Sort explicitly; dictionary insertion order from API traversal is not a valid
    # historical chronology.
    driver_rows = sorted(driver_rows, key=lambda r: pd.to_datetime(r.get("period"), errors="coerce") if pd.notna(pd.to_datetime(r.get("period"), errors="coerce")) else pd.Timestamp.min, reverse=True)
    out["historical_driver_rows"] = driver_rows
    out["_history_warnings"] = []
    current_map = {
        "operating_margin": out.get("operating_margin", np.nan),
        "da_pct_revenue": out.get("da_pct_revenue", np.nan),
        "capex_pct_revenue": out.get("capex_pct_revenue", np.nan),
        "nwc_investment_pct_revenue": out.get("nwc_investment_pct_revenue", np.nan),
    }
    sanity = {
        "operating_margin": (-0.30, 0.80, 0.15),
        "da_pct_revenue": (0.0, 0.25, 0.10),
        "capex_pct_revenue": (0.0, 0.65, 0.30),
        "nwc_investment_pct_revenue": (-0.15, 0.15, 0.12),
    }
    for key in ["operating_margin", "da_pct_revenue", "capex_pct_revenue", "nwc_investment_pct_revenue"]:
        lo, hi, delta = sanity[key]
        vals = [float(r[key]) for r in driver_rows[:5] if np.isfinite(r.get(key, np.nan)) and lo <= float(r[key]) <= hi]
        med = float(np.median(vals)) if vals else np.nan
        cur = current_map.get(key, np.nan)
        if np.isfinite(med) and np.isfinite(cur) and abs(med - float(cur)) > delta:
            out["_history_warnings"].append(f"Fiscal historical {key} median {med:.1%} diverged {abs(med-float(cur)):.1%} from the current ratio and was rejected.")
            med = np.nan
        if key == "da_pct_revenue" and np.isfinite(med) and np.isfinite(cur) and float(cur) > 0 and med > max(0.20, float(cur) * 2.0 + 0.02):
            out["_history_warnings"].append(f"Fiscal historical D&A/revenue median {med:.1%} was rejected as an implausible normalization from current {float(cur):.1%}.")
            med = np.nan
        out[f"historical_{key}"] = med
    return out


def ratio_series(payload: Any, ratio_id: str) -> list[dict[str, Any]]:
    target = _canon(ratio_id)
    rows = []
    for row in flatten_metric_payload(payload):
        if target == _canon(row.get("metric_id")) or target in _canon(row.get("metric_name")):
            rows.append(row)

    # Ratio endpoints can return multiple period types and response order is not a
    # reliable definition of "current". Sort explicitly so current/median context
    # cannot silently depend on JSON traversal order. Latest/daily or dated rows win,
    # followed by quarterly and annual observations.
    period_rank = {"latest": 4, "ltm": 4, "daily": 4, "quarterly": 3, "annual": 2}
    def key(row: dict[str, Any]):
        dt = pd.to_datetime(row.get("period_end"), errors="coerce")
        ts = int(dt.value) if pd.notna(dt) else -1
        try:
            fy = int(row.get("fiscal_year"))
        except Exception:
            fy = -9999
        return (period_rank.get(str(row.get("period_type") or "").lower(), 0), ts, fy)
    return sorted(rows, key=key, reverse=True)


def ratio_context(payload: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rid in RATIO_IDS:
        rows = ratio_series(payload, rid)
        vals = [float(r["value"]) for r in rows if np.isfinite(r.get("value", np.nan))]
        if not vals:
            continue
        out[f"current_{rid}"] = vals[0]
        hist = vals[1:] if len(vals) > 1 else vals
        out[f"median_{rid}"] = float(np.median(hist))
        if len(vals) >= 4:
            out[f"percentile_{rid}"] = float(np.mean(np.asarray(hist) <= vals[0]))
    return out


def kpi_driver_candidates(payload: Any, limit: int = 12) -> list[dict[str, Any]]:
    rows = flatten_metric_payload(payload)
    if not rows:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        mid = str(row.get("metric_id") or row.get("metric_name") or "")
        if not mid:
            continue
        grouped.setdefault(mid, []).append(row)

    revenue_kw = ["user", "subscriber", "member", "impression", "shipment", "delivery", "booking", "backlog", "store", "trip", "volume", "unit", "customer", "seat", "device"]
    price_kw = ["price", "arpu", "average revenue", "take rate", "asp", "yield"]
    capital_kw = ["capex", "capacity", "utilization", "utilisation", "data center", "data centre"]
    margin_kw = ["margin", "cost", "loss", "profit"]
    candidates = []
    for mid, series in grouped.items():
        name = str(series[0].get("metric_name") or mid)
        low = name.lower()
        # Pricing/ARPU metrics can contain activity words such as "user" (for
        # example "Average Revenue Per User"), so classify monetisation first.
        if any(k in low for k in price_kw): kind = "Pricing / monetisation driver"
        elif any(k in low for k in revenue_kw): kind = "Volume / activity driver"
        elif any(k in low for k in capital_kw): kind = "Capital intensity driver"
        elif any(k in low for k in margin_kw): kind = "Margin / cost driver"
        else: continue
        series = sorted(series, key=lambda r: str(r.get("period_end") or r.get("fiscal_year") or ""), reverse=True)
        latest = float(series[0]["value"])
        growth = np.nan
        if len(series) >= 2 and series[1]["value"] not in (0, None):
            try: growth = latest / float(series[1]["value"]) - 1
            except Exception: growth = np.nan
        candidates.append({"metric_id": mid, "name": name, "kind": kind, "latest": latest, "growth": growth, "observations": len(series)})
    # Prefer longer histories and recognized activity/pricing drivers.
    candidates.sort(key=lambda x: (x["kind"] in {"Volume / activity driver", "Pricing / monetisation driver"}, x["observations"]), reverse=True)
    return candidates[:limit]


def peer_multiple_table(bundle: FiscalValuationBundle) -> pd.DataFrame:
    rows = []
    for item in bundle.peer_ratios:
        profile = item.get("profile") or {}
        ctx = ratio_context(item.get("ratios") or {})
        rows.append({
            "Company": profile.get("displayNameEnglish") or profile.get("companyKey") or "Peer",
            "Ticker": (profile.get("primaryListing") or {}).get("ticker") or "",
            "P/E": ctx.get("current_ratio_price_to_earnings", np.nan),
            "EV/EBITDA": ctx.get("current_ratio_ev_to_ebitda", np.nan),
            "EV/EBIT": ctx.get("current_ratio_ev_to_ebit", np.nan),
            "P/S": ctx.get("current_ratio_price_to_sales", np.nan),
            "FCF yield": ctx.get("current_ratio_fcf_yield", np.nan),
        })
    return pd.DataFrame(rows)


def shares_outstanding_value(payload: Any) -> float:
    """Extract Fiscal's latest total shares outstanding from tolerant response shapes."""
    preferred = ["totalSharesOutstanding", "market_data_total_shares_outstanding", "sharesOutstanding", "total_shares_outstanding"]
    found: list[float] = []
    def walk(node: Any):
        if isinstance(node, dict):
            for key, value in node.items():
                if _canon(key) in {_canon(x) for x in preferred}:
                    v = _number(value)
                    if np.isfinite(v) and v > 0: found.append(float(v))
                if isinstance(value, (dict, list)): walk(value)
        elif isinstance(node, list):
            for item in node: walk(item)
    walk(payload)
    return found[0] if found else np.nan
