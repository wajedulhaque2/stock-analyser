"""Explicit, secret-safe audit wrapper for one Milestone 11C research report."""

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
from stock_analyser.domain import StockResearchReport, ValuationFamily
from stock_analyser.live_market_comparison_audit import (
    LiveMarketComparisonAuditOutcome,
    run_live_market_comparison_audit,
)
from stock_analyser.live_reverse_dcf_audit import (
    LiveReverseDcfAuditOutcome,
    run_live_reverse_dcf_audit,
)
from stock_analyser.services import build_stock_research_report


@dataclass(frozen=True, slots=True)
class LiveResearchReportAuditOutcome:
    symbol: str
    analysis_as_of: datetime
    report: StockResearchReport | None
    market_audit: LiveMarketComparisonAuditOutcome
    reverse_dcf_audit: LiveReverseDcfAuditOutcome | None
    safe_notes: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.report is not None


def _identity(outcome: LiveMarketComparisonAuditOutcome):
    publication = outcome.publication_outcome
    if publication.own_history is not None and publication.own_history.result is not None:
        return publication.own_history.result.identity
    if publication.peer_family is not None:
        return publication.peer_family.target_identity
    return None


def run_live_research_report_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    build_context: ResearchBuildContext | None = None,
    identity_configuration_source: LiveConfigurationSource | None = None,
) -> LiveResearchReportAuditOutcome:
    """Invoke existing bounded audits once each, then call only the pure 11C builder."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    snapshot = analysis_as_of or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    market_audit = run_live_market_comparison_audit(
        normalized_symbol,
        environment=environment,
        analysis_as_of=snapshot,
        build_context=build_context,
        identity_configuration_source=identity_configuration_source,
    )
    identity = _identity(market_audit)
    notes = []
    if identity is None:
        notes.append("Canonical identity was unavailable; report construction failed closed")
        return LiveResearchReportAuditOutcome(
            normalized_symbol, snapshot, None, market_audit, None, tuple(notes),
        )
    reverse_audit = run_live_reverse_dcf_audit(
        normalized_symbol,
        environment=environment,
        analysis_as_of=snapshot,
        build_context=build_context,
        canonical_identity=identity,
    )
    trajectory = reverse_audit.trajectory
    readiness = reverse_audit.readiness
    if reverse_audit.identity is not None and (
        reverse_audit.identity.security_id != identity.security_id
        or reverse_audit.identity.issuer_id != identity.issuer_id
    ):
        notes.append("Reverse-DCF audit identity did not match the publication identity; report construction failed closed")
        return LiveResearchReportAuditOutcome(
            normalized_symbol, snapshot, None, market_audit, reverse_audit, tuple(notes),
        )
    report_token = build_context.start_stage(ResearchReportPipelineStage.REPORT_BUILD) if build_context else None
    try:
        report = build_stock_research_report(
            identity=identity,
            analysis_as_of=snapshot,
            publication=market_audit.publication_outcome.publication,
            market_price=market_audit.market_price,
            market_comparison=market_audit.comparison,
            forward_trajectory=trajectory,
            reverse_dcf_readiness=readiness,
        )
    except ValueError as error:
        notes.append(f"Report target/snapshot validation failed closed: {error}")
        report = None
    if build_context is not None and report_token is not None:
        build_context.finish_stage(
            report_token,
            status=(
                ResearchBuildStageStatus.COMPLETED
                if report is not None else ResearchBuildStageStatus.FAILED
            ),
            safe_blocker=(None if report is not None else "The report contract failed closed."),
            issues_count=len(notes),
        )
    return LiveResearchReportAuditOutcome(
        normalized_symbol, snapshot, report, market_audit, reverse_audit, tuple(notes),
    )


def _value(value: float | None) -> str:
    return "WITHHELD" if value is None else f"{value:,.6g}"


def _percent(value: float | None) -> str:
    return "WITHHELD" if value is None else f"{value:.6%}"


def render_live_research_report_audit(outcome: LiveResearchReportAuditOutcome) -> str:
    """Render allowlisted report fields only; never provider payloads or credentials."""
    lines = [
        "V1 MILESTONE 11C LIVE RESEARCH-REPORT AUDIT",
        f"Symbol: {outcome.symbol}",
        f"analysis_as_of: {outcome.analysis_as_of.isoformat()}",
    ]
    if outcome.report is None:
        lines.append("Report status: UNAVAILABLE")
        lines.extend(f"safe note: {item}" for item in outcome.safe_notes)
        lines.append("No investment stance or generated narrative was produced.")
        return "\n".join(lines)
    report = outcome.report
    lines.extend((
        "IDENTITY",
        f"  canonical security ID: {report.target_security_id}",
        f"  canonical issuer ID: {report.target_issuer_id}",
        f"  symbol / company: {report.identity.display_symbol} / {report.identity.company_name}",
        f"  reporting currency: {report.identity.reporting_currency}",
        f"  quote currency / unit / scale: {report.identity.quote_currency} / {report.identity.quote_unit} / {report.identity.quote_price_scale:g}",
        "MARKET",
        f"  status: {report.market.availability.value}",
        f"  normalized current price: {_value(report.market.normalized_market_price)} {report.market.normalized_per_share_unit or ''}".rstrip(),
        f"  price timestamp: {report.market.observation_timestamp.isoformat() if report.market.observation_timestamp else 'unavailable'}",
        f"  source: {report.market.source_label}",
        "VALUATION SUMMARY",
        f"  publication status: {report.valuation_summary.publication_label}",
        f"  eligible family count: {report.valuation_summary.eligible_family_count}",
        f"  overall fair value: {_value(report.valuation_summary.overall_central_value)}",
        f"  overall price comparison: {_value(report.valuation_summary.overall_central_gap)} / {_percent(report.valuation_summary.overall_central_gap_percent)}",
        "FAMILIES",
    ))
    for item in report.valuation_families:
        lines.extend((
            f"  {item.family.value.upper()}",
            f"    status: {item.family_status.value}",
            f"    lower / central / upper: {_value(item.lower_value)} / {_value(item.central_value)} / {_value(item.upper_value)}",
            f"    price gaps: {_value(item.lower_gap)} / {_value(item.central_gap)} / {_value(item.upper_gap)}",
            f"    price gap percentages: {_percent(item.lower_gap_percent)} / {_percent(item.central_gap_percent)} / {_percent(item.upper_gap_percent)}",
        ))
        lines.extend(f"    blocker: {reason}" for reason in item.blocking_reasons)
    lines.extend((
        "CONSENSUS",
        f"  status / periods: {report.consensus.readiness_status.value if report.consensus.readiness_status else 'unavailable'} / {report.consensus.period_count}",
    ))
    for item in report.consensus.periods:
        lines.append(
            f"  {item.horizon_label} {item.fiscal_period_end.isoformat()}: revenue={_value(item.revenue)}; EBIT={_value(item.ebit)}; EBITDA={_value(item.ebitda)}; margin={_percent(item.ebit_margin)}; growth={_percent(item.revenue_growth)}; analysts={item.revenue_analyst_count if item.revenue_analyst_count is not None else 'unavailable'}"
        )
    lines.extend((
        "EXPECTATIONS",
        f"  reverse DCF: {report.expectations.display_label}",
    ))
    for item in report.expectations.entries:
        lines.append(f"  implied terminal growth: {_percent(item.implied_terminal_growth)}")
    lines.extend(f"  blocker: {item}" for item in report.expectations.blocking_reasons)
    lines.append("REFERENCES")
    if not report.references:
        lines.append("  none supplied")
    for item in report.references:
        lines.append(f"  {item.reference_type.value}: {item.reference_only_label} / {item.status.value}")
    lines.append("DATA QUALITY")
    lines.extend(
        f"  {item.category.value}: {item.status_label}"
        for item in report.data_quality.rows
    )
    lines.extend(f"safe note: {item}" for item in outcome.safe_notes)
    lines.append("No valuation recalculation, investment stance, recommendation, generated narrative, or UI behavior was produced.")
    return "\n".join(lines)
