from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

import requests

BASE_URL = "https://api.fiscal.ai"


class FiscalApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FiscalCompany:
    company_key: str
    display_name: str
    ticker: str
    exchange_code: str = ""
    fiscal_identifier: str = ""
    datasets: list[str] = field(default_factory=list)
    reporting_currency: str = ""
    sector: str = ""
    cik: str = ""


@dataclass
class FiscalEarningsBundle:
    company: FiscalCompany
    profile: dict[str, Any] = field(default_factory=dict)
    earnings_summary: list[dict[str, Any]] = field(default_factory=list)
    ir_events: list[dict[str, Any]] = field(default_factory=list)
    transcript: dict[str, Any] | None = None
    latest_event_key: str = ""
    diagnostics: list[str] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("error"), str):
            return payload["error"]
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            item = errors[0]
            if isinstance(item, dict) and isinstance(item.get("message"), str):
                return item["message"]
        if isinstance(errors, dict):
            # Older endpoints can return a nested error schema.
            msg = errors.get("message")
            if isinstance(msg, str):
                return msg
    return fallback


class FiscalClient:
    def __init__(self, api_key: str, timeout: float = 20.0, session: requests.Session | None = None):
        if not api_key or not api_key.strip():
            raise ValueError("A Fiscal.ai API key is required.")
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.session = session or requests.Session()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
        headers = {"X-Api-Key": self.api_key, "Accept": "application/json", "User-Agent": "StockAnalyser/0.7.1"}
        try:
            response = self.session.get(url, params=params or {}, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FiscalApiError(f"Fiscal.ai request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            fallback = f"Fiscal.ai returned HTTP {response.status_code}."
            raise FiscalApiError(_error_message(payload, fallback), response.status_code)
        return payload

    def companies(self) -> list[dict[str, Any]]:
        payload = self.get("/v3/companies-list", {"compact": "true", "pageNumber": 1})
        if isinstance(payload, dict):
            data = payload.get("data", [])
            return data if isinstance(data, list) else []
        return payload if isinstance(payload, list) else []

    def profile(self, company_key: str) -> dict[str, Any]:
        payload = self.get("/v3/company/profile", {"companyKey": company_key})
        return payload if isinstance(payload, dict) else {}

    def earnings_summary(self, company_key: str) -> list[dict[str, Any]]:
        payload = self.get("/v1/company/earnings-summary", {"companyKey": company_key})
        return payload if isinstance(payload, list) else []

    def ir_events(self, company_key: str) -> list[dict[str, Any]]:
        payload = self.get("/v1/company/ir-events", {"companyKey": company_key})
        return payload if isinstance(payload, list) else []

    def transcript(self, company_key: str, event_key: str) -> dict[str, Any]:
        payload = self.get(f"/v1/company/ir-events/transcript/{event_key}", {"companyKey": company_key})
        return payload if isinstance(payload, dict) else {}


def normalize_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    # Yahoo suffixes identify venues (.L, .TO, .PA, etc.). Fiscal resolves the company separately.
    if "." in ticker and ticker not in {"BRK.A", "BRK.B"}:
        ticker = ticker.split(".", 1)[0]
    return ticker.replace("-", ".") if ticker.startswith("BRK-") else ticker


def _norm_name(value: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower())
    stop = {"inc", "incorporated", "corp", "corporation", "plc", "ltd", "limited", "company", "co", "holdings", "holding"}
    return " ".join(tok for tok in text.split() if tok not in stop)


def resolve_company(client: FiscalClient, ticker: str, company_name: str = "") -> FiscalCompany:
    wanted = normalize_ticker(ticker)
    rows = client.companies()
    exact: list[dict[str, Any]] = []
    for row in rows:
        listing = row.get("primaryListing") or {}
        candidate = normalize_ticker(str(listing.get("ticker") or row.get("ticker") or ""))
        company_key = str(row.get("companyKey") or "")
        suffix = normalize_ticker(company_key.split("_", 1)[-1]) if "_" in company_key else ""
        if wanted and wanted in {candidate, suffix}:
            exact.append(row)
    candidates = exact
    if not candidates and company_name:
        target = _norm_name(company_name)
        for row in rows:
            display = _norm_name(str(row.get("displayNameEnglish") or row.get("name") or ""))
            if target and display and (target in display or display in target):
                candidates.append(row)
    if not candidates:
        raise FiscalApiError(
            f"{ticker} was not found in the companies available to this Fiscal.ai API key. "
            "The free trial is restricted to its included company universe.",
            404,
        )
    row = candidates[0]
    listing = row.get("primaryListing") or {}
    return FiscalCompany(
        company_key=str(row.get("companyKey") or ""),
        display_name=str(row.get("displayNameEnglish") or row.get("name") or company_name or ticker),
        ticker=str(listing.get("ticker") or row.get("ticker") or wanted),
        exchange_code=str(listing.get("exchangeCode") or row.get("exchangeSymbol") or ""),
        fiscal_identifier=str(row.get("companyFiscalIdentifier") or ""),
        datasets=list(row.get("availableDatasets") or []),
        reporting_currency=str(row.get("reportingCurrency") or ""),
        sector=str(row.get("sector") or ""),
        cik=str(row.get("cik") or ""),
    )


def _company_from_profile(base: FiscalCompany, profile: dict[str, Any]) -> FiscalCompany:
    listing = profile.get("primaryListing") or {}
    return FiscalCompany(
        company_key=str(profile.get("companyKey") or base.company_key),
        display_name=str(profile.get("displayNameEnglish") or base.display_name),
        ticker=str(listing.get("ticker") or base.ticker),
        exchange_code=str(listing.get("exchangeCode") or base.exchange_code),
        fiscal_identifier=str(profile.get("companyFiscalIdentifier") or base.fiscal_identifier),
        datasets=list(profile.get("availableDatasets") or base.datasets),
        reporting_currency=str(profile.get("reportingCurrency") or base.reporting_currency),
        sector=str(profile.get("sector") or base.sector),
        cik=str(profile.get("cik") or base.cik),
    )


def _event_sort_key(row: dict[str, Any]) -> tuple:
    return (
        str(row.get("eventDate") or ""),
        int(row.get("fiscalYear") or 0),
        int(row.get("fiscalQuarter") or 0),
    )


def latest_event_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max(rows, key=_event_sort_key)
    fy = latest.get("fiscalYear")
    fq = latest.get("fiscalQuarter")
    if fy is not None and fq is not None:
        grouped = [r for r in rows if r.get("fiscalYear") == fy and r.get("fiscalQuarter") == fq]
        if grouped:
            return sorted(grouped, key=lambda r: (str(r.get("role") or ""), str(r.get("resourceType") or "")))
    date = latest.get("eventDate")
    return [r for r in rows if r.get("eventDate") == date]


def transcript_event_key(resources: list[dict[str, Any]]) -> str:
    for row in resources:
        if str(row.get("role") or "").lower() == "transcript" or str(row.get("resourceType") or "").lower() == "transcript":
            return str(row.get("resourceId") or "")
    return ""


def load_earnings_bundle(client: FiscalClient, ticker: str, company_name: str = "") -> FiscalEarningsBundle:
    company = resolve_company(client, ticker, company_name)
    diagnostics: list[str] = []
    capabilities = {"profile": False, "earnings_summary": False, "investor_relations": False, "transcript": False}

    try:
        profile = client.profile(company.company_key)
        company = _company_from_profile(company, profile)
        capabilities["profile"] = True
    except FiscalApiError as exc:
        profile = {}
        diagnostics.append(f"Profile: {exc}")

    try:
        earnings = client.earnings_summary(company.company_key)
        capabilities["earnings_summary"] = True
    except FiscalApiError as exc:
        earnings = []
        diagnostics.append(f"Earnings summary: {exc}")

    try:
        events = client.ir_events(company.company_key)
        capabilities["investor_relations"] = True
    except FiscalApiError as exc:
        events = []
        diagnostics.append(f"IR events: {exc}")

    latest_resources = latest_event_resources(events)
    event_key = transcript_event_key(latest_resources)
    transcript = None
    if event_key:
        try:
            transcript = client.transcript(company.company_key, event_key)
            capabilities["transcript"] = bool(transcript)
        except FiscalApiError as exc:
            diagnostics.append(f"Transcript: {exc}")

    return FiscalEarningsBundle(
        company=company,
        profile=profile,
        earnings_summary=earnings,
        ir_events=events,
        transcript=transcript,
        latest_event_key=event_key,
        diagnostics=diagnostics,
        capabilities=capabilities,
    )


def _speaker_lookup(transcript: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for speaker in transcript.get("speakers", []) or []:
        try:
            out[int(speaker.get("speaker"))] = speaker
        except (TypeError, ValueError):
            continue
    return out


def _paragraph_rows(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    speakers = _speaker_lookup(transcript)
    rows: list[dict[str, Any]] = []
    for p in ((transcript.get("transcript") or {}).get("paragraphs") or []):
        try:
            sid = int(p.get("speaker"))
        except (TypeError, ValueError):
            sid = -1
        sp = speakers.get(sid, {})
        rows.append({
            "text": str(p.get("text") or "").strip(),
            "start": float(p.get("start") or 0.0),
            "end": float(p.get("end") or 0.0),
            "speaker": str(sp.get("name") or sp.get("label") or "Unknown"),
            "role": str(sp.get("role") or ""),
            "speaker_type": str(sp.get("speakerType") or "unknown"),
        })
    return [r for r in rows if r["text"]]


TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Guidance / outlook": ["guidance", "outlook", "we expect", "we anticipate", "we forecast", "we project", "full-year", "full year"],
    "Cash flow / CapEx": ["free cash flow", "cash flow", "capex", "capital expenditure", "capital expenditures", "capital spending", "finance lease"],
    "Margins / costs": ["operating margin", "gross margin", "margin", "cost of revenue", "expense growth", "expenses"],
    "Growth / demand": ["revenue growth", "demand", "engagement", "impressions", "price per ad", "customers", "bookings", "backlog"],
    "Investment / strategy": ["artificial intelligence", " ai ", "infrastructure", "data center", "data centre", "investment cycle", "monetization", "monetisation"],
    "Risks / headwinds": ["headwind", "uncertainty", "regulatory", "competition", "currency", "foreign exchange", "risk", "constraint"],
}


def transcript_topic_hits(transcript: dict[str, Any], per_topic: int = 3) -> list[dict[str, Any]]:
    rows = _paragraph_rows(transcript)
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for topic, keywords in TOPIC_KEYWORDS.items():
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if row["speaker_type"] not in {"company_management", "other", "unknown"}:
                continue
            low = f" {row['text'].lower()} "
            score = sum(1 for kw in keywords if kw in low)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], x[1]["start"]))
        for score, row in scored[:per_topic]:
            key = (topic, row["text"])
            if key in seen:
                continue
            seen.add(key)
            hits.append({"topic": topic, "speaker": row["speaker"], "role": row["role"], "excerpt": row["text"], "score": score, "start": row["start"]})
    return hits


