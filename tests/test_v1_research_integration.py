from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import fields
from datetime import timedelta
import ast
import inspect
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.application import (
    REPORT_INTEGRATION_POLICY_ID,
    ReportCachePolicy,
    ResearchReportBuildStatus,
    ResearchReportCache,
    ResearchReportCacheStatus,
    ResearchReportPipelineStage,
    V1ResearchCoordinator,
)
from stock_analyser.ui import v1_route
from stock_analyser.ui.v1_route import (
    V1_ANALYSIS_AS_OF_SESSION_KEY,
    V1_BUILD_RESULT_SESSION_KEY,
    V1_CURRENT_SYMBOL_SESSION_KEY,
    V1_TICKER_INPUT_SESSION_KEY,
    render_v1_report_route,
)
from stock_analyser.ui.v1_report import V1_REPORT_SESSION_KEY
from test_v1_research_report import NOW, complete_report, partial_report


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_SOURCE = ROOT / "src" / "stock_analyser" / "application" / "v1_research.py"
ROUTE_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_route.py"
RENDERER_SOURCE = ROOT / "src" / "stock_analyser" / "ui" / "v1_report.py"
APP_SOURCE = ROOT / "app.py"


def outcome(report, *notes):
    return SimpleNamespace(report=report, safe_notes=notes)


class CountingRunner:
    def __init__(self, reports):
        self.reports = list(reports)
        self.calls = []

    def __call__(self, symbol, *, environment, analysis_as_of):
        self.calls.append((symbol, environment, analysis_as_of))
        value = self.reports[min(len(self.calls) - 1, len(self.reports) - 1)]
        if isinstance(value, Exception):
            raise value
        return outcome(value)


def coordinator(reports, *, version="report-policy"):
    runner = CountingRunner(reports)
    cache = ResearchReportCache(ReportCachePolicy(ttl_seconds=300, snapshot_bucket_seconds=300, max_entries=3))
    return V1ResearchCoordinator(runner=runner, cache=cache, report_version=version), runner


def test_coordinator_accepts_ticker_and_reuses_existing_bounded_live_boundary_once():
    source = complete_report()
    service, runner = coordinator([source])
    result = service.build(" syn ", environment={"SAFE": "value"}, analysis_as_of=NOW)
    assert result.status is ResearchReportBuildStatus.READY
    assert result.stage is ResearchReportPipelineStage.COMPLETE
    assert result.report is source
    assert runner.calls == [("SYN", {"SAFE": "value"}, NOW)]


def test_empty_ticker_rejected_safely_without_live_call():
    service, runner = coordinator([complete_report()])
    result = service.build("  ", environment={}, analysis_as_of=NOW)
    assert result.status is ResearchReportBuildStatus.FAILED
    assert result.stage is ResearchReportPipelineStage.INPUT
    assert result.report is None
    assert runner.calls == []


@pytest.mark.parametrize("symbol,expected", [("goog", "GOOG"), ("googl", "GOOGL"), ("vod.l", "VOD.L")])
def test_input_hygiene_does_not_apply_ticker_specific_rewrites(symbol, expected):
    service, runner = coordinator([complete_report()])
    service.build(symbol, environment={}, analysis_as_of=NOW)
    assert runner.calls[0][0] == expected


def test_one_aware_analysis_snapshot_is_propagated_unchanged():
    service, runner = coordinator([complete_report()])
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    assert NOW.tzinfo is not None
    assert runner.calls[0][2] is NOW
    assert result.analysis_as_of is NOW
    assert result.report.analysis_as_of is NOW


def test_naive_analysis_snapshot_fails_before_runner():
    service, runner = coordinator([complete_report()])
    with pytest.raises(ValueError, match="timezone-aware"):
        service.build("SYN", environment={}, analysis_as_of=NOW.replace(tzinfo=None))
    assert runner.calls == []


def test_mixed_analysis_snapshot_from_runner_fails_closed_at_report_build():
    service, _ = coordinator([complete_report()])
    result = service.build("SYN", environment={}, analysis_as_of=NOW + timedelta(minutes=10))
    assert result.status is ResearchReportBuildStatus.FAILED
    assert result.stage is ResearchReportPipelineStage.REPORT_BUILD
    assert result.report is None


def test_partial_peer_reverse_dcf_market_and_reference_gaps_still_permit_report():
    source = partial_report()
    service, _ = coordinator([source])
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    assert result.status is ResearchReportBuildStatus.PARTIAL
    assert result.report is source
    assert result.report.market.normalized_market_price is None
    assert result.report.valuation_families[1].central_value is None
    assert result.report.expectations.entries == ()
    assert result.report.references == ()


