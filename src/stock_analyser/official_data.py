from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import re
from typing import Any

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}"


@dataclass
class FilingRef:
    form: str
    filed: str
    report_date: str
    accession: str
    primary_document: str
    items: str = ""
    description: str = ""
    url: str = ""
    exhibit_url: str = ""
    exhibit_name: str = ""


@dataclass
class OfficialEarningsPacket:
    ticker: str
    cik: str = ""
    company_name: str = ""
    current: FilingRef | None = None
    previous: FilingRef | None = None
    current_text: str = ""
    current_release_text: str = ""
    previous_release_text: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _headers(contact_email: str) -> dict[str, str]:
    email = (contact_email or "").strip()
    if not email or "@" not in email:
        raise ValueError("SEC access requires a contact email for the declared User-Agent.")
    return {
        "User-Agent": f"StockAnalyser/0.6 research-tool {email}",
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }


def _get(url: str, contact_email: str, timeout: int = 30) -> requests.Response:
    headers = _headers(contact_email)
    # data.sec.gov uses the same fair-access declaration requirement but a different host.
    headers.pop("Host", None)
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def _get_json(url: str, contact_email: str, timeout: int = 30) -> dict[str, Any]:
    return _get(url, contact_email, timeout=timeout).json()


def resolve_cik(ticker: str, contact_email: str) -> tuple[str, str]:
    ticker = ticker.upper().strip()
    payload = _get_json(SEC_TICKERS_URL, contact_email)
    for item in payload.values():
        if str(item.get("ticker", "")).upper() == ticker:
            return str(int(item["cik_str"])).zfill(10), str(item.get("title", ticker))
    raise LookupError(f"No SEC CIK mapping found for {ticker}. This source adapter currently targets SEC-reporting tickers.")


def _recent_filings(submissions: dict[str, Any]) -> pd.DataFrame:
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict) or not recent:
        return pd.DataFrame()
    lengths = [len(v) for v in recent.values() if isinstance(v, list)]
    if not lengths:
        return pd.DataFrame()
    n = min(lengths)
    rows = []
    for i in range(n):
        rows.append({k: (v[i] if isinstance(v, list) and i < len(v) else None) for k, v in recent.items()})
    return pd.DataFrame(rows)


def _filing_url(cik: str, accession: str, primary_document: str) -> str:
    base = SEC_ARCHIVES.format(cik_int=int(cik), accession_nodash=accession.replace("-", ""))
    return f"{base}/{primary_document}"


def _to_ref(row: pd.Series, cik: str) -> FilingRef:
    accession = str(row.get("accessionNumber") or "")
    primary = str(row.get("primaryDocument") or "")
    return FilingRef(
        form=str(row.get("form") or ""),
        filed=str(row.get("filingDate") or ""),
        report_date=str(row.get("reportDate") or ""),
        accession=accession,
        primary_document=primary,
        items=str(row.get("items") or ""),
        description=str(row.get("primaryDocDescription") or ""),
        url=_filing_url(cik, accession, primary) if accession and primary else "",
    )


def select_periodic_filings(submissions: dict[str, Any], cik: str, count: int = 2) -> list[FilingRef]:
    frame = _recent_filings(submissions)
    if frame.empty or "form" not in frame:
        return []
    allowed = {"10-Q", "10-K", "20-F", "40-F"}
    frame = frame[frame["form"].isin(allowed)].copy()
    if frame.empty:
        return []
    frame["filingDate"] = pd.to_datetime(frame["filingDate"], errors="coerce")
    frame = frame.sort_values("filingDate", ascending=False)
    return [_to_ref(row, cik) for _, row in frame.head(count).iterrows()]


def _find_nearby_earnings_8k(submissions: dict[str, Any], cik: str, periodic: FilingRef, days: int = 14) -> FilingRef | None:
    frame = _recent_filings(submissions)
    if frame.empty or "form" not in frame:
        return None
    frame = frame[frame["form"].isin(["8-K", "8-K/A", "6-K"])].copy()
    if frame.empty:
        return None
    anchor = pd.to_datetime(periodic.filed, errors="coerce")
    frame["_date"] = pd.to_datetime(frame["filingDate"], errors="coerce")
    frame["_distance"] = (frame["_date"] - anchor).abs().dt.days
    near = frame[frame["_distance"] <= days].copy()
    if near.empty:
        return None
    # Item 2.02 is Results of Operations and Financial Condition. Prefer it where present.
    near["_earnings"] = near.get("items", pd.Series("", index=near.index)).astype(str).str.contains(r"(^|,)2\.02(,|$)", regex=True)
    near = near.sort_values(["_earnings", "_distance", "_date"], ascending=[False, True, False])
    return _to_ref(near.iloc[0], cik)


