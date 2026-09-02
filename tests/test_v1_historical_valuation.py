from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
import math

import pytest

from stock_analyser.domain import (
    CapabilityStatus,
    CompanyIdentity,
    HistoricalMultipleType,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    MetricUnit,
    ProviderSymbol,
    ValuationBasis,
)
from stock_analyser.live_smoke import LiveFiscalSource, render_text, run_selected, build_live_runners
from stock_analyser.providers import FiscalAdapter


NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
ANALYSIS_AS_OF = datetime(2026, 8, 26, 23, 59, tzinfo=timezone.utc)
SECRET = "SYNTHETIC_FISCAL_SECRET"


def identity(*, fiscal_symbol: str = "F-SYN") -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol="SYN",
        security_id="canonical-security",
        issuer_id="canonical-issuer",
        company_name="Synthetic plc",
        issuer_domicile="GB",
        listing_country="GB",
        exchange="Synthetic Exchange",
        sector="Industrials",
        industry="Engineering",
        security_type="Ordinary share",
        reporting_currency="GBP",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", fiscal_symbol),),
    )


def ratio_row(source_metric="ratio_price_to_earnings", value=20.0, **changes):
    row = {
        "sourceMetric": source_metric,
        "observationDate": "2025-06-30",
        "samplingBasis": "daily",
        "asOf": None,
        "value": value,
    }
    row.update(changes)
    return row


class FakeFiscalHistory:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.seen_credential = None

    def identity_metadata(self, symbol, *, credential=None):
        return {}

    def standardized_financials(self, symbol, *, credential=None):
        return ()

    def historical_valuation_ratios(self, symbol, *, credential=None):
        self.seen_credential = credential
        return self.rows


def normalize(rows, *, company=None, analysis_as_of=ANALYSIS_AS_OF):
    return FiscalAdapter(FakeFiscalHistory(rows), clock=lambda: NOW).fetch_historical_valuation(
        company or identity(), analysis_as_of=analysis_as_of,
    )


def test_supported_multiples_have_controlled_equity_enterprise_and_denominator_semantics():
    result = normalize([
        ratio_row(),
        ratio_row("ratio_ev_to_ebitda", 15),
        ratio_row("ratio_ev_to_ebit", 18),
    ])
    pe, ev_ebitda, ev_ebit = result.observations
    assert (pe.multiple_type, pe.valuation_basis, pe.denominator) == (
        HistoricalMultipleType.P_E, ValuationBasis.EQUITY,
        HistoricalValuationDenominator.DILUTED_EPS,
    )
    assert (ev_ebitda.multiple_type, ev_ebitda.valuation_basis, ev_ebitda.denominator) == (
        HistoricalMultipleType.EV_EBITDA, ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.EBITDA,
    )
    assert (ev_ebit.multiple_type, ev_ebit.valuation_basis, ev_ebit.denominator) == (
        HistoricalMultipleType.EV_EBIT, ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
    )
    assert "Operating Profit" in ev_ebit.provenance.transformation_steps[0]
    assert "forward" not in pe.denominator.value and "normalized" not in pe.denominator.value


def test_operating_income_is_not_generically_promoted_to_ebit():
    result = normalize([ratio_row("ratio_ev_to_operating_income")])
    assert result.observations == ()
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert result.issues[0].observed == "ratio_ev_to_operating_income"


@pytest.mark.parametrize("source_metric", ["ratio_price_to_free_cash_flow", "ratio_ev_to_fcf"])
def test_cash_flow_multiples_are_withheld_when_fcf_economics_are_unverified(source_metric):
    result = normalize([ratio_row(source_metric)])
    assert result.observations == ()
    assert len(result.issues) == 1
    assert "FCF economics are unverified" in result.issues[0].reason
    assert not hasattr(HistoricalMultipleType, "P_FCF")
    assert not hasattr(HistoricalMultipleType, "EV_FCF")


