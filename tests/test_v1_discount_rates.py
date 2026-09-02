from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import math
import socket

import pytest

from stock_analyser.domain import (
    AnnualizationBasis,
    BetaDefinition,
    BetaSourceMethod,
    CurrencyApplicability,
    DebtValueDefinition,
    DiscountRateEvidenceStatus,
    DiscountRateReadinessStatus,
    EstimateCase,
    Frequency,
    MacroFrequency,
    MacroMetric,
    MacroObservation,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    RateEvidenceType,
    WaccComponentStatus,
)
from stock_analyser.live_discount_rate_audit import (
    render_live_discount_rate_audit,
    run_live_discount_rate_audit,
)
from stock_analyser.services import (
    DiscountRatePolicy,
    assess_discount_rate_readiness,
    assess_wacc_input_readiness,
    build_risk_free_rate_evidence,
    calculate_cost_of_equity,
    configure_equity_risk_premium,
    configure_verified_beta,
    provider_defined_beta_evidence,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
SECURITY = "security:synthetic:9a"
ISSUER = "issuer:synthetic:9a"


def provenance(provider="fred", symbol="DGS10", metric="DGS10", as_of=NOW):
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_9a",
        provider_symbol=symbol,
        retrieved_at=as_of,
        as_of_at=as_of,
        source_metric=metric,
    )


def macro(day=date(2026, 8, 27), value=0.047, *, as_of=NOW, currency="USD", series="DGS10"):
    prov = provenance(as_of=as_of)
    return MacroObservation(
        series_id=series, metric=MacroMetric.TREASURY_YIELD, value=value,
        unit=MetricUnit.PERCENT_DECIMAL, currency=currency,
        observation_date=day, frequency=MacroFrequency.DAILY,
        as_of_at=as_of, retrieved_at=as_of, provider="fred", provenance=prov,
    )


def erp(value=0.05, currency="USD", *, source_date=date(2026, 8, 20), source_as_of=NOW):
    prov = provenance("configured_external", "ERP", "equity_risk_premium", source_as_of)
    return configure_equity_risk_premium(
        value, applicable_currency=currency, market_scope="developed_equity",
        source_name="Synthetic published ERP", source_date=source_date,
        source_as_of=source_as_of, analysis_as_of=NOW,
        methodology_label="forward-looking implied ERP", provenance=prov,
    )


def beta(value=1.2, *, observed=date(2026, 8, 25), source_as_of=NOW):
    prov = provenance("configured_external", "BETA", "verified_beta", source_as_of)
    return configure_verified_beta(
        SECURITY, ISSUER, value, provider="configured_external", provider_symbol="BETA",
        observation_date=observed, source_as_of=source_as_of, analysis_as_of=NOW,
        benchmark="broad market total-return index", lookback="60 months",
        return_frequency="monthly", methodology_label="OLS slope with documented inputs",
        provenance=prov,
    )


def yahoo_beta(value=1.2, *, as_of=NOW - timedelta(days=1), symbol="Y-SYN"):
    prov = provenance("yahoo", symbol, "beta", as_of)
    return MetricObservation(
        observation_id="obs:synthetic_provider_beta_9a", metric_id=MetricId.BETA,
        value=value, unit=MetricUnit.RATIO, frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL, estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=as_of, as_of_at=as_of, provenance=prov, period_end=as_of.date(),
    )


def capm(*, currency="USD", risk=None, premium=None, beta_item=None, policy=DiscountRatePolicy()):
    risk = risk if risk is not None else build_risk_free_rate_evidence(currency, (macro(),), analysis_as_of=NOW, policy=policy)
    premium = premium if premium is not None else erp(currency=currency)
    beta_item = beta_item if beta_item is not None else beta()
    return calculate_cost_of_equity(
        SECURITY, ISSUER, currency, risk, premium, beta_item,
        analysis_as_of=NOW, policy=policy,
    )


