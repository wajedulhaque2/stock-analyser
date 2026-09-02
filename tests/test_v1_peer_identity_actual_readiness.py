from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import inspect

import pytest

from stock_analyser.domain import (
    ActualSelectionReason,
    ActualSelectionStatus,
    CompanyIdentity,
    EstimateCase,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerCandidate,
    PeerCandidateSource,
    PeerIdentityEvidenceStatus,
    PeerMethodDataStatus,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
    Provenance,
    ProviderSymbol,
    stable_observation_id,
    stable_peer_candidate_id,
)
from stock_analyser.providers import FiscalAdapter, FmpAdapter, ProviderId, ProviderPeerCandidate
from stock_analyser.services import (
    PeerCandidateInput,
    PeerCompanyEvidence,
    assess_peer_method_data_readiness,
    evaluate_peer_candidate,
    select_canonical_actual,
    select_peer_set,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def company(
    symbol: str,
    issuer: str,
    *,
    security: str | None = None,
    sector: str = "Technology",
    industry: str = "Software",
    currency: str = "USD",
) -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=security or f"security-{symbol.lower()}",
        issuer_id=issuer,
        company_name=f"Synthetic {symbol}",
        issuer_domicile="US",
        listing_country="US",
        exchange="NASDAQ",
        sector=sector,
        industry=industry,
        security_type="common stock",
        reporting_currency=currency,
        quote_currency=currency,
        quote_unit=currency,
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", symbol),),
    )


TARGET = company("TGT", "issuer-target")


def discovered(**changes) -> ProviderPeerCandidate:
    values = dict(
        provider=ProviderId.FISCAL,
        provider_symbol="SYN",
        provider_issuer_id="FSCLC-SYN",
        provider_security_id="FSCLS-SYN",
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_rank=1,
        source_as_of=NOW,
        retrieved_at=NOW,
        company_name="Synthetic Peer",
        listing_country="US",
        exchange="NASDAQ",
        sector="Technology",
        industry="Software",
        security_type="common_stock",
        provider_relationship="provider free-text direct competitor assertion",
    )
    values.update(changes)
    return ProviderPeerCandidate(**values)


def actual(
    symbol: str,
    metric: MetricId,
    value: float,
    year: int,
    *,
    as_of: datetime | None = None,
    retrieved: datetime = NOW,
    currency: str = "USD",
    provider: str = "fiscal",
    source_metric: str | None = None,
    substituted_as_of: bool = False,
) -> MetricObservation:
    source_as_of = as_of or NOW
    start, end = date(year, 1, 1), date(year, 12, 31)
    transformations = (
        "source as-of unavailable; used retrieval time explicitly",
    ) if substituted_as_of else ()
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset=f"{provider}_standardized_actuals",
        provider_symbol=symbol,
        retrieved_at=retrieved,
        as_of_at=source_as_of,
        transformation_steps=transformations,
        source_metric=source_metric or metric.value,
    )
    return MetricObservation(
        observation_id=stable_observation_id(
            metric_id=metric.value,
            provider=provider,
            provider_symbol=symbol,
            frequency=Frequency.ANNUAL.value,
            observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value,
            period_start=str(start),
            period_end=str(end),
            as_of_at=source_as_of.isoformat(),
        ),
        metric_id=metric,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency=currency,
        frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=retrieved,
        as_of_at=source_as_of,
        period_start=start,
        period_end=end,
        fiscal_year=year,
        provenance=provenance,
    )


def estimates(symbol: str) -> tuple[MetricObservation, ...]:
    item = actual(symbol, MetricId.EBITDA, 50, 2027, provider="fmp")
    return (replace(
        item,
        observation_type=ObservationType.ESTIMATE,
        estimate_case=EstimateCase.AVERAGE,
        observation_id=item.observation_id.replace("obs:", "est:"),
    ),)


def financials(symbol: str, *, currency: str = "USD") -> tuple[MetricObservation, ...]:
    return (
        actual(symbol, MetricId.REVENUE, 100, 2024, currency=currency),
        actual(symbol, MetricId.REVENUE, 110, 2025, currency=currency),
    )


