"""Explicit, secret-safe live own-history valuation audit support."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping

from stock_analyser.application.research_build_context import (
    NormalizedEvidenceKey,
    ResearchBuildContext,
    ResearchBuildStageStatus,
)
from stock_analyser.application.live_configuration import LiveConfigurationSource
from stock_analyser.application.v1_research import ResearchReportPipelineStage

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    DataIssue,
    EstimateCase,
    HistoricalMultipleType,
    HistoricalWindow,
    IssueSeverity,
    OwnHistoryMethodStatus,
    ShareCountSemantics,
    ValuationMethodStatus,
)
from stock_analyser.live_identity import (
    IdentityDiagnosticStage,
    IdentityLiveDiagnostic,
    assemble_live_identity,
    build_live_fiscal_adapter,
    resolve_live_target_identity,
)
from stock_analyser.live_smoke import build_live_transport
from stock_analyser.providers import (
    FmpAdapter,
    IdentityCandidate,
    ProviderId,
    SecretReference,
)
from stock_analyser.services import (
    IdentitySeed,
    OwnHistoryAuditStage,
    OwnHistoryOrchestrationResult,
    assemble_own_history_valuation,
)


class PortabilityClassification(str, Enum):
    PROVIDER_BOUNDARY = "PROVIDER_BOUNDARY"
    IDENTITY = "IDENTITY"
    HISTORICAL_DISTRIBUTION = "HISTORICAL_DISTRIBUTION"
    FORWARD_CONSENSUS = "FORWARD_CONSENSUS"
    DENOMINATOR_ALIGNMENT = "DENOMINATOR_ALIGNMENT"
    CAPITAL_BRIDGE = "CAPITAL_BRIDGE"
    SHARE_SEMANTICS = "SHARE_SEMANTICS"
    CURRENCY_UNIT = "CURRENCY_UNIT"
    ORCHESTRATION = "ORCHESTRATION"
    EXPECTED_UNAVAILABLE = "EXPECTED_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class LiveOwnHistoryAuditOutcome:
    symbol: str
    result: OwnHistoryOrchestrationResult | None
    failure_stage: OwnHistoryAuditStage | None
    issues: tuple[DataIssue, ...]
    failure_classification: PortabilityClassification | None = None
    identity_diagnostic: IdentityLiveDiagnostic | None = None

    @property
    def succeeded(self) -> bool:
        if self.result is None:
            return False
        return all(
            valuation.status in {ValuationMethodStatus.VALID, ValuationMethodStatus.PARTIAL}
            for valuation in self.result.valuations
        ) and all(
            item.readiness.status is not OwnHistoryMethodStatus.READY
            or (
                item.valuation is not None
                and item.failure_stage is None
                and item.scale_audit.scale_compatibility_passed
                and item.arithmetic_audits
                and all(check.passed for check in item.arithmetic_audits)
            )
            for item in self.result.methods
        )


def _secret(name: str, environment: Mapping[str, str]) -> SecretReference:
    return SecretReference(name, lambda: environment[name])


def run_live_own_history_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
    historical_window: HistoricalWindow = HistoricalWindow.FIVE_YEAR,
    build_context: ResearchBuildContext | None = None,
    identity_configuration_source: LiveConfigurationSource | None = None,
) -> LiveOwnHistoryAuditOutcome:
    """Make the bounded Fiscal/FMP calls needed for one explicit local audit."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol must be non-empty")
    snapshot = analysis_as_of or datetime.now(timezone.utc)
    if snapshot.tzinfo is None or snapshot.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")

    identity_token = build_context.start_stage(ResearchReportPipelineStage.IDENTITY) if build_context else None
    identity_resolution = resolve_live_target_identity(
        normalized_symbol,
        environment=environment,
        analysis_as_of=snapshot,
        build_context=build_context,
        configuration_source=identity_configuration_source,
    )
    fiscal_identity = identity_resolution.fiscal_result
    yahoo_identity = identity_resolution.yahoo_result
    diagnostic = identity_resolution.diagnostic
    if identity_resolution.identity is None or fiscal_identity is None:
        identity_issues = tuple(identity_resolution.issues) or (DataIssue(
            severity=IssueSeverity.BLOCKING,
            metric="identity",
            provider="fiscal",
            reason=diagnostic.safe_reason,
        ),)
        classification = (
            PortabilityClassification.IDENTITY
            if diagnostic.blocking_stage is IdentityDiagnosticStage.CANONICAL_ASSEMBLY
            else PortabilityClassification.PROVIDER_BOUNDARY
        )
        outcome = LiveOwnHistoryAuditOutcome(
            normalized_symbol,
            None,
            OwnHistoryAuditStage.IDENTITY,
            identity_issues,
            classification,
            diagnostic,
        )
        if build_context is not None and identity_token is not None:
            build_context.finish_stage(
                identity_token,
                status=ResearchBuildStageStatus.FAILED,
                safe_blocker=diagnostic.safe_reason,
                issues_count=len(identity_issues),
            )
        return outcome

    provisional = identity_resolution.identity
    candidates = tuple(item for item in (
        fiscal_identity.candidate,
        yahoo_identity.candidate if yahoo_identity is not None else None,
        IdentityCandidate(
            provider=ProviderId.FMP,
            provider_symbol=normalized_symbol,
            retrieved_at=snapshot,
        ),
    ) if item is not None)
    if build_context is not None and identity_token is not None:
        build_context.finish_stage(
            identity_token,
            issues_count=len(identity_resolution.issues),
        )

    fiscal = build_live_fiscal_adapter(environment)
    own_token = build_context.start_stage(ResearchReportPipelineStage.OWN_HISTORY) if build_context else None
    target_id = f"{provisional.security_id}|{provisional.issuer_id}"
    history_key = NormalizedEvidenceKey(
        "fiscal", "historical_valuation", target_id, snapshot,
        (("window", historical_window.value),),
    )
    bridge_key = NormalizedEvidenceKey("fiscal", "enterprise_bridge", target_id, snapshot)
    history = (
        build_context.get_or_create_normalized(
            history_key, lambda: fiscal.fetch_historical_valuation(provisional, analysis_as_of=snapshot),
        ) if build_context else fiscal.fetch_historical_valuation(provisional, analysis_as_of=snapshot)
    )
    bridge = (
        build_context.get_or_create_normalized(
            bridge_key, lambda: fiscal.fetch_enterprise_bridge(provisional, analysis_as_of=snapshot),
        ) if build_context else fiscal.fetch_enterprise_bridge(provisional, analysis_as_of=snapshot)
    )
    fmp = FmpAdapter(
        build_live_transport(), credential=_secret("FMP_API_KEY", environment),
    )
    estimates_key = NormalizedEvidenceKey(
        "fmp", "annual_estimates", target_id, snapshot,
        (("frequency", "annual"),),
    )
    estimates = (
        build_context.get_or_create_normalized(
            estimates_key, lambda: fmp.fetch_annual_estimates(provisional, source_as_of_at=snapshot),
        ) if build_context else fmp.fetch_annual_estimates(provisional, source_as_of_at=snapshot)
    )
    provider_issues = (
        *identity_resolution.issues,
        *history.issues,
        *bridge.issues,
        *estimates.issues,
    )
    result = assemble_own_history_valuation(
        identity_seed=_identity_seed(normalized_symbol, candidates),
        identity_candidates=candidates,
        historical_observations=history.observations,
        forward_observations=estimates.observations,
        bridge_observations=bridge.observations,
        analysis_as_of=snapshot,
        historical_window=historical_window,
        estimate_case=EstimateCase.AVERAGE,
        share_count_semantics=ShareCountSemantics.ISSUER_SHARES,
    )
    outcome = LiveOwnHistoryAuditOutcome(
        normalized_symbol,
        result,
        None if result.valuations else next(
            (item.failure_stage for item in result.methods if item.failure_stage is not None),
            OwnHistoryAuditStage.READINESS,
        ),
        tuple(dict.fromkeys((*provider_issues, *result.issues))),
        identity_diagnostic=diagnostic,
    )
    if build_context is not None and own_token is not None:
        build_context.finish_stage(
            own_token,
            issues_count=len(outcome.issues),
            safe_blocker=(
                None if outcome.result is not None
                else "Own-history orchestration was unavailable."
            ),
        )
    return outcome


