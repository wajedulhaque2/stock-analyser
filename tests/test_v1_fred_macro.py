from __future__ import annotations

from datetime import date, datetime, timezone

from stock_analyser.domain import CapabilityStatus, DataAvailability, MacroFrequency, MacroMetric, MetricUnit
from stock_analyser.providers import (
    CacheKey,
    FredAdapter,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    SecretReference,
    TransportResponse,
)
from stock_analyser.services import latest_macro_on_or_before, resolve_risk_free_rate


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
SECRET_A = "SYNTHETIC_FRED_A"
SECRET_B = "SYNTHETIC_FRED_B"


def fred_row(day, value, **changes):
    row = {
        "date": day, "value": value,
        "realtime_start": "2026-08-01", "realtime_end": "2026-08-31",
    }
    row.update(changes)
    return row


class FakeTransport:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return TransportResponse(status_code=200, headers={}, body=self.outcome, retrieved_at=NOW)


def adapter(body, *, secret=SECRET_A):
    source = FakeTransport(body)
    transport = RetryingTransport(
        source, retry_policy=RetryPolicy(max_attempts=1, jitter=0),
        sleep=lambda _: None, clock=lambda: NOW,
    )
    return FredAdapter(
        transport, credential=SecretReference("fred", lambda: secret), clock=lambda: NOW,
    ), source


def test_dgs10_percent_is_normalized_to_decimal_with_vintage_metadata():
    client, _ = adapter({"observations": [fred_row("2026-08-25", "4.70")]})
    result = client.fetch_dgs10()
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    observation = result.observations[0]
    assert observation.value == 0.047
    assert observation.series_id == "DGS10"
    assert observation.metric is MacroMetric.TREASURY_YIELD
    assert observation.unit is MetricUnit.PERCENT_DECIMAL
    assert observation.frequency is MacroFrequency.DAILY
    assert observation.currency == "USD"
    assert observation.realtime_start == date(2026, 8, 1)
    assert observation.realtime_end == date(2026, 8, 31)
    assert "divided provider percent" in observation.provenance.transformation_steps[0]


def test_missing_fred_values_are_skipped_and_never_converted_to_zero():
    client, _ = adapter({"observations": [
        fred_row("2026-08-22", "."), fred_row("2026-08-23", ""),
        fred_row("2026-08-24", None), fred_row("2026-08-25", "4.70"),
    ]})
    result = client.fetch_dgs10()
    assert [item.value for item in result.observations] == [0.047]
    assert not any(item.value == 0 for item in result.observations)
    assert len([item for item in result.issues if "skipped" in item.reason]) == 3


def test_malformed_unhashable_fred_value_becomes_an_issue_not_an_exception():
    client, _ = adapter({"observations": [fred_row("2026-08-25", {"bad": "value"})]})
    result = client.fetch_dgs10()
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert any("invalid date, vintage, or numeric" in item.reason for item in result.issues)


def test_as_of_selection_uses_latest_valid_observation_not_future_data():
    client, _ = adapter({"observations": [
        fred_row("2026-08-24", "4.60"), fred_row("2026-08-25", "4.70"),
        fred_row("2026-08-27", "4.90"),
    ]})
    observations = client.fetch_dgs10().observations
    selected = latest_macro_on_or_before(observations, date(2026, 8, 26))
    assert selected is not None and selected.observation_date == date(2026, 8, 25)
    result = resolve_risk_free_rate("USD", observations, as_of_at=NOW)
    assert result.availability is DataAvailability.AVAILABLE
    assert result.observation is selected


def test_non_usd_currency_returns_structured_unavailable_without_usd_fallback():
    client, _ = adapter({"observations": [fred_row("2026-08-25", "4.70")]})
    observations = client.fetch_dgs10().observations
    for currency in ("GBP", "EUR", "JPY"):
        result = resolve_risk_free_rate(currency, observations, as_of_at=NOW)
        assert result.currency == currency
        assert result.availability is DataAvailability.UNAVAILABLE
        assert result.observation is None
        assert currency in result.reason


def test_usd_without_observation_on_or_before_date_is_structured_unavailable():
    client, _ = adapter({"observations": [fred_row("2026-08-27", "4.90")]})
    result = resolve_risk_free_rate("USD", client.fetch_dgs10().observations, as_of_at=NOW)
    assert result.availability is DataAvailability.UNAVAILABLE
    assert result.observation is None


def test_provider_error_is_safe_and_capability_specific():
    failure = ProviderError(
        provider=ProviderId.FRED, endpoint_id="fred_dgs10_observations",
        category=ProviderErrorCategory.RATE_LIMIT, retryable=False,
        safe_message="safe synthetic provider failure", status_code=429,
    )
    client, _ = adapter(failure)
    result = client.fetch_dgs10()
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.ERROR
    assert all(SECRET_A not in item.reason for item in result.issues)


def test_fred_secret_is_absent_from_safe_identity_repr_and_cache_key():
    first, first_source = adapter({"observations": []})
    second, second_source = adapter({"observations": []}, secret=SECRET_B)
    first.fetch_dgs10()
    second.fetch_dgs10()
    request_a, request_b = first_source.requests[0], second_source.requests[0]
    assert request_a.url == "https://api.stlouisfed.org/fred/series/observations"
    assert request_a.parameters == {
        "series_id": "DGS10", "file_type": "json", "api_key": SECRET_A,
    }
    assert SECRET_A not in repr(first._credential)
    assert SECRET_A not in repr(request_a)
    assert SECRET_A not in repr(request_a.safe_identity)
    assert CacheKey.from_request(request_a.safe_identity) == CacheKey.from_request(request_b.safe_identity)


def test_naive_macro_snapshot_is_rejected():
    client, _ = adapter({"observations": []})
    result = client.fetch_dgs10(source_as_of_at=datetime(2026, 8, 26, 12))
    assert result.capabilities[0].status is CapabilityStatus.ERROR