def peer_input(
    identity,
    *,
    actuals: tuple[MetricObservation, ...] | None = None,
    forward: bool = False,
    rank: int = 1,
) -> PeerCandidateInput:
    provider_symbol = identity.provider_symbol if hasattr(identity, "provider_symbol") else identity.canonical_symbol
    provenance = Provenance(
        provider="fiscal",
        endpoint_or_dataset="fiscal_v3_company_profile_peers",
        provider_symbol=provider_symbol,
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric="peers",
    )
    item = PeerCandidate(
        candidate_id=stable_peer_candidate_id(
            target_security_id=TARGET.security_id,
            candidate_security_id=identity.security_id,
            provider="fiscal",
            provider_symbol=provider_symbol,
            candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        ),
        target_security_id=TARGET.security_id,
        target_issuer_id=TARGET.issuer_id,
        candidate_security_id=identity.security_id,
        candidate_issuer_id=identity.issuer_id,
        provider="fiscal",
        provider_symbol=provider_symbol,
        canonical_symbol=identity.canonical_symbol,
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_as_of=NOW,
        retrieved_at=NOW,
        provenance=provenance,
        source_rank=rank,
    )
    evidence = PeerCompanyEvidence(
        identity=identity,
        actual_observations=financials(provider_symbol) if actuals is None else actuals,
        forward_observations=estimates(provider_symbol) if forward else (),
    )
    return PeerCandidateInput(item, evidence)


def target_evidence(*, actuals: tuple[MetricObservation, ...] | None = None) -> PeerCompanyEvidence:
    return PeerCompanyEvidence(TARGET, financials("TGT") if actuals is None else actuals)


def select_actual(rows, year=2025):
    return select_canonical_actual(
        rows,
        metric_id=MetricId.REVENUE,
        frequency=Frequency.ANNUAL,
        period_end=date(year, 12, 31),
        analysis_as_of=NOW,
    )


def test_stable_summary_ids_seed_canonical_issuer_and_security():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    assert identity.issuer_id == "issuer:fiscal:FSCLC-SYN"
    assert identity.security_id == "security:fiscal:FSCLS-SYN"
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED


def test_summary_identity_is_complete_without_a_second_profile_call():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    assert identity.provider_symbol == "SYN"
    assert "fiscal_year_end" in identity.unavailable_enrichment_fields


@pytest.mark.parametrize("field", ["provider_issuer_id", "provider_security_id"])
def test_missing_stable_summary_identifier_fails_closed(field):
    with pytest.raises(ValueError, match="required identity evidence"):
        FiscalAdapter.build_summary_peer_identity(discovered(**{field: ""}))


def test_missing_required_listing_exchange_fails_closed():
    with pytest.raises(ValueError, match="required identity evidence"):
        FiscalAdapter.build_summary_peer_identity(discovered(exchange=None))


@pytest.mark.parametrize("field", ["listing_country", "security_type"])
def test_optional_summary_classification_fields_do_not_erase_identity(field):
    identity = FiscalAdapter.build_summary_peer_identity(discovered(**{field: None}))
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED


@pytest.mark.parametrize("security_type", ["ETF", "fund", "preferred share", "warrant", "bond"])
def test_non_common_security_class_does_not_erase_summary_identity(security_type):
    identity = FiscalAdapter.build_summary_peer_identity(discovered(security_type=security_type))
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert identity.security_type == security_type


def test_company_name_and_ticker_alone_cannot_seed_cross_provider_identity():
    with pytest.raises(ValueError):
        FiscalAdapter.build_summary_peer_identity(discovered(
            provider_issuer_id="", provider_security_id="",
            company_name="Name Only", provider_symbol="TICKER",
        ))


def test_summary_classification_is_bounded_canonical_evidence():
    identity = FiscalAdapter.build_summary_peer_identity(discovered(
        sector="Communication Services", industry="Interactive Media",
    ))
    assert (identity.sector, identity.industry) == ("Communication Services", "Interactive Media")


def test_provider_relationship_free_text_cannot_establish_industry():
    identity = FiscalAdapter.build_summary_peer_identity(discovered(
        sector=None, industry=None, provider_relationship="same industry direct competitor",
    ))
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(identity), analysis_as_of=NOW,
    )
    assert result.status is PeerSelectionStatus.UNVERIFIED
    assert PeerSelectionReason.INDUSTRY_UNRESOLVED in result.reasons


def test_summary_seeded_candidate_can_be_economically_included():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    result = evaluate_peer_candidate(target_evidence(), peer_input(identity), analysis_as_of=NOW)
    assert result.status is PeerSelectionStatus.INCLUDED
    assert result.evidence.identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED


