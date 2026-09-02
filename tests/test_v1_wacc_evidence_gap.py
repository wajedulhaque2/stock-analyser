from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import inspect
import socket

import pytest

from stock_analyser.domain import (
    CompanyClassScope, CompanyIdentity, CoverageNumeratorDefinition,
    DiscountRateEvidenceStatus, EstimateCase, Frequency, MetricId,
    MetricObservation, MetricUnit, ObservationType, Provenance, ProviderSymbol,
)
from stock_analyser.live_smoke import LiveFiscalSource
from stock_analyser.providers import FiscalAdapter, ProviderCapability
from stock_analyser.services import (
    IdentitySeed, build_company_class_eligibility,
    build_damodaran_operating_profit_equivalence, build_equity_value_evidence,
    build_interest_coverage_evidence, resolve_company_identity,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)


def identity(**changes):
    values = dict(
        canonical_symbol="SYN", security_id="security:synthetic:wacc93",
        issuer_id="issuer:synthetic:wacc93", company_name="Synthetic Inc.",
        issuer_domicile="US", listing_country="US", exchange="NASDAQ",
        sector="Communication Services", industry="Internet Content & Information",
        security_type="Common stock", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1, fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", "SYN"),), company_type="operating_company",
    )
    values.update(changes)
    return CompanyIdentity(**values)


def observation(
    metric, value, *, end=date(2025, 12, 31), frequency=Frequency.ANNUAL,
    provider="fiscal", dataset="fiscal_standardized_financials", source=None,
    as_of=None, observation_id=None, currency="USD",
):
    as_of = as_of or datetime(2026, 2, 1, tzinfo=timezone.utc)
    source = source or metric.value
    provenance = Provenance(
        provider=provider, endpoint_or_dataset=dataset, provider_symbol="SYN",
        retrieved_at=NOW, as_of_at=as_of, source_metric=source,
    )
    unit = MetricUnit.RATIO if metric is MetricId.INTEREST_COVERAGE else MetricUnit.CURRENCY
    return MetricObservation(
        observation_id=observation_id or f"obs:wacc93:{metric.value}:{end}:{str(value).replace('.', '_')}",
        metric_id=metric, value=value, unit=unit,
        currency=None if unit is MetricUnit.RATIO else currency,
        frequency=frequency, observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE, retrieved_at=NOW, as_of_at=as_of,
        provenance=provenance, period_start=(date(end.year, 1, 1) if frequency is Frequency.ANNUAL else None),
        period_end=end, fiscal_year=end.year if frequency is Frequency.ANNUAL else None,
    )


def market_cap(value=10_000_000_000, currency="USD"):
    row = observation(
        MetricId.MARKET_CAP, value, end=date(2026, 8, 28),
        frequency=Frequency.POINT_IN_TIME, dataset="fiscal_enterprise_bridge_metrics",
        source="calculated_market_cap", currency=currency,
    )
    return build_equity_value_evidence(row, analysis_as_of=NOW)


class Source:
    def __init__(self, rows=(), fail=False):
        self.rows = rows
        self.fail = fail

    def wacc_accounting_metrics(self, symbol, *, credential=None):
        if self.fail:
            raise RuntimeError("synthetic private response")
        return self.rows


def ratio_row(metric, value, *, period="2026-06-30", period_type="quarterly", currency="USD"):
    year = int(period[:4])
    return {
        "sourceMetric": metric, "periodType": period_type,
        "periodStart": f"{year}-01-01" if period_type == "annual" else f"{year}-04-01",
        "periodEnd": period, "fiscalYear": year,
        "asOf": "2026-08-01T00:00:00+00:00", "value": value, "currency": currency,
    }


