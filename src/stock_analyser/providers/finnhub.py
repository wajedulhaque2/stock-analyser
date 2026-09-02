"""Scoped Finnhub validator adapter with explicit cash-flow definition evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from typing import Any, Mapping

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    DataIssue,
    DefinitionVerificationStatus,
    EstimateCase,
    IssueSeverity,
    MetricId,
    MetricUnit,
)

from ._normalization import annual_period, aware_datetime, estimate_observation
from .base import BaseProviderAdapter
from .contracts import ProviderCapability, ProviderDataResult, SecretReference
from .errors import ProviderError, ProviderErrorCategory
from .identity import ProviderId
from .transport import RetryingTransport, TransportRequest


_FINNHUB_METRICS = {
    ProviderCapability.ANNUAL_REVENUE_ESTIMATES: ("revenue", MetricId.REVENUE, MetricUnit.CURRENCY),
    ProviderCapability.ANNUAL_EBIT_ESTIMATES: ("ebit", MetricId.EBIT, MetricUnit.CURRENCY),
    ProviderCapability.ANNUAL_EBITDA_ESTIMATES: ("ebitda", MetricId.EBITDA, MetricUnit.CURRENCY),
    ProviderCapability.ANNUAL_NET_INCOME_ESTIMATES: ("netIncome", MetricId.NET_INCOME, MetricUnit.CURRENCY),
    ProviderCapability.ANNUAL_EPS_ESTIMATES: ("eps", MetricId.EPS, MetricUnit.CURRENCY_PER_SHARE),
    ProviderCapability.ANNUAL_OCF_ESTIMATES: (
        "operatingCashFlow", MetricId.OPERATING_CASH_FLOW, MetricUnit.CURRENCY,
    ),
    ProviderCapability.ANNUAL_CAPEX_ESTIMATES: (
        "capitalExpenditure", MetricId.CAPITAL_EXPENDITURE, MetricUnit.CURRENCY,
    ),
    ProviderCapability.ANNUAL_FCF_ESTIMATES: (
        "freeCashFlow", MetricId.PROVIDER_DEFINED_FCF, MetricUnit.CURRENCY,
    ),
}

_DOCUMENTED_ENDPOINTS = {
    ProviderCapability.ANNUAL_REVENUE_ESTIMATES: "stock/revenue-estimate",
    ProviderCapability.ANNUAL_EBIT_ESTIMATES: "stock/ebit-estimate",
    ProviderCapability.ANNUAL_EBITDA_ESTIMATES: "stock/ebitda-estimate",
    ProviderCapability.ANNUAL_NET_INCOME_ESTIMATES: "stock/net-income-estimate",
    ProviderCapability.ANNUAL_EPS_ESTIMATES: "stock/eps-estimate",
}

_CASE_FIELDS = {
    EstimateCase.LOW: "low",
    EstimateCase.AVERAGE: "average",
    EstimateCase.HIGH: "high",
}


def _rows(body: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(body, Mapping):
        data = body.get("data")
        if isinstance(data, (list, tuple)):
            return tuple(item for item in data if isinstance(item, Mapping))
        return (body,)
    if isinstance(body, (list, tuple)):
        return tuple(item for item in body if isinstance(item, Mapping))
    return ()


class FinnhubAdapter(BaseProviderAdapter):
    provider = ProviderId.FINNHUB

    def __init__(
        self,
        transport: RetryingTransport,
        *,
        credential: SecretReference,
        cash_flow_definitions: tuple[CashFlowDefinitionEvidence, ...] = (),
        endpoint_overrides: Mapping[ProviderCapability, str] | None = None,
        base_url: str = "https://finnhub.io/api/v1",
        clock=None,
    ) -> None:
        super().__init__(transport, clock=clock)
        self._credential = credential
        self._base_url = base_url.rstrip("/")
        self._cash_flow_definitions = {
            item.provider_metric: item for item in cash_flow_definitions if item.provider.lower() == self.provider.value
        }
        self._endpoint_paths = {**_DOCUMENTED_ENDPOINTS, **dict(endpoint_overrides or {})}

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    @staticmethod
    def _provider_symbol(identity: CompanyIdentity) -> str | None:
        return next(
            (item.symbol for item in identity.provider_symbols if item.provider == ProviderId.FINNHUB.value),
            None,
        )

    def _request(
        self, symbol: str, capability: ProviderCapability, provider_metric: str,
    ) -> TransportRequest:
        path = self._endpoint_paths[capability]
        parameters = {"symbol": symbol, "freq": "annual", "token": self._credential.resolve()}
        if path.rstrip("/") == "stock/estimates":
            parameters["metric"] = provider_metric
        return TransportRequest(
            provider=self.provider,
            endpoint_id=f"finnhub_{capability.value}",
            method="GET",
            url=f"{self._base_url}/{path.lstrip('/')}",
            parameters=parameters,
            provider_symbol=symbol,
        )

    def _fcf_evidence(self) -> CashFlowDefinitionEvidence:
        return self._cash_flow_definitions.get("freeCashFlow") or CashFlowDefinitionEvidence(
            provider=self.provider.value,
            provider_metric="freeCashFlow",
            endpoint_or_dataset="finnhub_annual_estimates",
            definition=CashFlowDefinition.PROVIDER_DEFINED,
            verification_status=DefinitionVerificationStatus.UNVERIFIED,
            definition_reference="adapter-contract:finnhub-generic-fcf",
            notes="Generic provider FCF is not economically classified as FCFF or FCFE.",
        )

    @staticmethod
    def _fcf_metric(evidence: CashFlowDefinitionEvidence) -> MetricId:
        if evidence.is_verified_fcff:
            return MetricId.FCFF
        if evidence.is_verified_fcfe:
            return MetricId.FCFE
        if (
            evidence.definition is CashFlowDefinition.OCF_LESS_CAPEX
            and evidence.verification_status is DefinitionVerificationStatus.VERIFIED
        ):
            return MetricId.OCF_LESS_CAPEX
        return MetricId.PROVIDER_DEFINED_FCF

    def fetch_annual_estimates(
        self,
        identity: CompanyIdentity,
        capability: ProviderCapability,
        *,
        source_as_of_at: datetime | None = None,
    ) -> ProviderDataResult:
        if capability not in _FINNHUB_METRICS:
            raise ValueError("unsupported Finnhub annual estimate capability")
        provider_metric, canonical_metric, unit = _FINNHUB_METRICS[capability]
        if capability not in self._endpoint_paths:
            result = self.record_capability(
                capability.value,
                CapabilityStatus.UNAVAILABLE,
                reason="No approved documented Finnhub endpoint is configured for this estimate capability",
            )
            return ProviderDataResult(capabilities=(result,))
        try:
            snapshot_as_of = aware_datetime(source_as_of_at)[0] if source_as_of_at is not None else None
        except (TypeError, ValueError, OverflowError):
            result = self.record_capability(
                capability.value, CapabilityStatus.ERROR, reason="Finnhub source as-of timestamp is invalid",
            )
            return ProviderDataResult(capabilities=(result,))
        symbol = self._provider_symbol(identity)
        currency = getattr(identity, "reporting_currency", None)
        if symbol is None or not isinstance(currency, str):
            reason = "identity has no explicit Finnhub symbol" if symbol is None else "reporting currency is unavailable"
            result = self.record_capability(capability.value, CapabilityStatus.UNAVAILABLE, reason=reason)
            return ProviderDataResult(capabilities=(result,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value, reason=reason,
            ),))
        try:
            response = self.execute(self._request(symbol, capability, provider_metric))
        except ProviderError as error:
            if (
                error.status_code == 403
                and error.category is ProviderErrorCategory.UNKNOWN
                and self._endpoint_paths.get(capability) == _DOCUMENTED_ENDPOINTS.get(capability)
            ):
                error = ProviderError(
                    provider=self.provider,
                    endpoint_id=f"finnhub_{capability.value}",
                    category=ProviderErrorCategory.ENTITLEMENT,
                    status_code=403,
                    retryable=False,
                    safe_message="documented Finnhub premium estimate endpoint is not entitled",
                    attempts=error.attempts,
                )
            result = self.record_provider_error(capability.value, error)
            return ProviderDataResult(capabilities=(result,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                reason="Finnhub annual estimate retrieval failed",
            ),))
        except Exception:
            result = self.record_capability(
                capability.value, CapabilityStatus.ERROR, reason="Finnhub estimate request could not be constructed",
            )
            return ProviderDataResult(capabilities=(result,))

        evidence = self._fcf_evidence() if capability is ProviderCapability.ANNUAL_FCF_ESTIMATES else None
        if evidence is not None:
            canonical_metric = self._fcf_metric(evidence)
        observations = []
        issues = []
        for row in _rows(response.body):
            try:
                period_start, period_end = annual_period(
                    row.get("periodEnd") or row.get("period"), identity.fiscal_year_end,
                )
                as_of_at, substituted = aware_datetime(row.get("asOf"), fallback=snapshot_as_of or response.retrieved_at)
            except (TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                    reason="Finnhub estimate row has invalid annual period or as-of semantics",
                ))
                continue

            values = {}
            source_fields_by_case = dict(_CASE_FIELDS)
            for estimate_case, field_name in _CASE_FIELDS.items():
                provider_field = f"{provider_metric}{'Avg' if estimate_case is EstimateCase.AVERAGE else field_name.title()}"
                selected_field = field_name if row.get(field_name) is not None else provider_field
                source_fields_by_case[estimate_case] = selected_field
                if row.get(selected_field) is None:
                    continue
                try:
                    value = float(row[selected_field])
                    if not math.isfinite(value):
                        raise ValueError("non-finite")
                    values[estimate_case] = value
                except (TypeError, ValueError, OverflowError):
                    issues.append(DataIssue(
                        severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                        reason=f"Finnhub {selected_field} estimate is not finite",
                    ))
            low, average, high = (
                values.get(EstimateCase.LOW), values.get(EstimateCase.AVERAGE), values.get(EstimateCase.HIGH),
            )
            if (
                (low is not None and average is not None and low > average)
                or (average is not None and high is not None and average > high)
                or (low is not None and high is not None and low > high)
            ):
                issues.append(DataIssue(
                    severity=IssueSeverity.BLOCKING, metric=canonical_metric, provider=self.provider.value,
                    reason="Finnhub estimate cases violate low <= average <= high",
                ))
                continue

            analyst_count = None
            raw_analyst_count = row.get("analystCount", row.get("numberAnalysts"))
            if raw_analyst_count is not None:
                try:
                    raw_count = raw_analyst_count
                    numeric = float(raw_count)
                    if isinstance(raw_count, bool) or not numeric.is_integer() or numeric < 0:
                        raise ValueError("invalid count")
                    analyst_count = int(numeric)
                except (TypeError, ValueError, OverflowError):
                    issues.append(DataIssue(
                        severity=IssueSeverity.WARNING, metric=canonical_metric, provider=self.provider.value,
                        reason="Finnhub metric-specific analyst count is invalid and remains unknown",
                    ))

            transformations = ["constructed period_start from CompanyIdentity.fiscal_year_end"]
            if substituted:
                transformations.append("source as-of unavailable; used supplied snapshot or retrieval time explicitly")
            if capability is ProviderCapability.ANNUAL_CAPEX_ESTIMATES:
                sign = row.get("capexSignConvention")
                if sign not in {"negative_outflow", "positive_use_of_cash"}:
                    issues.append(DataIssue(
                        severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                        reason="Finnhub CapEx sign convention is unknown",
                        action="skip CapEx rather than apply absolute value",
                    ))
                    continue
                if sign == "negative_outflow":
                    if any(value > 0 for value in values.values()):
                        issues.append(DataIssue(
                            severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                            reason="Finnhub negative-outflow CapEx field contains a positive estimate",
                        ))
                        continue
                    inverted_cases = {
                        EstimateCase.LOW: EstimateCase.HIGH,
                        EstimateCase.AVERAGE: EstimateCase.AVERAGE,
                        EstimateCase.HIGH: EstimateCase.LOW,
                    }
                    values = {
                        inverted_cases[source_case]: -value for source_case, value in values.items()
                    }
                    source_fields_by_case = {
                        inverted_cases[source_case]: field_name
                        for source_case, field_name in _CASE_FIELDS.items()
                    }
                    transformations.append(
                        "converted explicit negative CapEx outflow to positive use-of-cash magnitude and inverted range endpoints"
                    )
                elif any(value < 0 for value in values.values()):
                    issues.append(DataIssue(
                        severity=IssueSeverity.ERROR, metric=canonical_metric, provider=self.provider.value,
                        reason="Finnhub positive-use-of-cash CapEx field contains a negative estimate",
                    ))
                    continue

            for estimate_case, value in values.items():
                observations.append(estimate_observation(
                    provider=self.provider.value,
                    dataset="finnhub_annual_estimates",
                    provider_symbol=symbol,
                    source_metric=f"{provider_metric}:{source_fields_by_case[estimate_case]}",
                    metric_id=canonical_metric,
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
                    transformations=tuple(transformations),
                ))

        definitions = ()
        if evidence is not None:
            definitions = (replace(
                evidence,
                observation_ids=tuple(
                    item.observation_id for item in observations if item.metric_id is canonical_metric
                ),
            ),)
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "Finnhub returned no supported, valid estimates for this capability"
        result = self.record_capability(capability.value, status, reason=reason)
        return ProviderDataResult(tuple(observations), (result,), tuple(issues), definitions)
