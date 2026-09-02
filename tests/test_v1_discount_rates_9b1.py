from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import math
import socket

import pytest

from stock_analyser.domain import (
    AdjustedPriceObservation,
    AnnualizationBasis,
    BetaDefinition,
    BetaSourceMethod,
    CurrencyApplicability,
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    EquityRiskPremiumObservation,
    MacroFrequency,
    MacroMetric,
    MacroObservation,
    MarketReturnFrequency,
    MetricUnit,
    PriceReturnSemantics,
    Provenance,
    RateEvidenceType,
    ReturnConvention,
    stable_adjusted_price_observation_id,
    stable_erp_observation_id,
)
from stock_analyser.providers import DamodaranAdapter, YahooAdapter
from stock_analyser.live_discount_rate_audit import render_live_discount_rate_audit
from stock_analyser.providers.damodaran import (
    US_IMPLIED_ERP_METHOD,
    US_IMPLIED_ERP_RISK_FREE_CONVENTION,
)
from stock_analyser.services import (
    DiscountRatePolicy,
    RegressionBetaPolicy,
    SPY_TOTAL_RETURN_BENCHMARK,
    assess_wacc_input_readiness,
    build_risk_free_rate_evidence,
    build_sourced_us_erp_evidence,
    calculate_cost_of_equity,
    calculate_regression_beta,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
SECURITY = "security:fiscal:FSCLS5RRW8UDP864"
ISSUER = "issuer:fiscal:FSCLC8JQP8UCR678"
HEADLINE = """
<html><body>
Implied ERP on May 1, 2026 = 4. 11% (Trailing 12 month, with adjusted payout);
4.07% (Trailing 12 month cash yield); 5.91% (Average CF yield last 10 years);
3.82% (Net cash yield); 3.54% (Normalized Earnings &amp; Payout)
</body></html>
"""


class FakeDamodaran:
    def __init__(self, payload=HEADLINE, fail=False):
        self.payload = payload
        self.fail = fail

    def current_us_implied_erp_page(self):
        if self.fail:
            raise ConnectionError("synthetic")
        return self.payload


class FakeYahooHistory:
    def __init__(self, rows=(), fail=False):
        self.rows = tuple(rows)
        self.fail = fail

    def identity_metadata(self, symbol):
        return {}

    def market_snapshot(self, symbol):
        return {}

    def adjusted_price_history(self, symbol, start, end):
        if self.fail:
            raise ConnectionError("synthetic")
        return self.rows


def erp_observation(day=date(2026, 8, 1), value=0.0428, *, method=US_IMPLIED_ERP_METHOD,
                    convention=US_IMPLIED_ERP_RISK_FREE_CONVENTION):
    as_of = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    provenance = Provenance(
        provider="damodaran_nyu_stern", endpoint_or_dataset="synthetic_erp_page",
        provider_symbol="US_IMPLIED_ERP_T12M_ADJUSTED_PAYOUT", retrieved_at=NOW,
        as_of_at=as_of, transformation_steps=("percent divided by 100",), source_metric=method,
    )
    return EquityRiskPremiumObservation(
        observation_id=stable_erp_observation_id(day, value, method), value=value,
        unit=MetricUnit.PERCENT_DECIMAL, applicable_currency="USD",
        market_scope="US broad equity market", source_name="Aswath Damodaran / NYU Stern",
        source_dataset="synthetic_current_erp", source_date=day, source_as_of=as_of,
        retrieved_at=NOW, methodology_label=method, risk_free_convention=convention,
        provenance=provenance,
    )


def price_observation(instrument_id, symbol, day, value, *, semantics=PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED):
    as_of = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    provenance = Provenance(
        provider="yahoo", endpoint_or_dataset="synthetic_adjusted_history",
        provider_symbol=symbol, retrieved_at=NOW, as_of_at=as_of,
        transformation_steps=("synthetic adjusted close",), source_metric="adjusted Close",
    )
    return AdjustedPriceObservation(
        observation_id=stable_adjusted_price_observation_id(instrument_id, symbol, day, value, semantics.value),
        instrument_id=instrument_id, provider_symbol=symbol, observation_date=day,
        adjusted_close=value, currency="USD", return_semantics=semantics,
        retrieved_at=NOW, as_of_at=as_of, provenance=provenance,
    )


def month_dates(count=61, *, start_year=2021, start_month=8):
    dates = []
    for offset in range(count):
        index = start_year * 12 + start_month - 1 + offset
        dates.append(date(index // 12, index % 12 + 1, 28))
    return dates


def price_series(*, beta=1.5, alpha=0.002, count=61, constant_market=False):
    days = month_dates(count)
    market_prices = [100.0]
    target_prices = [80.0]
    for index in range(1, count):
        market_return = 0.0 if constant_market else (0.004 + (index % 7) * 0.002)
        target_return = alpha + beta * market_return
        market_prices.append(market_prices[-1] * (1 + market_return))
        target_prices.append(target_prices[-1] * (1 + target_return))
    target = tuple(price_observation(SECURITY, "META", day, value) for day, value in zip(days, target_prices))
    market = tuple(price_observation(
        SPY_TOTAL_RETURN_BENCHMARK.benchmark_id, "SPY", day, value,
    ) for day, value in zip(days, market_prices))
    return target, market


def risk_free(day=date(2026, 8, 27), value=0.0467):
    as_of = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    provenance = Provenance(
        provider="fred", endpoint_or_dataset="fred_dgs10_observations",
        provider_symbol="DGS10", retrieved_at=NOW, as_of_at=as_of,
        source_metric="DGS10",
    )
    observation = MacroObservation(
        series_id="DGS10", metric=MacroMetric.TREASURY_YIELD, value=value,
        unit=MetricUnit.PERCENT_DECIMAL, currency="USD", observation_date=day,
        frequency=MacroFrequency.DAILY, as_of_at=as_of, retrieved_at=NOW,
        provider="fred", provenance=provenance,
    )
    return build_risk_free_rate_evidence("USD", (observation,), analysis_as_of=NOW)


def sourced_erp(*observations):
    return build_sourced_us_erp_evidence(observations or (erp_observation(),), analysis_as_of=NOW)


def regression_beta(**kwargs):
    target, market = price_series(**kwargs)
    return calculate_regression_beta(
        SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW,
    )


def test_official_us_implied_erp_percent_normalizes_to_decimal():
    result = DamodaranAdapter(FakeDamodaran(), clock=lambda: NOW).fetch_us_implied_erp()
    assert result.observations[0].value == pytest.approx(0.0411)
    assert result.observations[0].unit is MetricUnit.PERCENT_DECIMAL


def test_canonical_erp_methodology_is_fixed_explicitly():
    item = DamodaranAdapter(FakeDamodaran(), clock=lambda: NOW).fetch_us_implied_erp().observations[0]
    assert item.methodology_label == US_IMPLIED_ERP_METHOD


def test_alternative_erp_variants_are_not_selected_opportunistically():
    item = DamodaranAdapter(FakeDamodaran(), clock=lambda: NOW).fetch_us_implied_erp().observations[0]
    assert item.value == pytest.approx(0.0411)
    assert item.value not in {0.0407, 0.0591, 0.0382, 0.0354}


def test_latest_erp_at_or_before_snapshot_is_selected():
    old = erp_observation(date(2026, 7, 1), 0.0418)
    assert sourced_erp(old, erp_observation()).source_date == date(2026, 8, 1)


def test_future_erp_is_rejected_not_backdated():
    future = erp_observation(date(2026, 9, 1), 0.04)
    assert build_sourced_us_erp_evidence((future,), analysis_as_of=NOW) is None


def test_monthly_erp_freshness_is_centralized_at_62_days():
    assert DiscountRatePolicy().maximum_erp_age_calendar_days == 62


def test_stale_erp_is_retained_but_ineligible():
    item = sourced_erp(erp_observation(date(2026, 5, 1), 0.04))
    assert item.status is DiscountRateEvidenceStatus.STALE


def test_custom_erp_freshness_policy_is_applied_once():
    item = build_sourced_us_erp_evidence(
        (erp_observation(),), analysis_as_of=NOW,
        policy=DiscountRatePolicy(maximum_erp_age_calendar_days=10),
    )
    assert item.status is DiscountRateEvidenceStatus.STALE


def test_missing_erp_has_no_numeric_default():
    assert build_sourced_us_erp_evidence((), analysis_as_of=NOW) is None


def test_erp_source_failure_fails_closed():
    result = DamodaranAdapter(FakeDamodaran(fail=True), clock=lambda: NOW).fetch_us_implied_erp()
    assert not result.observations and result.capabilities[0].status.value == "error"


def test_erp_schema_failure_does_not_select_an_alternative():
    payload = "Implied ERP = 5.5% historical average"
    result = DamodaranAdapter(FakeDamodaran(payload), clock=lambda: NOW).fetch_us_implied_erp()
    assert not result.observations


def test_erp_provenance_and_dates_are_retained():
    item = sourced_erp()
    assert item.source_date == date(2026, 8, 1)
    assert item.provenance[0].provider == "damodaran_nyu_stern"


def test_erp_currency_and_market_scope_are_us_appropriate():
    item = sourced_erp()
    assert item.applicable_currency == "USD" and item.market_scope == "US broad equity market"


def test_sourced_erp_uses_controlled_evidence_type():
    assert sourced_erp().evidence_type is RateEvidenceType.SOURCED_US_IMPLIED_EQUITY_RISK_PREMIUM


def test_dgs10_and_erp_use_compatible_full_treasury_convention():
    item = erp_observation()
    assert "Full observed US Treasury" in item.risk_free_convention
    assert sourced_erp(item).status is DiscountRateEvidenceStatus.ELIGIBLE


def test_default_spread_adjusted_erp_convention_is_not_eligible():
    adjusted = erp_observation(convention="Treasury net of sovereign default spread")
    assert build_sourced_us_erp_evidence((adjusted,), analysis_as_of=NOW) is None


def test_adjusted_history_adapter_accepts_verified_total_return_semantics():
    row = {"date": "2026-08-28", "adjustedClose": 100, "currency": "USD",
           "returnSemantics": "split_and_distribution_adjusted", "asOf": "2026-08-28T00:00:00+00:00"}
    result = YahooAdapter(FakeYahooHistory((row,)), clock=lambda: NOW).fetch_adjusted_price_history(
        SECURITY, "META", start=date(2026, 8, 1), end=date(2026, 8, 29), expected_currency="USD",
    )
    assert len(result.observations) == 1


def test_price_only_history_is_not_promoted_to_total_return_evidence():
    row = {"date": "2026-08-28", "adjustedClose": 100, "currency": "USD",
           "returnSemantics": "price_only_unadjusted", "asOf": "2026-08-28T00:00:00+00:00"}
    result = YahooAdapter(FakeYahooHistory((row,)), clock=lambda: NOW).fetch_adjusted_price_history(
        SECURITY, "META", start=date(2026, 8, 1), end=date(2026, 8, 29), expected_currency="USD",
    )
    assert not result.observations


def test_history_currency_mismatch_fails_closed():
    row = {"date": "2026-08-28", "adjustedClose": 100, "currency": "GBP",
           "returnSemantics": "split_and_distribution_adjusted", "asOf": "2026-08-28T00:00:00+00:00"}
    result = YahooAdapter(FakeYahooHistory((row,)), clock=lambda: NOW).fetch_adjusted_price_history(
        SECURITY, "META", start=date(2026, 8, 1), end=date(2026, 8, 29), expected_currency="USD",
    )
    assert not result.observations


def test_history_source_failure_is_endpoint_isolated():
    result = YahooAdapter(FakeYahooHistory(fail=True), clock=lambda: NOW).fetch_adjusted_price_history(
        SECURITY, "META", start=date(2026, 8, 1), end=date(2026, 8, 29), expected_currency="USD",
    )
    assert not result.observations and result.capabilities[0].status.value == "error"


def test_beta_method_records_five_year_monthly_method():
    item = regression_beta()
    assert item.lookback == "60 months"
    assert item.return_frequency == MarketReturnFrequency.MONTHLY.value
    assert item.source_method is BetaSourceMethod.US_5Y_MONTHLY_MARKET_REGRESSION


def test_beta_records_explicit_benchmark_metadata():
    item = regression_beta()
    assert item.benchmark_id == SPY_TOTAL_RETURN_BENCHMARK.benchmark_id
    assert item.benchmark_symbol == "SPY"
    assert item.benchmark_currency == "USD"


def test_beta_records_simple_total_return_convention():
    item = regression_beta()
    assert item.return_convention is ReturnConvention.SIMPLE_TOTAL_RETURN
    assert item.return_semantics is PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED


def test_target_and_benchmark_monthly_observations_align_on_common_dates():
    item = regression_beta()
    assert item.sample_count == 60
    assert item.regression_start == date(2021, 9, 28)
    assert item.regression_end == date(2026, 8, 28)


def test_missing_month_is_not_interpolated_or_bridged():
    target, market = price_series()
    target = tuple(item for item in target if item.observation_date != date(2023, 1, 28))
    market = tuple(item for item in market if item.observation_date != date(2023, 1, 28))
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.sample_count == 58


def test_prices_are_not_forward_filled_to_align_different_dates():
    target, market = price_series()
    shifted_market = tuple(replace(item, observation_date=item.observation_date - timedelta(days=1)) for item in market)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, shifted_market, analysis_as_of=NOW)
    assert item.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_future_price_observations_are_excluded():
    target, market = price_series()
    future_day = date(2026, 9, 28)
    future_target = price_observation(SECURITY, "META", future_day, 500)
    future_market = price_observation(SPY_TOTAL_RETURN_BENCHMARK.benchmark_id, "SPY", future_day, 500)
    item = calculate_regression_beta(
        SECURITY, ISSUER, "META", (*target, future_target), (*market, future_market), analysis_as_of=NOW,
    )
    assert item.regression_end == date(2026, 8, 28) and item.sample_count == 60


def test_incompatible_price_return_semantics_are_not_mixed():
    target, market = price_series()
    market = tuple(replace(item, return_semantics=PriceReturnSemantics.PRICE_ONLY_UNADJUSTED) for item in market)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_monthly_simple_return_formula_is_correct():
    target, market = price_series(beta=2.0, alpha=0.0)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.value == pytest.approx(2.0, abs=1e-10)


def test_ols_beta_slope_is_correct_on_synthetic_data():
    assert regression_beta(beta=1.5, alpha=0.002).value == pytest.approx(1.5, abs=1e-10)


def test_ols_intercept_is_correct_on_synthetic_data():
    assert regression_beta(beta=1.5, alpha=0.002).alpha == pytest.approx(0.002, abs=1e-10)


def test_ols_r_squared_is_deterministic():
    assert regression_beta().r_squared == pytest.approx(1.0, abs=1e-10)


def test_normal_60_return_sample_is_eligible():
    item = regression_beta()
    assert item.sample_count == 60 and item.status is DiscountRateEvidenceStatus.ELIGIBLE


def test_less_than_36_returns_is_not_eligible():
    target, market = price_series(count=36)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.sample_count == 35 and item.status is DiscountRateEvidenceStatus.UNVERIFIED


def test_minimum_observation_policy_is_centralized():
    assert RegressionBetaPolicy().minimum_aligned_returns == 36


def test_short_history_does_not_switch_to_weekly_data():
    target, market = price_series(count=20)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.return_frequency == "monthly" and item.status is not DiscountRateEvidenceStatus.ELIGIBLE


def test_zero_benchmark_variance_rejects_beta():
    target, market = price_series(constant_market=True)
    item = calculate_regression_beta(SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW)
    assert item.value is None and item.benchmark_variance == 0
    assert item.status is DiscountRateEvidenceStatus.UNVERIFIED


def test_non_finite_adjusted_prices_are_rejected_by_contract():
    with pytest.raises(ValueError, match="finite"):
        price_observation(SECURITY, "META", date(2026, 8, 28), float("nan"))


def test_high_finite_beta_is_retained_without_cap():
    item = regression_beta(beta=4.0)
    assert item.value == pytest.approx(4.0, abs=1e-9)
    assert item.status is DiscountRateEvidenceStatus.ELIGIBLE


def test_negative_beta_is_retained_but_ineligible_by_default():
    item = regression_beta(beta=-0.5, alpha=0.02)
    assert item.value == pytest.approx(-0.5, abs=1e-9)
    assert item.status is DiscountRateEvidenceStatus.UNVERIFIED


def test_explicit_policy_can_accept_non_positive_beta_without_rewriting_it():
    target, market = price_series(beta=-0.5, alpha=0.02)
    item = calculate_regression_beta(
        SECURITY, ISSUER, "META", target, market, analysis_as_of=NOW,
        policy=RegressionBetaPolicy(allow_non_positive_beta=True),
    )
    assert item.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert item.value == pytest.approx(-0.5, abs=1e-9)


def test_verified_regression_beta_has_regression_definition():
    item = regression_beta()
    assert item.definition is BetaDefinition.REGRESSION_EQUITY_BETA


def test_regression_beta_binds_target_identity_and_snapshot():
    item = regression_beta()
    assert (item.security_id, item.issuer_id, item.analysis_as_of) == (SECURITY, ISSUER, NOW)


def test_beta_diagnostics_include_variance_errors_and_source_ids():
    item = regression_beta()
    assert item.benchmark_variance > 0
    assert item.residual_standard_error is not None
    assert item.beta_standard_error is not None
    assert len(item.source_observation_ids) == 122


def test_provider_defined_beta_is_not_averaged_with_regression_beta():
    import stock_analyser.services.beta as module
    source = inspect.getsource(module).lower()
    assert "provider_defined" not in source and "average beta" not in source


def test_beta_evidence_is_immutable():
    item = regression_beta()
    with pytest.raises(FrozenInstanceError):
        item.value = 1.0


def test_capm_uses_dgs10_regression_beta_and_sourced_implied_erp():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), sourced_erp(), regression_beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.READY
    assert result.cost_of_equity == pytest.approx(0.0467 + 1.5 * 0.0428)


def test_capm_arithmetic_remains_exact():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), sourced_erp(), regression_beta(beta=2.0), analysis_as_of=NOW,
    )
    assert result.cost_of_equity == pytest.approx(0.1323)