def test_direct_fiscal_total_and_net_debt_normalize_as_point_in_time_evidence():
    result = FiscalAdapter(Source((
        ratio_row("calculated_total_debt", 31_000_000_000),
        ratio_row("calculated_net_debt", 22_000_000_000),
    )), clock=lambda: NOW).fetch_wacc_accounting_evidence(identity(), analysis_as_of=NOW)
    by_metric = {row.metric_id: row for row in result.observations}
    assert set(by_metric) == {MetricId.GROSS_DEBT, MetricId.NET_DEBT}
    assert all(row.frequency is Frequency.POINT_IN_TIME for row in by_metric.values())
    assert all(row.period_start is None for row in by_metric.values())
    assert by_metric[MetricId.GROSS_DEBT].provenance.source_metric == "calculated_total_debt"
    assert "short-term debt" in by_metric[MetricId.GROSS_DEBT].provenance.transformation_steps[0]
    assert "Total Cash" in by_metric[MetricId.NET_DEBT].provenance.transformation_steps[0]


def test_provider_coverage_ratio_is_normalized_but_cannot_replace_underlying_actuals():
    result = FiscalAdapter(Source((
        ratio_row("ratio_ebit_to_interest_expense", 25, period="2025-12-31", period_type="annual", currency=None),
    )), clock=lambda: NOW).fetch_wacc_accounting_evidence(identity(), analysis_as_of=NOW)
    ratio = result.observations[0]
    assert ratio.metric_id is MetricId.INTEREST_COVERAGE
    assert ratio.unit is MetricUnit.RATIO and ratio.currency is None
    assert build_interest_coverage_evidence((ratio,), analysis_as_of=NOW).ratio is None
    assert "Operating Profit" in ratio.provenance.transformation_steps[0]


def test_unknown_and_future_fiscal_ratio_rows_do_not_create_evidence():
    result = FiscalAdapter(Source((
        ratio_row("preferred_equity_lookalike", 1),
        ratio_row("calculated_total_debt", 2, period="2027-01-01"),
    )), clock=lambda: NOW).fetch_wacc_accounting_evidence(identity(), analysis_as_of=NOW)
    assert result.observations == ()
    assert any("excluded 1" in issue.reason for issue in result.issues)


def test_wacc_accounting_failure_is_endpoint_isolated_and_secret_safe():
    adapter = FiscalAdapter(Source(fail=True), clock=lambda: NOW)
    result = adapter.fetch_wacc_accounting_evidence(identity(), analysis_as_of=NOW)
    assert result.capabilities[0].capability == ProviderCapability.WACC_ACCOUNTING_METRICS.value
    assert result.capabilities[0].status.value == "error"
    assert "private response" not in repr(result)


def test_missing_source_timestamp_uses_explicit_audit_cutoff_not_later_retrieval():
    cutoff = NOW.replace(hour=11)
    row = ratio_row("calculated_total_debt", 30)
    row["asOf"] = None
    result = FiscalAdapter(Source((row,)), clock=lambda: NOW).fetch_wacc_accounting_evidence(
        identity(), analysis_as_of=cutoff,
    )
    observation = result.observations[0]
    assert observation.as_of_at == cutoff
    assert observation.retrieved_at == NOW
    assert "source as-of unavailable; used analysis cutoff explicitly" in observation.provenance.transformation_steps


def test_live_fiscal_uses_single_documented_period_ratio_request_and_safe_shape():
    class Response:
        status_code = 200
        headers = {}
        def __init__(self, body): self.body = body
        def json(self): return self.body

    class Session:
        def __init__(self): self.calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                return Response({"data": [{
                    "companyKey": "NASDAQ_SYN", "companyFiscalIdentifier": "FISCAL-SYN",
                    "reportingCurrency": "USD", "primaryListing": {"ticker": "SYN"},
                }]})
            if url.endswith("/v1/company/ratios"):
                return Response({"data": [{
                    "periodType": "Annual", "fiscalYear": 2025, "reportDate": "2025-12-31",
                    "metricsValues": {
                        "calculated_total_debt": {"value": 30, "currency": "USD"},
                        "calculated_net_debt": {"value": 20, "currency": "USD"},
                        "ratio_ebit_to_interest_expense": {"value": 10},
                    },
                }]})
            raise AssertionError("undocumented endpoint")

    session = Session()
    rows = tuple(LiveFiscalSource(session=session).wacc_accounting_metrics("SYN", credential="secret"))
    assert {row["sourceMetric"] for row in rows} == {
        "calculated_total_debt", "calculated_net_debt", "ratio_ebit_to_interest_expense",
    }
    url, request = session.calls[-1]
    assert url.endswith("/v1/company/ratios")
    assert request["params"] == {
        "companyKey": "NASDAQ_SYN", "periodType": "annual,quarterly",
        "ratioId": "calculated_total_debt,calculated_net_debt,ratio_ebit_to_interest_expense",
    }
    assert "secret" not in repr(rows)


