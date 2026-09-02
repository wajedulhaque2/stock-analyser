from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import inspect
from pathlib import Path

import pytest
import requests

import stock_analyser.application.live_configuration as configuration_module
import stock_analyser.application.v1_research as coordinator_module
import stock_analyser.live_identity as identity_module
import stock_analyser.live_own_history_audit as own_history_module
from stock_analyser.application.live_configuration import (
    LiveConfigurationSource,
    prepare_live_environment,
)
from stock_analyser.application.research_build_context import ResearchBuildContext
from stock_analyser.application.v1_research import (
    ResearchReportBuildStatus,
    ResearchReportPipelineStage,
    V1ResearchCoordinator,
)
from stock_analyser.domain import CapabilityResult, CapabilityStatus
from stock_analyser.live_identity import (
    IdentityClientStatus,
    IdentityConfigurationStatus,
    IdentityDiagnosticStage,
    IdentityDiagnosticStatus,
    IdentityHttpStatusCategory,
    IdentityLiveDiagnostic,
    LiveIdentityResolution,
    resolve_live_target_identity,
)
from stock_analyser.live_own_history_audit import run_live_own_history_audit
from stock_analyser.live_smoke import LiveFiscalSource
from stock_analyser.providers import ProviderId, ProviderIdentityResult


NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
SECRET = "SYNTHETIC_SECRET_MUST_NEVER_APPEAR"


class Response:
    headers = {}

    def __init__(self, status_code=200, body=None, *, parse_error=False):
        self.status_code = status_code
        self.body = body
        self.parse_error = parse_error

    def json(self):
        if self.parse_error:
            raise ValueError("raw response and secret must stay private")
        return self.body


class Session:
    def __init__(self, responses=(), *, failure=None):
        self.responses = list(responses)
        self.failure = failure
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.failure is not None:
            raise self.failure
        return self.responses.pop(0)


def fiscal_company(*, complete=True):
    listing = {
        "ticker": "SYN",
        "exchangeCode": "NASDAQ",
        "exchangeCountryCode": "US",
        "tradingCurrency": "USD",
        "securityFiscalIdentifier": "FISCAL-SEC-SYN",
        "securityType": "EQUITY",
    }
    row = {
        "companyKey": "NASDAQ_SYN",
        "companyFiscalIdentifier": "FISCAL-ISSUER-SYN",
        "displayNameEnglish": "Synthetic Corp",
        "reportingCurrency": "USD",
        "primaryListing": listing,
    }
    if not complete:
        row["primaryListing"] = {"ticker": "SYN"}
        row.pop("companyFiscalIdentifier")
    return row


def fiscal_profile(*, complete=True):
    if not complete:
        return {}
    return {
        "companyFiscalIdentifier": "FISCAL-ISSUER-SYN",
        "displayNameEnglish": "Synthetic Corp",
        "legalDomicileCountryCode": "US",
        "sector": "Technology",
        "industry": "Software",
        "reportingCurrency": "USD",
        "fiscalYearEnd": "12-31",
        "primaryListing": fiscal_company()["primaryListing"],
    }


def unavailable_yahoo_result():
    return ProviderIdentityResult(
        candidate=None,
        capability=CapabilityResult(
            provider=ProviderId.YAHOO.value,
            capability="identity",
            status=CapabilityStatus.UNAVAILABLE,
            checked_at=NOW,
            reason="synthetic Yahoo evidence unavailable",
        ),
    )


def install_fiscal_session(monkeypatch, session):
    original = LiveFiscalSource
    monkeypatch.setattr(
        identity_module,
        "LiveFiscalSource",
        lambda *, observer=None: original(session=session, observer=observer),
    )


def install_unavailable_yahoo(monkeypatch):
    class Adapter:
        def __init__(self, source):
            pass

        def fetch_identity(self, symbol):
            return unavailable_yahoo_result()

    monkeypatch.setattr(identity_module, "LiveYahooSource", lambda: object())
    monkeypatch.setattr(identity_module, "YahooAdapter", Adapter)


def resolve(monkeypatch, responses):
    session = Session(responses)
    install_fiscal_session(monkeypatch, session)
    install_unavailable_yahoo(monkeypatch)
    result = resolve_live_target_identity(
        "SYN", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )
    return result, session


def test_ambient_configuration_uses_the_approved_dotenv_search_without_mutating_it(monkeypatch, tmp_path):
    ambient = {}
    monkeypatch.setattr(configuration_module.os, "environ", ambient)
    (tmp_path / ".env").write_text(f"FISCAL_API_KEY={SECRET}\n", encoding="utf-8")

    resolved, source = prepare_live_environment(ambient, project_root=tmp_path)

    assert source is LiveConfigurationSource.ENVIRONMENT
    assert resolved["FISCAL_API_KEY"] == SECRET
    assert ambient == {}