def test_usd_meta_can_become_cost_of_equity_ready():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), sourced_erp(), regression_beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.READY


def test_missing_erp_keeps_cost_of_equity_not_ready():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), None, regression_beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.NOT_READY


def test_missing_beta_keeps_cost_of_equity_not_ready():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), sourced_erp(), None, analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.NOT_READY


def test_stale_risk_free_keeps_cost_of_equity_not_ready():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(date(2026, 8, 1)), sourced_erp(), regression_beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.NOT_READY


def test_gbp_cannot_use_usd_risk_free_erp_beta_chain():
    usd_risk = risk_free()
    gbp_risk = replace(
        usd_risk, evidence_id="riskfree:synthetic_gbp_unavailable", valuation_currency="GBP",
        value=None, observation_date=None, source_as_of=None,
        currency_applicability=CurrencyApplicability.MISMATCHED,
        status=DiscountRateEvidenceStatus.UNAVAILABLE, provenance=(),
    )
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "GBP", gbp_risk, sourced_erp(), regression_beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.NOT_READY


def test_ready_cost_of_equity_does_not_make_wacc_ready():
    cost = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD", risk_free(), sourced_erp(), regression_beta(), analysis_as_of=NOW,
    )
    wacc = assess_wacc_input_readiness(cost)
    assert cost.status is DiscountRateReadinessStatus.READY
    assert wacc.status is DiscountRateReadinessStatus.PARTIAL
    assert not hasattr(wacc, "wacc") and not hasattr(wacc, "value")


