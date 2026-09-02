from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import socket

import pytest

from stock_analyser.domain import (
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    HistoricalMultipleType,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    HistoricalWindow,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    OwnHistoryMethodStatus,
    Provenance,
    ShareCountSemantics,
    ValuationBasis,
    ValuationMethodStatus,
    stable_historical_valuation_id,
)
from stock_analyser.live_own_history_audit import (
    LiveOwnHistoryAuditOutcome,
    render_live_own_history_audit,
)
from stock_analyser.providers import IdentityCandidate, ProviderId
from stock_analyser.services import (
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    IdentitySeed,
    OwnHistoryAuditStage,
    OwnHistoryInputPolicy,
    assemble_own_history_valuation,
)


NOW = datetime(2026, 8, 27, 16, tzinfo=timezone.utc)
SEED = IdentitySeed("SYN", "security-syn", "issuer-syn")
PERMISSIVE = HistoricalDistributionPolicy(
    policy_id="synthetic-7d1-distribution",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=tuple(HistoricalWindowMinimum(window, 1, 0.01) for window in HistoricalWindow),
)
SEMANTICS = {
    HistoricalMultipleType.P_E: (
        ValuationBasis.EQUITY, HistoricalValuationDenominator.DILUTED_EPS,
        "ratio_price_to_earnings",
    ),
    HistoricalMultipleType.EV_EBITDA: (
        ValuationBasis.ENTERPRISE, HistoricalValuationDenominator.EBITDA,
        "ratio_ev_to_ebitda",
    ),
    HistoricalMultipleType.EV_EBIT: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
        "ratio_ev_to_ebit",
    ),
}
FORWARD = {
    HistoricalMultipleType.P_E: (MetricId.EPS, MetricUnit.CURRENCY_PER_SHARE, 5.0),
    HistoricalMultipleType.EV_EBITDA: (MetricId.EBITDA, MetricUnit.CURRENCY, 100.0),
    HistoricalMultipleType.EV_EBIT: (MetricId.EBIT, MetricUnit.CURRENCY, 80.0),
}


def candidates():
    return (
        IdentityCandidate(
            provider=ProviderId.FISCAL,
            provider_symbol="F-SYN",
            retrieved_at=NOW,
            company_name="Synthetic Corp",
            issuer_domicile="US",
            listing_country="US",
            exchange="Synthetic Exchange",
            sector="Technology",
            industry="Software",
            security_type="Ordinary share",
            reporting_currency="USD",
            quote_currency="USD",
            quote_unit="USD",
            price_scale=1,
            fiscal_year_end="12-31",
        ),
        IdentityCandidate(ProviderId.FMP, "FMP-SYN", NOW),
    )


def provenance(provider: str, symbol: str, metric: str, *, as_of=NOW):
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_canonical",
        provider_symbol=symbol,
        retrieved_at=NOW,
        as_of_at=as_of,
        source_metric=metric,
    )


def historical(multiple_type, observed, value):
    basis, denominator, source_metric = SEMANTICS[multiple_type]
    return HistoricalValuationObservation(
        observation_id=stable_historical_valuation_id(
            security_id=SEED.security_id,
            issuer_id=SEED.issuer_id,
            provider="fiscal",
            provider_symbol="F-SYN",
            multiple_type=multiple_type,
            observation_date=observed,
            period_end=None,
            sampling=HistoricalValuationSampling.DAILY,
            source_metric=source_metric,
        ),
        security_id=SEED.security_id,
        issuer_id=SEED.issuer_id,
        provider="fiscal",
        provider_symbol="F-SYN",
        multiple_type=multiple_type,
        valuation_basis=basis,
        denominator=denominator,
        value=value,
        observation_date=observed,
        sampling=HistoricalValuationSampling.DAILY,
        as_of_at=NOW,
        retrieved_at=NOW,
        provenance=provenance("fiscal", "F-SYN", source_metric),
        source_metric=source_metric,
        eligibility=HistoricalValuationEligibility.ELIGIBLE,
        quote_currency_context="USD",
        reporting_currency_context="USD",
    )


