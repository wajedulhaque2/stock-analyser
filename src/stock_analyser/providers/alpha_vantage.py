"""Scoped Alpha Vantage revenue/EPS estimate and revision validator adapter."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import math
from typing import Any, Mapping

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataIssue,
    EstimateCase,
    EstimateRevision,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricUnit,
    Provenance,
)

from ._normalization import annual_period, aware_datetime, calendar_date, estimate_observation
from .base import BaseProviderAdapter
from .contracts import ProviderCapability, ProviderRevisionResult, SecretReference
from .errors import ProviderError
from .identity import ProviderId
from .transport import RetryingTransport, TransportRequest


_LEVEL_FIELDS = {
    MetricId.REVENUE: (
        MetricUnit.CURRENCY,
        {EstimateCase.LOW: "revenueLow", EstimateCase.AVERAGE: "revenueAverage", EstimateCase.HIGH: "revenueHigh"},
        "revenueAnalystCount",
    ),
    MetricId.EPS: (
        MetricUnit.CURRENCY_PER_SHARE,
        {EstimateCase.LOW: "epsLow", EstimateCase.AVERAGE: "epsAverage", EstimateCase.HIGH: "epsHigh"},
        "epsAnalystCount",
    ),
}

_REVISION_FIELDS = {
    MetricId.REVENUE: ("revenuePriorEstimate", "revenueUpRevisions30Days", "revenueDownRevisions30Days"),
    MetricId.EPS: ("epsPriorEstimate", "epsUpRevisions30Days", "epsDownRevisions30Days"),
}


def _section(body: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    rows = body.get(name)
    if not isinstance(rows, (list, tuple)):
        return ()
    return tuple(item for item in rows if isinstance(item, Mapping))


_LIVE_FIELD_MAP = {
    "revenueLow": "revenue_estimate_low",
    "revenueAverage": "revenue_estimate_average",
    "revenueHigh": "revenue_estimate_high",
    "revenueAnalystCount": "revenue_estimate_analyst_count",
    "epsLow": "eps_estimate_low",
    "epsAverage": "eps_estimate_average",
    "epsHigh": "eps_estimate_high",
    "epsAnalystCount": "eps_estimate_analyst_count",
    "epsPriorEstimate": "eps_estimate_average_30_days_ago",
    "epsUpRevisions30Days": "eps_estimate_revision_up_trailing_30_days",
    "epsDownRevisions30Days": "eps_estimate_revision_down_trailing_30_days",
}


def _estimate_sections(
    body: Mapping[str, Any],
) -> tuple[tuple[Frequency, tuple[Mapping[str, Any], ...]], ...]:
    """Accept the approved synthetic split form and Alpha's live unified form."""
    live_rows = body.get("estimates")
    if not isinstance(live_rows, (list, tuple)):
        return (
            (Frequency.ANNUAL, _section(body, "annualEstimates")),
            (Frequency.QUARTERLY, _section(body, "quarterlyEstimates")),
        )

    grouped: dict[Frequency, list[Mapping[str, Any]]] = {
        Frequency.ANNUAL: [],
        Frequency.QUARTERLY: [],
    }
    for raw in live_rows:
        if not isinstance(raw, Mapping):
            continue
        horizon = str(raw.get("horizon") or "").strip().lower()
        frequency = {
            "fiscal year": Frequency.ANNUAL,
            "fiscal quarter": Frequency.QUARTERLY,
        }.get(horizon)
        if frequency is None:
            continue
        row: dict[str, Any] = {
            "periodEnd": raw.get("date"),
            "_live_unified_schema": True,
            "_source_fields": {},
        }
        for normalized, source in _LIVE_FIELD_MAP.items():
            if source in raw:
                row[normalized] = raw.get(source)
                row["_source_fields"][normalized] = source
        grouped[frequency].append(row)
    return (
        (Frequency.ANNUAL, tuple(grouped[Frequency.ANNUAL])),
        (Frequency.QUARTERLY, tuple(grouped[Frequency.QUARTERLY])),
    )


