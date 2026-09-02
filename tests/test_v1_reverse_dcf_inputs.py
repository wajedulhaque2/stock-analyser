from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
from types import SimpleNamespace

import pytest

from stock_analyser.domain import (
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    CompanyIdentity,
    DefinitionVerificationStatus,
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    EstimateCase,
    Frequency,
    MarginalTaxRateEvidence,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    ProviderSymbol,
    ReverseDcfFormulation,
    ReverseDcfReadinessStage,
    ReverseDcfReadinessStatus,
    WaccResult,
)
from stock_analyser.services import (
    assess_reverse_dcf_readiness,
    build_forward_consensus,
    build_forward_operating_trajectory,
    build_market_enterprise_value_anchor,
    build_operating_readiness,
    build_reinvestment_readiness,
    build_reverse_dcf_actual_base,
    build_terminal_readiness,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
ACTUAL_AS_OF = datetime(2026, 2, 1, tzinfo=timezone.utc)


def identity(**changes) -> CompanyIdentity:
    values = dict(
        canonical_symbol="SYN", security_id="security:synthetic:reverse-dcf",
        issuer_id="issuer:synthetic:reverse-dcf", company_name="Synthetic Inc.",
        issuer_domicile="US", listing_country="US", exchange="NASDAQ",
        sector="Communication Services", industry="Internet Content & Information",
        security_type="Common stock", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1.0, fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "SYN"), ProviderSymbol("fmp", "SYN")),
    )
    values.update(changes)
    return CompanyIdentity(**values)


def observation(
    metric: MetricId,
    value: float,
    *,
    year: int = 2025,
    provider: str = "fiscal",
    provider_symbol: str = "SYN",
    source_metric: str | None = None,
    case: EstimateCase = EstimateCase.NOT_APPLICABLE,
    observation_type: ObservationType = ObservationType.ACTUAL,
    frequency: Frequency = Frequency.ANNUAL,
    as_of: datetime = ACTUAL_AS_OF,
    end: date | None = None,
    currency: str = "USD",
    analyst_count: int | None = None,
    suffix: str = "base",
) -> MetricObservation:
    end = end or date(year, 12, 31)
    start = None if frequency is Frequency.POINT_IN_TIME else date(year, 1, 1)
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset=(
            "fmp_annual_analyst_estimates" if provider == "fmp"
            else "fiscal_enterprise_bridge_metrics" if frequency is Frequency.POINT_IN_TIME
            else "fiscal_standardized_financials"
        ),
        provider_symbol=provider_symbol,
        retrieved_at=NOW,
        as_of_at=as_of,
        source_metric=source_metric or metric.value,
    )
    return MetricObservation(
        observation_id=f"obs:reverse:{provider}:{metric.value}:{year}:{case.value}:{suffix}",
        metric_id=metric,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency=currency,
        frequency=frequency,
        observation_type=observation_type,
        estimate_case=case,
        period_start=start,
        period_end=end,
        fiscal_year=year if frequency is Frequency.ANNUAL else None,
        retrieved_at=NOW,
        as_of_at=as_of,
        analyst_count=analyst_count,
        provenance=provenance,
    )


def estimate(
    metric: MetricId,
    value: float,
    year: int,
    *,
    case: EstimateCase = EstimateCase.AVERAGE,
    currency: str = "USD",
    provider: str = "fmp",
    provider_symbol: str = "SYN",
    as_of: datetime = NOW,
    analyst_count: int | None = None,
    suffix: str = "base",
) -> MetricObservation:
    return observation(
        metric, value, year=year, provider=provider, provider_symbol=provider_symbol,
        case=case, observation_type=ObservationType.ESTIMATE, as_of=as_of,
        currency=currency, analyst_count=analyst_count, suffix=suffix,
    )


