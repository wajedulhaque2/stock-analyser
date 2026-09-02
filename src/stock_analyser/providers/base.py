"""Base V1 adapter contract with endpoint-level capability isolation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from stock_analyser.domain import CapabilityResult, CapabilityStatus

from .errors import ProviderError, ProviderErrorCategory
from .identity import ProviderId
from .transport import RetryingTransport, TransportRequest, TransportResponse


class BaseProviderAdapter(ABC):
    provider: ProviderId

    def __init__(self, transport: RetryingTransport | None = None, *, clock=None) -> None:
        self.transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._capabilities: dict[str, CapabilityResult] = {}

    def record_capability(
        self,
        capability: str,
        status: CapabilityStatus,
        *,
        reason: str | None = None,
    ) -> CapabilityResult:
        result = CapabilityResult(
            provider=self.provider.value,
            capability=capability,
            status=status,
            checked_at=self._clock(),
            reason=reason,
        )
        self._capabilities[capability] = result
        return result

    def capability(self, capability: str) -> CapabilityResult:
        return self._capabilities.get(capability) or CapabilityResult(
            provider=self.provider.value,
            capability=capability,
            status=CapabilityStatus.UNAVAILABLE,
            checked_at=self._clock(),
            reason="capability has not been probed",
        )

    def capability_results(self) -> tuple[CapabilityResult, ...]:
        return tuple(self._capabilities[key] for key in sorted(self._capabilities))

    def execute(self, request: TransportRequest) -> TransportResponse:
        if request.provider is not self.provider:
            raise ValueError("request provider does not match adapter provider")
        if self.transport is None:
            raise RuntimeError("this adapter has no injected transport")
        return self.transport.execute(request)

    def probe_capability(self, capability: str) -> CapabilityResult:
        try:
            result = self._probe_capability(capability)
        except ProviderError as error:
            result = self._capability_from_error(capability, error)
        self._capabilities[capability] = result
        return result

    def probe_capabilities(self, capabilities: tuple[str, ...] | list[str]) -> tuple[CapabilityResult, ...]:
        # Each endpoint is isolated: failure never clears or overwrites another result.
        return tuple(self.probe_capability(capability) for capability in capabilities)

    def record_provider_error(self, capability: str, error: ProviderError) -> CapabilityResult:
        """Translate and retain an injected-source error without affecting other endpoints."""
        if error.provider is not self.provider:
            raise ValueError("provider error does not match adapter provider")
        result = self._capability_from_error(capability, error)
        self._capabilities[capability] = result
        return result

    def _capability_from_error(self, capability: str, error: ProviderError) -> CapabilityResult:
        if error.category is ProviderErrorCategory.ENTITLEMENT:
            status = CapabilityStatus.LOCKED
        elif error.category in {ProviderErrorCategory.NOT_FOUND, ProviderErrorCategory.EMPTY_RESPONSE}:
            status = CapabilityStatus.UNAVAILABLE
        else:
            status = CapabilityStatus.ERROR
        return CapabilityResult(
            provider=self.provider.value,
            capability=capability,
            status=status,
            checked_at=self._clock(),
            reason=str(error),
        )

    @abstractmethod
    def _probe_capability(self, capability: str) -> CapabilityResult:
        """Probe one infrastructure capability without assuming business methods."""
        raise NotImplementedError