def _shift_months(value: date, months: int) -> date:
    ordinal = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(ordinal, 12)
    month = month_zero + 1
    source_is_month_end = value.day == calendar.monthrange(value.year, value.month)[1]
    day = calendar.monthrange(year, month)[1] if source_is_month_end else min(
        value.day, calendar.monthrange(year, month)[1],
    )
    return date(year, month, day)


def _derived_quarter_period(
    raw_period_end: Any, fiscal_year_end: str | None,
) -> tuple[date, date, int, int]:
    period_end = calendar_date(raw_period_end)
    if not isinstance(fiscal_year_end, str):
        raise ValueError("fiscal-year-end metadata is unavailable")
    month, day = (int(part) for part in fiscal_year_end.split("-"))
    for fiscal_year in range(period_end.year - 1, period_end.year + 2):
        fiscal_end = date(fiscal_year, month, min(day, calendar.monthrange(fiscal_year, month)[1]))
        for offset, fiscal_quarter in ((0, 4), (-3, 3), (-6, 2), (-9, 1)):
            expected_end = _shift_months(fiscal_end, offset)
            if expected_end == period_end:
                return _shift_months(period_end, -3) + timedelta(days=1), period_end, fiscal_year, fiscal_quarter
    raise ValueError("provider quarter end does not align with canonical fiscal-year-end metadata")


def _optional_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < 0:
        return None
    return int(numeric)


