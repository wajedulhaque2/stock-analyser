from pathlib import Path

import pytest

from stock_analyser.fiscal_ai import (
    FiscalClient,
    FiscalApiError,
    latest_event_resources,
    load_earnings_bundle,
    qa_pairs,
    read_env_key,
    resolve_company,
    search_transcript,
    transcript_event_key,
    transcript_topic_hits,
    write_env_key,
)


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload


class Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params or {}, headers or {}, timeout))
        path = url.split("api.fiscal.ai", 1)[-1]
        value = self.routes[path]
        if callable(value):
            return value(params or {})
        return Response(*value)


def company_row():
    return {
        "companyKey": "NASDAQ_META",
        "companyFiscalIdentifier": "FSCLCMETA",
        "displayNameEnglish": "Meta Platforms, Inc.",
        "primaryListing": {"ticker": "META", "exchangeCode": "NASDAQ"},
        "availableDatasets": ["financials", "investor_relations"],
        "reportingCurrency": "USD",
        "sector": "Communication Services",
    }


def transcript_payload():
    return {
        "event": {"eventKey": "q2-2026", "fiscalYear": 2026, "fiscalQuarter": 2},
        "speakers": [
            {"speaker": 1, "name": "CFO", "role": "Chief Financial Officer", "speakerType": "company_management"},
            {"speaker": 2, "name": "Analyst A", "role": "Analyst", "speakerType": "analyst"},
        ],
        "transcript": {"paragraphs": [
            {"text": "We expect full-year capital expenditures to be between $130 billion and $145 billion.", "start": 10, "end": 20, "speaker": 1},
            {"text": "Our operating margin reflects higher infrastructure expenses.", "start": 25, "end": 30, "speaker": 1},
            {"text": "Can you discuss AI infrastructure returns and the capex outlook?", "start": 100, "end": 110, "speaker": 2},
            {"text": "We expect infrastructure investment to support future revenue growth.", "start": 111, "end": 120, "speaker": 1},
        ]},
        "sections": [{"sectionType": "qa", "start": 90, "end": 200}],
        "stats": {"speakerCount": 2, "paragraphCount": 4, "sentenceCount": 8},
    }


def test_fiscal_uses_header_api_key():
    session = Session({"/v3/companies-list": (200, {"data": [company_row()]})})
    client = FiscalClient("secret-key", session=session)
    client.companies()
    assert session.calls[0][2]["X-Api-Key"] == "secret-key"
    assert "apiKey" not in session.calls[0][1]


def test_resolve_meta_from_free_company_list():
    session = Session({"/v3/companies-list": (200, {"data": [company_row()]})})
    company = resolve_company(FiscalClient("k", session=session), "META", "Meta Platforms, Inc.")
    assert company.company_key == "NASDAQ_META"
    assert company.ticker == "META"


def test_yahoo_suffix_can_resolve_by_company_name():
    shell = company_row()
    shell.update({"companyKey": "NYSE_SHEL", "displayNameEnglish": "Shell plc", "primaryListing": {"ticker": "SHEL", "exchangeCode": "NYSE"}})
    session = Session({"/v3/companies-list": (200, {"data": [shell]})})
    company = resolve_company(FiscalClient("k", session=session), "SHEL.L", "Shell plc")
    assert company.company_key == "NYSE_SHEL"


def test_bundle_keeps_accessible_data_if_transcript_feature_missing():
    routes = {
        "/v3/companies-list": (200, {"data": [company_row()]}),
        "/v3/company/profile": (200, company_row()),
        "/v1/company/earnings-summary": (200, [{"symbol": "META", "date": "2026-07-30", "period": "Q2 2026", "epsActual": 7.14, "revenueActual": 60800000000}]),
        "/v1/company/ir-events": (403, {"error": "You do not have access to this feature"}),
    }
    bundle = load_earnings_bundle(FiscalClient("k", session=Session(routes)), "META", "Meta Platforms")
    assert bundle.capabilities["earnings_summary"] is True
    assert bundle.capabilities["investor_relations"] is False
    assert bundle.earnings_summary[0]["revenueActual"] == 60800000000
    assert any("IR events" in d for d in bundle.diagnostics)


def test_latest_event_and_transcript_key():
    rows = [
        {"eventDate": "2026-04-30", "fiscalYear": 2026, "fiscalQuarter": 1, "role": "transcript", "resourceType": "transcript", "resourceId": "q1-2026"},
        {"eventDate": "2026-07-30", "fiscalYear": 2026, "fiscalQuarter": 2, "role": "press_release", "resourceType": "document", "resourceId": "release"},
        {"eventDate": "2026-07-30", "fiscalYear": 2026, "fiscalQuarter": 2, "role": "transcript", "resourceType": "transcript", "resourceId": "q2-2026"},
    ]
    latest = latest_event_resources(rows)
    assert len(latest) == 2
    assert transcript_event_key(latest) == "q2-2026"


def test_transcript_topic_hits_and_qa_pairs_are_deterministic():
    tx = transcript_payload()
    hits = transcript_topic_hits(tx, per_topic=2)
    assert any(h["topic"] == "Cash flow / CapEx" and "$130 billion" in h["excerpt"] for h in hits)
    pairs = qa_pairs(tx)
    assert pairs[0]["analyst"] == "Analyst A"
    assert pairs[0]["executive"] == "CFO"
    assert "infrastructure investment" in pairs[0]["answer"]


def test_transcript_search():
    rows = search_transcript(transcript_payload(), "capex outlook")
    assert rows
    assert "capex" in rows[0]["text"].lower()


def test_env_key_roundtrip(tmp_path: Path):
    path = write_env_key(tmp_path, "abc123")
    assert path.name == ".env"
    assert read_env_key(tmp_path) == "abc123"
    write_env_key(tmp_path, "")
    assert read_env_key(tmp_path) == ""


def test_api_errors_include_server_message():
    session = Session({"/v3/companies-list": (403, {"error": "Invalid API key"})})
    with pytest.raises(FiscalApiError, match="Invalid API key"):
        FiscalClient("bad", session=session).companies()