def test_full_company_identity_is_explicitly_profile_verified():
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(company("ONE", "issuer-one")), analysis_as_of=NOW,
    )
    assert result.evidence.identity.status is PeerIdentityEvidenceStatus.PROFILE_VERIFIED


def test_summary_duplicate_listings_still_deduplicate_by_issuer():
    first = FiscalAdapter.build_summary_peer_identity(discovered())
    second = FiscalAdapter.build_summary_peer_identity(discovered(
        provider_symbol="SYN.A", provider_security_id="FSCLS-SYN-A",
    ))
    peer_set = select_peer_set(target_evidence(), (peer_input(first, rank=1), peer_input(second, rank=2)), analysis_as_of=NOW)
    assert len(peer_set.included_peer_issuer_ids) == 1
    assert any(PeerSelectionReason.DUPLICATE_ISSUER in item.reasons for item in peer_set.selections)


def test_exact_duplicate_actuals_are_deduplicated():
    row = actual("SYN", MetricId.REVENUE, 110, 2025)
    result = select_actual((row, row))
    assert result.status is ActualSelectionStatus.SELECTED
    assert result.reason is ActualSelectionReason.IDENTICAL_DUPLICATES_DEDUPLICATED
    assert result.selected_observation is row


def test_verified_source_chronology_selects_unique_latest_revision():
    first = actual("SYN", MetricId.REVENUE, 100, 2025, as_of=NOW - timedelta(days=10))
    latest = actual("SYN", MetricId.REVENUE, 110, 2025, as_of=NOW - timedelta(days=1))
    result = select_actual((latest, first))
    assert result.status is ActualSelectionStatus.SELECTED
    assert result.reason is ActualSelectionReason.LATEST_VERIFIED_REVISION
    assert result.selected_observation is latest


def test_equal_economic_values_deduplicate_without_using_retrieval_order():
    first = actual("SYN", MetricId.REVENUE, 110, 2025, as_of=NOW - timedelta(days=10))
    later_same_value = actual("SYN", MetricId.REVENUE, 110, 2025, as_of=NOW - timedelta(days=1))
    result = select_actual((later_same_value, first))
    assert result.status is ActualSelectionStatus.SELECTED
    assert result.reason is ActualSelectionReason.IDENTICAL_DUPLICATES_DEDUPLICATED
    assert result.selected_observation.value == 110


def test_conflicting_actuals_without_verified_chronology_are_unresolved():
    one = actual("SYN", MetricId.REVENUE, 100, 2025, as_of=NOW - timedelta(days=2), substituted_as_of=True)
    two = actual("SYN", MetricId.REVENUE, 110, 2025, as_of=NOW - timedelta(days=1), substituted_as_of=True)
    result = select_actual((one, two))
    assert result.status is ActualSelectionStatus.UNRESOLVED
    assert result.selected_observation is None


def test_conflicting_actuals_are_never_averaged():
    rows = (
        actual("SYN", MetricId.REVENUE, 100, 2025, substituted_as_of=True),
        replace(actual("SYN", MetricId.REVENUE, 200, 2025, substituted_as_of=True), observation_id="obs:synthetic-conflict-00000001"),
    )
    result = select_actual(rows)
    assert result.selected_observation is None
    assert all(value != 150 for value in (item.value for item in rows))


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_first_or_last_row_order_cannot_resolve_unordered_conflict(order):
    rows = (
        actual("SYN", MetricId.REVENUE, 100, 2025, substituted_as_of=True),
        replace(actual("SYN", MetricId.REVENUE, 200, 2025, substituted_as_of=True), observation_id="obs:synthetic-conflict-00000002"),
    )
    result = select_actual(tuple(rows[index] for index in order))
    assert result.status is ActualSelectionStatus.UNRESOLVED


def test_retrieval_time_is_not_revision_chronology():
    one = actual("SYN", MetricId.REVENUE, 100, 2025, retrieved=NOW - timedelta(days=3), substituted_as_of=True)
    two = replace(
        actual("SYN", MetricId.REVENUE, 200, 2025, retrieved=NOW, substituted_as_of=True),
        observation_id="obs:synthetic-conflict-00000003",
    )
    assert select_actual((one, two)).status is ActualSelectionStatus.UNRESOLVED


