from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import socket

import pytest

from stock_analyser.domain import (
    CapitalComponent, CompanyClassScope, CostOfEquityResult,
    DebtValueDefinition, DiscountRateEvidenceStatus, DiscountRateReadinessStatus,
    EstimateCase, Frequency, MetricId, MetricObservation, MetricUnit,
    ObservationType, OtherClaimsStatus, Provenance,
)
from stock_analyser.providers import DamodaranAdapter, FiscalAdapter
from stock_analyser.services import (
    build_capital_structure_weights, build_debt_value_evidence,
    build_equity_value_evidence, build_interest_coverage_evidence,
    build_marginal_tax_evidence, build_other_claims_evidence,
    build_risk_free_rate_evidence, build_synthetic_cost_of_debt, calculate_wacc,
)
from stock_analyser.domain import MacroFrequency, MacroMetric, MacroObservation


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def prov(metric, *, day=date(2026, 8, 27), provider="fiscal"):
    stamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return Provenance(
        provider=provider, endpoint_or_dataset=f"synthetic_{provider}_9b2",
        provider_symbol="SYN", retrieved_at=stamp, as_of_at=stamp,
        source_metric=metric,
    )


def point(metric, value, *, day=date(2026, 8, 27), currency="USD", source=None):
    source = source or {
        MetricId.MARKET_CAP: "calculated_market_cap",
        MetricId.GROSS_DEBT: "calculated_total_debt",
        MetricId.NET_DEBT: "calculated_net_debt",
        MetricId.ENTERPRISE_VALUE: "calculated_tev",
    }.get(metric, metric.value)
    p = prov(source, day=day)
    return MetricObservation(
        observation_id=f"obs:synthetic_{metric.value}_{day}_{str(value).replace('.', '_')}",
        metric_id=metric, value=value, unit=MetricUnit.CURRENCY, currency=currency,
        frequency=Frequency.POINT_IN_TIME, observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE, retrieved_at=p.retrieved_at,
        as_of_at=p.as_of_at, provenance=p, period_end=day,
    )


def annual(metric, value, *, end=date(2025, 12, 31), currency="USD"):
    p = prov(metric.value, day=end)
    return MetricObservation(
        observation_id=f"obs:synthetic_{metric.value}_{end}_{str(value).replace('.', '_')}",
        metric_id=metric, value=value, unit=MetricUnit.CURRENCY, currency=currency,
        frequency=Frequency.ANNUAL, observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE, retrieved_at=p.retrieved_at,
        as_of_at=p.as_of_at, provenance=p, period_start=date(end.year, 1, 1),
        period_end=end, fiscal_year=end.year,
    )


def risk(value=0.0467):
    p = prov("DGS10", provider="fred")
    obs = MacroObservation(
        series_id="DGS10", metric=MacroMetric.TREASURY_YIELD, value=value,
        unit=MetricUnit.PERCENT_DECIMAL, currency="USD", observation_date=date(2026, 8, 27),
        frequency=MacroFrequency.DAILY, as_of_at=p.as_of_at,
        retrieved_at=p.retrieved_at, provider="fred", provenance=p,
    )
    return build_risk_free_rate_evidence("USD", (obs,), analysis_as_of=NOW)


def capm(ready=True):
    return CostOfEquityResult(
        result_id="costequity:synthetic_9b2", security_id="security:synthetic:9b2",
        issuer_id="issuer:synthetic:9b2", analysis_as_of=NOW, valuation_currency="USD",
        risk_free_evidence_id="riskfree:synthetic", risk_free_rate=0.0467,
        erp_evidence_id="erp:synthetic", equity_risk_premium=0.0428,
        beta_evidence_id="beta:synthetic", beta=1.2383084543593788,
        cost_of_equity=0.09969960184658141 if ready else None,
        status=DiscountRateReadinessStatus.READY if ready else DiscountRateReadinessStatus.NOT_READY,
        blocking_reasons=() if ready else ("eligible beta unavailable",), warnings=(),
        supporting_ids=("riskfree:synthetic", "erp:synthetic", "beta:synthetic"),
        policy_id="discount-rate-evidence-v1", provenance=(prov("CAPM"),),
    )


