"""Explicit, secret-safe live audit of canonical peer discovery and selection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
from typing import Mapping

from stock_analyser.application.research_build_context import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
)

from stock_analyser.domain import (
    DataIssue,
    ActualSelectionStatus,
    CompanyIdentity,
    Frequency,
    IssueSeverity,
    MetricId,
    PeerCandidate,
    PeerCriterion,
    PeerCriterionStatus,
    PeerFamilyOrchestrationResult,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSet,
    PeerMultipleDistribution,
    PeerTargetValuationResult,
    PeerValuationSubset,
    Provenance,
    stable_peer_candidate_id,
)
from stock_analyser.live_smoke import LiveFiscalSource, LiveYahooSource, build_live_transport
from stock_analyser.providers import (
    FiscalAdapter,
    FmpAdapter,
    IdentityCandidate,
    ProviderId,
    ProviderLookupStatus,
    ProviderPeerCandidate,
    SecretReference,
    YahooAdapter,
    build_fiscal_summary_peer_identity,
)
from stock_analyser.services import (
    IdentitySeed,
    PeerCandidateInput,
    PeerCompanyEvidence,
    classify_peer_security_eligibility,
    evaluate_peer_candidate,
    resolve_company_identity,
    select_canonical_actual,
    select_peer_set,
    PeerFamilyOrchestrationPolicy,
    orchestrate_peer_family,
)


@dataclass(frozen=True, slots=True)
class UnresolvedLivePeerCandidate:
    provider_symbol: str
    provider_issuer_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class LivePeerCandidateContext:
    candidate_id: str
    listing_country: str | None
    identity_source: str
    identity_enrichment_missing: tuple[str, ...]
    provider_relationship: str | None
    financial_resolution_status: str
    financial_resolution_source: str | None
    financial_resolution_reason: str | None
    standardized_financial_status: str
    standardized_financial_reason: str | None
    normalized_annual_observation_count: int
    annual_revenue_status: str
    latest_annual_revenue_period: date | None
    previous_annual_revenue_period: date | None


@dataclass(frozen=True, slots=True)
class LivePeerAuditOutcome:
    symbol: str
    peer_set: PeerSet | None
    discovered_candidate_count: int
    resolved_candidate_count: int
    candidate_providers: tuple[str, ...]
    candidate_contexts: tuple[LivePeerCandidateContext, ...]
    unresolved_candidates: tuple[UnresolvedLivePeerCandidate, ...]
    issues: tuple[DataIssue, ...]
    peer_valuation_subset: PeerValuationSubset | None = None
    peer_multiple_distribution: PeerMultipleDistribution | None = None
    peer_target_valuation: PeerTargetValuationResult | None = None
    target_identity: CompanyIdentity | None = None
    peer_family_orchestration: PeerFamilyOrchestrationResult | None = None

    @property
    def succeeded(self) -> bool:
        return self.peer_set is not None


def _secret(name: str, environment: Mapping[str, str]) -> SecretReference:
    return SecretReference(name, lambda: environment[name])


def _seed_from_fiscal(symbol: str, candidate: IdentityCandidate) -> IdentitySeed:
    if not candidate.provider_issuer_id or not candidate.provider_security_id:
        raise ValueError("Fiscal identity lacks stable issuer/security identifiers")
    return IdentitySeed(
        canonical_symbol=symbol,
        security_id=f"security:fiscal:{candidate.provider_security_id}",
        issuer_id=f"issuer:fiscal:{candidate.provider_issuer_id}",
    )


def _resolve_live_identity(
    symbol: str,
    fiscal_identity: IdentityCandidate,
    yahoo_identity: IdentityCandidate | None,
    *,
    snapshot: datetime,
    include_runtime_fmp_symbol: bool = False,
):
    candidates = [fiscal_identity]
    if yahoo_identity is not None:
        candidates.append(yahoo_identity)
    if include_runtime_fmp_symbol:
        candidates.append(IdentityCandidate(
            provider=ProviderId.FMP,
            provider_symbol=symbol,
            retrieved_at=snapshot,
        ))
    return resolve_company_identity(
        _seed_from_fiscal(symbol, fiscal_identity),
        tuple(candidates),
    ).identity


def _fetch_yahoo_identity_safely(yahoo: YahooAdapter, symbol: str):
    """Discard third-party library console diagnostics; return only normalized adapter output."""
    sink = StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        return yahoo.fetch_identity(symbol)


def _canonical_candidate(
    target,
    identity,
    discovered: ProviderPeerCandidate,
) -> PeerCandidate:
    transformations = [
        "resolved stable Fiscal company/security identifiers into canonical V1 issuer/security identity",
        "retained provider rank as provenance only",
    ]
    if discovered.source_as_of_substituted:
        transformations.append("source as-of unavailable; used retrieval time explicitly")
    provenance = Provenance(
        provider=discovered.provider.value,
        endpoint_or_dataset="fiscal_v3_company_profile_peers",
        provider_symbol=discovered.provider_symbol,
        retrieved_at=discovered.retrieved_at,
        as_of_at=discovered.source_as_of,
        transformation_steps=tuple(transformations),
        source_metric="peers",
    )
    candidate_id = stable_peer_candidate_id(
        target_security_id=target.security_id,
        candidate_security_id=identity.security_id,
        provider=discovered.provider.value,
        provider_symbol=discovered.provider_symbol,
        candidate_source=discovered.candidate_source,
    )
    return PeerCandidate(
        candidate_id=candidate_id,
        target_security_id=target.security_id,
        target_issuer_id=target.issuer_id,
        candidate_security_id=identity.security_id,
        candidate_issuer_id=identity.issuer_id,
        provider=discovered.provider.value,
        provider_symbol=discovered.provider_symbol,
        canonical_symbol=identity.canonical_symbol,
        candidate_source=discovered.candidate_source,
        source_rank=discovered.source_rank,
        source_as_of=discovered.source_as_of,
        retrieved_at=discovered.retrieved_at,
        provenance=provenance,
    )


def _candidate_security_eligibility(
    identity,
    discovered: ProviderPeerCandidate,
    canonical_candidate: PeerCandidate,
    *,
    fiscal_profile: IdentityCandidate | None,
    yahoo_identity: IdentityCandidate | None,
):
    """Attach bounded classification evidence without changing Fiscal-seeded identity."""
    if fiscal_profile is not None and fiscal_profile.security_type:
        provenance = Provenance(
            provider=ProviderId.FISCAL.value,
            endpoint_or_dataset="fiscal_v3_company_profile",
            provider_symbol=fiscal_profile.provider_symbol,
            retrieved_at=fiscal_profile.retrieved_at,
            as_of_at=fiscal_profile.retrieved_at,
            transformation_steps=(
                "source as-of unavailable; used retrieval time explicitly",
                "verified full-profile stable identifiers against Fiscal peer summary",
                "used security class only as eligibility evidence; canonical identity unchanged",
            ),
            source_metric="security_type",
        )
        return classify_peer_security_eligibility(
            identity,
            security_type=fiscal_profile.security_type,
            provenance=provenance,
            listing_symbol=fiscal_profile.provider_symbol,
            exchange=fiscal_profile.exchange or discovered.exchange or "unavailable",
        )
    if discovered.security_type:
        return classify_peer_security_eligibility(
            identity,
            security_type=discovered.security_type,
            provenance=canonical_candidate.provenance,
            listing_symbol=discovered.provider_symbol,
            exchange=discovered.exchange or "unavailable",
        )
    if yahoo_identity is not None and yahoo_identity.security_type and yahoo_identity.exchange:
        provenance = Provenance(
            provider=ProviderId.YAHOO.value,
            endpoint_or_dataset="yahoo_identity_metadata",
            provider_symbol=yahoo_identity.provider_symbol,
            retrieved_at=yahoo_identity.retrieved_at,
            as_of_at=yahoo_identity.retrieved_at,
            transformation_steps=(
                "source as-of unavailable; used retrieval time explicitly",
                "checked listing symbol and exchange against the Fiscal-anchored security",
                "used security class only as eligibility evidence; canonical identity unchanged",
            ),
            source_metric="quoteType",
        )
        return classify_peer_security_eligibility(
            identity,
            security_type=yahoo_identity.security_type,
            provenance=provenance,
            listing_symbol=yahoo_identity.provider_symbol,
            exchange=yahoo_identity.exchange,
        )
    return classify_peer_security_eligibility(
        identity,
        security_type=None,
        provenance=canonical_candidate.provenance,
        listing_symbol=discovered.provider_symbol,
        exchange=discovered.exchange or "unavailable",
    )


def _annual_revenue_audit(observations, analysis_as_of: datetime):
    """Report period coverage by invoking the canonical selector unchanged."""
    rows = tuple(item for item in observations if (
        item.metric_id is MetricId.REVENUE
        and item.frequency is Frequency.ANNUAL
        and item.period_end is not None
        and item.period_end <= analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    ))
    periods = tuple(sorted({item.period_end for item in rows}, reverse=True))
    if not periods:
        return "unavailable", None, None
    selections = tuple(select_canonical_actual(
        rows,
        metric_id=MetricId.REVENUE,
        frequency=Frequency.ANNUAL,
        period_end=period_end,
        analysis_as_of=analysis_as_of,
    ) for period_end in periods[:2])
    latest = selections[0]
    if latest.status is not ActualSelectionStatus.SELECTED:
        return "unresolved", None, None
    previous_period = (
        selections[1].period_end
        if len(selections) > 1 and selections[1].status is ActualSelectionStatus.SELECTED
        else None
    )
    return "available", latest.period_end, previous_period


def _finalize_annual_revenue_contexts(
    contexts,
    candidate_actuals,
    *,
    analysis_as_of: datetime,
):
    """Apply one post-retrieval cutoff to safe audit context and peer selection."""
    return tuple(
        replace(
            context,
            annual_revenue_status=revenue_status,
            latest_annual_revenue_period=latest_period,
            previous_annual_revenue_period=previous_period,
        )
        for context in contexts
        for revenue_status, latest_period, previous_period in (
            _annual_revenue_audit(
                candidate_actuals[context.candidate_id],
                analysis_as_of,
            ),
        )
    )


def _industry_is_definitive_mismatch(
    target: PeerCompanyEvidence,
    candidate_input: PeerCandidateInput,
    *,
    analysis_as_of: datetime,
) -> bool:
    preliminary = evaluate_peer_candidate(
        target,
        candidate_input,
        analysis_as_of=analysis_as_of,
    )
    industry = next(
        item for item in preliminary.evidence.criteria
        if item.criterion is PeerCriterion.INDUSTRY
    )
    return (
        industry.status is PeerCriterionStatus.FAIL
        and PeerSelectionReason.INDUSTRY_MISMATCH in preliminary.reasons
    )


def run_live_peer_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    build_context: ResearchBuildContext | None = None,
    target_identity: CompanyIdentity | None = None,
) -> LivePeerAuditOutcome:
    """Run the documented Fiscal discovery path and canonical evidence checks once."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    snapshot = analysis_as_of or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    missing = tuple(name for name in ("FISCAL_API_KEY", "FMP_API_KEY") if not environment.get(name))
    if missing:
        return LivePeerAuditOutcome(
            normalized_symbol, None, 0, 0, (), (), (),
            (DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric="live_peer_audit",
                provider="live_audit",
                reason=f"required credential configuration is unavailable: {', '.join(missing)}",
            ),),
        )

    fiscal_source = LiveFiscalSource()
    fiscal = FiscalAdapter(
        fiscal_source,
        credential=_secret("FISCAL_API_KEY", environment),
    )
    yahoo = YahooAdapter(LiveYahooSource())
    fmp = FmpAdapter(
        build_live_transport(),
        credential=_secret("FMP_API_KEY", environment),
    )
    issues: list[DataIssue] = []

    if target_identity is None:
        fiscal_key = NormalizedEvidenceKey("fiscal", "identity", f"symbol:{normalized_symbol}", snapshot)
        yahoo_key = NormalizedEvidenceKey("yahoo", "identity", f"symbol:{normalized_symbol}", snapshot)
        target_fiscal = (
            build_context.get_or_create_normalized(
                fiscal_key, lambda: fiscal.fetch_identity(normalized_symbol),
            ) if build_context else fiscal.fetch_identity(normalized_symbol)
        )
        target_yahoo = (
            build_context.get_or_create_normalized(
                yahoo_key, lambda: _fetch_yahoo_identity_safely(yahoo, normalized_symbol),
            ) if build_context else _fetch_yahoo_identity_safely(yahoo, normalized_symbol)
        )
        issues.extend((*target_fiscal.issues, *target_yahoo.issues))
        if target_fiscal.candidate is None:
            return LivePeerAuditOutcome(
                normalized_symbol, None, 0, 0, (ProviderId.FISCAL.value,), (), (), tuple(issues),
            )
        try:
            target_identity = _resolve_live_identity(
                normalized_symbol,
                target_fiscal.candidate,
                target_yahoo.candidate,
                snapshot=snapshot,
                include_runtime_fmp_symbol=True,
            )
        except ValueError as error:
            issues.append(DataIssue(
                severity=IssueSeverity.BLOCKING,
                metric="peer_target_identity",
                provider="identity_resolver",
                reason=str(error),
            ))
            return LivePeerAuditOutcome(
                normalized_symbol, None, 0, 0, (ProviderId.FISCAL.value,), (), (), tuple(issues),
            )

    target_id = f"{target_identity.security_id}|{target_identity.issuer_id}"
    actuals_key = NormalizedEvidenceKey("fiscal", "standardized_actuals", target_id, snapshot)
    forward_key = NormalizedEvidenceKey(
        "fmp", "annual_estimates", target_id, snapshot, (("frequency", "annual"),),
    )
    # Peer discovery candidates intentionally retain an opaque provider lookup
    # field for the adapter's immediate follow-up.  They therefore never enter
    # the normalized per-build registry.
    discovery = fiscal.fetch_peer_candidates(target_identity)
    issues.extend(discovery.issues)
    target_actuals = (
        build_context.get_or_create_normalized(
            actuals_key,
            lambda: fiscal.fetch_standardized_actuals(target_identity, analysis_as_of=snapshot),
        ) if build_context else fiscal.fetch_standardized_actuals(target_identity)
    )
    target_forward = (
        build_context.get_or_create_normalized(
            forward_key,
            lambda: fmp.fetch_annual_estimates(target_identity, source_as_of_at=snapshot),
        ) if build_context else fmp.fetch_annual_estimates(target_identity, source_as_of_at=snapshot)
    )
    issues.extend((*target_actuals.issues, *target_forward.issues))
    target_evidence = PeerCompanyEvidence(
        identity=target_identity,
        actual_observations=target_actuals.observations,
        forward_observations=target_forward.observations,
    )

    candidate_inputs: list[PeerCandidateInput] = []
    contexts: list[LivePeerCandidateContext] = []
    candidate_actuals: dict[str, tuple] = {}
    unresolved: list[UnresolvedLivePeerCandidate] = []
    for discovered in discovery.candidates:
        try:
            summary_identity = build_fiscal_summary_peer_identity(discovered)
        except ValueError as error:
            unresolved.append(UnresolvedLivePeerCandidate(
                discovered.provider_symbol,
                discovered.provider_issuer_id,
                str(error),
            ))
            continue
        fiscal_identity = fiscal.fetch_identity(discovered.provider_symbol)
        issues.extend(fiscal_identity.issues)
        profile = fiscal_identity.candidate
        identity = summary_identity
        forward_observations = ()
        yahoo_candidate = None
        if profile is not None:
            if (
                profile.provider_issuer_id != discovered.provider_issuer_id
                or profile.provider_security_id != discovered.provider_security_id
            ):
                unresolved.append(UnresolvedLivePeerCandidate(
                    discovered.provider_symbol,
                    discovered.provider_issuer_id,
                    "Fiscal full profile identifiers do not match the verified peer summary",
                ))
                continue
            yahoo_identity = _fetch_yahoo_identity_safely(yahoo, discovered.provider_symbol)
            issues.extend(yahoo_identity.issues)
            yahoo_candidate = yahoo_identity.candidate
            try:
                enriched_identity = _resolve_live_identity(
                    discovered.provider_symbol,
                    profile,
                    yahoo_candidate,
                    snapshot=snapshot,
                    include_runtime_fmp_symbol=False,
                )
            except ValueError:
                # An incomplete optional profile never erases sufficient summary identity.
                pass
            else:
                identity = enriched_identity
        else:
            yahoo_identity = _fetch_yahoo_identity_safely(yahoo, discovered.provider_symbol)
            issues.extend(yahoo_identity.issues)
            yahoo_candidate = yahoo_identity.candidate
        canonical_candidate = _canonical_candidate(target_identity, identity, discovered)
        security_eligibility = _candidate_security_eligibility(
            summary_identity,
            discovered,
            canonical_candidate,
            fiscal_profile=profile,
            yahoo_identity=yahoo_candidate,
        )
        empty_input = PeerCandidateInput(
            candidate=canonical_candidate,
            company=PeerCompanyEvidence(
                identity=identity,
                security_eligibility=security_eligibility,
            ),
        )
        if _industry_is_definitive_mismatch(
            target_evidence,
            empty_input,
            analysis_as_of=snapshot,
        ):
            financial_resolution_status = "not_evaluated"
            financial_resolution_source = None
            financial_resolution_reason = "definitive industry mismatch; financial quota not consumed"
            standardized_financial_status = "not_evaluated"
            standardized_financial_reason = "definitive industry mismatch; financial quota not consumed"
            actual_observations = ()
        else:
            resolution = fiscal.resolve_peer_financial_lookup(discovered)
            financial_resolution_status = resolution.status.value
            financial_resolution_source = resolution.source_dataset
            financial_resolution_reason = resolution.reason
            if resolution.status is ProviderLookupStatus.AVAILABLE:
                actuals = fiscal.fetch_peer_standardized_actuals(summary_identity, resolution)
                issues.extend(actuals.issues)
                actual_observations = actuals.observations
                standardized_financial_status = actuals.capabilities[0].status.value
                standardized_financial_reason = actuals.capabilities[0].reason
            else:
                actual_observations = ()
                standardized_financial_status = "not_evaluated"
                standardized_financial_reason = "stable-ID financial resolution unavailable"
        normalized_annual_observation_count = sum(
            item.frequency is Frequency.ANNUAL for item in actual_observations
        )
        candidate_actuals[canonical_candidate.candidate_id] = tuple(actual_observations)
        candidate_inputs.append(PeerCandidateInput(
            candidate=canonical_candidate,
            company=PeerCompanyEvidence(
                identity=identity,
                actual_observations=actual_observations,
                forward_observations=forward_observations,
                security_eligibility=security_eligibility,
            ),
        ))
        evidence_identity = (
            identity if hasattr(identity, "status") else None
        )
        contexts.append(LivePeerCandidateContext(
            candidate_id=canonical_candidate.candidate_id,
            listing_country=identity.listing_country,
            identity_source=(
                evidence_identity.status.value if evidence_identity is not None else "profile_verified"
            ),
            identity_enrichment_missing=(
                evidence_identity.unavailable_enrichment_fields if evidence_identity is not None else ()
            ),
            provider_relationship=discovered.provider_relationship,
            financial_resolution_status=financial_resolution_status,
            financial_resolution_source=financial_resolution_source,
            financial_resolution_reason=financial_resolution_reason,
            standardized_financial_status=standardized_financial_status,
            standardized_financial_reason=standardized_financial_reason,
            normalized_annual_observation_count=normalized_annual_observation_count,
            annual_revenue_status="not_evaluated",
            latest_annual_revenue_period=None,
            previous_annual_revenue_period=None,
        ))

    effective_analysis_as_of = analysis_as_of or datetime.now(timezone.utc)
    contexts = list(_finalize_annual_revenue_contexts(
        contexts,
        candidate_actuals,
        analysis_as_of=effective_analysis_as_of,
    ))
    peer_set = select_peer_set(
        target_evidence,
        candidate_inputs,
        analysis_as_of=effective_analysis_as_of,
        provider_capabilities=discovery.capabilities,
    )
    orchestration = orchestrate_peer_family(
        target_identity,
        peer_set,
        analysis_as_of=effective_analysis_as_of,
        policy=PeerFamilyOrchestrationPolicy(unavailable_is_expected=True),
    )
    return LivePeerAuditOutcome(
        normalized_symbol,
        peer_set,
        len(discovery.candidates),
        len(candidate_inputs),
        tuple(sorted({item.provider.value for item in discovery.candidates})),
        tuple(contexts),
        tuple(unresolved),
        tuple(dict.fromkeys(issues)),
        orchestration.peer_subset,
        orchestration.peer_distribution,
        orchestration.target_peer_valuation,
        target_identity,
        orchestration,
    )


