"""Read-only Fiscal adapter for bounded standardized historical actuals."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping, Protocol

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataIssue,
    Frequency,
    HistoricalMultipleType,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    IssueSeverity,
    MetricId,
    MetricObservation,
    MetricUnit,
    PeerIdentityEvidence,
    PeerIdentityEvidenceStatus,
    Provenance,
    PeerCandidateSource,
    ValuationBasis,
    stable_historical_valuation_id,
)

from ._normalization import actual_observation, aware_datetime, calendar_date
from .base import BaseProviderAdapter
from .contracts import (
    IdentityCandidate,
    ProviderCapability,
    ProviderDataResult,
    ProviderIdentityResult,
    ProviderHistoricalValuationResult,
    ProviderLookupStatus,
    ProviderPeerCandidate,
    ProviderPeerDiscoveryResult,
    ProviderPeerFinancialResolution,
    SecretReference,
)
from .identity import ProviderId
from .errors import ProviderError


class FiscalActualSource(Protocol):
    def identity_metadata(self, symbol: str, *, credential: str | None = None) -> Mapping[str, Any]: ...
    def standardized_financials(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...
    def historical_valuation_ratios(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...
    def enterprise_bridge_metrics(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...
    def wacc_accounting_metrics(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...
    def peer_candidates(
        self, symbol: str, *, credential: str | None = None,
    ) -> Mapping[str, Any]: ...
    def company_lookup_records(
        self, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...
    def standardized_financials_by_company_key(
        self, company_key: str, *, provider_symbol: str, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]: ...


_STANDARDIZED_FIELDS = {
    "revenue": (MetricId.REVENUE, MetricUnit.CURRENCY, "direct"),
    "gross_profit": (MetricId.GROSS_PROFIT, MetricUnit.CURRENCY, "direct"),
    "operating_income": (MetricId.OPERATING_INCOME, MetricUnit.CURRENCY, "direct"),
    "ebit": (MetricId.EBIT, MetricUnit.CURRENCY, "direct"),
    "interest_expense": (MetricId.INTEREST_EXPENSE, MetricUnit.CURRENCY, "expense_magnitude"),
    "ebitda": (MetricId.EBITDA, MetricUnit.CURRENCY, "direct"),
    "net_income_common": (MetricId.NET_INCOME_COMMON, MetricUnit.CURRENCY, "direct"),
    "operating_cash_flow": (MetricId.OPERATING_CASH_FLOW, MetricUnit.CURRENCY, "direct"),
    "capital_expenditure_cash_outflow": (MetricId.CAPITAL_EXPENDITURE, MetricUnit.CURRENCY, "negative_outflow"),
    "depreciation_amortization": (MetricId.DEPRECIATION_AMORTIZATION, MetricUnit.CURRENCY, "direct"),
    "cash_and_equivalents": (MetricId.CASH_AND_EQUIVALENTS, MetricUnit.CURRENCY, "direct"),
    "gross_debt": (MetricId.GROSS_DEBT, MetricUnit.CURRENCY, "direct"),
    "shares_basic": (MetricId.SHARES_BASIC, MetricUnit.SHARES, "direct"),
    "shares_diluted": (MetricId.SHARES_DILUTED, MetricUnit.SHARES, "direct"),
    "shares_outstanding": (MetricId.SHARES_OUTSTANDING, MetricUnit.SHARES, "direct"),
    "free_cash_flow": (MetricId.PROVIDER_DEFINED_FCF, MetricUnit.CURRENCY, "provider_defined"),
}


_HISTORICAL_RATIO_FIELDS = {
    "ratio_price_to_earnings": (
        HistoricalMultipleType.P_E,
        ValuationBasis.EQUITY,
        HistoricalValuationDenominator.DILUTED_EPS,
        "Fiscal-defined share price divided by diluted EPS; no forward or normalized label inferred",
    ),
    "ratio_ev_to_ebitda": (
        HistoricalMultipleType.EV_EBITDA,
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.EBITDA,
        "Fiscal-defined total enterprise value divided by EBITDA",
    ),
    "ratio_ev_to_ebit": (
        HistoricalMultipleType.EV_EBIT,
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
        "Fiscal-defined EV/EBIT ratio uses Operating Profit as its documented denominator",
    ),
}
_WITHHELD_CASH_FLOW_RATIOS = {
    "ratio_price_to_free_cash_flow",
    "ratio_ev_to_fcf",
    "ratio_price_to_fcf",
}

def build_fiscal_summary_peer_identity(candidate: ProviderPeerCandidate) -> PeerIdentityEvidence:
    """Seed identity from stable Fiscal IDs and listing, independently of security class."""
    if candidate.provider is not ProviderId.FISCAL:
        raise ValueError("summary peer identity requires a Fiscal candidate")
    required = {
        "provider_symbol": candidate.provider_symbol,
        "provider_issuer_id": candidate.provider_issuer_id,
        "provider_security_id": candidate.provider_security_id,
        "exchange": candidate.exchange,
    }
    missing = tuple(name for name, value in required.items() if not isinstance(value, str) or not value.strip())
    if missing:
        raise ValueError(f"Fiscal peer summary lacks required identity evidence: {', '.join(missing)}")
    transformations = [
        "seeded canonical peer issuer/security from stable Fiscal company/security identifiers",
        "retained peer-summary listing and classification as bounded provider evidence",
        "kept security classification separate from canonical identity verification",
        "full profile remains optional identity enrichment",
    ]
    if candidate.source_as_of_substituted:
        transformations.append("source as-of unavailable; used retrieval time explicitly")
    provenance = Provenance(
        provider=candidate.provider.value,
        endpoint_or_dataset="fiscal_v3_company_profile_peers",
        provider_symbol=candidate.provider_symbol,
        retrieved_at=candidate.retrieved_at,
        as_of_at=candidate.source_as_of,
        transformation_steps=tuple(transformations),
        source_metric="peers",
    )
    return PeerIdentityEvidence(
        status=PeerIdentityEvidenceStatus.SUMMARY_VERIFIED,
        canonical_symbol=candidate.provider_symbol,
        security_id=f"security:fiscal:{candidate.provider_security_id}",
        issuer_id=f"issuer:fiscal:{candidate.provider_issuer_id}",
        provider=candidate.provider.value,
        provider_symbol=candidate.provider_symbol,
        listing_country=candidate.listing_country,
        exchange=str(candidate.exchange),
        security_type=candidate.security_type,
        source_as_of=candidate.source_as_of,
        retrieved_at=candidate.retrieved_at,
        provenance=provenance,
        company_name=candidate.company_name,
        sector=candidate.sector,
        industry=candidate.industry,
        unavailable_enrichment_fields=tuple((
            "issuer_domicile", "reporting_currency", "quote_currency",
            "quote_unit", "price_scale", "fiscal_year_end",
            *(("listing_country",) if candidate.listing_country is None else ()),
        )),
    )


def _lookup_token(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _listing_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _lookup_token(value).upper())


def resolve_fiscal_peer_financial_lookup(
    candidate: ProviderPeerCandidate,
    company_records: Iterable[Mapping[str, Any]],
) -> ProviderPeerFinancialResolution:
    """Resolve companyKey only from same-record stable Fiscal identifier evidence."""
    if candidate.provider is not ProviderId.FISCAL:
        raise ValueError("peer financial lookup requires a Fiscal candidate")
    records = tuple(row for row in company_records if isinstance(row, Mapping))
    matches = tuple(
        row for row in records
        if _lookup_token(row.get("companyFiscalIdentifier")) == candidate.provider_issuer_id
    )
    base = dict(
        provider=candidate.provider,
        provider_symbol=candidate.provider_symbol,
        provider_issuer_id=candidate.provider_issuer_id,
        provider_security_id=candidate.provider_security_id,
    )
    if len(matches) > 1:
        return ProviderPeerFinancialResolution(
            **base,
            status=ProviderLookupStatus.AMBIGUOUS,
            source_dataset="fiscal_v3_companies_list",
            reason="multiple Fiscal company records claim the stable company identifier",
        )
    if matches:
        row = matches[0]
        company_key = _lookup_token(row.get("companyKey"))
        if not company_key:
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.UNAVAILABLE,
                source_dataset="fiscal_v3_companies_list",
                reason="matching Fiscal company record has no companyKey",
            )
        direct_key = _lookup_token(candidate.provider_company_key)
        if direct_key and direct_key != company_key:
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.AMBIGUOUS,
                source_dataset="fiscal_v3_companies_list",
                reason="Fiscal peer-summary and company-list companyKey evidence conflicts",
            )
        listing = row.get("primaryListing") or {}
        record_security_id = _lookup_token(listing.get("securityFiscalIdentifier"))
        record_symbol = _listing_token(listing.get("ticker") or row.get("ticker"))
        record_exchange = _listing_token(
            listing.get("exchangeCode") or listing.get("operatingMic") or listing.get("micCode")
        )
        if record_security_id and record_security_id != candidate.provider_security_id:
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.AMBIGUOUS,
                source_dataset="fiscal_v3_companies_list",
                reason="Fiscal company-list security identifier conflicts with peer identity",
            )
        if record_symbol and record_symbol != _listing_token(candidate.provider_symbol):
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.AMBIGUOUS,
                source_dataset="fiscal_v3_companies_list",
                reason="Fiscal company-list primary listing conflicts with peer identity",
            )
        if record_exchange and candidate.exchange and record_exchange != _listing_token(candidate.exchange):
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.AMBIGUOUS,
                source_dataset="fiscal_v3_companies_list",
                reason="Fiscal company-list exchange conflicts with peer identity",
            )
        return ProviderPeerFinancialResolution(
            **base,
            status=ProviderLookupStatus.AVAILABLE,
            source_dataset="fiscal_v3_companies_list",
            provider_company_key=company_key,
        )
    direct_key = _lookup_token(candidate.provider_company_key)
    if direct_key:
        conflicting_key_claims = tuple(
            row for row in records
            if _lookup_token(row.get("companyKey")) == direct_key
            and _lookup_token(row.get("companyFiscalIdentifier")) != candidate.provider_issuer_id
        )
        if conflicting_key_claims:
            return ProviderPeerFinancialResolution(
                **base,
                status=ProviderLookupStatus.AMBIGUOUS,
                source_dataset="fiscal_v3_companies_list",
                reason="Fiscal companyKey is claimed by a different stable company identifier",
            )
        return ProviderPeerFinancialResolution(
            **base,
            status=ProviderLookupStatus.AVAILABLE,
            source_dataset="fiscal_v3_company_profile_peers",
            provider_company_key=direct_key,
        )
    return ProviderPeerFinancialResolution(
        **base,
        status=ProviderLookupStatus.UNAVAILABLE,
        reason="no same-record stable-identifier evidence resolves a Fiscal companyKey",
    )

_ENTERPRISE_BRIDGE_FIELDS = {
    "calculated_tev": (
        MetricId.ENTERPRISE_VALUE,
        MetricUnit.CURRENCY,
        "Fiscal-defined TEV equals market cap plus net debt plus preferred stock plus minority interests",
    ),
    "calculated_market_cap": (
        MetricId.MARKET_CAP,
        MetricUnit.CURRENCY,
        "Fiscal-defined market cap equals share price times total shares outstanding",
    ),
    "calculated_total_debt": (
        MetricId.GROSS_DEBT,
        MetricUnit.CURRENCY,
        "Fiscal-defined total debt includes short-term debt, current and long-term debt, and leases",
    ),
    "calculated_net_debt": (
        MetricId.NET_DEBT,
        MetricUnit.CURRENCY,
        "Fiscal-defined net debt equals total debt minus total cash and cash equivalents",
    ),
    "market_data_total_shares_outstanding": (
        MetricId.SHARES_OUTSTANDING,
        MetricUnit.SHARES,
        "Fiscal total shares outstanding preserves documented ADS-converted values for ADR companies",
    ),
}

_WACC_ACCOUNTING_FIELDS = {
    "calculated_total_debt": (
        MetricId.GROSS_DEBT,
        MetricUnit.CURRENCY,
        Frequency.POINT_IN_TIME,
        "Fiscal Total Debt equals short-term debt plus current portions of long-term debt and leases plus long-term debt and leases",
    ),
    "calculated_net_debt": (
        MetricId.NET_DEBT,
        MetricUnit.CURRENCY,
        Frequency.POINT_IN_TIME,
        "Fiscal Net Debt equals Total Debt minus Total Cash and Cash Equivalents",
    ),
    "ratio_ebit_to_interest_expense": (
        MetricId.INTEREST_COVERAGE,
        MetricUnit.RATIO,
        Frequency.ANNUAL,
        "Fiscal EBIT-to-interest-expense ratio is documented as Operating Profit divided by Interest Expense; retained as method evidence, not generic EBIT",
    ),
}


class FiscalAdapter(BaseProviderAdapter):
    provider = ProviderId.FISCAL

    def __init__(self, source: FiscalActualSource, *, credential: SecretReference | None = None, clock=None) -> None:
        super().__init__(clock=clock)
        self._source = source
        self._credential = credential

    def _secret(self) -> str | None:
        return self._credential.resolve() if self._credential is not None else None

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    def fetch_identity(self, provider_symbol: str) -> ProviderIdentityResult:
        retrieved = self._clock()
        try:
            row = self._source.identity_metadata(provider_symbol, credential=self._secret())
            resolved_provider_symbol = row.get("provider_symbol") or provider_symbol
            candidate = IdentityCandidate(
                provider=self.provider,
                provider_symbol=str(resolved_provider_symbol),
                retrieved_at=retrieved,
                company_name=row.get("company_name"),
                issuer_domicile=row.get("issuer_domicile"),
                listing_country=row.get("listing_country"),
                exchange=row.get("exchange"),
                sector=row.get("sector"),
                industry=row.get("industry"),
                security_type=row.get("security_type"),
                reporting_currency=row.get("reporting_currency"),
                quote_currency=row.get("quote_currency"),
                quote_unit=row.get("quote_unit"),
                price_scale=float(row["price_scale"]) if row.get("price_scale") is not None else None,
                fiscal_year_end=row.get("fiscal_year_end"),
                underlying_security_id=row.get("underlying_security_id"),
                adr_ratio=float(row["adr_ratio"]) if row.get("adr_ratio") is not None else None,
                provider_issuer_id=row.get("provider_issuer_id"),
                provider_security_id=row.get("provider_security_id"),
                company_type=row.get("company_type"),
            )
            capability = self.record_capability(ProviderCapability.IDENTITY.value, CapabilityStatus.AVAILABLE)
            return ProviderIdentityResult(candidate, capability)
        except ProviderError as error:
            capability = self.record_provider_error(ProviderCapability.IDENTITY.value, error)
            return ProviderIdentityResult(None, capability, (DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="Fiscal identity metadata retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                ProviderCapability.IDENTITY.value, CapabilityStatus.ERROR,
                reason="Fiscal identity metadata retrieval or normalization failed",
            )
            return ProviderIdentityResult(None, capability, (DataIssue(
                severity=IssueSeverity.ERROR, metric="identity", provider=self.provider.value,
                reason="Fiscal identity metadata retrieval or normalization failed",
            ),))

    @staticmethod
    def _peer_source_as_of(value: Any, retrieved_at: datetime) -> tuple[datetime, bool]:
        try:
            if isinstance(value, bool):
                raise ValueError("boolean timestamp")
            if isinstance(value, (int, float)):
                parsed = datetime.fromtimestamp(float(value), timezone.utc)
                return parsed, False
            return aware_datetime(value, fallback=retrieved_at)
        except (TypeError, ValueError, OverflowError, OSError):
            return retrieved_at, True

    def fetch_peer_candidates(self, identity: CompanyIdentity) -> ProviderPeerDiscoveryResult:
        """Normalize documented Fiscal v3 profile peers as candidates, never comparables."""
        capability_name = ProviderCapability.PEER_CANDIDATES.value
        symbol = next(
            (item.symbol for item in identity.provider_symbols if item.provider == self.provider.value),
            None,
        )
        if symbol is None:
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Fiscal provider symbol",
            )
            return ProviderPeerDiscoveryResult(capabilities=(capability,))
        retrieved = self._clock()
        try:
            payload = self._source.peer_candidates(symbol, credential=self._secret())
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderPeerDiscoveryResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="peer_candidates",
                provider=self.provider.value,
                reason="Fiscal profile peer-candidate retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="Fiscal profile peer-candidate retrieval or normalization failed",
            )
            return ProviderPeerDiscoveryResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="peer_candidates",
                provider=self.provider.value,
                reason="Fiscal profile peer-candidate retrieval or normalization failed",
            ),))

        rows = payload.get("peers") if isinstance(payload, Mapping) else None
        source_as_of_raw = payload.get("updatedAt") if isinstance(payload, Mapping) else None
        source_as_of, substituted = self._peer_source_as_of(source_as_of_raw, retrieved)
        candidates: list[ProviderPeerCandidate] = []
        issues: list[DataIssue] = []
        for rank, row in enumerate(rows or (), start=1):
            if not isinstance(row, Mapping):
                continue
            listing = row.get("primaryListing") or {}
            provider_symbol = listing.get("ticker")
            provider_issuer_id = row.get("companyFiscalIdentifier")
            provider_security_id = listing.get("securityFiscalIdentifier")
            if not all(isinstance(value, str) and value.strip() for value in (
                provider_symbol, provider_issuer_id, provider_security_id,
            )):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric="peer_candidates.identity",
                    provider=self.provider.value,
                    reason="Fiscal peer row lacks a ticker or stable company/security identifier",
                    action="skip the row rather than join by company name or incomplete ticker evidence",
                ))
                continue
            candidates.append(ProviderPeerCandidate(
                provider=self.provider,
                provider_symbol=str(provider_symbol),
                provider_issuer_id=str(provider_issuer_id),
                provider_security_id=str(provider_security_id),
                candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
                source_rank=rank,
                source_as_of=source_as_of,
                retrieved_at=retrieved,
                source_as_of_substituted=substituted,
                company_name=row.get("displayNameEnglish"),
                listing_country=(
                    listing.get("exchangeCountryCode")
                    or row.get("headquartersCountryCode")
                ),
                exchange=(
                    listing.get("exchangeCode")
                    or listing.get("operatingMic")
                ),
                sector=row.get("sector"),
                industry=row.get("industry") or row.get("subIndustry"),
                security_type=listing.get("securityType"),
                provider_relationship=row.get("peerRelationship"),
                provider_company_key=row.get("companyKey"),
            ))
        status = CapabilityStatus.AVAILABLE if candidates else CapabilityStatus.UNAVAILABLE
        reason = None if candidates else "Fiscal profile returned no identity-complete peer candidates"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderPeerDiscoveryResult(tuple(candidates), (capability,), tuple(issues))

    @staticmethod
    def build_summary_peer_identity(candidate: ProviderPeerCandidate) -> PeerIdentityEvidence:
        return build_fiscal_summary_peer_identity(candidate)

    def resolve_peer_financial_lookup(
        self,
        candidate: ProviderPeerCandidate,
    ) -> ProviderPeerFinancialResolution:
        """Resolve Fiscal financial access without a ticker or company-name join."""
        try:
            rows = tuple(self._source.company_lookup_records(credential=self._secret()))
        except Exception:
            return ProviderPeerFinancialResolution(
                provider=candidate.provider,
                provider_symbol=candidate.provider_symbol,
                provider_issuer_id=candidate.provider_issuer_id,
                provider_security_id=candidate.provider_security_id,
                status=ProviderLookupStatus.UNAVAILABLE,
                reason="Fiscal stable-identifier company lookup was unavailable",
            )
        return resolve_fiscal_peer_financial_lookup(candidate, rows)

    def fetch_peer_standardized_actuals(
        self,
        identity: CompanyIdentity | PeerIdentityEvidence,
        resolution: ProviderPeerFinancialResolution,
    ) -> ProviderDataResult:
        """Fetch a verified peer by opaque Fiscal companyKey, preserving canonical identity."""
        capability_name = ProviderCapability.HISTORICAL_STANDARDIZED_FINANCIALS.value
        expected_issuer = f"issuer:fiscal:{resolution.provider_issuer_id}"
        if (
            resolution.provider is not self.provider
            or resolution.status is not ProviderLookupStatus.AVAILABLE
            or not resolution.provider_company_key
            or identity.issuer_id != expected_issuer
        ):
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.UNAVAILABLE,
                reason="peer financial lookup is unavailable or conflicts with canonical issuer identity",
            )
            return ProviderDataResult(capabilities=(capability,))
        retrieved = self._clock()
        try:
            rows = tuple(self._source.standardized_financials_by_company_key(
                resolution.provider_company_key,
                provider_symbol=resolution.provider_symbol,
                credential=self._secret(),
            ))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="standardized_actuals",
                provider=self.provider.value,
                reason="Fiscal peer standardized actuals retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.ERROR,
                reason="Fiscal peer standardized actuals retrieval failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="standardized_actuals",
                provider=self.provider.value,
                reason="Fiscal peer standardized actuals retrieval failed",
            ),))
        return self._normalize_standardized_rows(
            rows,
            symbol=resolution.provider_symbol,
            retrieved=retrieved,
        )

    def fetch_standardized_actuals(
        self, identity: CompanyIdentity, *, analysis_as_of: datetime | None = None,
    ) -> ProviderDataResult:
        symbol = next((item.symbol for item in identity.provider_symbols if item.provider == self.provider.value), None)
        capability_name = ProviderCapability.HISTORICAL_STANDARDIZED_FINANCIALS.value
        if symbol is None:
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Fiscal provider symbol",
            )
            return ProviderDataResult(capabilities=(capability,))
        if analysis_as_of is not None and (
            not isinstance(analysis_as_of, datetime)
            or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None
        ):
            raise ValueError("analysis_as_of must be a timezone-aware datetime")
        retrieved = self._clock()
        try:
            rows = tuple(self._source.standardized_financials(symbol, credential=self._secret()))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="standardized_actuals", provider=self.provider.value,
                reason="Fiscal standardized actuals retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="Fiscal standardized actuals retrieval failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="standardized_actuals", provider=self.provider.value,
                reason="Fiscal standardized actuals retrieval failed",
            ),))

        return self._normalize_standardized_rows(
            rows, symbol=symbol, retrieved=retrieved, analysis_as_of=analysis_as_of,
        )

    def _normalize_standardized_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        symbol: str,
        retrieved: datetime,
        analysis_as_of: datetime | None = None,
    ) -> ProviderDataResult:
        """Single canonical normalizer for target and stable-ID peer actuals."""
        capability_name = ProviderCapability.HISTORICAL_STANDARDIZED_FINANCIALS.value
        observations = []
        issues = []
        for row in rows:
            normalized_metric = row.get("metric")
            source_metric = row.get("sourceMetric") or normalized_metric
            mapping = _STANDARDIZED_FIELDS.get(normalized_metric)
            if mapping is None:
                continue
            metric, unit, sign_policy = mapping
            try:
                frequency_text = str(row.get("periodType", "")).lower()
                frequency = {"annual": Frequency.ANNUAL, "quarterly": Frequency.QUARTERLY}[frequency_text]
                period_start = calendar_date(row.get("periodStart"))
                period_end = calendar_date(row.get("periodEnd"))
                fiscal_year = int(row["fiscalYear"])
                fiscal_quarter = int(row["fiscalQuarter"]) if frequency is Frequency.QUARTERLY else None
                fallback_as_of = analysis_as_of if analysis_as_of is not None else retrieved
                as_of, substituted = aware_datetime(row.get("asOf"), fallback=fallback_as_of)
                value = float(row["value"])
                transformations = []
                if sign_policy == "negative_outflow":
                    sign_convention = row.get("signConvention", "negative_outflow")
                    if sign_convention == "negative_outflow":
                        if value > 0:
                            raise ValueError("capital expenditure outflow sign was not negative")
                        value = -value
                        transformations.append("converted provider negative cash outflow to positive capital-expenditure magnitude")
                    elif sign_convention == "positive_use_of_cash":
                        if value < 0:
                            raise ValueError("capital expenditure use-of-cash magnitude was negative")
                        transformations.append("retained explicit provider positive capital-expenditure use-of-cash magnitude")
                    else:
                        raise ValueError("capital expenditure sign convention was unavailable")
                elif sign_policy == "provider_defined":
                    transformations.append("retained as provider-defined FCF; not classified as FCFF or FCFE")
                elif sign_policy == "expense_magnitude":
                    sign_convention = row.get("signConvention")
                    if sign_convention == "negative_expense":
                        if value >= 0:
                            raise ValueError("negative expense convention requires a negative value")
                        value = -value
                        transformations.append("converted explicitly signed negative interest expense to positive expense magnitude")
                    elif sign_convention == "positive_expense_magnitude":
                        if value <= 0:
                            raise ValueError("interest-expense magnitude must be positive")
                        transformations.append("retained explicit positive interest-expense magnitude")
                    else:
                        raise ValueError("interest-expense sign convention was unavailable")
                if substituted:
                    transformations.append(
                        "source as-of unavailable; used analysis cutoff explicitly"
                        if analysis_as_of is not None
                        else "source as-of unavailable; used retrieval time explicitly"
                    )
                currency = row.get("currency") if unit is MetricUnit.CURRENCY else None
                observations.append(actual_observation(
                    provider=self.provider.value,
                    dataset="fiscal_standardized_financials",
                    provider_symbol=symbol,
                    source_metric=str(source_metric),
                    metric_id=metric,
                    value=value,
                    unit=unit,
                    currency=currency,
                    frequency=frequency,
                    retrieved_at=retrieved,
                    as_of_at=as_of,
                    period_start=period_start,
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    transformations=tuple(transformations),
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric=metric, provider=self.provider.value,
                    reason=f"Fiscal row for {normalized_metric} has malformed or ambiguous period/value semantics",
                    action="skip the row rather than infer missing semantics",
                ))

        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "Fiscal returned no supported, valid standardized actuals"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderDataResult(tuple(observations), (capability,), tuple(issues))

    def fetch_historical_valuation(
        self,
        identity: CompanyIdentity,
        *,
        analysis_as_of: datetime,
    ) -> ProviderHistoricalValuationResult:
        """Normalize supported Fiscal ratios without calculating distributions or value."""
        symbol = next((item.symbol for item in identity.provider_symbols if item.provider == self.provider.value), None)
        capability_name = ProviderCapability.HISTORICAL_VALUATION_RATIOS.value
        if symbol is None:
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Fiscal provider symbol",
            )
            return ProviderHistoricalValuationResult(capabilities=(capability,))
        if not isinstance(analysis_as_of, datetime) or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
            raise ValueError("analysis_as_of must be a timezone-aware datetime")
        retrieved = self._clock()
        try:
            rows = tuple(self._source.historical_valuation_ratios(symbol, credential=self._secret()))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderHistoricalValuationResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="historical_valuation",
                provider=self.provider.value,
                reason="Fiscal historical valuation retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.ERROR,
                reason="Fiscal historical valuation retrieval failed",
            )
            return ProviderHistoricalValuationResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="historical_valuation",
                provider=self.provider.value,
                reason="Fiscal historical valuation retrieval failed",
            ),))

        observations: list[HistoricalValuationObservation] = []
        issues: list[DataIssue] = []
        future_rows = 0
        withheld_cash_flow: set[str] = set()
        for row in rows:
            source_metric_raw = row.get("sourceMetric")
            if not isinstance(source_metric_raw, str) or not source_metric_raw.strip():
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric="historical_valuation",
                    provider=self.provider.value,
                    reason="Fiscal historical valuation row has no safe source metric",
                    action="skip the row rather than infer its economic definition",
                ))
                continue
            source_metric = source_metric_raw.strip()
            if source_metric in _WITHHELD_CASH_FLOW_RATIOS:
                withheld_cash_flow.add(source_metric)
                continue
            mapping = _HISTORICAL_RATIO_FIELDS.get(source_metric)
            if mapping is None:
                issues.append(DataIssue(
                    severity=IssueSeverity.WARNING,
                    metric="historical_valuation",
                    provider=self.provider.value,
                    reason="Fiscal ratio has no approved canonical economic definition",
                    observed=source_metric,
                    action="withhold the ratio until its basis and denominator are verified",
                ))
                continue
            multiple_type, basis, denominator, definition_step = mapping
            try:
                observation_date = calendar_date(row.get("observationDate"))
                if observation_date > analysis_as_of.date():
                    future_rows += 1
                    continue
                period_end = calendar_date(row["periodEnd"]) if row.get("periodEnd") is not None else None
                sampling = HistoricalValuationSampling(str(row.get("samplingBasis", "")))
                as_of, substituted = aware_datetime(row.get("asOf"), fallback=retrieved)
                value = float(row["value"])
                eligibility = (
                    HistoricalValuationEligibility.ELIGIBLE
                    if value > 0
                    else HistoricalValuationEligibility.INELIGIBLE
                )
                reason = None if value > 0 else "non-positive provider multiple is not meaningful for a future valuation distribution"
                transformations = [definition_step, "ratio retained without FX conversion or outlier treatment"]
                if substituted:
                    transformations.append("provider snapshot as-of unavailable; used retrieval time explicitly")
                provenance = Provenance(
                    provider=self.provider.value,
                    endpoint_or_dataset="fiscal_daily_historical_ratios",
                    provider_symbol=symbol,
                    retrieved_at=retrieved,
                    as_of_at=as_of,
                    transformation_steps=tuple(transformations),
                    source_metric=source_metric,
                )
                observation_id = stable_historical_valuation_id(
                    security_id=identity.security_id,
                    issuer_id=identity.issuer_id,
                    provider=self.provider.value,
                    provider_symbol=symbol,
                    multiple_type=multiple_type,
                    observation_date=observation_date,
                    period_end=period_end,
                    sampling=sampling,
                    source_metric=source_metric,
                )
                observations.append(HistoricalValuationObservation(
                    observation_id=observation_id,
                    security_id=identity.security_id,
                    issuer_id=identity.issuer_id,
                    provider=self.provider.value,
                    provider_symbol=symbol,
                    multiple_type=multiple_type,
                    valuation_basis=basis,
                    denominator=denominator,
                    value=value,
                    observation_date=observation_date,
                    period_end=period_end,
                    sampling=sampling,
                    as_of_at=as_of,
                    retrieved_at=retrieved,
                    provenance=provenance,
                    source_metric=source_metric,
                    eligibility=eligibility,
                    eligibility_reason=reason,
                    quote_currency_context=identity.quote_currency,
                    reporting_currency_context=identity.reporting_currency,
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric="historical_valuation",
                    provider=self.provider.value,
                    reason=f"Fiscal row for {source_metric} has malformed or ambiguous date/value semantics",
                    action="skip the row rather than fabricate historical valuation evidence",
                ))

        for source_metric in sorted(withheld_cash_flow):
            issues.append(DataIssue(
                severity=IssueSeverity.WARNING,
                metric="historical_valuation",
                provider=self.provider.value,
                reason="Fiscal cash-flow multiple was withheld because its FCF economics are unverified",
                observed=source_metric,
                action="verify the provider cash-flow definition before adding a canonical multiple type",
            ))
        if future_rows:
            issues.append(DataIssue(
                severity=IssueSeverity.INFO,
                metric="historical_valuation",
                provider=self.provider.value,
                reason=f"excluded {future_rows} observations after the explicit analysis as-of date",
            ))
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "Fiscal returned no supported historical valuation observations"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderHistoricalValuationResult(tuple(observations), (capability,), tuple(issues))

    def fetch_enterprise_bridge(
        self,
        identity: CompanyIdentity,
        *,
        analysis_as_of: datetime,
    ) -> ProviderDataResult:
        """Normalize only explicitly documented Fiscal enterprise-bridge metrics."""
        symbol = next((
            item.symbol for item in identity.provider_symbols if item.provider == self.provider.value
        ), None)
        capability_name = ProviderCapability.ENTERPRISE_EQUITY_BRIDGE.value
        if symbol is None:
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Fiscal provider symbol",
            )
            return ProviderDataResult(capabilities=(capability,))
        if (
            not isinstance(analysis_as_of, datetime)
            or analysis_as_of.tzinfo is None
            or analysis_as_of.utcoffset() is None
        ):
            raise ValueError("analysis_as_of must be a timezone-aware datetime")
        retrieved = self._clock()
        try:
            rows = tuple(self._source.enterprise_bridge_metrics(symbol, credential=self._secret()))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="enterprise_equity_bridge",
                provider=self.provider.value,
                reason="Fiscal enterprise-bridge retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name,
                CapabilityStatus.ERROR,
                reason="Fiscal enterprise-bridge retrieval or normalization failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR,
                metric="enterprise_equity_bridge",
                provider=self.provider.value,
                reason="Fiscal enterprise-bridge retrieval or normalization failed",
            ),))

        observations: list[MetricObservation] = []
        issues: list[DataIssue] = []
        future_count = 0
        for row in rows:
            source_metric_raw = row.get("sourceMetric")
            if not isinstance(source_metric_raw, str):
                continue
            source_metric = source_metric_raw.strip()
            mapping = _ENTERPRISE_BRIDGE_FIELDS.get(source_metric)
            if mapping is None:
                continue
            metric, unit, definition = mapping
            try:
                observation_date = calendar_date(row.get("observationDate"))
                if observation_date > analysis_as_of.date():
                    future_count += 1
                    continue
                as_of, substituted = aware_datetime(row.get("asOf"), fallback=retrieved)
                value = float(row["value"])
                transformations = [definition, "retained without FX conversion"]
                if substituted:
                    transformations.append("source as-of unavailable; used retrieval time explicitly")
                currency = row.get("currency") if unit is MetricUnit.CURRENCY else None
                observations.append(actual_observation(
                    provider=self.provider.value,
                    dataset="fiscal_enterprise_bridge_metrics",
                    provider_symbol=symbol,
                    source_metric=source_metric,
                    metric_id=metric,
                    value=value,
                    unit=unit,
                    currency=currency,
                    frequency=Frequency.POINT_IN_TIME,
                    retrieved_at=retrieved,
                    as_of_at=as_of,
                    period_start=None,
                    period_end=observation_date,
                    transformations=tuple(transformations),
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR,
                    metric=metric,
                    provider=self.provider.value,
                    reason=f"Fiscal row for {source_metric} has malformed or ambiguous date/value semantics",
                    action="skip the row rather than infer bridge evidence",
                ))
        if future_count:
            issues.append(DataIssue(
                severity=IssueSeverity.INFO,
                metric="enterprise_equity_bridge",
                provider=self.provider.value,
                reason=f"excluded {future_count} observations after analysis_as_of",
            ))
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "Fiscal returned no supported enterprise-bridge observations"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderDataResult(tuple(observations), (capability,), tuple(issues))

    def fetch_wacc_accounting_evidence(
        self,
        identity: CompanyIdentity,
        *,
        analysis_as_of: datetime,
    ) -> ProviderDataResult:
        """Normalize only documented period-based Fiscal WACC accounting metrics."""
        symbol = next((
            item.symbol for item in identity.provider_symbols if item.provider == self.provider.value
        ), None)
        capability_name = ProviderCapability.WACC_ACCOUNTING_METRICS.value
        if symbol is None:
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="identity has no explicit Fiscal provider symbol",
            )
            return ProviderDataResult(capabilities=(capability,))
        if not isinstance(analysis_as_of, datetime) or analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
            raise ValueError("analysis_as_of must be a timezone-aware datetime")
        retrieved = self._clock()
        try:
            rows = tuple(self._source.wacc_accounting_metrics(symbol, credential=self._secret()))
        except ProviderError as error:
            capability = self.record_provider_error(capability_name, error)
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="wacc_accounting", provider=self.provider.value,
                reason="Fiscal WACC accounting-metric retrieval failed",
            ),))
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="Fiscal WACC accounting-metric retrieval or normalization failed",
            )
            return ProviderDataResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="wacc_accounting", provider=self.provider.value,
                reason="Fiscal WACC accounting-metric retrieval or normalization failed",
            ),))

        observations: list[MetricObservation] = []
        issues: list[DataIssue] = []
        future_count = 0
        for row in rows:
            source_metric = str(row.get("sourceMetric") or "").strip()
            mapping = _WACC_ACCOUNTING_FIELDS.get(source_metric)
            if mapping is None:
                continue
            metric, unit, frequency, definition = mapping
            try:
                period_end = calendar_date(row.get("periodEnd") or row.get("observationDate"))
                if period_end > analysis_as_of.date():
                    future_count += 1
                    continue
                as_of, substituted = aware_datetime(row.get("asOf"), fallback=analysis_as_of)
                value = float(row["value"])
                period_start = calendar_date(row.get("periodStart")) if frequency is Frequency.ANNUAL else None
                fiscal_year = int(row.get("fiscalYear") or period_end.year) if frequency is Frequency.ANNUAL else None
                transformations = [definition, "retained without FX conversion"]
                if substituted:
                    transformations.append("source as-of unavailable; used analysis cutoff explicitly")
                observations.append(actual_observation(
                    provider=self.provider.value,
                    dataset="fiscal_company_ratios",
                    provider_symbol=symbol,
                    source_metric=source_metric,
                    metric_id=metric,
                    value=value,
                    unit=unit,
                    currency=row.get("currency") if unit is MetricUnit.CURRENCY else None,
                    frequency=frequency,
                    retrieved_at=retrieved,
                    as_of_at=as_of,
                    period_start=period_start,
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    transformations=tuple(transformations),
                ))
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(DataIssue(
                    severity=IssueSeverity.ERROR, metric=metric, provider=self.provider.value,
                    reason=f"Fiscal WACC accounting row for {source_metric} has malformed or ambiguous period/value semantics",
                    action="skip the row rather than infer accounting evidence",
                ))
        if future_count:
            issues.append(DataIssue(
                severity=IssueSeverity.INFO, metric="wacc_accounting", provider=self.provider.value,
                reason=f"excluded {future_count} WACC accounting observations after analysis_as_of",
            ))
        status = CapabilityStatus.AVAILABLE if observations else CapabilityStatus.UNAVAILABLE
        reason = None if observations else "Fiscal returned no supported WACC accounting observations"
        capability = self.record_capability(capability_name, status, reason=reason)
        return ProviderDataResult(tuple(observations), (capability,), tuple(issues))