def qa_pairs(transcript: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows = _paragraph_rows(transcript)
    qa_start = None
    for sec in transcript.get("sections", []) or []:
        if str(sec.get("sectionType") or "").lower() == "qa":
            try:
                qa_start = float(sec.get("start"))
            except (TypeError, ValueError):
                qa_start = None
            break
    if qa_start is not None:
        rows = [r for r in rows if r["start"] >= qa_start]
    pairs: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if row["speaker_type"] != "analyst":
            continue
        answer = None
        for nxt in rows[i + 1:i + 5]:
            if nxt["speaker_type"] == "company_management":
                answer = nxt
                break
        pairs.append({
            "analyst": row["speaker"],
            "question": row["text"],
            "executive": answer["speaker"] if answer else "",
            "answer": answer["text"] if answer else "",
            "start": row["start"],
        })
        if len(pairs) >= limit:
            break
    return pairs


def search_transcript(transcript: dict[str, Any], query: str, limit: int = 10) -> list[dict[str, Any]]:
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9%]+", query or "") if len(t) > 1]
    if not terms:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in _paragraph_rows(transcript):
        low = row["text"].lower()
        score = sum(low.count(term) for term in terms)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], x[1]["start"]))
    return [{**row, "score": score} for score, row in scored[:limit]]


def _env_paths(root: Path) -> list[Path]:
    paths = [Path(root) / ".env"]
    # Reuse the same private key store across downloaded draft folders on Windows.
    import os
    local = os.getenv("LOCALAPPDATA")
    if local:
        paths.append(Path(local) / "StockAnalyser" / ".env")
    return paths


def read_env_key(root: Path, name: str = "FISCAL_API_KEY") -> str:
    for env_path in _env_paths(root):
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def write_env_key(root: Path, value: str, name: str = "FISCAL_API_KEY") -> Path:
    import os
    local = os.getenv("LOCALAPPDATA")
    env_path = (Path(local) / "StockAnalyser" / ".env") if local else (Path(root) / ".env")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if env_path.exists():
        try:
            existing = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []
    output: list[str] = []
    replaced = False
    for line in existing:
        if line.strip().startswith(f"{name}="):
            output.append(f"{name}={value.strip()}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{name}={value.strip()}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return env_path
