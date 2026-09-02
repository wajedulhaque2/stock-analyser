"""Explicit, secret-safe live audit for Milestone 11B market comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from stock_analyser.application.live_configuration import LiveConfigurationSource
from stock_analyser.application.research_build_context import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
    ResearchBuildStageStatus,
)
from stock_analyser.application.v1_research import ResearchReportPipelineStage
from stock_analyser.domain import (
    CompanyIdentity,
    DataAvailability,
    MarketComparisonResult,
    MarketPriceEvidence,
    MetricId,
)
from stock_analyser.live_publication_audit import (
    LivePublicationAuditOutcome,
    run_live_publication_audit,
)
from stock_analyser.live_smoke import LiveYahooSource
from stock_analyser.providers import YahooAdapter
from stock_analyser.services import build_market_price_evidence, compare_publication_to_market


@dataclass(frozen=True, slots=True)
class LiveMarketComparisonAuditOutcome:
    symbol: str
    analysis_as_of: datetime
    publication_outcome: LivePublicationAuditOutcome
    market_price: MarketPriceEvidence
    comparison: MarketComparisonResult
    safe_notes: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.market_price.status is DataAvailability.AVAILABLE


def _target_identity(outcome: LivePublicationAuditOutcome) -> CompanyIdentity | None:
    if outcome.own_history is not None and outcome.own_history.result is not None:
        return outcome.own_history.result.identity
    if outcome.peer_family is not None:
        return outcome.peer_family.target_identity
    return None


def run_live_market_comparison_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    build_context: ResearchBuildContext | None = None,
    identity_configuration_source: LiveConfigurationSource | None = None,
) -> LiveMarketComparisonAuditOutcome:
    """Run one bounded 11A audit, one Yahoo quote lookup, then pure 11B comparison."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    snapshot = analysis_as_of or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")

    publication_outcome = run_live_publication_audit(
        normalized_symbol,
        environment=environment,
        analysis_as_of=snapshot,
        build_context=build_context,
        identity_configuration_source=identity_configuration_source,
    )
    publication = publication_outcome.publication
    identity = _target_identity(publication_outcome)
    notes = []
    observation = None
    market_token = build_context.start_stage(ResearchReportPipelineStage.MARKET_DATA) if build_context else None
    if identity is None:
        notes.append("canonical target identity unavailable; Yahoo market price was not requested")
        observation_security_id = publication.target_security_id
        observation_issuer_id = publication.target_issuer_id
        quote_currency = quote_unit = None
        quote_unit_scale = None
        if build_context is not None and market_token is not None:
            build_context.finish_stage(
                market_token,
                status=ResearchBuildStageStatus.SKIPPED,
                safe_blocker="Canonical identity was unavailable; market data was skipped.",
            )
    else:
        target_id = f"{identity.security_id}|{identity.issuer_id}"
        market_key = NormalizedEvidenceKey("yahoo", "market_snapshot", target_id, snapshot)
        adapter = YahooAdapter(LiveYahooSource())
        market = (
            build_context.get_or_create_normalized(
                market_key, lambda: adapter.fetch_market_snapshot(identity),
            ) if build_context else adapter.fetch_market_snapshot(identity)
        )
        observation = next(
            (item for item in market.observations if item.metric_id is MetricId.SHARE_PRICE),
            None,
        )
        observation_security_id = identity.security_id
        observation_issuer_id = identity.issuer_id
        quote_currency = identity.quote_currency
        quote_unit = identity.quote_unit
        quote_unit_scale = identity.price_scale
        if observation is None:
            notes.append("Yahoo regular market price was unavailable in the bounded lookup")
        if build_context is not None and market_token is not None:
            build_context.finish_stage(
                market_token,
                status=(
                    ResearchBuildStageStatus.COMPLETED
                    if observation is not None else ResearchBuildStageStatus.FAILED
                ),
                safe_blocker=(None if observation is not None else "Current market evidence was unavailable."),
                issues_count=len(market.issues),
            )

    comparison_token = build_context.start_stage(ResearchReportPipelineStage.MARKET_COMPARISON) if build_context else None
    price = build_market_price_evidence(
        target_security_id=publication.target_security_id,
        target_issuer_id=publication.target_issuer_id,
        observation_security_id=observation_security_id,
        observation_issuer_id=observation_issuer_id,
        analysis_as_of=snapshot,
        observation=observation,
        quote_currency=quote_currency,
        quote_unit=quote_unit,
        quote_unit_scale=quote_unit_scale,
    )
    comparison = compare_publication_to_market(publication, price)
    if build_context is not None and comparison_token is not None:
        build_context.finish_stage(
            comparison_token,
            issues_count=len(price.issues) + len(comparison.overall_comparison.issues),
        )
    return LiveMarketComparisonAuditOutcome(
        symbol=normalized_symbol,
        analysis_as_of=snapshot,
        publication_outcome=publication_outcome,
        market_price=price,
        comparison=comparison,
        safe_notes=tuple(notes),
    )