def history(*, future=False):
    dates = (date(2016, 8, 27), date(2021, 8, 27), date(2024, 8, 27), NOW.date())
    output = [
        historical(multiple, observed, value)
        for multiple in HistoricalMultipleType
        for observed, value in zip(dates, (5.0, 10.0, 20.0, 30.0))
    ]
    if future:
        output.append(historical(HistoricalMultipleType.EV_EBITDA, date(2026, 8, 28), 999.0))
    return tuple(output)


def forward_observations(*, omit=(), future_snapshot=False, invalid_unit=False):
    output = []
    for multiple, (metric, unit, base) in FORWARD.items():
        if multiple in omit:
            continue
        for year in (2027, 2028):
            for case, offset in ((EstimateCase.LOW, -1), (EstimateCase.AVERAGE, 0), (EstimateCase.HIGH, 1)):
                selected_unit = MetricUnit.RATIO if invalid_unit and multiple is HistoricalMultipleType.EV_EBITDA else unit
                as_of = NOW + timedelta(days=1) if future_snapshot and multiple is HistoricalMultipleType.EV_EBITDA else NOW
                prov = provenance("fmp", "FMP-SYN", metric.value, as_of=as_of)
                output.append(MetricObservation(
                    observation_id=f"obs-fmp-{metric.value}-{year}-{case.value}",
                    metric_id=metric,
                    value=base + offset + (year - 2027) * 10,
                    unit=selected_unit,
                    frequency=Frequency.ANNUAL,
                    observation_type=ObservationType.ESTIMATE,
                    estimate_case=case,
                    retrieved_at=NOW,
                    as_of_at=as_of,
                    provenance=prov,
                    currency="USD",
                    period_start=date(year, 1, 1),
                    period_end=date(year, 12, 31),
                    fiscal_year=year,
                ))
    return tuple(output)


def actual(metric, value, observed, *, currency="USD"):
    unit = MetricUnit.SHARES if metric is MetricId.SHARES_OUTSTANDING else MetricUnit.CURRENCY
    prov = provenance("fiscal", "F-SYN", metric.value)
    return MetricObservation(
        observation_id=f"obs-fiscal-{metric.value}-{observed.isoformat()}-{value}",
        metric_id=metric,
        value=value,
        unit=unit,
        frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=NOW,
        as_of_at=NOW,
        provenance=prov,
        currency=None if unit is MetricUnit.SHARES else currency,
        period_end=observed,
    )


def bridge_observations(*, future=False, wrong_dates=False, currency="USD"):
    bridge_date = date(2026, 8, 28) if future else date(2026, 8, 26)
    market_date = bridge_date - timedelta(days=1) if wrong_dates else bridge_date
    return (
        actual(MetricId.ENTERPRISE_VALUE, 150.0, bridge_date, currency=currency),
        actual(MetricId.MARKET_CAP, 100.0, market_date, currency=currency),
        actual(MetricId.SHARES_OUTSTANDING, 10.0, date(2026, 6, 30), currency=None),
    )


def assemble(**changes):
    values = dict(
        identity_seed=SEED,
        identity_candidates=candidates(),
        historical_observations=history(),
        forward_observations=forward_observations(),
        bridge_observations=bridge_observations(),
        analysis_as_of=NOW,
        distribution_policy=PERMISSIVE,
        share_count_semantics=ShareCountSemantics.ISSUER_SHARES,
    )
    values.update(changes)
    return assemble_own_history_valuation(**values)


def test_default_orchestration_executes_only_ready_ev_ebitda():
    result = assemble()
    assert result.historical_window is HistoricalWindow.FIVE_YEAR
    assert result.forward_period is ForwardPeriodSelection.FY1
    assert result.estimate_case is EstimateCase.AVERAGE
    assert result.method(HistoricalMultipleType.P_E).readiness.status is OwnHistoryMethodStatus.NOT_READY
    assert result.method(HistoricalMultipleType.EV_EBITDA).readiness.status is OwnHistoryMethodStatus.READY
    assert result.method(HistoricalMultipleType.EV_EBIT).readiness.status is OwnHistoryMethodStatus.NOT_READY
    assert tuple(item.multiple_type for item in result.valuations) == (HistoricalMultipleType.EV_EBITDA,)