def test_operating_income_is_not_globally_accepted_as_ebit():
    result = build_interest_coverage_evidence((
        observation(MetricId.OPERATING_INCOME, 50), observation(MetricId.INTEREST_EXPENSE, 2),
    ), analysis_as_of=NOW)
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert result.numerator_definition is CoverageNumeratorDefinition.UNAVAILABLE


def test_damodaran_equivalence_is_method_specific_and_retains_metric_identity():
    operating = observation(MetricId.OPERATING_INCOME, 50, source="income_statement_operating_profit")
    result = build_interest_coverage_evidence((
        operating, observation(MetricId.INTEREST_EXPENSE, 2),
    ), analysis_as_of=NOW, operating_profit_equivalence=build_damodaran_operating_profit_equivalence())
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert result.ratio == 25
    assert result.ebit is None and result.ebit_observation_id is None
    assert result.numerator_metric == MetricId.OPERATING_INCOME.value
    assert result.numerator_definition is CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT
    assert operating.metric_id is MetricId.OPERATING_INCOME


def test_explicit_ebit_wins_within_the_same_period():
    result = build_interest_coverage_evidence((
        observation(MetricId.OPERATING_INCOME, 50), observation(MetricId.EBIT, 40),
        observation(MetricId.INTEREST_EXPENSE, 2),
    ), analysis_as_of=NOW, operating_profit_equivalence=build_damodaran_operating_profit_equivalence())
    assert result.ratio == 20
    assert result.ebit == 40
    assert result.numerator_definition is CoverageNumeratorDefinition.EXPLICIT_EBIT


@pytest.mark.parametrize("provider,dataset", (
    ("yahoo", "yahoo_financials"), ("fmp", "fmp_historical"),
    ("fiscal", "fiscal_other_endpoint"),
))
def test_method_equivalence_rejects_nonapproved_provider_boundaries(provider, dataset):
    result = build_interest_coverage_evidence((
        observation(MetricId.OPERATING_INCOME, 50, provider=provider, dataset=dataset),
        observation(MetricId.INTEREST_EXPENSE, 2),
    ), analysis_as_of=NOW, operating_profit_equivalence=build_damodaran_operating_profit_equivalence())
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE


def test_same_period_is_mandatory_for_method_specific_coverage():
    result = build_interest_coverage_evidence((
        observation(MetricId.OPERATING_INCOME, 50, end=date(2025, 12, 31)),
        observation(MetricId.INTEREST_EXPENSE, 2, end=date(2024, 12, 31)),
    ), analysis_as_of=NOW, operating_profit_equivalence=build_damodaran_operating_profit_equivalence())
    assert result.ratio is None


def test_conflicting_actuals_fail_closed_without_response_order_selection():
    first = observation(MetricId.OPERATING_INCOME, 50, observation_id="obs:wacc93:conflict:first")
    second = observation(MetricId.OPERATING_INCOME, 60, observation_id="obs:wacc93:conflict:second")
    interest = observation(MetricId.INTEREST_EXPENSE, 2)
    equivalence = build_damodaran_operating_profit_equivalence()
    left = build_interest_coverage_evidence((first, second, interest), analysis_as_of=NOW,
                                             operating_profit_equivalence=equivalence)
    right = build_interest_coverage_evidence((second, first, interest), analysis_as_of=NOW,
                                              operating_profit_equivalence=equivalence)
    assert left.status is right.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert left.ratio is right.ratio is None


