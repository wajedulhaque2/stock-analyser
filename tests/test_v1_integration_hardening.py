from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from stock_analyser.application import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
    ResearchBuildStageStatus,
    ResearchReportBuildStatus,
    ResearchReportCacheStatus,
    ResearchReportPipelineStage,
    V1ResearchCoordinator,
)
from stock_analyser.live_research_report_audit import run_live_research_report_audit
from stock_analyser.ui.v1_route import _render_build_diagnostics
from test_v1_research_integration import RouteUI
from test_v1_research_report import NOW, complete_report


@dataclass(frozen=True, slots=True)
class SafeEvidence:
    period_end: date
    amount: Decimal
    label: str = "normalized"


@dataclass(frozen=True, slots=True)
class UnsafePayloadEvidence:
    raw_payload: bytes


def test_normalized_registry_reuses_one_exact_semantic_request():
    context = ResearchBuildContext("syn", NOW)
    key = NormalizedEvidenceKey("fiscal", "identity", "symbol:SYN", NOW)
    calls = []
    factory = lambda: calls.append("called") or SafeEvidence(date(2025, 12, 31), Decimal("12.5"))
    first = context.get_or_create_normalized(key, factory)
    second = context.get_or_create_normalized(key, factory)
    assert first is second
    assert calls == ["called"]
    assert context.semantic_provider_call_count == 1
    assert context.reused_count == context.duplicate_requests_avoided == 1
    assert [item.reused for item in context.evidence_accesses] == [False, True]


@pytest.mark.parametrize("changed", ["provider", "capability", "target", "snapshot", "parameter"])
def test_registry_key_does_not_merge_distinct_semantics(changed):
    context = ResearchBuildContext("SYN", NOW)
    base = NormalizedEvidenceKey("fiscal", "actuals", "security:1", NOW, (("frequency", "annual"),))
    alternatives = {
        "provider": NormalizedEvidenceKey("fmp", "actuals", "security:1", NOW, (("frequency", "annual"),)),
        "capability": NormalizedEvidenceKey("fiscal", "estimates", "security:1", NOW, (("frequency", "annual"),)),
        "target": NormalizedEvidenceKey("fiscal", "actuals", "security:2", NOW, (("frequency", "annual"),)),
        "snapshot": NormalizedEvidenceKey("fiscal", "actuals", "security:1", NOW + timedelta(minutes=1), (("frequency", "annual"),)),
        "parameter": NormalizedEvidenceKey("fiscal", "actuals", "security:1", NOW, (("frequency", "quarterly"),)),
    }
    context.get_or_create_normalized(base, lambda: SafeEvidence(date(2025, 12, 31), Decimal("1")))
    if changed == "snapshot":
        with pytest.raises(ValueError, match="snapshot"):
            context.get_or_create_normalized(alternatives[changed], lambda: SafeEvidence(date.today(), Decimal("2")))
    else:
        context.get_or_create_normalized(alternatives[changed], lambda: SafeEvidence(date.today(), Decimal("2")))
        assert context.registry_size == 2


@pytest.mark.parametrize(
    "value,error",
    [
        ({"price": 1}, TypeError),
        (b"opaque", TypeError),
        (UnsafePayloadEvidence(b"opaque"), TypeError),
        (SafeEvidence(date.today(), Decimal("1"), "https://provider.invalid"), ValueError),
        (SafeEvidence(date.today(), Decimal("1"), "api_key=hidden"), ValueError),
    ],
)
def test_registry_rejects_raw_or_secret_bearing_values(value, error):
    context = ResearchBuildContext("SYN", NOW)
    key = NormalizedEvidenceKey("fiscal", "actuals", "security:1", NOW)
    with pytest.raises(error):
        context.register_normalized(key, value)
    assert context.registry_size == 0


def test_registry_rejects_opaque_provider_lookup_keys_in_metadata():
    with pytest.raises(ValueError):
        NormalizedEvidenceKey("fiscal", "actuals", "companyKey:opaque", NOW)


def test_stage_trace_is_ordered_timed_and_counts_only_reuse_within_stage():
    ticks = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2), NOW + timedelta(seconds=5)))
    context = ResearchBuildContext("SYN", NOW, clock=lambda: next(ticks))
    key = NormalizedEvidenceKey("fiscal", "identity", "symbol:SYN", NOW)
    context.register_normalized(key, SafeEvidence(date.today(), Decimal("1")))
    first = context.start_stage(ResearchReportPipelineStage.IDENTITY)
    context.get_or_create_normalized(key, lambda: None)
    event = context.finish_stage(first, issues_count=1, warnings_count=2)
    second = context.start_stage(ResearchReportPipelineStage.COMPLETE)
    context.finish_stage(second)
    assert event.duration_seconds == 1
    assert event.normalized_reuse_count == 1
    assert event.issues_count == 1 and event.warnings_count == 2
    assert [item.stage for item in context.stage_events] == [
        ResearchReportPipelineStage.IDENTITY, ResearchReportPipelineStage.COMPLETE,
    ]


class BlockingRunner:
    def __init__(self, *, failure: Exception | None = None):
        self.started = Event()
        self.release = Event()
        self.lock = Lock()
        self.calls = 0
        self.failure = failure

    def __call__(self, symbol, *, environment, analysis_as_of, build_context):
        with self.lock:
            self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(report=complete_report(), safe_notes=())


def _run_two_identical_builds(runner):
    service = V1ResearchCoordinator(runner=runner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.build, "SYN", environment={}, analysis_as_of=NOW)
        assert runner.started.wait(2)
        second = pool.submit(service.build, "syn", environment={}, analysis_as_of=NOW)
        runner.release.set()
        return service, first.result(timeout=5), second.result(timeout=5)


