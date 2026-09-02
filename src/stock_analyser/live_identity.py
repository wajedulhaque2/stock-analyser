"""One approved, secret-safe live target-identity boundary for V1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import os

from stock_analyser.application.live_configuration import LiveConfigurationSource
from stock_analyser.application.research_build_context import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
)
from stock_analyser.domain import CompanyIdentity, DataIssue
from stock_analyser.live_smoke import LiveFiscalSource, LiveYahooSource
from stock_analyser.providers import (
    FiscalAdapter,
    IdentityCandidate,
    ProviderId,
    ProviderIdentityResult,
    SecretReference,
    YahooAdapter,
)
from stock_analyser.services import IdentitySeed, resolve_company_identity


class IdentityDiagnosticStage(str, Enum):
    CONFIGURATION = "configuration"
    CLIENT_CONSTRUCTION = "client_construction"
    REQUEST = "request"
    HTTP_RESPONSE = "http_response"
    PARSE = "parse"
    NORMALIZATION = "normalization"
    CANONICAL_ASSEMBLY = "canonical_assembly"
    COMPLETE = "complete"


class IdentityDiagnosticStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IdentityConfigurationStatus(str, Enum):
    CONFIGURED = "configured"
    MISSING = "missing"


class IdentityClientStatus(str, Enum):
    NOT_CONSTRUCTED = "not_constructed"
    CONSTRUCTED = "constructed"
    FAILED = "failed"


class IdentityHttpStatusCategory(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    SUCCESS = "success"
    AUTHENTICATION = "authentication"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TRANSPORT = "transport"
    OTHER_FAILURE = "other_failure"


class IdentityCandidateResolutionStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    BOUND_EXHAUSTED = "bound_exhausted"


@dataclass(frozen=True, slots=True)
class IdentityLiveDiagnostic:
    provider: str
    configuration_status: IdentityConfigurationStatus
    configuration_source: LiveConfigurationSource
    client_status: IdentityClientStatus
    request_attempted: bool
    http_status_category: IdentityHttpStatusCategory
    parse_status: IdentityDiagnosticStatus
    normalization_status: IdentityDiagnosticStatus
    canonical_identity_status: IdentityDiagnosticStatus
    blocking_stage: IdentityDiagnosticStage
    safe_reason: str
    candidate_count: int = 0
    pages_examined: int = 0
    venue_evidence_found: bool = False
    candidate_resolution_status: IdentityCandidateResolutionStatus = (
        IdentityCandidateResolutionStatus.NOT_ATTEMPTED
    )
    profile_enrichment_attempted: bool = False
    bounded_search_exhausted: bool = False

    def __post_init__(self) -> None:
        if self.provider != ProviderId.FISCAL.value:
            raise ValueError("identity diagnostic provider must be Fiscal")
        reason = str(self.safe_reason).strip()
        if not reason:
            raise ValueError("safe_reason must be non-empty")
        forbidden = (
            "api_key", "apikey", "authorization", "bearer ", "companykey",
            "http://", "https://", "x_api_key", "headers", "payload", "response body",
        )
        normalized_reason = reason.lower().replace("-", "_")
        if any(token in normalized_reason for token in forbidden):
            raise ValueError("safe_reason contains forbidden request metadata")
        if self.candidate_count < 0 or self.pages_examined < 0:
            raise ValueError("identity acquisition counts must be non-negative")
        object.__setattr__(self, "safe_reason", reason)


@dataclass(frozen=True, slots=True)
class LiveIdentityResolution:
    identity: CompanyIdentity | None
    fiscal_result: ProviderIdentityResult | None
    yahoo_result: ProviderIdentityResult | None
    issues: tuple[DataIssue, ...]
    diagnostic: IdentityLiveDiagnostic


class _IdentityDiagnosticRecorder:
    """Mutable request-local recorder; only its immutable safe snapshot escapes."""

    def __init__(self, source: LiveConfigurationSource) -> None:
        self.configuration_source = source
        self.configuration_status = IdentityConfigurationStatus.MISSING
        self.client_status = IdentityClientStatus.NOT_CONSTRUCTED
        self.request_was_attempted = False
        self.http_category = IdentityHttpStatusCategory.NOT_ATTEMPTED
        self.parse_status = IdentityDiagnosticStatus.NOT_ATTEMPTED
        self.normalization_status = IdentityDiagnosticStatus.NOT_ATTEMPTED
        self.canonical_status = IdentityDiagnosticStatus.NOT_ATTEMPTED
        self.blocking_stage = IdentityDiagnosticStage.CONFIGURATION
        self.safe_reason = "Fiscal identity configuration is unavailable."
        self.candidate_count = 0
        self.pages_examined = 0
        self.venue_evidence_found = False
        self.candidate_resolution_status = IdentityCandidateResolutionStatus.NOT_ATTEMPTED
        self.profile_enrichment_attempted = False
        self.bounded_search_exhausted = False

    def request_attempted(self) -> None:
        self.request_was_attempted = True
        self.blocking_stage = IdentityDiagnosticStage.REQUEST
        self.safe_reason = "Fiscal identity request did not complete."

    def request_failed(self) -> None:
        self.http_category = IdentityHttpStatusCategory.TRANSPORT
        self.blocking_stage = IdentityDiagnosticStage.REQUEST
        self.safe_reason = "Fiscal identity request failed before an HTTP result was available."

    def http_response(self, status_code: int) -> None:
        self.http_category = {
            401: IdentityHttpStatusCategory.AUTHENTICATION,
            403: IdentityHttpStatusCategory.ACCESS_DENIED,
            404: IdentityHttpStatusCategory.NOT_FOUND,
            429: IdentityHttpStatusCategory.RATE_LIMIT,
        }.get(
            status_code,
            IdentityHttpStatusCategory.SUCCESS if 200 <= status_code < 300
            else IdentityHttpStatusCategory.SERVER if status_code >= 500
            else IdentityHttpStatusCategory.OTHER_FAILURE,
        )
        if self.http_category is not IdentityHttpStatusCategory.SUCCESS:
            self.blocking_stage = IdentityDiagnosticStage.HTTP_RESPONSE
            self.safe_reason = "Fiscal identity request returned a non-success access category."

    def parse_completed(self, succeeded: bool) -> None:
        self.parse_status = (
            IdentityDiagnosticStatus.SUCCEEDED if succeeded else IdentityDiagnosticStatus.FAILED
        )
        if not succeeded:
            self.blocking_stage = IdentityDiagnosticStage.PARSE
            self.safe_reason = "Fiscal identity result could not be parsed as JSON."

    def identity_acquisition_updated(
        self,
        *,
        candidate_count: int,
        pages_examined: int,
        venue_evidence_found: bool,
        candidate_resolution_status: str,
        profile_enrichment_attempted: bool,
        bounded_search_exhausted: bool,
    ) -> None:
        self.candidate_count = candidate_count
        self.pages_examined = pages_examined
        self.venue_evidence_found = venue_evidence_found
        self.candidate_resolution_status = IdentityCandidateResolutionStatus(
            candidate_resolution_status
        )
        self.profile_enrichment_attempted = profile_enrichment_attempted
        self.bounded_search_exhausted = bounded_search_exhausted

    def snapshot(self) -> IdentityLiveDiagnostic:
        return IdentityLiveDiagnostic(
            provider=ProviderId.FISCAL.value,
            configuration_status=self.configuration_status,
            configuration_source=(
                self.configuration_source
                if self.configuration_status is IdentityConfigurationStatus.CONFIGURED
                else LiveConfigurationSource.MISSING
            ),
            client_status=self.client_status,
            request_attempted=self.request_was_attempted,
            http_status_category=self.http_category,
            parse_status=self.parse_status,
            normalization_status=self.normalization_status,
            canonical_identity_status=self.canonical_status,
            blocking_stage=self.blocking_stage,
            safe_reason=self.safe_reason,
            candidate_count=self.candidate_count,
            pages_examined=self.pages_examined,
            venue_evidence_found=self.venue_evidence_found,
            candidate_resolution_status=self.candidate_resolution_status,
            profile_enrichment_attempted=self.profile_enrichment_attempted,
            bounded_search_exhausted=self.bounded_search_exhausted,
        )


def _configuration_source(
    environment: Mapping[str, str],
    supplied: LiveConfigurationSource | None,
) -> LiveConfigurationSource:
    if supplied is not None:
        return supplied
    return (
        LiveConfigurationSource.ENVIRONMENT
        if environment is os.environ else LiveConfigurationSource.EXPLICIT_INJECTED
    )


def _identity_seed(symbol: str, fiscal: IdentityCandidate) -> IdentitySeed:
    if not fiscal.provider_issuer_id or not fiscal.provider_security_id:
        raise ValueError("Fiscal identity lacks stable issuer/security identifiers")
    return IdentitySeed(
        canonical_symbol=symbol,
        security_id=f"security:fiscal:{fiscal.provider_security_id}",
        issuer_id=f"issuer:fiscal:{fiscal.provider_issuer_id}",
    )


def assemble_live_identity(
    symbol: str,
    fiscal: IdentityCandidate,
    candidates: tuple[IdentityCandidate, ...],
) -> CompanyIdentity:
    """Use the approved deterministic resolver with Fiscal stable-ID anchoring."""
    return resolve_company_identity(_identity_seed(symbol, fiscal), candidates).identity


def build_live_fiscal_adapter(
    environment: Mapping[str, str],
    *,
    clock=None,
    observer=None,
) -> FiscalAdapter:
    """Construct the approved Fiscal client without exposing credential values."""
    return FiscalAdapter(
        LiveFiscalSource(observer=observer),
        credential=SecretReference(
            "FISCAL_API_KEY", lambda: environment["FISCAL_API_KEY"],
        ),
        clock=clock,
    )


def resolve_live_target_identity(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime,
    build_context: ResearchBuildContext | None = None,
    configuration_source: LiveConfigurationSource | None = None,
) -> LiveIdentityResolution:
    """Resolve Fiscal-anchored identity once for direct and coordinator paths."""
    source = _configuration_source(environment, configuration_source)
    recorder = _IdentityDiagnosticRecorder(source)
    credential_configured = bool(str(environment.get("FISCAL_API_KEY", "")).strip())
    if not credential_configured:
        return LiveIdentityResolution(None, None, None, (), recorder.snapshot())
    recorder.configuration_status = IdentityConfigurationStatus.CONFIGURED
    recorder.blocking_stage = IdentityDiagnosticStage.CLIENT_CONSTRUCTION
    recorder.safe_reason = "Fiscal identity client could not be constructed."
    try:
        fiscal = build_live_fiscal_adapter(
            environment, clock=lambda: analysis_as_of, observer=recorder,
        )
    except Exception:
        recorder.client_status = IdentityClientStatus.FAILED
        return LiveIdentityResolution(None, None, None, (), recorder.snapshot())
    recorder.client_status = IdentityClientStatus.CONSTRUCTED

    normalized_symbol = symbol.strip().upper()
    fiscal_key = NormalizedEvidenceKey(
        "fiscal", "identity", f"symbol:{normalized_symbol}", analysis_as_of,
    )
    fiscal_result = (
        build_context.get_or_create_normalized(
            fiscal_key, lambda: fiscal.fetch_identity(normalized_symbol),
        ) if build_context else fiscal.fetch_identity(normalized_symbol)
    )
    if fiscal_result.candidate is None:
        if recorder.http_category is IdentityHttpStatusCategory.SUCCESS:
            if recorder.parse_status is IdentityDiagnosticStatus.FAILED:
                recorder.blocking_stage = IdentityDiagnosticStage.PARSE
            else:
                recorder.normalization_status = IdentityDiagnosticStatus.FAILED
                recorder.blocking_stage = IdentityDiagnosticStage.NORMALIZATION
                recorder.safe_reason = "Fiscal identity evidence could not be normalized."
        elif not recorder.request_was_attempted:
            recorder.blocking_stage = IdentityDiagnosticStage.REQUEST
            recorder.safe_reason = "Fiscal identity request was not attempted."
        return LiveIdentityResolution(
            None, fiscal_result, None, tuple(fiscal_result.issues), recorder.snapshot(),
        )
    recorder.normalization_status = IdentityDiagnosticStatus.SUCCEEDED

    yahoo = YahooAdapter(LiveYahooSource())
    yahoo_key = NormalizedEvidenceKey(
        "yahoo", "identity", f"symbol:{normalized_symbol}", analysis_as_of,
    )
    yahoo_result = (
        build_context.get_or_create_normalized(
            yahoo_key, lambda: yahoo.fetch_identity(normalized_symbol),
        ) if build_context else yahoo.fetch_identity(normalized_symbol)
    )
    candidates = tuple(item for item in (
        fiscal_result.candidate,
        yahoo_result.candidate,
        IdentityCandidate(
            provider=ProviderId.FMP,
            provider_symbol=normalized_symbol,
            retrieved_at=analysis_as_of,
        ),
    ) if item is not None)
    recorder.blocking_stage = IdentityDiagnosticStage.CANONICAL_ASSEMBLY
    recorder.safe_reason = "Canonical identity assembly failed closed."
    try:
        identity = assemble_live_identity(
            normalized_symbol, fiscal_result.candidate, candidates,
        )
    except (TypeError, ValueError):
        recorder.canonical_status = IdentityDiagnosticStatus.FAILED
        return LiveIdentityResolution(
            None,
            fiscal_result,
            yahoo_result,
            tuple(dict.fromkeys((*fiscal_result.issues, *yahoo_result.issues))),
            recorder.snapshot(),
        )
    recorder.canonical_status = IdentityDiagnosticStatus.SUCCEEDED
    recorder.blocking_stage = IdentityDiagnosticStage.COMPLETE
    recorder.safe_reason = "Canonical Fiscal-anchored identity completed."
    if build_context is not None:
        build_context.register_normalized(
            NormalizedEvidenceKey(
                "canonical", "target_identity", identity.security_id, analysis_as_of,
                (("issuer_id", identity.issuer_id),),
            ),
            identity,
        )
    return LiveIdentityResolution(
        identity,
        fiscal_result,
        yahoo_result,
        tuple(dict.fromkeys((*fiscal_result.issues, *yahoo_result.issues))),
        recorder.snapshot(),
    )