def actual_rows(*, include_ebit: bool = True) -> tuple[MetricObservation, ...]:
    rows = [
        observation(MetricId.REVENUE, 100, suffix="revenue"),
        observation(MetricId.OPERATING_INCOME, 18, suffix="operating-income"),
        observation(MetricId.EBITDA, 25, suffix="ebitda"),
    ]
    if include_ebit:
        rows.append(observation(MetricId.EBIT, 20, suffix="ebit"))
    return tuple(rows)


def forward_rows(
    years: tuple[int, ...] = (2026, 2027, 2028),
    *,
    include_ebit: bool = True,
) -> tuple[MetricObservation, ...]:
    rows: list[MetricObservation] = []
    for index, year in enumerate(years, start=1):
        rows.append(estimate(MetricId.REVENUE, 100 + index * 10, year, analyst_count=8, suffix="revenue"))
        if include_ebit:
            rows.append(estimate(MetricId.EBIT, 20 + index * 2, year, suffix="ebit"))
        rows.append(estimate(MetricId.EBITDA, 25 + index * 2, year, suffix="ebitda"))
    return tuple(rows)


def actual_base(*, include_ebit: bool = True):
    return build_reverse_dcf_actual_base(
        identity(), actual_rows(include_ebit=include_ebit), analysis_as_of=NOW,
        valuation_currency="USD",
    )


def trajectory(rows: tuple[MetricObservation, ...] | None = None, *, base=None):
    rows = forward_rows() if rows is None else rows
    consensus = build_forward_consensus(rows, as_of_at=NOW)
    return build_forward_operating_trajectory(
        identity(), consensus, base or actual_base(), analysis_as_of=NOW,
        valuation_currency="USD",
    )


def tev(*, age_days: int = 1, provider: str = "fiscal", source: str = "calculated_tev", value=1_000):
    row = observation(
        MetricId.ENTERPRISE_VALUE, value, provider=provider, source_metric=source,
        frequency=Frequency.POINT_IN_TIME, end=NOW.date() - timedelta(days=age_days),
        year=NOW.year, suffix=f"{provider}-{source}-{age_days}",
    )
    return build_market_enterprise_value_anchor(
        identity(), (row,), analysis_as_of=NOW, valuation_currency="USD",
    )


def tax(*, status=DiscountRateEvidenceStatus.ELIGIBLE, as_of=NOW, jurisdiction="US", source_date=None):
    provenance = Provenance(
        provider="configured-tax-source", endpoint_or_dataset="marginal-tax-rate",
        provider_symbol="US", retrieved_at=NOW, as_of_at=as_of,
        configuration_or_override_id="tax-policy:test", source_metric="marginal_tax_rate",
    )
    return MarginalTaxRateEvidence(
        evidence_id="tax-evidence:synthetic", jurisdiction=jurisdiction if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        value=0.25 if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        source_name="Synthetic tax table", source_date=(source_date or NOW.date()) if status is DiscountRateEvidenceStatus.ELIGIBLE else None,
        analysis_as_of=as_of, methodology="explicit marginal rate",
        underlying_source="Synthetic public tax schedule", status=status,
        issues=(), policy_id="tax-policy:test", provenance=(provenance,) if status is DiscountRateEvidenceStatus.ELIGIBLE else (),
    )


def ready_wacc(**changes):
    values = dict(
        result_id="wacc-result:synthetic", security_id=identity().security_id,
        issuer_id=identity().issuer_id, analysis_as_of=NOW, valuation_currency="USD",
        cost_of_equity_result_id="coe:synthetic", equity_value_evidence_id="equity:synthetic",
        debt_value_evidence_id="debt:synthetic", interest_coverage_evidence_id="coverage:synthetic",
        cost_of_debt_evidence_id="cod:synthetic", marginal_tax_evidence_id="tax-evidence:synthetic",
        capital_structure_weights_id="weights:synthetic", other_claims_evidence_id="claims:synthetic",
        cost_of_equity=0.09, pretax_cost_of_debt=0.05, marginal_tax_rate=0.25,
        after_tax_cost_of_debt=0.0375, equity_weight=0.8, debt_weight=0.2,
        value=0.0795, status=DiscountRateReadinessStatus.READY, issues=(), warnings=(),
        supporting_ids=("coe:synthetic", "weights:synthetic"), policy_ids=("wacc-policy:test",),
        provenance=(), readiness_id="wacc-readiness:synthetic",
    )
    values.update(changes)
    return WaccResult(**values)


