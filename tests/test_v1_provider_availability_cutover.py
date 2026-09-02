from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import inspect
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.application import (
    ProviderAvailabilityStatus,
    ResearchReportBuildResult,
    ResearchReportBuildStatus,
    ResearchReportPipelineStage,
    V1ResearchCoordinator,
    classify_provider_availability,
)
from stock_analyser.application.live_configuration import LiveConfigurationSource
from stock_analyser.live_identity import (
    IdentityCandidateResolutionStatus,
    IdentityClientStatus,
    IdentityConfigurationStatus,
    IdentityDiagnosticStage,
    IdentityDiagnosticStatus,
    IdentityHttpStatusCategory,
    IdentityLiveDiagnostic,
)
from stock_analyser.ui import v1_route
from stock_analyser.ui.feature_flags import is_v1_report_ui_enabled
from stock_analyser.ui.v1_report import V1_REPORT_SESSION_KEY
from stock_analyser.ui.v1_route import (
    V1_BUILD_RESULT_SESSION_KEY,
    V1_CURRENT_SYMBOL_SESSION_KEY,
    V1_SCENARIO_RESULT_SESSION_KEY,
    render_v1_report_route,
)
from test_v1_research_integration import RouteUI, UIService
from test_v1_research_report import NOW, complete_report


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_research.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
TERMS = ROOT / "docs" / "TERMS.md"
APP_SOURCE = ROOT / "app.py"


def diagnostic(
    http: IdentityHttpStatusCategory,
    *,
    canonical=False,
    candidate_status=IdentityCandidateResolutionStatus.NOT_ATTEMPTED,
):
    return IdentityLiveDiagnostic(
        provider="fiscal",
        configuration_status=IdentityConfigurationStatus.CONFIGURED,
        configuration_source=LiveConfigurationSource.EXPLICIT_INJECTED,
        client_status=IdentityClientStatus.CONSTRUCTED,
        request_attempted=True,
        http_status_category=http,
        parse_status=(
            IdentityDiagnosticStatus.SUCCEEDED
            if http is IdentityHttpStatusCategory.SUCCESS
            else IdentityDiagnosticStatus.NOT_ATTEMPTED
        ),
        normalization_status=(
            IdentityDiagnosticStatus.SUCCEEDED
            if canonical else IdentityDiagnosticStatus.NOT_ATTEMPTED
        ),
        canonical_identity_status=(
            IdentityDiagnosticStatus.SUCCEEDED
            if canonical else IdentityDiagnosticStatus.NOT_ATTEMPTED
        ),
        blocking_stage=(
            IdentityDiagnosticStage.COMPLETE
            if canonical else IdentityDiagnosticStage.HTTP_RESPONSE
        ),
        safe_reason=(
            "Canonical Fiscal-anchored identity completed."
            if canonical else "Required identity evidence was unavailable."
        ),
        candidate_count=1 if canonical else 0,
        pages_examined=1,
        venue_evidence_found=canonical,
        candidate_resolution_status=(
            IdentityCandidateResolutionStatus.RESOLVED if canonical else candidate_status
        ),
        profile_enrichment_attempted=True,
    )


def unavailable_outcome(item: IdentityLiveDiagnostic):
    own_history = SimpleNamespace(
        result=None,
        identity_diagnostic=item,
        issues=(),
    )
    return SimpleNamespace(
        report=None,
        safe_notes=(),
        reverse_dcf_audit=None,
        market_audit=SimpleNamespace(
            safe_notes=(),
            publication_outcome=SimpleNamespace(
                safe_notes=(),
                own_history=own_history,
                peer_family=None,
            ),
        ),
    )


@pytest.mark.parametrize("market_family", ["generic_us", "generic_lse"])
def test_successful_generic_identity_is_application_available(market_family):
    item = diagnostic(IdentityHttpStatusCategory.SUCCESS, canonical=True)
    assert classify_provider_availability(
        item, canonical_identity_available=True,
    ) is ProviderAvailabilityStatus.AVAILABLE
    assert market_family in {"generic_us", "generic_lse"}


