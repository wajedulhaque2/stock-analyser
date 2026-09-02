"""Safe, process-local execution state for one V1 report build.

This is application metadata, not an economic domain model.  The registry may
hold only normalized/canonical immutable contracts and never provider payloads,
sessions, credentials, URLs, headers, or opaque lookup keys.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Any, Callable


_FORBIDDEN_NAMES = (
    "apikey", "api_key", "authorization", "companykey", "company_key",
    "credential", "environment", "header", "payload", "raw_json",
    "response", "secret", "session", "token", "url",
)
_FORBIDDEN_TEXT = (
    "http://", "https://", "api_key=", "apikey=", "authorization:",
    "companykey", "company_key", "bearer ",
)


class ResearchBuildStageStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    REUSED = "reused"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class NormalizedEvidenceKey:
    provider: str
    capability: str
    target_id: str
    analysis_as_of: datetime | None = None
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in ("provider", "capability", "target_id"):
            value = str(getattr(self, name)).strip()
            if not value or _contains_forbidden(value):
                raise ValueError(f"{name} must be safe non-empty metadata")
            object.__setattr__(self, name, value)
        if self.analysis_as_of is not None and (
            self.analysis_as_of.tzinfo is None or self.analysis_as_of.utcoffset() is None
        ):
            raise ValueError("analysis_as_of must be timezone-aware")
        normalized = tuple(sorted((str(key).strip(), str(value).strip()) for key, value in self.parameters))
        if any(not key or _contains_forbidden(key) or _contains_forbidden(value) for key, value in normalized):
            raise ValueError("parameters must contain only safe metadata")
        object.__setattr__(self, "parameters", normalized)


@dataclass(frozen=True, slots=True)
class NormalizedEvidenceAccess:
    provider: str
    capability: str
    target_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ResearchBuildStageEvent:
    stage: Any
    status: ResearchBuildStageStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    safe_blocker: str | None = None
    issues_count: int = 0
    warnings_count: int = 0
    normalized_reuse_count: int = 0

    def __post_init__(self) -> None:
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.finished_at < self.started_at or self.duration_seconds < 0:
            raise ValueError("stage timing must be nonnegative")
        for name in ("issues_count", "warnings_count", "normalized_reuse_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.safe_blocker is not None and _contains_forbidden(self.safe_blocker):
            raise ValueError("safe_blocker contains forbidden operational metadata")


@dataclass(frozen=True, slots=True)
class _StageToken:
    stage: Any
    started_at: datetime
    started_reuse_count: int


def _contains_forbidden(value: str) -> bool:
    normalized = str(value).strip().lower().replace("-", "_")
    return any(token in normalized for token in _FORBIDDEN_TEXT) or any(
        token in normalized.replace("_", "") for token in ("companykey", "apikey")
    )


def _validate_normalized_value(value: Any, *, path: str = "value", seen: set[int] | None = None) -> None:
    if value is None or isinstance(value, (bool, int, float, Decimal, date, datetime, Enum)):
        return
    if isinstance(value, str):
        if _contains_forbidden(value):
            raise ValueError(f"{path} contains forbidden operational metadata")
        return
    if isinstance(value, bytes) or isinstance(value, dict):
        raise TypeError(f"{path} must not contain raw payload structures")
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_normalized_value(item, path=f"{path}[{index}]", seen=seen)
        return
    if not is_dataclass(value):
        raise TypeError(f"{path} must be an immutable normalized dataclass or tuple")
    params = getattr(type(value), "__dataclass_params__", None)
    if params is None or not params.frozen:
        raise TypeError(f"{path} must be immutable")
    for field in fields(value):
        normalized_name = field.name.lower().replace("_", "")
        if any(token.replace("_", "") in normalized_name for token in _FORBIDDEN_NAMES):
            raise TypeError(f"{path}.{field.name} is forbidden in normalized evidence")
        _validate_normalized_value(getattr(value, field.name), path=f"{path}.{field.name}", seen=seen)


class ResearchBuildContext:
    """Own one report snapshot, normalized registry, and safe stage trace."""

    cancellation_supported = False

    def __init__(
        self,
        requested_symbol: str,
        analysis_as_of: datetime,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_symbol = str(requested_symbol).strip().upper()
        if not normalized_symbol:
            raise ValueError("requested_symbol must be non-empty")
        if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
            raise ValueError("analysis_as_of must be timezone-aware")
        self.requested_symbol = normalized_symbol
        self.analysis_as_of = analysis_as_of
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._registry: dict[NormalizedEvidenceKey, Any] = {}
        self._accesses: list[NormalizedEvidenceAccess] = []
        self._events: list[ResearchBuildStageEvent] = []
        self._issues: list[str] = []
        self._warnings: list[str] = []
        self._supporting_ids: list[str] = []
        self._policy_ids: list[str] = []
        self._provenance: list[Any] = []
        self._active_stage: Any | None = None
        self._lock = RLock()

    def get_or_create_normalized(self, key: NormalizedEvidenceKey, factory: Callable[[], Any]) -> Any:
        if key.analysis_as_of is not None and key.analysis_as_of != self.analysis_as_of:
            raise ValueError("normalized evidence key snapshot must match the build context")
        with self._lock:
            if key in self._registry:
                self._accesses.append(NormalizedEvidenceAccess(
                    key.provider, key.capability, key.target_id, True,
                ))
                return self._registry[key]
            value = factory()
            _validate_normalized_value(value)
            self._registry[key] = value
            self._accesses.append(NormalizedEvidenceAccess(
                key.provider, key.capability, key.target_id, False,
            ))
            return value

    def register_normalized(self, key: NormalizedEvidenceKey, value: Any) -> Any:
        return self.get_or_create_normalized(key, lambda: value)

    def start_stage(self, stage: Any) -> _StageToken:
        with self._lock:
            started = self._clock()
            if started.tzinfo is None or started.utcoffset() is None:
                raise ValueError("stage clock must return timezone-aware values")
            self._active_stage = stage
            return _StageToken(stage, started, self.reused_count)

    def finish_stage(
        self,
        token: _StageToken,
        *,
        status: ResearchBuildStageStatus = ResearchBuildStageStatus.COMPLETED,
        safe_blocker: str | None = None,
        issues_count: int = 0,
        warnings_count: int = 0,
    ) -> ResearchBuildStageEvent:
        with self._lock:
            finished = self._clock()
            if finished.tzinfo is None or finished.utcoffset() is None:
                raise ValueError("stage clock must return timezone-aware values")
            blocker = None if safe_blocker is None else str(safe_blocker).strip()
            event = ResearchBuildStageEvent(
                stage=token.stage,
                status=status,
                started_at=token.started_at,
                finished_at=finished,
                duration_seconds=max(0.0, (finished - token.started_at).total_seconds()),
                safe_blocker=blocker or None,
                issues_count=issues_count,
                warnings_count=warnings_count,
                normalized_reuse_count=max(0, self.reused_count - token.started_reuse_count),
            )
            self._events.append(event)
            self._active_stage = None
            return event

    @property
    def active_stage(self) -> Any | None:
        with self._lock:
            return self._active_stage

    @property
    def stage_events(self) -> tuple[ResearchBuildStageEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def evidence_accesses(self) -> tuple[NormalizedEvidenceAccess, ...]:
        with self._lock:
            return tuple(self._accesses)

    @property
    def semantic_provider_call_count(self) -> int:
        with self._lock:
            return sum(not item.reused for item in self._accesses)

    @property
    def reused_count(self) -> int:
        with self._lock:
            return sum(item.reused for item in self._accesses)

    @property
    def duplicate_requests_avoided(self) -> int:
        return self.reused_count

    @property
    def registry_size(self) -> int:
        with self._lock:
            return len(self._registry)

    def add_safe_metadata(
        self,
        *,
        issues=(),
        warnings=(),
        supporting_ids=(),
        policy_ids=(),
        provenance=(),
    ) -> None:
        with self._lock:
            for target, values in (
                (self._issues, issues), (self._warnings, warnings),
                (self._supporting_ids, supporting_ids), (self._policy_ids, policy_ids),
            ):
                for value in values:
                    text = str(value).strip()
                    if text and not _contains_forbidden(text) and text not in target:
                        target.append(text)
            for item in provenance:
                _validate_normalized_value(item, path="provenance")
                if item not in self._provenance:
                    self._provenance.append(item)