def reinvestment(*, approved=False):
    rows = (
        estimate(MetricId.CAPITAL_EXPENDITURE, 10, 2026, suffix="capex"),
        estimate(MetricId.DEPRECIATION_AMORTIZATION, 7, 2026, suffix="da"),
        estimate(MetricId.CHANGE_IN_WORKING_CAPITAL, 2, 2026, suffix="nwc"),
    )
    return build_reinvestment_readiness(
        rows, actual_rows(), analysis_as_of=NOW,
        approved_methodology_id="reinvestment-method:test" if approved else None,
    )


def terminal(*, ready=False):
    return build_terminal_readiness(
        "USD", analysis_as_of=NOW,
        terminal_growth_policy_id="growth:test" if ready else None,
        terminal_margin_policy_id="margin:test" if ready else None,
        steady_state_reinvestment_policy_id="steady-state:test" if ready else None,
        discount_rate_compatible=ready,
    )


def test_actual_base_uses_latest_canonical_fiscal_annual_revenue_and_same_period_metrics():
    rows = (
        observation(MetricId.REVENUE, 80, year=2024, suffix="old"),
        *actual_rows(),
        observation(MetricId.EBIT, 999, year=2024, suffix="wrong-period"),
    )
    result = build_reverse_dcf_actual_base(identity(), rows, analysis_as_of=NOW, valuation_currency="USD")
    assert result.status is ReverseDcfReadinessStatus.READY
    assert (result.fiscal_year, result.revenue, result.ebit, result.operating_income, result.ebitda) == (2025, 100, 20, 18, 25)


def test_actual_operating_income_is_retained_but_never_relabelled_ebit():
    result = actual_base(include_ebit=False)
    assert result.operating_income == 18 and result.ebit is None
    assert result.ebit_continuity_status is ReverseDcfReadinessStatus.NOT_READY
    assert any("not relabeled EBIT" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (observation(MetricId.REVENUE, 0, suffix="zero"), ReverseDcfReadinessStatus.UNAVAILABLE),
        (observation(MetricId.REVENUE, -1, suffix="negative"), ReverseDcfReadinessStatus.UNAVAILABLE),
        (observation(MetricId.REVENUE, 100, provider="yahoo", suffix="yahoo"), ReverseDcfReadinessStatus.UNAVAILABLE),
        (observation(MetricId.REVENUE, 100, currency="EUR", suffix="eur"), ReverseDcfReadinessStatus.UNAVAILABLE),
    ],
)
def test_actual_base_fails_closed_for_ineligible_revenue(row, expected):
    result = build_reverse_dcf_actual_base(identity(), (row,), analysis_as_of=NOW, valuation_currency="USD")
    assert result.status is expected and result.revenue is None


def test_forward_trajectory_is_average_only_annual_fiscal_and_preserves_counts():
    result = trajectory()
    assert result.status is ReverseDcfReadinessStatus.READY
    assert [item.fiscal_period for item in result.periods] == ["FY1", "FY2", "FY3"]
    assert all(item.estimate_case is EstimateCase.AVERAGE for item in result.periods)
    assert all(item.revenue_analyst_count == 8 for item in result.periods)
    assert result.periods[0].revenue_growth == pytest.approx(0.10)
    assert result.periods[1].revenue_growth == pytest.approx(120 / 110 - 1)