@pytest.mark.parametrize("source_metric", [
    "ratio_price_to_earnings", "ratio_ev_to_ebitda", "ratio_ev_to_ebit",
])
def test_positive_supported_multiple_is_eligible(source_metric):
    observation = normalize([ratio_row(source_metric, 12.5)]).observations[0]
    assert observation.eligibility is HistoricalValuationEligibility.ELIGIBLE
    assert observation.eligibility_reason is None


@pytest.mark.parametrize("value", [-2.0, 0.0])
def test_non_positive_multiple_is_preserved_but_explicitly_ineligible(value):
    observation = normalize([ratio_row(value=value)]).observations[0]
    assert observation.value == value
    assert observation.eligibility is HistoricalValuationEligibility.INELIGIBLE
    assert "non-positive" in observation.eligibility_reason


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_multiple_fails_closed(value):
    result = normalize([ratio_row(value=value)])
    assert result.observations == ()
    assert len(result.issues) == 1


def test_multiple_type_basis_sampling_and_unit_are_controlled_enums():
    observation = normalize([ratio_row()]).observations[0]
    with pytest.raises(TypeError, match="multiple_type"):
        replace(observation, multiple_type="P_E")
    with pytest.raises(TypeError, match="valuation_basis"):
        replace(observation, valuation_basis="equity")
    with pytest.raises(TypeError, match="sampling"):
        replace(observation, sampling="daily")
    with pytest.raises(ValueError, match="dimensionless"):
        replace(observation, unit=MetricUnit.CURRENCY)


def test_observation_identity_is_stable_immutable_and_bound_to_canonical_security_and_issuer():
    first = normalize([ratio_row()]).observations[0]
    second = FiscalAdapter(FakeFiscalHistory([ratio_row()]), clock=lambda: datetime(
        2026, 9, 1, tzinfo=timezone.utc,
    )).fetch_historical_valuation(identity(), analysis_as_of=ANALYSIS_AS_OF).observations[0]
    assert first.observation_id == second.observation_id
    assert (first.security_id, first.issuer_id) == ("canonical-security", "canonical-issuer")
    with pytest.raises(FrozenInstanceError):
        first.value = 99


def test_provider_symbol_is_provenance_not_canonical_identity():
    first = normalize([ratio_row()], company=identity(fiscal_symbol="F-ONE")).observations[0]
    second = normalize([ratio_row()], company=identity(fiscal_symbol="F-TWO")).observations[0]
    assert first.security_id == second.security_id == "canonical-security"
    assert first.issuer_id == second.issuer_id == "canonical-issuer"
    assert first.provider_symbol != second.provider_symbol
    assert first.provenance.provider_symbol == "F-ONE"


def test_date_period_sampling_and_snapshot_times_remain_distinct_without_interpolation():
    rows = [
        ratio_row(observationDate="2024-12-31", samplingBasis="annual", periodEnd="2024-12-31"),
        ratio_row(observationDate="2025-06-30", samplingBasis="quarterly", periodEnd="2025-03-31"),
    ]
    annual, quarterly = normalize(rows).observations
    assert annual.observation_date == date(2024, 12, 31)
    assert annual.period_end == date(2024, 12, 31)
    assert annual.sampling is HistoricalValuationSampling.ANNUAL
    assert quarterly.observation_date == date(2025, 6, 30)
    assert quarterly.period_end == date(2025, 3, 31)
    assert quarterly.sampling is HistoricalValuationSampling.QUARTERLY
    assert annual.retrieved_at == annual.as_of_at == NOW
    assert annual.observation_date != annual.retrieved_at.date()
    assert len((annual, quarterly)) == len(rows)


def test_ratio_is_not_fx_converted_and_currency_is_audit_context_only():
    observation = normalize([ratio_row(value=123.45)]).observations[0]
    assert observation.value == 123.45
    assert observation.unit is MetricUnit.RATIO
    assert observation.quote_currency_context == "USD"
    assert observation.reporting_currency_context == "GBP"
    assert "without FX conversion" in observation.provenance.transformation_steps[1]