def test_fatal_identity_unavailable_preserves_earliest_stage():
    service = V1ResearchCoordinator(
        runner=lambda *args, **kwargs: SimpleNamespace(
            report=None,
            safe_notes=("Canonical identity was unavailable",),
            market_audit=None,
        )
    )
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    assert result.status is ResearchReportBuildStatus.UNAVAILABLE
    assert result.stage is ResearchReportPipelineStage.IDENTITY
    assert result.blocking_reason == "Canonical identity was unavailable."


def test_identity_failure_collects_only_existing_safe_nested_audit_issues():
    nested = SimpleNamespace(
        report=None,
        safe_notes=("report note",),
        reverse_dcf_audit=SimpleNamespace(safe_issues=("reverse note",)),
        market_audit=SimpleNamespace(
            safe_notes=("market note",),
            publication_outcome=SimpleNamespace(
                safe_notes=("publication note",),
                own_history=SimpleNamespace(result=None, issues=(SimpleNamespace(reason="identity unavailable"),)),
                peer_family=None,
            ),
        ),
    )
    service = V1ResearchCoordinator(runner=lambda *args, **kwargs: nested)
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    assert result.issues == (
        "report note", "market note", "publication note", "reverse note", "identity unavailable",
    )


def test_report_builder_exception_is_safe_fatal_and_does_not_leak_exception():
    secret = "api_key=never-display"
    service, _ = coordinator([ValueError(secret)])
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    rendered = repr(result).lower()
    assert result.stage is ResearchReportPipelineStage.REPORT_BUILD
    assert result.status is ResearchReportBuildStatus.FAILED
    assert secret not in rendered
    assert "safe technical details were withheld" in rendered


def test_result_retains_safe_issues_warnings_ids_and_provenance_only():
    source = partial_report()
    service = V1ResearchCoordinator(runner=lambda *a, **k: outcome(source, "Safe bounded note"))
    result = service.build("SYN", environment={}, analysis_as_of=NOW)
    assert "Safe bounded note" in result.issues
    assert source.report_id in result.supporting_ids
    assert result.provenance == source.provenance
    assert REPORT_INTEGRATION_POLICY_ID in result.policy_ids
    names = {field.name for field in fields(result)}
    assert names.isdisjoint({"payload", "response", "headers", "companyKey", "api_key", "url"})


def test_cache_prevents_same_ticker_bucket_rebuild_and_marks_hit():
    service, runner = coordinator([complete_report()])
    first = service.build("SYN", environment={}, analysis_as_of=NOW)
    second = service.build("syn", environment={}, analysis_as_of=NOW + timedelta(seconds=30))
    assert first.cache_status is ResearchReportCacheStatus.MISS
    assert second.cache_status is ResearchReportCacheStatus.HIT
    assert second.report is first.report
    assert len(runner.calls) == 1


def test_cache_isolated_by_ticker_and_report_policy_version():
    shared = ResearchReportCache()
    first_runner = CountingRunner([complete_report(), complete_report()])
    first = V1ResearchCoordinator(runner=first_runner, cache=shared, report_version="v1")
    first.build("AAA", environment={}, analysis_as_of=NOW)
    first.build("BBB", environment={}, analysis_as_of=NOW)
    second_runner = CountingRunner([complete_report()])
    second = V1ResearchCoordinator(runner=second_runner, cache=shared, report_version="v2")
    second.build("AAA", environment={}, analysis_as_of=NOW)
    assert [call[0] for call in first_runner.calls] == ["AAA", "BBB"]
    assert [call[0] for call in second_runner.calls] == ["AAA"]


def test_cache_is_bounded_and_expired_results_are_not_fresh():
    ticks = iter((0.0, 3.0))
    cache = ResearchReportCache(
        ReportCachePolicy(ttl_seconds=2, snapshot_bucket_seconds=300, max_entries=1),
        clock=lambda: next(ticks),
    )
    service = V1ResearchCoordinator(runner=CountingRunner([complete_report()]), cache=cache)
    first = service.build("SYN", environment={}, analysis_as_of=NOW)
    key = cache.key_for("SYN", NOW, service.report_version)
    assert first.report is not None and cache.size == 1
    assert cache.get(key) is None
    assert cache.size == 0