def test_dgs10_maps_to_explicit_usd_risk_free_evidence_in_decimal_units():
    result = build_risk_free_rate_evidence("USD", (macro(value=0.0425),), analysis_as_of=NOW)
    assert result.value == 0.0425
    assert result.evidence_type is RateEvidenceType.USD_TREASURY_10Y_CONSTANT_MATURITY
    assert result.unit is MetricUnit.PERCENT_DECIMAL
    assert result.annualization_basis is AnnualizationBasis.ANNUAL_PERCENT_DECIMAL
    assert result.currency_applicability is CurrencyApplicability.MATCHED
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE


@pytest.mark.parametrize("currency", ("GBP", "EUR", "JPY"))
def test_dgs10_is_never_used_for_non_usd_valuation(currency):
    result = build_risk_free_rate_evidence(currency, (macro(),), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert result.value is None
    assert result.currency_applicability is CurrencyApplicability.MISMATCHED
    assert currency in result.reason


def test_future_risk_free_observation_is_excluded_not_backdated():
    result = build_risk_free_rate_evidence("USD", (macro(day=date(2026, 8, 29)),), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert result.observation_date is None


def test_centralized_seven_calendar_day_staleness_policy_is_enforced():
    result = build_risk_free_rate_evidence("USD", (macro(day=date(2026, 8, 20)),), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.STALE
    assert result.value == 0.047
    assert "7-day" in result.reason


def test_freshness_policy_is_configurable_in_one_object():
    result = build_risk_free_rate_evidence(
        "USD", (macro(day=date(2026, 8, 20)),), analysis_as_of=NOW,
        policy=DiscountRatePolicy(maximum_risk_free_age_calendar_days=10),
    )
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE


def test_missing_weekend_market_day_selects_latest_prior_observation_without_interpolation():
    monday = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    friday = macro(day=date(2026, 8, 28), value=0.046, as_of=datetime(2026, 8, 28, 18, tzinfo=timezone.utc))
    thursday = macro(day=date(2026, 8, 27), value=0.044, as_of=datetime(2026, 8, 28, 18, tzinfo=timezone.utc))
    result = build_risk_free_rate_evidence("USD", (thursday, friday), analysis_as_of=monday)
    assert result.observation_date == date(2026, 8, 28)
    assert result.value == 0.046


def test_erp_requires_explicit_configured_evidence_and_retains_provenance():
    item = erp()
    assert item.value == 0.05
    assert item.unit is MetricUnit.PERCENT_DECIMAL
    assert item.evidence_type is RateEvidenceType.CONFIGURED_EXTERNAL_EQUITY_RISK_PREMIUM
    assert item.source_name == "Synthetic published ERP"
    assert item.provenance[0].provider == "configured_external"


@pytest.mark.parametrize("ambiguous", (5, 5.5, 100, 0, -0.01, float("inf")))
def test_erp_rejects_percent_points_and_invalid_decimal_values(ambiguous):
    with pytest.raises(ValueError, match="canonical decimal rate|finite"):
        erp(ambiguous)


def test_future_erp_evidence_is_rejected():
    with pytest.raises(ValueError, match="future"):
        erp(source_date=date(2026, 8, 29))


def test_missing_erp_never_uses_a_default():
    result = calculate_cost_of_equity(
        SECURITY, ISSUER, "USD",
        build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW),
        None, beta(), analysis_as_of=NOW,
    )
    assert result.status is DiscountRateReadinessStatus.NOT_READY
    assert result.equity_risk_premium is None and result.cost_of_equity is None
    assert any("no default" in reason for reason in result.blocking_reasons)


@pytest.mark.parametrize("invalid", (float("inf"), float("nan")))
def test_beta_requires_finite_values(invalid):
    with pytest.raises(ValueError, match="finite"):
        yahoo_beta(invalid)


def test_provider_defined_beta_exposes_unknown_methodology_and_is_ineligible_by_default():
    item = provider_defined_beta_evidence(SECURITY, ISSUER, yahoo_beta(), analysis_as_of=NOW)
    assert item.definition is BetaDefinition.PROVIDER_DEFINED
    assert item.source_method is BetaSourceMethod.PROVIDER_FIELD
    assert item.status is DiscountRateEvidenceStatus.UNVERIFIED
    assert item.benchmark is item.lookback is item.return_frequency is None
    assert "not documented" in item.methodology_label


def test_provider_defined_beta_is_not_mislabeled_when_explicitly_allowed():
    policy = DiscountRatePolicy(allow_provider_defined_beta=True)
    item = provider_defined_beta_evidence(SECURITY, ISSUER, yahoo_beta(), analysis_as_of=NOW, policy=policy)
    assert item.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert item.definition is BetaDefinition.PROVIDER_DEFINED


def test_missing_and_unverified_beta_withhold_cost_of_equity():
    risk = build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW)
    missing = calculate_cost_of_equity(SECURITY, ISSUER, "USD", risk, erp(), None, analysis_as_of=NOW)
    provider_beta = provider_defined_beta_evidence(SECURITY, ISSUER, yahoo_beta(), analysis_as_of=NOW)
    partial = calculate_cost_of_equity(SECURITY, ISSUER, "USD", risk, erp(), provider_beta, analysis_as_of=NOW)
    assert missing.status is DiscountRateReadinessStatus.NOT_READY
    assert partial.status is DiscountRateReadinessStatus.PARTIAL
    assert missing.cost_of_equity is partial.cost_of_equity is None


def test_verified_beta_permits_exact_capm_cost_of_equity():
    result = capm()
    assert result.status is DiscountRateReadinessStatus.READY
    assert result.cost_of_equity == pytest.approx(0.047 + 1.2 * 0.05)
    assert result.valuation_currency == "USD"
    assert result.supporting_ids == (
        result.risk_free_evidence_id, result.erp_evidence_id, result.beta_evidence_id,
    )


@pytest.mark.parametrize("value", (0, -0.5, 2.5, 10.0))
def test_beta_is_retained_without_clamping_or_arbitrary_upper_cap(value):
    item = beta(value)
    assert item.value == value
    if value > 0:
        assert item.status is DiscountRateEvidenceStatus.ELIGIBLE
    else:
        assert item.status is DiscountRateEvidenceStatus.UNVERIFIED


def test_extreme_eligible_beta_produces_unclamped_capm_result():
    result = capm(beta_item=beta(25))
    assert result.cost_of_equity == pytest.approx(0.047 + 25 * 0.05)
    assert result.cost_of_equity > 1


def test_risk_free_and_valuation_currency_mismatch_blocks_capm():
    usd = build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW)
    result = calculate_cost_of_equity(SECURITY, ISSUER, "GBP", usd, erp(currency="GBP"), beta(), analysis_as_of=NOW)
    assert result.status is DiscountRateReadinessStatus.NOT_READY
    assert result.cost_of_equity is None