@pytest.mark.parametrize(
    "http,expected",
    [
        (IdentityHttpStatusCategory.ACCESS_DENIED, ProviderAvailabilityStatus.ACCESS_DENIED),
        (IdentityHttpStatusCategory.NOT_FOUND, ProviderAvailabilityStatus.NOT_FOUND),
        (
            IdentityHttpStatusCategory.AUTHENTICATION,
            ProviderAvailabilityStatus.AUTHENTICATION_FAILURE,
        ),
        (IdentityHttpStatusCategory.RATE_LIMIT, ProviderAvailabilityStatus.RATE_LIMITED),
        (IdentityHttpStatusCategory.SERVER, ProviderAvailabilityStatus.PROVIDER_FAILURE),
        (IdentityHttpStatusCategory.TRANSPORT, ProviderAvailabilityStatus.PROVIDER_FAILURE),
    ],
)
def test_controlled_provider_availability_categories_remain_distinct(http, expected):
    assert classify_provider_availability(
        diagnostic(http), canonical_identity_available=False,
    ) is expected


def test_explicit_identity_conflict_is_not_misreported_as_access_denied():
    item = diagnostic(
        IdentityHttpStatusCategory.SUCCESS,
        candidate_status=IdentityCandidateResolutionStatus.CONFLICT,
    )
    assert classify_provider_availability(
        item, canonical_identity_available=False,
    ) is ProviderAvailabilityStatus.EVIDENCE_CONFLICT


def test_access_denied_coordinator_result_has_no_identity_or_report_and_uses_safe_product_reason():
    item = diagnostic(IdentityHttpStatusCategory.ACCESS_DENIED)
    calls = []

    def runner(symbol, **kwargs):
        calls.append(symbol)
        return unavailable_outcome(item)

    result = V1ResearchCoordinator(runner=runner).build(
        "RR.L", environment={}, analysis_as_of=NOW,
    )

    assert calls == ["RR.L"]
    assert result.provider_availability is ProviderAvailabilityStatus.ACCESS_DENIED
    assert result.status is ResearchReportBuildStatus.UNAVAILABLE
    assert result.stage is ResearchReportPipelineStage.IDENTITY
    assert result.report is None
    assert result.supporting_ids == ()
    assert result.scenario_context is None
    assert result.blocking_reason == (
        "Required canonical identity evidence is unavailable under the current data provider access."
    )
    assert "invalid ticker" not in result.blocking_reason.lower()
    assert "identity mismatch" not in result.blocking_reason.lower()


def test_access_denied_never_constructs_a_yahoo_or_alternate_listing_identity():
    source = inspect.getsource(classify_provider_availability)
    result = V1ResearchCoordinator(
        runner=lambda *args, **kwargs: unavailable_outcome(
            diagnostic(IdentityHttpStatusCategory.ACCESS_DENIED)
        )
    ).build("RR.L", environment={}, analysis_as_of=NOW)
    assert result.report is None
    assert result.supporting_ids == ()
    assert "yahoo" not in source.lower()
    assert "listing" not in source.lower()


class RecordingRouteUI(RouteUI):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.button_labels = []

    def button(self, label, **kwargs):
        self.button_labels.append(label)
        return super().button(label, **kwargs)


def access_denied_result():
    return ResearchReportBuildResult(
        requested_symbol="RR.L",
        analysis_as_of=NOW,
        status=ResearchReportBuildStatus.UNAVAILABLE,
        report=None,
        stage=ResearchReportPipelineStage.IDENTITY,
        blocking_reason=(
            "Required canonical identity evidence is unavailable under the current data provider access."
        ),
        provider_availability=ProviderAvailabilityStatus.ACCESS_DENIED,
        identity_diagnostic=diagnostic(IdentityHttpStatusCategory.ACCESS_DENIED),
    )


def test_access_denied_route_replaces_stale_cross_ticker_report_and_scenario_state():
    failed = access_denied_result()
    stale_report = complete_report()
    session = {
        V1_REPORT_SESSION_KEY: stale_report,
        V1_SCENARIO_RESULT_SESSION_KEY: object(),
    }
    service = UIService(failed)
    ui = RecordingRouteUI(
        symbol="RR.L",
        buttons={"Analyse": [True]},
        session=session,
    )

    rendered = render_v1_report_route(
        coordinator=service,
        environment={},
        streamlit_module=ui,
        now=lambda: NOW,
    )

    text = "\n".join(ui.markdowns)
    assert rendered is failed
    assert len(service.build_calls) == 1
    assert service.retry_calls == []
    assert session[V1_BUILD_RESULT_SESSION_KEY] is failed
    assert session[V1_CURRENT_SYMBOL_SESSION_KEY] == "RR.L"
    assert V1_REPORT_SESSION_KEY not in session
    assert V1_SCENARIO_RESULT_SESSION_KEY not in session
    assert "Requested symbol: RR.L" in text
    assert "Stopped at IDENTITY" in text
    assert "current data provider access" in text
    assert "Retry" in ui.button_labels
    assert stale_report.target_security_id not in text
    assert "NASDAQ" not in text