def test_explicit_configuration_is_authoritative_and_does_not_gain_ambient_secrets(tmp_path):
    (tmp_path / ".env").write_text(f"FISCAL_API_KEY={SECRET}\n", encoding="utf-8")
    resolved, source = prepare_live_environment({}, project_root=tmp_path)
    assert source is LiveConfigurationSource.EXPLICIT_INJECTED
    assert "FISCAL_API_KEY" not in resolved


def test_missing_configuration_fails_before_client_or_request_and_is_secret_safe():
    result = resolve_live_target_identity("SYN", environment={}, analysis_as_of=NOW)
    diagnostic = result.diagnostic
    assert result.identity is None
    assert diagnostic.configuration_status is IdentityConfigurationStatus.MISSING
    assert diagnostic.configuration_source is LiveConfigurationSource.MISSING
    assert diagnostic.client_status is IdentityClientStatus.NOT_CONSTRUCTED
    assert diagnostic.request_attempted is False
    assert diagnostic.blocking_stage is IdentityDiagnosticStage.CONFIGURATION
    assert SECRET not in repr(diagnostic)


def test_client_construction_failure_is_classified_without_exception_repr(monkeypatch):
    monkeypatch.setattr(
        identity_module, "build_live_fiscal_adapter",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"private {SECRET}")),
    )
    result = resolve_live_target_identity(
        "SYN", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )
    assert result.diagnostic.client_status is IdentityClientStatus.FAILED
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.CLIENT_CONSTRUCTION
    assert SECRET not in repr(result)
    assert "RuntimeError" not in repr(result)


def test_request_failure_records_attempt_and_transport_category_without_building_yahoo(monkeypatch):
    session = Session(failure=requests.ConnectionError(f"private {SECRET}"))
    install_fiscal_session(monkeypatch, session)
    monkeypatch.setattr(
        identity_module, "YahooAdapter",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yahoo must not replace Fiscal")),
    )
    result = resolve_live_target_identity(
        "SYN", environment={"FISCAL_API_KEY": SECRET}, analysis_as_of=NOW,
    )
    assert result.yahoo_result is None
    assert result.diagnostic.request_attempted is True
    assert result.diagnostic.http_status_category is IdentityHttpStatusCategory.TRANSPORT
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.REQUEST
    assert SECRET not in repr(result)


@pytest.mark.parametrize(
    "status,category",
    [(401, IdentityHttpStatusCategory.AUTHENTICATION),
     (403, IdentityHttpStatusCategory.ACCESS_DENIED),
     (404, IdentityHttpStatusCategory.NOT_FOUND),
     (429, IdentityHttpStatusCategory.RATE_LIMIT),
     (500, IdentityHttpStatusCategory.SERVER)],
)
def test_http_failures_use_safe_access_categories(monkeypatch, status, category):
    result, _ = resolve(monkeypatch, (Response(status, {}),))
    diagnostic = result.diagnostic
    assert result.identity is None
    assert diagnostic.request_attempted is True
    assert diagnostic.http_status_category is category
    assert diagnostic.blocking_stage is IdentityDiagnosticStage.HTTP_RESPONSE
    assert diagnostic.parse_status is IdentityDiagnosticStatus.NOT_ATTEMPTED


def test_parse_failure_is_classified_and_raw_response_is_absent(monkeypatch):
    result, _ = resolve(monkeypatch, (Response(200, parse_error=True),))
    assert result.diagnostic.http_status_category is IdentityHttpStatusCategory.SUCCESS
    assert result.diagnostic.parse_status is IdentityDiagnosticStatus.FAILED
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.PARSE
    rendered = repr(result.diagnostic).lower()
    assert "raw response" not in rendered
    assert "headers" not in rendered
    assert "companykey" not in rendered


def test_normalization_failure_after_valid_json_is_classified(monkeypatch):
    result, _ = resolve(monkeypatch, (Response(200, {"data": []}),))
    assert result.identity is None
    assert result.diagnostic.parse_status is IdentityDiagnosticStatus.SUCCEEDED
    assert result.diagnostic.normalization_status is IdentityDiagnosticStatus.FAILED
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.NORMALIZATION


def test_canonical_assembly_failure_is_distinct_from_fiscal_normalization(monkeypatch):
    result, _ = resolve(monkeypatch, (
        Response(200, {"data": [fiscal_company()]}),
        Response(200, fiscal_profile(complete=False)),
    ))
    assert result.identity is None
    assert result.diagnostic.normalization_status is IdentityDiagnosticStatus.SUCCEEDED
    assert result.diagnostic.canonical_identity_status is IdentityDiagnosticStatus.FAILED
    assert result.diagnostic.blocking_stage is IdentityDiagnosticStage.CANONICAL_ASSEMBLY


