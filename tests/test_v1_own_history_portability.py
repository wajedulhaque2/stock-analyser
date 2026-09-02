from __future__ import annotations

from datetime import date, datetime, timezone
import inspect
from types import SimpleNamespace

import pytest

from stock_analyser.live_own_history_audit import (
    LiveOwnHistoryAuditOutcome,
    PortabilityClassification,
    classify_method_failure,
)
from stock_analyser.live_smoke import LiveFiscalSource
from stock_analyser.domain import (
    CapabilityStatus,
    EstimateCase,
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
from stock_analyser.providers import FiscalAdapter, IdentityCandidate, ProviderId, SecretReference
from stock_analyser.services import (
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    IdentitySeed,
    OwnHistoryAuditStage,
    assemble_own_history_valuation,
    resolve_company_identity,
)


NOW = datetime(2026, 8, 27, 18, tzinfo=timezone.utc)
PERMISSIVE = HistoricalDistributionPolicy(
    policy_id="synthetic-7e-portability",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=tuple(HistoricalWindowMinimum(window, 1, 0.01) for window in HistoricalWindow),
)


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FiscalIdentitySession:
    def get(self, url, *, params, headers, timeout):
        listing = {
            "listingFiscalIdentifier": "listing-syn",
            "securityFiscalIdentifier": "security-syn",
            "ticker": "SYN",
            "exchangeCode": "NASDAQ",
        }
        if url.endswith("/v3/companies-list"):
            return Response({"data": [{
                "companyKey": "NASDAQ_SYN",
                "companyFiscalIdentifier": "issuer-syn",
                "displayNameEnglish": "Synthetic Corp",
                "reportingCurrency": "USD",
                "primaryListing": listing,
            }]})
        return Response({
            "companyFiscalIdentifier": "issuer-syn",
            "displayNameEnglish": "Synthetic Corp",
            "reportingCurrency": "USD",
            "sector": "Technology",
            "industry": "Software",
            "primaryListing": listing,
            # The provider deliberately supplies no fiscalYearEnd.
        })


class LondonFiscalIdentitySession:
    def __init__(self, exchange_code):
        self.exchange_code = exchange_code

    def get(self, url, *, params, headers, timeout):
        listing = {
            "listingFiscalIdentifier": "listing-shell",
            "securityFiscalIdentifier": "security-shell",
            "ticker": "SHEL",
            "exchangeCode": self.exchange_code,
        }
        if url.endswith("/v3/companies-list"):
            return Response({"data": [{
                "companyKey": f"{self.exchange_code}_SHEL",
                "companyFiscalIdentifier": "issuer-shell",
                "displayNameEnglish": "Shell plc",
                "reportingCurrency": "USD",
                "primaryListing": listing,
            }]})
        return Response({
            "companyFiscalIdentifier": "issuer-shell",
            "displayNameEnglish": "Shell plc",
            "reportingCurrency": "USD",
            "sector": "Energy",
            "industry": "Integrated Oil & Gas",
            "primaryListing": listing,
        })


def yahoo_candidate(*, symbol="SYN", fiscal_year_end="06-30"):
    return IdentityCandidate(
        provider=ProviderId.YAHOO,
        provider_symbol=symbol,
        retrieved_at=NOW,
        company_name="Synthetic Corp",
        issuer_domicile="US",
        listing_country="US",
        exchange="NASDAQ",
        sector="Technology",
        industry="Software",
        security_type="EQUITY",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end=fiscal_year_end,
    )


def test_missing_fiscal_year_end_is_not_fabricated_and_yahoo_source_can_win():
    fiscal = FiscalAdapter(
        LiveFiscalSource(session=FiscalIdentitySession()),
        credential=SecretReference("FISCAL_API_KEY", lambda: "synthetic-secret"),
        clock=lambda: NOW,
    ).fetch_identity("SYN")
    assert fiscal.candidate.fiscal_year_end is None
    resolved = resolve_company_identity(
        IdentitySeed("SYN", "security-syn", "issuer-syn"),
        (fiscal.candidate, yahoo_candidate()),
    )
    assert resolved.identity.fiscal_year_end == "06-30"
    assert ("fiscal_year_end", ProviderId.YAHOO) in resolved.field_sources


def test_london_suffix_resolves_only_an_explicit_london_fiscal_listing():
    fiscal = FiscalAdapter(
        LiveFiscalSource(session=LondonFiscalIdentitySession("LSE")),
        credential=SecretReference("FISCAL_API_KEY", lambda: "synthetic-secret"),
        clock=lambda: NOW,
    ).fetch_identity("SHEL.L")
    assert fiscal.candidate is not None
    assert fiscal.candidate.provider_symbol == "SHEL"
    resolved = resolve_company_identity(
        IdentitySeed("SHEL.L", "security-shell-london", "issuer-shell"),
        (fiscal.candidate, yahoo_candidate(symbol="SHEL.L", fiscal_year_end="12-31")),
    ).identity
    assert resolved.canonical_symbol == "SHEL.L"
    assert {item.provider: item.symbol for item in resolved.provider_symbols} == {
        "fiscal": "SHEL",
        "yahoo": "SHEL.L",
    }


def test_london_suffix_never_maps_to_same_base_ticker_on_us_exchange():
    fiscal = FiscalAdapter(
        LiveFiscalSource(session=LondonFiscalIdentitySession("NYSE")),
        credential=SecretReference("FISCAL_API_KEY", lambda: "synthetic-secret"),
        clock=lambda: NOW,
    ).fetch_identity("SHEL.L")
    assert fiscal.candidate is None
    assert fiscal.capability.status is CapabilityStatus.UNAVAILABLE
    assert "not_found" in fiscal.capability.reason


def test_missing_forward_denominator_is_classified_as_forward_consensus_not_currency():
    method = SimpleNamespace(
        executed=False,
        readiness=SimpleNamespace(blocking_reasons=("FY1 annual consensus period is unavailable",)),
        scale_audit=SimpleNamespace(currency_compatibility_passed=False),
        failure_stage=OwnHistoryAuditStage.FORWARD_DENOMINATOR,
    )
    assert classify_method_failure(method) is PortabilityClassification.FORWARD_CONSENSUS


def test_earliest_historical_failure_is_not_reclassified_by_later_missing_shares():
    method = SimpleNamespace(
        executed=False,
        readiness=SimpleNamespace(blocking_reasons=(
            "Historical distribution is insufficient",
            "Fiscal total shares outstanding",
        )),
        scale_audit=SimpleNamespace(currency_compatibility_passed=False),
        failure_stage=OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS,
    )
    assert classify_method_failure(method) is PortabilityClassification.HISTORICAL_DISTRIBUTION


def test_correct_fail_closed_result_is_a_portability_success_without_numeric_method():
    outcome = LiveOwnHistoryAuditOutcome(
        symbol="NO-DATA",
        result=SimpleNamespace(valuations=(), methods=()),
        failure_stage=OwnHistoryAuditStage.READINESS,
        issues=(),
    )
    assert outcome.succeeded


def _provenance(provider, symbol, metric):
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_portability",
        provider_symbol=symbol,
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric=metric,
    )


