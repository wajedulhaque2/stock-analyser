"""Normalized contracts shared by the read-only V1 actual-data adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from stock_analyser.domain import (
    CapabilityResult,
    CashFlowDefinitionEvidence,
    DataIssue,
    AdjustedPriceObservation,
    EquityRiskPremiumObservation,
    MarginalTaxRateObservation,
    EstimateRevision,
    ExternalValuationReference,
    HistoricalValuationObservation,
    MacroObservation,
    MetricObservation,
    PeerCandidateSource,
    SyntheticRatingBand,
)

from .identity import ProviderId


class ProviderCapability(str, Enum):
    IDENTITY = "identity"
    CURRENT_PRICE = "current_price"
    MARKET_SNAPSHOT = "market_snapshot"
    PRICE_HISTORY = "price_history"
    HISTORICAL_STANDARDIZED_FINANCIALS = "historical_standardized_financials"
    REPORTED_ACTUALS = "reported_actuals"
    ANNUAL_ANALYST_ESTIMATES = "annual_analyst_estimates"
    ANALYST_PRICE_TARGETS = "analyst_price_targets"
    EXTERNAL_STANDARD_DCF = "external_standard_dcf"
    ANNUAL_REVENUE_ESTIMATES = "annual_revenue_estimates"
    ANNUAL_EBIT_ESTIMATES = "annual_ebit_estimates"
    ANNUAL_EBITDA_ESTIMATES = "annual_ebitda_estimates"
    ANNUAL_NET_INCOME_ESTIMATES = "annual_net_income_estimates"
    ANNUAL_EPS_ESTIMATES = "annual_eps_estimates"
    ANNUAL_OCF_ESTIMATES = "annual_ocf_estimates"
    ANNUAL_CAPEX_ESTIMATES = "annual_capex_estimates"
    ANNUAL_FCF_ESTIMATES = "annual_fcf_estimates"
    EARNINGS_ESTIMATES = "earnings_estimates"
    ESTIMATE_REVISIONS = "estimate_revisions"
    USD_TREASURY_YIELD = "usd_treasury_yield"
    US_EQUITY_RISK_PREMIUM = "us_equity_risk_premium"
    SYNTHETIC_RATING_DEFAULT_SPREADS = "synthetic_rating_default_spreads"
    COUNTRY_MARGINAL_TAX_RATES = "country_marginal_tax_rates"
    HISTORICAL_VALUATION_RATIOS = "historical_valuation_ratios"
    ENTERPRISE_EQUITY_BRIDGE = "enterprise_equity_bridge"
    WACC_ACCOUNTING_METRICS = "wacc_accounting_metrics"
    PEER_CANDIDATES = "peer_candidates"


class ProviderLookupStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    """Provider-supplied identity facts before deterministic resolution."""

    provider: ProviderId
    provider_symbol: str
    retrieved_at: datetime
    company_name: str | None = None
    issuer_domicile: str | None = None
    listing_country: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    security_type: str | None = None
    reporting_currency: str | None = None
    quote_currency: str | None = None
    quote_unit: str | None = None
    price_scale: float | None = None
    fiscal_year_end: str | None = None
    underlying_security_id: str | None = None
    adr_ratio: float | None = None
    provider_issuer_id: str | None = None
    provider_security_id: str | None = None
    company_type: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderPeerCandidate:
    """Safe normalized discovery metadata before canonical identity resolution."""

    provider: ProviderId
    provider_symbol: str
    provider_issuer_id: str
    provider_security_id: str
    candidate_source: PeerCandidateSource
    source_rank: int | None
    source_as_of: datetime
    retrieved_at: datetime
    source_as_of_substituted: bool = False
    company_name: str | None = None
    listing_country: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    security_type: str | None = None
    provider_relationship: str | None = None
    provider_company_key: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderPeerFinancialResolution:
    """Provider-internal lookup metadata bound to stable peer identifiers."""

    provider: ProviderId
    provider_symbol: str
    provider_issuer_id: str
    provider_security_id: str
    status: ProviderLookupStatus
    source_dataset: str | None = None
    provider_company_key: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderPeerDiscoveryResult:
    candidates: tuple[ProviderPeerCandidate, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderDataResult:
    """Only normalized domain records cross an adapter boundary."""

    observations: tuple[MetricObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()
    cash_flow_definitions: tuple[CashFlowDefinitionEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderIdentityResult:
    candidate: IdentityCandidate | None
    capability: CapabilityResult
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderExternalReferenceResult:
    references: tuple[ExternalValuationReference, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderRevisionResult:
    observations: tuple[MetricObservation, ...] = ()
    revisions: tuple[EstimateRevision, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderMacroResult:
    observations: tuple[MacroObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderErpResult:
    observations: tuple[EquityRiskPremiumObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderSyntheticRatingResult:
    observations: tuple[SyntheticRatingBand, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderMarginalTaxResult:
    observations: tuple[MarginalTaxRateObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderPriceHistoryResult:
    observations: tuple[AdjustedPriceObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderHistoricalValuationResult:
    """Canonical dated multiples returned by a provider adapter."""

    observations: tuple[HistoricalValuationObservation, ...] = ()
    capabilities: tuple[CapabilityResult, ...] = ()
    issues: tuple[DataIssue, ...] = ()


class SecretSupplier(Protocol):
    def __call__(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SecretReference:
    """Named, lazy secret boundary whose representation never contains its value."""

    name: str
    _supplier: Callable[[], str] = field(repr=False, compare=False)

    def resolve(self) -> str:
        value = self._supplier()
        if not isinstance(value, str) or not value:
            raise ValueError(f"secret reference {self.name!r} resolved to an empty value")
        return value
