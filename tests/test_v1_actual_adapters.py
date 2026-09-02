from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from stock_analyser.domain import (
    CapabilityStatus,
    CompanyIdentity,
    Frequency,
    MetricId,
    ObservationType,
    ProviderSymbol,
)
from stock_analyser.providers import (
    FiscalAdapter,
    ProviderError,
    ProviderErrorCategory,
    ProviderId,
    SecAdapter,
    SecretReference,
    YahooAdapter,
)


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
MARKET_AS_OF = datetime(2026, 8, 25, 16, tzinfo=timezone.utc)


def identity(*, quote_unit="GBP", price_scale=1, symbols=None):
    return CompanyIdentity(
        canonical_symbol="CANON", security_id="security", issuer_id="issuer",
        company_name="Synthetic plc", issuer_domicile="GB", listing_country="GB",
        exchange="Synthetic Exchange", sector="Industrials", industry="Engineering",
        security_type="Ordinary share", reporting_currency="GBP", quote_currency="GBP",
        quote_unit=quote_unit, price_scale=price_scale, fiscal_year_end="12-31",
        provider_symbols=tuple(symbols or (
            ProviderSymbol("yahoo", "Y-SYN"), ProviderSymbol("fiscal", "F-SYN"), ProviderSymbol("sec", "0000123456"),
        )),
    )


class FakeYahoo:
    def __init__(self, market=None, fail_market=False):
        self.market = market or {}
        self.fail_market = fail_market

    def identity_metadata(self, symbol):
        return {
            "longName": "Synthetic plc", "country": "GB", "listingCountry": "GB",
            "exchange": "Synthetic Exchange", "sector": "Industrials", "industry": "Engineering",
            "quoteType": "Ordinary share", "financialCurrency": "GBP", "currency": "GBP",
            "quoteUnit": "GBp", "fiscalYearEnd": "12-31",
        }

    def market_snapshot(self, symbol):
        if self.fail_market:
            raise RuntimeError("synthetic private payload must not escape")
        return self.market


def test_yahoo_identity_and_complete_market_snapshot_normalize_canonically():
    source = FakeYahoo({
        "regularMarketPrice": 585, "marketCap": 2_000_000, "sharesOutstanding": 300_000,
        "beta": 1.2, "regularMarketVolume": 40_000, "asOf": MARKET_AS_OF,
    })
    adapter = YahooAdapter(source, clock=lambda: NOW)
    identity_result = adapter.fetch_identity("Y-SYN")
    assert identity_result.candidate.quote_unit == "GBp"
    assert identity_result.candidate.price_scale == 0.01
    result = adapter.fetch_market_snapshot(identity(quote_unit="GBp", price_scale=0.01))
    observations = {item.metric_id: item for item in result.observations}
    assert observations[MetricId.SHARE_PRICE].value == pytest.approx(5.85)
    assert observations[MetricId.MARKET_CAP].value == 2_000_000
    assert observations[MetricId.SHARES_OUTSTANDING].value == 300_000
    assert observations[MetricId.BETA].value == 1.2
    assert observations[MetricId.VOLUME].value == 40_000
    assert all(item.observation_type is ObservationType.ACTUAL for item in observations.values())
    assert all(item.frequency is Frequency.POINT_IN_TIME for item in observations.values())
    assert observations[MetricId.SHARE_PRICE].provenance.source_metric == "regularMarketPrice"
    assert "quote price scale" in observations[MetricId.SHARE_PRICE].provenance.transformation_steps[0]
    assert observations[MetricId.MARKET_CAP].provenance.transformation_steps == ()


def test_yahoo_missing_fields_are_not_fabricated_and_price_capability_is_unavailable():
    result = YahooAdapter(FakeYahoo({"marketCap": 10, "asOf": MARKET_AS_OF}), clock=lambda: NOW).fetch_market_snapshot(identity())
    assert [item.metric_id for item in result.observations] == [MetricId.MARKET_CAP]
    statuses = {item.capability: item.status for item in result.capabilities}
    assert statuses["market_snapshot"] is CapabilityStatus.AVAILABLE
    assert statuses["current_price"] is CapabilityStatus.UNAVAILABLE


def test_yahoo_market_failure_does_not_invalidate_identity_capability_or_leak_details():
    adapter = YahooAdapter(FakeYahoo(fail_market=True), clock=lambda: NOW)
    assert adapter.fetch_identity("Y-SYN").capability.status is CapabilityStatus.AVAILABLE
    failed = adapter.fetch_market_snapshot(identity())
    assert failed.capabilities[0].status is CapabilityStatus.ERROR
    assert adapter.capability("identity").status is CapabilityStatus.AVAILABLE
    assert "private payload" not in repr(failed)


