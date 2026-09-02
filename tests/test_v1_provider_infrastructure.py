from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from stock_analyser.domain import CapabilityResult, CapabilityStatus
from stock_analyser.providers import (
    REDACTED,
    BaseProviderAdapter,
    CacheKey,
    CacheLookupStatus,
    InMemoryCache,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    TimeoutPolicy,
    TransportRequest,
    TransportResponse,
    V1_CACHE_SCHEMA_VERSION,
    redact_headers,
    redact_text,
    redact_url,
)


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
SYNTHETIC_SECRET = "SYNTHETIC_CREDENTIAL_DO_NOT_USE"
_DEFAULT_BODY = object()


def response(status=200, *, headers=None, body=_DEFAULT_BODY):
    return TransportResponse(
        status_code=status,
        headers=headers or {},
        body={"synthetic": "data"} if body is _DEFAULT_BODY else body,
        retrieved_at=NOW,
    )


def request(*, endpoint="synthetic_data", symbol="SYN", params=None, headers=None, url=None):
    return TransportRequest(
        provider=ProviderId.FMP,
        endpoint_id=endpoint,
        method="GET",
        url=url or "https://synthetic.invalid/data",
        parameters=params or {"symbol": symbol},
        headers=headers or {},
        provider_symbol=symbol,
    )


class SequenceTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def send(self, outgoing):
        self.requests.append(outgoing)
        if not self.outcomes:
            raise AssertionError("unexpected transport attempt")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def executor(outcomes, *, policy=None, waits=None, jitter=None):
    transport = SequenceTransport(outcomes)
    waits = waits if waits is not None else []
    wrapped = RetryingTransport(
        transport,
        retry_policy=policy or RetryPolicy(jitter=0),
        sleep=waits.append,
        clock=lambda: NOW,
        jitter_source=(lambda: 0.0) if jitter is None else jitter,
    )
    return wrapped, transport, waits


class SyntheticAdapter(BaseProviderAdapter):
    provider = ProviderId.FMP

    def __init__(self, transport, actions):
        super().__init__(transport, clock=lambda: NOW)
        self.actions = actions

    def _probe_capability(self, capability):
        action = self.actions[capability]
        if isinstance(action, BaseException):
            raise action
        return CapabilityResult(
            provider=self.provider.value,
            capability=capability,
            status=action,
            checked_at=NOW,
            reason=None if action is CapabilityStatus.AVAILABLE else f"synthetic {action.value}",
        )


def provider_error(category, *, retryable=False, status=None):
    return ProviderError(
        provider=ProviderId.FMP,
        endpoint_id="synthetic_capability",
        category=category,
        status_code=status,
        retryable=retryable,
        safe_message="synthetic provider failure",
    )


def test_base_adapter_exposes_independent_endpoint_capabilities():
    wrapped, _, _ = executor([response()])
    adapter = SyntheticAdapter(wrapped, {
        "estimates": CapabilityStatus.AVAILABLE,
        "targets": CapabilityStatus.LOCKED,
        "peers": CapabilityStatus.UNAVAILABLE,
        "other": CapabilityStatus.ERROR,
    })
    results = adapter.probe_capabilities(["estimates", "targets", "peers", "other"])
    assert [item.status for item in results] == [
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.LOCKED,
        CapabilityStatus.UNAVAILABLE,
        CapabilityStatus.ERROR,
    ]
    assert len(adapter.capability_results()) == 4


def test_failed_capability_does_not_invalidate_successful_capability():
    wrapped, _, _ = executor([response()])
    adapter = SyntheticAdapter(wrapped, {
        "working": CapabilityStatus.AVAILABLE,
        "failing": provider_error(ProviderErrorCategory.SERVER, retryable=True, status=503),
    })
    adapter.probe_capabilities(["working", "failing"])
    assert adapter.capability("working").status is CapabilityStatus.AVAILABLE
    assert adapter.capability("failing").status is CapabilityStatus.ERROR


