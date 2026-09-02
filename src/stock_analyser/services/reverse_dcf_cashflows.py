"""Pure consensus-anchored FCFF construction for Milestone 10B."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math

from stock_analyser.domain import (
    DiscountRateEvidenceStatus,
    ForwardOperatingTrajectory,
    MarginalTaxRateEvidence,
    Provenance,
    ReverseDcfActualBase,
    ReverseDcfCashFlowPath,
    ReverseDcfCashFlowPeriod,
    ReverseDcfPrecedingRevenueInput,
    ReverseDcfReadinessStatus,
    ReverseDcfSalesToCapitalInput,
    ReverseDcfTimingConvention,
    SalesToCapitalEvidence,
    SalesToCapitalSourceType,
    stable_reverse_dcf_id,
)


@dataclass(frozen=True, slots=True)
class ReverseDcfCashFlowPolicy:
    economic_definition: str = "incremental revenue divided by sales-to-capital"
    timing_convention: ReverseDcfTimingConvention = ReverseDcfTimingConvention.DISCRETE_ANNUAL_FISCAL_INDEX
    policy_id: str = "reverse-dcf-sales-to-capital-fcff-v1"


DEFAULT_REVERSE_DCF_CASH_FLOW_POLICY = ReverseDcfCashFlowPolicy()


def configure_sales_to_capital_evidence(
    *,
    target_security_id: str,
    target_issuer_id: str,
    value: float,
    source_name: str,
    source_date: date,
    analysis_as_of: datetime,
    methodology: str,
    provenance: tuple[Provenance, ...],
    company_scope: str | None = None,
    industry_scope: str | None = None,
    currency_scope: str | None = None,
    policy_id: str = "configured-external-sales-to-capital-v1",
) -> SalesToCapitalEvidence:
    """Create explicit configured evidence; no anonymous numeric default exists."""
    return SalesToCapitalEvidence(
        evidence_id=stable_reverse_dcf_id(
            "salestocapital", target_security_id, target_issuer_id, value,
            source_name, source_date, methodology, company_scope, industry_scope,
            currency_scope, policy_id,
        ),
        target_security_id=target_security_id,
        target_issuer_id=target_issuer_id,
        value=value,
        economic_definition=DEFAULT_REVERSE_DCF_CASH_FLOW_POLICY.economic_definition,
        source_type=SalesToCapitalSourceType.CONFIGURED_EXTERNAL,
        source_name=source_name,
        source_date=source_date,
        analysis_as_of=analysis_as_of,
        methodology=methodology,
        currency_scope=currency_scope,
        industry_scope=industry_scope,
        company_scope=company_scope,
        status=ReverseDcfReadinessStatus.READY,
        issues=(),
        warnings=(),
        policy_id=policy_id,
        provenance=provenance,
    )


def _eligible_tax(
    tax: MarginalTaxRateEvidence | None,
    *,
    analysis_as_of: datetime,
) -> bool:
    return bool(
        tax is not None
        and tax.status is DiscountRateEvidenceStatus.ELIGIBLE
        and tax.value is not None
        and tax.analysis_as_of == analysis_as_of
        and tax.source_date is not None
        and tax.source_date <= analysis_as_of.date()
    )


def _eligible_sales_to_capital(
    evidence: SalesToCapitalEvidence | ReverseDcfSalesToCapitalInput | None,
    trajectory: ForwardOperatingTrajectory,
) -> bool:
    return bool(
        evidence is not None
        and evidence.status is ReverseDcfReadinessStatus.READY
        and evidence.value is not None
        and math.isfinite(evidence.value)
        and evidence.value > 0
        and evidence.target_security_id == trajectory.target_security_id
        and evidence.target_issuer_id == trajectory.target_issuer_id
        and evidence.analysis_as_of == trajectory.analysis_as_of
        and (
            isinstance(evidence, ReverseDcfSalesToCapitalInput)
            or (
                evidence.source_date is not None
                and evidence.source_date <= trajectory.analysis_as_of.date()
            )
        )
        and (
            not isinstance(evidence, SalesToCapitalEvidence)
            or evidence.currency_scope is None
            or evidence.currency_scope == trajectory.valuation_currency
        )
    )


def _eligible_fy1_base(
    actual_base: ReverseDcfActualBase | ReverseDcfPrecedingRevenueInput | None,
    trajectory: ForwardOperatingTrajectory,
) -> bool:
    if actual_base is None or not trajectory.periods:
        return False
    first = trajectory.periods[0]
    return bool(
        actual_base.status is ReverseDcfReadinessStatus.READY
        and actual_base.target_security_id == trajectory.target_security_id
        and actual_base.target_issuer_id == trajectory.target_issuer_id
        and actual_base.analysis_as_of == trajectory.analysis_as_of
        and actual_base.currency == trajectory.valuation_currency
        and (
            actual_base.revenue if isinstance(actual_base, ReverseDcfActualBase)
            else actual_base.value
        ) > 0
        and (
            actual_base.revenue_observation_id if isinstance(actual_base, ReverseDcfActualBase)
            else actual_base.input_id
        )
        and actual_base.fiscal_year is not None
        and actual_base.fiscal_year + 1 == first.fiscal_year
        and actual_base.period_end is not None
        and (actual_base.period_end.month, actual_base.period_end.day)
        == (first.fiscal_period_end.month, first.fiscal_period_end.day)
    )


def build_reverse_dcf_cash_flow_path(
    trajectory: ForwardOperatingTrajectory,
    actual_base: ReverseDcfActualBase | None,
    operating_tax: MarginalTaxRateEvidence | None,
    sales_to_capital: SalesToCapitalEvidence | None,
    *,
    preceding_revenue_input: ReverseDcfPrecedingRevenueInput | None = None,
    sales_to_capital_input: ReverseDcfSalesToCapitalInput | None = None,
    policy: ReverseDcfCashFlowPolicy = DEFAULT_REVERSE_DCF_CASH_FLOW_POLICY,
) -> ReverseDcfCashFlowPath:
    """Derive NOPAT and sales-to-capital FCFF without forecasting operating levels."""
    issues: list[str] = []
    tax_ready = _eligible_tax(operating_tax, analysis_as_of=trajectory.analysis_as_of)
    selected_sales = sales_to_capital_input or sales_to_capital
    selected_base = preceding_revenue_input or actual_base
    sales_ready = _eligible_sales_to_capital(selected_sales, trajectory)
    fy1_base_ready = _eligible_fy1_base(selected_base, trajectory)
    if trajectory.status is not ReverseDcfReadinessStatus.READY:
        issues.append("A complete consecutive annual consensus revenue/EBIT trajectory is required.")
    if not tax_ready:
        issues.append("Eligible same-snapshot marginal operating-tax evidence is required.")
    if not sales_ready:
        issues.append("Eligible explicit sales-to-capital evidence is required; no default was used.")
    if not fy1_base_ready:
        issues.append("FY1_REINVESTMENT_BASE is unavailable; FY1 revenue change and reinvestment were not fabricated.")

    periods: list[ReverseDcfCashFlowPeriod] = []
    if tax_ready and sales_ready:
        previous_revenue = (
            selected_base.revenue if isinstance(selected_base, ReverseDcfActualBase)
            else selected_base.value if selected_base is not None else None
        ) if fy1_base_ready else None
        previous_revenue_id = (
            selected_base.revenue_observation_id if isinstance(selected_base, ReverseDcfActualBase)
            else selected_base.input_id if selected_base is not None else None
        ) if fy1_base_ready else None
        previous_fiscal_year = selected_base.fiscal_year if fy1_base_ready else None
        sales_value = selected_sales.value
        sales_input_id = (
            selected_sales.evidence_id if isinstance(selected_sales, SalesToCapitalEvidence)
            else selected_sales.input_id
        )
        sales_provenance = selected_sales.provenance
        for index, source in enumerate(trajectory.periods, start=1):
            period_issues: list[str] = []
            revenue = source.revenue
            ebit = source.ebit
            if revenue is None or revenue <= 0 or ebit is None or source.operating_margin is None:
                continue
            nopat = ebit * (1 - operating_tax.value)
            revenue_change = None
            base_id = None
            if previous_revenue is not None and previous_fiscal_year is not None and previous_fiscal_year + 1 == source.fiscal_year:
                revenue_change = revenue - previous_revenue
                base_id = previous_revenue_id
            else:
                period_issues.append(
                    "Consecutive preceding revenue evidence is unavailable; reinvestment and FCFF were withheld."
                )
            reinvestment = revenue_change / sales_value if revenue_change is not None else None
            fcff = nopat - reinvestment if reinvestment is not None else None
            status = (
                ReverseDcfReadinessStatus.READY
                if fcff is not None else ReverseDcfReadinessStatus.NOT_READY
            )
            supporting = tuple(dict.fromkeys(item for item in (
                source.revenue_observation_id,
                source.ebit_observation_id,
                base_id,
                operating_tax.evidence_id,
                sales_input_id,
            ) if item))
            provenance = tuple(dict.fromkeys((
                *source.provenance,
                *operating_tax.provenance,
                *sales_provenance,
            )))
            periods.append(ReverseDcfCashFlowPeriod(
                period_id=stable_reverse_dcf_id(
                    "reversedcffcffperiod", trajectory.trajectory_id, source.period_id,
                    base_id, operating_tax.evidence_id, sales_input_id,
                    policy.policy_id,
                ),
                target_security_id=trajectory.target_security_id,
                target_issuer_id=trajectory.target_issuer_id,
                fiscal_period=source.fiscal_period,
                fiscal_year=source.fiscal_year,
                period_end=source.fiscal_period_end,
                discount_period_index=index,
                revenue=revenue,
                ebit=ebit,
                ebit_margin=source.operating_margin,
                tax_rate=operating_tax.value,
                nopat=nopat,
                revenue_change=revenue_change,
                sales_to_capital=sales_value,
                reinvestment=reinvestment,
                fcff=fcff,
                revenue_observation_id=source.revenue_observation_id,
                ebit_observation_id=source.ebit_observation_id,
                revenue_change_base_observation_id=base_id,
                tax_evidence_id=operating_tax.evidence_id,
                sales_to_capital_evidence_id=sales_input_id,
                status=status,
                issues=tuple(period_issues),
                supporting_ids=supporting,
                policy_id=policy.policy_id,
                provenance=provenance,
            ))
            previous_revenue = revenue
            previous_revenue_id = source.revenue_observation_id
            previous_fiscal_year = source.fiscal_year

    if (
        trajectory.status is ReverseDcfReadinessStatus.READY
        and periods
        and all(item.status is ReverseDcfReadinessStatus.READY for item in periods)
        and len(periods) == trajectory.period_count
        and fy1_base_ready and tax_ready and sales_ready
    ):
        status = ReverseDcfReadinessStatus.READY
    elif periods or trajectory.periods:
        status = ReverseDcfReadinessStatus.PARTIAL
    else:
        status = ReverseDcfReadinessStatus.UNAVAILABLE
    supporting = tuple(dict.fromkeys((
        trajectory.trajectory_id,
        *((actual_base.evidence_id,) if actual_base else ()),
        *((preceding_revenue_input.input_id,) if preceding_revenue_input else ()),
        *((operating_tax.evidence_id,) if operating_tax else ()),
        *((sales_to_capital.evidence_id,) if sales_to_capital else ()),
        *((sales_to_capital_input.input_id,) if sales_to_capital_input else ()),
        *(item for period in periods for item in period.supporting_ids),
    )))
    provenance = tuple(dict.fromkeys(item for period in periods for item in period.provenance))
    return ReverseDcfCashFlowPath(
        path_id=stable_reverse_dcf_id(
            "reversedcffcffpath", trajectory.trajectory_id,
            getattr(actual_base, "evidence_id", None),
            getattr(preceding_revenue_input, "input_id", None),
            getattr(operating_tax, "evidence_id", None),
            getattr(sales_to_capital, "evidence_id", None),
            getattr(sales_to_capital_input, "input_id", None),
            policy.policy_id,
        ),
        target_security_id=trajectory.target_security_id,
        target_issuer_id=trajectory.target_issuer_id,
        trajectory_id=trajectory.trajectory_id,
        analysis_as_of=trajectory.analysis_as_of,
        valuation_currency=trajectory.valuation_currency,
        timing_convention=policy.timing_convention,
        periods=tuple(periods),
        period_count=len(periods),
        fy1_reinvestment_base_status=(
            ReverseDcfReadinessStatus.READY if fy1_base_ready else ReverseDcfReadinessStatus.NOT_READY
        ),
        tax_status=(ReverseDcfReadinessStatus.READY if tax_ready else ReverseDcfReadinessStatus.NOT_READY),
        sales_to_capital_status=(
            ReverseDcfReadinessStatus.READY if sales_ready else ReverseDcfReadinessStatus.NOT_READY
        ),
        status=status,
        issues=tuple(issues),
        warnings=(),
        supporting_ids=supporting,
        policy_ids=(policy.policy_id,),
        provenance=provenance,
    )