def render_live_peer_audit(outcome: LivePeerAuditOutcome) -> str:
    lines = [
        "V1 LIVE PEER DISCOVERY AUDIT",
        f"Target symbol: {outcome.symbol}",
        f"Candidate providers: {', '.join(outcome.candidate_providers) or 'unavailable'}",
        f"Discovered candidates: {outcome.discovered_candidate_count}",
        f"Canonical identities resolved: {outcome.resolved_candidate_count}",
    ]
    if outcome.target_identity is not None:
        lines.extend((
            f"Target canonical security: {outcome.target_identity.security_id}",
            f"Target canonical issuer: {outcome.target_identity.issuer_id}",
            f"Target reporting currency: {outcome.target_identity.reporting_currency}",
            f"Target quote currency: {outcome.target_identity.quote_currency}",
            f"Target quote unit: {outcome.target_identity.quote_unit}",
            f"Target quote-price scale: {outcome.target_identity.price_scale}",
        ))
    if outcome.peer_set is None:
        lines.append("Peer-set status: UNAVAILABLE")
        lines.append(f"Safe issue count: {len(outcome.issues)}")
        lines.extend(
            f"Safe issue: {item.metric} - {item.reason}"
            for item in outcome.issues
        )
        return "\n".join(lines)

    peer_set = outcome.peer_set
    contexts = {item.candidate_id: item for item in outcome.candidate_contexts}
    readiness = {item.candidate_id: item for item in peer_set.method_data_readiness}
    for selection in peer_set.selections:
        criteria = {item.criterion.value: item for item in selection.evidence.criteria}
        industry = criteria["industry"]
        security = criteria["security_type"]
        security_eligibility = selection.evidence.security_eligibility
        scale = criteria["scale"]
        growth = criteria["growth"]
        margin = criteria["margin"]
        business_model = criteria["business_model"]
        context = contexts[selection.candidate.candidate_id]
        method = readiness[selection.candidate.candidate_id]
        lines.extend((
            "",
            f"Candidate: {selection.candidate.canonical_symbol}",
            f"Canonical issuer: {selection.candidate.candidate_issuer_id}",
            f"Canonical security: {selection.candidate.candidate_security_id}",
            f"Canonical identity status: {context.identity_source}",
            (
                "Identity enrichment: complete"
                if not context.identity_enrichment_missing
                else f"Identity enrichment: partial (missing {', '.join(context.identity_enrichment_missing)})"
            ),
            f"Provider source: {selection.candidate.provider} / {selection.candidate.candidate_source.value}",
            f"Provider symbol: {selection.candidate.provider_symbol}",
            f"Provider source rank (provenance only): {selection.candidate.source_rank or 'unavailable'}",
            f"Security type: {security.candidate_value or 'unavailable'}",
            f"Security eligibility status: {security_eligibility.status.value} ({security.status.value})",
            (
                "Security eligibility source: "
                f"{security_eligibility.provider} / {security_eligibility.provenance.endpoint_or_dataset}"
            ),
            f"Listing country: {context.listing_country or 'unavailable'}",
            f"Industry evidence: {selection.evidence.industry_comparability.value} ({industry.candidate_value or 'unavailable'})",
            f"Business-model evidence: {business_model.status.value}; provider relationship={context.provider_relationship or 'unavailable'} (provenance only)",
            f"Fiscal financial resolver status: {context.financial_resolution_status}",
            f"Fiscal financial resolver source: {context.financial_resolution_source or 'unavailable'}",
            f"Fiscal financial resolver reason: {context.financial_resolution_reason or 'none'}",
            f"Fiscal standardized-financial capability: {context.standardized_financial_status}",
            f"Fiscal standardized-financial reason: {context.standardized_financial_reason or 'none'}",
            f"Normalized annual observations: {context.normalized_annual_observation_count}",
            f"Canonical annual revenue evidence: {context.annual_revenue_status}",
            f"Latest selected annual revenue period: {context.latest_annual_revenue_period or 'unavailable'}",
            f"Previous selected annual revenue period: {context.previous_annual_revenue_period or 'unavailable'}",
            (
                "Financial-comparability evidence availability: "
                f"scale={scale.status.value}, growth={growth.status.value}, margin={margin.status.value}"
            ),
            f"Economic peer selection status: {selection.status.value}",
            f"Economic peer selection reasons: {', '.join(reason.value for reason in selection.reasons)}",
            f"EV/EBITDA forward denominator available: {str(method.forward_denominator_available).lower()}",
            f"EV/EBITDA required multiple inputs available: {str(method.required_multiple_inputs_available).lower()}",
            f"EV/EBITDA method-data readiness: {method.status.value}",
            f"EV/EBITDA readiness reasons: {', '.join(reason.value for reason in method.reasons)}",
        ))
    for item in outcome.unresolved_candidates:
        lines.extend((
            "",
            f"Candidate: {item.provider_symbol}",
            f"Canonical issuer: unresolved ({item.provider_issuer_id})",
            "Selection status: unavailable",
            f"Selection reasons: identity_mismatch ({item.reason})",
        ))

    counts = {
        status: sum(item.status is status for item in peer_set.selections)
        for status in PeerSelectionStatus
    }
    security_eligible_count = sum(
        selection.evidence.criterion(PeerCriterion.SECURITY_TYPE).status is PeerCriterionStatus.PASS
        for selection in peer_set.selections
    )
    lines.extend((
        "",
        f"Included peer count: {counts[PeerSelectionStatus.INCLUDED]}",
        f"Security-eligible candidate count: {security_eligible_count}",
        f"Excluded count: {counts[PeerSelectionStatus.EXCLUDED]}",
        f"Unverified count: {counts[PeerSelectionStatus.UNVERIFIED]}",
        f"Unavailable identity count: {len(outcome.unresolved_candidates)}",
        f"Minimum independent issuers required: {peer_set.minimum_required_peers}",
        f"Peer-set status: {peer_set.status.value}",
        (
            "EV/EBITDA peer valuation observations: "
            f"{outcome.peer_valuation_subset.valid_observation_count}"
            if outcome.peer_valuation_subset is not None
            else "EV/EBITDA peer valuation observations: unavailable"
        ),
        (
            "EV/EBITDA valuation-subset status: "
            f"{outcome.peer_valuation_subset.status.value}"
            if outcome.peer_valuation_subset is not None
            else "EV/EBITDA valuation-subset status: unavailable"
        ),
        (
            "EV/EBITDA minimum valid independent issuers required: "
            f"{outcome.peer_valuation_subset.minimum_required_valid_observations}"
            if outcome.peer_valuation_subset is not None
            else "EV/EBITDA minimum valid independent issuers required: unavailable"
        ),
        (
            "Peer EV/EBITDA distribution status: "
            f"{outcome.peer_multiple_distribution.status.value}"
            if outcome.peer_multiple_distribution is not None
            else "Peer EV/EBITDA distribution status: unavailable"
        ),
        (
            "Peer EV/EBITDA distribution sample count: "
            f"{outcome.peer_multiple_distribution.sample_count}"
            if outcome.peer_multiple_distribution is not None
            else "Peer EV/EBITDA distribution sample count: unavailable"
        ),
        (
            "Peer EV/EBITDA P25: "
            f"{outcome.peer_multiple_distribution.p25}"
            if outcome.peer_multiple_distribution is not None and outcome.peer_multiple_distribution.p25 is not None
            else "Peer EV/EBITDA P25: unavailable"
        ),
        (
            "Peer EV/EBITDA median: "
            f"{outcome.peer_multiple_distribution.median}"
            if outcome.peer_multiple_distribution is not None and outcome.peer_multiple_distribution.median is not None
            else "Peer EV/EBITDA median: unavailable"
        ),
        (
            "Peer EV/EBITDA P75: "
            f"{outcome.peer_multiple_distribution.p75}"
            if outcome.peer_multiple_distribution is not None and outcome.peer_multiple_distribution.p75 is not None
            else "Peer EV/EBITDA P75: unavailable"
        ),
        (
            "Target peer EV/EBITDA valuation status: "
            f"{outcome.peer_target_valuation.status.value}"
            if outcome.peer_target_valuation is not None
            else "Target peer EV/EBITDA valuation status: not evaluated"
        ),
        (
            "Target peer EV/EBITDA valuation reason: "
            f"{outcome.peer_target_valuation.issues[0].reason}"
            if outcome.peer_target_valuation is not None and outcome.peer_target_valuation.issues
            else "Target peer EV/EBITDA valuation reason: none"
        ),
        f"Policy ID: {peer_set.policy_id}",
        f"Safe issue count: {len(outcome.issues)}",
        *tuple(
            f"Safe issue: {item.metric} - {item.reason}"
            for item in outcome.issues
        ),
        "No unavailable peer distribution was applied; no current-price comparison, aggregation, or stance was calculated.",
    ))
    if outcome.peer_family_orchestration is not None:
        orchestration = outcome.peer_family_orchestration
        lines.extend((
            f"Peer-family status: {orchestration.status.value}",
            f"Peer-family failure stage: {orchestration.failure_stage.value}",
            f"Peer-family blocking reasons: {', '.join(orchestration.blocking_reasons) or 'none'}",
            f"Peer-family orchestration ID: {orchestration.orchestration_id}",
        ))
    return "\n".join(lines)
