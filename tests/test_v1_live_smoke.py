from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from stock_analyser.domain import CapabilityStatus
from stock_analyser.live_smoke import (
    LiveFiscalSource,
    NormalizationStatus,
    ProviderSmokeSummary,
    RequestsJsonTransport,
    SafeCapabilitySummary,
    _SafeRequestSource,
    build_live_runners,
    credential_presence,
    load_dotenv_safely,
    parse_provider_selection,
    render_json,
    render_text,
    run_selected,
    _secret,
)
from stock_analyser.providers import (
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    TransportRequest,
)


SECRET = "SYNTHETIC_LIVE_SMOKE_SECRET_MUST_NOT_APPEAR"


def summary(
    provider: str,
    status: NormalizationStatus = NormalizationStatus.PASS,
    capability_status: CapabilityStatus = CapabilityStatus.AVAILABLE,
) -> ProviderSmokeSummary:
    return ProviderSmokeSummary(
        provider=provider,
        normalization=status,
        capabilities=(SafeCapabilitySummary("synthetic_capability", capability_status),),
        observation_count=1 if capability_status is CapabilityStatus.AVAILABLE else 0,
        metric_ids=("revenue",) if capability_status is CapabilityStatus.AVAILABLE else (),
        annual_period_count=1 if capability_status is CapabilityStatus.AVAILABLE else 0,
    )


def test_importing_cli_does_not_execute_smoke_or_network(monkeypatch):
    called = []
    monkeypatch.setattr("stock_analyser.live_smoke.run_selected", lambda **kwargs: called.append(kwargs))
    path = Path("scripts/v1_provider_smoke.py").resolve()
    spec = importlib.util.spec_from_file_location("v1_provider_smoke_import_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert called == []


def test_dotenv_loading_and_presence_never_return_credential_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"FMP_API_KEY={SECRET}\nFINNHUB_API_KEY='another-secret'\n", encoding="utf-8")
    environment = {}
    loaded = load_dotenv_safely((env_file,), environment)
    presence = credential_presence(environment)
    assert set(loaded) == {"FMP_API_KEY", "FINNHUB_API_KEY"}
    assert presence["fmp"] and presence["finnhub"]
    assert SECRET not in repr(loaded)
    assert SECRET not in repr(presence)


def test_fiscal_live_request_matches_proven_auth_and_documented_list_contract():
    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"data": []}

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    source = LiveFiscalSource(session=session)
    assert source._api_get(
        "/v3/companies-list",
        credential=SECRET,
        params={"compact": "true", "pageNumber": 1},
    ) == {"data": []}
    url, kwargs = session.calls[0]
    assert url == "https://api.fiscal.ai/v3/companies-list"
    assert kwargs["params"] == {"compact": "true", "pageNumber": 1}
    assert kwargs["headers"] == {
        "X-Api-Key": SECRET,
        "Accept": "application/json",
        "User-Agent": "StockAnalyser/0.7.1",
    }
    assert kwargs["timeout"] == 20


def test_fiscal_final_prepared_request_preserves_resolved_secret_and_contract():
    from urllib.parse import parse_qsl, urlparse

    import requests

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"data": []}

    class PreparingSession:
        def __init__(self):
            self.prepared = []

        def get(self, url, **kwargs):
            request = requests.Request(
                "GET", url, params=kwargs["params"], headers=kwargs["headers"],
            )
            self.prepared.append(request.prepare())
            return Response()

    session = PreparingSession()
    source = LiveFiscalSource(session=session)
    source._api_get(
        "/v3/companies-list",
        credential=SECRET,
        params={"compact": "true", "pageNumber": 1},
    )

    prepared = session.prepared[0]
    parsed = urlparse(prepared.url)
    assert (parsed.scheme, parsed.hostname, parsed.path) == (
        "https", "api.fiscal.ai", "/v3/companies-list",
    )
    assert prepared.method == "GET"
    assert dict(parse_qsl(parsed.query)) == {"compact": "true", "pageNumber": "1"}
    assert prepared.headers["Accept"] == "application/json"
    assert prepared.headers["User-Agent"] == "StockAnalyser/0.7.1"
    assert prepared.headers["X-Api-Key"] == SECRET
    assert prepared.headers["X-Api-Key"] != "FISCAL_API_KEY"