@pytest.mark.parametrize("window", list(HistoricalWindow))
def test_explicit_window_remains_one_unblended_distribution(window):
    result = assemble(historical_window=window)
    assert all(item.distribution.window is window for item in result.methods)
    assert len({item.distribution.distribution_id for item in result.methods}) == 3


@pytest.mark.parametrize("period,year", [
    (ForwardPeriodSelection.FY1, 2027),
    (ForwardPeriodSelection.FY2, 2028),
])
def test_forward_period_and_average_case_are_preserved(period, year):
    method = assemble(forward_period=period).method(HistoricalMultipleType.EV_EBITDA)
    assert method.readiness.selected_forward_period is period
    assert method.forward_denominator.fiscal_year == year
    assert method.forward_denominator.estimate_case is EstimateCase.AVERAGE


def test_one_ready_method_is_valid_without_two_method_requirement():
    result = assemble()
    assert len(result.valuations) == 1
    assert result.valuations[0].status is ValuationMethodStatus.VALID


def test_exact_distribution_forward_bridge_and_share_ids_flow_through():
    result = assemble()
    method = result.method(HistoricalMultipleType.EV_EBITDA)
    valuation = method.valuation
    assert valuation.distribution_id == method.distribution.distribution_id
    assert valuation.lower_point.forward_observation_id == method.forward_denominator.observation_id
    assert valuation.bridge_id == result.direct_bridge.bridge_id
    assert valuation.lower_point.share_observation_id == result.direct_bridge.shares_observation_id


def test_units_currency_and_scale_are_audited_in_canonical_base_units():
    scale = assemble().method(HistoricalMultipleType.EV_EBITDA).scale_audit
    assert scale.forward_unit is MetricUnit.CURRENCY
    assert scale.enterprise_value_unit is MetricUnit.CURRENCY
    assert scale.market_cap_unit is MetricUnit.CURRENCY
    assert scale.share_count_unit is MetricUnit.SHARES
    assert scale.unit_compatibility_passed
    assert scale.currency_compatibility_passed
    assert scale.scale_compatibility_passed


def test_arithmetic_audit_independently_reconstructs_every_enterprise_point():
    method = assemble().method(HistoricalMultipleType.EV_EBITDA)
    assert len(method.arithmetic_audits) == 3
    assert all(item.implied_enterprise_value_passed for item in method.arithmetic_audits)
    assert all(item.enterprise_equity_adjustment_passed for item in method.arithmetic_audits)
    assert all(item.implied_equity_value_passed for item in method.arithmetic_audits)
    assert all(item.per_share_value_passed for item in method.arithmetic_audits)
    assert [point.per_share_value for point in (
        method.valuation.lower_point, method.valuation.central_point, method.valuation.upper_point,
    )] == pytest.approx([145.0, 195.0, 245.0])


def test_future_historical_observation_is_excluded():
    method = assemble(historical_observations=history(future=True)).method(HistoricalMultipleType.EV_EBITDA)
    assert method.distribution.maximum == 30.0
    assert any("excluded 1 observations" in issue.reason for issue in method.distribution.issues)


def test_future_fmp_snapshot_is_excluded_and_denominator_stage_blocks():
    method = assemble(
        forward_observations=forward_observations(future_snapshot=True),
    ).method(HistoricalMultipleType.EV_EBITDA)
    assert method.forward_denominator is None
    assert not method.executed
    assert method.failure_stage is OwnHistoryAuditStage.FORWARD_DENOMINATOR


@pytest.mark.parametrize("bridge", [
    bridge_observations(future=True),
    bridge_observations(wrong_dates=True),
])
def test_future_or_wrong_bridge_dates_block_valuation(bridge):
    method = assemble(bridge_observations=bridge).method(HistoricalMultipleType.EV_EBITDA)
    assert method.readiness.status is not OwnHistoryMethodStatus.READY
    assert not method.executed
    assert method.failure_stage in {OwnHistoryAuditStage.BRIDGE, OwnHistoryAuditStage.READINESS}


