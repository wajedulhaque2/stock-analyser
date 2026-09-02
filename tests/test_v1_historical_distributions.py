from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
import math

import pytest

from stock_analyser.domain import (
    HistoricalDistributionUsability,
    HistoricalDuplicateHandling,
    HistoricalMultipleDistribution,
    HistoricalMultipleType,
    HistoricalQuantileConvention,
    HistoricalValuationDenominator,
    HistoricalValuationEligibility,
    HistoricalValuationObservation,
    HistoricalValuationSampling,
    HistoricalWindow,
    MetricUnit,
    Provenance,
    ValuationBasis,
    stable_historical_valuation_id,
)
from stock_analyser.services import (
    DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    build_historical_distribution,
    build_historical_window_availability,
    linear_quantile,
)


ANALYSIS_AS_OF = datetime(2026, 8, 27, 16, tzinfo=timezone.utc)
RETRIEVED = datetime(2026, 8, 27, 17, tzinfo=timezone.utc)
SEMANTICS = {
    HistoricalMultipleType.P_E: (
        ValuationBasis.EQUITY,
        HistoricalValuationDenominator.DILUTED_EPS,
        "ratio_price_to_earnings",
    ),
    HistoricalMultipleType.EV_EBITDA: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.EBITDA,
        "ratio_ev_to_ebitda",
    ),
    HistoricalMultipleType.EV_EBIT: (
        ValuationBasis.ENTERPRISE,
        HistoricalValuationDenominator.PROVIDER_OPERATING_PROFIT_AS_EBIT,
        "ratio_ev_to_ebit",
    ),
}


PERMISSIVE_POLICY = HistoricalDistributionPolicy(
    policy_id="synthetic-permissive-linear",
    default_window=HistoricalWindow.FIVE_YEAR,
    window_minimums=tuple(
        HistoricalWindowMinimum(window, 1, 0.01) for window in HistoricalWindow
    ),
)


def observation(
    observation_date: date,
    value: float,
    *,
    multiple_type: HistoricalMultipleType = HistoricalMultipleType.P_E,
    eligibility: HistoricalValuationEligibility = HistoricalValuationEligibility.ELIGIBLE,
    provider: str = "fiscal",
    provider_symbol: str = "F-SYN",
    security_id: str = "security-syn",
    issuer_id: str = "issuer-syn",
) -> HistoricalValuationObservation:
    basis, denominator, source_metric = SEMANTICS[multiple_type]
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset="synthetic_historical_ratios",
        provider_symbol=provider_symbol,
        retrieved_at=RETRIEVED,
        as_of_at=RETRIEVED,
        source_metric=source_metric,
    )
    return HistoricalValuationObservation(
        observation_id=stable_historical_valuation_id(
            security_id=security_id,
            issuer_id=issuer_id,
            provider=provider,
            provider_symbol=provider_symbol,
            multiple_type=multiple_type,
            observation_date=observation_date,
            period_end=None,
            sampling=HistoricalValuationSampling.DAILY,
            source_metric=source_metric,
        ),
        security_id=security_id,
        issuer_id=issuer_id,
        provider=provider,
        provider_symbol=provider_symbol,
        multiple_type=multiple_type,
        valuation_basis=basis,
        denominator=denominator,
        value=value,
        observation_date=observation_date,
        sampling=HistoricalValuationSampling.DAILY,
        as_of_at=RETRIEVED,
        retrieved_at=RETRIEVED,
        provenance=provenance,
        source_metric=source_metric,
        eligibility=eligibility,
        eligibility_reason=(None if eligibility is HistoricalValuationEligibility.ELIGIBLE else "synthetic ineligible evidence"),
        quote_currency_context="USD",
        reporting_currency_context="USD",
    )


def distribution(items, *, window=None, multiple_type=HistoricalMultipleType.P_E, policy=PERMISSIVE_POLICY):
    return build_historical_distribution(
        items,
        security_id="security-syn",
        issuer_id="issuer-syn",
        provider="fiscal",
        multiple_type=multiple_type,
        analysis_as_of=ANALYSIS_AS_OF,
        window=window,
        policy=policy,
    )


def spaced_history(*, start: date, end: date, count: int, value_base: float = 10.0):
    span = (end - start).days
    return tuple(
        observation(
            start + timedelta(days=round(index * span / (count - 1))),
            value_base + (index % 17) / 10,
        )
        for index in range(count)
    )


@pytest.mark.parametrize(
    ("window", "included_date", "excluded_date"),
    [
        (HistoricalWindow.THREE_YEAR, date(2023, 8, 27), date(2023, 8, 26)),
        (HistoricalWindow.FIVE_YEAR, date(2021, 8, 27), date(2021, 8, 26)),
        (HistoricalWindow.TEN_YEAR, date(2016, 8, 27), date(2016, 8, 26)),
    ],
)
def test_windows_use_inclusive_calendar_year_boundaries(window, included_date, excluded_date):
    result = distribution([
        observation(excluded_date, 1),
        observation(included_date, 2),
        observation(ANALYSIS_AS_OF.date(), 3),
    ], window=window)
    assert result.window_start == included_date
    assert result.window_end == ANALYSIS_AS_OF.date()
    assert result.sample_count == 2
    assert result.minimum == 2 and result.maximum == 3