def test_success_is_complete_uses_approved_request_contract_and_registers_only_normalized_identity(monkeypatch):
    result, session = resolve(monkeypatch, (
        Response(200, {"data": [fiscal_company()]}),
        Response(200, fiscal_profile()),
    ))
    context = ResearchBuildContext("SYN", NOW)
    session = Session((
        Response(200, {"data": [fiscal_company()]}),
        Response(200, fiscal_profile()),
    ))
    install_fiscal_session(monkeypatch, session)
    registered = resolve_live_target_identity(
        "SYN",
        environment={"FISCAL_API_KEY": SECRET},
        analysis_as_of=NOW,
        build_context=context,
        configuration_source=LiveConfigurationSource.EXPLICIT_INJECTED,
    )
    assert result.identity is not None and registered.identity is not None
    assert registered.diagnostic.blocking_stage is IdentityDiagnosticStage.COMPLETE
    assert registered.diagnostic.canonical_identity_status is IdentityDiagnosticStatus.SUCCEEDED
    assert registered.identity.security_id == "security:fiscal:FISCAL-SEC-SYN"
    assert registered.identity.issuer_id == "issuer:fiscal:FISCAL-ISSUER-SYN"
    assert context.semantic_provider_call_count == 3
    assert len(session.calls) == 2
    assert any(item.capability == "target_identity" for item in context.evidence_accesses)
    for _, kwargs in session.calls:
        assert kwargs["headers"]["User-Agent"] == "StockAnalyser/0.7.1"
        assert kwargs["headers"]["X-Api-Key"] == SECRET
    diagnostic_fields = {item.name for item in fields(registered.diagnostic)}
    assert diagnostic_fields.isdisjoint({"payload", "response", "headers", "companyKey", "api_key", "url"})
    assert SECRET not in repr(registered.diagnostic)


def failed_resolution(source):
    return LiveIdentityResolution(
        identity=None,
        fiscal_result=None,
        yahoo_result=None,
        issues=(),
        diagnostic=IdentityLiveDiagnostic(
            provider="fiscal",
            configuration_status=IdentityConfigurationStatus.MISSING,
            configuration_source=LiveConfigurationSource.MISSING,
            client_status=IdentityClientStatus.NOT_CONSTRUCTED,
            request_attempted=False,
            http_status_category=IdentityHttpStatusCategory.NOT_ATTEMPTED,
            parse_status=IdentityDiagnosticStatus.NOT_ATTEMPTED,
            normalization_status=IdentityDiagnosticStatus.NOT_ATTEMPTED,
            canonical_identity_status=IdentityDiagnosticStatus.NOT_ATTEMPTED,
            blocking_stage=IdentityDiagnosticStage.CONFIGURATION,
            safe_reason="Fiscal identity configuration is unavailable.",
        ),
    )


def test_direct_and_coordinator_paths_delegate_to_the_same_identity_service(monkeypatch):
    calls = []

    def shared(symbol, **kwargs):
        calls.append((symbol, kwargs["configuration_source"], dict(kwargs["environment"])))
        return failed_resolution(kwargs["configuration_source"])

    monkeypatch.setattr(own_history_module, "resolve_live_target_identity", shared)
    environment = {"FISCAL_API_KEY": SECRET}
    direct = run_live_own_history_audit(
        "SYN",
        environment=environment,
        analysis_as_of=NOW,
        identity_configuration_source=LiveConfigurationSource.EXPLICIT_INJECTED,
    )
    coordinated = V1ResearchCoordinator().build(
        "SYN", environment=environment, analysis_as_of=NOW,
    )
    assert direct.identity_diagnostic is not None
    assert coordinated.identity_diagnostic is not None
    assert coordinated.status is ResearchReportBuildStatus.UNAVAILABLE
    assert coordinated.stage is ResearchReportPipelineStage.IDENTITY
    assert [(item[0], item[1]) for item in calls] == [
        ("SYN", LiveConfigurationSource.EXPLICIT_INJECTED),
        ("SYN", LiveConfigurationSource.EXPLICIT_INJECTED),
    ]
    assert all(item[2] == environment for item in calls)
    coordinator_source = inspect.getsource(coordinator_module._default_runner)
    assert "FiscalAdapter" not in coordinator_source
    assert "resolve_company_identity" not in coordinator_source


def test_no_ticker_specific_branches_or_identity_equality_fallback_exist():
    source = Path(identity_module.__file__).read_text(encoding="utf-8")
    assert '"META"' not in source
    assert '"MSFT"' not in source
    assert "GOOG" not in source
    assert "GOOGL" not in source
    assert "ticker equality" not in source.lower()