def test_authentication_error_is_distinct_from_entitlement_lock():
    wrapped, _, _ = executor([response()])
    adapter = SyntheticAdapter(wrapped, {
        "auth": provider_error(ProviderErrorCategory.AUTHENTICATION, status=401),
        "locked": provider_error(ProviderErrorCategory.ENTITLEMENT, status=403),
    })
    auth, locked = adapter.probe_capabilities(["auth", "locked"])
    assert auth.status is CapabilityStatus.ERROR
    assert locked.status is CapabilityStatus.LOCKED
    assert "authentication" in auth.reason
    assert "entitlement" in locked.reason


def test_unprobed_capability_is_structured_unavailable_without_provider_wide_failure():
    wrapped, _, _ = executor([response()])
    adapter = SyntheticAdapter(wrapped, {})
    result = adapter.capability("not_probed")
    assert result.status is CapabilityStatus.UNAVAILABLE
    assert "not been probed" in result.reason


def test_default_timeout_policy_is_applied_centrally():
    wrapped, transport, _ = executor([response()])
    wrapped.execute(request())
    assert transport.requests[0].timeout == TimeoutPolicy().request_timeout


@pytest.mark.parametrize("retry_timeouts,expected_calls,expected_waits", [(True, 2, [1.0]), (False, 1, [])])
def test_timeout_retryability_follows_policy(retry_timeouts, expected_calls, expected_waits):
    outcomes = [TimeoutError("synthetic timeout"), response()]
    policy = RetryPolicy(max_attempts=3, base_backoff=1, maximum_backoff=10, jitter=0, retry_timeouts=retry_timeouts)
    wrapped, transport, waits = executor(outcomes, policy=policy)
    if retry_timeouts:
        wrapped.execute(request())
    else:
        with pytest.raises(ProviderError) as caught:
            wrapped.execute(request())
        assert caught.value.category is ProviderErrorCategory.TIMEOUT
        assert not caught.value.retryable
    assert len(transport.requests) == expected_calls
    assert waits == expected_waits


def test_connection_failure_uses_bounded_exponential_retry_without_sleeping():
    policy = RetryPolicy(max_attempts=3, base_backoff=1, maximum_backoff=10, jitter=0)
    wrapped, transport, waits = executor(
        [ConnectionError("one"), ConnectionError("two"), ConnectionError("three")],
        policy=policy,
    )
    with pytest.raises(ProviderError) as caught:
        wrapped.execute(request())
    assert caught.value.category is ProviderErrorCategory.CONNECTION
    assert caught.value.attempts == 3
    assert len(transport.requests) == 3
    assert waits == [1.0, 2.0]


@pytest.mark.parametrize(
    "status,category",
    [(500, ProviderErrorCategory.SERVER), (503, ProviderErrorCategory.PROVIDER_UNAVAILABLE)],
)
def test_retryable_server_statuses_follow_policy(status, category):
    wrapped, transport, waits = executor([response(status), response(200)])
    assert wrapped.execute(request()).status_code == 200
    assert len(transport.requests) == 2
    assert waits == [1.0]


@pytest.mark.parametrize(
    "status,category",
    [
        (400, ProviderErrorCategory.INVALID_REQUEST),
        (401, ProviderErrorCategory.AUTHENTICATION),
        (403, ProviderErrorCategory.UNKNOWN),
        (404, ProviderErrorCategory.NOT_FOUND),
    ],
)
def test_permanent_client_statuses_do_not_retry(status, category):
    wrapped, transport, waits = executor([response(status), response(200)])
    with pytest.raises(ProviderError) as caught:
        wrapped.execute(request())
    assert caught.value.category is category
    assert caught.value.category is not ProviderErrorCategory.ENTITLEMENT
    assert len(transport.requests) == 1
    assert waits == []


def test_http_429_retries_and_honors_integer_retry_after():
    wrapped, transport, waits = executor([
        response(429, headers={"Retry-After": "7"}),
        response(200),
    ])
    assert wrapped.execute(request()).status_code == 200
    assert len(transport.requests) == 2
    assert waits == [7.0]