def _actual(metric, value, observed, currency, fiscal_symbol):
    unit = MetricUnit.SHARES if metric is MetricId.SHARES_OUTSTANDING else MetricUnit.CURRENCY
    provenance = _provenance("fiscal", fiscal_symbol, metric.value)
    return MetricObservation(
        observation_id=f"obs-portability-{metric.value}-{observed.isoformat()}",
        metric_id=metric,
        value=value,
        unit=unit,
        frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=NOW,
        as_of_at=NOW,
        provenance=provenance,
        currency=None if unit is MetricUnit.SHARES else currency,
        period_end=observed,
    )


def portable_assembly(
    canonical_symbol,
    *,
    security_id,
    issuer_id,
    fiscal_symbol,
    fmp_symbol,
    reporting_currency,
    quote_currency,
    quote_unit,
    price_scale,
    listing_country="GB",
    security_type="EQUITY",
    adr_ratio=None,
    historical_values=(10.0, 20.0, 30.0),
    include_history=True,
):
    seed = IdentitySeed(canonical_symbol, security_id, issuer_id)
    candidates = (
        IdentityCandidate(
            provider=ProviderId.FISCAL,
            provider_symbol=fiscal_symbol,
            retrieved_at=NOW,
            company_name=f"{canonical_symbol} issuer",
            issuer_domicile=listing_country,
            sector="Energy" if canonical_symbol.startswith("ENERGY") else "Industrials",
            industry="Synthetic",
            reporting_currency=reporting_currency,
            fiscal_year_end="12-31",
        ),
        IdentityCandidate(
            provider=ProviderId.YAHOO,
            provider_symbol=canonical_symbol,
            retrieved_at=NOW,
            company_name=f"{canonical_symbol} issuer",
            issuer_domicile=listing_country,
            listing_country=listing_country,
            exchange="LSE" if canonical_symbol.endswith(".L") else "NASDAQ",
            sector="Industrials",
            industry="Synthetic",
            security_type=security_type,
            reporting_currency=reporting_currency,
            quote_currency=quote_currency,
            quote_unit=quote_unit,
            price_scale=price_scale,
            fiscal_year_end="12-31",
            underlying_security_id=(f"underlying-{issuer_id}" if "ADR" in security_type else None),
            adr_ratio=adr_ratio,
        ),
        IdentityCandidate(ProviderId.FMP, fmp_symbol, NOW),
    )
    history = ()
    if include_history:
        source_metric = "ratio_ev_to_ebitda"
        history = tuple(
            HistoricalValuationObservation(
                observation_id=stable_historical_valuation_id(
                    security_id=security_id,
                    issuer_id=issuer_id,
                    provider="fiscal",
                    provider_symbol=fiscal_symbol,
                    multiple_type=HistoricalMultipleType.EV_EBITDA,
                    observation_date=observed,
                    period_end=None,
                    sampling=HistoricalValuationSampling.DAILY,
                    source_metric=source_metric,
                ),
                security_id=security_id,
                issuer_id=issuer_id,
                provider="fiscal",
                provider_symbol=fiscal_symbol,
                multiple_type=HistoricalMultipleType.EV_EBITDA,
                valuation_basis=ValuationBasis.ENTERPRISE,
                denominator=HistoricalValuationDenominator.EBITDA,
                value=value,
                observation_date=observed,
                sampling=HistoricalValuationSampling.DAILY,
                as_of_at=NOW,
                retrieved_at=NOW,
                provenance=_provenance("fiscal", fiscal_symbol, source_metric),
                source_metric=source_metric,
                eligibility=HistoricalValuationEligibility.ELIGIBLE,
                quote_currency_context=quote_currency,
                reporting_currency_context=reporting_currency,
            )
            for observed, value in zip(
                (date(2021, 8, 27), date(2024, 8, 27), NOW.date()), historical_values,
            )
        )
    forwards = tuple(
        MetricObservation(
            observation_id=f"obs-portability-ebitda-{year}-{case.value}",
            metric_id=MetricId.EBITDA,
            value=100 + year - 2027 + offset,
            unit=MetricUnit.CURRENCY,
            frequency=Frequency.ANNUAL,
            observation_type=ObservationType.ESTIMATE,
            estimate_case=case,
            retrieved_at=NOW,
            as_of_at=NOW,
            provenance=_provenance("fmp", fmp_symbol, "ebitdaAvg"),
            currency=reporting_currency,
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            fiscal_year=year,
        )
        for year in (2027, 2028)
        for case, offset in (
            (EstimateCase.LOW, -1), (EstimateCase.AVERAGE, 0), (EstimateCase.HIGH, 1),
        )
    )
    bridge = (
        _actual(MetricId.ENTERPRISE_VALUE, 150.0, date(2026, 8, 26), reporting_currency, fiscal_symbol),
        _actual(MetricId.MARKET_CAP, 100.0, date(2026, 8, 26), reporting_currency, fiscal_symbol),
        _actual(MetricId.SHARES_OUTSTANDING, 10.0, date(2026, 8, 20), None, fiscal_symbol),
    )
    return assemble_own_history_valuation(
        identity_seed=seed,
        identity_candidates=candidates,
        historical_observations=history,
        forward_observations=forwards,
        bridge_observations=bridge,
        analysis_as_of=NOW,
        methods=(HistoricalMultipleType.EV_EBITDA,),
        distribution_policy=PERMISSIVE,
        share_count_semantics=ShareCountSemantics.ISSUER_SHARES,
    )