def test_access_denied_route_does_not_retry_automatically_on_rerun():
    failed = access_denied_result()
    session = {
        V1_BUILD_RESULT_SESSION_KEY: failed,
        V1_CURRENT_SYMBOL_SESSION_KEY: "RR.L",
    }
    service = UIService(failed)
    ui = RecordingRouteUI(symbol="RR.L", session=session)
    render_v1_report_route(
        coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW,
    )
    assert service.build_calls == []
    assert service.retry_calls == []
    assert "Retry" in ui.button_labels


def test_one_explicit_retry_action_performs_one_bounded_attempt():
    failed = access_denied_result()
    session = {
        V1_BUILD_RESULT_SESSION_KEY: failed,
        V1_CURRENT_SYMBOL_SESSION_KEY: "RR.L",
    }
    service = UIService(failed)
    ui = RecordingRouteUI(
        symbol="RR.L", buttons={"Retry": [True]}, session=session,
    )
    render_v1_report_route(
        coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW,
    )
    assert service.build_calls == []
    assert len(service.retry_calls) == 1
    assert service.retry_calls[0][0] == "RR.L"


def test_access_denied_primary_view_contains_no_http_or_secret_request_detail():
    failed = access_denied_result()
    ui = RecordingRouteUI(
        symbol="RR.L",
        session={
            V1_BUILD_RESULT_SESSION_KEY: failed,
            V1_CURRENT_SYMBOL_SESSION_KEY: "RR.L",
        },
    )
    render_v1_report_route(
        coordinator=UIService(failed),
        environment={},
        streamlit_module=ui,
        now=lambda: NOW,
    )
    text = "\n".join(ui.markdowns).lower()
    for forbidden in (
        "http 403", "status=403", "api_key", "authorization", "bearer ",
        "http://", "https://", "companykey", "raw response", "traceback",
    ):
        assert forbidden not in text


def test_product_limitation_is_generic_and_not_a_permanent_security_blacklist():
    terms = TERMS.read_text(encoding="utf-8")
    assert (
        "Security coverage depends on the configured upstream data-provider entitlements. "
        "A security may therefore be unavailable even when the analyser supports its listing market generically."
    ) in terms
    assert "RR.L" not in terms
    assert "Rolls-Royce" not in terms


def test_production_has_no_rr_branch_blacklist_or_provider_bypass():
    production = (
        COORDINATOR_SOURCE.read_text(encoding="utf-8")
        + ROUTE_SOURCE.read_text(encoding="utf-8")
    ).lower()
    for forbidden in (
        '"rr.l"', "rolls-royce", "blacklist", "manual identity", "yahoo fallback",
    ):
        assert forbidden not in production


def test_default_v1_route_keeps_legacy_rollback_and_no_stance():
    assert is_v1_report_ui_enabled({}) is True
    app = APP_SOURCE.read_text(encoding="utf-8")
    assert "if is_v1_report_ui_enabled():" in app
    assert 'st.title("STOCK ANALYSER")' in app
    production = (COORDINATOR_SOURCE.read_text(encoding="utf-8") + ROUTE_SOURCE.read_text(encoding="utf-8")).lower()
    for forbidden in ('"buy"', '"hold"', '"sell"', "recommendation", "investment stance"):
        assert forbidden not in production


def test_normal_provider_availability_and_route_acceptance_make_zero_network_calls(monkeypatch):
    monkeypatch.setattr(
        socket, "create_connection", lambda *args, **kwargs: pytest.fail("network call"),
    )
    result = V1ResearchCoordinator(
        runner=lambda *args, **kwargs: unavailable_outcome(
            diagnostic(IdentityHttpStatusCategory.ACCESS_DENIED)
        )
    ).build("RR.L", environment={}, analysis_as_of=NOW)
    assert result.provider_availability is ProviderAvailabilityStatus.ACCESS_DENIED