def test_fy1_is_first_fiscal_period_after_analysis_not_ntm():
    result = trajectory(forward_rows((2026, 2027)))
    assert result.first_period == "FY1" and result.periods[0].fiscal_year == 2026
    assert all(item.fiscal_period_end == date(item.fiscal_year, 12, 31) for item in result.periods)


def test_low_and_high_never_fallback_for_missing_average():
    rows = tuple(
        estimate(MetricId.REVENUE, value, 2026, case=case, suffix=case.value)
        for value, case in ((90, EstimateCase.LOW), (120, EstimateCase.HIGH))
    )
    result = trajectory(rows)
    assert result.periods[0].revenue is None
    assert result.revenue_status is ReverseDcfReadinessStatus.UNAVAILABLE


def test_forward_gap_is_exposed_not_interpolated_and_horizon_labels_retain_gap():
    result = trajectory(forward_rows((2026, 2028)))
    assert [item.fiscal_period for item in result.periods] == ["FY1", "FY3"]
    assert result.missing_fiscal_years == (2027,)
    assert result.periods[1].revenue_growth is None
    assert result.status is ReverseDcfReadinessStatus.PARTIAL


def test_fy1_growth_requires_exact_immediately_preceding_actual_period():
    base = replace(actual_base(), fiscal_year=2024)
    result = trajectory(forward_rows((2026, 2027)), base=base)
    assert result.periods[0].revenue_growth is None
    assert result.periods[1].revenue_growth is not None


def test_negative_ebit_is_retained_and_margin_is_not_clamped():
    rows = (
        estimate(MetricId.REVENUE, 110, 2026, suffix="revenue"),
        estimate(MetricId.EBIT, -11, 2026, suffix="ebit"),
        estimate(MetricId.REVENUE, 120, 2027, suffix="revenue"),
        estimate(MetricId.EBIT, -30, 2027, suffix="ebit"),
    )
    result = trajectory(rows)
    assert [item.ebit for item in result.periods] == [-11, -30]
    assert [item.operating_margin for item in result.periods] == pytest.approx([-0.10, -0.25])


@pytest.mark.parametrize(
    "bad_row",
    [
        estimate(MetricId.REVENUE, 110, 2026, case=EstimateCase.LOW, suffix="low"),
        estimate(MetricId.REVENUE, 110, 2026, currency="EUR", suffix="eur"),
        estimate(MetricId.REVENUE, 110, 2026, provider="fiscal", suffix="fiscal"),
        estimate(MetricId.REVENUE, 110, 2026, provider_symbol="OTHER", suffix="other-symbol"),
    ],
)
def test_forward_levels_fail_closed_on_case_currency_provider_or_identity_mismatch(bad_row):
    result = trajectory((bad_row,))
    assert not result.periods or result.periods[0].revenue is None


def test_future_consensus_snapshot_is_rejected():
    future = NOW + timedelta(minutes=1)
    rows = (estimate(MetricId.REVENUE, 110, 2026, as_of=future), estimate(MetricId.EBIT, 22, 2026, as_of=future))
    consensus = build_forward_consensus(rows, as_of_at=future)
    result = build_forward_operating_trajectory(identity(), consensus, actual_base(), analysis_as_of=NOW, valuation_currency="USD")
    assert result.periods == () and result.status is ReverseDcfReadinessStatus.UNAVAILABLE


def test_missing_ebit_does_not_use_ebitda_or_operating_income_substitute():
    result = trajectory(forward_rows(include_ebit=False))
    assert result.ebit_status is ReverseDcfReadinessStatus.UNAVAILABLE
    assert all(item.ebit is None and item.ebitda is not None for item in result.periods)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ReverseDcfReadinessStatus.READY),
        ({"age_days": 46}, ReverseDcfReadinessStatus.UNAVAILABLE),
        ({"provider": "yahoo"}, ReverseDcfReadinessStatus.UNAVAILABLE),
        ({"source": "reconstructed_enterprise_value"}, ReverseDcfReadinessStatus.UNAVAILABLE),
        ({"value": 0}, ReverseDcfReadinessStatus.UNAVAILABLE),
        ({"value": -1}, ReverseDcfReadinessStatus.UNAVAILABLE),
    ],
)
def test_market_enterprise_value_requires_positive_fresh_canonical_fiscal_calculated_tev(kwargs, expected):
    assert tev(**kwargs).status is expected


