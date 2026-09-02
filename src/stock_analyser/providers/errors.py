"""Structured, secret-safe provider failures."""

from __future__ import annotations

from enum import Enum

from .identity import ProviderId
from .sanitization import redact_text, redact_url


class ProviderErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    ENTITLEMENT = "entitlement"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    EMPTY_RESPONSE = "empty_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """An error record that deliberately retains no request headers or response body."""

    def __init__(
        self,
        *,
        provider: ProviderId,
        endpoint_id: str,
        category: ProviderErrorCategory,
        retryable: bool,
        safe_message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
        attempts: int = 1,
    ) -> None:
        self.provider = provider
        self.endpoint_id = redact_url(endpoint_id)
        self.category = category
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.safe_message = redact_text(safe_message)
        self.retry_after = retry_after
        self.attempts = attempts
        super().__init__(self.__str__())

    def __str__(self) -> str:
        status = f" status={self.status_code}" if self.status_code is not None else ""
        retry = f" retry_after={self.retry_after:g}s" if self.retry_after is not None else ""
        return (
            f"{self.provider.value}:{self.endpoint_id} {self.category.value}{status} "
            f"after {self.attempts} attempt(s): {self.safe_message}{retry}"
        )


class InvalidTransportResponse(TypeError):
    """Raised by a transport implementation that violates its normalized contract."""

