"""Explicit, secret-safe live verification for V1 provider adapter contracts.

Nothing in this module runs at import time.  Network access occurs only when
``build_live_runners`` is called and a returned runner is invoked by the opt-in
CLI entry point.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Protocol

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    Frequency,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    IssueSeverity,
    MetricObservation,
    ProviderSymbol,
)
from stock_analyser.providers import (
    AlphaVantageAdapter,
    FiscalAdapter,
    FinnhubAdapter,
    FmpAdapter,
    FredAdapter,
    ProviderCapability,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    RetryPolicy,
    RetryingTransport,
    SecretReference,
    SecAdapter,
    TimeoutPolicy,
    TransportRequest,
    TransportResponse,
    YahooAdapter,
)


PROVIDER_NAMES = ("yahoo", "fiscal", "sec", "fmp", "finnhub", "alpha", "fred")
_CREDENTIAL_NAMES = {
    "fiscal": "FISCAL_API_KEY",
    "fmp": "FMP_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
    "alpha": "ALPHAVANTAGE_API_KEY",
    "fred": "FRED_API_KEY",
}
_SENSITIVE_FIELD = re.compile(
    r"(?:api[_-]?key|apikey|token|authorization|cookie|secret|password|headers?|raw|payload|url)",
    re.IGNORECASE,
)


class NormalizationStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class SafeCapabilitySummary:
    name: str
    status: CapabilityStatus


@dataclass(frozen=True, slots=True)
class HistoricalValuationSmokeSummary:
    normalization: NormalizationStatus
    observation_count: int
    multiple_types: tuple[str, ...]
    earliest_observation_date: str | None
    latest_observation_date: str | None
    daily_observation_count: int = 0
    annual_observation_count: int = 0
    quarterly_observation_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "observation_count", "daily_observation_count",
            "annual_observation_count", "quarterly_observation_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "multiple_types", tuple(dict.fromkeys(self.multiple_types)))

    def safe_dict(self) -> dict[str, Any]:
        return {
            "normalization": self.normalization.value,
            "observation_count": self.observation_count,
            "multiple_types": list(self.multiple_types),
            "earliest_observation_date": self.earliest_observation_date,
            "latest_observation_date": self.latest_observation_date,
            "daily_observation_count": self.daily_observation_count,
            "annual_observation_count": self.annual_observation_count,
            "quarterly_observation_count": self.quarterly_observation_count,
        }


@dataclass(frozen=True, slots=True)
class ProviderSmokeSummary:
    provider: str
    normalization: NormalizationStatus
    capabilities: tuple[SafeCapabilitySummary, ...] = ()
    observation_count: int = 0
    metric_ids: tuple[str, ...] = ()
    annual_period_count: int = 0
    quarterly_period_count: int = 0
    future_period_count: int = 0
    reference_count: int = 0
    revision_count: int = 0
    issue_count: int = 0
    notes: tuple[str, ...] = ()
    historical_valuation: HistoricalValuationSmokeSummary | None = None

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_NAMES:
            raise ValueError("provider is not a supported smoke provider")
        for name in (
            "observation_count", "annual_period_count", "quarterly_period_count",
            "future_period_count", "reference_count", "revision_count", "issue_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metric_ids", tuple(dict.fromkeys(self.metric_ids)))
        object.__setattr__(self, "notes", tuple(self.notes))

    def safe_dict(self) -> dict[str, Any]:
        result = {
            "provider": self.provider,
            "normalization": self.normalization.value,
            "capabilities": [
                {"name": item.name, "status": item.status.value} for item in self.capabilities
            ],
            "observation_count": self.observation_count,
            "metric_ids": list(self.metric_ids),
            "annual_period_count": self.annual_period_count,
            "quarterly_period_count": self.quarterly_period_count,
            "future_period_count": self.future_period_count,
            "reference_count": self.reference_count,
            "revision_count": self.revision_count,
            "issue_count": self.issue_count,
            "notes": list(self.notes),
        }
        if self.historical_valuation is not None:
            result["historical_valuation"] = self.historical_valuation.safe_dict()
        if any(_SENSITIVE_FIELD.search(key) for key in result):
            raise AssertionError("safe summary field allowlist contains a sensitive name")
        return result


@dataclass(frozen=True, slots=True)
class SmokeRunResult:
    symbol: str
    summaries: tuple[ProviderSmokeSummary, ...]
    strict: bool = False

    @property
    def exit_code(self) -> int:
        if self.strict and any(item.normalization is NormalizationStatus.FAIL for item in self.summaries):
            return 1
        return 0

    def safe_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strict": self.strict,
            "providers": [item.safe_dict() for item in self.summaries],
        }


SmokeRunner = Callable[[str], ProviderSmokeSummary]


def parse_provider_selection(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return PROVIDER_NAMES
    selected = tuple(dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip()))
    invalid = tuple(item for item in selected if item not in PROVIDER_NAMES)
    if invalid:
        raise ValueError(f"unsupported provider selection: {', '.join(invalid)}")
    if not selected:
        raise ValueError("at least one provider must be selected")
    return selected


def _clean_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", normalized):
        raise ValueError("symbol must be a short exchange symbol")
    return normalized


def load_dotenv_safely(paths: Iterable[Path], environment: dict[str, str] | None = None) -> tuple[str, ...]:
    """Load simple KEY=VALUE entries without logging or returning values."""
    target = environment if environment is not None else os.environ
    loaded: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in target:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            target[name] = value
            loaded.append(name)
    return tuple(dict.fromkeys(loaded))


def credential_presence(environment: Mapping[str, str]) -> dict[str, bool]:
    return {
        provider: bool(environment.get(variable, "").strip())
        for provider, variable in _CREDENTIAL_NAMES.items()
    }


def _fred_credential_has_documented_format(value: str) -> bool:
    """FRED documents API keys as exactly 32 lowercase alphanumeric characters."""
    return re.fullmatch(r"[a-z0-9]{32}", value.strip()) is not None


def run_selected(
    *,
    symbol: str,
    providers: Iterable[str],
    runners: Mapping[str, SmokeRunner],
    strict: bool = False,
) -> SmokeRunResult:
    normalized_symbol = _clean_symbol(symbol)
    selected = tuple(providers)
    summaries: list[ProviderSmokeSummary] = []
    for provider in selected:
        if provider not in PROVIDER_NAMES:
            raise ValueError(f"unsupported provider selection: {provider}")
        runner = runners.get(provider)
        if runner is None:
            summaries.append(ProviderSmokeSummary(
                provider=provider,
                normalization=NormalizationStatus.SKIPPED,
                notes=("runtime or credential unavailable",),
            ))
            continue
        try:
            summary = runner(normalized_symbol)
            if summary.provider != provider:
                raise ValueError("runner returned the wrong provider summary")
            summaries.append(summary)
        except Exception:
            # Arbitrary exception text can contain request URLs, credentials, or raw bodies.
            summaries.append(ProviderSmokeSummary(
                provider=provider,
                normalization=NormalizationStatus.FAIL,
                notes=("provider execution failed with a suppressed secret-safe diagnostic",),
            ))
    return SmokeRunResult(normalized_symbol, tuple(summaries), strict)


def render_text(result: SmokeRunResult) -> str:
    lines = [f"V1 live provider smoke: {result.symbol}"]
    for summary in result.summaries:
        lines.extend(("", summary.provider.upper(), f"  normalization          {summary.normalization.value}"))
        for capability in summary.capabilities:
            lines.append(f"  {capability.name[:22]:<22} {capability.status.value.upper()}")
        lines.append(f"  observations mapped    {summary.observation_count}")
        if summary.metric_ids:
            lines.append(f"  metrics                {', '.join(summary.metric_ids)}")
        if summary.annual_period_count or summary.quarterly_period_count:
            lines.append(f"  annual periods         {summary.annual_period_count}")
            lines.append(f"  quarterly periods      {summary.quarterly_period_count}")
        if summary.future_period_count:
            lines.append(f"  future periods         {summary.future_period_count}")
        if summary.reference_count:
            lines.append(f"  external references    {summary.reference_count}")
        if summary.revision_count:
            lines.append(f"  revisions              {summary.revision_count}")
        if summary.issue_count:
            lines.append(f"  structured issues      {summary.issue_count}")
        for note in summary.notes:
            lines.append(f"  note                   {note}")
        history = summary.historical_valuation
        if history is not None:
            lines.extend((
                "",
                f"{summary.provider.upper()} HISTORICAL VALUATION",
                f"  normalization          {history.normalization.value}",
                f"  observations mapped    {history.observation_count}",
                f"  multiple types         {', '.join(history.multiple_types) if history.multiple_types else 'none'}",
                f"  earliest observation   {history.earliest_observation_date or 'none'}",
                f"  latest observation     {history.latest_observation_date or 'none'}",
                f"  daily observations     {history.daily_observation_count}",
                f"  annual observations    {history.annual_observation_count}",
                f"  quarterly observations {history.quarterly_observation_count}",
            ))
    return "\n".join(lines)


def render_json(result: SmokeRunResult) -> str:
    return json.dumps(result.safe_dict(), indent=2, sort_keys=True)


def _capability_summaries(capabilities: Iterable[CapabilityResult]) -> tuple[SafeCapabilitySummary, ...]:
    return tuple(SafeCapabilitySummary(item.capability, item.status) for item in capabilities)


def _normalization(
    capabilities: Iterable[CapabilityResult], issue_severities: Iterable[IssueSeverity], evidence_count: int,
) -> NormalizationStatus:
    caps = tuple(capabilities)
    severities = tuple(issue_severities)
    if any(item.status is CapabilityStatus.ERROR for item in caps):
        return NormalizationStatus.FAIL
    if evidence_count and not any(item is IssueSeverity.BLOCKING for item in severities):
        if any(item.status in {CapabilityStatus.LOCKED, CapabilityStatus.UNAVAILABLE} for item in caps) or severities:
            return NormalizationStatus.PARTIAL
        return NormalizationStatus.PASS
    if any(item.status in {CapabilityStatus.LOCKED, CapabilityStatus.UNAVAILABLE} for item in caps):
        return NormalizationStatus.PARTIAL
    return NormalizationStatus.FAIL


def _observation_summary(
    provider: str,
    capabilities: Iterable[CapabilityResult],
    observations: Iterable[MetricObservation],
    issues: Iterable[Any],
    *,
    notes: tuple[str, ...] = (),
    reference_count: int = 0,
    revision_count: int = 0,
    historical_valuation: HistoricalValuationSmokeSummary | None = None,
) -> ProviderSmokeSummary:
    caps = tuple(capabilities)
    items = tuple(observations)
    issue_items = tuple(issues)
    periods = {item.period_end for item in items if item.period_end is not None}
    annual = {item.period_end for item in items if item.frequency is Frequency.ANNUAL}
    quarterly = {item.period_end for item in items if item.frequency is Frequency.QUARTERLY}
    today = datetime.now(timezone.utc).date()
    future = {item.period_end for item in items if item.period_end is not None and item.period_end > today}
    evidence_count = len(items) + reference_count + revision_count
    return ProviderSmokeSummary(
        provider=provider,
        normalization=_normalization(caps, (item.severity for item in issue_items), evidence_count),
        capabilities=_capability_summaries(caps),
        observation_count=len(items),
        metric_ids=tuple(sorted({item.metric_id.value for item in items})),
        annual_period_count=len(annual),
        quarterly_period_count=len(quarterly),
        future_period_count=len(future),
        reference_count=reference_count,
        revision_count=revision_count,
        issue_count=len(issue_items),
        notes=notes,
        historical_valuation=historical_valuation,
    )


def _historical_valuation_summary(
    capabilities: Iterable[CapabilityResult],
    observations: Iterable[HistoricalValuationObservation],
    issues: Iterable[Any],
) -> HistoricalValuationSmokeSummary:
    caps = tuple(capabilities)
    items = tuple(observations)
    issue_items = tuple(issues)
    dates = tuple(item.observation_date for item in items)
    return HistoricalValuationSmokeSummary(
        normalization=_normalization(caps, (item.severity for item in issue_items), len(items)),
        observation_count=len(items),
        multiple_types=tuple(sorted({item.multiple_type.value for item in items})),
        earliest_observation_date=min(dates).isoformat() if dates else None,
        latest_observation_date=max(dates).isoformat() if dates else None,
        daily_observation_count=sum(item.sampling is HistoricalValuationSampling.DAILY for item in items),
        annual_observation_count=sum(item.sampling is HistoricalValuationSampling.ANNUAL for item in items),
        quarterly_observation_count=sum(item.sampling is HistoricalValuationSampling.QUARTERLY for item in items),
    )


class RequestsJsonTransport:
    """Minimal in-memory JSON transport for an explicitly invoked smoke run."""

    def __init__(self, session=None) -> None:
        import requests

        self._requests = requests
        self._session = session or requests.Session()

    def send(self, request: TransportRequest) -> TransportResponse:
        try:
            response = self._session.request(
                request.method,
                request.url,
                params=dict(request.parameters),
                headers=dict(request.headers),
                timeout=request.timeout,
            )
        except self._requests.Timeout as error:
            raise TimeoutError from error
        except self._requests.RequestException as error:
            raise ConnectionError from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and isinstance(body, Mapping):
            if request.provider is ProviderId.ALPHA_VANTAGE:
                if "Note" in body:
                    raise ProviderError(
                        provider=request.provider, endpoint_id=request.endpoint_id,
                        category=ProviderErrorCategory.RATE_LIMIT, retryable=False,
                        safe_message="Alpha Vantage returned a rate-limit notice",
                    )
                if "Error Message" in body:
                    raise ProviderError(
                        provider=request.provider, endpoint_id=request.endpoint_id,
                        category=ProviderErrorCategory.INVALID_REQUEST, retryable=False,
                        safe_message="Alpha Vantage rejected the request",
                    )
                if "Information" in body and len(body) == 1:
                    raise ProviderError(
                        provider=request.provider, endpoint_id=request.endpoint_id,
                        category=ProviderErrorCategory.ENTITLEMENT, retryable=False,
                        safe_message="Alpha Vantage endpoint is unavailable for the current entitlement",
                    )
            if request.provider is ProviderId.FMP and any(
                key in body for key in ("Error Message", "error", "Error")
            ):
                raise ProviderError(
                    provider=request.provider, endpoint_id=request.endpoint_id,
                    category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                    safe_message="FMP returned a structured endpoint error",
                )
        return TransportResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
            retrieved_at=datetime.now(timezone.utc),
        )


def build_live_transport() -> RetryingTransport:
    return RetryingTransport(
        RequestsJsonTransport(),
        timeout_policy=TimeoutPolicy(request_timeout=20),
        retry_policy=RetryPolicy(max_attempts=2, base_backoff=1, maximum_backoff=2, jitter=0),
    )


def _retrying_transport() -> RetryingTransport:
    """Backward-compatible private alias for the existing smoke runners."""
    return build_live_transport()


def _secret(provider: str, environment: Mapping[str, str]) -> SecretReference:
    variable = _CREDENTIAL_NAMES[provider]
    return SecretReference(variable, lambda: environment[variable])


def _provisional_identity(symbol: str) -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=f"live-smoke-security:{symbol}",
        issuer_id=f"live-smoke-issuer:{symbol}",
        company_name=f"Live smoke input {symbol}",
        issuer_domicile="US",
        listing_country="US",
        exchange="US",
        sector="Unknown",
        industry="Unknown",
        security_type="Ordinary share",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=tuple(
            ProviderSymbol(provider, symbol)
            for provider in ("yahoo", "fiscal", "fmp", "finnhub", "alpha_vantage")
        ),
    )


class LiveYahooSource:
    def __init__(self) -> None:
        self._cache: dict[str, Mapping[str, Any]] = {}

    def _info(self, symbol: str) -> Mapping[str, Any]:
        if symbol not in self._cache:
            import yfinance as yf

            self._cache[symbol] = dict(yf.Ticker(symbol).get_info())
        return self._cache[symbol]

    @staticmethod
    def _as_of(value: Any) -> str | None:
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _fiscal_year_end(value: Any) -> str | None:
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).strftime("%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def identity_metadata(self, symbol: str) -> Mapping[str, Any]:
        row = self._info(symbol)
        return {
            "longName": row.get("longName") or row.get("shortName"),
            "country": row.get("country"),
            "listingCountry": row.get("country"),
            "exchange": row.get("exchange") or row.get("fullExchangeName"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "quoteType": row.get("quoteType"),
            "financialCurrency": row.get("financialCurrency"),
            "currency": row.get("currency"),
            "quoteUnit": row.get("currency"),
            "fiscalYearEnd": self._fiscal_year_end(row.get("lastFiscalYearEnd")),
        }

    def market_snapshot(self, symbol: str) -> Mapping[str, Any]:
        row = self._info(symbol)
        return {
            # Milestone 11B admits one explicit price semantic. Do not relabel
            # another Yahoo field as regularMarketPrice when it is absent.
            "regularMarketPrice": row.get("regularMarketPrice"),
            "marketCap": row.get("marketCap"),
            "sharesOutstanding": row.get("sharesOutstanding"),
            "beta": row.get("beta"),
            "regularMarketVolume": row.get("regularMarketVolume") or row.get("volume"),
            "asOf": self._as_of(row.get("regularMarketTime")),
        }

    def adjusted_price_history(self, symbol: str, start: date, end: date) -> tuple[Mapping[str, Any], ...]:
        """Use the existing Yahoo/yfinance boundary with one explicit adjusted-history convention."""
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        history = ticker.history(
            start=start.isoformat(), end=end.isoformat(), interval="1d",
            auto_adjust=True, actions=False, repair=True,
        )
        metadata = ticker.get_history_metadata(repair=True)
        currency = str(metadata.get("currency") or "").upper() if isinstance(metadata, Mapping) else ""
        if history is None or history.empty or "Close" not in history.columns:
            return ()
        rows = []
        for index, row in history.iterrows():
            day = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
            value = row.get("Close")
            if value is None:
                continue
            rows.append({
                "date": day.isoformat(),
                "adjustedClose": float(value),
                "currency": currency,
                "returnSemantics": "split_and_distribution_adjusted",
                "asOf": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            })
        return tuple(rows)


class SafeRequestObserver(Protocol):
    """Receive allowlisted request lifecycle facts, never request data."""

    def request_attempted(self) -> None: ...
    def request_failed(self) -> None: ...
    def http_response(self, status_code: int) -> None: ...
    def parse_completed(self, succeeded: bool) -> None: ...

    def identity_acquisition_updated(
        self,
        *,
        candidate_count: int,
        pages_examined: int,
        venue_evidence_found: bool,
        candidate_resolution_status: str,
        profile_enrichment_attempted: bool,
        bounded_search_exhausted: bool,
    ) -> None: ...


class _SafeRequestSource:
    def __init__(
        self,
        provider: ProviderId,
        *,
        session=None,
        user_agent: str | None = None,
        observer: SafeRequestObserver | None = None,
    ) -> None:
        import requests

        self._requests = requests
        self._session = session or requests.Session()
        self._provider = provider
        self._user_agent = user_agent
        self._observer = observer

    def _get(self, endpoint_id: str, url: str, *, params=None, headers=None) -> Any:
        request_headers = dict(headers or {})
        if self._user_agent:
            request_headers.setdefault("User-Agent", self._user_agent)
        if self._observer is not None:
            self._observer.request_attempted()
        try:
            response = self._session.get(url, params=params or {}, headers=request_headers, timeout=20)
        except self._requests.Timeout as error:
            if self._observer is not None:
                self._observer.request_failed()
            raise ProviderError(
                provider=self._provider, endpoint_id=endpoint_id,
                category=ProviderErrorCategory.TIMEOUT, retryable=False,
                safe_message="live source request timed out",
            ) from error
        except self._requests.RequestException as error:
            if self._observer is not None:
                self._observer.request_failed()
            raise ProviderError(
                provider=self._provider, endpoint_id=endpoint_id,
                category=ProviderErrorCategory.CONNECTION, retryable=False,
                safe_message="live source connection failed",
            ) from error
        if self._observer is not None:
            self._observer.http_response(response.status_code)
        category = {
            401: ProviderErrorCategory.AUTHENTICATION,
            403: ProviderErrorCategory.UNKNOWN,
            404: ProviderErrorCategory.NOT_FOUND,
            429: ProviderErrorCategory.RATE_LIMIT,
        }.get(response.status_code)
        if category is not None or response.status_code >= 400:
            raise ProviderError(
                provider=self._provider,
                endpoint_id=endpoint_id,
                category=category or ProviderErrorCategory.SERVER,
                status_code=response.status_code,
                retryable=False,
                safe_message="live source returned a non-success status",
            )
        try:
            payload = response.json()
            if self._observer is not None:
                self._observer.parse_completed(True)
            return payload
        except ValueError as error:
            if self._observer is not None:
                self._observer.parse_completed(False)
            raise ProviderError(
                provider=self._provider, endpoint_id=endpoint_id,
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="live source did not return JSON",
            ) from error


class LiveSecSource(_SafeRequestSource):
    _TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    _FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def __init__(self, *, user_agent: str) -> None:
        super().__init__(ProviderId.SEC, user_agent=user_agent)
        self._ticker_rows: tuple[Mapping[str, Any], ...] | None = None

    def _tickers(self) -> tuple[Mapping[str, Any], ...]:
        if self._ticker_rows is None:
            payload = self._get("sec_company_tickers", self._TICKERS_URL)
            rows = payload.values() if isinstance(payload, Mapping) else ()
            self._ticker_rows = tuple(item for item in rows if isinstance(item, Mapping))
        return self._ticker_rows

    def identity_metadata(self, symbol: str) -> Mapping[str, Any]:
        row = next(
            (item for item in self._tickers() if str(item.get("ticker", "")).upper() == symbol.upper()),
            None,
        )
        if row is None:
            raise ProviderError(
                provider=ProviderId.SEC, endpoint_id="sec_company_tickers",
                category=ProviderErrorCategory.NOT_FOUND, retryable=False,
                safe_message="SEC ticker-to-CIK mapping was not found",
            )
        return {
            "cik": f"{int(row['cik_str']):010d}",
            "companyName": row.get("title"),
            "issuerDomicile": "US",
        }

    def reported_actuals(self, cik: str) -> Iterable[Mapping[str, Any]]:
        payload = self._get(
            "sec_companyfacts", self._FACTS_URL.format(cik=str(cik).zfill(10)),
        )
        us_gaap = ((payload.get("facts") or {}).get("us-gaap") or {}) if isinstance(payload, Mapping) else {}
        supported = {
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues", "SalesRevenueNet", "OperatingIncomeLoss", "NetIncomeLoss", "ProfitLoss",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            "CashAndCashEquivalentsAtCarryingValue", "EntityCommonStockSharesOutstanding",
        }
        output: list[Mapping[str, Any]] = []
        for tag in supported:
            fact = us_gaap.get(tag)
            if not isinstance(fact, Mapping):
                continue
            units = fact.get("units") or {}
            preferred_units = ("USD",) if tag != "EntityCommonStockSharesOutstanding" else ("shares",)
            rows = next((units.get(unit) for unit in preferred_units if isinstance(units.get(unit), list)), [])
            candidates = []
            for row in rows:
                if not isinstance(row, Mapping) or row.get("val") is None or row.get("end") is None:
                    continue
                instant = row.get("start") is None
                annual = row.get("form") in {"10-K", "10-K/A"} and row.get("fp") == "FY"
                if not instant and not annual:
                    continue
                candidates.append(row)
            candidates.sort(key=lambda item: (str(item.get("end")), str(item.get("filed"))), reverse=True)
            for row in candidates[:8]:
                output.append({
                    "tag": tag,
                    "taxonomy": "us-gaap",
                    "value": row.get("val"),
                    "currency": "USD" if tag != "EntityCommonStockSharesOutstanding" else None,
                    "periodStart": row.get("start"),
                    "periodEnd": row.get("end"),
                    "filedAt": f"{row.get('filed')}T00:00:00+00:00",
                    "frequency": "annual",
                    "fiscalYear": row.get("fy"),
                })
        return output


def _canon(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


_FISCAL_METRICS = {
    "revenue": ("revenue", "total revenue", "revenues", "sales"),
    "operating_income": ("operating income", "operating profit"),
    "ebit": ("ebit", "earnings before interest and taxes"),
    "interest_expense": ("interest expense", "interest expenses", "finance costs", "finance cost"),
    "ebitda": ("ebitda",),
    "net_income_common": ("net income attributable to common", "net income"),
    "operating_cash_flow": ("operating cash flow", "cash from operating activities", "net cash provided by operating activities"),
    "capital_expenditure_cash_outflow": ("capital expenditures", "capital expenditure", "purchase of property plant and equipment"),
    "depreciation_amortization": ("depreciation and amortization", "depreciation", "d&a"),
    "cash_and_equivalents": ("cash and cash equivalents", "cash and short term investments"),
    "gross_debt": ("total debt", "debt and finance lease obligations"),
    "shares_basic": ("basic weighted average shares outstanding", "weighted average shares basic"),
    "shares_diluted": ("diluted weighted average shares outstanding", "weighted average shares diluted"),
    "shares_outstanding": ("total shares outstanding", "shares outstanding"),
    "free_cash_flow": ("free cash flow",),
}


class LiveFiscalSource(_SafeRequestSource):
    _BASE_URL = "https://api.fiscal.ai"
    _IDENTITY_MAX_PAGES = 16
    _IDENTITY_MAX_CANDIDATES = 16_000
    _IDENTITY_MAX_PROFILE_LISTINGS = 256
    _CANONICAL_SUFFIX_VENUES = {
        ".L": frozenset({"LSE", "XLON", "LON", "LONDON STOCK EXCHANGE"}),
    }
    _CANONICAL_SUFFIX_LOOKUP_MIC = {".L": "XLON"}

    def __init__(self, *, session=None, observer: SafeRequestObserver | None = None) -> None:
        super().__init__(
            ProviderId.FISCAL,
            session=session,
            user_agent="StockAnalyser/0.7.1",
            observer=observer,
        )
        self._companies: dict[str, Mapping[str, Any]] = {}
        self._companies_by_key: dict[str, Mapping[str, Any]] = {}
        self._company_records: tuple[Mapping[str, Any], ...] | None = None
        self._company_pages: dict[int, tuple[Mapping[str, Any], ...]] = {}
        self._company_pagination: dict[int, Mapping[str, Any]] = {}
        self._profiles: dict[str, Mapping[str, Any]] = {}

    def _api_get(self, endpoint: str, *, credential: str, params=None) -> Any:
        return self._get(
            f"fiscal:{endpoint}", f"{self._BASE_URL}{endpoint}",
            params=params, headers={"X-Api-Key": credential, "Accept": "application/json"},
        )

    def company_lookup_records(
        self, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Return one cached Fiscal company-list page for stable-ID resolution."""
        if not credential:
            raise ValueError("credential is required")
        if self._company_records is None:
            self._company_records = self._company_page(1, credential)
        return self._company_records

    def _record_identity_acquisition(self, **facts: Any) -> None:
        callback = getattr(self._observer, "identity_acquisition_updated", None)
        if callable(callback):
            callback(**facts)

    def _register_company_rows(self, rows: tuple[Mapping[str, Any], ...]) -> None:
        for item in rows:
            listing = item.get("primaryListing") or {}
            provider_symbol = str(listing.get("ticker") or item.get("ticker") or "").upper()
            company_key = self._company_key(item)
            if provider_symbol:
                self._companies.setdefault(provider_symbol, item)
            if company_key:
                self._companies_by_key.setdefault(company_key, item)

    def _company_page(
        self, page_number: int, credential: str,
    ) -> tuple[Mapping[str, Any], ...]:
        if page_number not in self._company_pages:
            payload = self._api_get(
                "/v3/companies-list", credential=credential,
                params={"compact": "true", "pageNumber": page_number},
            )
            rows = payload.get("data", ()) if isinstance(payload, Mapping) else payload
            normalized = tuple(item for item in rows or () if isinstance(item, Mapping))
            self._company_pages[page_number] = normalized
            pagination = payload.get("pagination") if isinstance(payload, Mapping) else None
            self._company_pagination[page_number] = (
                pagination if isinstance(pagination, Mapping) else {}
            )
            self._register_company_rows(normalized)
        return self._company_pages[page_number]

    @classmethod
    def _suffix(cls, symbol: str) -> str | None:
        return next((value for value in cls._CANONICAL_SUFFIX_VENUES if symbol.endswith(value)), None)

    @staticmethod
    def _listing_venue_tokens(listing: Mapping[str, Any]) -> frozenset[str]:
        return frozenset(
            str(value).strip().upper()
            for value in (
                listing.get("exchangeCode"),
                listing.get("operatingMic"),
                listing.get("micCode"),
                listing.get("exchangeName"),
            )
            if str(value or "").strip()
        )

    @staticmethod
    def _listing_venue_codes(listing: Mapping[str, Any]) -> frozenset[str]:
        return frozenset(
            str(value).strip().upper()
            for value in (
                listing.get("exchangeCode"),
                listing.get("operatingMic"),
                listing.get("micCode"),
            )
            if str(value or "").strip()
        )

    @classmethod
    def _listing_matches_context(
        cls, listing: Mapping[str, Any], *, base_symbol: str, suffix: str,
    ) -> bool:
        provider_ticker = str(listing.get("ticker") or "").strip().upper()
        venue_codes = cls._listing_venue_codes(listing)
        venue_name = str(listing.get("exchangeName") or "").strip().upper()
        accepted = cls._CANONICAL_SUFFIX_VENUES[suffix]
        venue_is_proven = (
            bool(venue_codes.intersection(accepted))
            and not venue_codes.difference(accepted)
        ) if venue_codes else venue_name in accepted
        return (
            provider_ticker == base_symbol
            and venue_is_proven
        )

    @staticmethod
    def _profile_listings(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        candidates = []
        primary = profile.get("primaryListing")
        if isinstance(primary, Mapping):
            candidates.append(primary)
        candidates.extend(
            listing for listing in (profile.get("secondaryListings") or ())
            if isinstance(listing, Mapping)
        )
        return tuple(candidates)

    @staticmethod
    def _company_with_listing(
        company: Mapping[str, Any], listing: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        selected = dict(company)
        selected["primaryListing"] = dict(listing)
        return selected

    def _exact_listing_company(
        self, *, base_symbol: str, suffix: str, credential: str,
    ) -> Mapping[str, Any] | None:
        try:
            profile = self._api_get(
                "/v3/company/profile",
                credential=credential,
                params={
                    "ticker": base_symbol,
                    "micCode": self._CANONICAL_SUFFIX_LOOKUP_MIC[suffix],
                },
            )
        except ProviderError as error:
            if error.category is ProviderErrorCategory.NOT_FOUND:
                return None
            raise
        if not isinstance(profile, Mapping):
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE,
                retryable=False,
                safe_message="Fiscal exact-listing lookup returned an invalid result",
            )
        listings = self._profile_listings(profile)
        if len(listings) > self._IDENTITY_MAX_PROFILE_LISTINGS:
            self._record_identity_acquisition(
                candidate_count=0, pages_examined=1, venue_evidence_found=False,
                candidate_resolution_status="bound_exhausted",
                profile_enrichment_attempted=True, bounded_search_exhausted=True,
            )
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE,
                retryable=False,
                safe_message="Fiscal exact-listing evidence exceeded the bounded listing limit",
            )
        matches = tuple(
            listing for listing in listings
            if self._listing_matches_context(listing, base_symbol=base_symbol, suffix=suffix)
        )
        if len(matches) != 1:
            self._record_identity_acquisition(
                candidate_count=len(matches), pages_examined=1,
                venue_evidence_found=bool(matches),
                candidate_resolution_status="conflict" if matches else "not_found",
                profile_enrichment_attempted=True, bounded_search_exhausted=False,
            )
            if not matches:
                return None
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE,
                retryable=False,
                safe_message="Fiscal exact-listing evidence did not prove one requested venue security",
            )
        issuer_id = str(profile.get("companyFiscalIdentifier") or "").strip()
        security_id = str(matches[0].get("securityFiscalIdentifier") or "").strip()
        if not issuer_id or not security_id:
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE,
                retryable=False,
                safe_message="Fiscal exact-listing evidence lacks stable issuer or security identity",
            )
        company = self._company_with_listing(profile, matches[0])
        self._profiles[issuer_id] = profile
        company_key = self._company_key(profile)
        if company_key:
            self._profiles[company_key] = profile
        self._register_company_rows((company,))
        self._record_identity_acquisition(
            candidate_count=1, pages_examined=1, venue_evidence_found=True,
            candidate_resolution_status="resolved", profile_enrichment_attempted=True,
            bounded_search_exhausted=False,
        )
        return company

    def _bounded_listing_company(
        self, *, base_symbol: str, suffix: str, credential: str,
    ) -> Mapping[str, Any] | None:
        first_page = tuple(self.company_lookup_records(credential=credential))
        pagination = self._company_pagination.get(1, {})
        try:
            total_pages = int(pagination.get("totalPages", 1))
        except (TypeError, ValueError):
            total_pages = 1
        if total_pages < 1:
            total_pages = 1
        if total_pages > self._IDENTITY_MAX_PAGES:
            self._record_identity_acquisition(
                candidate_count=0, pages_examined=1, venue_evidence_found=False,
                candidate_resolution_status="bound_exhausted",
                profile_enrichment_attempted=True, bounded_search_exhausted=True,
            )
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:companies-list",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="Fiscal identity pagination exceeded the configured bound",
            )
        pages = [first_page]
        for page_number in range(2, total_pages + 1):
            pages.append(self._company_page(page_number, credential))
        rows = tuple(item for page in pages for item in page)
        if len(rows) > self._IDENTITY_MAX_CANDIDATES:
            self._record_identity_acquisition(
                candidate_count=0, pages_examined=len(pages), venue_evidence_found=False,
                candidate_resolution_status="bound_exhausted",
                profile_enrichment_attempted=True, bounded_search_exhausted=True,
            )
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:companies-list",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="Fiscal identity candidate volume exceeded the configured bound",
            )
        matches = tuple(
            item for item in rows
            if isinstance(item.get("primaryListing"), Mapping)
            and self._listing_matches_context(
                item["primaryListing"], base_symbol=base_symbol, suffix=suffix,
            )
        )
        self._record_identity_acquisition(
            candidate_count=len(matches), pages_examined=len(pages),
            venue_evidence_found=bool(matches),
            candidate_resolution_status=(
                "resolved" if len(matches) == 1 else "conflict" if matches else "not_found"
            ),
            profile_enrichment_attempted=True, bounded_search_exhausted=False,
        )
        if len(matches) > 1:
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:companies-list",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="multiple Fiscal securities match the requested listing context",
            )
        return matches[0] if matches else None

    def _company(self, symbol: str, credential: str) -> Mapping[str, Any]:
        normalized = symbol.upper()
        if normalized not in self._companies:
            available_rows = tuple(self.company_lookup_records(credential=credential))
            row = next((
                item for item in available_rows
                if str(((item.get("primaryListing") or {}).get("ticker") or item.get("ticker") or "")).upper()
                == normalized
            ), None)
            if row is None:
                suffix = self._suffix(normalized)
                if suffix is not None:
                    base_symbol = normalized[:-len(suffix)]
                    initial_matches = tuple(
                        item for item in available_rows
                        if isinstance(item.get("primaryListing"), Mapping)
                        and self._listing_matches_context(
                            item["primaryListing"], base_symbol=base_symbol, suffix=suffix,
                        )
                    )
                    if len(initial_matches) == 1:
                        row = initial_matches[0]
                        self._record_identity_acquisition(
                            candidate_count=1, pages_examined=1, venue_evidence_found=True,
                            candidate_resolution_status="resolved",
                            profile_enrichment_attempted=False,
                            bounded_search_exhausted=False,
                        )
                    elif len(initial_matches) > 1:
                        raise ProviderError(
                            provider=ProviderId.FISCAL,
                            endpoint_id="fiscal:companies-list",
                            category=ProviderErrorCategory.INVALID_RESPONSE,
                            retryable=False,
                            safe_message="multiple Fiscal securities match the requested listing context",
                        )
                    else:
                        row = self._exact_listing_company(
                            base_symbol=base_symbol, suffix=suffix, credential=credential,
                        )
                        if row is None:
                            row = self._bounded_listing_company(
                                base_symbol=base_symbol, suffix=suffix, credential=credential,
                            )
            if row is None:
                raise ProviderError(
                    provider=ProviderId.FISCAL, endpoint_id="fiscal:companies-list",
                    category=ProviderErrorCategory.NOT_FOUND, retryable=False,
                    safe_message="symbol is outside the entitled Fiscal company universe",
                )
            self._companies[normalized] = row
            listing = row.get("primaryListing") or {}
            resolved_symbol = str(listing.get("ticker") or row.get("ticker") or "").upper()
            company_key = self._company_key(row)
            if resolved_symbol:
                self._companies.setdefault(resolved_symbol, row)
            if company_key:
                self._companies_by_key.setdefault(company_key, row)
        return self._companies[normalized]

    def _identifier(self, company: Mapping[str, Any]) -> str:
        return str(company.get("companyFiscalIdentifier") or "")

    @staticmethod
    def _company_key(company: Mapping[str, Any]) -> str:
        return str(company.get("companyKey") or "")

    def _profile(self, company: Mapping[str, Any], credential: str) -> Mapping[str, Any]:
        issuer_id = self._identifier(company)
        if not issuer_id:
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="Fiscal company evidence lacks a stable issuer identifier",
            )
        if issuer_id not in self._profiles:
            self._record_identity_acquisition(
                candidate_count=1, pages_examined=1, venue_evidence_found=False,
                candidate_resolution_status="resolved", profile_enrichment_attempted=True,
                bounded_search_exhausted=False,
            )
            payload = self._api_get(
                "/v3/company/profile", credential=credential, params={"fscl": issuer_id},
            )
            profile = payload if isinstance(payload, Mapping) else {}
            profile_issuer_id = str(profile.get("companyFiscalIdentifier") or "")
            if profile_issuer_id and profile_issuer_id != issuer_id:
                raise ProviderError(
                    provider=ProviderId.FISCAL, endpoint_id="fiscal:company-profile",
                    category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                    safe_message="Fiscal company-list and profile issuer identity conflict",
                )
            self._profiles[issuer_id] = profile
            company_key = self._company_key(company)
            if company_key:
                self._profiles[company_key] = profile
        return self._profiles[issuer_id]

    def _verified_profile_listing(
        self, symbol: str, company: Mapping[str, Any], profile: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        summary_listing = company.get("primaryListing") or {}
        security_id = str(summary_listing.get("securityFiscalIdentifier") or "").strip()
        if not security_id:
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="Fiscal listing evidence lacks a stable security identifier",
            )
        matches = tuple(
            listing for listing in self._profile_listings(profile)
            if str(listing.get("securityFiscalIdentifier") or "").strip() == security_id
        )
        if not self._profile_listings(profile):
            return summary_listing
        listing_id = str(summary_listing.get("listingFiscalIdentifier") or "").strip()
        if listing_id:
            matches = tuple(
                listing for listing in matches
                if str(listing.get("listingFiscalIdentifier") or "").strip() == listing_id
            )
        else:
            summary_ticker = str(summary_listing.get("ticker") or "").strip().upper()
            summary_venues = self._listing_venue_tokens(summary_listing)
            matches = tuple(
                listing for listing in matches
                if str(listing.get("ticker") or "").strip().upper() == summary_ticker
                and bool(summary_venues.intersection(self._listing_venue_tokens(listing)))
            )
        suffix = self._suffix(symbol.upper())
        if suffix is not None:
            base_symbol = symbol.upper()[:-len(suffix)]
            matches = tuple(
                listing for listing in matches
                if self._listing_matches_context(
                    listing, base_symbol=base_symbol, suffix=suffix,
                )
            )
        if len(matches) != 1:
            raise ProviderError(
                provider=ProviderId.FISCAL, endpoint_id="fiscal:company-profile",
                category=ProviderErrorCategory.INVALID_RESPONSE, retryable=False,
                safe_message="Fiscal profile did not preserve one stable requested security",
            )
        return matches[0]

    def identity_metadata(self, symbol: str, *, credential: str | None = None) -> Mapping[str, Any]:
        if not credential:
            raise ValueError("credential is required")
        company = self._company(symbol, credential)
        profile = self._profile(company, credential)
        listing = self._verified_profile_listing(symbol, company, profile)
        return {
            "provider_symbol": listing.get("ticker") or company.get("ticker") or symbol,
            "company_name": profile.get("displayNameEnglish") or company.get("displayNameEnglish"),
            "issuer_domicile": (
                profile.get("legalDomicileCountryCode")
                or profile.get("countryOfIncorporation")
                or profile.get("country")
            ),
            "listing_country": (
                listing.get("exchangeCountryCode")
                or listing.get("country")
                or profile.get("headquartersCountryCode")
                or profile.get("country")
            ),
            "exchange": (
                listing.get("exchangeCode")
                or listing.get("operatingMic")
                or listing.get("micCode")
            ),
            "sector": profile.get("sector") or company.get("sector"),
            "industry": profile.get("industry") or profile.get("subIndustry"),
            "security_type": listing.get("securityType"),
            "reporting_currency": profile.get("reportingCurrency") or company.get("reportingCurrency"),
            "quote_currency": (
                listing.get("tradingCurrency")
                or listing.get("currency")
                or profile.get("reportingCurrency")
            ),
            "quote_unit": (
                listing.get("tradingCurrency")
                or listing.get("currency")
                or profile.get("reportingCurrency")
            ),
            "price_scale": 1,
            "fiscal_year_end": profile.get("fiscalYearEnd"),
            "provider_issuer_id": self._identifier(company),
            "provider_security_id": listing.get("securityFiscalIdentifier"),
            "company_type": profile.get("companyType") or company.get("companyType"),
        }

    def peer_candidates(
        self, symbol: str, *, credential: str | None = None,
    ) -> Mapping[str, Any]:
        """Return the documented v3 profile peer surface without persisting its payload."""
        if not credential:
            raise ValueError("credential is required")
        company = self._company(symbol, credential)
        profile = self._profile(company, credential)
        peers = tuple(item for item in (profile.get("peers") or ()) if isinstance(item, Mapping))
        for peer in peers:
            listing = peer.get("primaryListing") or {}
            provider_symbol = str(listing.get("ticker") or "").upper()
            company_key = self._company_key(peer)
            if provider_symbol:
                self._companies.setdefault(provider_symbol, peer)
            if company_key:
                self._companies_by_key.setdefault(company_key, peer)
        return {"updatedAt": profile.get("updatedAt"), "peers": peers}

    @staticmethod
    def _metric_name(row: Mapping[str, Any]) -> tuple[str, str] | None:
        metric_id = str(row.get("metric_id") or "")
        metric_name = str(row.get("metric_name") or "")
        canonical_id, canonical_name = _canon(metric_id), _canon(metric_name)
        for target, aliases in _FISCAL_METRICS.items():
            if any(
                canonical_name == _canon(alias)
                or canonical_id == _canon(alias)
                or canonical_id.endswith(_canon(alias))
                for alias in aliases
            ):
                return target, metric_id or metric_name
        return None

    @staticmethod
    def _period_start(period_end: date, frequency: str) -> date:
        if frequency == "annual":
            return date(period_end.year - 1, period_end.month, period_end.day) + timedelta(days=1)
        month = period_end.month - 2
        year = period_end.year
        if month <= 0:
            month += 12
            year -= 1
        return date(year, month, 1)

    @classmethod
    def _normalize_live_period_keys(cls, node: Any) -> Any:
        """Alias the verified live period-end field without changing legacy parsing."""
        if isinstance(node, list):
            return [cls._normalize_live_period_keys(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        normalized = {
            key: cls._normalize_live_period_keys(value) for key, value in node.items()
        }
        if normalized.get("reportDate") is not None and normalized.get("periodEndDate") is None:
            normalized["periodEndDate"] = normalized["reportDate"]
        return normalized

    def standardized_financials(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        if not credential:
            raise ValueError("credential is required")
        company = self._company(symbol, credential)
        company_key = self._company_key(company)
        profile = self._profiles.get(company_key) or {}
        reporting_currency = str(
            company.get("reportingCurrency") or profile.get("reportingCurrency") or ""
        ).strip().upper()
        return self._standardized_financials_for_key(
            company_key,
            provider_symbol=symbol,
            reporting_currency=reporting_currency,
            credential=credential,
        )

    def standardized_financials_by_company_key(
        self,
        company_key: str,
        *,
        provider_symbol: str,
        credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Read peer financials by verified opaque key; never rematch a ticker or name."""
        if not credential:
            raise ValueError("credential is required")
        normalized_key = str(company_key or "").strip()
        if not normalized_key:
            raise ValueError("company_key is required")
        company = self._companies_by_key.get(normalized_key)
        if company is None:
            self.company_lookup_records(credential=credential)
            company = self._companies_by_key.get(normalized_key)
        if company is None:
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:company-key",
                category=ProviderErrorCategory.NOT_FOUND,
                retryable=False,
                safe_message="verified Fiscal companyKey is outside the bounded lookup result",
            )
        profile = self._profiles.get(normalized_key) or {}
        reporting_currency = str(
            company.get("reportingCurrency") or profile.get("reportingCurrency") or ""
        ).strip().upper()
        return self._standardized_financials_for_key(
            normalized_key,
            provider_symbol=provider_symbol,
            reporting_currency=reporting_currency,
            credential=credential,
        )

    def _standardized_financials_for_key(
        self,
        company_key: str,
        *,
        provider_symbol: str,
        reporting_currency: str,
        credential: str,
    ) -> Iterable[Mapping[str, Any]]:
        from stock_analyser.fiscal_valuation import flatten_metric_payload

        output: list[Mapping[str, Any]] = []
        for statement in ("income-statement", "balance-sheet", "cash-flow-statement"):
            payload = self._api_get(
                f"/v1/company/financials/{statement}/standardized",
                credential=credential,
                params={"companyKey": company_key, "periodType": "annual,quarterly"},
            )
            for row in flatten_metric_payload(self._normalize_live_period_keys(payload)):
                matched = self._metric_name(row)
                frequency = str(row.get("period_type") or "").lower()
                if matched is None or frequency not in {"annual", "quarterly"}:
                    continue
                target, provider_metric = matched
                try:
                    period_end = date.fromisoformat(str(row.get("period_end"))[:10])
                    period_start_raw = row.get("period_start")
                    period_start = (
                        date.fromisoformat(str(period_start_raw)[:10])
                        if period_start_raw
                        else self._period_start(period_end, frequency)
                    )
                    fiscal_year = int(row.get("fiscal_year") or period_end.year)
                    fiscal_quarter = (
                        int(row.get("fiscal_quarter") or ((period_end.month - 1) // 3 + 1))
                        if frequency == "quarterly" else None
                    )
                    numeric_value = float(row["value"])
                    if not math.isfinite(numeric_value):
                        continue
                except (TypeError, ValueError, OverflowError):
                    continue
                sign_convention = None
                if target == "capital_expenditure_cash_outflow":
                    sign_convention = "negative_outflow" if numeric_value <= 0 else "positive_use_of_cash"
                elif target == "interest_expense":
                    sign_convention = "negative_expense" if numeric_value < 0 else "positive_expense_magnitude"
                output.append({
                    "metric": target,
                    "sourceMetric": provider_metric,
                    "periodType": frequency,
                    "periodStart": period_start.isoformat(),
                    "periodEnd": period_end.isoformat(),
                    "fiscalYear": fiscal_year,
                    "fiscalQuarter": fiscal_quarter,
                    "asOf": row.get("as_of"),
                    "value": numeric_value,
                    "currency": str(row.get("currency") or reporting_currency).strip().upper() or None,
                    "signConvention": sign_convention,
                })
        return output

    def wacc_accounting_metrics(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Read documented non-daily Fiscal debt and coverage-ratio metrics once."""
        if not credential:
            raise ValueError("credential is required")
        from stock_analyser.fiscal_valuation import flatten_metric_payload

        company = self._company(symbol, credential)
        company_key = self._company_key(company)
        profile = self._profiles.get(company_key) or {}
        reporting_currency = str(
            company.get("reportingCurrency") or profile.get("reportingCurrency") or ""
        ).strip().upper()
        ratio_ids = (
            "calculated_total_debt",
            "calculated_net_debt",
            "ratio_ebit_to_interest_expense",
        )
        payload = self._api_get(
            "/v1/company/ratios",
            credential=credential,
            params={
                "companyKey": company_key,
                "periodType": "annual,quarterly",
                "ratioId": ",".join(ratio_ids),
            },
        )
        output: list[Mapping[str, Any]] = []
        for row in flatten_metric_payload(self._normalize_live_period_keys(payload)):
            source_metric = str(row.get("metric_id") or "").strip()
            if source_metric not in ratio_ids:
                continue
            period_type = str(row.get("period_type") or "").lower()
            if period_type not in {"annual", "quarterly"}:
                continue
            try:
                period_end = date.fromisoformat(str(row.get("period_end"))[:10])
                value = float(row["value"])
                if not math.isfinite(value):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            period_start_raw = row.get("period_start")
            period_start = (
                date.fromisoformat(str(period_start_raw)[:10])
                if period_start_raw else self._period_start(period_end, period_type)
            )
            output.append({
                "sourceMetric": source_metric,
                "periodType": period_type,
                "periodStart": period_start.isoformat(),
                "periodEnd": period_end.isoformat(),
                "fiscalYear": int(row.get("fiscal_year") or period_end.year),
                "asOf": row.get("as_of"),
                "value": value,
                "currency": (
                    str(row.get("currency") or reporting_currency).strip().upper() or None
                    if source_metric != "ratio_ebit_to_interest_expense" else None
                ),
            })
        return output

    def historical_valuation_ratios(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Map the verified bare-list daily ratio response into the adapter DTO."""
        if not credential:
            raise ValueError("credential is required")
        company = self._company(symbol, credential)
        company_key = self._company_key(company)
        output: list[Mapping[str, Any]] = []
        for ratio_id in (
            "ratio_price_to_earnings",
            "ratio_ev_to_ebitda",
            "ratio_ev_to_ebit",
        ):
            payload = self._api_get(
                f"/v1/company/ratios/daily/{ratio_id}",
                credential=credential,
                params={"companyKey": company_key},
            )
            if not isinstance(payload, list):
                raise ProviderError(
                    provider=ProviderId.FISCAL,
                    endpoint_id="fiscal:daily-ratio",
                    category=ProviderErrorCategory.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="Fiscal daily ratio response was not a list",
                )
            for row in payload:
                if not isinstance(row, Mapping):
                    continue
                output.append({
                    "sourceMetric": ratio_id,
                    "observationDate": row.get("date"),
                    "value": row.get("ratio"),
                    "samplingBasis": "daily",
                })
        return output

    def enterprise_bridge_metrics(
        self, symbol: str, *, credential: str | None = None,
    ) -> Iterable[Mapping[str, Any]]:
        """Map only the live-verified Fiscal bridge and current-share schemas."""
        if not credential:
            raise ValueError("credential is required")
        company = self._company(symbol, credential)
        company_key = self._company_key(company)
        reporting_currency = str(company.get("reportingCurrency") or "USD").upper()
        output: list[Mapping[str, Any]] = []
        for ratio_id in ("calculated_tev", "calculated_market_cap"):
            payload = self._api_get(
                f"/v1/company/ratios/daily/{ratio_id}",
                credential=credential,
                params={"companyKey": company_key},
            )
            if not isinstance(payload, list):
                raise ProviderError(
                    provider=ProviderId.FISCAL,
                    endpoint_id="fiscal:daily-enterprise-bridge",
                    category=ProviderErrorCategory.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="Fiscal daily bridge response was not a list",
                )
            for row in payload:
                if not isinstance(row, Mapping):
                    continue
                observation_date = row.get("date")
                output.append({
                    "sourceMetric": ratio_id,
                    "observationDate": observation_date,
                    "asOf": f"{str(observation_date)[:10]}T00:00:00+00:00" if observation_date else None,
                    "value": row.get("ratio"),
                    "currency": reporting_currency,
                })

        shares_payload = self._api_get(
            "/v1/company/shares-outstanding",
            credential=credential,
            params={"companyKey": company_key},
        )
        if not isinstance(shares_payload, list):
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="fiscal:shares-outstanding",
                category=ProviderErrorCategory.INVALID_RESPONSE,
                retryable=False,
                safe_message="Fiscal shares-outstanding response was not a list",
            )
        for row in shares_payload:
            if not isinstance(row, Mapping):
                continue
            observation_date = row.get("date")
            output.append({
                "sourceMetric": "market_data_total_shares_outstanding",
                "observationDate": observation_date,
                "asOf": f"{str(observation_date)[:10]}T00:00:00+00:00" if observation_date else None,
                "value": row.get("totalSharesOutstanding"),
                "currency": None,
            })
        return output


def _identity_with_sec(identity: CompanyIdentity, cik: str) -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=identity.canonical_symbol,
        security_id=identity.security_id,
        issuer_id=identity.issuer_id,
        company_name=identity.company_name,
        issuer_domicile=identity.issuer_domicile,
        listing_country=identity.listing_country,
        exchange=identity.exchange,
        sector=identity.sector,
        industry=identity.industry,
        security_type=identity.security_type,
        reporting_currency=identity.reporting_currency,
        quote_currency=identity.quote_currency,
        quote_unit=identity.quote_unit,
        price_scale=identity.price_scale,
        fiscal_year_end=identity.fiscal_year_end,
        provider_symbols=identity.provider_symbols + (ProviderSymbol("sec", cik),),
        company_type=identity.company_type,
    )


def build_live_runners(
    environment: Mapping[str, str] | None = None,
) -> dict[str, SmokeRunner]:
    env = os.environ if environment is None else environment
    presence = credential_presence(env)
    runners: dict[str, SmokeRunner] = {}

    def yahoo(symbol: str) -> ProviderSmokeSummary:
        adapter = YahooAdapter(LiveYahooSource())
        identity_result = adapter.fetch_identity(symbol)
        company = _provisional_identity(symbol)
        market = adapter.fetch_market_snapshot(company)
        notes = ()
        if identity_result.candidate is not None:
            candidate = identity_result.candidate
            notes = (
                f"quote currency={candidate.quote_currency or 'unknown'}; quote unit={candidate.quote_unit or 'unknown'}; price scale={candidate.price_scale if candidate.price_scale is not None else 'unknown'}",
            )
        return _observation_summary(
            "yahoo", (identity_result.capability, *market.capabilities), market.observations,
            (*identity_result.issues, *market.issues), notes=notes,
        )

    runners["yahoo"] = yahoo

    if presence["fiscal"]:
        def fiscal(symbol: str) -> ProviderSmokeSummary:
            adapter = FiscalAdapter(
                LiveFiscalSource(), credential=_secret("fiscal", env),
            )
            identity_result = adapter.fetch_identity(symbol)
            company = _provisional_identity(symbol)
            result = adapter.fetch_standardized_actuals(company)
            history = adapter.fetch_historical_valuation(
                company, analysis_as_of=datetime.now(timezone.utc),
            )
            bridge = adapter.fetch_enterprise_bridge(
                company, analysis_as_of=datetime.now(timezone.utc),
            )
            return _observation_summary(
                "fiscal",
                (
                    identity_result.capability,
                    *result.capabilities,
                    *history.capabilities,
                    *bridge.capabilities,
                ),
                (*result.observations, *bridge.observations),
                (*identity_result.issues, *result.issues, *history.issues, *bridge.issues),
                historical_valuation=_historical_valuation_summary(
                    history.capabilities, history.observations, history.issues,
                ),
            )
        runners["fiscal"] = fiscal

    def sec(symbol: str) -> ProviderSmokeSummary:
        user_agent = env.get("SEC_USER_AGENT", "StockAnalyser/1.0 smoke-contact@example.invalid")
        source = LiveSecSource(user_agent=user_agent)
        adapter = SecAdapter(source)
        identity_result = adapter.fetch_identity(symbol)
        if identity_result.candidate is None:
            return _observation_summary(
                "sec", (identity_result.capability,), (), identity_result.issues,
            )
        result = adapter.fetch_reported_actuals(
            _identity_with_sec(_provisional_identity(symbol), identity_result.candidate.provider_symbol)
        )
        return _observation_summary(
            "sec", (identity_result.capability, *result.capabilities), result.observations,
            (*identity_result.issues, *result.issues), notes=("CIK resolved",),
        )
    runners["sec"] = sec

    if presence["fmp"]:
        def fmp(symbol: str) -> ProviderSmokeSummary:
            adapter = FmpAdapter(_retrying_transport(), credential=_secret("fmp", env))
            company = _provisional_identity(symbol)
            snapshot = datetime.now(timezone.utc)
            estimates = adapter.fetch_annual_estimates(company, source_as_of_at=snapshot)
            targets = adapter.fetch_price_targets(company, source_as_of_at=snapshot)
            dcf = adapter.fetch_standard_dcf(company, source_as_of_at=snapshot)
            capabilities = (*estimates.capabilities, *targets.capabilities, *dcf.capabilities)
            issues = (*estimates.issues, *targets.issues, *dcf.issues)
            return _observation_summary(
                "fmp", capabilities, estimates.observations, issues,
                reference_count=len(targets.references) + len(dcf.references),
                notes=("external targets and standard DCF remain reference-only",),
            )
        runners["fmp"] = fmp

    if presence["finnhub"]:
        def finnhub(symbol: str) -> ProviderSmokeSummary:
            adapter = FinnhubAdapter(_retrying_transport(), credential=_secret("finnhub", env))
            company = _provisional_identity(symbol)
            snapshot = datetime.now(timezone.utc)
            results = tuple(
                adapter.fetch_annual_estimates(company, capability, source_as_of_at=snapshot)
                for capability in (
                    ProviderCapability.ANNUAL_REVENUE_ESTIMATES,
                    ProviderCapability.ANNUAL_EBIT_ESTIMATES,
                    ProviderCapability.ANNUAL_EBITDA_ESTIMATES,
                    ProviderCapability.ANNUAL_NET_INCOME_ESTIMATES,
                    ProviderCapability.ANNUAL_EPS_ESTIMATES,
                    ProviderCapability.ANNUAL_OCF_ESTIMATES,
                    ProviderCapability.ANNUAL_CAPEX_ESTIMATES,
                    ProviderCapability.ANNUAL_FCF_ESTIMATES,
                )
            )
            return _observation_summary(
                "finnhub",
                tuple(capability for result in results for capability in result.capabilities),
                tuple(item for result in results for item in result.observations),
                tuple(issue for result in results for issue in result.issues),
                notes=("generic FCF is not promoted to FCFF or FCFE",),
            )
        runners["finnhub"] = finnhub

    if presence["alpha"]:
        def alpha(symbol: str) -> ProviderSmokeSummary:
            adapter = AlphaVantageAdapter(_retrying_transport(), credential=_secret("alpha", env))
            result = adapter.fetch_earnings_estimates(
                _provisional_identity(symbol), source_as_of_at=datetime.now(timezone.utc),
            )
            return _observation_summary(
                "alpha", result.capabilities, result.observations, result.issues,
                revision_count=len(result.revisions),
            )
        runners["alpha"] = alpha

    if presence["fred"]:
        def fred(symbol: str) -> ProviderSmokeSummary:
            del symbol
            if not _fred_credential_has_documented_format(env[_CREDENTIAL_NAMES["fred"]]):
                return ProviderSmokeSummary(
                    provider="fred",
                    normalization=NormalizationStatus.FAIL,
                    capabilities=(SafeCapabilitySummary(
                        ProviderCapability.USD_TREASURY_YIELD.value,
                        CapabilityStatus.ERROR,
                    ),),
                    notes=("configured API key does not match the documented FRED key format",),
                )
            adapter = FredAdapter(_retrying_transport(), credential=_secret("fred", env))
            result = adapter.fetch_dgs10(source_as_of_at=datetime.now(timezone.utc))
            observations = result.observations
            notes = ()
            if observations:
                latest = max(observations, key=lambda item: item.observation_date)
                notes = (
                    f"latest valid observation date={latest.observation_date.isoformat()}; decimal normalization verified",
                )
            return ProviderSmokeSummary(
                provider="fred",
                normalization=_normalization(
                    result.capabilities, (item.severity for item in result.issues), len(observations),
                ),
                capabilities=_capability_summaries(result.capabilities),
                observation_count=len(observations),
                metric_ids=tuple(sorted({item.metric.value for item in observations})),
                annual_period_count=0,
                quarterly_period_count=0,
                issue_count=len(result.issues),
                notes=notes,
            )
        runners["fred"] = fred

    return runners