def test_different_source_metrics_do_not_silently_replace_each_other():
    one = actual("SYN", MetricId.REVENUE, 100, 2025, source_metric="reported_revenue")
    two = replace(
        actual("SYN", MetricId.REVENUE, 110, 2025, source_metric="adjusted_revenue"),
        observation_id="obs:synthetic-source-00000000001",
    )
    result = select_actual((one, two))
    assert result.reason is ActualSelectionReason.INCOMPATIBLE_SOURCE_SEMANTICS


def test_sec_does_not_replace_canonical_fiscal_actual():
    fiscal = actual("SYN", MetricId.REVENUE, 100, 2025, provider="fiscal")
    sec = actual("0000000001", MetricId.REVENUE, 110, 2025, provider="sec")
    result = select_actual((fiscal, sec))
    assert result.status is ActualSelectionStatus.UNRESOLVED
    assert result.selected_observation is None


def test_no_eligible_actual_is_structured_unavailable():
    result = select_actual(())
    assert result.status is ActualSelectionStatus.UNAVAILABLE
    assert result.reason is ActualSelectionReason.NO_ELIGIBLE_OBSERVATION


def test_revenue_scale_uses_deterministically_selected_actual():
    target_rows = financials("TGT")
    early = actual("SYN", MetricId.REVENUE, 80, 2025, as_of=NOW - timedelta(days=10))
    latest = actual("SYN", MetricId.REVENUE, 110, 2025, as_of=NOW - timedelta(days=1))
    candidate_rows = (actual("SYN", MetricId.REVENUE, 100, 2024), early, latest)
    result = evaluate_peer_candidate(target_evidence(actuals=target_rows), peer_input(
        FiscalAdapter.build_summary_peer_identity(discovered()), actuals=candidate_rows,
    ), analysis_as_of=NOW)
    scale = next(item for item in result.evidence.criteria if item.criterion.value == "scale")
    assert scale.candidate_value == 110


def test_revenue_growth_uses_deterministically_selected_actuals():
    row = actual("SYN", MetricId.REVENUE, 100, 2024)
    candidate_rows = (row, row, actual("SYN", MetricId.REVENUE, 110, 2025))
    result = evaluate_peer_candidate(target_evidence(), peer_input(
        FiscalAdapter.build_summary_peer_identity(discovered()), actuals=candidate_rows,
    ), analysis_as_of=NOW)
    growth = next(item for item in result.evidence.criteria if item.criterion.value == "growth")
    assert growth.status.value == "pass"
    assert growth.candidate_value == pytest.approx(0.10)


def test_missing_annual_ebitda_remains_missing_and_is_not_derived():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()))
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    margin = next(value for value in result.evidence.criteria if value.criterion.value == "margin")
    assert margin.status.value == "unresolved"
    assert all(row.metric_id is not MetricId.EBITDA for row in item.company.actual_observations)


def test_ebitda_is_not_fabricated_from_operating_income_and_depreciation():
    rows = (
        *financials("SYN"),
        actual("SYN", MetricId.OPERATING_INCOME, 20, 2025),
        actual("SYN", MetricId.DEPRECIATION_AMORTIZATION, 5, 2025),
    )
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), actuals=rows)
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    margin = next(value for value in result.evidence.criteria if value.criterion.value == "margin")
    assert margin.status.value == "unresolved"
    assert not any(row.metric_id is MetricId.EBITDA for row in rows)


def test_missing_forward_ebitda_does_not_block_economic_inclusion():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=False)
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    assert result.status is PeerSelectionStatus.INCLUDED


def test_included_peer_can_have_ev_ebitda_data_unavailable():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=False)
    selection = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    readiness = assess_peer_method_data_readiness(item.candidate, item.company, analysis_as_of=NOW)
    assert selection.status is PeerSelectionStatus.INCLUDED
    assert readiness.status is PeerMethodDataStatus.UNAVAILABLE


def test_forward_ebitda_does_not_turn_industry_mismatch_into_inclusion():
    identity = FiscalAdapter.build_summary_peer_identity(discovered(sector="Energy", industry="Oil & Gas"))
    result = evaluate_peer_candidate(target_evidence(), peer_input(identity, forward=True), analysis_as_of=NOW)
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.INDUSTRY_MISMATCH in result.reasons


def test_forward_ebitda_is_only_method_readiness_evidence():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=True)
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    readiness = assess_peer_method_data_readiness(item.candidate, item.company, analysis_as_of=NOW)
    assert result.status is PeerSelectionStatus.INCLUDED
    assert readiness.status is PeerMethodDataStatus.PARTIAL
    assert readiness.forward_denominator_available