def test_fiscal_profile_uses_stable_id_and_financial_requests_use_proven_company_key_contract():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                return Response({"data": [{
                    "companyKey": "NASDAQ_SYN",
                    "companyFiscalIdentifier": "FISCAL-SYN",
                    "displayNameEnglish": "Synthetic Company",
                    "reportingCurrency": "USD",
                    "primaryListing": {
                        "ticker": "SYN", "country": "US", "exchangeCode": "NASDAQ",
                        "listingFiscalIdentifier": "LIST-SYN",
                        "securityFiscalIdentifier": "SEC-SYN",
                    },
                }]})
            if url.endswith("/v3/company/profile"):
                return Response({
                    "companyFiscalIdentifier": "FISCAL-SYN",
                    "displayNameEnglish": "Synthetic Company",
                    "primaryListing": {
                        "ticker": "SYN", "country": "US", "exchangeCode": "NASDAQ",
                        "listingFiscalIdentifier": "LIST-SYN",
                        "securityFiscalIdentifier": "SEC-SYN",
                    },
                })
            return Response({"data": []})

    session = Session()
    source = LiveFiscalSource(session=session)
    identity = source.identity_metadata("SYN", credential=SECRET)
    rows = tuple(source.standardized_financials("SYN", credential=SECRET))

    assert identity["provider_issuer_id"] == "FISCAL-SYN"
    assert rows == ()
    profile_params = session.calls[1][1]["params"]
    financial_params = [call[1]["params"] for call in session.calls[2:]]
    assert profile_params == {"fscl": "FISCAL-SYN"}
    assert financial_params == [
        {"companyKey": "NASDAQ_SYN", "periodType": "annual,quarterly"},
        {"companyKey": "NASDAQ_SYN", "periodType": "annual,quarterly"},
        {"companyKey": "NASDAQ_SYN", "periodType": "annual,quarterly"},
    ]
    assert all("company" not in params for params in (profile_params, *financial_params))


def test_fiscal_verified_live_report_date_shape_normalizes_without_legacy_change():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Session:
        def get(self, url, **kwargs):
            if url.endswith("/v3/companies-list"):
                return Response({"data": [{
                    "companyKey": "NASDAQ_SYN",
                    "companyFiscalIdentifier": "FISCAL-SYN",
                    "reportingCurrency": "USD",
                    "primaryListing": {"ticker": "SYN"},
                }]})
            if "income-statement" in url:
                return Response({
                    "metrics": [{
                        "standardizedMetricId": "income_statement_total_revenues",
                        "metricName": "Total Revenues",
                    }],
                    "data": [{
                        "periodType": "Annual",
                        "fiscalYear": 2025,
                        "reportDate": "2025-12-31",
                        "metricsValues": {
                            "income_statement_total_revenues": {
                                "value": 100,
                                "currency": "USD",
                            },
                        },
                    }],
                })
            return Response({"data": []})

    rows = tuple(LiveFiscalSource(session=Session()).standardized_financials(
        "SYN", credential=SECRET,
    ))

    assert len(rows) == 1
    assert rows[0] == {
        "metric": "revenue",
        "sourceMetric": "income_statement_total_revenues",
        "periodType": "annual",
        "periodStart": "2025-01-01",
        "periodEnd": "2025-12-31",
        "fiscalYear": 2025,
        "fiscalQuarter": None,
        "asOf": None,
        "value": 100.0,
        "currency": "USD",
        "signConvention": None,
    }


