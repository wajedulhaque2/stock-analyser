"""One opt-in, bounded, secret-safe Milestone 10B META readiness audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping

from stock_analyser.domain import (
    MarketImpliedTerminalGrowthResult,
    ReverseDcfCashFlowPath,
    ReverseDcfReadinessStatus,
    TerminalMarginPolicy,
)
from stock_analyser.live_reverse_dcf_audit import (
    LiveReverseDcfAuditOutcome,
    run_live_reverse_dcf_audit,
)
from stock_analyser.services import (
    build_reverse_dcf_cash_flow_path,
    solve_market_implied_terminal_growth,
)


@dataclass(frozen=True, slots=True)
class LiveReverseDcf10BAuditOutcome:
    evidence: LiveReverseDcfAuditOutcome
    cash_flow_path: ReverseDcfCashFlowPath | None
    solver_result: MarketImpliedTerminalGrowthResult | None
    terminal_margin_policy: TerminalMarginPolicy
    terminal_margin_policy_status: ReverseDcfReadinessStatus


def run_live_reverse_dcf_10b_audit(
    symbol: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of: datetime | None = None,
) -> LiveReverseDcf10BAuditOutcome:
    """Reuse the bounded 10A evidence fetch and evaluate only 10B readiness."""
    evidence = run_live_reverse_dcf_audit(
        symbol, environment=environment, analysis_as_of=analysis_as_of,
    )
    trajectory = evidence.trajectory
    if trajectory is None:
        return LiveReverseDcf10BAuditOutcome(
            evidence=evidence,
            cash_flow_path=None,
            solver_result=None,
            terminal_margin_policy=TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN,
            terminal_margin_policy_status=ReverseDcfReadinessStatus.UNAVAILABLE,
        )

    final_margin_ready = bool(
        trajectory.status is ReverseDcfReadinessStatus.READY
        and trajectory.periods
        and math.isfinite(trajectory.periods[-1].operating_margin)
    )
    path = build_reverse_dcf_cash_flow_path(
        trajectory,
        evidence.actual_base,
        evidence.operating_tax,
        None,  # No live or configured sales-to-capital assumption is authorized in 10B.
    )
    solver = solve_market_implied_terminal_growth(
        path,
        evidence.market_anchor,
        evidence.operating_tax,
        None,  # No sales-to-capital evidence was fabricated.
        None,  # Frozen production WACC remains NOT_READY; cost of equity is not substituted.
    )
    return LiveReverseDcf10BAuditOutcome(
        evidence=evidence,
        cash_flow_path=path,
        solver_result=solver,
        terminal_margin_policy=TerminalMarginPolicy.HOLD_FINAL_CONSENSUS_MARGIN,
        terminal_margin_policy_status=(
            ReverseDcfReadinessStatus.READY
            if final_margin_ready else ReverseDcfReadinessStatus.NOT_READY
        ),
    )


def _status(value) -> str:
    return value.value if value is not None else "unavailable"


def render_live_reverse_dcf_10b_audit(outcome: LiveReverseDcf10BAuditOutcome) -> str:
    """Render canonical statuses only; never raw payloads, credentials, or assumptions."""
    evidence = outcome.evidence
    trajectory = evidence.trajectory
    path = outcome.cash_flow_path
    tax = evidence.operating_tax
    anchor = evidence.market_anchor
    solver = outcome.solver_result
    lines = (
        "V1 MILESTONE 10B REVERSE-DCF READINESS AUDIT",
        f"Target symbol: {evidence.symbol}",
        f"Analysis as-of: {evidence.analysis_as_of.isoformat()}",
        f"Forward trajectory: {_status(trajectory.status if trajectory else None)}; periods={trajectory.period_count if trajectory else 0}",
        f"FY1 reinvestment base: {_status(path.fy1_reinvestment_base_status if path else None)}",
        f"Operating marginal tax: {_status(tax.status if tax else None)}",
        "Sales-to-capital: unavailable (no live source or configured assumption authorized)",
        "Production WACC: not_ready (frozen WACC path; cost of equity not substituted)",
        f"Market TEV anchor: {_status(anchor.status if anchor else None)}",
        f"Terminal margin policy: {outcome.terminal_margin_policy.value}; status={_status(outcome.terminal_margin_policy_status)}",
        f"Explicit FCFF path: {_status(path.status if path else None)}; periods={path.period_count if path else 0}",
        f"Solver: {_status(solver.status if solver else None)}",
        f"Solver blockers: {' | '.join(solver.issues) if solver and solver.issues else 'unavailable'}",
        "No sales-to-capital ratio, WACC, FY1 reinvestment, current price, fair value, target price, aggregation, stance, or UI output was fabricated.",
    )
    return "\n".join(lines)
