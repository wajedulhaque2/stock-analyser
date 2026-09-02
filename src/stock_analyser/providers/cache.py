"""Schema-versioned cache contracts and an in-memory reference implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any, Protocol

from .identity import ProviderId
from .transport import SafeRequestIdentity


V1_CACHE_SCHEMA_VERSION = "v1-provider-schema-1"


class CacheLookupStatus(str, Enum):
    HIT = "hit"
    MISS = "miss"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class CacheKey:
    provider: ProviderId
    endpoint_id: str
    method: str
    provider_symbol: str | None
    parameters: tuple[tuple[str, str], ...]
    schema_version: str = V1_CACHE_SCHEMA_VERSION

    @classmethod
    def from_request(
        cls,
        identity: SafeRequestIdentity,
        *,
        schema_version: str = V1_CACHE_SCHEMA_VERSION,
    ) -> "CacheKey":
        return cls(
            provider=identity.provider,
            endpoint_id=identity.endpoint_id,
            method=identity.method,
            provider_symbol=identity.provider_symbol,
            parameters=identity.safe_parameters,
            schema_version=schema_version,
        )

    @property
    def safe_key(self) -> str:
        payload = {
            "provider": self.provider.value,
            "endpoint": self.endpoint_id,
            "method": self.method,
            "symbol": self.provider_symbol,
            "parameters": self.parameters,
            "schema": self.schema_version,
        }
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"provider-cache:{digest}"


@dataclass(frozen=True, slots=True)
class CacheMetadata:
    created_at: datetime
    expires_at: datetime
    schema_version: str
    provider: ProviderId
    endpoint_id: str
    safe_key: str
    retrieved_at: datetime | None = None
    as_of_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("created_at", "expires_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.expires_at < self.created_at:
            raise ValueError("expires_at must not precede created_at")
        for name in ("retrieved_at", "as_of_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware when present")


@dataclass(frozen=True, slots=True, repr=False)
class CacheEntry:
    value: Any = field(repr=False)
    metadata: CacheMetadata


@dataclass(frozen=True, slots=True)
class CacheLookup:
    status: CacheLookupStatus
    entry: CacheEntry | None = field(default=None, repr=False)

    @property
    def value(self) -> Any | None:
        return self.entry.value if self.status is CacheLookupStatus.HIT and self.entry is not None else None


class Cache(Protocol):
    def get(self, key: CacheKey) -> CacheLookup: ...
    def set(
        self,
        key: CacheKey,
        value: Any,
        *,
        ttl_seconds: float,
        retrieved_at: datetime | None = None,
        as_of_at: datetime | None = None,
    ) -> CacheEntry: ...
    def delete(self, key: CacheKey) -> None: ...
    def clear_provider(self, provider: ProviderId) -> int: ...


class InMemoryCache:
    def __init__(self, *, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._entries: dict[CacheKey, CacheEntry] = {}

    def get(self, key: CacheKey) -> CacheLookup:
        entry = self._entries.get(key)
        if entry is None:
            return CacheLookup(CacheLookupStatus.MISS)
        if entry.metadata.expires_at <= self._clock():
            return CacheLookup(CacheLookupStatus.EXPIRED, entry)
        return CacheLookup(CacheLookupStatus.HIT, entry)

    def set(
        self,
        key: CacheKey,
        value: Any,
        *,
        ttl_seconds: float,
        retrieved_at: datetime | None = None,
        as_of_at: datetime | None = None,
    ) -> CacheEntry:
        if not math.isfinite(float(ttl_seconds)) or ttl_seconds < 0:
            raise ValueError("ttl_seconds must be finite and non-negative")
        created = self._clock()
        metadata = CacheMetadata(
            created_at=created,
            expires_at=created + timedelta(seconds=float(ttl_seconds)),
            schema_version=key.schema_version,
            provider=key.provider,
            endpoint_id=key.endpoint_id,
            safe_key=key.safe_key,
            retrieved_at=retrieved_at,
            as_of_at=as_of_at,
        )
        entry = CacheEntry(value=value, metadata=metadata)
        self._entries[key] = entry
        return entry

    def delete(self, key: CacheKey) -> None:
        self._entries.pop(key, None)

    def clear_provider(self, provider: ProviderId) -> int:
        keys = [key for key in self._entries if key.provider is provider]
        for key in keys:
            del self._entries[key]
        return len(keys)
