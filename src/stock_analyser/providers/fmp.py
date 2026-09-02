"""Isolated FMP forward-consensus and external-reference adapter."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any, Mapping

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataIssue,
    EstimateCase,
    ExternalReferenceType,
    ExternalValuationReference,
    IssueSeverity,
    MetricId,
    MetricUnit,
    Provenance,
)

from ._normalization import aware_datetime, calendar_date, estimate_observation
from .base import BaseProviderAdapter
from .contracts import (
    ProviderCapability,
    ProviderDataResult,
    ProviderExternalReferenceResult,
    SecretReference,
)
from .errors import ProviderError
from .identity import ProviderId
from .transport import RetryingTransport, TransportRequest


_ENDPOINTS = {
    ProviderCapability.ANNUAL_ANALYST_ESTIMATES: (
        "fmp_annual_analyst_estimates", "analyst-estimates",
    ),
    ProviderCapability.ANALYST_PRICE_TARGETS: (
        "fmp_analyst_price_targets", "price-target-consensus",
    ),
    ProviderCapability.EXTERNAL_STANDARD_DCF: (
        "fmp_external_standard_dcf", "discounted-cash-flow",
    ),
}

_ESTIMATE_FIELDS = {
    MetricId.REVENUE: (
        MetricUnit.CURRENCY,
        {EstimateCase.LOW: "revenueLow", EstimateCase.AVERAGE: "revenueAvg", EstimateCase.HIGH: "revenueHigh"},
        "numAnalystsRevenue",
    ),
    MetricId.EBIT: (
        MetricUnit.CURRENCY,
        {EstimateCase.LOW: "ebitLow", EstimateCase.AVERAGE: "ebitAvg", EstimateCase.HIGH: "ebitHigh"},
        None,
    ),
    MetricId.EBITDA: (
        MetricUnit.CURRENCY,
        {EstimateCase.LOW: "ebitdaLow", EstimateCase.AVERAGE: "ebitdaAvg", EstimateCase.HIGH: "ebitdaHigh"},
        None,
    ),
    MetricId.NET_INCOME: (
        MetricUnit.CURRENCY,
        {
            EstimateCase.LOW: "netIncomeLow",
            EstimateCase.AVERAGE: "netIncomeAvg",
            EstimateCase.HIGH: "netIncomeHigh",
        },
        None,
    ),
    MetricId.EPS: (
        MetricUnit.CURRENCY_PER_SHARE,
        {EstimateCase.LOW: "epsLow", EstimateCase.AVERAGE: "epsAvg", EstimateCase.HIGH: "epsHigh"},
        "numAnalystsEps",
    ),
}

_TARGET_FIELDS = {
    "targetLow": ExternalReferenceType.ANALYST_TARGET_LOW,
    "targetMedian": ExternalReferenceType.ANALYST_TARGET_MEDIAN,
    "targetConsensus": ExternalReferenceType.ANALYST_TARGET_CONSENSUS,
    "targetHigh": ExternalReferenceType.ANALYST_TARGET_HIGH,
}


def _first_row(body: Any) -> Mapping[str, Any] | None:
    if isinstance(body, Mapping):
        return body
    if isinstance(body, (list, tuple)) and body and isinstance(body[0], Mapping):
        return body[0]
    return None


def _all_rows(body: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(body, (list, tuple)):
        return tuple(item for item in body if isinstance(item, Mapping))
    if isinstance(body, Mapping):
        return (body,)
    return ()


class FmpAdapter(BaseProviderAdapter):
    provider = ProviderId.FMP

    def __init__(
        self,
        transport: RetryingTransport,
        *,
        credential: SecretReference,
        base_url: str = "https://financialmodelingprep.com/stable",
        clock=None,
    ) -> None:
        super().__init__(transport, clock=clock)
        self._credential = credential
        self._base_url = base_url.rstrip("/")

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    @staticmethod
    def _provider_symbol(identity: CompanyIdentity) -> str | None:
        return next(
            (item.symbol for item in identity.provider_symbols if item.provider == ProviderId.FMP.value),
            None,
        )

    def _request(self, capability: ProviderCapability, symbol: str) -> TransportRequest:
        endpoint_id, path = _ENDPOINTS[capability]
        parameters: dict[str, Any] = {"symbol": symbol, "apikey": self._credential.resolve()}
        if capability is ProviderCapability.ANNUAL_ANALYST_ESTIMATES:
            parameters["period"] = "annual"
        return TransportRequest(
            provider=self.provider,
            endpoint_id=endpoint_id,
            method="GET",
            url=f"{self._base_url}/{path}",
            parameters=parameters,
            provider_symbol=symbol,
        )

    @staticmethod
    def _currency(identity: CompanyIdentity, attribute: str) -> str | None:
        value = getattr(identity, attribute, None)
        return value if isinstance(value, str) and len(value) == 3 and value.isupper() else None

    @staticmethod
    def _annual_period(identity: CompanyIdentity, raw_period_end: Any) -> tuple[date, date]:
        period_end = calendar_date(raw_period_end)
        fiscal_year_end = getattr(identity, "fiscal_year_end", None)
        if not isinstance(fiscal_year_end, str):
            raise ValueError("fiscal-year-end metadata is unavailable")
        month, day = (int(part) for part in fiscal_year_end.split("-"))
        expected_day = min(day, calendar.monthrange(period_end.year, month)[1])
        if (period_end.month, period_end.day) != (month, expected_day):
            raise ValueError("provider period end does not align with canonical fiscal-year-end metadata")
        prior_day = min(day, calendar.monthrange(period_end.year - 1, month)[1])
        prior_end = date(period_end.year - 1, month, prior_day)
        return prior_end + timedelta(days=1), period_end

    @staticmethod
    def _source_as_of(
        row: Mapping[str, Any],
        retrieved_at: datetime,
        explicit: datetime | None,
        *,
        provider_field: str = "asOf",
    ) -> tuple[datetime, bool]:
        if explicit is not None:
            return aware_datetime(explicit)
        return aware_datetime(row.get(provider_field), fallback=retrieved_at)

    def fetch_annual_estimates(
        self,
        identity: CompanyIdentity,
        *,
        source_as_of_at: datetime | None = None,
    ) -> ProviderDataResult:
        capability_name = ProviderCapability.ANNUAL_ANALYST_ESTIMATES.value
        symbol = self._provider_symbol(identity)
        currency = self._currency(identity, "reporting_currency")
        if symbol is None or currency is None:
            reason = "identity has no explicit FMP symbol" if symbol is None else "reporting currency is unavailable"
            capability = self.record_capability(capability_name, CapabilityStatus.UNAVAILABLE, reason=reason)
            issue = DataIssue(
                severity=IssueSeverity.ERROR,
                metric="annual_analyst_estimates",
                provider=self.provider.value,
                reason=reason,
                action="withhold monetary consensus observations rather than assume a provider symbol or currency",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(issue,))

        try:
            response = self.execute(self._request(ProviderCapability.ANNUAL_ANALYST_ESTIMATES, symbol))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="annual_analyst_estimates",
                provider=self.provider.value,
                reason="FMP annual analyst-estimates retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="FMP annual analyst-estimates request could not be constructed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="annual_analyst_estimates",
                provider=self.provider.value,
                reason="FMP annual analyst-estimates request could not be constructed",
            ),))

        observations = []
        issues = []
        for row in _all_rows(response.body):
            try:
                period_start, period_end = self._annual_period(identity, row.get("date"))
                as_of_at, substituted = self._source_as_of(row, response.retrieved_at, source_as_of_at)
            except (TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric="annual_analyst_estimates",
                    provider=self.provider.value,
                    reason="FMP estimate row has missing or incompatible annual fiscal-period/as-of semantics",
                    action="skip the row rather than guess its annual period",
                ))
                continue

            base_transformations = ["constructed period_start from CompanyIdentity.fiscal_year_end"]
            if substituted:
                base_transformations.append("source as-of unavailable; used retrieval time explicitly")

            for metric_id, (unit, fields, count_field) in _ESTIMATE_FIELDS.items():
                raw_values: dict[EstimateCase, float] = {}
                for estimate_case, field_name in fields.items():
                    raw = row.get(field_name)
                    if raw is None:
                        continue
                    try:
                        value = float(raw)
                        if not math.isfinite(value):
                            raise ValueError("non-finite estimate")
                        raw_values[estimate_case] = value
                    except (TypeError, ValueError, OverflowError):
                        issues.append(DataIssue(
                            severity=IssueSeverity.ERROR,
                            metric=metric_id,
                            provider=self.provider.value,
                            reason=f"FMP {field_name} is not a finite numeric estimate",
                        ))

                low = raw_values.get(EstimateCase.LOW)
                average = raw_values.get(EstimateCase.AVERAGE)
                high = raw_values.get(EstimateCase.HIGH)
                invalid_order = (
                    (low is not None and average is not None and low > average)
                    or (average is not None and high is not None and average > high)
                    or (low is not None and high is not None and low > high)
                )
                if invalid_order:
                    issues.append(DataIssue(
                        severity=IssueSeverity.BLOCKING,
                        metric=metric_id,
                        provider=self.provider.value,
                        reason="FMP estimate cases violate low <= average <= high",
                        expected="low <= average <= high for available comparable cases",
                        action="withhold this metric and fiscal period; do not reorder or clamp provider values",
                    ))
                    continue

                analyst_count = None
                if count_field is not None and row.get(count_field) is not None:
                    try:
                        raw_count = row[count_field]
                        if isinstance(raw_count, bool):
                            raise ValueError("boolean analyst count")
                        numeric_count = float(raw_count)
                        if not math.isfinite(numeric_count) or not numeric_count.is_integer() or numeric_count < 0:
                            raise ValueError("negative analyst count")
                        analyst_count = int(numeric_count)
                    except (TypeError, ValueError, OverflowError):
                        issues.append(DataIssue(
                            severity=IssueSeverity.WARNING,
                            metric=metric_id,
                            provider=self.provider.value,
                            reason=f"FMP {count_field} is invalid; analyst_count remains unknown",
                        ))

                for estimate_case, value in raw_values.items():
                    field_name = fields[estimate_case]
                    observations.append(estimate_observation(
                        provider=self.provider.value,
                        dataset="fmp_annual_analyst_estimates",
                        provider_symbol=symbol,
                        source_metric=field_name,
                        metric_id=metric_id,
                        value=value,
                        unit=unit,
                        currency=currency,
                        estimate_case=estimate_case,
                        analyst_count=analyst_count,
                        retrieved_at=response.retrieved_at,
                        as_of_at=as_of_at,
                        period_start=period_start,
                        period_end=period_end,
                        fiscal_year=period_end.year,
                        transformations=tuple(base_transformations),
                    ))

        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "FMP returned no supported, valid annual analyst estimates"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderDataResult(tuple(observations), (capability,), tuple(issues))

    def fetch_price_targets(
        self,
        identity: CompanyIdentity,
        *,
        source_as_of_at: datetime | None = None,
    ) -> ProviderExternalReferenceResult:
        capability_name = ProviderCapability.ANALYST_PRICE_TARGETS.value
        symbol = self._provider_symbol(identity)
        currency = self._currency(identity, "quote_currency")
        if symbol is None or currency is None:
            reason = "identity has no explicit FMP symbol" if symbol is None else "quote currency is unavailable"
            capability = self.record_capability(capability_name, CapabilityStatus.UNAVAILABLE, reason=reason)
            return ProviderExternalReferenceResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="analyst_price_targets", provider=self.provider.value,
                reason=reason,
            ),))
        try:
            response = self.execute(self._request(ProviderCapability.ANALYST_PRICE_TARGETS, symbol))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderExternalReferenceResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="analyst_price_targets", provider=self.provider.value,
                reason="FMP analyst-price-target retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="FMP analyst-price-target request could not be constructed",
            )
            return ProviderExternalReferenceResult(capabilities=(capability,))

        row = _first_row(response.body)
        references = []
        issues = []
        if row is not None:
            try:
                as_of_at, substituted = self._source_as_of(
                    row, response.retrieved_at, source_as_of_at, provider_field="lastUpdated",
                )
            except (TypeError, ValueError, OverflowError):
                as_of_at, substituted = response.retrieved_at, True
                issues.append(DataIssue(
                    severity=IssueSeverity.WARNING, metric="analyst_price_targets", provider=self.provider.value,
                    reason="FMP price-target as-of timestamp is invalid; retrieval time was used explicitly",
                ))
            transformations = ("source as-of unavailable; used retrieval time explicitly",) if substituted else ()
            for source_metric, reference_type in _TARGET_FIELDS.items():
                if row.get(source_metric) is None:
                    continue
                try:
                    value = float(row[source_metric])
                    if not math.isfinite(value):
                        raise ValueError("non-finite target")
                    provenance = Provenance(
                        provider=self.provider.value,
                        endpoint_or_dataset="fmp_analyst_price_targets",
                        provider_symbol=symbol,
                        retrieved_at=response.retrieved_at,
                        as_of_at=as_of_at,
                        transformation_steps=transformations,
                        source_metric=source_metric,
                    )
                    references.append(ExternalValuationReference(
                        reference_type=reference_type,
                        value=value,
                        currency=currency,
                        provider=self.provider.value,
                        as_of_at=as_of_at,
                        provenance=provenance,
                    ))
                except (TypeError, ValueError, OverflowError):
                    issues.append(DataIssue(
                        severity=IssueSeverity.ERROR, metric=reference_type.value, provider=self.provider.value,
                        reason=f"FMP {source_metric} is not a valid finite external reference",
                    ))
        status = CapabilityStatus.AVAILABLE if references else CapabilityStatus.UNAVAILABLE
        reason = None if references else "FMP returned no supported, valid analyst price targets"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderExternalReferenceResult(tuple(references), (capability,), tuple(issues))

    def fetch_standard_dcf(
        self,
        identity: CompanyIdentity,
        *,
        source_as_of_at: datetime | None = None,
    ) -> ProviderExternalReferenceResult:
        capability_name = ProviderCapability.EXTERNAL_STANDARD_DCF.value
        symbol = self._provider_symbol(identity)
        currency = self._currency(identity, "quote_currency")
        if symbol is None or currency is None:
            reason = "identity has no explicit FMP symbol" if symbol is None else "quote currency is unavailable"
            capability = self.record_capability(capability_name, CapabilityStatus.UNAVAILABLE, reason=reason)
            return ProviderExternalReferenceResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="external_standard_dcf", provider=self.provider.value,
                reason=reason,
            ),))
        try:
            response = self.execute(self._request(ProviderCapability.EXTERNAL_STANDARD_DCF, symbol))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderExternalReferenceResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="external_standard_dcf", provider=self.provider.value,
                reason="FMP standard-DCF reference retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="FMP standard-DCF request could not be constructed",
            )
            return ProviderExternalReferenceResult(capabilities=(capability,))

        row = _first_row(response.body)
        references = []
        issues = []
        if row is not None and row.get("dcf") is not None:
            try:
                if source_as_of_at is not None:
                    as_of_at, substituted = aware_datetime(source_as_of_at)
                elif row.get("date") is not None:
                    as_of_at = datetime.combine(calendar_date(row["date"]), time.min, tzinfo=timezone.utc)
                    substituted = False
                else:
                    as_of_at, substituted = response.retrieved_at, True
                value = float(row["dcf"])
                if not math.isfinite(value):
                    raise ValueError("non-finite DCF")
                transformations = ("source as-of unavailable; used retrieval time explicitly",) if substituted else ()
                provenance = Provenance(
                    provider=self.provider.value,
                    endpoint_or_dataset="fmp_external_standard_dcf",
                    provider_symbol=symbol,
                    retrieved_at=response.retrieved_at,
                    as_of_at=as_of_at,
                    transformation_steps=transformations,
                    source_metric="dcf",
                )
                references.append(ExternalValuationReference(
                    reference_type=ExternalReferenceType.FMP_STANDARD_DCF,
                    value=value,
                    currency=currency,
                    provider=self.provider.value,
                    as_of_at=as_of_at,
                    provenance=provenance,
                ))
            except (TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric="external_standard_dcf", provider=self.provider.value,
                    reason="FMP standard-DCF value or as-of timestamp is invalid",
                ))
        status = CapabilityStatus.AVAILABLE if references else CapabilityStatus.UNAVAILABLE
        reason = None if references else "FMP returned no supported, valid standard-DCF reference"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderExternalReferenceResult(tuple(references), (capability,), tuple(issues))