def _value(value: float | None) -> str:
    return "WITHHELD" if value is None else f"{value:,.6g}"


def _percent(value: float | None) -> str:
    return "WITHHELD" if value is None else f"{value:.6%}"


def render_live_market_comparison_audit(outcome: LiveMarketComparisonAuditOutcome) -> str:
    """Render canonical evidence and arithmetic only; never render provider payloads."""
    price = outcome.market_price
    comparison = outcome.comparison
    lines = [
        "V1 MILESTONE 11B LIVE MARKET-COMPARISON AUDIT",
        f"Symbol: {outcome.symbol}",
        f"analysis_as_of: {outcome.analysis_as_of.isoformat()}",
        f"target security ID: {comparison.target_security_id}",
        f"target issuer ID: {comparison.target_issuer_id}",
        f"price semantic: {price.price_semantic.value}",
        f"price status: {price.status.value}",
        f"price observation timestamp: {price.observation_timestamp.isoformat() if price.observation_timestamp else 'unavailable'}",
        f"raw quote: {_value(price.raw_quote_value)} {price.quote_unit or 'unavailable'}",
        f"quote currency / scale: {price.quote_currency or 'unavailable'} / {_value(price.quote_unit_scale)}",
        f"normalized price: {_value(price.normalized_price_per_share)} {price.normalized_per_share_unit or 'unavailable'}",
        "Valuation-family comparisons:",
    ]
    if not comparison.family_comparisons:
        lines.append("  none")
    for item in comparison.family_comparisons:
        lines.extend((
            f"  {item.valuation_family.value if item.valuation_family else 'unknown'}:",
            f"    status: {item.status.value}",
            f"    lower / central / upper: {_value(item.lower_value)} / {_value(item.central_value)} / {_value(item.upper_value)}",
            f"    gaps: {_value(item.lower_gap)} / {_value(item.central_gap)} / {_value(item.upper_gap)}",
            f"    gap percentages: {_percent(item.lower_gap_percent)} / {_percent(item.central_gap_percent)} / {_percent(item.upper_gap_percent)}",
        ))
        lines.extend(f"    issue: {issue}" for issue in item.issues)
    overall = comparison.overall_comparison
    lines.extend((
        f"overall publication status: {overall.publication_status.value if overall.publication_status else 'unavailable'}",
        f"overall comparison status: {overall.status.value}",
        f"overall envelope / central: {_value(overall.lower_value)} / {_value(overall.central_value)} / {_value(overall.upper_value)}",
        f"overall gaps: {_value(overall.lower_gap)} / {_value(overall.central_gap)} / {_value(overall.upper_gap)}",
        f"reverse-DCF canonical expectation: {outcome.publication_outcome.reverse_dcf_canonical_status}",
        f"reverse-DCF scenario: {outcome.publication_outcome.reverse_dcf_scenario_status}",
    ))
    lines.extend(f"price issue: {item.reason}" for item in price.issues)
    lines.extend(f"overall issue: {item}" for item in overall.issues)
    lines.extend(f"safe note: {item}" for item in outcome.safe_notes)
    lines.append("No recommendation, stance, target-price label, valuation feedback, or UI behavior was produced.")
    return "\n".join(lines)