def test_invalid_distribution_blocks_execution():
    method = assemble(historical_observations=()).method(HistoricalMultipleType.EV_EBITDA)
    assert not method.executed
    assert method.failure_stage is OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS


def test_invalid_denominator_unit_blocks_execution_without_scale_fix():
    method = assemble(
        forward_observations=forward_observations(invalid_unit=True),
    ).method(HistoricalMultipleType.EV_EBITDA)
    assert not method.executed
    assert not method.scale_audit.scale_compatibility_passed


def test_partial_bridge_readiness_is_not_executed_as_normal_method():
    method = assemble(bridge_observations=()).method(HistoricalMultipleType.EV_EBITDA)
    assert method.readiness.status is OwnHistoryMethodStatus.PARTIAL
    assert not method.executed
    assert method.failure_stage is OwnHistoryAuditStage.BRIDGE


def test_withheld_methods_retain_structured_semantic_reasons():
    result = assemble()
    pe = result.method(HistoricalMultipleType.P_E)
    ebit = result.method(HistoricalMultipleType.EV_EBIT)
    assert any("diluted EPS" in reason for reason in pe.readiness.blocking_reasons)
    assert any("Operating Profit" in reason for reason in ebit.readiness.blocking_reasons)
    assert pe.issues and ebit.issues


def test_orchestration_calls_numeric_engine_only_for_ready_method(monkeypatch):
    import stock_analyser.services.own_history_orchestration as module

    original = module.calculate_own_history_valuation
    calls = []

    def recording(*args, **kwargs):
        calls.append(args[0].multiple_type)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "calculate_own_history_valuation", recording)
    assemble()
    assert calls == [HistoricalMultipleType.EV_EBITDA]


def test_safe_audit_trace_is_reconstructible_and_contains_no_secret_or_raw_payload():
    result = assemble()
    rendered = render_live_own_history_audit(LiveOwnHistoryAuditOutcome("SYN", result, None, ()))
    lowered = rendered.lower()
    assert "own-history ev/ebitda method value" in lowered
    assert "independent arithmetic reconstruction: pass" in lowered
    assert "api_key" not in lowered and "apikey" not in lowered and "authorization" not in lowered
    assert "raw payload" not in lowered and "response body" not in lowered
    assert "upside" in lowered  # present only in the explicit prohibition statement
    assert "upside:" not in lowered and "downside:" not in lowered


def test_result_has_no_market_price_external_reference_aggregation_or_stance_fields():
    result = assemble()
    names = set(result.__dataclass_fields__)
    forbidden = {
        "current_price", "share_price", "upside", "downside", "analyst_target", "fmp_dcf",
        "fair_value", "overall_fair_value", "aggregation", "stance", "recommendation",
    }
    assert names.isdisjoint(forbidden)
    assert len(result.valuations) == 1


def test_orchestration_is_immutable():
    with pytest.raises(FrozenInstanceError):
        assemble().historical_window = HistoricalWindow.THREE_YEAR


def test_default_pytest_path_does_not_open_a_network_socket(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    assert assemble().valuations


def test_production_orchestration_has_no_ticker_branch_or_provider_scale_constant():
    import stock_analyser.services.own_history_orchestration as module

    source = inspect.getsource(module).lower()
    assert "if ticker" not in source and '== "meta"' not in source and "== 'meta'" not in source
    assert "1_000" not in source and "1_000_000" not in source and "million" not in source
    assert "current_price" not in source and "analyst_target" not in source and "standard_dcf" not in source


def test_live_entry_point_is_opt_in_and_not_imported_by_streamlit():
    script = inspect.getsource(__import__("stock_analyser.live_own_history_audit", fromlist=["*"]))
    app_source = open("app.py", encoding="utf-8").read()
    assert "run_live_own_history_audit(" in script
    assert "fetch_market_snapshot" not in script
    assert "live_own_history_audit" not in app_source