RATINGS_HTML = """
<html><table>
<tr><td>For large non-financial service firms</td><td></td><td></td><td></td><td>For financial service firms</td></tr>
<tr><td>-100</td><td>1.0</td><td>Spec/Low</td><td>4.00%</td><td>-100</td><td>0.5</td><td>Financial</td><td>8.00%</td></tr>
<tr><td>1.0</td><td>3.0</td><td>Mid/Grade</td><td>1.50%</td><td>0.5</td><td>2.0</td><td>Financial2</td><td>3.00%</td></tr>
<tr><td>3.0</td><td>100</td><td>Prime/High</td><td>0.40%</td><td>2</td><td>100</td><td>Financial3</td><td>1.00%</td></tr>
</table></html>
"""
TAX_HTML = """
<html><body>Corporate Marginal Tax Rates - By country Source : PWC From : January 2026 Update
<table><tr><th>Country</th><th>Corporate Tax Rate</th><th>Tax Rate Accounting for Global Minimum Tax</th></tr>
<tr><td>United States of America</td><td>24.75%</td><td>24.75%</td></tr>
<tr><td>Fictivia</td><td>18.00%</td><td>18.00%</td></tr></table></body></html>
"""


class Source:
    def current_synthetic_rating_page(self): return RATINGS_HTML
    def current_country_tax_page(self): return TAX_HTML
    def current_us_implied_erp_page(self): return "unused"


def rating_bands():
    return DamodaranAdapter(Source(), clock=lambda: NOW).fetch_large_nonfinancial_rating_bands().observations


def tax_observations():
    return DamodaranAdapter(Source(), clock=lambda: NOW).fetch_country_marginal_tax_rates().observations


def ready_inputs(*, debt_value=20, residual=0):
    equity_obs = point(MetricId.MARKET_CAP, 100)
    debt_obs = point(MetricId.GROSS_DEBT, debt_value)
    equity = build_equity_value_evidence(equity_obs, analysis_as_of=NOW)
    debt = build_debt_value_evidence(debt_obs, analysis_as_of=NOW,
                                     company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, 12), annual(MetricId.INTEREST_EXPENSE, 4)), analysis_as_of=NOW,
    )
    cod = build_synthetic_cost_of_debt(
        coverage, rating_bands(), risk(),
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING,
    )
    tax = build_marginal_tax_evidence("US", tax_observations(), analysis_as_of=NOW)
    claims = build_other_claims_evidence(
        point(MetricId.ENTERPRISE_VALUE, 110 + residual), equity_obs,
        point(MetricId.NET_DEBT, 10), analysis_as_of=NOW,
    )
    return equity, debt, coverage, cod, tax, claims


