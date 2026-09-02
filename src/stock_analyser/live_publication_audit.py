"""Explicit, secret-safe live audit for Milestone 11A valuation publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from stock_analyser.application.live_configuration import LiveConfigurationSource
from stock_analyser.application.research_build_context import (
    ResearchBuildContext,
    ResearchBuildStageStatus,
)
from stock_analyser.application.v1_research import ResearchReportPipelineStage
from stock_analyser.domain import AggregationStatus, ValuationPublicationResult
from stock_analyser.live_own_history_audit import LiveOwnHistoryAuditOutcome, run_live_own_history_audit
from stock_analyser.live_peer_audit import LivePeerAuditOutcome
from stock_analyser.live_peer_family_audit import run_live_peer_family_audit
from stock_analyser.services import (
    family_evidence_from_own_history,
    family_evidence_from_peer_orchestration,
    publish_cross_family_valuation,
)


@dataclass(frozen=True, slots=True)
class LivePublicationAuditOutcome:
    symbol: str
    analysis_as_of: datetime
    own_history: LiveOwnHistoryAuditOutcome | None
    peer_family: LivePeerAuditOutcome | None
    publication: ValuationPublicationResult
    reverse_dcf_canonical_status: str
    reverse_dcf_scenario_status: str
    safe_notes: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.publication.publication_status in set(AggregationStatus)


def run_live_publication_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    build_context: ResearchBuildContext | None = None,
    identity_configuration_source: LiveConfigurationSource | None = None,
) -> LivePublicationAuditOutcome:
    """Run each approved family audit once and publish reconstructed canonical results."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    snapshot = analysis_as_of or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")

    own_outcome = run_live_own_history_audit(
        normalized_symbol,
        environment=environment,
        analysis_as_of=snapshot,
        build_context=build_context,
        identity_configuration_source=identity_configuration_source,
    )
    canonical_identity = own_outcome.result.identity if own_outcome.result is not None else None
    peer_token = build_context.start_stage(ResearchReportPipelineStage.PEER) if build_context else None
    if canonical_identity is None and build_context is not None:
        peer_outcome = None
        build_context.finish_stage(
            peer_token,
            status=ResearchBuildStageStatus.SKIPPED,
            safe_blocker="Canonical identity was unavailable; peer execution was skipped.",
        )
    else:
        peer_outcome = run_live_peer_family_audit(
            normalized_symbol,
            environment=environment,
            analysis_as_of=snapshot,
            build_context=build_context,
            target_identity=canonical_identity,
        )
        if build_context is not None and peer_token is not None:
            build_context.finish_stage(
                peer_token,
                status=(
                    ResearchBuildStageStatus.COMPLETED
                    if peer_outcome.peer_set is not None else ResearchBuildStageStatus.FAILED
                ),
                safe_blocker=(
                    None if peer_outcome.peer_set is not None
                    else "Canonical peer evidence was unavailable."
                ),
                issues_count=len(peer_outcome.issues),
            )
    candidates = []
    if own_outcome.result is not None:
        candidates.extend(
            family_evidence_from_own_history(item)
            for item in own_outcome.result.valuations
        )
    if peer_outcome is not None and peer_outcome.peer_family_orchestration is not None:
        candidates.append(
            family_evidence_from_peer_orchestration(peer_outcome.peer_family_orchestration)
        )

    if own_outcome.result is not None:
        target = own_outcome.result.identity
        target_security_id = target.security_id
        target_issuer_id = target.issuer_id
    elif peer_outcome is not None and peer_outcome.target_identity is not None:
        target_security_id = peer_outcome.target_identity.security_id
        target_issuer_id = peer_outcome.target_identity.issuer_id
    else:
        target_security_id = f"v1-security:{normalized_symbol}"
        target_issuer_id = f"v1-issuer:{normalized_symbol}"

    publication_token = build_context.start_stage(ResearchReportPipelineStage.PUBLICATION) if build_context else None
    publication = publish_cross_family_valuation(
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        analysis_as_of=snapshot,
        family_candidates=tuple(candidates),
    )
    if build_context is not None and publication_token is not None:
        build_context.finish_stage(
            publication_token,
            issues_count=len(publication.blocking_reasons),
        )
    notes = []
    if own_outcome.result is None:
        notes.append("own-history canonical reconstruction unavailable in this bounded run")
    if peer_outcome is None or peer_outcome.peer_family_orchestration is None:
        notes.append("peer-family canonical reconstruction unavailable in this bounded run")
    return LivePublicationAuditOutcome(
        symbol=normalized_symbol,
        analysis_as_of=snapshot,
        own_history=own_outcome,
        peer_family=peer_outcome,
        publication=publication,
        reverse_dcf_canonical_status="NOT_READY: no canonical execution result supplied by the approved live path",
        reverse_dcf_scenario_status="NOT_SUPPLIED: no explicit scenario execution requested",
        safe_notes=tuple(notes),
    )


def _value(value: float | None) -> str:
    return "WITHHELD" if value is None else f"{value:,.2f}"


def render_live_publication_audit(outcome: LivePublicationAuditOutcome) -> str:
    """Render only canonical identifiers, controlled statuses, and derived publication values."""
    result = outcome.publication
    lines = [
        "V1 MILESTONE 11A LIVE PUBLICATION AUDIT",
        f"Symbol: {outcome.symbol}",
        f"analysis_as_of: {outcome.analysis_as_of.isoformat()}",
        f"target security ID: {result.target_security_id}",
        f"target issuer ID: {result.target_issuer_id}",
        f"publication currency/unit: {result.currency or 'unavailable'} / {result.per_share_unit or 'unavailable'}",
        "Family evidence:",
    ]
    if not result.family_evidence:
        lines.append("  none reconstructed")
    for item in result.family_evidence:
        lines.extend((
            f"  {item.valuation_family.value}:",
            f"    selected method: {item.selected_method}",
            f"    source result ID: {item.source_result_id}",
            f"    status: {item.family_status.value}",
            f"    central eligible: {str(item.central_valuation_eligible).upper()}",
            f"    lower / central / upper: {_value(item.lower_value)} / {_value(item.central_value)} / {_value(item.upper_value)}",
            f"    currency/unit: {item.currency or 'unavailable'} / {item.per_share_unit or 'unavailable'}",
        ))
    lines.extend((
        f"eligible central family IDs: {', '.join(item.value for item in result.eligible_family_ids) or 'none'}",
        f"eligible central family count: {result.eligible_family_count}",
        f"publication status: {result.publication_status.value}",
        f"overall central fair value: {_value(result.overall_central_value)}",
        f"family envelope: {_value(result.envelope_lower)} to {_value(result.envelope_upper)} ({result.envelope_semantics.value if result.envelope_semantics else 'unavailable'})",
        f"common overlap: {_value(result.overlap_lower)} to {_value(result.overlap_upper)} ({result.overlap_semantics.value if result.overlap_semantics else 'unavailable'})",
        f"reverse-DCF canonical expectation: {outcome.reverse_dcf_canonical_status}",
        f"reverse-DCF scenario: {outcome.reverse_dcf_scenario_status}",
    ))
    lines.extend(f"blocking reason: {item}" for item in result.blocking_reasons)
    lines.extend(f"safe note: {item}" for item in outcome.safe_notes)
    lines.append("No current market price, price comparison, investment stance, or UI behavior was used.")
    return "\n".join(lines)