@pytest.mark.parametrize("header", [None, "not-a-delay", "-1"])
def test_missing_or_invalid_retry_after_uses_exponential_backoff(header):
    headers = {} if header is None else {"Retry-After": header}
    wrapped, _, waits = executor([response(429, headers=headers), response(200)])
    wrapped.execute(request())
    assert waits == [1.0]


def test_retry_after_http_date_is_supported_and_safety_capped():
    future = (NOW + timedelta(seconds=300)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    policy = RetryPolicy(max_attempts=2, jitter=0, maximum_retry_after=20)
    wrapped, _, waits = executor([response(429, headers={"Retry-After": future}), response()], policy=policy)
    wrapped.execute(request())
    assert waits == [20]


def test_injected_jitter_is_deterministic():
    policy = RetryPolicy(max_attempts=3, base_backoff=1, maximum_backoff=10, jitter=0.5)
    wrapped, _, waits = executor(
        [ConnectionError("one"), ConnectionError("two"), response()],
        policy=policy,
        jitter=lambda: 0.4,
    )
    wrapped.execute(request())
    assert waits == pytest.approx([1.2, 2.2])


def test_query_secret_is_redacted_from_url_and_safe_request_identity():
    raw = f"https://synthetic.invalid/data?symbol=SYN&apikey={SYNTHETIC_SECRET}"
    safe = redact_url(raw)
    identity = request(url=raw, params={"symbol": "SYN", "apikey": SYNTHETIC_SECRET}).safe_identity
    assert SYNTHETIC_SECRET not in safe
    assert "REDACTED" in safe
    assert SYNTHETIC_SECRET not in repr(identity)
    assert identity.safe_parameters == (("symbol", '"SYN"'),)


@pytest.mark.parametrize("name", ["Authorization", "X-Api-Key", "api_key", "access-token"])
def test_sensitive_headers_are_redacted_case_insensitively(name):
    safe = dict(redact_headers({name: f"Bearer {SYNTHETIC_SECRET}"}))
    assert safe[name] == REDACTED
    assert SYNTHETIC_SECRET not in repr(safe)


def test_free_text_bearer_and_assignment_redaction_never_retains_secret():
    safe = redact_text(f"Authorization: Bearer {SYNTHETIC_SECRET} api_key={SYNTHETIC_SECRET}")
    assert SYNTHETIC_SECRET not in safe
    assert "REDACTED" in safe


def test_transport_request_repr_is_secret_safe():
    outgoing = request(
        params={"symbol": "SYN", "token": SYNTHETIC_SECRET},
        headers={"Authorization": f"Bearer {SYNTHETIC_SECRET}"},
    )
    assert SYNTHETIC_SECRET not in repr(outgoing)


def test_provider_error_never_uses_raw_proprietary_response_body():
    wrapped, _, _ = executor([response(500, body={"private": SYNTHETIC_SECRET})], policy=RetryPolicy(max_attempts=1))
    with pytest.raises(ProviderError) as caught:
        wrapped.execute(request())
    diagnostic = str(caught.value)
    assert SYNTHETIC_SECRET not in diagnostic
    assert not hasattr(caught.value, "body")
    assert not hasattr(caught.value, "headers")


def test_provider_error_constructor_redacts_secret_bearing_message_and_endpoint():
    error = ProviderError(
        provider=ProviderId.FMP,
        endpoint_id=f"https://synthetic.invalid/data?api_key={SYNTHETIC_SECRET}",
        category=ProviderErrorCategory.UNKNOWN,
        retryable=False,
        safe_message=f"token={SYNTHETIC_SECRET}",
    )
    assert SYNTHETIC_SECRET not in str(error)
    assert "REDACTED" in str(error)


def cache_key(*, symbol="SYN", endpoint="synthetic_data", params=None, headers=None, schema=V1_CACHE_SCHEMA_VERSION):
    identity = request(endpoint=endpoint, symbol=symbol, params=params, headers=headers).safe_identity
    return CacheKey.from_request(identity, schema_version=schema)


def test_cache_key_excludes_api_keys_and_authorization_headers():
    one = cache_key(
        params={"symbol": "SYN", "apikey": "SYNTHETIC_A"},
        headers={"Authorization": "Bearer SYNTHETIC_A"},
    )
    two = cache_key(
        params={"symbol": "SYN", "api_key": "SYNTHETIC_B"},
        headers={"Authorization": "Bearer SYNTHETIC_B"},
    )
    assert one.safe_key == two.safe_key
    assert "SYNTHETIC_A" not in repr(one)
    assert "SYNTHETIC_B" not in repr(two)


def test_cache_key_normalizes_parameter_order():
    one = cache_key(params={"a": 1, "b": 2})
    two = cache_key(params={"b": 2, "a": 1})
    assert one == two
    assert one.safe_key == two.safe_key


def test_cache_keys_change_for_symbol_endpoint_and_schema():
    base = cache_key(symbol="SYN", endpoint="one", schema="schema-1")
    assert base.safe_key != cache_key(symbol="OTHER", endpoint="one", schema="schema-1").safe_key
    assert base.safe_key != cache_key(symbol="SYN", endpoint="two", schema="schema-1").safe_key
    assert base.safe_key != cache_key(symbol="SYN", endpoint="one", schema="schema-2").safe_key


def test_cache_miss_hit_and_metadata_are_secret_safe():
    current = [NOW]
    cache = InMemoryCache(clock=lambda: current[0])
    key = cache_key(params={"symbol": "SYN", "api-key": SYNTHETIC_SECRET})
    assert cache.get(key).status is CacheLookupStatus.MISS
    entry = cache.set(key, {"normalized": 1}, ttl_seconds=60, retrieved_at=NOW, as_of_at=NOW)
    lookup = cache.get(key)
    assert lookup.status is CacheLookupStatus.HIT
    assert lookup.value == {"normalized": 1}
    assert entry.metadata.schema_version == V1_CACHE_SCHEMA_VERSION
    assert entry.metadata.provider is ProviderId.FMP
    assert entry.metadata.endpoint_id == "synthetic_data"
    assert SYNTHETIC_SECRET not in repr(entry.metadata)
    metadata_fields = {item.name.lower().replace("_", "") for item in fields(type(entry.metadata))}
    assert not metadata_fields.intersection({"apikey", "authorization", "token", "secret", "rawpayload"})


def test_expired_cache_entry_is_not_returned_as_fresh():
    current = [NOW]
    cache = InMemoryCache(clock=lambda: current[0])
    key = cache_key()
    cache.set(key, {"normalized": "stale"}, ttl_seconds=10)
    current[0] += timedelta(seconds=10)
    lookup = cache.get(key)
    assert lookup.status is CacheLookupStatus.EXPIRED
    assert lookup.value is None
    assert lookup.entry is not None  # retained only for a future explicitly stale policy


def test_cache_delete_and_clear_provider_are_scoped():
    cache = InMemoryCache(clock=lambda: NOW)
    first = cache_key(symbol="ONE")
    second = cache_key(symbol="TWO")
    cache.set(first, 1, ttl_seconds=60)
    cache.set(second, 2, ttl_seconds=60)
    cache.delete(first)
    assert cache.get(first).status is CacheLookupStatus.MISS
    assert cache.clear_provider(ProviderId.FMP) == 1
    assert cache.get(second).status is CacheLookupStatus.MISS


def test_empty_or_invalid_normalized_response_is_structured_and_not_retried():
    for outcome, category in [
        (response(200, body=None), ProviderErrorCategory.EMPTY_RESPONSE),
        ({"not": "a response"}, ProviderErrorCategory.INVALID_RESPONSE),
    ]:
        wrapped, transport, waits = executor([outcome, response()])
        with pytest.raises(ProviderError) as caught:
            wrapped.execute(request())
        assert caught.value.category is category
        assert len(transport.requests) == 1
        assert waits == []