def test_fiscal_verified_bridge_and_shares_shapes_are_bounded_and_normalized():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                return Response({"data": [{
                    "companyKey": "NASDAQ_SYN",
                    "companyFiscalIdentifier": "FISCAL-SYN",
                    "reportingCurrency": "USD",
                    "primaryListing": {"ticker": "SYN"},
                }]})
            if url.endswith("/calculated_tev"):
                return Response([{"date": "2026-08-25", "ratio": 150}])
            if url.endswith("/calculated_market_cap"):
                return Response([{"date": "2026-08-25", "ratio": 100}])
            if url.endswith("/v1/company/shares-outstanding"):
                return Response([{
                    "date": "2026-06-30",
                    "totalSharesOutstanding": 10,
                    "shareClasses": [{"synthetic": "not forwarded"}],
                }])
            raise AssertionError("unexpected Fiscal endpoint")

    session = Session()
    rows = tuple(LiveFiscalSource(session=session).enterprise_bridge_metrics(
        "SYN", credential=SECRET,
    ))
    assert rows == (
        {
            "sourceMetric": "calculated_tev",
            "observationDate": "2026-08-25",
            "asOf": "2026-08-25T00:00:00+00:00",
            "value": 150,
            "currency": "USD",
        },
        {
            "sourceMetric": "calculated_market_cap",
            "observationDate": "2026-08-25",
            "asOf": "2026-08-25T00:00:00+00:00",
            "value": 100,
            "currency": "USD",
        },
        {
            "sourceMetric": "market_data_total_shares_outstanding",
            "observationDate": "2026-06-30",
            "asOf": "2026-06-30T00:00:00+00:00",
            "value": 10,
            "currency": None,
        },
    )
    bridge_calls = session.calls[1:]
    assert [call[0].removeprefix("https://api.fiscal.ai") for call in bridge_calls] == [
        "/v1/company/ratios/daily/calculated_tev",
        "/v1/company/ratios/daily/calculated_market_cap",
        "/v1/company/shares-outstanding",
    ]
    assert all(call[1]["params"] == {"companyKey": "NASDAQ_SYN"} for call in bridge_calls)
    assert "shareClasses" not in repr(rows)


def test_invalid_fred_key_format_fails_safely_without_network(monkeypatch):
    monkeypatch.setattr(
        "stock_analyser.live_smoke._retrying_transport",
        lambda: (_ for _ in ()).throw(AssertionError("network transport must not be built")),
    )
    runners = build_live_runners({"FRED_API_KEY": "not-a-documented-fred-key"})
    result = run_selected(symbol="META", providers=("fred",), runners=runners, strict=True)
    summary = result.summaries[0]
    assert result.exit_code == 1
    assert summary.normalization is NormalizationStatus.FAIL
    assert summary.capabilities[0].status is CapabilityStatus.ERROR
    assert summary.notes == ("configured API key does not match the documented FRED key format",)


def test_missing_credentials_are_omitted_from_live_runner_map_without_failure():
    runners = build_live_runners({})
    assert set(runners) == {"yahoo", "sec"}
    result = run_selected(symbol="META", providers=("fmp",), runners=runners)
    assert result.exit_code == 0
    assert result.summaries[0].normalization is NormalizationStatus.SKIPPED


def test_secret_reference_resolves_the_declared_environment_variable():
    environment = {"FMP_API_KEY": SECRET}

    reference = _secret("fmp", environment)

    assert reference.resolve() == SECRET
    assert SECRET not in repr(reference)


def test_live_generic_http_boundaries_leave_403_unclassified():
    class Response:
        status_code = 403
        headers = {}

        @staticmethod
        def json():
            return {}

    class Session:
        @staticmethod
        def request(*_args, **_kwargs):
            return Response()

        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    request = TransportRequest(
        provider=ProviderId.FMP,
        endpoint_id="synthetic_endpoint",
        method="GET",
        url="https://synthetic.invalid/data",
    )
    transport = RetryingTransport(
        RequestsJsonTransport(Session()),
        retry_policy=RetryPolicy(max_attempts=1, jitter=0),
    )

    with pytest.raises(ProviderError) as transport_error:
        transport.execute(request)
    assert transport_error.value.category is ProviderErrorCategory.UNKNOWN
    assert transport_error.value.category is not ProviderErrorCategory.ENTITLEMENT

    source = _SafeRequestSource(ProviderId.FISCAL, session=Session())
    with pytest.raises(ProviderError) as source_error:
        source._get("synthetic_endpoint", "https://synthetic.invalid/data")
    assert source_error.value.category is ProviderErrorCategory.UNKNOWN
    assert source_error.value.category is not ProviderErrorCategory.ENTITLEMENT


def test_provider_exception_text_and_raw_payload_are_never_rendered():
    def failing(_symbol):
        raise RuntimeError(f"token={SECRET}; raw={{'private': [1, 2, 3]}}")

    result = run_selected(symbol="META", providers=("fmp",), runners={"fmp": failing})
    rendered = render_text(result)
    assert SECRET not in rendered
    assert "private" not in rendered
    assert "provider execution failed" in rendered