def test_same_orchestration_executes_multiple_canonical_securities_independently():
    first = portable_assembly(
        "USD-A", security_id="security-a", issuer_id="issuer-a", fiscal_symbol="F-A",
        fmp_symbol="FMP-A", reporting_currency="USD", quote_currency="USD", quote_unit="USD",
        price_scale=1, listing_country="US",
    )
    second = portable_assembly(
        "GBP-B.L", security_id="security-b", issuer_id="issuer-b", fiscal_symbol="B",
        fmp_symbol="B-LON", reporting_currency="GBP", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01,
    )
    assert first.identity.security_id != second.identity.security_id
    assert all(item.status is ValuationMethodStatus.VALID for item in (*first.valuations, *second.valuations))


def test_london_canonical_and_issuer_join_survive_differing_provider_symbols():
    result = portable_assembly(
        "PORT.L", security_id="security-port-l", issuer_id="issuer-port", fiscal_symbol="PORT",
        fmp_symbol="PORT-GB", reporting_currency="GBP", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01,
    )
    assert result.identity.canonical_symbol == "PORT.L"
    assert result.identity.issuer_id == "issuer-port"
    assert {item.provider: item.symbol for item in result.identity.provider_symbols} == {
        "fiscal": "PORT", "fmp": "PORT-GB", "yahoo": "PORT.L",
    }
    assert result.valuations[0].security_id == "security-port-l"
    assert result.valuations[0].issuer_id == "issuer-port"