def test_market_anchor_rejects_future_observation_and_never_reconstructs_components():
    row = observation(
        MetricId.ENTERPRISE_VALUE, 1_000, frequency=Frequency.POINT_IN_TIME,
        end=NOW.date() + timedelta(days=1), source_metric="calculated_tev", suffix="future",
    )
    result = build_market_enterprise_value_anchor(identity(), (row,), analysis_as_of=NOW, valuation_currency="USD")
    assert result.status is ReverseDcfReadinessStatus.UNAVAILABLE
    assert "no component reconstruction" in result.issues[0]


def test_reinvestment_inventory_keeps_generic_fcf_separate_from_fcff_and_fcfe():
    generic = estimate(MetricId.OCF_LESS_CAPEX, 30, 2026, suffix="generic")
    result = build_reinvestment_readiness((generic,), (), analysis_as_of=NOW)
    assert result.generic_fcf_status is ReverseDcfReadinessStatus.READY
    assert result.external_fcff_status is ReverseDcfReadinessStatus.UNAVAILABLE
    assert result.external_fcfe_status is ReverseDcfReadinessStatus.UNAVAILABLE
    assert result.status is ReverseDcfReadinessStatus.PARTIAL


def test_verified_external_fcff_requires_definition_evidence_and_approved_methodology():
    fcff = estimate(MetricId.FCFF, 30, 2026, suffix="fcff")
    definition = CashFlowDefinitionEvidence(
        provider="fmp", provider_metric="verified_fcff", endpoint_or_dataset="synthetic_fcff",
        definition=CashFlowDefinition.FCFF, verification_status=DefinitionVerificationStatus.VERIFIED,
        definition_reference="synthetic-public-definition", verified_at=NOW,
        observation_ids=(fcff.observation_id,),
    )
    without_method = build_reinvestment_readiness((fcff,), (), (definition,), analysis_as_of=NOW)
    with_method = build_reinvestment_readiness(
        (fcff,), (), (definition,), analysis_as_of=NOW,
        approved_methodology_id="external-fcff-method:test",
    )
    assert without_method.external_fcff_status is ReverseDcfReadinessStatus.READY
    assert without_method.status is ReverseDcfReadinessStatus.PARTIAL
    assert with_method.status is ReverseDcfReadinessStatus.READY


def test_reinvestment_components_are_inventory_until_methodology_is_approved():
    assert reinvestment(approved=False).status is ReverseDcfReadinessStatus.PARTIAL
    ready = reinvestment(approved=True)
    assert ready.status is ReverseDcfReadinessStatus.READY
    assert ready.forward_capex_status is ReverseDcfReadinessStatus.READY
    assert ready.forward_da_status is ReverseDcfReadinessStatus.READY
    assert ready.forward_change_nwc_status is ReverseDcfReadinessStatus.READY


def test_historical_reinvestment_does_not_become_a_forecast():
    result = build_reinvestment_readiness((), actual_rows(), analysis_as_of=NOW)
    assert result.historical_reinvestment_status is ReverseDcfReadinessStatus.UNAVAILABLE
    capex = observation(MetricId.CAPITAL_EXPENDITURE, 10, suffix="historical-capex")
    result = build_reinvestment_readiness((), (capex,), analysis_as_of=NOW)
    assert result.historical_reinvestment_status is ReverseDcfReadinessStatus.READY
    assert result.methodology_status is ReverseDcfReadinessStatus.UNAVAILABLE
    assert result.status is ReverseDcfReadinessStatus.PARTIAL