def test_single_flight_concurrent_identical_builds_execute_runner_once():
    runner = BlockingRunner()
    _, first, second = _run_two_identical_builds(runner)
    assert runner.calls == 1
    assert {first.cache_status, second.cache_status} == {
        ResearchReportCacheStatus.MISS, ResearchReportCacheStatus.SINGLE_FLIGHT,
    }
    assert first.report is second.report
    assert first.analysis_as_of == second.analysis_as_of == NOW


def test_single_flight_exception_releases_all_waiters_without_leaking_detail():
    runner = BlockingRunner(failure=RuntimeError("api_key=do-not-display"))
    service, first, second = _run_two_identical_builds(runner)
    assert runner.calls == 1
    assert first.status is second.status is ResearchReportBuildStatus.FAILED
    assert "do-not-display" not in repr((first, second))
    runner.failure = None
    runner.started.clear()
    runner.release.set()
    recovered = service.retry("SYN", environment={}, analysis_as_of=NOW)
    assert recovered.report is not None
    assert runner.calls == 2


def test_context_factory_exception_cannot_deadlock_single_flight():
    runner = BlockingRunner()
    service = V1ResearchCoordinator(
        runner=runner,
        context_factory=lambda *_: (_ for _ in ()).throw(RuntimeError("context failed")),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.build("SYN", environment={}, analysis_as_of=NOW), range(2)))
    assert all(item.status is ResearchReportBuildStatus.FAILED for item in results)
    assert runner.calls == 0


def test_coordinator_exposes_safe_reuse_metrics_without_raw_fields():
    def runner(symbol, *, environment, analysis_as_of, build_context):
        key = NormalizedEvidenceKey("fiscal", "identity", "symbol:SYN", analysis_as_of)
        build_context.register_normalized(key, SafeEvidence(date.today(), Decimal("1")))
        build_context.get_or_create_normalized(key, lambda: None)
        return SimpleNamespace(report=complete_report(), safe_notes=())

    result = V1ResearchCoordinator(runner=runner).build("SYN", environment={}, analysis_as_of=NOW)
    assert result.semantic_provider_call_count == 1
    assert result.normalized_reuse_count == result.duplicate_requests_avoided == 1
    assert result.cancellation_supported is False
    assert {item.name for item in fields(result)}.isdisjoint({"payload", "headers", "url", "api_key"})


def test_synthetic_performance_audit_collapses_five_requests_to_three_unique_semantics():
    context = ResearchBuildContext("SYN", NOW)
    calls = []
    keys = [
        NormalizedEvidenceKey("fiscal", "identity", "symbol:SYN", NOW),
        NormalizedEvidenceKey("fiscal", "actuals", "security:1", NOW),
        NormalizedEvidenceKey("fmp", "annual_estimates", "security:1", NOW),
    ]
    for key in (*keys, keys[1], keys[2]):
        context.get_or_create_normalized(
            key, lambda key=key: calls.append(key.capability) or SafeEvidence(date.today(), Decimal("1")),
        )
    assert len(calls) == context.semantic_provider_call_count == 3
    assert context.duplicate_requests_avoided == 2


def test_safe_diagnostics_render_stage_timing_but_not_request_counts_or_provider_details():
    context = ResearchBuildContext("SYN", NOW)
    token = context.start_stage(ResearchReportPipelineStage.IDENTITY)
    context.finish_stage(token, status=ResearchBuildStageStatus.FAILED, safe_blocker="Canonical identity unavailable.")
    result = V1ResearchCoordinator(
        runner=lambda *a, **k: SimpleNamespace(report=None, market_audit=None, safe_notes=())
    ).build("SYN", environment={}, analysis_as_of=NOW)
    result = result.__class__(**{**{field.name: getattr(result, field.name) for field in fields(result)}, "stage_events": context.stage_events})
    ui = RouteUI()
    _render_build_diagnostics(ui, result)
    rendered = "\n".join(ui.markdowns).lower()
    assert "identity" in rendered and "failed" in rendered and "cancellation" in rendered
    assert all(term not in rendered for term in ("request count", "api_key", "http://", "https://"))


def test_live_report_wrapper_passes_one_identity_forward_and_skips_reverse_on_identity_failure(monkeypatch):
    market = SimpleNamespace(
        publication_outcome=SimpleNamespace(own_history=None, peer_family=None),
    )
    calls = []
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_market_comparison_audit",
        lambda *a, **k: market,
    )
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_reverse_dcf_audit",
        lambda *a, **k: calls.append((a, k)),
    )
    outcome = run_live_research_report_audit("SYN", environment={}, analysis_as_of=NOW)
    assert outcome.report is None and outcome.reverse_dcf_audit is None
    assert calls == []


def test_hardening_sources_add_no_ticker_specific_or_economic_policy_branches():
    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "src/stock_analyser/application/research_build_context.py",
            "src/stock_analyser/application/v1_research.py",
        )
    ).lower()
    string_literals = {
        node.value.lower()
        for node in __import__("ast").walk(__import__("ast").parse(sources))
        if isinstance(node, __import__("ast").Constant) and isinstance(node.value, str)
    }
    assert string_literals.isdisjoint({"meta", "msft", "goog", "googl", "vod.l"})
    assert all(name not in sources for name in (
        "calculate_own_history_valuation", "orchestrate_peer_family",
        "execute_reverse_dcf", "publish_cross_family_valuation",
    ))