def test_default_window_is_five_year_and_windows_are_never_blended():
    items = [
        observation(date(2018, 1, 1), 10),
        observation(date(2022, 1, 1), 20),
        observation(date(2025, 1, 1), 30),
    ]
    default = distribution(items)
    three = distribution(items, window=HistoricalWindow.THREE_YEAR)
    ten = distribution(items, window=HistoricalWindow.TEN_YEAR)
    assert default.window is HistoricalWindow.FIVE_YEAR
    assert default.supporting_observation_ids == (items[1].observation_id, items[2].observation_id)
    assert three.sample_count == 1 and three.median == 30
    assert ten.sample_count == 3 and ten.median == 20


def test_future_and_ineligible_observations_are_excluded_but_ineligible_count_is_visible():
    items = [
        observation(date(2025, 1, 1), 12),
        observation(date(2025, 2, 1), -3, eligibility=HistoricalValuationEligibility.INELIGIBLE),
        observation(date(2025, 3, 1), 0, eligibility=HistoricalValuationEligibility.INELIGIBLE),
        observation(date(2025, 4, 1), 50, eligibility=HistoricalValuationEligibility.UNVERIFIED),
        observation(date(2026, 8, 28), 100),
    ]
    result = distribution(items)
    assert result.sample_count == result.eligible_count == 1
    assert result.ineligible_count == 3
    assert result.total_candidate_count == 4
    assert result.eligible_fraction == pytest.approx(1 / 4)
    assert result.median == 12
    assert any("after analysis_as_of" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("multiple_type", tuple(HistoricalMultipleType))
def test_multiple_basis_denominator_sampling_and_ratio_unit_survive_distribution(multiple_type):
    basis, denominator, _ = SEMANTICS[multiple_type]
    result = distribution([
        observation(date(2024, 1, 1), 10, multiple_type=multiple_type),
        observation(date(2026, 1, 1), 20, multiple_type=multiple_type),
    ], multiple_type=multiple_type)
    assert result.multiple_type is multiple_type
    assert result.valuation_basis is basis
    assert result.denominator is denominator
    assert result.sampling is HistoricalValuationSampling.DAILY
    assert result.unit is MetricUnit.RATIO
    assert result.independent_regime_count is None


def test_linear_quantiles_and_descriptive_statistics_are_exact_and_deterministic():
    items = [observation(date(2025, month, 1), float(month)) for month in range(1, 5)]
    result = distribution(items)
    assert result.p25 == 1.75
    assert result.median == 2.5
    assert result.p75 == 3.25
    assert result.minimum == 1
    assert result.maximum == 4
    assert result.mean == 2.5
    assert result.standard_deviation == pytest.approx(math.sqrt(1.25))
    assert result.iqr == 1.5
    assert result.quantile_convention is HistoricalQuantileConvention.LINEAR
    assert linear_quantile((1.0, 3.0, 9.0), 0.5) == 3


def test_median_is_correct_for_odd_sample():
    result = distribution([
        observation(date(2025, 1, 1), 9),
        observation(date(2025, 2, 1), 1),
        observation(date(2025, 3, 1), 3),
    ])
    assert result.median == 3


def test_extreme_finite_value_is_preserved_with_no_outlier_or_missing_day_manufacture():
    items = [
        observation(date(2024, 1, 2), 10),
        observation(date(2024, 7, 19), 11),
        observation(date(2026, 8, 27), 1_000_000),
    ]
    result = distribution(items)
    assert result.sample_count == 3
    assert result.calendar_month_count == 3
    assert result.maximum == 1_000_000
    assert result.supporting_observation_ids == tuple(item.observation_id for item in items)
    assert not result.observations_altered
    assert not result.winsorization_applied
    assert not result.trimming_applied
    assert not result.interpolation_applied


def test_identical_duplicate_semantic_date_is_counted_once():
    item = observation(date(2025, 1, 1), 20)
    result = distribution([item, item])
    assert result.total_candidate_count == 1
    assert result.sample_count == 1
    assert result.duplicate_count == 1
    assert result.conflicting_duplicate_count == 0
    assert result.duplicate_handling is HistoricalDuplicateHandling.DEDUPLICATE_IDENTICAL_FAIL_CONFLICT


def test_conflicting_duplicate_values_fail_numeric_distribution_closed():
    first = observation(date(2025, 1, 1), 20, provider_symbol="F-ONE")
    second = observation(date(2025, 1, 1), 21, provider_symbol="F-TWO")
    result = distribution([first, second])
    assert result.usability is HistoricalDistributionUsability.INSUFFICIENT
    assert result.sample_count == 0
    assert result.median is None
    assert result.conflicting_duplicate_count == 1
    assert any(issue.severity.value == "blocking" for issue in result.issues)


def test_minimum_observation_count_is_enforced_independently_of_span():
    items = spaced_history(
        start=date(2021, 8, 27), end=date(2026, 8, 27), count=100,
    )
    result = distribution(items, policy=DEFAULT_HISTORICAL_DISTRIBUTION_POLICY)
    assert result.span_coverage == 1
    assert result.usability is HistoricalDistributionUsability.PARTIAL
    assert any("sample count" in issue.reason for issue in result.issues)


def test_count_alone_cannot_make_short_span_five_year_sample_usable():
    policy = HistoricalDistributionPolicy(
        policy_id="synthetic-count-vs-span",
        default_window=HistoricalWindow.FIVE_YEAR,
        window_minimums=(
            HistoricalWindowMinimum(HistoricalWindow.THREE_YEAR, 10, 0.80),
            HistoricalWindowMinimum(HistoricalWindow.FIVE_YEAR, 500, 0.80),
            HistoricalWindowMinimum(HistoricalWindow.TEN_YEAR, 10, 0.80),
        ),
    )
    items = spaced_history(start=date(2024, 8, 27), end=date(2026, 8, 27), count=500)
    result = distribution(items, policy=policy)
    assert result.sample_count == 500
    assert result.span_coverage == pytest.approx(0.4, abs=0.002)
    assert result.usability is HistoricalDistributionUsability.PARTIAL
    assert any("span coverage" in issue.reason for issue in result.issues)


@pytest.mark.parametrize(
    ("window", "start", "count"),
    [
        (HistoricalWindow.THREE_YEAR, date(2023, 8, 27), 500),
        (HistoricalWindow.FIVE_YEAR, date(2021, 8, 27), 750),
        (HistoricalWindow.TEN_YEAR, date(2016, 8, 27), 1_250),
    ],
)
def test_adequate_default_window_samples_are_usable(window, start, count):
    items = spaced_history(start=start, end=ANALYSIS_AS_OF.date(), count=count)
    result = distribution(
        items, window=window, policy=DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    )
    assert result.sample_count == count
    assert result.span_coverage == 1
    assert result.usability is HistoricalDistributionUsability.USABLE


def test_short_history_can_make_three_year_usable_without_five_year_fallback():
    items = spaced_history(
        start=date(2023, 8, 27), end=ANALYSIS_AS_OF.date(), count=500,
    )
    availability = build_historical_window_availability(
        items,
        security_id="security-syn",
        issuer_id="issuer-syn",
        provider="fiscal",
        multiple_type=HistoricalMultipleType.P_E,
        analysis_as_of=ANALYSIS_AS_OF,
        policy=DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    )
    three, five, ten = availability
    assert three.window is HistoricalWindow.THREE_YEAR
    assert three.usability is HistoricalDistributionUsability.USABLE
    assert five.window is HistoricalWindow.FIVE_YEAR
    assert five.usability is HistoricalDistributionUsability.INSUFFICIENT
    assert five.sample_count == 500
    assert ten.window is HistoricalWindow.TEN_YEAR
    assert ten.usability is HistoricalDistributionUsability.INSUFFICIENT


def test_selected_provider_is_not_combined_with_another_provider():
    fiscal = observation(date(2025, 1, 1), 10)
    other = observation(date(2025, 2, 1), 100, provider="other", provider_symbol="O-SYN")
    result = distribution([fiscal, other])
    assert result.sample_count == 1
    assert result.median == 10
    assert result.provider == "fiscal"
    assert any("other providers" in issue.reason for issue in result.issues)


def test_provider_symbol_variation_does_not_change_canonical_distribution_identity():
    first = distribution([observation(date(2025, 1, 1), 10, provider_symbol="F-ONE")])
    second = distribution([observation(date(2025, 1, 1), 10, provider_symbol="F-TWO")])
    assert first.distribution_id == second.distribution_id
    assert first.security_id == second.security_id
    assert first.provider_symbols == ("F-ONE",)
    assert second.provider_symbols == ("F-TWO",)


def test_distribution_is_immutable_and_contains_no_valuation_output_fields():
    result = distribution([observation(date(2025, 1, 1), 10)])
    names = {field.name for field in fields(HistoricalMultipleDistribution)}
    assert not names.intersection({
        "fair_value", "target_price", "upside", "downside", "forward_estimate",
        "peer_value", "dcf_value", "aggregate_value",
    })
    with pytest.raises(FrozenInstanceError):
        result.median = 99


def test_policy_is_immutable_centralized_and_has_no_ineligible_share_rejection_threshold():
    policy = DEFAULT_HISTORICAL_DISTRIBUTION_POLICY
    assert policy.default_window is HistoricalWindow.FIVE_YEAR
    assert [item.minimum_observation_count for item in policy.window_minimums] == [500, 750, 1_250]
    assert all(item.minimum_span_coverage == 0.80 for item in policy.window_minimums)
    assert policy.minimum_eligible_fraction is None
    with pytest.raises(FrozenInstanceError):
        policy.default_window = HistoricalWindow.THREE_YEAR


def test_analysis_as_of_must_be_explicit_and_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_historical_distribution(
            (),
            security_id="security-syn",
            issuer_id="issuer-syn",
            provider="fiscal",
            multiple_type=HistoricalMultipleType.P_E,
            analysis_as_of=datetime(2026, 8, 27),
        )