def test_method_readiness_becomes_ready_only_when_all_inputs_are_explicitly_available():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=True)
    readiness = assess_peer_method_data_readiness(
        item.candidate, item.company, analysis_as_of=NOW,
        required_multiple_inputs_available=True,
    )
    assert readiness.status is PeerMethodDataStatus.READY


def test_method_readiness_contains_no_valuation_output():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=True)
    readiness = assess_peer_method_data_readiness(item.candidate, item.company, analysis_as_of=NOW)
    forbidden = {"multiple", "fair_value", "median", "price", "upside", "downside", "stance"}
    assert forbidden.isdisjoint(readiness.__dataclass_fields__)


def test_peer_set_preserves_separate_readiness_per_candidate():
    item = peer_input(FiscalAdapter.build_summary_peer_identity(discovered()), forward=False)
    peer_set = select_peer_set(target_evidence(), (item,), analysis_as_of=NOW)
    assert peer_set.selections[0].status is PeerSelectionStatus.INCLUDED
    assert peer_set.method_data_readiness[0].status is PeerMethodDataStatus.UNAVAILABLE


def test_minimum_three_independent_peers_is_unchanged():
    inputs = tuple(peer_input(FiscalAdapter.build_summary_peer_identity(discovered(
        provider_symbol=f"SYN{index}", provider_issuer_id=f"FSCLC-{index}", provider_security_id=f"FSCLS-{index}",
    ))) for index in range(3))
    assert select_peer_set(target_evidence(), inputs, analysis_as_of=NOW).status is PeerSetStatus.USABLE


def test_two_included_peers_remain_partial():
    inputs = tuple(peer_input(FiscalAdapter.build_summary_peer_identity(discovered(
        provider_symbol=f"SYN{index}", provider_issuer_id=f"FSCLC-{index}", provider_security_id=f"FSCLS-{index}",
    ))) for index in range(2))
    assert select_peer_set(target_evidence(), inputs, analysis_as_of=NOW).status is PeerSetStatus.PARTIAL


def test_cross_currency_scale_remains_unresolved_without_fx():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    item = peer_input(identity, actuals=financials("SYN", currency="GBP"))
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    scale = next(value for value in result.evidence.criteria if value.criterion.value == "scale")
    assert scale.status.value == "unresolved" and scale.comparison_value is None
    assert "no FX" in scale.note


def test_same_currency_scale_rule_is_preserved():
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(FiscalAdapter.build_summary_peer_identity(discovered())), analysis_as_of=NOW,
    )
    scale = next(value for value in result.evidence.criteria if value.criterion.value == "scale")
    assert scale.status.value == "pass" and scale.currency == "USD"


def test_live_peer_path_has_no_fmp_symbol_guess_or_literal_rewrite():
    import stock_analyser.live_peer_audit as live
    source = inspect.getsource(live)
    assert "GOOG" not in source and "GOOGL" not in source and '".L"' not in source
    assert "include_runtime_fmp_symbol=False" in source


def test_no_company_name_search_or_name_join_was_added():
    import stock_analyser.live_peer_audit as live
    import stock_analyser.providers.fmp as fmp
    source = (inspect.getsource(live) + inspect.getsource(fmp)).lower()
    assert "search-name" not in source and "company_name ==" not in source


def test_fmp_symbol_resolution_fails_closed_without_explicit_provider_identity():
    assert FmpAdapter._provider_symbol(TARGET) is None


def test_peer_8a1_path_has_no_valuation_price_dcf_aggregation_or_ui_wiring():
    import stock_analyser.domain.peers as peers
    import stock_analyser.services.actual_selection as actual_service
    import stock_analyser.services.peer_selection as peer_service
    source = (inspect.getsource(peers) + inspect.getsource(actual_service) + inspect.getsource(peer_service)).lower()
    forbidden = (
        "fair_value", "current_price", "peer_median", "own_history", "reverse_dcf",
        "streamlit", "aggregationstatus", "investment_stance",
    )
    assert all(value not in source for value in forbidden)


def test_new_selection_and_readiness_services_make_zero_network_calls_by_construction():
    import stock_analyser.services.actual_selection as actual_service
    import stock_analyser.services.peer_selection as peer_service
    source = inspect.getsource(actual_service) + inspect.getsource(peer_service)
    assert all(value not in source for value in ("requests", "httpx", "urllib", "socket", "yfinance"))