def test_exact_duplicate_actuals_deduplicate_without_averaging():
    first = observation(MetricId.EBIT, 40, observation_id="obs:wacc93:duplicate:first")
    second = observation(MetricId.EBIT, 40, observation_id="obs:wacc93:duplicate:second")
    result = build_interest_coverage_evidence((second, observation(MetricId.INTEREST_EXPENSE, 2), first), analysis_as_of=NOW)
    assert result.ratio == 20


def test_company_class_is_established_from_canonical_evidence_not_ticker():
    result = build_company_class_eligibility(identity(canonical_symbol="ANY"), market_cap())
    assert result.status is DiscountRateEvidenceStatus.ELIGIBLE
    assert result.scope is CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING
    assert result.market_cap_value == 10_000_000_000


@pytest.mark.parametrize("changes,cap", (
    ({"issuer_domicile": "GB"}, 10_000_000_000),
    ({"sector": "Financial Services", "industry": "Banks"}, 10_000_000_000),
    ({"company_type": None}, 10_000_000_000),
    ({"company_type": "fund"}, 10_000_000_000),
    ({"industry": "Unknown"}, 10_000_000_000),
    ({}, 4_999_999_999),
))
def test_unsupported_company_classes_fail_closed(changes, cap):
    result = build_company_class_eligibility(identity(**changes), market_cap(cap))
    assert result.status is DiscountRateEvidenceStatus.UNAVAILABLE
    assert result.scope is CompanyClassScope.UNSUPPORTED


def test_company_type_is_preserved_by_generic_identity_resolution():
    from stock_analyser.providers import IdentityCandidate, ProviderId
    candidate = IdentityCandidate(
        provider=ProviderId.FISCAL, provider_symbol="SYN", retrieved_at=NOW,
        company_name="Synthetic Inc.", issuer_domicile="US", listing_country="US",
        exchange="NASDAQ", sector="Industrials", industry="Machinery",
        security_type="Common stock", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1, fiscal_year_end="12-31",
        company_type="operating_company",
    )
    result = resolve_company_identity(
        IdentitySeed("SYN", "security:synthetic:identity", "issuer:synthetic:identity"), (candidate,),
    )
    assert result.identity.company_type == "operating_company"
    assert ("company_type", ProviderId.FISCAL) in result.field_sources


def test_no_preferred_or_nci_lookalike_mapping_and_no_provider_role_expansion():
    import stock_analyser.providers.fiscal as fiscal_module
    source = inspect.getsource(fiscal_module).lower()
    mapping = fiscal_module._WACC_ACCOUNTING_FIELDS
    assert set(mapping) == {
        "calculated_total_debt", "calculated_net_debt", "ratio_ebit_to_interest_expense",
    }
    assert "preferred_equity" not in mapping and "minority_interest" not in mapping
    assert "yahoo" not in inspect.getsource(fiscal_module.FiscalAdapter.fetch_wacc_accounting_evidence).lower()
    assert "fmp" not in inspect.getsource(fiscal_module.FiscalAdapter.fetch_wacc_accounting_evidence).lower()
    assert "operating_income == ebit" not in source


def test_9b3_services_are_offline_and_do_not_add_valuation_or_ui(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    import stock_analyser.services.wacc as module
    source = inspect.getsource(module).lower()
    for forbidden in (
        "reverse dcf", "terminal value", "current price", "streamlit", "fcff forecast",
        "peer multiple", "own-history", "meta ==", "googl",
    ):
        assert forbidden not in source
    assert not __import__("re").search(r"\bstance\b", source)
    assert build_company_class_eligibility(identity(), market_cap()).status is DiscountRateEvidenceStatus.ELIGIBLE
