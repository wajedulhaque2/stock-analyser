from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import ast
import importlib
import inspect
import socket

import pytest

from stock_analyser.domain import (
    AggregationStatus,
    DataAvailability,
    EstimateCase,
    ExpectationGapEvidence,
    FamilyValuationEvidence,
    Frequency,
    MarketComparisonResult,
    MarketComparisonScope,
    MarketPriceSemantic,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    PublicationEvidenceType,
    PublicationSupplementalEvidence,
    ReverseDcfExecutionMode,
    ReverseDcfPublicationEligibility,
    ValuationFamily,
    ValuationMethodStatus,
    stable_family_valuation_evidence_id,
    stable_publication_supplemental_evidence_id,
)
from stock_analyser.services import (
    build_market_price_evidence,
    compare_publication_to_market,
    expectation_gap_from_reverse_dcf,
    publish_cross_family_valuation,
)
from test_v1_reverse_dcf_execution import execute_canonical, execute_scenario


NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
PRICE_AS_OF = datetime(2026, 8, 29, 16, tzinfo=timezone.utc)
SECURITY = "security:synthetic:11b"
ISSUER = "issuer:synthetic:11b"


def provenance(*, as_of=PRICE_AS_OF, provider="yahoo", metric="regularMarketPrice"):
    return Provenance(
        provider=provider,
        endpoint_or_dataset="synthetic_market_snapshot",
        provider_symbol="SYN",
        retrieved_at=NOW,
        as_of_at=as_of,
        source_metric=metric,
    )


def observation(
    value=90.0,
    *,
    as_of=PRICE_AS_OF,
    currency="USD",
    provider="yahoo",
    source_metric="regularMarketPrice",
    metric=MetricId.SHARE_PRICE,
    unit=MetricUnit.CURRENCY_PER_SHARE,
):
    prov = provenance(as_of=as_of, provider=provider, metric=source_metric)
    return MetricObservation(
        observation_id=f"observation:yahoo:price:{as_of.timestamp():.0f}:{value}",
        metric_id=metric,
        value=value,
        unit=unit,
        frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=NOW,
        as_of_at=as_of,
        provenance=prov,
        currency=currency,
        period_end=as_of.date(),
    )


def price(
    *,
    value=90.0,
    raw=None,
    currency="USD",
    quote_unit="USD",
    scale=1.0,
    as_of=PRICE_AS_OF,
    analysis_as_of=NOW,
    target_security=SECURITY,
    target_issuer=ISSUER,
    observation_security=SECURITY,
    observation_issuer=ISSUER,
    obs=None,
):
    source = observation(value, as_of=as_of, currency=currency) if obs is None else obs
    return build_market_price_evidence(
        target_security_id=target_security,
        target_issuer_id=target_issuer,
        observation_security_id=observation_security,
        observation_issuer_id=observation_issuer,
        analysis_as_of=analysis_as_of,
        observation=source,
        quote_currency=currency,
        quote_unit=quote_unit,
        quote_unit_scale=scale,
        raw_quote_value=raw,
    )


def family(
    valuation_family=ValuationFamily.OWN_HISTORY,
    lower=90.0,
    central=110.0,
    upper=130.0,
    *,
    status=ValuationMethodStatus.VALID,
    eligible=True,
    currency="USD",
    unit="USD/share",
    security=SECURITY,
    issuer=ISSUER,
    as_of=NOW,
    suffix="one",
):
    source_id = f"source:{valuation_family.value}:{suffix}"
    prov = Provenance(
        provider="synthetic-valuation",
        endpoint_or_dataset=f"{valuation_family.value}_result",
        provider_symbol="SYN",
        retrieved_at=NOW,
        as_of_at=as_of,
        source_metric="per_share_valuation",
    )
    return FamilyValuationEvidence(
        evidence_id=stable_family_valuation_evidence_id(
            security, issuer, valuation_family.value, source_id, as_of.isoformat(),
        ),
        target_security_id=security,
        target_issuer_id=issuer,
        valuation_family=valuation_family,
        selected_method="EV_EBITDA",
        source_result_id=source_id,
        analysis_as_of=as_of,
        currency=currency,
        per_share_unit=unit,
        lower_value=lower,
        central_value=central,
        upper_value=upper,
        family_status=status,
        central_valuation_eligible=eligible,
        supporting_ids=(f"support:{suffix}",),
        policy_ids=(f"policy:{suffix}",),
        provenance=(prov,),
    )


