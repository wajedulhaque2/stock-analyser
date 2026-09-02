"""Read-only Yahoo adapter over an injected data source; no network client lives here."""

from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Mapping, Protocol, Sequence

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    AdjustedPriceObservation,
    DataIssue,
    Frequency,
    IssueSeverity,
    MetricId,
    MetricUnit,
    PriceReturnSemantics,
    Provenance,
    stable_adjusted_price_observation_id,
)

from ._normalization import actual_observation, aware_datetime, calendar_date
from .base import BaseProviderAdapter
from .contracts import (
    IdentityCandidate,
    ProviderCapability,
    ProviderDataResult,
    ProviderIdentityResult,
    ProviderPriceHistoryResult,
)
from .errors import ProviderError
from .identity import ProviderId


class YahooDataSource(Protocol):
    def identity_metadata(self, symbol: str) -> Mapping[str, Any]: ...
    def market_snapshot(self, symbol: str) -> Mapping[str, Any]: ...
    def adjusted_price_history(self, symbol: str, start: date, end: date) -> Sequence[Mapping[str, Any]]: ...


_MARKET_FIELDS = {
    "regularMarketPrice": (MetricId.SHARE_PRICE, MetricUnit.CURRENCY_PER_SHARE, True),
    "marketCap": (MetricId.MARKET_CAP, MetricUnit.CURRENCY, False),
    "sharesOutstanding": (MetricId.SHARES_OUTSTANDING, MetricUnit.SHARES, False),
    "beta": (MetricId.BETA, MetricUnit.RATIO, False),
    "regularMarketVolume": (MetricId.VOLUME, MetricUnit.SHARES, False),
}