def _identity_seed(symbol: str, candidates: tuple[IdentityCandidate, ...] = ()) -> IdentitySeed:
    fiscal = next((
        item for item in candidates
        if (
            item.provider is ProviderId.FISCAL
            and item.provider_issuer_id
            and item.provider_security_id
        )
    ), None)
    return IdentitySeed(
        canonical_symbol=symbol,
        security_id=(
            f"security:fiscal:{fiscal.provider_security_id}"
            if fiscal is not None else f"v1-security:{symbol}"
        ),
        issuer_id=(
            f"issuer:fiscal:{fiscal.provider_issuer_id}"
            if fiscal is not None else f"v1-issuer:{symbol}"
        ),
    )


def assemble_identity_only(symbol: str, candidates: tuple[IdentityCandidate, ...]):
    """Resolve the same canonical identity once before adapter data calls."""
    fiscal = next((item for item in candidates if item.provider is ProviderId.FISCAL), None)
    if fiscal is None:
        raise ValueError("Fiscal identity evidence is unavailable")
    return assemble_live_identity(symbol, fiscal, candidates)


def _number(value: float | None, decimals: int = 2) -> str:
    return "unavailable" if value is None else f"{value:,.{decimals}f}"


def _pass(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _method_label(value: HistoricalMultipleType) -> str:
    return {
        HistoricalMultipleType.P_E: "P/E",
        HistoricalMultipleType.EV_EBITDA: "EV/EBITDA",
        HistoricalMultipleType.EV_EBIT: "EV/EBIT",
    }[value]


def classify_method_failure(method) -> PortabilityClassification | None:
    if method.executed:
        return None
    reasons = " ".join(method.readiness.blocking_reasons).lower()
    stage_classification = {
        OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS: PortabilityClassification.HISTORICAL_DISTRIBUTION,
        OwnHistoryAuditStage.DISTRIBUTION: PortabilityClassification.HISTORICAL_DISTRIBUTION,
        OwnHistoryAuditStage.FORWARD_DENOMINATOR: PortabilityClassification.FORWARD_CONSENSUS,
        OwnHistoryAuditStage.ALIGNMENT: PortabilityClassification.DENOMINATOR_ALIGNMENT,
        OwnHistoryAuditStage.BRIDGE: PortabilityClassification.CAPITAL_BRIDGE,
        OwnHistoryAuditStage.READINESS: PortabilityClassification.EXPECTED_UNAVAILABLE,
        OwnHistoryAuditStage.VALUATION: PortabilityClassification.ORCHESTRATION,
        OwnHistoryAuditStage.IDENTITY: PortabilityClassification.IDENTITY,
        None: PortabilityClassification.EXPECTED_UNAVAILABLE,
    }[method.failure_stage]
    if method.failure_stage in {
        OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS,
        OwnHistoryAuditStage.DISTRIBUTION,
        OwnHistoryAuditStage.FORWARD_DENOMINATOR,
        OwnHistoryAuditStage.ALIGNMENT,
        OwnHistoryAuditStage.IDENTITY,
    }:
        return stage_classification
    if "share" in reasons or "adr" in reasons or "depositary" in reasons:
        return PortabilityClassification.SHARE_SEMANTICS
    if "currency" in reasons or (
        method.failure_stage is OwnHistoryAuditStage.VALUATION
        and not method.scale_audit.currency_compatibility_passed
    ):
        return PortabilityClassification.CURRENCY_UNIT
    return stage_classification


def render_live_own_history_audit(outcome: LiveOwnHistoryAuditOutcome) -> str:
    """Render allowlisted normalized inputs and derived values; never raw payloads."""
    lines = ["V1 OWN-HISTORY LIVE VALUATION AUDIT", f"Security: {outcome.symbol}"]
    if outcome.result is None:
        lines.append(f"Status: FAILED at {(outcome.failure_stage or OwnHistoryAuditStage.IDENTITY).value}")
        lines.append(
            f"classification: {(outcome.failure_classification or PortabilityClassification.ORCHESTRATION).value}"
        )
        lines.extend(f"Issue: {issue.reason}" for issue in outcome.issues)
        return "\n".join(lines)
    audit = outcome.result
    identity = audit.identity
    bridge = audit.direct_bridge
    lines.extend((
        f"analysis_as_of: {audit.analysis_as_of.isoformat()}",
        f"canonical symbol: {identity.canonical_symbol}",
        f"canonical security identity: {identity.security_id}",
        f"canonical issuer identity: {identity.issuer_id}",
        f"company: {identity.company_name}",
        f"listing: {identity.exchange} / {identity.listing_country}",
        f"issuer domicile: {identity.issuer_domicile}",
        f"security type: {identity.security_type}",
        f"ADR ratio: {identity.adr_ratio if identity.adr_ratio is not None else 'not applicable or unavailable'}",
        f"reporting currency: {identity.reporting_currency}",
        f"quote currency: {identity.quote_currency}",
        f"quote unit: {identity.quote_unit}",
        f"quote price scale: {identity.price_scale:g}",
        f"fiscal year end: {identity.fiscal_year_end}",
        "provider symbols: " + ", ".join(
            f"{item.provider}={item.symbol}" for item in identity.provider_symbols
        ),
        f"historical window: {audit.historical_window.value}",
        f"forward period: {audit.forward_period.value}",
        f"estimate case: {audit.estimate_case.value}",
        "sampling: DAILY",
        "independent fundamental regime count: UNKNOWN",
        f"bridge completeness: {bridge.completeness_status.value}",
        f"bridge currency: {bridge.currency or 'unavailable'}",
        f"Fiscal TEV: {_number(bridge.enterprise_value)}",
        f"Fiscal market cap: {_number(bridge.market_cap)}",
        f"enterprise-equity adjustment: {_number(bridge.enterprise_equity_adjustment)}",
        f"approved shares outstanding: {_number(bridge.shares_outstanding)}",
    ))
    for method in audit.methods:
        dist = method.distribution
        ready = method.readiness
        lines.extend((
            "",
            f"Method: {_method_label(method.multiple_type)}",
            f"readiness: {ready.status.value}",
            f"historical window start/end: {dist.window_start.isoformat()} / {dist.window_end.isoformat()}",
            f"latest historical ratio observation date: {dist.latest_observation or 'unavailable'}",
            f"candidate count: {dist.total_candidate_count}",
            f"eligible count: {dist.eligible_count}",
            f"ineligible count: {dist.ineligible_count}",
            f"sample span: {dist.actual_span_days if dist.actual_span_days is not None else 'unavailable'} days",
            f"sample usability: {dist.usability.value}",
            f"P25 / median / P75: {_number(dist.p25)} / {_number(dist.median)} / {_number(dist.p75)}",
            f"minimum / maximum: {_number(dist.minimum)} / {_number(dist.maximum)}",
            f"distribution ID: {dist.distribution_id}",
            f"FMP FY1 {ready.forward_selection.forward_metric_id.value} availability: {'AVAILABLE' if method.forward_denominator is not None else 'UNAVAILABLE'}",
            *(() if method.forward_denominator is None else (
                f"FMP FY1 value/currency: {_number(method.forward_denominator.value)} {method.forward_denominator.currency}",
                f"FMP FY1 period end: {method.forward_denominator.period_end}",
            )),
            f"denominator alignment: {ready.denominator_alignment.compatibility_status.value}",
        ))
        if ready.status is not OwnHistoryMethodStatus.READY:
            lines.extend(f"withheld reason: {reason}" for reason in ready.blocking_reasons)
            lines.append(f"valuation executed: NO; failing stage: {(method.failure_stage or OwnHistoryAuditStage.READINESS).value}")
            lines.append(f"classification: {classify_method_failure(method).value}")
            continue
        forward = method.forward_denominator
        scale = method.scale_audit
        lines.extend((
            f"selected forward observation ID: {forward.observation_id}",
            f"FY1 average {forward.metric_id.value}: {_number(forward.value)}",
            f"FMP selected period end: {forward.period_end}",
            f"FMP estimate as-of: {forward.as_of_at.isoformat()}",
            f"currency: {forward.currency}",
            f"direct bridge ID: {bridge.bridge_id}",
            f"Fiscal TEV: {_number(bridge.enterprise_value)}",
            f"Fiscal market cap: {_number(bridge.market_cap)}",
            f"enterprise-equity adjustment: {_number(bridge.enterprise_equity_adjustment)}",
            f"approved shares outstanding: {_number(bridge.shares_outstanding)}",
            f"Fiscal TEV observation date: {bridge.enterprise_value_observation_date}",
            f"Fiscal market-cap observation date: {bridge.market_cap_observation_date}",
            f"Fiscal share-count observation date: {bridge.shares_observation_date}",
            f"forward denominator canonical unit: {scale.forward_unit.value if scale.forward_unit else 'unavailable'}",
            f"bridge TEV canonical unit: {scale.enterprise_value_unit.value if scale.enterprise_value_unit else 'unavailable'}",
            f"bridge market cap canonical unit: {scale.market_cap_unit.value if scale.market_cap_unit else 'unavailable'}",
            f"share count canonical unit: {scale.share_count_unit.value if scale.share_count_unit else 'unavailable'}",
            f"unit check: {_pass(scale.unit_compatibility_passed)}",
            f"currency alignment: {_pass(scale.currency_compatibility_passed)}",
            f"scale compatibility: {_pass(scale.scale_compatibility_passed)}",
            f"bridge check: {_pass(bridge.enterprise_equity_adjustment == bridge.enterprise_value - bridge.market_cap)}",
            f"share check: {_pass(bridge.shares_outstanding is not None and bridge.shares_outstanding > 0)}",
            f"bridge date check: {_pass(not bridge.date_gap_exceeded and bridge.completeness_status is CapitalStructureCompleteness.COMPLETE)}",
        ))
        valuation = method.valuation
        if valuation is None:
            lines.append(f"valuation executed: NO; failing stage: {(method.failure_stage or OwnHistoryAuditStage.VALUATION).value}")
            continue
        lines.append(f"Own-history {_method_label(method.multiple_type)} method value:")
        for point, arithmetic in zip(
            (valuation.lower_point, valuation.central_point, valuation.upper_point),
            method.arithmetic_audits,
        ):
            lines.extend((
                f"  {point.statistic.value}:",
                f"    historical multiple: {_number(point.historical_multiple)}",
                f"    implied enterprise value: {_number(point.implied_enterprise_value)}",
                f"    implied equity value: {_number(point.implied_equity_value)}",
                f"    per-share value: {_number(point.per_share_value)} {point.currency}",
                f"    implied EV reconstruction: {_pass(arithmetic.implied_enterprise_value_passed)}",
                f"    equity adjustment reconstruction: {_pass(arithmetic.enterprise_equity_adjustment_passed)}",
                f"    implied equity reconstruction: {_pass(arithmetic.implied_equity_value_passed)}",
                f"    per-share reconstruction: {_pass(arithmetic.per_share_value_passed)}",
                f"    independent arithmetic reconstruction: {_pass(arithmetic.passed)}",
            ))
        lines.append(f"Method status: {valuation.status.value}")
    lines.extend((
        "",
        f"Audit status: {'PASS' if outcome.succeeded else 'FAIL'}",
        "No current market price, upside/downside, analyst target, FMP DCF, aggregation, or stance was used.",
    ))
    if outcome.issues:
        lines.append("Safe diagnostics:")
        lines.extend(
            f"  {issue.provider}: {issue.reason}"
            for issue in tuple(dict.fromkeys(outcome.issues))
        )
    return "\n".join(lines)