def test_gbp_financial_values_remain_gbp_and_gbp_per_share_is_not_gbp_quote_unit():
    result = portable_assembly(
        "GBP.L", security_id="security-gbp", issuer_id="issuer-gbp", fiscal_symbol="GBP",
        fmp_symbol="GBP-GB", reporting_currency="GBP", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01,
    )
    valuation = result.valuations[0]
    assert result.identity.quote_unit == "GBp"
    assert result.identity.price_scale == 0.01
    assert valuation.currency == "GBP"
    assert valuation.central_point.per_share_value == pytest.approx(195.0)
    assert valuation.central_point.per_share_value != pytest.approx(19_500.0)


def test_usd_reporting_with_uk_listing_stays_usd_without_implicit_fx():
    result = portable_assembly(
        "USD-UK.L", security_id="security-usd-uk", issuer_id="issuer-usd-uk", fiscal_symbol="USD-UK",
        fmp_symbol="USD-UK-L", reporting_currency="USD", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01,
    )
    assert result.identity.listing_country == "GB"
    assert result.identity.quote_currency == "GBP"
    assert result.identity.reporting_currency == "USD"
    assert result.valuations[0].currency == "USD"


def test_unresolved_adr_share_conversion_blocks_execution():
    result = portable_assembly(
        "ADR-X", security_id="security-adr", issuer_id="issuer-adr", fiscal_symbol="ADR-X",
        fmp_symbol="ADR-X", reporting_currency="USD", quote_currency="USD", quote_unit="USD",
        price_scale=1, listing_country="US", security_type="ADR", adr_ratio=None,
    )
    method = result.method(HistoricalMultipleType.EV_EBITDA)
    assert method.readiness.status is OwnHistoryMethodStatus.NOT_READY
    assert not method.executed
    assert any("ADR" in reason for reason in method.readiness.blocking_reasons)


def test_high_multiple_observation_is_retained_without_nvda_specific_cap():
    result = portable_assembly(
        "HIGH", security_id="security-high", issuer_id="issuer-high", fiscal_symbol="HIGH",
        fmp_symbol="HIGH", reporting_currency="USD", quote_currency="USD", quote_unit="USD",
        price_scale=1, listing_country="US", historical_values=(40.0, 100.0, 250.0),
    )
    distribution = result.method(HistoricalMultipleType.EV_EBITDA).distribution
    assert distribution.maximum == 250.0
    assert distribution.p75 == pytest.approx(175.0)
    assert not distribution.observations_altered


def test_energy_identity_does_not_switch_default_away_from_five_year_window():
    result = portable_assembly(
        "ENERGY.L", security_id="security-energy", issuer_id="issuer-energy", fiscal_symbol="ENERGY",
        fmp_symbol="ENERGY-L", reporting_currency="USD", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01,
    )
    assert result.historical_window is HistoricalWindow.FIVE_YEAR
    assert result.method(HistoricalMultipleType.EV_EBITDA).distribution.window is HistoricalWindow.FIVE_YEAR


def test_one_security_unavailability_does_not_invalidate_another():
    unavailable = portable_assembly(
        "MISS.L", security_id="security-miss", issuer_id="issuer-miss", fiscal_symbol="MISS",
        fmp_symbol="MISS-L", reporting_currency="GBP", quote_currency="GBP", quote_unit="GBp",
        price_scale=0.01, include_history=False,
    )
    available = portable_assembly(
        "GOOD", security_id="security-good", issuer_id="issuer-good", fiscal_symbol="GOOD",
        fmp_symbol="GOOD", reporting_currency="USD", quote_currency="USD", quote_unit="USD",
        price_scale=1, listing_country="US",
    )
    assert not unavailable.valuations
    assert unavailable.method(HistoricalMultipleType.EV_EBITDA).failure_stage is OwnHistoryAuditStage.HISTORICAL_OBSERVATIONS
    assert available.valuations[0].status is ValuationMethodStatus.VALID


def test_own_history_portability_path_has_no_macro_price_ranking_or_ui_dependency():
    import stock_analyser.services.own_history_orchestration as orchestration
    import stock_analyser.live_own_history_audit as live_audit

    source = (inspect.getsource(orchestration) + inspect.getsource(live_audit)).lower()
    for forbidden in (
        "fred", "risk_free", "current_price", "fetch_market_snapshot", "upside =", "downside =",
        "overall_fair_value", "ranking", "streamlit",
    ):
        assert forbidden not in source