class YahooAdapter(BaseProviderAdapter):
    provider = ProviderId.YAHOO

    def __init__(self, source: YahooDataSource, *, clock=None) -> None:
        super().__init__(clock=clock)
        self._source = source

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    def fetch_identity(self, provider_symbol: str) -> ProviderIdentityResult:
        retrieved = self._clock()
        try:
            row = self._source.identity_metadata(provider_symbol)
            quote_unit = row.get("quoteUnit") or row.get("currency")
            quote_currency = row.get("currency")
            explicit_scale = row.get("priceScale")
            if explicit_scale is not None:
                scale = float(explicit_scale)
            elif quote_unit in {"GBp", "GBX"}:
                scale, quote_currency = 0.01, "GBP"
            else:
                scale = 1.0
            candidate = IdentityCandidate(
                provider=self.provider,
                provider_symbol=provider_symbol,
                retrieved_at=retrieved,
                company_name=row.get("longName"),
                issuer_domicile=row.get("country"),
                listing_country=row.get("listingCountry") or row.get("country"),
                exchange=row.get("exchange"),
                sector=row.get("sector"),
                industry=row.get("industry"),
                security_type=row.get("quoteType"),
                reporting_currency=row.get("financialCurrency"),
                quote_currency=quote_currency,
                quote_unit=quote_unit,
                price_scale=scale,
                fiscal_year_end=row.get("fiscalYearEnd"),
                underlying_security_id=row.get("underlyingSecurityId"),
                adr_ratio=float(row["adrRatio"]) if row.get("adrRatio") is not None else None,
            )
            result = self.record_capability(ProviderCapability.IDENTITY.value, CapabilityStatus.AVAILABLE)
            return ProviderIdentityResult(candidate, result)
        except ProviderError as error:
            result = self.record_provider_error(ProviderCapability.IDENTITY.value, error)
            issue = DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="Yahoo identity metadata retrieval failed",
            )
            return ProviderIdentityResult(None, result, (issue,))
        except Exception:
            result = self.record_capability(
                ProviderCapability.IDENTITY.value, CapabilityStatus.ERROR,
                reason="Yahoo identity metadata could not be normalized",
            )
            issue = DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="Yahoo identity metadata could not be normalized",
                action="withhold the Yahoo identity candidate",
            )
            return ProviderIdentityResult(None, result, (issue,))

    def fetch_market_snapshot(self, identity: CompanyIdentity) -> ProviderDataResult:
        symbol = next((item.symbol for item in identity.provider_symbols if item.provider == self.provider.value), None)
        if symbol is None:
            capability = self.record_capability(
                ProviderCapability.MARKET_SNAPSHOT.value, CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Yahoo provider symbol",
            )
            return ProviderDataResult(capabilities=(capability,))
        retrieved = self._clock()
        try:
            row = self._source.market_snapshot(symbol)
        except ProviderError as error:
            capability = self.record_provider_error(ProviderCapability.MARKET_SNAPSHOT.value, error)
            issue = DataIssue(
                severity=IssueSeverity.ERROR, metric="market_snapshot", provider=self.provider.value,
                reason="Yahoo market snapshot retrieval failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(issue,))
        except Exception:
            capability = self.record_capability(
                ProviderCapability.MARKET_SNAPSHOT.value, CapabilityStatus.ERROR,
                reason="Yahoo market snapshot retrieval failed",
            )
            issue = DataIssue(
                severity=IssueSeverity.ERROR, metric="market_snapshot", provider=self.provider.value,
                reason="Yahoo market snapshot retrieval failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(issue,))

        try:
            as_of, substituted = aware_datetime(row.get("asOf"), fallback=retrieved)
        except (TypeError, ValueError):
            capability = self.record_capability(
                ProviderCapability.MARKET_SNAPSHOT.value, CapabilityStatus.ERROR,
                reason="Yahoo market snapshot has an invalid as-of timestamp",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="market_snapshot", provider=self.provider.value,
                reason="Yahoo market snapshot has an invalid as-of timestamp",
            ),))

        observations = []
        issues = []
        for source_field, (metric, unit, quote_scaled) in _MARKET_FIELDS.items():
            raw = row.get(source_field)
            if raw is None:
                issues.append(DataIssue(
                    severity=IssueSeverity.INFO, metric=metric, provider=self.provider.value,
                    reason=f"Yahoo market snapshot did not provide {source_field}",
                ))
                continue
            transformations = []
            value = float(raw)
            if quote_scaled:
                value = identity.normalize_quote_price(value)
                if identity.price_scale != 1:
                    transformations.append(f"applied explicit quote price scale {identity.price_scale:g}")
            if substituted:
                transformations.append("source as-of unavailable; used retrieval time explicitly")
            currency = identity.quote_currency if unit in {MetricUnit.CURRENCY, MetricUnit.CURRENCY_PER_SHARE} else None
            observations.append(actual_observation(
                provider=self.provider.value,
                dataset="yahoo_market_snapshot",
                provider_symbol=symbol,
                source_metric=source_field,
                metric_id=metric,
                value=value,
                unit=unit,
                currency=currency,
                frequency=Frequency.POINT_IN_TIME,
                retrieved_at=retrieved,
                as_of_at=as_of,
                period_start=None,
                period_end=as_of.date(),
                transformations=tuple(transformations),
            ))

        snapshot_status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        snapshot_reason = None if observations else "Yahoo market snapshot contained no supported values"
        snapshot = self.record_capability(ProviderCapability.MARKET_SNAPSHOT.value, snapshot_status, reason=snapshot_reason)
        price_available = any(item.metric_id is MetricId.SHARE_PRICE for item in observations)
        price = self.record_capability(
            ProviderCapability.CURRENT_PRICE.value,
            CapabilityStatus.AVAILABLE if price_available else CapabilityStatus.UNAVAILABLE,
            reason=None if price_available else "Yahoo current price was not available",
        )
        return ProviderDataResult(tuple(observations), (snapshot, price), tuple(issues))

    def fetch_adjusted_price_history(
        self,
        instrument_id: str,
        provider_symbol: str,
        *,
        start: date,
        end: date,
        expected_currency: str,
    ) -> ProviderPriceHistoryResult:
        """Normalize only Yahoo history explicitly marked split/distribution adjusted."""
        capability_name = ProviderCapability.PRICE_HISTORY.value
        if not isinstance(start, date) or not isinstance(end, date) or start >= end:
            raise ValueError("price-history start must be before end")
        currency = expected_currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("expected_currency must be a three-letter code")
        retrieved = self._clock()
        try:
            rows = self._source.adjusted_price_history(provider_symbol, start, end)
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR, reason="Yahoo adjusted price-history retrieval failed",
            )
            return ProviderPriceHistoryResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="adjusted_price_history", provider=self.provider.value,
                reason="Yahoo adjusted price-history retrieval failed",
            ),))
        observations = []
        issues = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                observation_date = calendar_date(row.get("date"))
                adjusted_close = float(row["adjustedClose"])
                if not math.isfinite(adjusted_close) or adjusted_close <= 0:
                    raise ValueError("invalid adjusted close")
                row_currency = str(row.get("currency") or "").strip().upper()
                if row_currency != currency:
                    raise ValueError("currency mismatch")
                if row.get("returnSemantics") != PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED.value:
                    raise ValueError("unverified return semantics")
                source_as_of = datetime.combine(observation_date, datetime.min.time(), tzinfo=timezone.utc)
                if row.get("asOf") is not None:
                    source_as_of, _ = aware_datetime(row.get("asOf"), fallback=source_as_of)
                provenance = Provenance(
                    provider=self.provider.value,
                    endpoint_or_dataset="yahoo_adjusted_price_history",
                    provider_symbol=provider_symbol,
                    retrieved_at=retrieved,
                    as_of_at=source_as_of,
                    transformation_steps=(
                        "retained Yahoo Close produced with auto_adjust=True, actions=False, repair=True",
                        "accepted only source rows explicitly marked split-and-distribution adjusted",
                    ),
                    source_metric="adjusted Close",
                )
                observations.append(AdjustedPriceObservation(
                    observation_id=stable_adjusted_price_observation_id(
                        instrument_id, provider_symbol, observation_date.isoformat(), adjusted_close,
                        PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED.value,
                    ),
                    instrument_id=instrument_id, provider_symbol=provider_symbol,
                    observation_date=observation_date, adjusted_close=adjusted_close,
                    currency=currency,
                    return_semantics=PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED,
                    retrieved_at=retrieved, as_of_at=source_as_of, provenance=provenance,
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric="adjusted_price_history",
                    provider=self.provider.value,
                    reason="Yahoo history row lacked compatible adjusted-return, date, currency, or price semantics",
                    action="skip the row; never substitute price-only data or forward-fill",
                ))
        observations.sort(key=lambda item: (item.observation_date, item.observation_id))
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        capability = self.record_capability(
            capability_name, status,
            reason=None if observations else "Yahoo returned no compatible adjusted price observations",
        )
        return ProviderPriceHistoryResult(tuple(observations), (capability,), tuple(issues))
