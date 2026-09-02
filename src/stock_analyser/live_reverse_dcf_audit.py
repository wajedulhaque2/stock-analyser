"""One opt-in, bounded, secret-safe Milestone 10A META evidence audit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping

from stock_analyser.application.research_build_context import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
    ResearchBuildStageStatus,
)
from stock_analyser.application.v1_research import ResearchReportPipelineStage
from stock_analyser.domain import (
    CompanyIdentity,
    MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor,
    ProviderSymbol,
    ReverseDcfActualBase,
    ReverseDcfReadiness,
    ReverseDcfReinvestmentReadiness,
    ReverseDcfTerminalReadiness,
    ForwardOperatingTrajectory,
)
from stock_analyser.live_discount_rate_audit import _LiveDamodaranSource
from stock_analyser.live_smoke import LiveFiscalSource, _provisional_identity, _retrying_transport, _secret
from stock_analyser.providers import DamodaranAdapter, FiscalAdapter, FmpAdapter
from stock_analyser.services import (
    assess_reverse_dcf_readiness,
    build_forward_consensus,
    build_forward_operating_trajectory,
    build_marginal_tax_evidence,
    build_market_enterprise_value_anchor,
    build_reinvestment_readiness,
    build_reverse_dcf_actual_base,
    build_terminal_readiness,
)


@dataclass(frozen=True, slots=True)
class LiveReverseDcfAuditOutcome:
    symbol: str
    analysis_as_of: datetime
    identity: CompanyIdentity | None
    actual_base: ReverseDcfActualBase | None
    trajectory: ForwardOperatingTrajectory | None
    market_anchor: MarketEnterpriseValueAnchor | None
    operating_tax: MarginalTaxRateEvidence | None
    reinvestment: ReverseDcfReinvestmentReadiness | None
    terminal: ReverseDcfTerminalReadiness | None
    readiness: ReverseDcfReadiness | None
    fiscal_identity_status: str
    fiscal_actuals_status: str
    fiscal_market_anchor_status: str
    fmp_consensus_status: str
    safe_issues: tuple[str, ...]


def _capability_status(result) -> str:
    capabilities = getattr(result, "capabilities", ())
    capability = capabilities[0] if capabilities else getattr(result, "capability", None)
    return capability.status.value if capability is not None else "unavailable"


def _canonical_live_identity(symbol: str, candidate) -> CompanyIdentity | None:
    if candidate is None or not candidate.provider_issuer_id or not candidate.provider_security_id:
        return None
    base = _provisional_identity(symbol)
    fiscal_symbol = candidate.provider_symbol or symbol
    return replace(
        base,
        canonical_symbol=symbol.upper(),
        security_id=f"security:fiscal:{candidate.provider_security_id}",
        issuer_id=f"issuer:fiscal:{candidate.provider_issuer_id}",
        company_name=candidate.company_name or base.company_name,
        issuer_domicile=candidate.issuer_domicile or base.issuer_domicile,
        listing_country=candidate.listing_country or base.listing_country,
        exchange=candidate.exchange or base.exchange,
        sector=candidate.sector or base.sector,
        industry=candidate.industry or base.industry,
        security_type=candidate.security_type or base.security_type,
        reporting_currency=candidate.reporting_currency or base.reporting_currency,
        quote_currency=candidate.quote_currency or candidate.reporting_currency or base.quote_currency,
        quote_unit=candidate.quote_unit or candidate.quote_currency or base.quote_unit,
        price_scale=candidate.price_scale or base.price_scale,
        fiscal_year_end=candidate.fiscal_year_end or base.fiscal_year_end,
        provider_symbols=(ProviderSymbol("fiscal", fiscal_symbol), ProviderSymbol("fmp", symbol)),
        company_type=candidate.company_type,
    )


def run_live_reverse_dcf_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    build_context: ResearchBuildContext | None = None,
    canonical_identity: CompanyIdentity | None = None,
) -> LiveReverseDcfAuditOutcome:
    """Fetch only approved Fiscal/FMP operating evidence and explicit tax evidence once."""
    analysis_as_of = analysis_as_of or datetime.now(timezone.utc)
    if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol is required")
    issues: list[str] = []
    missing = tuple(name for name in ("FISCAL_API_KEY", "FMP_API_KEY") if not environment.get(name))
    if missing:
        issues.append("Required live credential references are unavailable: " + ", ".join(missing))
        return LiveReverseDcfAuditOutcome(
            normalized_symbol, analysis_as_of, None, None, None, None, None, None, None, None,
            "unavailable", "unavailable", "unavailable", "unavailable", tuple(issues),
        )

    fiscal = FiscalAdapter(
        LiveFiscalSource(), credential=_secret("fiscal", environment), clock=lambda: analysis_as_of,
    )
    if canonical_identity is None:
        identity_key = NormalizedEvidenceKey(
            "fiscal", "identity", f"symbol:{normalized_symbol}", analysis_as_of,
        )
        identity_result = (
            build_context.get_or_create_normalized(
                identity_key, lambda: fiscal.fetch_identity(normalized_symbol),
            ) if build_context else fiscal.fetch_identity(normalized_symbol)
        )
        issues.extend(item.reason for item in identity_result.issues)
        identity = _canonical_live_identity(normalized_symbol, identity_result.candidate)
        identity_status = _capability_status(identity_result)
    else:
        identity = canonical_identity
        identity_status = "reused_canonical"
    if identity is None:
        issues.append("Fiscal stable issuer/security identity evidence is unavailable.")
        return LiveReverseDcfAuditOutcome(
            normalized_symbol, analysis_as_of, None, None, None, None, None, None, None, None,
            identity_status, "unavailable", "unavailable", "unavailable",
            tuple(dict.fromkeys(issues)),
        )

    target_id = f"{identity.security_id}|{identity.issuer_id}"
    actuals_key = NormalizedEvidenceKey("fiscal", "standardized_actuals", target_id, analysis_as_of)
    bridge_key = NormalizedEvidenceKey("fiscal", "enterprise_bridge", target_id, analysis_as_of)
    consensus_key = NormalizedEvidenceKey(
        "fmp", "annual_estimates", target_id, analysis_as_of, (("frequency", "annual"),),
    )
    fundamentals_token = build_context.start_stage(ResearchReportPipelineStage.FUNDAMENTALS) if build_context else None
    actuals_result = (
        build_context.get_or_create_normalized(
            actuals_key, lambda: fiscal.fetch_standardized_actuals(identity, analysis_as_of=analysis_as_of),
        ) if build_context else fiscal.fetch_standardized_actuals(identity, analysis_as_of=analysis_as_of)
    )
    bridge_result = (
        build_context.get_or_create_normalized(
            bridge_key, lambda: fiscal.fetch_enterprise_bridge(identity, analysis_as_of=analysis_as_of),
        ) if build_context else fiscal.fetch_enterprise_bridge(identity, analysis_as_of=analysis_as_of)
    )
    if build_context is not None and fundamentals_token is not None:
        build_context.finish_stage(
            fundamentals_token,
            issues_count=len((*actuals_result.issues, *bridge_result.issues)),
        )
    fmp = FmpAdapter(
        _retrying_transport(), credential=_secret("fmp", environment), clock=lambda: analysis_as_of,
    )
    consensus_token = build_context.start_stage(ResearchReportPipelineStage.FORWARD_CONSENSUS) if build_context else None
    consensus_result = (
        build_context.get_or_create_normalized(
            consensus_key,
            lambda: fmp.fetch_annual_estimates(identity, source_as_of_at=analysis_as_of),
        ) if build_context else fmp.fetch_annual_estimates(identity, source_as_of_at=analysis_as_of)
    )
    if build_context is not None and consensus_token is not None:
        build_context.finish_stage(consensus_token, issues_count=len(consensus_result.issues))
    for result in (actuals_result, bridge_result, consensus_result):
        issues.extend(item.reason for item in result.issues)

    actual_base = build_reverse_dcf_actual_base(
        identity, actuals_result.observations, analysis_as_of=analysis_as_of,
        valuation_currency=identity.reporting_currency,
    )
    consensus = build_forward_consensus(consensus_result.observations, as_of_at=analysis_as_of)
    issues.extend(item.reason for item in consensus.issues)
    trajectory = build_forward_operating_trajectory(
        identity, consensus, actual_base, analysis_as_of=analysis_as_of,
        valuation_currency=identity.reporting_currency,
    )
    anchor = build_market_enterprise_value_anchor(
        identity, bridge_result.observations, analysis_as_of=analysis_as_of,
        valuation_currency=identity.reporting_currency,
    )
    reinvestment = build_reinvestment_readiness(
        consensus_result.observations, actuals_result.observations,
        analysis_as_of=analysis_as_of,
    )
    terminal = build_terminal_readiness(identity.reporting_currency, analysis_as_of=analysis_as_of)

    operating_tax = None
    try:
        tax_result = DamodaranAdapter(_LiveDamodaranSource()).fetch_country_marginal_tax_rates()
        issues.extend(item.reason for item in tax_result.issues)
        operating_tax = build_marginal_tax_evidence(
            identity.issuer_domicile, tax_result.observations, analysis_as_of=analysis_as_of,
        )
    except Exception:
        issues.append("Approved marginal-tax evidence could not be retrieved for this bounded audit.")

    reverse_token = build_context.start_stage(ResearchReportPipelineStage.REVERSE_DCF) if build_context else None
    readiness = assess_reverse_dcf_readiness(
        identity, actual_base, trajectory, anchor, reinvestment, terminal,
        analysis_as_of=analysis_as_of, valuation_currency=identity.reporting_currency,
        operating_tax=operating_tax,
        wacc=None,  # Frozen 9B.3 production-WACC result remains NOT_READY; no fallback is allowed.
    )
    issues.extend(readiness.issues)
    if build_context is not None and reverse_token is not None:
        build_context.finish_stage(
            reverse_token,
            status=(
                ResearchBuildStageStatus.COMPLETED
                if readiness is not None else ResearchBuildStageStatus.FAILED
            ),
            issues_count=len(readiness.issues),
        )
    return LiveReverseDcfAuditOutcome(
        normalized_symbol, analysis_as_of, identity, actual_base, trajectory, anchor,
        operating_tax, reinvestment, terminal, readiness,
        identity_status, _capability_status(actuals_result),
        _capability_status(bridge_result), _capability_status(consensus_result),
        tuple(dict.fromkeys(issues)),
    )


def _value(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def render_live_reverse_dcf_audit(outcome: LiveReverseDcfAuditOutcome) -> str:
    """Render canonical evidence only; never raw provider responses or credentials."""
    identity = outcome.identity
    actual = outcome.actual_base
    trajectory = outcome.trajectory
    anchor = outcome.market_anchor
    tax = outcome.operating_tax
    reinvestment = outcome.reinvestment
    terminal = outcome.terminal
    readiness = outcome.readiness
    lines = [
        "V1 MILESTONE 10A REVERSE-DCF EVIDENCE AUDIT",
        f"Target symbol: {outcome.symbol}",
        f"Canonical security ID: {_value(identity.security_id if identity else None)}",
        f"Canonical issuer ID: {_value(identity.issuer_id if identity else None)}",
        f"Analysis as-of: {outcome.analysis_as_of.isoformat()}",
        f"Valuation/reporting currency: {_value(identity.reporting_currency if identity else None)}",
        f"Fiscal identity capability: {outcome.fiscal_identity_status}",
        f"Fiscal actuals capability: {outcome.fiscal_actuals_status}",
        f"FMP annual consensus capability: {outcome.fmp_consensus_status}",
        f"Actual revenue: period={_value(actual.period_end if actual else None)}; value={_value(actual.revenue if actual else None)}; currency={_value(actual.currency if actual else None)}; status={_value(actual.status.value if actual else None)}",
        f"Actual EBIT: period={_value(actual.period_end if actual and actual.ebit is not None else None)}; value={_value(actual.ebit if actual else None)}; status={_value(actual.ebit_continuity_status.value if actual else None)}",
        f"Actual operating income (separate): period={_value(actual.period_end if actual and actual.operating_income is not None else None)}; value={_value(actual.operating_income if actual else None)}",
        f"Actual-to-forward EBIT continuity: {_value(actual.ebit_continuity_status.value if actual else None)}",
        f"Forward annual period count: {trajectory.period_count if trajectory else 0}",
    ]
    for period in trajectory.periods if trajectory else ():
        lines.append(
            f"{period.fiscal_period}: period_end={period.fiscal_period_end}; revenue={_value(period.revenue)}; EBIT={_value(period.ebit)}; EBITDA={_value(period.ebitda)}; EBIT_margin={_value(period.operating_margin)}; revenue_growth={_value(period.revenue_growth)}; revenue_analysts={_value(period.revenue_analyst_count)}; EBIT_analysts={_value(period.ebit_analyst_count)}; EBITDA_analysts={_value(period.ebitda_analyst_count)}; estimate_as_of={period.estimate_as_of.isoformat()}; status={period.status.value}"
        )
    lines.extend((
        f"Forward revenue trajectory: {_value(trajectory.revenue_status.value if trajectory else None)}",
        f"Forward EBIT trajectory: {_value(trajectory.ebit_status.value if trajectory else None)}",
        f"Forward EBIT-margin trajectory: {_value(trajectory.margin_status.value if trajectory else None)}",
        f"Fiscal TEV capability: {outcome.fiscal_market_anchor_status}",
        f"Market TEV anchor: value={_value(anchor.value if anchor else None)}; date={_value(anchor.observation_date if anchor else None)}; currency={_value(anchor.currency if anchor else None)}; status={_value(anchor.status.value if anchor else None)}",
        f"Operating marginal tax: jurisdiction={_value(tax.jurisdiction if tax else None)}; source_date={_value(tax.source_date if tax else None)}; value={_value(tax.value if tax else None)}; status={_value(tax.status.value if tax else None)}",
        f"Reinvestment external FCFF: {_value(reinvestment.external_fcff_status.value if reinvestment else None)}",
        f"Reinvestment external FCFE: {_value(reinvestment.external_fcfe_status.value if reinvestment else None)}",
        f"Reinvestment generic FCF: {_value(reinvestment.generic_fcf_status.value if reinvestment else None)}",
        f"Reinvestment forward CapEx: {_value(reinvestment.forward_capex_status.value if reinvestment else None)}",
        f"Reinvestment forward D&A: {_value(reinvestment.forward_da_status.value if reinvestment else None)}",
        f"Reinvestment forward change-NWC: {_value(reinvestment.forward_change_nwc_status.value if reinvestment else None)}",
        f"Historical reinvestment evidence: {_value(reinvestment.historical_reinvestment_status.value if reinvestment else None)}",
        f"Sales-to-capital evidence: {_value(reinvestment.sales_to_capital_status.value if reinvestment else None)}",
        f"Approved reinvestment methodology: {_value(reinvestment.methodology_status.value if reinvestment else None)}",
        f"Reinvestment overall: {_value(reinvestment.status.value if reinvestment else None)}",
        "Production WACC: not_ready (frozen Milestone 9B.3 result; cost of equity not substituted)",
        f"Terminal growth policy: {_value(terminal.terminal_growth_policy_status.value if terminal else None)}",
        f"Terminal margin policy: {_value(terminal.terminal_margin_policy_status.value if terminal else None)}",
        f"Steady-state reinvestment policy: {_value(terminal.steady_state_reinvestment_status.value if terminal else None)}",
    ))
    for formulation in readiness.formulation_readiness if readiness else ():
        blockers = ",".join(item.value for item in formulation.blocking_stages) or "none"
        lines.append(f"Formulation {formulation.formulation.value}: {formulation.status.value}; blockers={blockers}")
    lines.extend((
        f"Overall reverse-DCF solver readiness: {_value(readiness.status.value if readiness else None)}",
        f"Earliest blocking stage: {_value(readiness.earliest_blocking_stage.value if readiness else None)}",
        f"Safe issue count: {len(outcome.safe_issues)}",
        "No raw provider payload, current share price, FX conversion, solver, DCF present value, terminal value, implied assumption, fair value, aggregation, stance, or UI output was produced.",
    ))
    return "\n".join(lines)