def test_locked_and_unavailable_capabilities_are_nonfatal_in_strict_mode():
    runners = {
        "finnhub": lambda _symbol: summary(
            "finnhub", NormalizationStatus.PARTIAL, CapabilityStatus.LOCKED,
        ),
        "alpha": lambda _symbol: summary(
            "alpha", NormalizationStatus.PARTIAL, CapabilityStatus.UNAVAILABLE,
        ),
    }
    result = run_selected(
        symbol="META", providers=("finnhub", "alpha"), runners=runners, strict=True,
    )
    assert result.exit_code == 0


def test_strict_normalization_failure_returns_nonzero():
    result = run_selected(
        symbol="META",
        providers=("fmp",),
        runners={"fmp": lambda _symbol: summary("fmp", NormalizationStatus.FAIL, CapabilityStatus.ERROR)},
        strict=True,
    )
    assert result.exit_code == 1
    assert run_selected(
        symbol="META",
        providers=("fmp",),
        runners={"fmp": lambda _symbol: summary("fmp", NormalizationStatus.FAIL, CapabilityStatus.ERROR)},
        strict=False,
    ).exit_code == 0


def test_provider_selection_is_explicit_ordered_and_validated():
    assert parse_provider_selection("fred,yahoo,fred") == ("fred", "yahoo")
    with pytest.raises(ValueError, match="unsupported"):
        parse_provider_selection("yahoo,unknown")


def test_one_provider_failure_does_not_stop_unrelated_provider():
    def failing(_symbol):
        raise RuntimeError(SECRET)

    result = run_selected(
        symbol="META",
        providers=("fmp", "fred"),
        runners={"fmp": failing, "fred": lambda _symbol: summary("fred")},
        strict=True,
    )
    assert tuple(item.provider for item in result.summaries) == ("fmp", "fred")
    assert result.summaries[0].normalization is NormalizationStatus.FAIL
    assert result.summaries[1].normalization is NormalizationStatus.PASS


def test_json_summary_has_only_safe_normalized_metadata():
    result = run_selected(
        symbol="META", providers=("fmp",), runners={"fmp": lambda _symbol: summary("fmp")},
    )
    encoded = render_json(result)
    parsed = json.loads(encoded)
    assert set(parsed) == {"symbol", "strict", "providers"}
    provider = parsed["providers"][0]
    forbidden = {"apikey", "api_key", "token", "authorization", "headers", "raw", "payload", "url"}
    assert not forbidden.intersection(key.lower() for key in provider)
    assert SECRET not in encoded


def test_symbol_is_runtime_input_with_identical_code_path():
    seen = []
    runner = lambda symbol: (seen.append(symbol) or summary("yahoo"))
    run_selected(symbol="meta", providers=("yahoo",), runners={"yahoo": runner})
    run_selected(symbol="MSFT", providers=("yahoo",), runners={"yahoo": runner})
    assert seen == ["META", "MSFT"]
    source = Path("src/stock_analyser/live_smoke.py").read_text(encoding="utf-8")
    assert 'symbol == "META"' not in source and "symbol == 'META'" not in source


def test_safe_text_is_structural_and_contains_no_financial_values():
    result = run_selected(
        symbol="META", providers=("yahoo",), runners={"yahoo": lambda _symbol: summary("yahoo")},
    )
    text = render_text(result)
    assert "YAHOO" in text and "observations mapped" in text
    assert "revenue" in text
    assert "{" not in text and "}" not in text


def test_smoke_module_does_not_write_payloads_or_import_streamlit():
    source = Path("src/stock_analyser/live_smoke.py").read_text(encoding="utf-8")
    assert "streamlit" not in source
    assert "write_text" not in source and "open(" not in source
    assert "tests/fixtures" not in source and "cache/" not in source


def test_cli_script_is_guarded_and_not_imported_by_application():
    script = Path("scripts/v1_provider_smoke.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' in script
    for application_file in ("src/stock_analyser/app.py", "src/stock_analyser/analysis.py"):
        path = Path(application_file)
        if path.exists():
            assert "live_smoke" not in path.read_text(encoding="utf-8")