class AlphaVantageAdapter(BaseProviderAdapter):
    provider = ProviderId.ALPHA_VANTAGE

    def __init__(
        self,
        transport: RetryingTransport,
        *,
        credential: SecretReference,
        base_url: str = "https://www.alphavantage.co/query",
        clock=None,
    ) -> None:
        super().__init__(transport, clock=clock)
        self._credential = credential
        self._base_url = base_url

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    @staticmethod
    def _provider_symbol(identity: CompanyIdentity) -> str | None:
        return next(
            (item.symbol for item in identity.provider_symbols if item.provider == ProviderId.ALPHA_VANTAGE.value),
            None,
        )

    def _request(self, symbol: str) -> TransportRequest:
        return TransportRequest(
            provider=self.provider,
            endpoint_id="alpha_earnings_estimates",
            method="GET",
            url=self._base_url,
            parameters={
                "function": "EARNINGS_ESTIMATES",
                "symbol": symbol,
                "apikey": self._credential.resolve(),
            },
            provider_symbol=symbol,
        )

    @staticmethod
    def _period(
        row: Mapping[str, Any], frequency: Frequency, identity: CompanyIdentity,
    ) -> tuple[date, date, int, int | None]:
        if frequency is Frequency.ANNUAL:
            start, end = annual_period(row.get("periodEnd"), identity.fiscal_year_end)
            return start, end, end.year, None
        if row.get("_live_unified_schema"):
            return _derived_quarter_period(row.get("periodEnd"), identity.fiscal_year_end)
        start = calendar_date(row.get("periodStart"))
        end = calendar_date(row.get("periodEnd"))
        fiscal_year = int(row["fiscalYear"])
        fiscal_quarter = int(row["fiscalQuarter"])
        if fiscal_quarter not in {1, 2, 3, 4} or start > end:
            raise ValueError("invalid quarterly period")
        return start, end, fiscal_year, fiscal_quarter

    def fetch_earnings_estimates(
        self,
        identity: CompanyIdentity,
        *,
        source_as_of_at: datetime | None = None,
    ) -> ProviderRevisionResult:
        try:
            snapshot_as_of = aware_datetime(source_as_of_at)[0] if source_as_of_at is not None else None
        except (TypeError, ValueError, OverflowError):
            reason = "Alpha Vantage source as-of timestamp is invalid"
            capabilities = (
                self.record_capability(ProviderCapability.EARNINGS_ESTIMATES.value, CapabilityStatus.ERROR, reason=reason),
                self.record_capability(ProviderCapability.ESTIMATE_REVISIONS.value, CapabilityStatus.ERROR, reason=reason),
            )
            return ProviderRevisionResult(capabilities=capabilities)
        symbol = self._provider_symbol(identity)
        currency = getattr(identity, "reporting_currency", None)
        if symbol is None or not isinstance(currency, str):
            reason = "identity has no explicit Alpha Vantage symbol" if symbol is None else "reporting currency is unavailable"
            capabilities = (
                self.record_capability(ProviderCapability.EARNINGS_ESTIMATES.value, CapabilityStatus.UNAVAILABLE, reason=reason),
                self.record_capability(ProviderCapability.ESTIMATE_REVISIONS.value, CapabilityStatus.UNAVAILABLE, reason=reason),
            )
            return ProviderRevisionResult(capabilities=capabilities, issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="earnings_estimates", provider=self.provider.value, reason=reason,
            ),))
        try:
            response = self.execute(self._request(symbol))
        except ProviderError as error:
            capabilities = (
                self.record_provider_error(ProviderCapability.EARNINGS_ESTIMATES.value, error),
                self.record_provider_error(ProviderCapability.ESTIMATE_REVISIONS.value, error),
            )
            return ProviderRevisionResult(capabilities=capabilities, issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="earnings_estimates", provider=self.provider.value,
                reason="Alpha Vantage earnings-estimates retrieval failed",
            ),))
        except Exception:
            reason = "Alpha Vantage earnings-estimates request could not be constructed"
            capabilities = (
                self.record_capability(ProviderCapability.EARNINGS_ESTIMATES.value, CapabilityStatus.ERROR, reason=reason),
                self.record_capability(ProviderCapability.ESTIMATE_REVISIONS.value, CapabilityStatus.ERROR, reason=reason),
            )
            return ProviderRevisionResult(capabilities=capabilities)

        if not isinstance(response.body, Mapping):
            reason = "Alpha Vantage response is not a supported normalized mapping"
            capabilities = (
                self.record_capability(ProviderCapability.EARNINGS_ESTIMATES.value, CapabilityStatus.UNAVAILABLE, reason=reason),
                self.record_capability(ProviderCapability.ESTIMATE_REVISIONS.value, CapabilityStatus.UNAVAILABLE, reason=reason),
            )
            return ProviderRevisionResult(capabilities=capabilities)

        observations = []
        revisions = []
        issues = []
        sections = _estimate_sections(response.body)
        for frequency, rows in sections:
            for row in rows:
                try:
                    period_start, period_end, fiscal_year, fiscal_quarter = self._period(row, frequency, identity)
                    as_of_at, substituted = aware_datetime(
                        row.get("asOf"), fallback=snapshot_as_of or response.retrieved_at,
                    )
                except (KeyError, TypeError, ValueError, OverflowError):
                    issues.append(DataIssue(
                        severity=IssueSeverity.ERROR, metric="earnings_estimates", provider=self.provider.value,
                        reason="Alpha Vantage row has invalid period or as-of semantics",
                    ))
                    continue
                transformations = []
                if frequency is Frequency.ANNUAL:
                    transformations.append("constructed period_start from CompanyIdentity.fiscal_year_end")
                elif row.get("_live_unified_schema"):
                    transformations.append(
                        "constructed quarterly fiscal period from date and CompanyIdentity.fiscal_year_end"
                    )
                if row.get("_live_unified_schema"):
                    transformations.append("mapped Alpha Vantage unified snake_case estimates collection")
                if substituted:
                    transformations.append("source as-of unavailable; used supplied snapshot or retrieval time explicitly")

                for metric_id, (unit, fields, count_field) in _LEVEL_FIELDS.items():
                    values = {}
                    for estimate_case, field_name in fields.items():
                        if row.get(field_name) is None:
                            continue
                        try:
                            value = float(row[field_name])
                            if not math.isfinite(value):
                                raise ValueError("non-finite")
                            values[estimate_case] = value
                        except (TypeError, ValueError, OverflowError):
                            issues.append(DataIssue(
                                severity=IssueSeverity.ERROR, metric=metric_id, provider=self.provider.value,
                                reason=f"Alpha Vantage {field_name} estimate is not finite",
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
                            severity=IssueSeverity.BLOCKING, metric=metric_id, provider=self.provider.value,
                            reason="Alpha Vantage estimate cases violate low <= average <= high",
                        ))
                        continue

                    raw_count = row.get(count_field)
                    analyst_count = _optional_count(raw_count)
                    if raw_count is not None and analyst_count is None:
                        issues.append(DataIssue(
                            severity=IssueSeverity.WARNING, metric=metric_id, provider=self.provider.value,
                            reason=f"Alpha Vantage {count_field} is invalid and remains unknown",
                        ))
                    for estimate_case, value in values.items():
                        source_fields = row.get("_source_fields") or {}
                        source_metric = source_fields.get(fields[estimate_case], fields[estimate_case])
                        observations.append(estimate_observation(
                            provider=self.provider.value,
                            dataset="alpha_earnings_estimates",
                            provider_symbol=symbol,
                            source_metric=source_metric,
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
                            fiscal_year=fiscal_year,
                            frequency=frequency,
                            fiscal_quarter=fiscal_quarter,
                            transformations=tuple(transformations),
                        ))

                    prior_field, up_field, down_field = _REVISION_FIELDS[metric_id]
                    if not any(row.get(name) is not None for name in (prior_field, up_field, down_field)):
                        continue
                    prior_estimate = None
                    if row.get(prior_field) is not None:
                        try:
                            prior_estimate = float(row[prior_field])
                            if not math.isfinite(prior_estimate):
                                raise ValueError("non-finite")
                        except (TypeError, ValueError, OverflowError):
                            prior_estimate = None
                            issues.append(DataIssue(
                                severity=IssueSeverity.WARNING, metric=metric_id, provider=self.provider.value,
                                reason=f"Alpha Vantage {prior_field} is invalid and remains unknown",
                            ))
                    up_revisions = _optional_count(row.get(up_field))
                    down_revisions = _optional_count(row.get(down_field))
                    for raw_name, normalized_count in ((up_field, up_revisions), (down_field, down_revisions)):
                        if row.get(raw_name) is not None and normalized_count is None:
                            issues.append(DataIssue(
                                severity=IssueSeverity.WARNING, metric=metric_id, provider=self.provider.value,
                                reason=f"Alpha Vantage {raw_name} is invalid and remains unknown",
                            ))
                    current_estimate = values.get(EstimateCase.AVERAGE)
                    if all(value is None for value in (current_estimate, prior_estimate, up_revisions, down_revisions)):
                        continue
                    revision_provenance = Provenance(
                        provider=self.provider.value,
                        endpoint_or_dataset="alpha_estimate_revisions",
                        provider_symbol=symbol,
                        retrieved_at=response.retrieved_at,
                        as_of_at=as_of_at,
                        transformation_steps=tuple(transformations),
                        source_metric=f"{metric_id.value}:revision_30_days",
                    )
                    revisions.append(EstimateRevision(
                        metric_id=metric_id,
                        frequency=frequency,
                        period_start=period_start,
                        period_end=period_end,
                        fiscal_year=fiscal_year,
                        fiscal_quarter=fiscal_quarter,
                        revision_window="30_days",
                        current_estimate=current_estimate,
                        prior_estimate=prior_estimate,
                        up_revisions=up_revisions,
                        down_revisions=down_revisions,
                        analyst_count=analyst_count,
                        unit=unit,
                        currency=currency,
                        provider=self.provider.value,
                        retrieved_at=response.retrieved_at,
                        as_of_at=as_of_at,
                        provenance=revision_provenance,
                    ))

        estimate_status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        revision_status = CapabilityStatus.AVAILABLE if revisions else CapabilityStatus.UNAVAILABLE
        capabilities = (
            self.record_capability(
                ProviderCapability.EARNINGS_ESTIMATES.value, estimate_status,
                reason=None if observations else "Alpha Vantage returned no supported estimate levels",
            ),
            self.record_capability(
                ProviderCapability.ESTIMATE_REVISIONS.value, revision_status,
                reason=None if revisions else "Alpha Vantage returned no supported revision evidence",
            ),
        )
        return ProviderRevisionResult(tuple(observations), tuple(revisions), capabilities, tuple(issues))
