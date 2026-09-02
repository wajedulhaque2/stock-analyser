"""Mockable transport contracts plus bounded retry policy.

This module has no provider business endpoints and performs no network I/O by
itself. A later production transport can implement ``Transport``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import time
from typing import Any, Protocol

from .errors import InvalidTransportResponse, ProviderError, ProviderErrorCategory
from .identity import ProviderId
from .sanitization import redact_headers, redact_url, safe_parameters


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    request_timeout: float = 20.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            raise ValueError("request_timeout must be finite and positive")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_backoff: float = 1.0
    maximum_backoff: float = 30.0
    jitter: float = 0.25
    maximum_retry_after: float = 120.0
    retry_timeouts: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        for name in ("base_backoff", "maximum_backoff", "jitter", "maximum_retry_after"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.maximum_backoff < self.base_backoff:
            raise ValueError("maximum_backoff must be at least base_backoff")

    def backoff(self, failed_attempt: int, jitter_value: float = 0.0) -> float:
        base = min(self.maximum_backoff, self.base_backoff * (2 ** max(0, failed_attempt - 1)))
        bounded_jitter = min(1.0, max(0.0, float(jitter_value))) * self.jitter
        return min(self.maximum_backoff, base + bounded_jitter)


@dataclass(frozen=True, slots=True)
class SafeRequestIdentity:
    provider: ProviderId
    endpoint_id: str
    method: str
    safe_url: str
    safe_parameters: tuple[tuple[str, str], ...]
    safe_headers: tuple[tuple[str, str], ...]
    provider_symbol: str | None = None

    @classmethod
    def from_request(cls, request: "TransportRequest") -> "SafeRequestIdentity":
        return cls(
            provider=request.provider,
            endpoint_id=redact_url(request.endpoint_id),
            method=request.method.upper(),
            safe_url=redact_url(request.url),
            safe_parameters=safe_parameters(request.parameters),
            safe_headers=redact_headers(request.headers),
            provider_symbol=request.provider_symbol,
        )


@dataclass(frozen=True, slots=True, repr=False)
class TransportRequest:
    provider: ProviderId
    endpoint_id: str
    method: str
    url: str
    parameters: Mapping[str, Any] = field(default_factory=dict, repr=False)
    headers: Mapping[str, Any] = field(default_factory=dict, repr=False)
    timeout: float | None = None
    provider_symbol: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint_id.strip():
            raise ValueError("endpoint_id must be non-empty")
        if not self.method.strip():
            raise ValueError("method must be non-empty")
        if not self.url.strip():
            raise ValueError("url must be non-empty")
        if self.timeout is not None and (not math.isfinite(float(self.timeout)) or self.timeout <= 0):
            raise ValueError("timeout must be finite and positive when supplied")

    @property
    def safe_identity(self) -> SafeRequestIdentity:
        return SafeRequestIdentity.from_request(self)

    def __repr__(self) -> str:
        return f"TransportRequest({self.safe_identity!r})"


@dataclass(frozen=True, slots=True, repr=False)
class TransportResponse:
    status_code: int
    headers: Mapping[str, Any]
    body: Any = field(repr=False)
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not 100 <= int(self.status_code) <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")

    def __repr__(self) -> str:
        return f"TransportResponse(status_code={self.status_code}, retrieved_at={self.retrieved_at.isoformat()!r})"


class Transport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse:
        """Return a normalized response or raise TimeoutError/ConnectionError."""


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        seconds = int(value.strip())
        return float(seconds) if seconds >= 0 else None
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _http_error(provider: ProviderId, endpoint_id: str, response: TransportResponse) -> ProviderError:
    status = response.status_code
    if status == 400:
        category, retryable, message = ProviderErrorCategory.INVALID_REQUEST, False, "provider rejected the request"
    elif status == 401:
        category, retryable, message = ProviderErrorCategory.AUTHENTICATION, False, "provider authentication failed"
    elif status == 403:
        # The generic layer cannot distinguish bad credentials from plan entitlement.
        category, retryable, message = ProviderErrorCategory.UNKNOWN, False, "provider access was forbidden; adapter classification is required"
    elif status == 404:
        category, retryable, message = ProviderErrorCategory.NOT_FOUND, False, "provider endpoint or resource was not found"
    elif status == 429:
        category, retryable, message = ProviderErrorCategory.RATE_LIMIT, True, "provider rate limit was reached"
    elif status == 503:
        category, retryable, message = ProviderErrorCategory.PROVIDER_UNAVAILABLE, True, "provider is temporarily unavailable"
    elif 500 <= status <= 599:
        category, retryable, message = ProviderErrorCategory.SERVER, True, "provider returned a server error"
    elif 400 <= status <= 499:
        category, retryable, message = ProviderErrorCategory.INVALID_REQUEST, False, "provider returned a permanent client error"
    else:
        category, retryable, message = ProviderErrorCategory.INVALID_RESPONSE, False, "provider returned an unexpected HTTP status"
    return ProviderError(
        provider=provider,
        endpoint_id=endpoint_id,
        category=category,
        status_code=status,
        retryable=retryable,
        safe_message=message,
    )


class RetryingTransport:
    """Execute a transport with injected clock, sleep, and jitter sources."""

    def __init__(
        self,
        transport: Transport,
        *,
        timeout_policy: TimeoutPolicy | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        jitter_source: Callable[[], float] | None = None,
    ) -> None:
        self.transport = transport
        self.timeout_policy = timeout_policy or TimeoutPolicy()
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleep = sleep or time.sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.jitter_source = jitter_source or random.random

    def execute(self, request: TransportRequest) -> TransportResponse:
        effective = request if request.timeout is not None else replace(request, timeout=self.timeout_policy.request_timeout)
        last_error: ProviderError | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = self.transport.send(effective)
                if not isinstance(response, TransportResponse):
                    raise InvalidTransportResponse("transport did not return TransportResponse")
                if 200 <= response.status_code <= 299:
                    if response.body is None:
                        raise ProviderError(
                            provider=request.provider,
                            endpoint_id=request.endpoint_id,
                            category=ProviderErrorCategory.EMPTY_RESPONSE,
                            retryable=False,
                            safe_message="provider returned an empty response",
                            attempts=attempt,
                        )
                    return response
                error = _http_error(request.provider, request.endpoint_id, response)
                retry_after = None
                if error.category is ProviderErrorCategory.RATE_LIMIT:
                    parsed = parse_retry_after(_header(response.headers, "Retry-After"), self.clock())
                    retry_after = min(parsed, self.retry_policy.maximum_retry_after) if parsed is not None else None
                error = ProviderError(
                    provider=error.provider,
                    endpoint_id=error.endpoint_id,
                    category=error.category,
                    status_code=error.status_code,
                    retryable=error.retryable,
                    safe_message=error.safe_message,
                    retry_after=retry_after,
                    attempts=attempt,
                )
            except TimeoutError:
                error = ProviderError(
                    provider=request.provider,
                    endpoint_id=request.endpoint_id,
                    category=ProviderErrorCategory.TIMEOUT,
                    retryable=self.retry_policy.retry_timeouts,
                    safe_message="provider request timed out",
                    attempts=attempt,
                )
            except ConnectionError:
                error = ProviderError(
                    provider=request.provider,
                    endpoint_id=request.endpoint_id,
                    category=ProviderErrorCategory.CONNECTION,
                    retryable=True,
                    safe_message="provider connection failed",
                    attempts=attempt,
                )
            except InvalidTransportResponse:
                error = ProviderError(
                    provider=request.provider,
                    endpoint_id=request.endpoint_id,
                    category=ProviderErrorCategory.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="transport returned an invalid normalized response",
                    attempts=attempt,
                )
            except ProviderError as caught:
                error = caught

            last_error = error
            if not error.retryable or attempt >= self.retry_policy.max_attempts:
                raise error
            delay = error.retry_after
            if delay is None:
                delay = self.retry_policy.backoff(attempt, self.jitter_source())
            self.sleep(delay)
        assert last_error is not None
        raise last_error
