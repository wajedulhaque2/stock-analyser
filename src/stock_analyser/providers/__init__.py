"""Shared V1 provider infrastructure and injected read-only actual-data adapters."""

from .base import BaseProviderAdapter
from .cache import (
    V1_CACHE_SCHEMA_VERSION,
    Cache,
    CacheEntry,
    CacheKey,
    CacheLookup,
    CacheLookupStatus,
    CacheMetadata,
    InMemoryCache,
)
from .errors import InvalidTransportResponse, ProviderError, ProviderErrorCategory
from .contracts import (
    IdentityCandidate,
    ProviderCapability,
    ProviderDataResult,
    ProviderErpResult,
    ProviderIdentityResult,
    ProviderExternalReferenceResult,
    ProviderMacroResult,
    ProviderMarginalTaxResult,
    ProviderPriceHistoryResult,
    ProviderHistoricalValuationResult,
    ProviderLookupStatus,
    ProviderPeerCandidate,
    ProviderPeerDiscoveryResult,
    ProviderPeerFinancialResolution,
    ProviderRevisionResult,
    ProviderSyntheticRatingResult,
    SecretReference,
    SecretSupplier,
)
from .alpha_vantage import AlphaVantageAdapter
from .finnhub import FinnhubAdapter
from .fmp import FmpAdapter
from .fred import FredAdapter
from .damodaran import DamodaranAdapter, DamodaranDataSource
from .fiscal import (
    FiscalAdapter,
    FiscalActualSource,
    build_fiscal_summary_peer_identity,
    resolve_fiscal_peer_financial_lookup,
)
from .identity import ProviderId
from .sanitization import REDACTED, is_sensitive_name, redact_headers, redact_text, redact_url, safe_parameters
from .transport import (
    RetryPolicy,
    RetryingTransport,
    SafeRequestIdentity,
    TimeoutPolicy,
    Transport,
    TransportRequest,
    TransportResponse,
    parse_retry_after,
)
from .sec import SecAdapter, SecActualSource
from .yahoo import YahooAdapter, YahooDataSource

__all__ = [
    "AlphaVantageAdapter", "BaseProviderAdapter", "Cache", "CacheEntry", "CacheKey", "CacheLookup",
    "CacheLookupStatus", "CacheMetadata", "InMemoryCache", "InvalidTransportResponse",
    "DamodaranAdapter", "DamodaranDataSource", "FinnhubAdapter", "FiscalActualSource", "FiscalAdapter", "FmpAdapter", "FredAdapter", "IdentityCandidate", "ProviderCapability",
    "ProviderDataResult", "ProviderErpResult", "ProviderError", "ProviderErrorCategory", "ProviderId",
    "ProviderExternalReferenceResult", "ProviderHistoricalValuationResult", "ProviderIdentityResult", "ProviderLookupStatus", "ProviderMacroResult", "ProviderMarginalTaxResult", "ProviderPeerCandidate", "ProviderPeerDiscoveryResult", "ProviderPeerFinancialResolution", "ProviderPriceHistoryResult", "ProviderRevisionResult", "ProviderSyntheticRatingResult",
    "REDACTED", "RetryPolicy", "SecActualSource", "SecAdapter",
    "SecretReference", "SecretSupplier",
    "RetryingTransport", "SafeRequestIdentity", "TimeoutPolicy", "Transport",
    "TransportRequest", "TransportResponse", "V1_CACHE_SCHEMA_VERSION",
    "build_fiscal_summary_peer_identity", "resolve_fiscal_peer_financial_lookup", "is_sensitive_name", "parse_retry_after", "redact_headers", "redact_text",
    "redact_url", "safe_parameters", "YahooAdapter", "YahooDataSource",
]
