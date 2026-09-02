"""Provider-independent operating evidence and readiness for a future reverse DCF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from stock_analyser.domain import (
    ActualSelectionStatus, CashFlowDefinitionEvidence, CompanyIdentity,
    DefinitionVerificationStatus, DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus, EstimateCase, ForwardOperatingPeriod,
    ForwardOperatingTrajectory, Frequency, MarginalTaxRateEvidence,
    MarketEnterpriseValueAnchor, MetricId, MetricObservation, MetricUnit,
    ObservationType, ReverseDcfActualBase, ReverseDcfFormulation,
    ReverseDcfFormulationReadiness, ReverseDcfOperatingReadiness,
    ReverseDcfReadiness, ReverseDcfReadinessStage, ReverseDcfReadinessStatus,
    ReverseDcfReinvestmentReadiness, ReverseDcfTerminalReadiness, WaccResult,
    stable_reverse_dcf_id,
)
from stock_analyser.services.consensus import ForwardConsensus
from .actual_selection import select_canonical_actual


@dataclass(frozen=True, slots=True)
class ReverseDcfInputPolicy:
    minimum_ready_forward_periods: int = 2
    maximum_market_anchor_age_calendar_days: int = 45
    policy_id: str = "reverse-dcf-operating-evidence-v1"


DEFAULT_REVERSE_DCF_INPUT_POLICY = ReverseDcfInputPolicy()


def _fiscal_symbol(identity: CompanyIdentity) -> str | None:
    return next((item.symbol for item in identity.provider_symbols if item.provider == "fiscal"), None)


def _fmp_symbol(identity: CompanyIdentity) -> str | None:
    return next((item.symbol for item in identity.provider_symbols if item.provider == "fmp"), None)


def _selected_actual(
    observations: tuple[MetricObservation, ...], metric: MetricId, period_end,
    analysis_as_of: datetime,
):
    return select_canonical_actual(
        observations, metric_id=metric, frequency=Frequency.ANNUAL,
        period_end=period_end, analysis_as_of=analysis_as_of,
    )


def build_reverse_dcf_actual_base(
    identity: CompanyIdentity,
    observations: Iterable[MetricObservation],
    *,
    analysis_as_of: datetime,
    valuation_currency: str,
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ReverseDcfActualBase:
    """Reuse canonical actual selection; annual revenue determines the base period."""
    rows = tuple(
        item for item in observations
        if isinstance(item, MetricObservation)
        and item.provenance.provider.lower() == "fiscal"
        and item.provenance.provider_symbol == _fiscal_symbol(identity)
        and item.frequency is Frequency.ANNUAL
        and item.observation_type is ObservationType.ACTUAL
        and item.unit is MetricUnit.CURRENCY
        and item.currency == valuation_currency
        and item.period_end is not None
        and item.period_end <= analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    )
    revenue_dates = sorted(
        {item.period_end for item in rows if item.metric_id is MetricId.REVENUE}, reverse=True,
    )
    issues: list[str] = []
    warnings: list[str] = []
    revenue = None
    revenue_selection = None
    for period_end in revenue_dates[:1]:
        revenue_selection = _selected_actual(rows, MetricId.REVENUE, period_end, analysis_as_of)
        if revenue_selection.status is ActualSelectionStatus.SELECTED:
            candidate = revenue_selection.selected_observation
            if candidate.value > 0:
                revenue = candidate
            else:
                issues.append("Latest canonical annual actual revenue is non-positive.")
        else:
            issues.append("Latest annual actual revenue conflicts or cannot be selected canonically.")
    if not revenue_dates:
        issues.append("Canonical annual actual revenue is unavailable.")
    selected: dict[MetricId, MetricObservation | None] = {MetricId.REVENUE: revenue}
    if revenue is not None:
        for metric in (MetricId.EBIT, MetricId.OPERATING_INCOME, MetricId.EBITDA):
            selection = _selected_actual(rows, metric, revenue.period_end, analysis_as_of)
            selected[metric] = (
                selection.selected_observation
                if selection.status is ActualSelectionStatus.SELECTED else None
            )
            if selection.status is ActualSelectionStatus.UNRESOLVED:
                warnings.append(f"Same-period canonical actual {metric.value} is conflicted and was withheld.")
    else:
        selected.update({MetricId.EBIT: None, MetricId.OPERATING_INCOME: None, MetricId.EBITDA: None})
    if selected[MetricId.EBIT] is None and selected[MetricId.OPERATING_INCOME] is not None:
        warnings.append(
            "Actual Operating Income is retained separately; it is not relabeled EBIT and does not establish actual-to-forward EBIT continuity."
        )
    supporting = tuple(
        item.observation_id for item in selected.values() if item is not None
    )
    provenance = tuple(dict.fromkeys(
        item.provenance for item in selected.values() if item is not None
    ))
    status = ReverseDcfReadinessStatus.READY if revenue is not None else ReverseDcfReadinessStatus.UNAVAILABLE
    continuity = (
        ReverseDcfReadinessStatus.READY
        if selected[MetricId.EBIT] is not None
        else ReverseDcfReadinessStatus.NOT_READY
    )
    return ReverseDcfActualBase(
        evidence_id=stable_reverse_dcf_id(
            "reversedcfactual", identity.security_id, identity.issuer_id,
            getattr(revenue, "observation_id", None), analysis_as_of, policy.policy_id,
        ),
        target_security_id=identity.security_id, target_issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of,
        fiscal_year=revenue.fiscal_year if revenue else None,
        period_start=revenue.period_start if revenue else None,
        period_end=revenue.period_end if revenue else None,
        currency=valuation_currency,
        revenue=revenue.value if revenue else None,
        revenue_observation_id=revenue.observation_id if revenue else None,
        ebit=selected[MetricId.EBIT].value if selected[MetricId.EBIT] else None,
        ebit_observation_id=selected[MetricId.EBIT].observation_id if selected[MetricId.EBIT] else None,
        operating_income=(selected[MetricId.OPERATING_INCOME].value if selected[MetricId.OPERATING_INCOME] else None),
        operating_income_observation_id=(selected[MetricId.OPERATING_INCOME].observation_id if selected[MetricId.OPERATING_INCOME] else None),
        ebitda=selected[MetricId.EBITDA].value if selected[MetricId.EBITDA] else None,
        ebitda_observation_id=selected[MetricId.EBITDA].observation_id if selected[MetricId.EBITDA] else None,
        status=status, ebit_continuity_status=continuity,
        issues=tuple(issues), warnings=tuple(warnings), supporting_ids=supporting,
        policy_id=policy.policy_id, provenance=provenance,
    )


def _valid_forward_level(
    observation: MetricObservation | None,
    *,
    metric: MetricId,
    period_end,
    consensus_as_of: datetime,
    analysis_as_of: datetime,
    currency: str,
    provider_symbol: str | None,
) -> MetricObservation | None:
    if not isinstance(observation, MetricObservation):
        return None
    if not (
        observation.metric_id is metric
        and observation.frequency is Frequency.ANNUAL
        and observation.observation_type is ObservationType.ESTIMATE
        and observation.estimate_case is EstimateCase.AVERAGE
        and observation.period_end == period_end
        and observation.as_of_at == consensus_as_of
        and observation.as_of_at <= analysis_as_of
        and observation.provenance.provider.lower() == "fmp"
        and observation.provenance.provider_symbol == provider_symbol
        and observation.currency == currency
        and observation.unit is MetricUnit.CURRENCY
    ):
        return None
    if metric is MetricId.REVENUE and observation.value <= 0:
        return None
    return observation


def build_forward_operating_trajectory(
    identity: CompanyIdentity,
    consensus: ForwardConsensus,
    actual_base: ReverseDcfActualBase,
    *,
    analysis_as_of: datetime,
    valuation_currency: str,
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ForwardOperatingTrajectory:
    """Build an AVERAGE-only annual consensus path without interpolation or forecasting."""
    issues: list[str] = []
    warnings: list[str] = []
    if consensus.as_of_at > analysis_as_of:
        issues.append("Consensus snapshot is after analysis_as_of and was withheld.")
        periods_source = ()
    elif consensus.provider_symbol != _fmp_symbol(identity):
        issues.append("Consensus provider symbol does not match canonical FMP identity evidence.")
        periods_source = ()
    else:
        periods_source = consensus.periods
    if consensus.as_of_at < analysis_as_of:
        warnings.append("Used the latest supplied eligible consensus snapshot at or before analysis_as_of.")

    fiscal_years = [period.fiscal_year for period in periods_source]
    first_fiscal_year = min(fiscal_years) if fiscal_years else None
    missing_years = tuple(
        year for year in range(min(fiscal_years), max(fiscal_years) + 1)
        if year not in set(fiscal_years)
    ) if fiscal_years else ()
    if missing_years:
        issues.append("Forward annual consensus contains fiscal-year gaps; no interpolation was performed.")

    periods: list[ForwardOperatingPeriod] = []
    previous_revenue: MetricObservation | None = None
    previous_fiscal_year: int | None = None
    for source_period in periods_source:
        horizon = source_period.fiscal_year - first_fiscal_year + 1
        label = f"FY{horizon}"
        revenue = _valid_forward_level(
            source_period.observation(MetricId.REVENUE, EstimateCase.AVERAGE),
            metric=MetricId.REVENUE, period_end=source_period.period_end,
            consensus_as_of=consensus.as_of_at, analysis_as_of=analysis_as_of,
            currency=valuation_currency, provider_symbol=_fmp_symbol(identity),
        )
        ebit = _valid_forward_level(
            source_period.observation(MetricId.EBIT, EstimateCase.AVERAGE),
            metric=MetricId.EBIT, period_end=source_period.period_end,
            consensus_as_of=consensus.as_of_at, analysis_as_of=analysis_as_of,
            currency=valuation_currency, provider_symbol=_fmp_symbol(identity),
        )
        ebitda = _valid_forward_level(
            source_period.observation(MetricId.EBITDA, EstimateCase.AVERAGE),
            metric=MetricId.EBITDA, period_end=source_period.period_end,
            consensus_as_of=consensus.as_of_at, analysis_as_of=analysis_as_of,
            currency=valuation_currency, provider_symbol=_fmp_symbol(identity),
        )
        period_issues: list[str] = []
        if revenue is None:
            period_issues.append("Positive canonical AVERAGE annual FMP revenue is unavailable.")
        if ebit is None:
            period_issues.append("Finite canonical AVERAGE annual FMP EBIT is unavailable.")
        margin = ebit.value / revenue.value if revenue is not None and ebit is not None else None
        growth = None
        growth_base = None
        if revenue is not None:
            if previous_revenue is not None and source_period.fiscal_year == previous_fiscal_year + 1:
                growth = revenue.value / previous_revenue.value - 1
                growth_base = previous_revenue.observation_id
            elif (
                previous_revenue is None
                and actual_base.status is ReverseDcfReadinessStatus.READY
                and actual_base.revenue is not None
                and actual_base.revenue_observation_id is not None
                and actual_base.fiscal_year is not None
                and source_period.fiscal_year == actual_base.fiscal_year + 1
                and actual_base.currency == valuation_currency
            ):
                growth = revenue.value / actual_base.revenue - 1
                growth_base = actual_base.revenue_observation_id
        status = (
            ReverseDcfReadinessStatus.READY if revenue is not None and ebit is not None
            else ReverseDcfReadinessStatus.PARTIAL if revenue is not None or ebit is not None or ebitda is not None
            else ReverseDcfReadinessStatus.UNAVAILABLE
        )
        source_rows = tuple(item for item in (revenue, ebit, ebitda) if item is not None)
        periods.append(ForwardOperatingPeriod(
            period_id=stable_reverse_dcf_id(
                "forwardoperating", identity.security_id, identity.issuer_id,
                label, source_period.period_end, *(item.observation_id for item in source_rows),
                policy.policy_id,
            ),
            target_security_id=identity.security_id, target_issuer_id=identity.issuer_id,
            fiscal_period=label, fiscal_year=source_period.fiscal_year,
            fiscal_period_start=source_period.period_start, fiscal_period_end=source_period.period_end,
            estimate_case=EstimateCase.AVERAGE,
            revenue=revenue.value if revenue else None,
            revenue_observation_id=revenue.observation_id if revenue else None,
            ebit=ebit.value if ebit else None,
            ebit_observation_id=ebit.observation_id if ebit else None,
            ebitda=ebitda.value if ebitda else None,
            ebitda_observation_id=ebitda.observation_id if ebitda else None,
            operating_margin=margin, revenue_growth=growth,
            growth_base_observation_id=growth_base,
            estimate_as_of=consensus.as_of_at, analysis_as_of=analysis_as_of,
            currency=valuation_currency, unit=MetricUnit.CURRENCY,
            revenue_analyst_count=revenue.analyst_count if revenue else None,
            ebit_analyst_count=ebit.analyst_count if ebit else None,
            ebitda_analyst_count=ebitda.analyst_count if ebitda else None,
            status=status, issues=tuple(period_issues), warnings=(),
            provenance=tuple(dict.fromkeys(item.provenance for item in source_rows)),
        ))
        previous_revenue = revenue
        previous_fiscal_year = source_period.fiscal_year

    def component_status(attribute: str) -> ReverseDcfReadinessStatus:
        available = sum(getattr(period, attribute) is not None for period in periods)
        if available == len(periods) and available:
            return ReverseDcfReadinessStatus.READY
        if available:
            return ReverseDcfReadinessStatus.PARTIAL
        return ReverseDcfReadinessStatus.UNAVAILABLE

    revenue_status = component_status("revenue")
    ebit_status = component_status("ebit")
    margin_status = component_status("operating_margin")
    if (
        len(periods) >= policy.minimum_ready_forward_periods
        and not missing_years
        and revenue_status is ReverseDcfReadinessStatus.READY
        and ebit_status is ReverseDcfReadinessStatus.READY
    ):
        status = ReverseDcfReadinessStatus.READY
    elif periods:
        status = ReverseDcfReadinessStatus.PARTIAL
        if len(periods) < policy.minimum_ready_forward_periods:
            issues.append("Forward consensus trajectory is shorter than the two-period readiness minimum.")
    else:
        status = ReverseDcfReadinessStatus.UNAVAILABLE
        issues.append("No eligible annual FMP forward operating periods are available.")
    supporting = tuple(dict.fromkeys(
        item for period in periods for item in (
            period.revenue_observation_id, period.ebit_observation_id, period.ebitda_observation_id,
            period.growth_base_observation_id,
        ) if item
    ))
    provenance = tuple(dict.fromkeys(item for period in periods for item in period.provenance))
    return ForwardOperatingTrajectory(
        trajectory_id=stable_reverse_dcf_id(
            "forwardtrajectory", identity.security_id, identity.issuer_id,
            consensus.as_of_at, *supporting, policy.policy_id,
        ),
        target_security_id=identity.security_id, target_issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of, valuation_currency=valuation_currency,
        estimate_case=EstimateCase.AVERAGE, periods=tuple(periods),
        period_count=len(periods), first_period=periods[0].fiscal_period if periods else None,
        last_period=periods[-1].fiscal_period if periods else None,
        missing_fiscal_years=missing_years, revenue_status=revenue_status,
        ebit_status=ebit_status, margin_status=margin_status, status=status,
        issues=tuple(issues), warnings=tuple(warnings), supporting_ids=supporting,
        policy_id=policy.policy_id, provenance=provenance,
    )


def build_market_enterprise_value_anchor(
    identity: CompanyIdentity,
    observations: Iterable[MetricObservation],
    *,
    analysis_as_of: datetime,
    valuation_currency: str,
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> MarketEnterpriseValueAnchor:
    rows = tuple(
        item for item in observations
        if isinstance(item, MetricObservation)
        and item.metric_id is MetricId.ENTERPRISE_VALUE
        and item.frequency is Frequency.POINT_IN_TIME
        and item.observation_type is ObservationType.ACTUAL
        and item.provenance.provider.lower() == "fiscal"
        and item.provenance.provider_symbol == _fiscal_symbol(identity)
        and item.provenance.source_metric == "calculated_tev"
        and item.currency == valuation_currency
        and item.unit is MetricUnit.CURRENCY
        and item.period_end is not None and item.period_end <= analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    )
    issues: list[str] = []
    selected = None
    if rows:
        latest_date = max(item.period_end for item in rows)
        selection = select_canonical_actual(
            rows, metric_id=MetricId.ENTERPRISE_VALUE, frequency=Frequency.POINT_IN_TIME,
            period_end=latest_date, analysis_as_of=analysis_as_of,
        )
        if selection.status is ActualSelectionStatus.SELECTED:
            candidate = selection.selected_observation
            if candidate.value <= 0:
                issues.append("Fiscal TEV must be finite and positive.")
            elif (analysis_as_of.date() - candidate.period_end).days > policy.maximum_market_anchor_age_calendar_days:
                issues.append("Fiscal TEV exceeds the centralized 45-day market-anchor freshness policy.")
            else:
                selected = candidate
        else:
            issues.append("Latest Fiscal TEV conflicts or cannot be selected canonically.")
    else:
        issues.append("Canonical Fiscal calculated_tev evidence is unavailable; no component reconstruction was attempted.")
    return MarketEnterpriseValueAnchor(
        anchor_id=stable_reverse_dcf_id(
            "marketenterprisevalue", identity.security_id, identity.issuer_id,
            getattr(selected, "observation_id", None), analysis_as_of, policy.policy_id,
        ),
        target_security_id=identity.security_id, target_issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of, value=selected.value if selected else None,
        currency=valuation_currency, observation_date=selected.period_end if selected else None,
        source_observation_id=selected.observation_id if selected else None,
        status=ReverseDcfReadinessStatus.READY if selected else ReverseDcfReadinessStatus.UNAVAILABLE,
        issues=tuple(issues), policy_id=policy.policy_id,
        provenance=(selected.provenance,) if selected else (),
    )


def build_reinvestment_readiness(
    forward_observations: Iterable[MetricObservation],
    historical_observations: Iterable[MetricObservation],
    definition_evidence: Iterable[CashFlowDefinitionEvidence] = (),
    *,
    analysis_as_of: datetime,
    approved_methodology_id: str | None = None,
    sales_to_capital_evidence_ids: tuple[str, ...] = (),
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ReverseDcfReinvestmentReadiness:
    forward = tuple(
        item for item in forward_observations
        if isinstance(item, MetricObservation)
        and item.observation_type is ObservationType.ESTIMATE
        and item.frequency is Frequency.ANNUAL
        and item.period_end is not None and item.period_end > analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    )
    historical = tuple(
        item for item in historical_observations
        if isinstance(item, MetricObservation)
        and item.observation_type is ObservationType.ACTUAL
        and item.period_end is not None and item.period_end <= analysis_as_of.date()
    )
    definitions = tuple(definition_evidence)
    verified_fcff_ids = {
        item_id for evidence in definitions if evidence.is_verified_fcff
        for item_id in evidence.observation_ids
    }
    verified_fcfe_ids = {
        item_id for evidence in definitions if evidence.is_verified_fcfe
        for item_id in evidence.observation_ids
    }
    forward_ids = {item.observation_id for item in forward}
    fcff = forward_ids & verified_fcff_ids
    fcfe = forward_ids & verified_fcfe_ids
    generic = tuple(item for item in forward if item.metric_id in {
        MetricId.PROVIDER_DEFINED_FCF, MetricId.OCF_LESS_CAPEX,
    })
    capex = tuple(item for item in forward if item.metric_id is MetricId.CAPITAL_EXPENDITURE)
    da = tuple(item for item in forward if item.metric_id is MetricId.DEPRECIATION_AMORTIZATION)
    nwc = tuple(item for item in forward if item.metric_id is MetricId.CHANGE_IN_WORKING_CAPITAL)
    historical_reinvestment = tuple(item for item in historical if item.metric_id in {
        MetricId.CAPITAL_EXPENDITURE, MetricId.DEPRECIATION_AMORTIZATION,
        MetricId.CHANGE_IN_WORKING_CAPITAL,
    })
    ready = lambda value: ReverseDcfReadinessStatus.READY if value else ReverseDcfReadinessStatus.UNAVAILABLE
    methodology_status = ready(approved_methodology_id)
    component_complete = bool(capex and da and nwc)
    status = (
        ReverseDcfReadinessStatus.READY
        if approved_methodology_id and (fcff or component_complete)
        else ReverseDcfReadinessStatus.PARTIAL
        if any((fcff, fcfe, generic, capex, da, nwc, historical_reinvestment, sales_to_capital_evidence_ids))
        else ReverseDcfReadinessStatus.NOT_READY
    )
    issues = []
    if not fcff:
        issues.append("Externally sourced forward cash flow does not have verified FCFF semantics.")
    if not approved_methodology_id:
        issues.append("No forward reinvestment methodology is approved; historical percentages were not forecast.")
    if generic:
        issues.append("Generic FCF/OCF-less-CapEx evidence is not relabeled FCFF or FCFE.")
    supporting = tuple(dict.fromkeys((
        *fcff, *fcfe, *(item.observation_id for item in generic),
        *(item.observation_id for item in capex), *(item.observation_id for item in da),
        *(item.observation_id for item in nwc),
        *(item.observation_id for item in historical_reinvestment),
        *sales_to_capital_evidence_ids,
        *((approved_methodology_id,) if approved_methodology_id else ()),
    )))
    provenance = tuple(dict.fromkeys(
        item.provenance for item in (*forward, *historical)
        if item.observation_id in supporting
    ))
    return ReverseDcfReinvestmentReadiness(
        readiness_id=stable_reverse_dcf_id(
            "reinvestmentreadiness", analysis_as_of, *supporting, policy.policy_id,
        ), analysis_as_of=analysis_as_of,
        forward_capex_status=ready(capex), forward_da_status=ready(da),
        forward_change_nwc_status=ready(nwc), external_fcff_status=ready(fcff),
        external_fcfe_status=ready(fcfe), generic_fcf_status=ready(generic),
        historical_reinvestment_status=ready(historical_reinvestment),
        sales_to_capital_status=ready(sales_to_capital_evidence_ids),
        methodology_status=methodology_status, status=status, issues=tuple(issues), warnings=(),
        supporting_ids=supporting, policy_id=policy.policy_id, provenance=provenance,
    )


def build_terminal_readiness(
    valuation_currency: str,
    *,
    analysis_as_of: datetime,
    terminal_growth_policy_id: str | None = None,
    terminal_margin_policy_id: str | None = None,
    steady_state_reinvestment_policy_id: str | None = None,
    discount_rate_compatible: bool = False,
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ReverseDcfTerminalReadiness:
    ready = lambda value: ReverseDcfReadinessStatus.READY if value else ReverseDcfReadinessStatus.NOT_READY
    statuses = (
        ready(terminal_growth_policy_id), ready(terminal_margin_policy_id),
        ready(steady_state_reinvestment_policy_id), ready(discount_rate_compatible),
    )
    status = ReverseDcfReadinessStatus.READY if all(
        item is ReverseDcfReadinessStatus.READY for item in statuses
    ) else ReverseDcfReadinessStatus.NOT_READY
    issues = () if status is ReverseDcfReadinessStatus.READY else (
        "No complete currency-compatible terminal growth, margin, and steady-state reinvestment policy is approved.",
    )
    return ReverseDcfTerminalReadiness(
        readiness_id=stable_reverse_dcf_id(
            "terminalreadiness", valuation_currency, analysis_as_of,
            terminal_growth_policy_id, terminal_margin_policy_id,
            steady_state_reinvestment_policy_id, discount_rate_compatible, policy.policy_id,
        ), valuation_currency=valuation_currency, analysis_as_of=analysis_as_of,
        terminal_growth_policy_status=statuses[0], terminal_margin_policy_status=statuses[1],
        steady_state_reinvestment_status=statuses[2], discount_rate_compatibility_status=statuses[3],
        status=status, issues=issues, policy_id=policy.policy_id,
    )


def build_operating_readiness(
    actual_base: ReverseDcfActualBase,
    trajectory: ForwardOperatingTrajectory,
    *, policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ReverseDcfOperatingReadiness:
    issues = tuple(dict.fromkeys((*actual_base.issues, *trajectory.issues)))
    status = (
        ReverseDcfReadinessStatus.READY
        if actual_base.status is ReverseDcfReadinessStatus.READY
        and trajectory.status is ReverseDcfReadinessStatus.READY
        else ReverseDcfReadinessStatus.PARTIAL
        if actual_base.status is ReverseDcfReadinessStatus.READY or trajectory.period_count
        else ReverseDcfReadinessStatus.UNAVAILABLE
    )
    return ReverseDcfOperatingReadiness(
        readiness_id=stable_reverse_dcf_id(
            "operatingreadiness", actual_base.evidence_id, trajectory.trajectory_id, policy.policy_id,
        ), actual_base_status=actual_base.status,
        forward_revenue_status=trajectory.revenue_status,
        forward_ebit_status=trajectory.ebit_status,
        forward_margin_status=trajectory.margin_status,
        actual_to_forward_ebit_continuity_status=actual_base.ebit_continuity_status,
        trajectory_status=trajectory.status, status=status, issues=issues,
        supporting_ids=tuple(dict.fromkeys((actual_base.evidence_id, trajectory.trajectory_id))),
        policy_id=policy.policy_id,
    )


_STAGE_ORDER = (
    ReverseDcfReadinessStage.IDENTITY,
    ReverseDcfReadinessStage.ACTUAL_BASE,
    ReverseDcfReadinessStage.FORWARD_REVENUE,
    ReverseDcfReadinessStage.FORWARD_EBIT,
    ReverseDcfReadinessStage.FORWARD_TRAJECTORY,
    ReverseDcfReadinessStage.OPERATING_TAX,
    ReverseDcfReadinessStage.REINVESTMENT,
    ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE,
    ReverseDcfReadinessStage.DISCOUNT_RATE,
    ReverseDcfReadinessStage.TERMINAL_POLICY,
    ReverseDcfReadinessStage.READY_FOR_SOLVER,
)


def assess_reverse_dcf_readiness(
    identity: CompanyIdentity,
    actual_base: ReverseDcfActualBase,
    trajectory: ForwardOperatingTrajectory,
    market_anchor: MarketEnterpriseValueAnchor,
    reinvestment: ReverseDcfReinvestmentReadiness,
    terminal: ReverseDcfTerminalReadiness,
    *,
    analysis_as_of: datetime,
    valuation_currency: str,
    operating_tax: MarginalTaxRateEvidence | None = None,
    wacc: WaccResult | None = None,
    policy: ReverseDcfInputPolicy = DEFAULT_REVERSE_DCF_INPUT_POLICY,
) -> ReverseDcfReadiness:
    identity_ready = all((identity.security_id, identity.issuer_id))
    tax_ready = bool(
        operating_tax is not None
        and operating_tax.status is DiscountRateEvidenceStatus.ELIGIBLE
        and operating_tax.analysis_as_of == analysis_as_of
        and operating_tax.value is not None
        and operating_tax.source_date is not None
        and operating_tax.source_date <= analysis_as_of.date()
        and operating_tax.jurisdiction is not None
    )
    wacc_ready = bool(
        wacc is not None
        and wacc.status is DiscountRateReadinessStatus.READY
        and wacc.security_id == identity.security_id and wacc.issuer_id == identity.issuer_id
        and wacc.analysis_as_of == analysis_as_of
        and wacc.valuation_currency == valuation_currency
        and wacc.value is not None
    )
    stages = {
        ReverseDcfReadinessStage.IDENTITY: identity_ready,
        ReverseDcfReadinessStage.ACTUAL_BASE: actual_base.status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.FORWARD_REVENUE: trajectory.revenue_status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.FORWARD_EBIT: trajectory.ebit_status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.FORWARD_TRAJECTORY: trajectory.status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.OPERATING_TAX: tax_ready,
        ReverseDcfReadinessStage.REINVESTMENT: reinvestment.status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE: market_anchor.status is ReverseDcfReadinessStatus.READY,
        ReverseDcfReadinessStage.DISCOUNT_RATE: wacc_ready,
        ReverseDcfReadinessStage.TERMINAL_POLICY: terminal.status is ReverseDcfReadinessStatus.READY,
    }

    requirements = {
        ReverseDcfFormulation.CONSENSUS_FCFF_TRAJECTORY: (
            ReverseDcfReadinessStage.ACTUAL_BASE, ReverseDcfReadinessStage.REINVESTMENT,
            ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE, ReverseDcfReadinessStage.DISCOUNT_RATE,
            ReverseDcfReadinessStage.TERMINAL_POLICY,
        ),
        ReverseDcfFormulation.CONSENSUS_REVENUE_MARGIN_WITH_REINVESTMENT: (
            ReverseDcfReadinessStage.ACTUAL_BASE, ReverseDcfReadinessStage.FORWARD_REVENUE,
            ReverseDcfReadinessStage.FORWARD_EBIT, ReverseDcfReadinessStage.FORWARD_TRAJECTORY,
            ReverseDcfReadinessStage.OPERATING_TAX, ReverseDcfReadinessStage.REINVESTMENT,
            ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE, ReverseDcfReadinessStage.DISCOUNT_RATE,
            ReverseDcfReadinessStage.TERMINAL_POLICY,
        ),
        ReverseDcfFormulation.MARKET_IMPLIED_REVENUE_GROWTH: (
            ReverseDcfReadinessStage.ACTUAL_BASE, ReverseDcfReadinessStage.FORWARD_REVENUE,
            ReverseDcfReadinessStage.FORWARD_EBIT, ReverseDcfReadinessStage.FORWARD_TRAJECTORY,
            ReverseDcfReadinessStage.OPERATING_TAX, ReverseDcfReadinessStage.REINVESTMENT,
            ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE, ReverseDcfReadinessStage.DISCOUNT_RATE,
            ReverseDcfReadinessStage.TERMINAL_POLICY,
        ),
        ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_MARGIN: (
            ReverseDcfReadinessStage.ACTUAL_BASE, ReverseDcfReadinessStage.FORWARD_REVENUE,
            ReverseDcfReadinessStage.FORWARD_EBIT, ReverseDcfReadinessStage.FORWARD_TRAJECTORY,
            ReverseDcfReadinessStage.OPERATING_TAX, ReverseDcfReadinessStage.REINVESTMENT,
            ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE, ReverseDcfReadinessStage.DISCOUNT_RATE,
            ReverseDcfReadinessStage.TERMINAL_POLICY,
        ),
        # 10B correction: actual history is not intrinsically required to solve
        # a terminal-growth variable from an external FY1+ path.  The authorized
        # sales-to-capital cash-flow method applies its narrower, explicit
        # FY1_REINVESTMENT_BASE gate in reverse_dcf_cashflows instead.
        ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_GROWTH: (
            ReverseDcfReadinessStage.FORWARD_REVENUE,
            ReverseDcfReadinessStage.FORWARD_EBIT,
            ReverseDcfReadinessStage.FORWARD_TRAJECTORY,
            ReverseDcfReadinessStage.OPERATING_TAX,
            ReverseDcfReadinessStage.REINVESTMENT,
            ReverseDcfReadinessStage.MARKET_ENTERPRISE_VALUE,
            ReverseDcfReadinessStage.DISCOUNT_RATE,
            ReverseDcfReadinessStage.TERMINAL_POLICY,
        ),
    }
    formulation_results = []
    for formulation, required in requirements.items():
        blockers = tuple(stage for stage in required if not stages[stage])
        if formulation is ReverseDcfFormulation.CONSENSUS_FCFF_TRAJECTORY and (
            reinvestment.external_fcff_status is not ReverseDcfReadinessStatus.READY
        ):
            blockers = tuple(dict.fromkeys((ReverseDcfReadinessStage.REINVESTMENT, *blockers)))
        satisfied_count = sum(stages[stage] for stage in required if stage in stages)
        status = (
            ReverseDcfReadinessStatus.READY if not blockers
            else ReverseDcfReadinessStatus.PARTIAL if satisfied_count
            else ReverseDcfReadinessStatus.NOT_READY
        )
        formulation_results.append(ReverseDcfFormulationReadiness(
            formulation=formulation, status=status, blocking_stages=blockers,
            issues=tuple(f"Missing prerequisite: {stage.value}." for stage in blockers),
            supporting_ids=tuple(dict.fromkeys((
                actual_base.evidence_id, trajectory.trajectory_id, market_anchor.anchor_id,
                reinvestment.readiness_id, terminal.readiness_id,
                *((operating_tax.evidence_id,) if operating_tax else ()),
                *((wacc.result_id,) if wacc else ()),
            ))),
        ))
    supported = tuple(item.formulation for item in formulation_results if item.status is ReverseDcfReadinessStatus.READY)
    blocked = tuple(item.formulation for item in formulation_results if item.status is not ReverseDcfReadinessStatus.READY)
    earliest = next((stage for stage in _STAGE_ORDER[:-1] if not stages.get(stage, False)), ReverseDcfReadinessStage.READY_FOR_SOLVER)
    issues = tuple(dict.fromkeys((
        *actual_base.issues, *trajectory.issues, *market_anchor.issues,
        *reinvestment.issues, *terminal.issues,
        *(("Eligible explicit marginal operating-tax evidence is unavailable.",) if not tax_ready else ()),
        *(("Production WACC is not ready; cost of equity was not substituted.",) if not wacc_ready else ()),
    )))
    supporting = tuple(dict.fromkeys((
        actual_base.evidence_id, trajectory.trajectory_id, market_anchor.anchor_id,
        reinvestment.readiness_id, terminal.readiness_id,
        *actual_base.supporting_ids, *trajectory.supporting_ids,
        *((operating_tax.evidence_id,) if operating_tax else ()),
        *((wacc.result_id,) if wacc else ()),
    )))
    provenance = tuple(dict.fromkeys((
        *actual_base.provenance, *trajectory.provenance, *market_anchor.provenance,
        *reinvestment.provenance,
        *((operating_tax.provenance) if operating_tax else ()),
        *((wacc.provenance) if wacc else ()),
    )))
    status = ReverseDcfReadinessStatus.READY if supported else ReverseDcfReadinessStatus.NOT_READY
    return ReverseDcfReadiness(
        readiness_id=stable_reverse_dcf_id(
            "reversedcfreadiness", identity.security_id, identity.issuer_id,
            analysis_as_of, *supporting, policy.policy_id,
        ), target_security_id=identity.security_id, target_issuer_id=identity.issuer_id,
        analysis_as_of=analysis_as_of, valuation_currency=valuation_currency,
        actual_base_period=actual_base.period_end, actual_base_status=actual_base.status,
        forward_trajectory_id=trajectory.trajectory_id,
        forward_period_count=trajectory.period_count,
        forward_trajectory_status=trajectory.status,
        market_enterprise_value_anchor_id=market_anchor.anchor_id,
        market_anchor_status=market_anchor.status,
        operating_tax_status=(ReverseDcfReadinessStatus.READY if tax_ready else ReverseDcfReadinessStatus.NOT_READY),
        reinvestment_status=reinvestment.status,
        discount_rate_status=(ReverseDcfReadinessStatus.READY if wacc_ready else ReverseDcfReadinessStatus.NOT_READY),
        terminal_policy_status=terminal.status,
        formulation_readiness=tuple(formulation_results), supported_formulations=supported,
        blocked_formulations=blocked, earliest_blocking_stage=earliest, status=status,
        issues=issues, warnings=tuple(dict.fromkeys((*actual_base.warnings, *trajectory.warnings, *reinvestment.warnings))),
        supporting_ids=supporting, policy_ids=(policy.policy_id,), provenance=provenance,
    )