def publication(*families, supplemental=(), **changes):
    values = dict(
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        analysis_as_of=NOW,
        family_candidates=tuple(families),
        supplemental_evidence=tuple(supplemental),
    )
    values.update(changes)
    return publish_cross_family_valuation(**values)


def resolved_publication():
    return publication(
        family(lower=80, central=95, upper=110),
        family(ValuationFamily.PEER, lower=90, central=105, upper=120),
    )


def unresolved_publication():
    return publication(family())


def wide_publication():
    return publication(
        family(lower=90, central=95, upper=120),
        family(ValuationFamily.PEER, lower=110, central=115, upper=140),
    )


def test_yahoo_canonical_regular_market_price_is_accepted():
    result = price()
    assert result.status is DataAvailability.AVAILABLE
    assert result.price_semantic is MarketPriceSemantic.YAHOO_REGULAR_MARKET_PRICE
    assert result.normalized_price_per_share == 90
    assert result.source_observation_id.startswith("observation:yahoo:price")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"observation_security": "security:other"}, "security identity"),
        ({"observation_issuer": "issuer:other"}, "issuer identity"),
    ],
)
def test_market_price_requires_exact_security_and_issuer_identity(changes, reason):
    result = price(**changes)
    assert result.status is DataAvailability.UNAVAILABLE
    assert any(reason in issue.reason for issue in result.issues)


def test_issuer_match_alone_is_insufficient_for_another_share_class_or_ads():
    result = price(observation_security="security:ordinary", observation_issuer=ISSUER)
    assert result.status is DataAvailability.UNAVAILABLE
    assert result.normalized_price_per_share is None


