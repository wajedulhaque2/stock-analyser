"""Bounded FRED DGS10 macro adapter; no non-USD rate inference."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Mapping

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    DataIssue,
    IssueSeverity,
    MacroFrequency,
    MacroMetric,
    MacroObservation,
    MetricUnit,
    Provenance,
)

from ._normalization import aware_datetime, calendar_date
from .base import BaseProviderAdapter
from .contracts import ProviderCapability, ProviderMacroResult, SecretReference
from .errors import ProviderError
from .identity import ProviderId
from .transport import RetryingTransport, TransportRequest


class FredAdapter(BaseProviderAdapter):
    provider = ProviderId.FRED

    def __init__(
        self,
        transport: RetryingTransport,
        *,
        credential: SecretReference,
        base_url: str = "https://api.stlouisfed.org/fred/series/observations",
        clock=None,
    ) -> None:
        super().__init__(transport, clock=clock)
        self._credential = credential
        self._base_url = base_url

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    def _request(self) -> TransportRequest:
        return TransportRequest(
            provider=self.provider,
            endpoint_id="fred_dgs10_observations",
            method="GET",
            url=self._base_url,
            parameters={"series_id": "DGS10", "file_type": "json", "api_key": self._credential.resolve()},
            provider_symbol="DGS10",
        )

    def fetch_dgs10(self, *, source_as_of_at: datetime | None = None) -> ProviderMacroResult:
        capability_name = ProviderCapability.USD_TREASURY_YIELD.value
        try:
            response = self.execute(self._request())
        except ProviderError as error:
            result = self.record_provider_error(capability_name, error)
            return ProviderMacroResult(capabilities=(result,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric=MacroMetric.TREASURY_YIELD.value,
                provider=self.provider.value, reason="FRED DGS10 retrieval failed",
            ),))
        except Exception:
            result = self.record_capability(
                capability_name, CapabilityStatus.ERROR, reason="FRED DGS10 request could not be constructed",
            )
            return ProviderMacroResult(capabilities=(result,))

        if not isinstance(response.body, Mapping) or not isinstance(response.body.get("observations"), (list, tuple)):
            result = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="FRED returned no supported DGS10 observation collection",
            )
            return ProviderMacroResult(capabilities=(result,))
        try:
            as_of_at, substituted = aware_datetime(source_as_of_at, fallback=response.retrieved_at)
        except (TypeError, ValueError, OverflowError):
            result = self.record_capability(
                capability_name, CapabilityStatus.ERROR, reason="FRED source as-of timestamp is invalid",
            )
            return ProviderMacroResult(capabilities=(result,))

        observations = []
        issues = []
        for row in response.body["observations"]:
            if not isinstance(row, Mapping):
                continue
            raw_value = row.get("value")
            if raw_value is None or raw_value == "." or raw_value == "":
                issues.append(DataIssue(
                    severity=IssueSeverity.INFO, metric=MacroMetric.TREASURY_YIELD.value,
                    provider=self.provider.value,
                    reason="FRED DGS10 observation is missing and was skipped, not converted to zero",
                ))
                continue
            try:
                percent_value = float(raw_value)
                if not math.isfinite(percent_value):
                    raise ValueError("non-finite")
                observation_date = calendar_date(row.get("date"))
                realtime_start = calendar_date(row["realtime_start"]) if row.get("realtime_start") else None
                realtime_end = calendar_date(row["realtime_end"]) if row.get("realtime_end") else None
                transformations = ["divided provider percent-per-annum value by 100 to canonical decimal rate"]
                if substituted:
                    transformations.append("source as-of unavailable; used retrieval time explicitly")
                provenance = Provenance(
                    provider=self.provider.value,
                    endpoint_or_dataset="fred_dgs10_observations",
                    provider_symbol="DGS10",
                    retrieved_at=response.retrieved_at,
                    as_of_at=as_of_at,
                    transformation_steps=tuple(transformations),
                    source_metric="DGS10",
                )
                observations.append(MacroObservation(
                    series_id="DGS10",
                    metric=MacroMetric.TREASURY_YIELD,
                    value=percent_value / 100.0,
                    unit=MetricUnit.PERCENT_DECIMAL,
                    currency="USD",
                    observation_date=observation_date,
                    frequency=MacroFrequency.DAILY,
                    as_of_at=as_of_at,
                    retrieved_at=response.retrieved_at,
                    provider=self.provider.value,
                    provenance=provenance,
                    realtime_start=realtime_start,
                    realtime_end=realtime_end,
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric=MacroMetric.TREASURY_YIELD.value,
                    provider=self.provider.value, reason="FRED DGS10 row has invalid date, vintage, or numeric semantics",
                ))
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "FRED returned no valid DGS10 observations"
        result = self.record_capability(capability_name, status, reason=reason)
        return ProviderMacroResult(tuple(observations), (result,), tuple(issues))