def test_retry_invalidates_current_symbol_and_performs_exactly_one_attempt():
    service, runner = coordinator([complete_report(), partial_report()])
    service.build("SYN", environment={}, analysis_as_of=NOW)
    result = service.retry("SYN", environment={}, analysis_as_of=NOW)
    assert len(runner.calls) == 2
    assert result.report.status is partial_report().status
    assert result.cache_status is ResearchReportCacheStatus.REFRESHED


def test_coordinator_contains_no_valuation_or_gap_arithmetic_and_reuses_live_chain():
    tree = ast.parse(COORDINATOR_SOURCE.read_text(encoding="utf-8"))
    guarded = {
        "overall_central_value", "lower_value", "central_value", "upper_value",
        "lower_gap", "central_gap", "upper_gap", "implied_terminal_growth",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            attributes = {item.attr for item in ast.walk(node) if isinstance(item, ast.Attribute)}
            assert attributes.isdisjoint(guarded)
    source = COORDINATOR_SOURCE.read_text(encoding="utf-8")
    assert "run_live_research_report_audit" in source
    assert all(name not in source for name in (
        "calculate_own_history_valuation", "orchestrate_peer_family", "execute_reverse_dcf",
        "publish_cross_family_valuation", "compare_publication_to_market", "build_stock_research_report(",
    ))


class Scope(AbstractContextManager):
    def __init__(self, ui):
        self.ui = ui

    def __enter__(self):
        return self.ui

    def __exit__(self, *args):
        return False


class RouteUI:
    def __init__(self, *, symbol="", buttons=None, session=None):
        self.symbol = symbol
        self.buttons = {key: list(value) for key, value in (buttons or {}).items()}
        self.session_state = {} if session is None else session
        self.markdowns = []
        self.spinners = []
        self.captions = []
        self.expanders = []

    def markdown(self, body, **kwargs):
        self.markdowns.append(str(body))

    def columns(self, spec):
        return Scope(self), Scope(self)

    def text_input(self, label, **kwargs):
        self.session_state[V1_TICKER_INPUT_SESSION_KEY] = self.symbol
        return self.symbol

    def button(self, label, **kwargs):
        values = self.buttons.get(label, [])
        return values.pop(0) if values else False

    def spinner(self, label):
        self.spinners.append(label)
        return Scope(self)

    def expander(self, label, **kwargs):
        self.expanders.append(label)
        return Scope(self)

    def caption(self, value):
        self.captions.append(str(value))


class UIService:
    def __init__(self, result):
        self.result = result
        self.build_calls = []
        self.retry_calls = []

    def build(self, symbol, **kwargs):
        self.build_calls.append((symbol, kwargs))
        return self.result

    def retry(self, symbol, **kwargs):
        self.retry_calls.append((symbol, kwargs))
        return self.result


def build_result(source=None, *, status=ResearchReportBuildStatus.PARTIAL, stage=ResearchReportPipelineStage.COMPLETE):
    source = partial_report() if source is None and stage is ResearchReportPipelineStage.COMPLETE else source
    return SimpleNamespace(
        requested_symbol="SYN",
        analysis_as_of=NOW,
        status=status,
        report=source,
        stage=stage,
        blocking_reason=None if source else "Canonical identity was unavailable.",
        issues=("safe issue",) if source is None else (),
        warnings=(),
    )


def test_empty_route_has_input_and_empty_state_without_default_or_build(monkeypatch):
    ui = RouteUI()
    service = UIService(build_result())
    monkeypatch.setattr(v1_route, "render_v1_report_empty_state", lambda **kwargs: ui.markdown("EMPTY STATE"))
    result = render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert result is None
    assert service.build_calls == service.retry_calls == []
    assert "EMPTY STATE" in ui.markdowns
    assert "META" not in "\n".join(ui.markdowns)


def test_one_submit_creates_one_snapshot_build_and_passes_only_report_to_renderer(monkeypatch):
    source = partial_report()
    expected = build_result(source)
    service = UIService(expected)
    ui = RouteUI(symbol="syn", buttons={"Analyse": [True]})
    rendered = []
    monkeypatch.setattr(v1_route, "render_stock_research_report", lambda report, **kwargs: rendered.append(report))
    result = render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert result is expected
    assert len(service.build_calls) == 1 and service.retry_calls == []
    assert service.build_calls[0][0] == "syn"
    assert service.build_calls[0][1]["analysis_as_of"] is NOW
    assert rendered == [source]
    assert ui.spinners == ["Building research report..."]


def test_normal_streamlit_rerun_uses_session_result_without_rebuild(monkeypatch):
    expected = build_result()
    session = {V1_BUILD_RESULT_SESSION_KEY: expected}
    service = UIService(expected)
    ui = RouteUI(symbol="SYN", session=session)
    monkeypatch.setattr(v1_route, "render_stock_research_report", lambda *a, **k: None)
    render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert service.build_calls == service.retry_calls == []


def test_new_ticker_submit_builds_new_report_once(monkeypatch):
    expected = build_result()
    session = {V1_BUILD_RESULT_SESSION_KEY: expected}
    service = UIService(expected)
    ui = RouteUI(symbol="NEW", buttons={"Analyse": [True]}, session=session)
    monkeypatch.setattr(v1_route, "render_stock_research_report", lambda *a, **k: None)
    render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert [call[0] for call in service.build_calls] == ["NEW"]


def test_fatal_failure_state_and_one_explicit_retry_are_visible(monkeypatch):
    failed = build_result(None, status=ResearchReportBuildStatus.UNAVAILABLE, stage=ResearchReportPipelineStage.IDENTITY)
    service = UIService(failed)
    session = {
        V1_BUILD_RESULT_SESSION_KEY: failed,
        V1_CURRENT_SYMBOL_SESSION_KEY: "SYN",
    }
    ui = RouteUI(symbol="SYN", buttons={"Retry": [True]}, session=session)
    render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert len(service.retry_calls) == 1 and service.build_calls == []
    assert "Building research report..." in ui.spinners
    assert "Stopped at IDENTITY" in "\n".join(ui.markdowns)
    assert "Safe technical details" in ui.expanders


def test_session_state_contains_only_safe_integration_objects(monkeypatch):
    source = partial_report()
    expected = build_result(source)
    service = UIService(expected)
    ui = RouteUI(symbol="SYN", buttons={"Analyse": [True]})
    monkeypatch.setattr(v1_route, "render_stock_research_report", lambda *a, **k: None)
    render_v1_report_route(coordinator=service, environment={}, streamlit_module=ui, now=lambda: NOW)
    assert ui.session_state[V1_REPORT_SESSION_KEY] is source
    assert ui.session_state[V1_BUILD_RESULT_SESSION_KEY] is expected
    assert ui.session_state[V1_CURRENT_SYMBOL_SESSION_KEY] == "SYN"
    assert ui.session_state[V1_ANALYSIS_AS_OF_SESSION_KEY] is NOW
    assert all(token not in str(ui.session_state).lower() for token in ("api_key", "authorization", "companykey"))


def test_ui_route_owns_no_financial_policy_or_provider_field_resolution():
    source = ROUTE_SOURCE.read_text(encoding="utf-8").lower()
    forbidden = (
        "stock_analyser.providers", "calculate_own_history", "orchestrate_peer",
        "publish_cross_family", "compare_publication", "build_stock_research_report",
        "implied_terminal_growth", "companykey", "api_key", '"buy"', '"hold"', '"sell"',
        "number_input", "slider(", "selectbox", "multiselect",
    )
    assert all(token not in source for token in forbidden)


def test_renderer_remains_provider_and_integration_free():
    source = RENDERER_SOURCE.read_text(encoding="utf-8").lower()
    assert "stock_analyser.providers" not in source
    assert "stock_analyser.application" not in source
    assert "v1researchcoordinator" not in source


def test_app_defaults_to_v1_and_stops_before_legacy_route():
    source = APP_SOURCE.read_text(encoding="utf-8")
    route_start = source.index("if is_v1_report_ui_enabled():")
    legacy_start = source.index('st.title("STOCK ANALYSER")')
    route = source[route_start:legacy_start]
    assert "render_v1_report_route()" in route
    assert "st.stop()" in route
    assert "load_ticker(ticker)" in source


def test_normal_integration_tests_make_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    service, runner = coordinator([partial_report()])
    assert service.build("SYN", environment={}, analysis_as_of=NOW).report is not None
    assert len(runner.calls) == 1


def test_new_integration_adds_no_provider_and_no_ticker_specific_production_branch():
    source = "\n".join((
        COORDINATOR_SOURCE.read_text(encoding="utf-8").lower(),
        ROUTE_SOURCE.read_text(encoding="utf-8").lower(),
    ))
    assert "== \"meta\"" not in source
    assert "== \"goog\"" not in source
    assert "googl" not in source
    assert "endswith(\".l\")" not in source
    assert "new provider" not in source