def test_london_listing_metadata_and_quote_unit_do_not_choose_discount_currency():
    usd_shell_style = capm(currency="USD")
    gbp_rr_style = build_risk_free_rate_evidence("GBP", (macro(),), analysis_as_of=NOW)
    assert usd_shell_style.valuation_currency == "USD" and usd_shell_style.status is DiscountRateReadinessStatus.READY
    assert gbp_rr_style.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_wacc_readiness_names_every_missing_prerequisite_and_calculates_nothing():
    cost = capm()
    result = assess_wacc_input_readiness(cost)
    assert result.status is DiscountRateReadinessStatus.PARTIAL
    assert "explicit pre-tax cost of debt" in result.missing_requirements
    assert "explicit tax-rate evidence" in result.missing_requirements
    assert "verified market-value debt evidence or an explicitly approved proxy policy" in result.missing_requirements
    assert not hasattr(result, "wacc")


def test_book_debt_proxy_is_not_called_market_debt():
    result = assess_wacc_input_readiness(
        capm(), debt_value_status=WaccComponentStatus.UNVERIFIED,
        debt_value_definition=DebtValueDefinition.BOOK_VALUE_PROXY,
    )
    assert result.debt_value_definition is DebtValueDefinition.BOOK_VALUE_PROXY
    assert any("not called market debt" in warning for warning in result.warnings)
    assert result.status is not DiscountRateReadinessStatus.READY