def test_terminal_policy_has_no_hidden_defaults():
    result = terminal()
    assert result.status is ReverseDcfReadinessStatus.NOT_READY
    assert result.terminal_growth_policy_status is ReverseDcfReadinessStatus.NOT_READY
    assert not hasattr(result, "terminal_growth_rate")
    assert not hasattr(result, "terminal_margin")


def test_complete_explicit_terminal_policy_can_be_marked_ready_without_calculation():
    assert terminal(ready=True).status is ReverseDcfReadinessStatus.READY


def test_operating_readiness_reports_actual_ebit_continuity_separately():
    base = actual_base(include_ebit=False)
    result = build_operating_readiness(base, trajectory(base=base))
    assert result.status is ReverseDcfReadinessStatus.READY
    assert result.actual_to_forward_ebit_continuity_status is ReverseDcfReadinessStatus.NOT_READY


def test_overall_readiness_is_blocked_by_frozen_wacc_reinvestment_and_terminal_gaps():
    base = actual_base()
    forward = trajectory(base=base)
    result = assess_reverse_dcf_readiness(
        identity(), base, forward, tev(), reinvestment(), terminal(),
        analysis_as_of=NOW, valuation_currency="USD", operating_tax=tax(), wacc=None,
    )
    assert result.status is ReverseDcfReadinessStatus.NOT_READY
    assert result.reinvestment_status is ReverseDcfReadinessStatus.PARTIAL
    assert result.discount_rate_status is ReverseDcfReadinessStatus.NOT_READY
    assert result.terminal_policy_status is ReverseDcfReadinessStatus.NOT_READY
    assert result.earliest_blocking_stage is ReverseDcfReadinessStage.REINVESTMENT
    assert set(result.blocked_formulations) == set(ReverseDcfFormulation)
    assert all(item.status is ReverseDcfReadinessStatus.PARTIAL for item in result.formulation_readiness)


def test_ready_prerequisites_expose_formulation_readiness_without_running_solver():
    base = actual_base()
    forward = trajectory(base=base)
    result = assess_reverse_dcf_readiness(
        identity(), base, forward, tev(), reinvestment(approved=True), terminal(ready=True),
        analysis_as_of=NOW, valuation_currency="USD", operating_tax=tax(), wacc=ready_wacc(),
    )
    assert result.status is ReverseDcfReadinessStatus.READY
    assert result.earliest_blocking_stage is ReverseDcfReadinessStage.READY_FOR_SOLVER
    assert ReverseDcfFormulation.CONSENSUS_REVENUE_MARGIN_WITH_REINVESTMENT in result.supported_formulations
    assert ReverseDcfFormulation.CONSENSUS_FCFF_TRAJECTORY in result.blocked_formulations


@pytest.mark.parametrize(
    "wacc",
    [
        None,
        ready_wacc(status=DiscountRateReadinessStatus.NOT_READY, value=None),
        ready_wacc(valuation_currency="EUR"),
        ready_wacc(analysis_as_of=NOW - timedelta(days=1)),
        ready_wacc(security_id="security:other"),
    ],
)
def test_only_matching_ready_production_wacc_satisfies_discount_rate(wacc):
    base = actual_base()
    result = assess_reverse_dcf_readiness(
        identity(), base, trajectory(base=base), tev(), reinvestment(approved=True), terminal(ready=True),
        analysis_as_of=NOW, valuation_currency="USD", operating_tax=tax(), wacc=wacc,
    )
    assert result.discount_rate_status is ReverseDcfReadinessStatus.NOT_READY
    assert any("cost of equity was not substituted" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "tax_evidence",
    [
        None,
        tax(status=DiscountRateEvidenceStatus.UNAVAILABLE),
        tax(as_of=NOW - timedelta(days=1)),
        tax(source_date=NOW.date() + timedelta(days=1)),
    ],
)
def test_only_explicit_eligible_same_snapshot_marginal_tax_evidence_is_accepted(tax_evidence):
    base = actual_base()
    result = assess_reverse_dcf_readiness(
        identity(), base, trajectory(base=base), tev(), reinvestment(approved=True), terminal(ready=True),
        analysis_as_of=NOW, valuation_currency="USD", operating_tax=tax_evidence, wacc=ready_wacc(),
    )
    assert result.operating_tax_status is ReverseDcfReadinessStatus.NOT_READY