class FakeFiscal:
    def __init__(self, rows=(), fail=False, bridge_rows=()):
        self.rows = rows
        self.bridge_rows = bridge_rows
        self.fail = fail
        self.seen_credential = None

    def identity_metadata(self, symbol, *, credential=None):
        self.seen_credential = credential
        return {
            "company_name": "Synthetic plc", "issuer_domicile": "GB", "listing_country": "GB",
            "exchange": "Synthetic Exchange", "sector": "Industrials", "industry": "Engineering",
            "security_type": "Ordinary share", "reporting_currency": "GBP", "quote_currency": "GBP",
            "quote_unit": "GBP", "price_scale": 1, "fiscal_year_end": "12-31",
        }

    def standardized_financials(self, symbol, *, credential=None):
        self.seen_credential = credential
        if self.fail:
            raise RuntimeError(f"credential={credential}")
        return self.rows

    def enterprise_bridge_metrics(self, symbol, *, credential=None):
        self.seen_credential = credential
        if self.fail:
            raise RuntimeError(f"credential={credential}")
        return self.bridge_rows


def fiscal_row(metric="revenue", value=1_000_000, period_type="annual", **changes):
    row = {
        "metric": metric, "value": value, "currency": "GBP", "periodType": period_type,
        "periodStart": "2025-01-01", "periodEnd": "2025-12-31", "fiscalYear": 2025,
        "asOf": "2026-02-20T09:00:00+00:00",
    }
    if period_type == "quarterly":
        row.update(periodStart="2025-04-01", periodEnd="2025-06-30", fiscalQuarter=2)
    row.update(changes)
    return row


def fiscal_bridge_row(source_metric, value, observation_date="2026-08-25", currency="GBP", **changes):
    row = {
        "sourceMetric": source_metric,
        "value": value,
        "observationDate": observation_date,
        "asOf": f"{observation_date}T00:00:00+00:00",
        "currency": currency,
    }
    row.update(changes)
    return row


def test_fiscal_bridge_maps_only_explicit_documented_metrics_with_safe_provenance():
    rows = (
        fiscal_bridge_row("calculated_tev", 150),
        fiscal_bridge_row("calculated_market_cap", 100),
        fiscal_bridge_row("calculated_total_debt", 60),
        fiscal_bridge_row("calculated_net_debt", 40),
        fiscal_bridge_row("market_data_total_shares_outstanding", 10, currency=None),
        fiscal_bridge_row("calculated_tev_lookalike", 999),
    )
    result = FiscalAdapter(
        FakeFiscal(bridge_rows=rows), clock=lambda: NOW,
    ).fetch_enterprise_bridge(identity(), analysis_as_of=NOW)
    by_metric = {item.metric_id: item for item in result.observations}
    assert set(by_metric) == {
        MetricId.ENTERPRISE_VALUE, MetricId.MARKET_CAP, MetricId.GROSS_DEBT,
        MetricId.NET_DEBT, MetricId.SHARES_OUTSTANDING,
    }
    assert all(item.frequency is Frequency.POINT_IN_TIME for item in by_metric.values())
    assert by_metric[MetricId.ENTERPRISE_VALUE].provenance.source_metric == "calculated_tev"
    assert by_metric[MetricId.MARKET_CAP].provenance.source_metric == "calculated_market_cap"
    assert by_metric[MetricId.GROSS_DEBT].provenance.source_metric == "calculated_total_debt"
    assert by_metric[MetricId.NET_DEBT].provenance.source_metric == "calculated_net_debt"
    assert by_metric[MetricId.SHARES_OUTSTANDING].currency is None
    assert "ADS-converted" in by_metric[MetricId.SHARES_OUTSTANDING].provenance.transformation_steps[0]
    assert all(not hasattr(item, "sourceMetric") for item in by_metric.values())
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE


def test_fiscal_bridge_excludes_future_rows_and_keeps_capability_isolated():
    source = FakeFiscal(bridge_rows=(
        fiscal_bridge_row("calculated_tev", 150, "2026-08-25"),
        fiscal_bridge_row("calculated_market_cap", 999, "2026-08-27"),
    ))
    adapter = FiscalAdapter(source, clock=lambda: NOW)
    result = adapter.fetch_enterprise_bridge(identity(), analysis_as_of=NOW)
    assert [item.metric_id for item in result.observations] == [MetricId.ENTERPRISE_VALUE]
    assert any("excluded 1" in issue.reason for issue in result.issues)
    assert adapter.capability("enterprise_equity_bridge").status is CapabilityStatus.AVAILABLE


