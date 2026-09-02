"""Explicit, compact, secret-safe live audit for the complete peer family."""

from __future__ import annotations

from typing import Mapping

from stock_analyser.application.research_build_context import ResearchBuildContext
from stock_analyser.domain import CompanyIdentity

from stock_analyser.domain import PeerCriterion, PeerCriterionStatus, PeerSelectionStatus
from stock_analyser.live_peer_audit import LivePeerAuditOutcome, run_live_peer_audit


def run_live_peer_family_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of=None,
    build_context: ResearchBuildContext | None = None,
    target_identity: CompanyIdentity | None = None,
) -> LivePeerAuditOutcome:
    """Run the existing safe 8A audit and its provider-independent 8E coordinator."""
    return run_live_peer_audit(
        symbol,
        environment=environment,
        analysis_as_of=analysis_as_of,
        build_context=build_context,
        target_identity=target_identity,
    )


def _value(value) -> str:
    return "unavailable" if value is None else str(value)


def render_live_peer_family_audit(outcome: LivePeerAuditOutcome) -> str:
    """Render canonical IDs and derived summaries only; never provider payloads."""
    identity = outcome.target_identity
    peer_set = outcome.peer_set
    subset = outcome.peer_valuation_subset
    distribution = outcome.peer_multiple_distribution
    valuation = outcome.peer_target_valuation
    orchestration = outcome.peer_family_orchestration
    included = (
        sum(item.status is PeerSelectionStatus.INCLUDED for item in peer_set.selections)
        if peer_set is not None else 0
    )
    security_eligible = (
        sum(
            item.evidence.criterion(PeerCriterion.SECURITY_TYPE).status is PeerCriterionStatus.PASS
            for item in peer_set.selections
        )
        if peer_set is not None else 0
    )
    lines = [
        "V1 LIVE PEER-FAMILY AUDIT",
        f"Target symbol: {outcome.symbol}",
        f"Target security ID: {_value(identity.security_id if identity else None)}",
        f"Target issuer ID: {_value(identity.issuer_id if identity else None)}",
        f"Reporting currency: {_value(identity.reporting_currency if identity else None)}",
        f"Quote currency: {_value(identity.quote_currency if identity else None)}",
        f"Quote unit: {_value(identity.quote_unit if identity else None)}",
        f"Quote-price scale: {_value(identity.price_scale if identity else None)}",
        f"8A candidate count: {outcome.discovered_candidate_count}",
        f"8A canonical identities resolved: {outcome.resolved_candidate_count}",
        f"8A security-eligible count: {security_eligible}",
        f"8A included economic peers: {included}",
        f"8A peer-set status: {_value(peer_set.status.value if peer_set else None)}",
        f"8B valid observations: {subset.valid_observation_count if subset else 0}",
        f"8B subset status: {_value(subset.status.value if subset else None)}",
        f"8C sample count: {distribution.sample_count if distribution else 0}",
        f"8C distribution status: {_value(distribution.status.value if distribution else None)}",
        f"8C P25: {_value(distribution.p25 if distribution else None)}",
        f"8C median: {_value(distribution.median if distribution else None)}",
        f"8C P75: {_value(distribution.p75 if distribution else None)}",
        f"8D status: {_value(valuation.status.value if valuation else None)}",
        f"8D P25 per share: {_value(valuation.lower_point.per_share_value if valuation and valuation.lower_point else None)}",
        f"8D median per share: {_value(valuation.central_point.per_share_value if valuation and valuation.central_point else None)}",
        f"8D P75 per share: {_value(valuation.upper_point.per_share_value if valuation and valuation.upper_point else None)}",
        f"8D result currency: {_value(valuation.currency if valuation else None)}",
        f"Peer-family status: {_value(orchestration.status.value if orchestration else None)}",
        f"Failure stage: {_value(orchestration.failure_stage.value if orchestration else 'identity')}",
        (
            "Blocking reasons: "
            + (
                "; ".join(orchestration.blocking_reasons)
                if orchestration is not None
                else "; ".join(issue.reason for issue in outcome.issues) or "target identity unavailable"
            )
        ),
        f"Safe issue count: {len(outcome.issues)}",
        "No raw provider payload, current price, FX conversion, family aggregation, or stance was produced.",
    ]
    return "\n".join(lines)