def test_market_cap_creates_market_value_equity_evidence():
    result = build_equity_value_evidence(point(MetricId.MARKET_CAP, 100), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert result.component is CapitalComponent.EQUITY
    assert result.definition is DebtValueDefinition.MARKET_VALUE


@pytest.mark.parametrize("mutation", ("future", "wrong_currency", "zero", "negative", "wrong_metric", "stale"))
def test_invalid_market_cap_fails_closed(mutation):
    item = point(MetricId.MARKET_CAP, 100)
    if mutation == "future": item = point(MetricId.MARKET_CAP, 100, day=date(2026, 8, 29))
    elif mutation == "wrong_currency": item = point(MetricId.MARKET_CAP, 100, currency="GBP")
    elif mutation == "zero": item = point(MetricId.MARKET_CAP, 0)
    elif mutation == "negative": item = point(MetricId.MARKET_CAP, -1)
    elif mutation == "wrong_metric": item = point(MetricId.NET_DEBT, 100)
    else: item = point(MetricId.MARKET_CAP, 100, day=date(2026, 1, 1))
    assert build_equity_value_evidence(item, analysis_as_of=NOW).status is not DiscountRateEvidenceStatus.ELIGIBLE


def test_book_gross_debt_proxy_remains_explicit_and_zero_is_valid():
    result = build_debt_value_evidence(point(MetricId.GROSS_DEBT, 0), analysis_as_of=NOW,
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert result.value == 0
    assert result.definition is DebtValueDefinition.BOOK_VALUE_PROXY
    assert "BOOK_VALUE_PROXY" in result.provenance[0].transformation_steps[-1]


@pytest.mark.parametrize("metric,value,source,scope", (
    (MetricId.NET_DEBT, 20, "calculated_net_debt", CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING),
    (MetricId.GROSS_DEBT, -1, "calculated_total_debt", CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING),
    (MetricId.GROSS_DEBT, 20, "total_liabilities", CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING),
    (MetricId.GROSS_DEBT, 20, "calculated_total_debt", CompanyClassScope.UNSUPPORTED),
))
def test_net_debt_negative_debt_wrong_semantics_and_unsupported_scope_are_rejected(metric, value, source, scope):
    evidence = build_debt_value_evidence(point(metric, value, source=source), analysis_as_of=NOW,
                                         company_class_scope=scope)
    assert evidence.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert evidence.value is None


def test_missing_debt_is_not_zero():
    result = build_debt_value_evidence(None, analysis_as_of=NOW,
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert result.value is None


def test_rating_and_tax_provider_normalization_retains_source_semantics():
    rating_result = DamodaranAdapter(Source(), clock=lambda: NOW).fetch_large_nonfinancial_rating_bands()
    tax_result = DamodaranAdapter(Source(), clock=lambda: NOW).fetch_country_marginal_tax_rates()
    assert [x.default_spread for x in rating_result.observations] == [0.04, 0.015, 0.004]
    assert all("Financial" not in x.synthetic_rating for x in rating_result.observations)
    assert rating_result.observations[0].source_date == date(2026, 1, 9)
    assert tax_result.observations[0].value == 0.2475
    assert tax_result.observations[0].source_date == date(2026, 1, 1)
    assert tax_result.observations[0].underlying_source == "PwC"


@pytest.mark.parametrize("ratio,expected", ((-10, "Spec/Low"), (1.0, "Spec/Low"), (1.000001, "Mid/Grade"), (3.0, "Mid/Grade"), (3.000001, "Prime/High"), (50, "Prime/High")))
def test_rating_band_boundaries_are_deterministic_without_interpolation(ratio, expected):
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, ratio * 2), annual(MetricId.INTEREST_EXPENSE, 2)), analysis_as_of=NOW)
    result = build_synthetic_cost_of_debt(coverage, rating_bands(), risk(),
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert result.synthetic_rating == expected


@pytest.mark.parametrize("ratio", (-101, 101))
def test_rating_does_not_cap_outside_sourced_bounds(ratio):
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, ratio), annual(MetricId.INTEREST_EXPENSE, 1)), analysis_as_of=NOW)
    result = build_synthetic_cost_of_debt(coverage, rating_bands(), risk(),
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_overlapping_rating_bands_fail_exactly_one_rule():
    bands = rating_bands()
    overlap = replace(bands[0], evidence_id="ratingband:synthetic_overlap", coverage_lower_bound=0.5, coverage_upper_bound=2)
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, 3), annual(MetricId.INTEREST_EXPENSE, 2)), analysis_as_of=NOW)
    result = build_synthetic_cost_of_debt(coverage, (*bands, overlap), risk(),
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_coverage_requires_explicit_ebit_and_interest_expense_same_annual_period():
    wrong_metric = annual(MetricId.OPERATING_INCOME, 12)
    interest = annual(MetricId.INTEREST_EXPENSE, 4)
    assert build_interest_coverage_evidence((wrong_metric, interest), analysis_as_of=NOW).ratio is None
    prior = annual(MetricId.INTEREST_EXPENSE, 4, end=date(2024, 12, 31))
    assert build_interest_coverage_evidence((annual(MetricId.EBIT, 12), prior), analysis_as_of=NOW).ratio is None


def test_quarterly_values_cannot_manufacture_coverage():
    ebit = annual(MetricId.EBIT, 12)
    interest = replace(annual(MetricId.INTEREST_EXPENSE, 4), frequency=Frequency.QUARTERLY,
                       fiscal_quarter=4, period_start=date(2025, 10, 1))
    assert build_interest_coverage_evidence((ebit, interest), analysis_as_of=NOW).ratio is None


@pytest.mark.parametrize("interest", (0, -1))
def test_zero_or_negative_interest_expense_never_divides(interest):
    result = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, 12), annual(MetricId.INTEREST_EXPENSE, interest)), analysis_as_of=NOW)
    assert result.ratio is None
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_coverage_and_pretax_cost_of_debt_arithmetic_are_exact():
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, 12), annual(MetricId.INTEREST_EXPENSE, 4)), analysis_as_of=NOW)
    result = build_synthetic_cost_of_debt(coverage, rating_bands(), risk(),
        company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING)
    assert coverage.ratio == 3
    assert result.default_spread == 0.015
    assert result.pretax_cost_of_debt == 0.0467 + 0.015


