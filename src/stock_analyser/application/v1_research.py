"""Bounded ticker-to-report coordination for the feature-flagged V1 route.

This layer delegates the financial work to the already-approved live audit and
service chain.  It owns only execution state, safe failure translation, one
analysis snapshot, and a short-lived report cache.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import inspect
import math
from pathlib import Path
from threading import Event, RLock
import time
from typing import Any

from stock_analyser.domain import Provenance, ReportStatus, StockResearchReport
from stock_analyser.live_identity import (
    IdentityCandidateResolutionStatus,
    IdentityDiagnosticStatus,
    IdentityHttpStatusCategory,
    IdentityLiveDiagnostic,
)
from stock_analyser.services.research_report import REPORT_POLICY_ID
from .research_build_context import (
    NormalizedEvidenceAccess,
    ResearchBuildContext,
    ResearchBuildStageEvent,
    ResearchBuildStageStatus,
)
from .v1_scenario import ReverseDcfScenarioExecutionContext


REPORT_INTEGRATION_POLICY_ID = "v1-12b-research-report-integration"


class ResearchReportBuildStatus(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ResearchReportPipelineStage(str, Enum):
    INPUT = "input"
    IDENTITY = "identity"
    MARKET_DATA = "market_data"
    FUNDAMENTALS = "fundamentals"
    FORWARD_CONSENSUS = "forward_consensus"
    OWN_HISTORY = "own_history"
    PEER = "peer"
    REVERSE_DCF = "reverse_dcf"
    PUBLICATION = "publication"
    MARKET_COMPARISON = "market_comparison"
    REPORT_BUILD = "report_build"
    COMPLETE = "complete"


class ResearchReportCacheStatus(str, Enum):
    NOT_USED = "not_used"
    MISS = "miss"
    HIT = "hit"
    REFRESHED = "refreshed"
    SINGLE_FLIGHT = "single_flight"


class ProviderAvailabilityStatus(str, Enum):
    """Controlled product-facing provider availability, never raw HTTP detail."""

    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMITED = "rate_limited"
    PROVIDER_FAILURE = "provider_failure"
    EVIDENCE_CONFLICT = "evidence_conflict"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ReportCachePolicy:
    ttl_seconds: int = 300
    snapshot_bucket_seconds: int = 300
    max_entries: int = 8
    policy_id: str = "v1-12b-ui-report-cache-5m"

    def __post_init__(self) -> None:
        for name in ("ttl_seconds", "snapshot_bucket_seconds", "max_entries"):
            value = getattr(self, name)
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")


DEFAULT_REPORT_CACHE_POLICY = ReportCachePolicy()


@dataclass(frozen=True, slots=True)
class SingleFlightPolicy:
    wait_timeout_seconds: float = 180.0
    policy_id: str = "v1-12c-process-local-single-flight"

    def __post_init__(self) -> None:
        if isinstance(self.wait_timeout_seconds, bool) or self.wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        if not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")


DEFAULT_SINGLE_FLIGHT_POLICY = SingleFlightPolicy()


@dataclass(frozen=True, slots=True)
class ResearchReportBuildResult:
    requested_symbol: str
    analysis_as_of: datetime
    status: ResearchReportBuildStatus
    report: StockResearchReport | None
    stage: ResearchReportPipelineStage
    blocking_reason: str | None = None
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    cache_status: ResearchReportCacheStatus = ResearchReportCacheStatus.NOT_USED
    supporting_ids: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    policy_ids: tuple[str, ...] = (REPORT_INTEGRATION_POLICY_ID, REPORT_POLICY_ID)
    stage_events: tuple[ResearchBuildStageEvent, ...] = ()
    normalized_evidence_accesses: tuple[NormalizedEvidenceAccess, ...] = ()
    semantic_provider_call_count: int = 0
    normalized_reuse_count: int = 0
    duplicate_requests_avoided: int = 0
    cancellation_supported: bool = False
    identity_diagnostic: IdentityLiveDiagnostic | None = None
    provider_availability: ProviderAvailabilityStatus = ProviderAvailabilityStatus.UNAVAILABLE
    scenario_context: ReverseDcfScenarioExecutionContext | None = None

    def __post_init__(self) -> None:
        if not self.requested_symbol.strip() and self.stage is not ResearchReportPipelineStage.INPUT:
            raise ValueError("requested_symbol must be non-empty")
        if self.analysis_as_of.tzinfo is None or self.analysis_as_of.utcoffset() is None:
            raise ValueError("analysis_as_of must be timezone-aware")
        if self.report is None and self.status in {
            ResearchReportBuildStatus.READY,
            ResearchReportBuildStatus.PARTIAL,
        }:
            raise ValueError("ready or partial integration result requires a report")
        if self.report is not None:
            if self.status not in {ResearchReportBuildStatus.READY, ResearchReportBuildStatus.PARTIAL}:
                raise ValueError("a report is valid only for ready or partial status")
            if self.stage is not ResearchReportPipelineStage.COMPLETE:
                raise ValueError("a coherent report must complete the integration pipeline")
            if self.report.analysis_as_of != self.analysis_as_of:
                raise ValueError("report must use the integration analysis snapshot")
        if self.blocking_reason is not None and not self.blocking_reason.strip():
            raise ValueError("blocking_reason must be non-empty when supplied")
        object.__setattr__(self, "issues", _safe_texts(self.issues))
        object.__setattr__(self, "warnings", _safe_texts(self.warnings))
        object.__setattr__(self, "supporting_ids", _safe_texts(self.supporting_ids))
        object.__setattr__(self, "provenance", tuple(dict.fromkeys(self.provenance)))
        object.__setattr__(self, "policy_ids", _safe_texts(self.policy_ids))
        if any(not isinstance(item, ResearchBuildStageEvent) for item in self.stage_events):
            raise TypeError("stage_events must contain ResearchBuildStageEvent")
        if any(not isinstance(item, NormalizedEvidenceAccess) for item in self.normalized_evidence_accesses):
            raise TypeError("normalized_evidence_accesses must contain NormalizedEvidenceAccess")
        object.__setattr__(self, "stage_events", tuple(self.stage_events))
        object.__setattr__(self, "normalized_evidence_accesses", tuple(self.normalized_evidence_accesses))
        for name in ("semantic_provider_call_count", "normalized_reuse_count", "duplicate_requests_avoided"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.cancellation_supported:
            raise ValueError("Milestone 12C does not implement provider-call cancellation")
        if self.identity_diagnostic is not None and not isinstance(
            self.identity_diagnostic, IdentityLiveDiagnostic,
        ):
            raise TypeError("identity_diagnostic must be IdentityLiveDiagnostic")
        if not isinstance(self.provider_availability, ProviderAvailabilityStatus):
            raise TypeError("provider_availability must be ProviderAvailabilityStatus")
        if self.scenario_context is not None:
            if not isinstance(self.scenario_context, ReverseDcfScenarioExecutionContext):
                raise TypeError("scenario_context must be ReverseDcfScenarioExecutionContext")
            if self.report is None:
                raise ValueError("scenario_context requires a coherent report")
            if self.scenario_context.identity_key != (
                self.report.target_security_id,
                self.report.target_issuer_id,
                self.report.analysis_as_of.isoformat(),
            ):
                raise ValueError("scenario_context must match report identity and snapshot")


@dataclass(frozen=True, slots=True)
class _CacheKey:
    symbol: str
    snapshot_bucket: int
    report_version: str


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    stored_at: float
    result: ResearchReportBuildResult


@dataclass(slots=True)
class _InFlightBuild:
    completed: Event
    result: ResearchReportBuildResult | None = None


def _safe_texts(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _safe_outcome_issues(outcome: Any) -> tuple[str, ...]:
    """Collect only already-sanitized audit notes and DataIssue reasons."""
    market = getattr(outcome, "market_audit", None)
    publication = getattr(market, "publication_outcome", None)
    own_history = getattr(publication, "own_history", None)
    peer_family = getattr(publication, "peer_family", None)
    reverse_dcf = getattr(outcome, "reverse_dcf_audit", None)
    values = [
        *getattr(outcome, "safe_notes", ()),
        *getattr(market, "safe_notes", ()),
        *getattr(publication, "safe_notes", ()),
        *getattr(reverse_dcf, "safe_issues", ()),
    ]
    for audit in (own_history, peer_family):
        values.extend(
            getattr(item, "reason", str(item))
            for item in getattr(audit, "issues", ())
        )
    return _safe_texts(values)


def _identity_diagnostic(outcome: Any) -> IdentityLiveDiagnostic | None:
    market = getattr(outcome, "market_audit", None)
    publication = getattr(market, "publication_outcome", None)
    own_history = getattr(publication, "own_history", None)
    diagnostic = getattr(own_history, "identity_diagnostic", None)
    return diagnostic if isinstance(diagnostic, IdentityLiveDiagnostic) else None


def classify_provider_availability(
    diagnostic: IdentityLiveDiagnostic | None,
    *,
    canonical_identity_available: bool,
) -> ProviderAvailabilityStatus:
    """Translate controlled identity evidence into one release/product classification."""
    if canonical_identity_available:
        return ProviderAvailabilityStatus.AVAILABLE
    if diagnostic is None:
        return ProviderAvailabilityStatus.UNAVAILABLE
    http = diagnostic.http_status_category
    if http is IdentityHttpStatusCategory.ACCESS_DENIED:
        return ProviderAvailabilityStatus.ACCESS_DENIED
    if http is IdentityHttpStatusCategory.AUTHENTICATION:
        return ProviderAvailabilityStatus.AUTHENTICATION_FAILURE
    if http is IdentityHttpStatusCategory.NOT_FOUND:
        return ProviderAvailabilityStatus.NOT_FOUND
    if http is IdentityHttpStatusCategory.RATE_LIMIT:
        return ProviderAvailabilityStatus.RATE_LIMITED
    if http in {
        IdentityHttpStatusCategory.SERVER,
        IdentityHttpStatusCategory.TRANSPORT,
        IdentityHttpStatusCategory.OTHER_FAILURE,
    }:
        return ProviderAvailabilityStatus.PROVIDER_FAILURE
    if (
        diagnostic.candidate_resolution_status is IdentityCandidateResolutionStatus.CONFLICT
        or diagnostic.canonical_identity_status is IdentityDiagnosticStatus.FAILED
    ):
        return ProviderAvailabilityStatus.EVIDENCE_CONFLICT
    if (
        diagnostic.candidate_resolution_status is IdentityCandidateResolutionStatus.NOT_FOUND
    ):
        return ProviderAvailabilityStatus.NOT_FOUND
    if (
        diagnostic.parse_status is IdentityDiagnosticStatus.FAILED
        or http is not IdentityHttpStatusCategory.SUCCESS
    ):
        return ProviderAvailabilityStatus.PROVIDER_FAILURE
    return ProviderAvailabilityStatus.UNAVAILABLE


def _identity_unavailable_reason(status: ProviderAvailabilityStatus) -> str:
    return {
        ProviderAvailabilityStatus.ACCESS_DENIED: (
            "Required canonical identity evidence is unavailable under the current data provider access."
        ),
        ProviderAvailabilityStatus.AUTHENTICATION_FAILURE: (
            "Required canonical identity evidence is unavailable because data provider authentication failed."
        ),
        ProviderAvailabilityStatus.NOT_FOUND: (
            "Required canonical identity evidence was not found by the configured data provider."
        ),
        ProviderAvailabilityStatus.RATE_LIMITED: (
            "Required canonical identity evidence is temporarily unavailable because the data provider rate limit was reached."
        ),
        ProviderAvailabilityStatus.PROVIDER_FAILURE: (
            "Required canonical identity evidence is unavailable because the data provider request failed."
        ),
        ProviderAvailabilityStatus.EVIDENCE_CONFLICT: (
            "Required canonical identity evidence conflicted and was withheld."
        ),
        ProviderAvailabilityStatus.UNAVAILABLE: "Canonical identity was unavailable.",
        ProviderAvailabilityStatus.AVAILABLE: "Canonical identity was unavailable.",
    }[status]


def _scenario_execution_context(outcome: Any) -> ReverseDcfScenarioExecutionContext | None:
    reverse = getattr(outcome, "reverse_dcf_audit", None)
    trajectory = getattr(reverse, "trajectory", None)
    market_anchor = getattr(reverse, "market_anchor", None)
    if trajectory is None or market_anchor is None:
        return None
    try:
        return ReverseDcfScenarioExecutionContext(
            target_security_id=trajectory.target_security_id,
            target_issuer_id=trajectory.target_issuer_id,
            analysis_as_of=trajectory.analysis_as_of,
            valuation_currency=trajectory.valuation_currency,
            trajectory=trajectory,
            market_anchor=market_anchor,
            operating_tax=getattr(reverse, "operating_tax", None),
            actual_base=getattr(reverse, "actual_base", None),
            canonical_sales_to_capital=getattr(reverse, "canonical_sales_to_capital", None),
            production_wacc=getattr(reverse, "production_wacc", None),
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _normalized_symbol(symbol: object) -> str:
    # This is input hygiene only. Canonical identity remains the responsibility
    # of the existing identity/provider architecture.
    return str(symbol or "").strip().upper()


def _aware_snapshot(value: datetime | None) -> datetime:
    snapshot = value or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    return snapshot


class ResearchReportCache:
    """Small in-memory cache of canonical reports, never raw provider data."""

    def __init__(
        self,
        policy: ReportCachePolicy = DEFAULT_REPORT_CACHE_POLICY,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._entries: OrderedDict[_CacheKey, _CacheEntry] = OrderedDict()
        self._lock = RLock()

    def key_for(self, symbol: str, analysis_as_of: datetime, report_version: str) -> _CacheKey:
        timestamp = analysis_as_of.timestamp()
        if not math.isfinite(timestamp):
            raise ValueError("analysis_as_of timestamp must be finite")
        return _CacheKey(
            _normalized_symbol(symbol),
            int(timestamp // self.policy.snapshot_bucket_seconds),
            str(report_version),
        )

    def get(self, key: _CacheKey) -> ResearchReportBuildResult | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if self._clock() - entry.stored_at >= self.policy.ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return replace(entry.result, cache_status=ResearchReportCacheStatus.HIT)

    def put(self, key: _CacheKey, result: ResearchReportBuildResult) -> None:
        # Failed/unavailable executions remain user-visible session state but are
        # not cached, so an explicit retry always performs one fresh attempt.
        if result.report is None:
            return
        with self._lock:
            self._entries[key] = _CacheEntry(self._clock(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self.policy.max_entries:
                self._entries.popitem(last=False)

    def invalidate_symbol(self, symbol: str) -> None:
        normalized = _normalized_symbol(symbol)
        with self._lock:
            for key in tuple(self._entries):
                if key.symbol == normalized:
                    del self._entries[key]

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)


def _default_runner(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime,
    build_context: ResearchBuildContext | None = None,
):
    from stock_analyser.live_research_report_audit import run_live_research_report_audit
    from .live_configuration import prepare_live_environment

    project_root = Path(__file__).resolve().parents[3]
    resolved_environment, configuration_source = prepare_live_environment(
        environment,
        project_root=project_root,
    )

    return run_live_research_report_audit(
        symbol,
        environment=resolved_environment,
        analysis_as_of=analysis_as_of,
        build_context=build_context,
        identity_configuration_source=configuration_source,
    )


def _accepts_build_context(runner: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        item.name == "build_context" or item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters
    )


class V1ResearchCoordinator:
    """Coordinate exactly one bounded existing live report execution."""

    def __init__(
        self,
        *,
        runner: Callable[..., Any] | None = None,
        cache: ResearchReportCache | None = None,
        report_version: str = REPORT_POLICY_ID,
        single_flight_policy: SingleFlightPolicy = DEFAULT_SINGLE_FLIGHT_POLICY,
        context_factory: Callable[[str, datetime], ResearchBuildContext] = ResearchBuildContext,
    ) -> None:
        self._runner = runner or _default_runner
        self.cache = cache or ResearchReportCache()
        self.report_version = str(report_version).strip()
        self.single_flight_policy = single_flight_policy
        self._context_factory = context_factory
        self._flight_lock = RLock()
        self._flights: dict[_CacheKey, _InFlightBuild] = {}
        if not self.report_version:
            raise ValueError("report_version must be non-empty")

    def build(
        self,
        symbol: object,
        *,
        environment: Mapping[str, str],
        analysis_as_of: datetime | None = None,
        force_refresh: bool = False,
    ) -> ResearchReportBuildResult:
        snapshot = _aware_snapshot(analysis_as_of)
        normalized = _normalized_symbol(symbol)
        if not normalized:
            return ResearchReportBuildResult(
                requested_symbol="",
                analysis_as_of=snapshot,
                status=ResearchReportBuildStatus.FAILED,
                report=None,
                stage=ResearchReportPipelineStage.INPUT,
                blocking_reason="Enter a ticker before running the analysis.",
                issues=("Ticker input was empty.",),
            )

        key = self.cache.key_for(normalized, snapshot, self.report_version)
        if force_refresh:
            self.cache.invalidate_symbol(normalized)
        else:
            cached = self.cache.get(key)
            if cached is not None:
                return cached

        with self._flight_lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = _InFlightBuild(Event())
                self._flights[key] = flight
                owns_flight = True
            else:
                owns_flight = False

        if not owns_flight:
            if not flight.completed.wait(self.single_flight_policy.wait_timeout_seconds):
                return ResearchReportBuildResult(
                    requested_symbol=normalized,
                    analysis_as_of=snapshot,
                    status=ResearchReportBuildStatus.FAILED,
                    report=None,
                    stage=ResearchReportPipelineStage.REPORT_BUILD,
                    blocking_reason="The active report build did not finish within the bounded wait.",
                    issues=("No duplicate provider execution was started.",),
                    cache_status=ResearchReportCacheStatus.SINGLE_FLIGHT,
                    policy_ids=(
                        REPORT_INTEGRATION_POLICY_ID, REPORT_POLICY_ID,
                        self.single_flight_policy.policy_id,
                    ),
                )
            if flight.result is None:
                return ResearchReportBuildResult(
                    requested_symbol=normalized,
                    analysis_as_of=snapshot,
                    status=ResearchReportBuildStatus.FAILED,
                    report=None,
                    stage=ResearchReportPipelineStage.REPORT_BUILD,
                    blocking_reason="The active report build ended without a result.",
                    cache_status=ResearchReportCacheStatus.SINGLE_FLIGHT,
                )
            return replace(flight.result, cache_status=ResearchReportCacheStatus.SINGLE_FLIGHT)

        try:
            result = self._execute_owned_build(
                normalized,
                snapshot,
                environment=environment,
                force_refresh=force_refresh,
            )
        except Exception:
            # This outer boundary also covers context construction, outcome
            # translation, and diagnostics finalization.  It guarantees every
            # waiter is released even if application metadata itself fails.
            result = ResearchReportBuildResult(
                requested_symbol=normalized,
                analysis_as_of=snapshot,
                status=ResearchReportBuildStatus.FAILED,
                report=None,
                stage=ResearchReportPipelineStage.REPORT_BUILD,
                blocking_reason="The canonical report pipeline did not complete.",
                issues=("A bounded report-build attempt failed; safe technical details were withheld.",),
                cache_status=(
                    ResearchReportCacheStatus.REFRESHED
                    if force_refresh else ResearchReportCacheStatus.MISS
                ),
            )
        finally:
            # Publish the terminal result before signalling.  There is no path
            # that can strand another caller behind an abandoned Event.
            if 'result' not in locals():
                result = ResearchReportBuildResult(
                    requested_symbol=normalized,
                    analysis_as_of=snapshot,
                    status=ResearchReportBuildStatus.FAILED,
                    report=None,
                    stage=ResearchReportPipelineStage.REPORT_BUILD,
                    blocking_reason="The canonical report pipeline did not complete.",
                )
            try:
                self.cache.put(key, result)
            finally:
                with self._flight_lock:
                    flight.result = result
                    flight.completed.set()
                    self._flights.pop(key, None)
        return result

    def _execute_owned_build(
        self,
        normalized: str,
        snapshot: datetime,
        *,
        environment: Mapping[str, str],
        force_refresh: bool,
    ) -> ResearchReportBuildResult:
        context = self._context_factory(normalized, snapshot)
        input_token = context.start_stage(ResearchReportPipelineStage.INPUT)
        context.finish_stage(input_token)
        cache_status = (
            ResearchReportCacheStatus.REFRESHED
            if force_refresh else ResearchReportCacheStatus.MISS
        )
        try:
            runner_kwargs = {"environment": environment, "analysis_as_of": snapshot}
            if _accepts_build_context(self._runner):
                runner_kwargs["build_context"] = context
            outcome = self._runner(normalized, **runner_kwargs)
        except Exception:
            result = ResearchReportBuildResult(
                requested_symbol=normalized,
                analysis_as_of=snapshot,
                status=ResearchReportBuildStatus.FAILED,
                report=None,
                stage=ResearchReportPipelineStage.REPORT_BUILD,
                blocking_reason="The canonical report pipeline did not complete.",
                issues=("A bounded report-build attempt failed; safe technical details were withheld.",),
                cache_status=cache_status,
            )
            failure_token = context.start_stage(ResearchReportPipelineStage.REPORT_BUILD)
            context.finish_stage(
                failure_token,
                status=ResearchBuildStageStatus.FAILED,
                safe_blocker=result.blocking_reason,
                issues_count=len(result.issues),
            )
        else:
            result = self._translate_outcome(
                normalized, snapshot, outcome, cache_status=cache_status,
            )
            if result.report is not None:
                complete_token = context.start_stage(ResearchReportPipelineStage.COMPLETE)
                context.finish_stage(
                    complete_token,
                    issues_count=len(result.issues),
                    warnings_count=len(result.warnings),
                )
            elif not any(item.stage is result.stage for item in context.stage_events):
                failure_token = context.start_stage(result.stage)
                context.finish_stage(
                    failure_token,
                    status=ResearchBuildStageStatus.FAILED,
                    safe_blocker=result.blocking_reason,
                    issues_count=len(result.issues),
                    warnings_count=len(result.warnings),
                )
        return replace(
            result,
            stage_events=context.stage_events,
            normalized_evidence_accesses=context.evidence_accesses,
            semantic_provider_call_count=context.semantic_provider_call_count,
            normalized_reuse_count=context.reused_count,
            duplicate_requests_avoided=context.duplicate_requests_avoided,
            policy_ids=(*result.policy_ids, self.single_flight_policy.policy_id),
        )

    def retry(
        self,
        symbol: object,
        *,
        environment: Mapping[str, str],
        analysis_as_of: datetime | None = None,
    ) -> ResearchReportBuildResult:
        """Perform one user-triggered invalidation and one rebuild attempt."""
        return self.build(
            symbol,
            environment=environment,
            analysis_as_of=analysis_as_of,
            force_refresh=True,
        )

    @staticmethod
    def _translate_outcome(
        symbol: str,
        snapshot: datetime,
        outcome: Any,
        *,
        cache_status: ResearchReportCacheStatus,
    ) -> ResearchReportBuildResult:
        report = getattr(outcome, "report", None)
        safe_notes = _safe_outcome_issues(outcome)
        identity_diagnostic = _identity_diagnostic(outcome)
        if report is None:
            market_audit = getattr(outcome, "market_audit", None)
            publication_outcome = getattr(market_audit, "publication_outcome", None)
            own_history = getattr(publication_outcome, "own_history", None)
            peer_family = getattr(publication_outcome, "peer_family", None)
            identity = getattr(getattr(own_history, "result", None), "identity", None)
            if identity is None:
                identity = getattr(peer_family, "target_identity", None)
            identity_unavailable = identity is None
            provider_availability = classify_provider_availability(
                identity_diagnostic,
                canonical_identity_available=not identity_unavailable,
            )
            return ResearchReportBuildResult(
                requested_symbol=symbol,
                analysis_as_of=snapshot,
                status=(
                    ResearchReportBuildStatus.UNAVAILABLE
                    if identity_unavailable else ResearchReportBuildStatus.FAILED
                ),
                report=None,
                stage=(
                    ResearchReportPipelineStage.IDENTITY
                    if identity_unavailable else ResearchReportPipelineStage.REPORT_BUILD
                ),
                blocking_reason=(
                    _identity_unavailable_reason(provider_availability)
                    if identity_unavailable else "The report contract failed closed."
                ),
                issues=safe_notes,
                cache_status=cache_status,
                identity_diagnostic=identity_diagnostic,
                provider_availability=provider_availability,
            )
        if not isinstance(report, StockResearchReport):
            return ResearchReportBuildResult(
                requested_symbol=symbol,
                analysis_as_of=snapshot,
                status=ResearchReportBuildStatus.FAILED,
                report=None,
                stage=ResearchReportPipelineStage.REPORT_BUILD,
                blocking_reason="The report builder returned an incompatible contract.",
                issues=safe_notes,
                cache_status=cache_status,
                identity_diagnostic=identity_diagnostic,
                provider_availability=classify_provider_availability(
                    identity_diagnostic,
                    canonical_identity_available=False,
                ),
            )
        if report.analysis_as_of != snapshot:
            return ResearchReportBuildResult(
                requested_symbol=symbol,
                analysis_as_of=snapshot,
                status=ResearchReportBuildStatus.FAILED,
                report=None,
                stage=ResearchReportPipelineStage.REPORT_BUILD,
                blocking_reason="The report analysis snapshot did not match the requested snapshot.",
                issues=safe_notes,
                cache_status=cache_status,
                identity_diagnostic=identity_diagnostic,
                provider_availability=classify_provider_availability(
                    identity_diagnostic,
                    canonical_identity_available=True,
                ),
            )
        status = (
            ResearchReportBuildStatus.READY
            if report.status is ReportStatus.READY
            else ResearchReportBuildStatus.PARTIAL
        )
        return ResearchReportBuildResult(
            requested_symbol=symbol,
            analysis_as_of=snapshot,
            status=status,
            report=report,
            stage=ResearchReportPipelineStage.COMPLETE,
            issues=(*safe_notes, *(item.message for item in report.issues)),
            warnings=report.warnings,
            cache_status=cache_status,
            supporting_ids=(
                report.report_id,
                report.target_security_id,
                report.target_issuer_id,
                *report.supporting_ids,
            ),
            provenance=report.provenance,
            policy_ids=(REPORT_INTEGRATION_POLICY_ID, REPORT_POLICY_ID, *report.policy_ids),
            identity_diagnostic=identity_diagnostic,
            provider_availability=ProviderAvailabilityStatus.AVAILABLE,
            scenario_context=_scenario_execution_context(outcome),
        )


def build_live_stock_research_report(
    symbol: object,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    coordinator: V1ResearchCoordinator | None = None,
    force_refresh: bool = False,
) -> ResearchReportBuildResult:
    """Convenience entry point for one canonical ticker-to-report execution."""
    service = coordinator or V1ResearchCoordinator()
    return service.build(
        symbol,
        environment=environment,
        analysis_as_of=analysis_as_of,
        force_refresh=force_refresh,
    )