def test_fiscal_bridge_failure_does_not_invalidate_existing_actual_capability():
    adapter = FiscalAdapter(FakeFiscal([fiscal_row()]), clock=lambda: NOW)
    assert adapter.fetch_standardized_actuals(identity()).capabilities[0].status is CapabilityStatus.AVAILABLE
    adapter._source = FakeFiscal(fail=True)
    failed = adapter.fetch_enterprise_bridge(identity(), analysis_as_of=NOW)
    assert failed.capabilities[0].status is CapabilityStatus.ERROR
    assert adapter.capability("historical_standardized_financials").status is CapabilityStatus.AVAILABLE


def test_fiscal_annual_and_quarterly_actuals_remain_distinct_with_source_metric():
    rows = [fiscal_row(), fiscal_row(period_type="quarterly")]
    result = FiscalAdapter(FakeFiscal(rows), clock=lambda: NOW).fetch_standardized_actuals(identity())
    annual, quarterly = result.observations
    assert annual.metric_id is quarterly.metric_id is MetricId.REVENUE
    assert annual.frequency is Frequency.ANNUAL
    assert quarterly.frequency is Frequency.QUARTERLY
    assert annual.observation_id != quarterly.observation_id
    assert annual.currency == "GBP"
    assert annual.provenance.source_metric == "revenue"
    assert annual.provenance.endpoint_or_dataset == "fiscal_standardized_financials"


def test_fiscal_missing_source_as_of_uses_retrieval_time_with_explicit_provenance():
    result = FiscalAdapter(
        FakeFiscal([fiscal_row(asOf=None)]), clock=lambda: NOW,
    ).fetch_standardized_actuals(identity())

    observation = result.observations[0]
    assert observation.as_of_at == NOW
    assert observation.retrieved_at == NOW
    assert "source as-of unavailable; used retrieval time explicitly" in (
        observation.provenance.transformation_steps
    )


def test_fiscal_capex_and_generic_fcf_have_explicit_non_dcf_semantics():
    result = FiscalAdapter(FakeFiscal([
        fiscal_row("capital_expenditure_cash_outflow", -100), fiscal_row("free_cash_flow", 250),
    ]), clock=lambda: NOW).fetch_standardized_actuals(identity())
    capex, fcf = result.observations
    assert capex.metric_id is MetricId.CAPITAL_EXPENDITURE and capex.value == 100
    assert "negative cash outflow" in capex.provenance.transformation_steps[0]
    assert fcf.metric_id is MetricId.PROVIDER_DEFINED_FCF
    assert "not classified as FCFF or FCFE" in fcf.provenance.transformation_steps[0]


def test_fiscal_positive_capex_convention_and_provider_source_metric_are_preserved():
    row = fiscal_row(
        "capital_expenditure_cash_outflow",
        100,
        sourceMetric="provider_capex_metric",
        signConvention="positive_use_of_cash",
    )

    observation = FiscalAdapter(
        FakeFiscal([row]), clock=lambda: NOW,
    ).fetch_standardized_actuals(identity()).observations[0]

    assert observation.value == 100
    assert observation.provenance.source_metric == "provider_capex_metric"
    assert "positive capital-expenditure" in observation.provenance.transformation_steps[0]


def test_fiscal_operating_income_is_not_silently_duplicated_as_ebit():
    result = FiscalAdapter(FakeFiscal([fiscal_row("operating_income")]), clock=lambda: NOW).fetch_standardized_actuals(identity())
    assert [item.metric_id for item in result.observations] == [MetricId.OPERATING_INCOME]


def test_fiscal_unsupported_and_malformed_rows_are_skipped_with_structured_issue():
    result = FiscalAdapter(FakeFiscal([
        fiscal_row("lookalike_revenue"), fiscal_row("revenue", period_type="mystery"),
    ]), clock=lambda: NOW).fetch_standardized_actuals(identity())
    assert result.observations == ()
    assert len(result.issues) == 1
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE


def test_fiscal_authenticated_failure_is_secret_safe_and_identity_capability_isolated():
    secret = "SYNTHETIC_SECRET_VALUE"
    source = FakeFiscal(fail=True)
    adapter = FiscalAdapter(source, credential=SecretReference("fiscal", lambda: secret), clock=lambda: NOW)
    assert secret not in repr(adapter._credential)
    assert adapter.fetch_identity("F-SYN").capability.status is CapabilityStatus.AVAILABLE
    failure = adapter.fetch_standardized_actuals(identity())
    assert failure.capabilities[0].status is CapabilityStatus.ERROR
    assert secret not in repr(failure)
    assert adapter.capability("identity").status is CapabilityStatus.AVAILABLE