def test_extremely_high_finite_outlier_is_preserved_without_winsorization():
    observation = normalize([ratio_row(value=1_000_000.0)]).observations[0]
    assert observation.value == 1_000_000.0
    assert observation.eligibility is HistoricalValuationEligibility.ELIGIBLE


def test_missing_source_metric_and_unknown_definition_fail_closed():
    missing = normalize([ratio_row(source_metric=None)])
    unknown = normalize([ratio_row("ratio_unknown_economic_definition")])
    assert missing.observations == unknown.observations == ()
    assert "no safe source metric" in missing.issues[0].reason
    assert "no approved canonical economic definition" in unknown.issues[0].reason


def test_future_dated_rows_are_excluded_relative_to_explicit_analysis_as_of():
    result = normalize([
        ratio_row(observationDate="2026-08-26"),
        ratio_row(observationDate="2026-08-27"),
    ])
    assert [item.observation_date for item in result.observations] == [date(2026, 8, 26)]
    assert "excluded 1 observations" in result.issues[0].reason
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize([ratio_row()], analysis_as_of=datetime(2026, 8, 26))


def test_source_metric_is_mandatory_and_matches_provenance():
    observation = normalize([ratio_row()]).observations[0]
    assert observation.source_metric == observation.provenance.source_metric
    with pytest.raises(ValueError, match="match provenance"):
        replace(observation, source_metric="different_metric")


def test_contract_contains_no_fair_value_target_forward_or_percentile_fields():
    names = {field.name for field in fields(HistoricalValuationObservation)}
    assert not names.intersection({
        "fair_value", "target_price", "upside", "downside", "percentile",
        "median", "forward_estimate", "peer_value", "dcf_value",
    })


def test_fiscal_live_source_uses_fixed_user_agent_company_key_and_verified_bare_list_shape():
    class Response:
        status_code = 200
        headers = {}

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith("/v3/companies-list"):
                return Response({"data": [{
                    "companyKey": "NASDAQ_SYN",
                    "primaryListing": {"ticker": "SYN"},
                }]})
            return Response([
                {"date": "2025-01-02", "ratio": 10.0},
                {"date": "2025-01-03", "ratio": 11.0},
            ])

    session = Session()
    rows = tuple(LiveFiscalSource(session=session).historical_valuation_ratios(
        "SYN", credential=SECRET,
    ))
    ratio_calls = session.calls[1:]
    assert len(ratio_calls) == 3
    assert all(call[1]["params"] == {"companyKey": "NASDAQ_SYN"} for call in ratio_calls)
    assert all(call[1]["headers"]["User-Agent"] == "StockAnalyser/0.7.1" for call in ratio_calls)
    assert all("company" not in call[1]["params"] for call in ratio_calls)
    assert len(rows) == 6
    assert set(rows[0]) == {"sourceMetric", "observationDate", "value", "samplingBasis"}
    assert rows[0]["samplingBasis"] == "daily"
    assert SECRET not in repr(rows)


def test_fiscal_smoke_renders_a_separate_safe_historical_valuation_block():
    class FakeLiveFiscal:
        def identity_metadata(self, symbol, *, credential=None):
            return {}

        def standardized_financials(self, symbol, *, credential=None):
            return ()

        def historical_valuation_ratios(self, symbol, *, credential=None):
            return (ratio_row(), ratio_row("ratio_ev_to_ebitda", 10), ratio_row("ratio_ev_to_ebit", 11))

    import stock_analyser.live_smoke as smoke

    original = smoke.LiveFiscalSource
    smoke.LiveFiscalSource = FakeLiveFiscal
    try:
        result = run_selected(
            symbol="META",
            providers=("fiscal",),
            runners=build_live_runners({"FISCAL_API_KEY": SECRET}),
        )
    finally:
        smoke.LiveFiscalSource = original
    rendered = render_text(result)
    assert "FISCAL HISTORICAL VALUATION" in rendered
    assert "multiple types" in rendered
    assert "EV_EBIT" in rendered and "P_E" in rendered
    assert SECRET not in rendered