def test_future_price_is_retained_as_unavailable_evidence_not_used():
    future = NOW + timedelta(minutes=1)
    result = price(as_of=future)
    assert result.status is DataAvailability.UNAVAILABLE
    assert result.observation_timestamp == future
    assert any("after the analysis" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("age", [0, 1, 2, 3])
def test_latest_session_within_centralized_three_calendar_day_window_is_accepted(age):
    as_of = NOW - timedelta(days=age)
    result = price(as_of=as_of)
    assert result.status is DataAvailability.AVAILABLE


def test_friday_close_is_accepted_on_monday_without_interpolation():
    monday = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    friday = datetime(2026, 8, 28, 16, tzinfo=timezone.utc)
    result = price(as_of=friday, analysis_as_of=monday)
    assert result.status is DataAvailability.AVAILABLE
    assert result.observation_timestamp == friday


@pytest.mark.parametrize("age", [4, 10, 365])
def test_stale_market_price_is_unavailable(age):
    result = price(as_of=NOW - timedelta(days=age))
    assert result.status is DataAvailability.UNAVAILABLE
    assert any("freshness" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_canonical_price_is_rejected(value):
    result = price(value=value)
    assert result.status is DataAvailability.UNAVAILABLE
    assert result.normalized_price_per_share is None


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_price_cannot_enter_canonical_observation(value):
    with pytest.raises(ValueError, match="finite"):
        observation(value)


def test_usd_scale_one_preserves_raw_quote_exactly():
    result = price(value=123.456789, raw=123.456789)
    assert result.raw_quote_value == result.normalized_price_per_share == 123.456789
    assert result.quote_currency == "USD" and result.quote_unit == "USD"


@pytest.mark.parametrize("quote_unit", ["GBp", "GBX"])
def test_verified_gbpence_scale_normalizes_to_gbp_per_share_without_fx(quote_unit):
    result = price(value=7.5, raw=750, currency="GBP", quote_unit=quote_unit, scale=0.01)
    assert result.status is DataAvailability.AVAILABLE
    assert result.raw_quote_value == 750
    assert result.quote_currency == "GBP" and result.quote_unit == quote_unit
    assert result.normalized_price_per_share == 7.5
    assert result.normalized_per_share_unit == "GBP/share"


def test_gbp_currency_and_gbpence_quote_unit_remain_distinct_fields():
    result = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    assert result.quote_currency != result.quote_unit
    assert result.normalized_currency == result.quote_currency


@pytest.mark.parametrize("missing", ["unit", "scale", "currency"])
def test_missing_quote_metadata_blocks_normalization_without_guessing(missing):
    kwargs = dict(
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        observation_security_id=SECURITY,
        observation_issuer_id=ISSUER,
        analysis_as_of=NOW,
        observation=observation(7.5, currency="GBP"),
        quote_currency="GBP",
        quote_unit="GBp",
        quote_unit_scale=0.01,
    )
    kwargs[{"unit": "quote_unit", "scale": "quote_unit_scale", "currency": "quote_currency"}[missing]] = None
    result = build_market_price_evidence(**kwargs)
    assert result.status is DataAvailability.PARTIAL
    assert result.normalized_price_per_share is None


@pytest.mark.parametrize(
    "currency,unit,scale",
    [
        ("USD", "USD", 0.01),
        ("GBP", "GBp", 1.0),
        ("GBP", "USD", 1.0),
        ("GBP", "unknown", 0.01),
    ],
)
def test_inconsistent_or_unsupported_quote_unit_scale_fails_closed(currency, unit, scale):
    result = price(value=7.5, currency=currency, quote_unit=unit, scale=scale)
    assert result.status is DataAvailability.UNAVAILABLE
    assert any("scale semantics" in issue.reason for issue in result.issues)


def test_raw_quote_must_reconstruct_existing_canonical_yahoo_value():
    result = price(value=7.5, raw=700, currency="GBP", quote_unit="GBp", scale=0.01)
    assert result.status is DataAvailability.UNAVAILABLE
    assert any("reconstruct" in issue.reason for issue in result.issues)


def test_non_yahoo_observation_cannot_enter_canonical_market_price_evidence():
    obs = observation(provider="other", source_metric="some_price")
    result = price(obs=obs)
    assert result.status is DataAvailability.UNAVAILABLE
    assert any("Yahoo market-price" in issue.reason for issue in result.issues)


def test_provider_field_semantics_stay_inside_yahoo_adapter_boundary():
    service_source = inspect.getsource(
        importlib.import_module("stock_analyser.services.market_comparison"),
    )
    adapter_source = open("src/stock_analyser/providers/yahoo.py", encoding="utf-8").read()
    assert "regularMarketPrice" not in service_source
    assert '"regularMarketPrice": (MetricId.SHARE_PRICE' in adapter_source


def test_synthetic_resolved_overall_comparison_uses_exact_11a_values():
    result = compare_publication_to_market(resolved_publication(), price())
    overall = result.overall_comparison
    assert overall.status is DataAvailability.AVAILABLE
    assert overall.comparison_scope is MarketComparisonScope.OVERALL_RESOLVED
    assert (overall.lower_value, overall.central_value, overall.upper_value) == (80, 100, 120)
    assert (overall.lower_gap, overall.central_gap, overall.upper_gap) == (-10, 10, 30)
    assert overall.central_gap_percent == pytest.approx(100 / 90 - 1)


def test_resolved_overall_uses_family_envelope_not_common_overlap():
    publication_result = resolved_publication()
    result = compare_publication_to_market(publication_result, price())
    assert result.overall_comparison.lower_value == publication_result.envelope_lower == 80
    assert result.overall_comparison.upper_value == publication_result.envelope_upper == 120
    assert publication_result.overlap_lower == 90 and publication_result.overlap_upper == 110


@pytest.mark.parametrize(
    "publication_result,status",
    [
        (unresolved_publication(), AggregationStatus.UNRESOLVED),
        (wide_publication(), AggregationStatus.WIDE),
        (publication(), AggregationStatus.UNAVAILABLE),
    ],
)
def test_nonresolved_publication_has_no_overall_price_comparison(publication_result, status):
    result = compare_publication_to_market(publication_result, price())
    overall = result.overall_comparison
    assert publication_result.publication_status is status
    assert overall.status is DataAvailability.UNAVAILABLE
    assert overall.central_value is None and overall.central_gap_percent is None


@pytest.mark.parametrize("publication_factory", [unresolved_publication, wide_publication])
def test_complete_family_comparison_is_available_even_when_overall_is_not(publication_factory):
    result = compare_publication_to_market(publication_factory(), price(value=100))
    available = tuple(item for item in result.family_comparisons if item.status is DataAvailability.AVAILABLE)
    assert available
    assert result.overall_comparison.status is DataAvailability.UNAVAILABLE


def test_synthetic_unresolved_family_comparison_is_not_labeled_overall():
    result = compare_publication_to_market(unresolved_publication(), price(value=100))
    item = result.family_comparisons[0]
    assert item.comparison_scope is MarketComparisonScope.INDIVIDUAL_FAMILY
    assert item.valuation_family is ValuationFamily.OWN_HISTORY
    assert item.central_value == 110 and item.central_gap_percent == pytest.approx(0.10)
    assert result.overall_comparison.central_gap_percent is None


def test_family_comparison_retains_exact_family_source_and_supporting_ids():
    publication_result = unresolved_publication()
    source = publication_result.family_evidence[0]
    item = compare_publication_to_market(publication_result, price()).family_comparisons[0]
    assert item.valuation_source_id == source.source_result_id
    assert source.evidence_id in item.supporting_ids
    assert source.supporting_ids[0] in item.supporting_ids


def test_partial_family_is_retained_without_three_point_gap_arithmetic():
    partial = family(
        upper=None,
        status=ValuationMethodStatus.PARTIAL,
        eligible=False,
    )
    result = compare_publication_to_market(publication(partial), price())
    item = result.family_comparisons[0]
    assert item.status is DataAvailability.PARTIAL
    assert item.lower_value == 90 and item.central_value == 110 and item.upper_value is None
    assert item.lower_gap is None and item.central_gap_percent is None


def test_price_currency_mismatch_blocks_comparison_without_fx():
    result = compare_publication_to_market(
        unresolved_publication(),
        price(value=75, currency="GBP", quote_unit="GBP"),
    )
    item = result.family_comparisons[0]
    assert item.status is DataAvailability.UNAVAILABLE
    assert any("no FX" in issue for issue in item.issues)


def test_per_share_unit_mismatch_blocks_comparison():
    publication_result = publication(family(currency="GBP", unit="GBp/share"))
    market = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    result = compare_publication_to_market(publication_result, market)
    assert result.family_comparisons[0].status is DataAvailability.UNAVAILABLE


def test_gbpence_synthetic_family_gap_is_twenty_percent_without_fx():
    publication_result = publication(
        family(lower=8, central=9, upper=10, currency="GBP", unit="GBP/share"),
    )
    market = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    item = compare_publication_to_market(publication_result, market).family_comparisons[0]
    assert item.status is DataAvailability.AVAILABLE
    assert item.central_gap == 1.5
    assert item.central_gap_percent == pytest.approx(0.20)


@pytest.mark.parametrize(
    "market_value,central,expected_sign",
    [(100, 80, -1), (100, 120, 1)],
)
def test_negative_and_positive_valuation_gaps_are_retained_without_judgment(market_value, central, expected_sign):
    result = compare_publication_to_market(
        publication(family(lower=central - 10, central=central, upper=central + 10)),
        price(value=market_value),
    )
    gap = result.family_comparisons[0].central_gap
    assert (gap > 0) - (gap < 0) == expected_sign


def test_service_performs_no_rounding():
    result = compare_publication_to_market(
        publication(family(lower=100.000001, central=110.000003, upper=120.000007)),
        price(value=97.000009),
    )
    item = result.family_comparisons[0]
    assert item.central_gap == 110.000003 - 97.000009
    assert item.central_gap_percent == 110.000003 / 97.000009 - 1


def test_market_price_zero_cannot_reach_gap_division():
    market = price(value=0)
    result = compare_publication_to_market(unresolved_publication(), market)
    assert market.status is DataAvailability.UNAVAILABLE
    assert result.family_comparisons[0].central_gap_percent is None


@pytest.mark.parametrize("mode", ["below_lower", "above_upper"])
def test_price_outside_family_range_does_not_create_economic_label(mode):
    market_value = 50 if mode == "below_lower" else 200
    item = compare_publication_to_market(unresolved_publication(), price(value=market_value)).family_comparisons[0]
    assert item.status is DataAvailability.AVAILABLE
    fields = set(item.__dataclass_fields__)
    assert fields.isdisjoint({"judgment", "cheap", "expensive", "attractive", "margin_of_safety"})


def expectation(mode=ReverseDcfExecutionMode.CANONICAL_EVIDENCE):
    eligibility = (
        ReverseDcfPublicationEligibility.CANONICAL
        if mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE
        else ReverseDcfPublicationEligibility.SCENARIO_ONLY
    )
    scenario_ids = () if mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE else ("scenario:one",)
    return ExpectationGapEvidence(
        evidence_id=f"expectation:{mode.value}",
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        analysis_as_of=NOW,
        reverse_dcf_result_id=f"reverse:{mode.value}",
        execution_mode=mode,
        publication_eligibility=eligibility,
        central_valuation_eligible=False,
        implied_terminal_growth=0.03,
        final_consensus_revenue_growth=0.05,
        final_consensus_ebit_margin=0.40,
        growth_rate_difference=-0.02,
        scenario_assumption_ids=scenario_ids,
        status=DataAvailability.AVAILABLE,
        issues=(),
        warnings=(),
        supporting_ids=("reverse:support",),
        policy_ids=("reverse:policy",),
        provenance=(provenance(provider="synthetic-reverse", metric="terminal_growth"),),
    )


@pytest.mark.parametrize(
    "mode,eligibility",
    [
        (ReverseDcfExecutionMode.CANONICAL_EVIDENCE, ReverseDcfPublicationEligibility.CANONICAL),
        (ReverseDcfExecutionMode.EXPLICIT_SCENARIO, ReverseDcfPublicationEligibility.SCENARIO_ONLY),
    ],
)
def test_reverse_dcf_expectations_retain_canonical_or_scenario_labels_without_price_gap(mode, eligibility):
    evidence = expectation(mode)
    assert evidence.publication_eligibility is eligibility
    assert evidence.central_valuation_eligible is False
    assert not hasattr(evidence, "price_gap") and not hasattr(evidence, "upside")


def test_real_canonical_reverse_dcf_adapter_retains_raw_growth_comparators():
    source = execute_canonical()
    evidence = expectation_gap_from_reverse_dcf(source)
    assert evidence.execution_mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE
    assert evidence.implied_terminal_growth == source.solver_result.implied_terminal_growth
    assert evidence.final_consensus_revenue_growth == source.final_consensus_revenue_growth
    assert evidence.growth_rate_difference == pytest.approx(
        source.solver_result.implied_terminal_growth - source.final_consensus_revenue_growth
    )


def test_real_scenario_reverse_dcf_adapter_remains_scenario_only():
    source = execute_scenario()
    evidence = expectation_gap_from_reverse_dcf(source)
    assert evidence.execution_mode is ReverseDcfExecutionMode.EXPLICIT_SCENARIO
    assert evidence.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert evidence.scenario_assumption_ids == source.scenario_assumption_ids


def test_expectation_growth_difference_must_be_raw_arithmetic_not_judgment():
    evidence = expectation()
    assert evidence.growth_rate_difference == pytest.approx(0.03 - 0.05)
    assert set(evidence.__dataclass_fields__).isdisjoint({
        "judgment", "score", "mispricing", "expected_return", "valuation_upside",
    })


def test_reverse_dcf_expectation_does_not_change_publication_status_or_family_count():
    publication_result = unresolved_publication()
    comparison = compare_publication_to_market(
        publication_result,
        price(),
        reverse_dcf_results=(execute_canonical(),),
    )
    assert publication_result.publication_status is AggregationStatus.UNRESOLVED
    assert publication_result.eligible_family_count == 1
    # The reverse-DCF helper fixture belongs to a different analysis snapshot.
    # It must therefore be rejected rather than silently joined to this result.
    assert comparison.expectation_evidence == ()
    assert "reverse-DCF expectation target/snapshot does not match market comparison" in comparison.issues
    assert comparison.overall_comparison.status is DataAvailability.UNAVAILABLE


def test_external_analyst_and_fmp_dcf_references_remain_reference_only_and_uncompared():
    extras = tuple(
        PublicationSupplementalEvidence(
            evidence_id=stable_publication_supplemental_evidence_id(SECURITY, kind.value),
            target_security_id=SECURITY,
            target_issuer_id=ISSUER,
            evidence_type=kind,
            source_result_id=f"external:{kind.value}",
            evidence_as_of=NOW,
            central_valuation_eligible=False,
        )
        for kind in (
            PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
            PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
        )
    )
    publication_result = publication(family(), supplemental=extras)
    result = compare_publication_to_market(publication_result, price())
    assert publication_result.eligible_family_count == 1
    assert result.reference_comparisons == ()
    assert result.overall_comparison.status is DataAvailability.UNAVAILABLE


def test_market_comparison_retains_market_family_and_expectation_provenance():
    source = execute_canonical()
    expectation = expectation_gap_from_reverse_dcf(source)
    result = compare_publication_to_market(unresolved_publication(), price())
    assert result.market_price_evidence.provenance[0] in result.provenance
    assert result.family_comparisons[0].provenance
    assert expectation.provenance


def test_current_price_does_not_feed_backward_into_11a_publication():
    publication_result = unresolved_publication()
    before = publication_result
    low_price = compare_publication_to_market(publication_result, price(value=1))
    high_price = compare_publication_to_market(publication_result, price(value=1000))
    assert publication_result == before
    assert low_price.overall_comparison.status is high_price.overall_comparison.status
    assert low_price.family_comparisons[0].central_value == high_price.family_comparisons[0].central_value == 110


def test_comparison_contracts_are_immutable():
    result = compare_publication_to_market(unresolved_publication(), price())
    with pytest.raises(FrozenInstanceError):
        result.overall_comparison.status = DataAvailability.AVAILABLE
    with pytest.raises(FrozenInstanceError):
        result.market_price_evidence.normalized_price_per_share = 1


def test_market_comparison_result_rejects_mismatched_nested_target():
    result = compare_publication_to_market(unresolved_publication(), price())
    mismatched = replace(result.family_comparisons[0], target_security_id="security:other")
    with pytest.raises(ValueError, match="share target"):
        replace(result, family_comparisons=(mismatched,))


def test_market_comparison_result_rejects_nested_price_evidence_mismatch():
    result = compare_publication_to_market(unresolved_publication(), price())
    mismatched = replace(result.family_comparisons[0], market_price_evidence_id="marketprice:other")
    with pytest.raises(ValueError, match="bundled market-price"):
        replace(result, family_comparisons=(mismatched,))


def test_expectation_contract_requires_controlled_execution_enums():
    evidence = expectation_gap_from_reverse_dcf(execute_canonical())
    with pytest.raises(TypeError, match="execution_mode"):
        replace(evidence, execution_mode="canonical_evidence")


def test_no_recommendation_stance_target_price_or_margin_of_safety_fields():
    result = compare_publication_to_market(unresolved_publication(), price())
    names = set(result.__dataclass_fields__) | set(result.overall_comparison.__dataclass_fields__)
    forbidden = {
        "buy", "hold", "sell", "overweight", "underweight", "stance",
        "recommendation", "target_price", "margin_of_safety", "judgment", "score",
    }
    assert names.isdisjoint(forbidden)


def test_production_service_has_no_ticker_branch_fx_rounding_or_valuation_recalculation():
    module = importlib.import_module("stock_analyser.services.market_comparison")
    source = inspect.getsource(module).lower()
    identifiers = {
        node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
    }
    assert 'endswith(".l")' not in source and "endswith('.l')" not in source and "ticker" not in source
    assert {"round", "calculate_own_history_valuation", "calculate_peer_target_valuation", "calculate_wacc"}.isdisjoint(identifiers)
    assert "fx" in source  # only appears in explicit no-FX blocker text
    assert "requests" not in source and "streamlit" not in source


def test_no_provider_adapter_or_legacy_ui_wiring():
    app_source = open("app.py", encoding="utf-8").read()
    yahoo_source = open("src/stock_analyser/providers/yahoo.py", encoding="utf-8").read()
    assert "market_comparison" not in app_source
    assert "MarketPriceEvidence" not in yahoo_source


def test_default_pytest_path_makes_no_network_call(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    result = compare_publication_to_market(unresolved_publication(), price())
    assert result.family_comparisons[0].status is DataAvailability.AVAILABLE