def test_injected_provider_entitlement_error_maps_only_that_fiscal_capability_to_locked():
    class LockedFiscal(FakeFiscal):
        def standardized_financials(self, symbol, *, credential=None):
            raise ProviderError(
                provider=ProviderId.FISCAL,
                endpoint_id="standardized_financials",
                category=ProviderErrorCategory.ENTITLEMENT,
                retryable=False,
                safe_message="capability is not included",
                status_code=403,
            )

    adapter = FiscalAdapter(LockedFiscal(), clock=lambda: NOW)
    assert adapter.fetch_identity("F-SYN").capability.status is CapabilityStatus.AVAILABLE
    result = adapter.fetch_standardized_actuals(identity())
    assert result.capabilities[0].status is CapabilityStatus.LOCKED
    assert adapter.capability("identity").status is CapabilityStatus.AVAILABLE


class FakeSec:
    def __init__(self, rows=(), fail=False):
        self.rows = rows
        self.fail = fail

    def identity_metadata(self, symbol):
        return {"cik": "0000123456", "companyName": "Synthetic plc", "issuerDomicile": "US"}

    def reported_actuals(self, cik):
        if self.fail:
            raise RuntimeError("synthetic raw filing must not escape")
        return self.rows


def sec_row(tag="Revenues", frequency="annual", **changes):
    row = {
        "tag": tag, "taxonomy": "us-gaap", "value": 2_000_000, "currency": "USD",
        "frequency": frequency, "periodStart": "2025-01-01", "periodEnd": "2025-12-31",
        "fiscalYear": 2025, "filedAt": "2026-02-25T16:30:00+00:00",
    }
    if frequency == "quarterly":
        row.update(periodStart="2025-04-01", periodEnd="2025-06-30", fiscalQuarter=2)
    row.update(changes)
    return row


def test_sec_cik_identity_is_distinct_from_lookup_ticker():
    result = SecAdapter(FakeSec(), clock=lambda: NOW).fetch_identity("TICKER-SYN")
    assert result.candidate.provider is ProviderId.SEC
    assert result.candidate.provider_symbol == "0000123456"
    assert result.candidate.provider_symbol != "TICKER-SYN"


def test_sec_annual_and_quarterly_actuals_preserve_filing_time_and_taxonomy():
    result = SecAdapter(FakeSec([sec_row(), sec_row(frequency="quarterly")]), clock=lambda: NOW).fetch_reported_actuals(identity())
    annual, quarterly = result.observations
    assert annual.frequency is Frequency.ANNUAL and quarterly.frequency is Frequency.QUARTERLY
    assert annual.period_end == date(2025, 12, 31)
    assert annual.as_of_at.date() == date(2026, 2, 25)
    assert annual.period_end != annual.as_of_at.date()
    assert annual.provenance.source_metric == "us-gaap:Revenues"
    assert all(item.observation_type is ObservationType.ACTUAL for item in result.observations)


def test_sec_point_in_time_period_and_filing_as_of_remain_distinct():
    row = sec_row(
        "EntityCommonStockSharesOutstanding", frequency=None, value=123_000, currency=None,
        periodStart=None, periodEnd="2026-02-10", filedAt="2026-02-20T16:00:00+00:00",
    )
    observation = SecAdapter(FakeSec([row]), clock=lambda: NOW).fetch_reported_actuals(identity()).observations[0]
    assert observation.frequency is Frequency.POINT_IN_TIME
    assert observation.period_end == date(2026, 2, 10)
    assert observation.as_of_at.date() == date(2026, 2, 20)


def test_sec_unsupported_tag_is_not_guessed_and_failure_is_capability_isolated():
    adapter = SecAdapter(FakeSec([sec_row("AlmostRevenue")]), clock=lambda: NOW)
    assert adapter.fetch_identity("SYN").capability.status is CapabilityStatus.AVAILABLE
    result = adapter.fetch_reported_actuals(identity())
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert adapter.capability("identity").status is CapabilityStatus.AVAILABLE


def test_fiscal_and_sec_same_period_actuals_coexist_without_overwrite():
    fiscal = FiscalAdapter(FakeFiscal([fiscal_row()]), clock=lambda: NOW).fetch_standardized_actuals(identity()).observations[0]
    sec = SecAdapter(FakeSec([sec_row()]), clock=lambda: NOW).fetch_reported_actuals(identity()).observations[0]
    assert fiscal.metric_id is sec.metric_id is MetricId.REVENUE
    assert fiscal.period_end == sec.period_end
    assert fiscal.provenance.provider != sec.provenance.provider
    assert fiscal.observation_id != sec.observation_id