def _discover_earnings_exhibit(filing: FilingRef, cik: str, contact_email: str) -> FilingRef:
    if not filing.accession:
        return filing
    base = SEC_ARCHIVES.format(cik_int=int(cik), accession_nodash=filing.accession.replace("-", ""))
    try:
        index = _get_json(f"{base}/index.json", contact_email)
        items = index.get("directory", {}).get("item", [])
        candidates = []
        for item in items:
            name = str(item.get("name") or "")
            desc = str(item.get("description") or "")
            low = f"{name} {desc}".lower()
            score = 0
            if re.search(r"ex(?:hibit)?[-_]?99(?:\.1|01)", low):
                score += 6
            if "earnings" in low or "results" in low or "press release" in low:
                score += 5
            if name.lower().endswith((".htm", ".html")):
                score += 2
            if score:
                candidates.append((score, name, desc))
        if candidates:
            candidates.sort(reverse=True)
            _, name, desc = candidates[0]
            filing.exhibit_name = desc or name
            filing.exhibit_url = f"{base}/{name}"
    except Exception:
        pass
    return filing


def html_to_text(content: str) -> str:
    if not content:
        return ""
    soup = BeautifulSoup(content, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"[\t\r ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()



def _sentence_candidates(text: str) -> list[str]:
    """Return readable sentence/paragraph candidates from an official release."""
    if not text:
        return []
    compact = re.sub(r"[\t\r ]+", " ", text)
    compact = re.sub(r"\n{2,}", "\n", compact)
    lines = [x.strip() for x in compact.split("\n") if len(x.strip()) >= 28]
    out: list[str] = []
    for line in lines:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
        for part in parts:
            part = re.sub(r"\s+", " ", part).strip()
            if len(part) >= 28 and part not in out:
                out.append(part)
    return out


def extract_release_highlights(text: str) -> dict[str, list[str]]:
    """Deterministically surface high-signal official-release sentences before AI.

    This is deliberately lexical rather than generative: if guidance/cash-flow language
    is present in the furnished earnings exhibit, users should see it even when external transcript data
    retrieval is weak or unavailable.
    """
    sentences = _sentence_candidates(text)
    rules = {
        "guidance": ["we expect", "we anticipate", "outlook", "guidance", "we continue to expect", "we now expect"],
        "cash_flow_capex": ["free cash flow", "cash flow from operating activities", "capital expenditures", "finance leases", "capital expenditure"],
        "operations": ["operating margin", "income from operations", "revenue", "price per ad", "impressions"],
        "risk": ["regulatory", "legal", "headwind", "risk", "trial", "scrutiny"],
    }
    result: dict[str, list[str]] = {}
    for category, terms in rules.items():
        scored = []
        for idx, sentence in enumerate(sentences):
            low = sentence.lower()
            score = sum(3 if phrase in low else 0 for phrase in terms)
            if score:
                # Prefer more explicit forward-looking / quantified sentences.
                score += min(3, len(re.findall(r"\d", sentence)) // 2)
                scored.append((score, -idx, sentence[:520]))
        scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
        result[category] = [x[2] for x in scored[:3]]
    return result


def extract_company_defined_fcf(text: str) -> dict[str, Any]:
    """Extract an explicitly labelled company free-cash-flow amount from a release.

    The function only accepts a sentence that literally contains ``free cash flow`` and
    an amount with a disclosed scale. It is intentionally conservative and returns an
    empty mapping when the wording is ambiguous.
    """
    for sentence in _sentence_candidates(text):
        low = sentence.lower()
        if "free cash flow" not in low:
            continue
        match = re.search(
            r"free cash flow(?:\s+(?:was|of|totaled|was approximately|was about))?\s*"
            r"(?:us\$|\$)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|billion|thousand)",
            sentence,
            flags=re.I,
        )
        if not match:
            # Some releases phrase the metric as ``free cash flow was $784 million``
            # after another clause; allow a bounded amount later in the same sentence.
            tail = sentence[low.find("free cash flow"):]
            match = re.search(r"(?:us\$|\$)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|billion|thousand)", tail, flags=re.I)
        if not match:
            continue
        value = float(match.group(1).replace(",", ""))
        scale = match.group(2).lower()
        multiplier = {"thousand": 1e3, "million": 1e6, "billion": 1e9}[scale]
        currency = "USD" if "$" in sentence or "US$" in sentence.upper() else None
        return {
            "value": value * multiplier,
            "reported_value": value,
            "currency": currency,
            "unit_scale": scale,
            "excerpt": sentence[:520],
            "source": "Official earnings release",
        }
    return {}

def fetch_document_text(url: str, contact_email: str, max_chars: int = 1_200_000) -> str:
    if not url:
        return ""
    response = _get(url, contact_email, timeout=45)
    text = html_to_text(response.text)
    return text[:max_chars]


CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForAdditionsToPropertyPlantAndEquipment"],
}


def _fact_units(companyfacts: dict[str, Any], concept: str) -> list[dict[str, Any]]:
    facts = companyfacts.get("facts", {})
    for taxonomy in ["us-gaap", "ifrs-full"]:
        node = facts.get(taxonomy, {}).get(concept, {})
        units = node.get("units", {}) if isinstance(node, dict) else {}
        for unit in ["USD", "EUR", "GBP"]:
            if unit in units and isinstance(units[unit], list):
                return units[unit]
        for value in units.values():
            if isinstance(value, list):
                return value
    return []


def _concept_facts(companyfacts: dict[str, Any], concepts: list[str]) -> list[dict[str, Any]]:
    for concept in concepts:
        rows = _fact_units(companyfacts, concept)
        if rows:
            return rows
    return []


def _duration_days(row: dict[str, Any]) -> float:
    try:
        return float((pd.Timestamp(row["end"]) - pd.Timestamp(row["start"])).days)
    except Exception:
        return np.nan


def _quarter_duration_fact(rows: list[dict[str, Any]], report_date: str, forms: set[str]) -> float:
    if not rows or not report_date:
        return np.nan
    end = pd.Timestamp(report_date)
    candidates = []
    for r in rows:
        if str(r.get("form")) not in forms or not r.get("end") or not r.get("start"):
            continue
        try:
            if abs((pd.Timestamp(r["end"]) - end).days) > 5:
                continue
        except Exception:
            continue
        days = _duration_days(r)
        if np.isfinite(days) and 55 <= days <= 120:
            candidates.append((abs(days - 91), pd.Timestamp(r.get("filed") or "1900-01-01"), r))
    if not candidates:
        return np.nan
    candidates.sort(key=lambda x: (x[0], -x[1].value))
    try:
        return float(candidates[0][2]["val"])
    except Exception:
        return np.nan


def _quarter_from_ytd(rows: list[dict[str, Any]], report_date: str, fy: Any = None) -> float:
    """Return a quarterly cash-flow amount from cumulative YTD XBRL facts where needed."""
    if not rows or not report_date:
        return np.nan
    end = pd.Timestamp(report_date)
    candidates = []
    for r in rows:
        if str(r.get("form")) not in {"10-Q", "10-K"} or not r.get("start") or not r.get("end"):
            continue
        try:
            r_end = pd.Timestamp(r["end"])
            if abs((r_end - end).days) > 5:
                continue
            days = _duration_days(r)
            if not np.isfinite(days) or days < 55 or days > 380:
                continue
            candidates.append((days, r))
        except Exception:
            continue
    if not candidates:
        return np.nan
    # Prefer the shortest duration ending on report date because Q1 is already quarterly;
    # for Q2/Q3 the filing normally provides only cumulative YTD cash flow.
    candidates.sort(key=lambda x: x[0])
    days, current = candidates[0]
    try:
        current_val = float(current["val"])
    except Exception:
        return np.nan
    if days <= 120:
        return current_val

    cur_start = pd.Timestamp(current["start"])
    prior = []
    for r in rows:
        if str(r.get("form")) != "10-Q" or not r.get("start") or not r.get("end"):
            continue
        try:
            if abs((pd.Timestamp(r["start"]) - cur_start).days) > 5:
                continue
            r_end = pd.Timestamp(r["end"])
            if r_end >= end:
                continue
            r_days = _duration_days(r)
            if np.isfinite(r_days) and r_days < days - 20:
                prior.append((r_end, r_days, r))
        except Exception:
            continue
    if prior:
        prior.sort(key=lambda x: x[0], reverse=True)
        try:
            return current_val - float(prior[0][2]["val"])
        except Exception:
            pass
    return np.nan


def extract_sec_quarter_metrics(companyfacts: dict[str, Any], filing: FilingRef | None) -> dict[str, Any]:
    if filing is None:
        return {}
    report_date = filing.report_date
    forms = {filing.form} if filing.form else {"10-Q", "10-K"}
    revenue_rows = _concept_facts(companyfacts, CONCEPTS["revenue"])
    op_rows = _concept_facts(companyfacts, CONCEPTS["operating_income"])
    net_rows = _concept_facts(companyfacts, CONCEPTS["net_income"])
    cfo_rows = _concept_facts(companyfacts, CONCEPTS["cfo"])
    capex_rows = _concept_facts(companyfacts, CONCEPTS["capex"])

    revenue = _quarter_duration_fact(revenue_rows, report_date, forms)
    op_income = _quarter_duration_fact(op_rows, report_date, forms)
    net_income = _quarter_duration_fact(net_rows, report_date, forms)
    # Q4 is commonly only available as the full-year 10-K duration. When a
    # standalone quarter fact is absent, derive the quarter from full-year less
    # the prior 9-month YTD fact using the same concept.
    if not np.isfinite(revenue):
        revenue = _quarter_from_ytd(revenue_rows, report_date)
    if not np.isfinite(op_income):
        op_income = _quarter_from_ytd(op_rows, report_date)
    if not np.isfinite(net_income):
        net_income = _quarter_from_ytd(net_rows, report_date)
    cfo = _quarter_from_ytd(cfo_rows, report_date)
    capex = _quarter_from_ytd(capex_rows, report_date)
    fcf_proxy = cfo - capex if np.isfinite(cfo) and np.isfinite(capex) else np.nan

    return {
        "period_end": report_date,
        "filing_date": filing.filed,
        "form": filing.form,
        "revenue": revenue,
        "operating_income": op_income,
        "operating_margin": op_income / revenue if np.isfinite(op_income) and np.isfinite(revenue) and revenue else np.nan,
        "net_income": net_income,
        "cfo": cfo,
        "capex": capex,
        "fcf_proxy": fcf_proxy,
        "fcf_proxy_margin": fcf_proxy / revenue if np.isfinite(fcf_proxy) and np.isfinite(revenue) and revenue else np.nan,
    }


def fetch_official_earnings(ticker: str, contact_email: str) -> OfficialEarningsPacket:
    packet = OfficialEarningsPacket(ticker=ticker.upper().strip())
    try:
        cik, name = resolve_cik(packet.ticker, contact_email)
        packet.cik, packet.company_name = cik, name
    except Exception as exc:
        packet.errors.append(str(exc))
        return packet

    try:
        submissions = _get_json(SEC_SUBMISSIONS_URL.format(cik=packet.cik), contact_email)
        periodic = select_periodic_filings(submissions, packet.cik, count=2)
        if periodic:
            packet.current = periodic[0]
        if len(periodic) > 1:
            packet.previous = periodic[1]
        for ref in [packet.current, packet.previous]:
            if ref is None:
                continue
            nearby = _find_nearby_earnings_8k(submissions, packet.cik, ref)
            if nearby is not None:
                nearby = _discover_earnings_exhibit(nearby, packet.cik, contact_email)
                # Keep periodic metadata but attach the earnings exhibit.
                ref.exhibit_url = nearby.exhibit_url or nearby.url
                ref.exhibit_name = nearby.exhibit_name or nearby.description or nearby.form
    except Exception as exc:
        packet.errors.append(f"SEC submissions: {type(exc).__name__}: {exc}")

    try:
        companyfacts = _get_json(SEC_COMPANYFACTS_URL.format(cik=packet.cik), contact_email)
        packet.metrics = extract_sec_quarter_metrics(companyfacts, packet.current)
        if packet.current and packet.current.url:
            packet.sources["periodic_filing"] = packet.current.url
    except Exception as exc:
        packet.errors.append(f"SEC companyfacts: {type(exc).__name__}: {exc}")

    if packet.current:
        try:
            packet.current_text = fetch_document_text(packet.current.url, contact_email)
        except Exception as exc:
            packet.errors.append(f"Current filing text: {type(exc).__name__}: {exc}")
        if packet.current.exhibit_url:
            try:
                packet.current_release_text = fetch_document_text(packet.current.exhibit_url, contact_email, max_chars=700_000)
                packet.sources["earnings_release"] = packet.current.exhibit_url
                packet.metrics["release_highlights"] = extract_release_highlights(packet.current_release_text)
                release_fcf = extract_company_defined_fcf(packet.current_release_text)
                if release_fcf:
                    packet.metrics["company_defined_fcf"] = release_fcf.get("value", np.nan)
                    packet.metrics["company_defined_fcf_detail"] = release_fcf
            except Exception as exc:
                packet.errors.append(f"Current earnings exhibit: {type(exc).__name__}: {exc}")
    if packet.previous and packet.previous.exhibit_url:
        try:
            packet.previous_release_text = fetch_document_text(packet.previous.exhibit_url, contact_email, max_chars=500_000)
            packet.sources["previous_earnings_release"] = packet.previous.exhibit_url
        except Exception as exc:
            packet.errors.append(f"Previous earnings exhibit: {type(exc).__name__}: {exc}")

    return packet