def test_unsupported_company_class_has_no_synthetic_cost_of_debt():
    coverage = build_interest_coverage_evidence(
        (annual(MetricId.EBIT, 12), annual(MetricId.INTEREST_EXPENSE, 4)), analysis_as_of=NOW)
    result = build_synthetic_cost_of_debt(coverage, rating_bands(), risk(),
        company_class_scope=CompanyClassScope.UNSUPPORTED)
    assert result.pretax_cost_of_debt is None


@pytest.mark.parametrize("domicile", ("US", "USA", "United States", "United States of America"))
def test_us_domicile_selects_sourced_us_marginal_tax(domicile):
    result = build_marginal_tax_evidence(domicile, tax_observations(), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert result.value == 0.2475


@pytest.mark.parametrize("domicile", (None, "", "GB", "USD", "NYSE"))
def test_missing_or_nonmatching_domicile_does_not_use_listing_currency_or_default(domicile):
    result = build_marginal_tax_evidence(domicile, tax_observations(), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert result.value is None


def test_tax_future_and_stale_policy_fail_closed():
    original = tax_observations()[0]
    future_p = replace(original.provenance, as_of_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    future = replace(original, observation_id="taxobs:synthetic_future", source_date=date(2027, 1, 1), provenance=future_p)
    assert build_marginal_tax_evidence("US", (future,), analysis_as_of=NOW).value is None
    old_p = replace(original.provenance, as_of_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    old = replace(original, observation_id="taxobs:synthetic_old", source_date=date(2024, 1, 1), provenance=old_p)
    assert build_marginal_tax_evidence("US", (old,), analysis_as_of=NOW).status is DiscountRateEvidenceStatus.STALE


def test_other_claims_residual_requires_aligned_tev_market_cap_and_net_debt():
    result = build_other_claims_evidence(point(MetricId.ENTERPRISE_VALUE, 115),
        point(MetricId.MARKET_CAP, 100), point(MetricId.NET_DEBT, 10), analysis_as_of=NOW)
    assert result.value == 5
    assert result.status is OtherClaimsStatus.UNRESOLVED_NONZERO
    assert "preferred" in result.issues[0]


@pytest.mark.parametrize("mutation", ("missing", "date", "currency", "metric"))
def test_other_claims_missing_or_misaligned_is_not_assumed_zero(mutation):
    tev, cap, net = point(MetricId.ENTERPRISE_VALUE, 110), point(MetricId.MARKET_CAP, 100), point(MetricId.NET_DEBT, 10)
    if mutation == "missing": net = None
    elif mutation == "date": net = point(MetricId.NET_DEBT, 10, day=date(2026, 8, 26))
    elif mutation == "currency": net = point(MetricId.NET_DEBT, 10, currency="GBP")
    else: net = point(MetricId.GROSS_DEBT, 10)
    result = build_other_claims_evidence(tev, cap, net, analysis_as_of=NOW)
    assert result.status is OtherClaimsStatus.UNAVAILABLE
    assert result.value is None


def test_explicit_zero_residual_permits_standard_formula_and_weights_are_exact():
    equity, debt, coverage, cod, tax, claims = ready_inputs()
    weights = build_capital_structure_weights(equity, debt)
    assert claims.status is OtherClaimsStatus.VERIFIED_ZERO
    assert weights.equity_weight == 100 / 120
    assert weights.debt_weight == 20 / 120
    assert weights.equity_weight + weights.debt_weight == 1


def test_zero_debt_reduces_ready_wacc_to_cost_of_equity():
    equity, debt, coverage, cod, tax, claims = ready_inputs(debt_value=0)
    result, readiness = calculate_wacc(capm(), equity, debt, coverage, cod, tax, claims)
    assert readiness.status is DiscountRateReadinessStatus.READY
    assert result.value == capm().cost_of_equity
    assert result.debt_weight == 0


def test_wacc_formula_after_tax_cost_and_book_proxy_provenance_are_exact():
    equity, debt, coverage, cod, tax, claims = ready_inputs()
    result, readiness = calculate_wacc(capm(), equity, debt, coverage, cod, tax, claims)
    after_tax = (0.0467 + 0.015) * (1 - 0.2475)
    expected = (100 / 120) * 0.09969960184658141 + (20 / 120) * after_tax
    assert result.after_tax_cost_of_debt == after_tax
    assert result.value == expected
    assert readiness.status is DiscountRateReadinessStatus.READY
    assert any("Book gross debt" in x for x in result.warnings)


@pytest.mark.parametrize("blocker", ("cost_equity", "equity", "debt", "coverage", "cost_debt", "tax", "claims", "currency"))
def test_each_required_component_blocks_numeric_wacc(blocker):
    equity, debt, coverage, cod, tax, claims = ready_inputs()
    cost = capm()
    if blocker == "cost_equity": cost = capm(False)
    elif blocker == "equity": equity = replace(equity, status=DiscountRateEvidenceStatus.UNAVAILABLE, value=None,
                                                source_observation_id=None, observation_date=None, provenance=())
    elif blocker == "debt": debt = replace(debt, status=DiscountRateEvidenceStatus.UNAVAILABLE, value=None,
                                            source_observation_id=None, observation_date=None,
                                            definition=DebtValueDefinition.UNAVAILABLE, provenance=())
    elif blocker == "coverage": coverage = replace(coverage, status=DiscountRateEvidenceStatus.UNAVAILABLE, ratio=None)
    elif blocker == "cost_debt": cod = replace(cod, status=DiscountRateEvidenceStatus.UNAVAILABLE,
                                               pretax_cost_of_debt=None, default_spread=None)
    elif blocker == "tax": tax = replace(tax, status=DiscountRateEvidenceStatus.UNAVAILABLE, value=None)
    elif blocker == "claims": claims = replace(claims, status=OtherClaimsStatus.UNRESOLVED_NONZERO, value=1)
    else: debt = replace(debt, currency="GBP")
    result, readiness = calculate_wacc(cost, equity, debt, coverage, cod, tax, claims)
    assert result.status is DiscountRateReadinessStatus.NOT_READY
    assert result.value is None
    assert readiness.status is not DiscountRateReadinessStatus.READY


@pytest.mark.parametrize("field,value", (("equity_value", 0), ("debt_value", -1), ("equity_weight", 0.7), ("debt_weight", 0.4)))
def test_weight_contract_rejects_malformed_values(field, value):
    equity, debt, *_ = ready_inputs()
    weights = build_capital_structure_weights(equity, debt)
    with pytest.raises(ValueError):
        replace(weights, **{field: value})


def test_contracts_are_immutable_and_no_display_rounding_occurs():
    equity, debt, coverage, cod, tax, claims = ready_inputs()
    result, _ = calculate_wacc(capm(), equity, debt, coverage, cod, tax, claims)
    with pytest.raises(FrozenInstanceError):
        result.value = 0.1
    assert len(repr(result.value)) > 6


def test_wacc_service_has_no_network_and_no_forbidden_valuation_or_ui_concepts(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    import stock_analyser.services.wacc as module
    source = inspect.getsource(module).lower()
    for forbidden in ("reverse dcf", "terminal value", "current price", "streamlit", "upside", "downside", "googl", "meta =="):
        assert forbidden not in source
    equity, debt, coverage, cod, tax, claims = ready_inputs()
    assert calculate_wacc(capm(), equity, debt, coverage, cod, tax, claims)[0].value is not None


@pytest.mark.parametrize("value,sign,expected", (
    (-4, "negative_expense", 4),
    (4, "positive_expense_magnitude", 4),
    (4, "negative_expense", None),
    (-4, None, None),
))
def test_fiscal_interest_expense_requires_explicit_sign_semantics(value, sign, expected):
    row = {
        "metric": "interest_expense", "sourceMetric": "interest expense",
        "periodType": "annual", "periodStart": "2025-01-01", "periodEnd": "2025-12-31",
        "fiscalYear": 2025, "asOf": "2025-12-31T00:00:00+00:00",
        "value": value, "currency": "USD", "signConvention": sign,
    }
    result = FiscalAdapter(Source(), clock=lambda: NOW)._normalize_standardized_rows(
        (row,), symbol="SYN", retrieved=NOW,
    )
    if expected is None:
        assert not result.observations
        assert result.issues
    else:
        assert result.observations[0].metric_id is MetricId.INTEREST_EXPENSE
        assert result.observations[0].value == expected


def test_malformed_rating_source_fails_provider_boundary_without_business_fallback():
    class Bad(Source):
        def current_synthetic_rating_page(self): return "<html>no rating rows</html>"
    result = DamodaranAdapter(Bad(), clock=lambda: NOW).fetch_large_nonfinancial_rating_bands()
    assert not result.observations


def test_malformed_tax_source_fails_provider_boundary_without_hardcoded_default():
    class Bad(Source):
        def current_country_tax_page(self): return "<html>United States 21%</html>"
    result = DamodaranAdapter(Bad(), clock=lambda: NOW).fetch_country_marginal_tax_rates()
    assert not result.observations
