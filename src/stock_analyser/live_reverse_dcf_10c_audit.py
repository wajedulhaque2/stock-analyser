"""One opt-in, secret-safe Milestone 10C execution-mode readiness audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from stock_analyser.domain import (
    ReverseDcfExecutionMode,
    ReverseDcfExecutionResult,
)
from stock_analyser.live_reverse_dcf_audit import (
    LiveReverseDcfAuditOutcome,
    run_live_reverse_dcf_audit,
)
from stock_analyser.services import execute_reverse_dcf


@dataclass(frozen=True, slots=True)
class LiveReverseDcf10CAuditOutcome:
    evidence: LiveReverseDcfAuditOutcome
    canonical_execution: ReverseDcfExecutionResult | None
    scenario_execution: ReverseDcfExecutionResult | None


def run_live_reverse_dcf_10c_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
) -> LiveReverseDcf10CAuditOutcome:
    """Evaluate both explicit modes without entering any scenario assumption."""
    evidence = run_live_reverse_dcf_audit(
        symbol, environment=environment, analysis_as_of=analysis_as_of,
    )
    if evidence.trajectory is None or evidence.market_anchor is None:
        return LiveReverseDcf10CAuditOutcome(evidence, None, None)
    common = dict(
        trajectory=evidence.trajectory,
        actual_base=evidence.actual_base,
        market_anchor=evidence.market_anchor,
        operating_tax=evidence.operating_tax,
        canonical_sales_to_capital=None,
        production_wacc=None,
        scenario_assumptions=(),
    )
    canonical = execute_reverse_dcf(
        execution_mode=ReverseDcfExecutionMode.CANONICAL_EVIDENCE,
        **common,
    )
    scenario = execute_reverse_dcf(
        execution_mode=ReverseDcfExecutionMode.EXPLICIT_SCENARIO,
        **common,
    )
    return LiveReverseDcf10CAuditOutcome(evidence, canonical, scenario)


def render_live_reverse_dcf_10c_audit(outcome: LiveReverseDcf10CAuditOutcome) -> str:
    """Render controlled statuses only, never provider payloads or assumption values."""
    canonical = outcome.canonical_execution
    scenario = outcome.scenario_execution
    canonical_status = canonical.solver_result.status.value if canonical else "unavailable"
    canonical_blockers = ", ".join(canonical.canonical_blockers) if canonical else "unavailable"
    scenario_status = (
        "awaiting_explicit_assumptions"
        if scenario is not None and scenario.solver_result.status.value == "not_ready"
        else scenario.solver_result.status.value if scenario else "unavailable"
    )
    publication = scenario.publication_eligibility.value if scenario else "unavailable"
    return "\n".join((
        "V1 MILESTONE 10C REVERSE-DCF EXECUTION-MODE AUDIT",
        f"Target symbol: {outcome.evidence.symbol}",
        f"Analysis as-of: {outcome.evidence.analysis_as_of.isoformat()}",
        f"Canonical mode: {canonical_status}",
        f"Canonical blockers: {canonical_blockers}",
        f"Scenario mode: {scenario_status}",
        "Required explicit scenario assumptions: preceding_annual_revenue, sales_to_capital, wacc",
        f"Publication eligibility: {publication}",
        "Implied terminal growth: unavailable",
        "No scenario assumptions, provider additions, current price, fair value, aggregation, stance, or UI output were introduced.",
    ))