def test_canonical_eligible_tax_status_accepts_normalized_jurisdiction_name():
    base = actual_base()
    result = assess_reverse_dcf_readiness(
        identity(), base, trajectory(base=base), tev(), reinvestment(approved=True), terminal(ready=True),
        analysis_as_of=NOW, valuation_currency="USD",
        operating_tax=tax(jurisdiction="United States of America"), wacc=ready_wacc(),
    )
    assert result.operating_tax_status is ReverseDcfReadinessStatus.READY


def test_readiness_stages_are_controlled_and_complete():
    assert {item.value for item in ReverseDcfReadinessStage} == {
        "identity", "actual_base", "fy1_reinvestment_base", "forward_revenue", "forward_ebit", "forward_trajectory",
        "operating_tax", "reinvestment", "market_enterprise_value", "discount_rate",
        "terminal_policy", "ready_for_solver",
    }


def test_reverse_dcf_contracts_are_immutable_and_have_stable_ids():
    first = trajectory()
    second = trajectory()
    assert first.trajectory_id == second.trajectory_id
    with pytest.raises(FrozenInstanceError):
        first.status = ReverseDcfReadinessStatus.NOT_READY


def test_reverse_dcf_service_exposes_no_solver_or_valuation_entry_point():
    import stock_analyser.services.reverse_dcf_inputs as module

    public_functions = {
        name for name, value in inspect.getmembers(module, inspect.isfunction)
        if value.__module__ == module.__name__ and not name.startswith("_")
    }
    assert not any(
        forbidden in name
        for name in public_functions
        for forbidden in ("solve", "fair_value", "present_value", "terminal_value", "current_price")
    )


def test_normal_reverse_dcf_module_has_no_network_or_ui_dependency():
    import stock_analyser.services.reverse_dcf_inputs as module

    source = inspect.getsource(module)
    assert "requests" not in source and "urllib" not in source and "streamlit" not in source
    assert "GOOG" not in source and "GOOGL" not in source


def test_live_audit_is_explicit_and_missing_credentials_make_zero_requests(monkeypatch):
    import socket
    from stock_analyser.live_reverse_dcf_audit import run_live_reverse_dcf_audit

    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: calls.append(args))
    outcome = run_live_reverse_dcf_audit("META", environment={}, analysis_as_of=NOW)
    assert outcome.identity is None and outcome.readiness is None
    assert calls == []


def test_live_audit_renderer_is_secret_safe_and_has_no_current_price_output():
    from stock_analyser.live_reverse_dcf_audit import (
        render_live_reverse_dcf_audit,
        run_live_reverse_dcf_audit,
    )

    output = render_live_reverse_dcf_audit(
        run_live_reverse_dcf_audit("META", environment={}, analysis_as_of=NOW),
    )
    assert "raw provider payload" in output
    assert "current share price" in output
    assert "api_key=" not in output.lower() and "secret=" not in output.lower()
    assert "upside" not in output.lower() and "fair value:" not in output.lower()


def test_live_audit_normalizes_singular_identity_and_plural_data_capability_shapes():
    from stock_analyser.domain import CapabilityStatus
    from stock_analyser.live_reverse_dcf_audit import _capability_status

    available = SimpleNamespace(status=CapabilityStatus.AVAILABLE)
    assert _capability_status(SimpleNamespace(capability=available)) == "available"
    assert _capability_status(SimpleNamespace(capabilities=(available,))) == "available"
    assert _capability_status(SimpleNamespace()) == "unavailable"