def test_safe_audit_distinguishes_partial_prerequisites_from_not_ready_production_wacc():
    source = inspect.getsource(render_live_discount_rate_audit).lower()
    assert "wacc prerequisite readiness" in source
    assert "production wacc status: not_ready" in source


@pytest.mark.parametrize(
    "forbidden",
    (
        "0.67 * beta", "0.33 +", "min(beta", "max(beta", "resample(\"w",
        "provider_beta +", "size_premium", "country_premium", "liquidity_premium",
        "company_specific_premium", "interest_expense /", "0.21", "current_price",
        "reverse_dcf", "streamlit", "ticker ==",
    ),
)
def test_9b1_production_services_contain_no_forbidden_fallback_or_scope_logic(forbidden):
    import stock_analyser.services.beta as beta_module
    import stock_analyser.services.discount_rates as rate_module
    source = (inspect.getsource(beta_module) + inspect.getsource(rate_module)).lower()
    assert forbidden not in source


def test_normal_9b1_path_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    assert regression_beta().status is DiscountRateEvidenceStatus.ELIGIBLE
    assert sourced_erp().status is DiscountRateEvidenceStatus.ELIGIBLE


def test_9b1_contracts_do_not_import_legacy_valuation_or_ui():
    import stock_analyser.domain.discount_rates as domain_module
    source = inspect.getsource(domain_module).lower()
    assert "adaptive_valuation" not in source and "streamlit" not in source
    assert "from .peers" not in source and "from .own_history" not in source