def test_market_cap_alone_cannot_make_wacc_ready():
    result = assess_wacc_input_readiness(
        capm(), equity_market_value_status=WaccComponentStatus.VERIFIED,
    )
    assert result.status is DiscountRateReadinessStatus.PARTIAL
    assert "explicit pre-tax cost of debt" in result.missing_requirements


def test_all_explicit_wacc_prerequisites_can_be_ready_without_calculating_wacc():
    result = assess_wacc_input_readiness(
        capm(), equity_market_value_status=WaccComponentStatus.VERIFIED,
        debt_value_status=WaccComponentStatus.VERIFIED,
        debt_value_definition=DebtValueDefinition.MARKET_VALUE,
        pretax_cost_of_debt_status=WaccComponentStatus.VERIFIED,
        tax_rate_status=WaccComponentStatus.VERIFIED,
        capital_structure_weights_status=WaccComponentStatus.VERIFIED,
        preferred_equity_treatment_status=WaccComponentStatus.NOT_APPLICABLE,
        nci_treatment_status=WaccComponentStatus.NOT_APPLICABLE,
        currency_alignment_status=WaccComponentStatus.VERIFIED,
    )
    assert result.status is DiscountRateReadinessStatus.READY
    assert not hasattr(result, "value") and not hasattr(result, "wacc")


def test_discount_readiness_keeps_cost_of_equity_and_wacc_separate():
    risk = build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW)
    cost = capm(risk=risk)
    wacc = assess_wacc_input_readiness(cost)
    result = assess_discount_rate_readiness(risk, erp(), beta(), cost, wacc)
    assert result.cost_of_equity_status is DiscountRateReadinessStatus.READY
    assert result.wacc_status is DiscountRateReadinessStatus.PARTIAL
    assert result.status is DiscountRateReadinessStatus.PARTIAL


def test_contracts_are_immutable_and_stable_ids_are_deterministic():
    first = build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW)
    second = build_risk_free_rate_evidence("USD", (macro(),), analysis_as_of=NOW)
    assert first.evidence_id == second.evidence_id
    with pytest.raises(FrozenInstanceError):
        first.value = 0.09


@pytest.mark.parametrize(
    "forbidden",
    (
        "interest_expense / debt", "0.21", "size premium", "country premium",
        "company-specific premium", "cov(", "current_price", "reverse_dcf",
        "streamlit", "buy", "hold", "sell", "ticker ==",
    ),
)
def test_service_contains_no_forbidden_proxy_premium_valuation_or_ui_logic(forbidden):
    import stock_analyser.services.discount_rates as module
    source = inspect.getsource(module).lower()
    assert forbidden not in source


def test_capm_expression_contains_no_extra_premiums():
    source = inspect.getsource(calculate_cost_of_equity)
    assert "risk_free.value + beta.value * erp.value" in source
    assert "premium" not in source.split("risk_free.value + beta.value * erp.value", 1)[1].split("supporting", 1)[0]


def test_normal_discount_rate_path_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    assert capm().status is DiscountRateReadinessStatus.READY


def test_safe_gbp_audit_uses_no_fred_call_and_emits_no_secret_or_raw_payload(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    outcome = run_live_discount_rate_audit(
        "RR.L", "GBP", environment={"FRED_API_KEY": "secret-must-not-appear"},
        analysis_as_of=NOW,
    )
    rendered = render_live_discount_rate_audit(outcome)
    assert outcome.risk_free.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert "USD-only" in rendered
    assert "secret-must-not-appear" not in rendered
    assert "No WACC, DCF, reverse DCF, current price" in rendered
