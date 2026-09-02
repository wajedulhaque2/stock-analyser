"""Read-only SEC Company Facts adapter for bounded reported actual tags."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataIssue,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricUnit,
)

from ._normalization import actual_observation, aware_datetime, calendar_date
from .base import BaseProviderAdapter
from .contracts import IdentityCandidate, ProviderCapability, ProviderDataResult, ProviderIdentityResult
from .identity import ProviderId
from .errors import ProviderError


class SecActualSource(Protocol):
    def identity_metadata(self, symbol: str) -> Mapping[str, Any]: ...
    def reported_actuals(self, cik: str) -> Iterable[Mapping[str, Any]]: ...


_REPORTED_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": (MetricId.REVENUE, MetricUnit.CURRENCY, "flow"),
    "Revenues": (MetricId.REVENUE, MetricUnit.CURRENCY, "flow"),
    "SalesRevenueNet": (MetricId.REVENUE, MetricUnit.CURRENCY, "flow"),
    "OperatingIncomeLoss": (MetricId.OPERATING_INCOME, MetricUnit.CURRENCY, "flow"),
    "NetIncomeLoss": (MetricId.NET_INCOME_COMMON, MetricUnit.CURRENCY, "flow"),
    "ProfitLoss": (MetricId.NET_INCOME_COMMON, MetricUnit.CURRENCY, "flow"),
    "NetCashProvidedByUsedInOperatingActivities": (MetricId.OPERATING_CASH_FLOW, MetricUnit.CURRENCY, "flow"),
    "PaymentsToAcquirePropertyPlantAndEquipment": (MetricId.CAPITAL_EXPENDITURE, MetricUnit.CURRENCY, "flow"),
    "PaymentsForAdditionsToPropertyPlantAndEquipment": (MetricId.CAPITAL_EXPENDITURE, MetricUnit.CURRENCY, "flow"),
    "CashAndCashEquivalentsAtCarryingValue": (MetricId.CASH_AND_EQUIVALENTS, MetricUnit.CURRENCY, "instant"),
    "EntityCommonStockSharesOutstanding": (MetricId.SHARES_OUTSTANDING, MetricUnit.SHARES, "instant"),
}


class SecAdapter(BaseProviderAdapter):
    provider = ProviderId.SEC

    def __init__(self, source: SecActualSource, *, clock=None) -> None:
        super().__init__(clock=clock)
        self._source = source

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    def fetch_identity(self, provider_symbol: str) -> ProviderIdentityResult:
        retrieved = self._clock()
        try:
            row = self._source.identity_metadata(provider_symbol)
            cik = str(row["cik"]).strip()
            if not cik:
                raise ValueError("empty CIK")
            candidate = IdentityCandidate(
                provider=self.provider,
                provider_symbol=cik,
                retrieved_at=retrieved,
                company_name=row.get("companyName"),
                issuer_domicile=row.get("issuerDomicile"),
                provider_issuer_id=cik,
            )
            capability = self.record_capability(ProviderCapability.IDENTITY.value, CapabilityStatus.AVAILABLE)
            return ProviderIdentityResult(candidate, capability)
        except ProviderError as error:
            capability = self.record_provider_error(ProviderCapability.IDENTITY.value, error)
            return ProviderIdentityResult(None, capability, (DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="SEC ticker-to-CIK identity lookup failed",
            ),))
        except Exception:
            capability = self.record_capability(
                ProviderCapability.IDENTITY.value, CapabilityStatus.ERROR,
                reason="SEC ticker-to-CIK identity lookup failed",
            )
            return ProviderIdentityResult(None, capability, (DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="SEC ticker-to-CIK identity lookup failed",
            ),))

    def fetch_reported_actuals(self, identity: CompanyIdentity) -> ProviderDataResult:
        cik = next((item.symbol for item in identity.provider_symbols if item.provider == self.provider.value), None)
        capability_name = ProviderCapability.REPORTED_ACTUALS.value
        if cik is None:
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit SEC CIK provider identifier",
            )
            return ProviderDataResult(capabilities=(capability,))
        retrieved = self._clock()
        try:
            rows = tuple(self._source.reported_actuals(cik))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="reported_actuals", provider=self.provider.value,
                reason="SEC reported actuals retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="SEC reported actuals retrieval failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="reported_actuals", provider=self.provider.value,
                reason="SEC reported actuals retrieval failed",
            ),))

        observations = []
        issues = []
        for row in rows:
            tag = row.get("tag")
            mapping = _REPORTED_TAGS.get(tag)
            if mapping is None:
                continue
            metric, unit, period_kind = mapping
            try:
                as_of, _ = aware_datetime(row.get("filedAt"))
                period_end = calendar_date(row.get("periodEnd"))
                if period_kind == "instant":
                    frequency = Frequency.POINT_IN_TIME
                    period_start = None
                    fiscal_year = int(row["fiscalYear"]) if row.get("fiscalYear") is not None else None
                    fiscal_quarter = None
                else:
                    frequency_text = str(row.get("frequency", "")).lower()
                    frequency = {"annual": Frequency.ANNUAL, "quarterly": Frequency.QUARTERLY}[frequency_text]
                    period_start = calendar_date(row.get("periodStart"))
                    fiscal_year = int(row["fiscalYear"])
                    fiscal_quarter = int(row["fiscalQuarter"]) if frequency is Frequency.QUARTERLY else None
                source_metric = f"{row.get('taxonomy', 'us-gaap')}:{tag}"
                currency = row.get("currency") if unit is MetricUnit.CURRENCY else None
                observations.append(actual_observation(
                    provider=self.provider.value,
                    dataset="sec_companyfacts_reported_actuals",
                    provider_symbol=cik,
                    source_metric=source_metric,
                    metric_id=metric,
                    value=float(row["value"]),
                    unit=unit,
                    currency=currency,
                    frequency=frequency,
                    retrieved_at=retrieved,
                    as_of_at=as_of,
                    period_start=period_start,
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric=metric, provider=self.provider.value,
                    reason=f"SEC row for {tag} has malformed or ambiguous filing/period semantics",
                    action="skip the row rather than infer a filing date, period, or fiscal label",
                ))

        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "SEC returned no supported, valid reported actuals"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderDataResult(tuple(observations), (capability,), tuple(issues))
