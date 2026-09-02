from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timezone
import inspect

import pytest

from stock_analyser.domain import (
    CompanyIdentity,
    EstimateCase,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerCandidate,
    PeerCandidateSource,
    PeerCriterion,
    PeerCriterionStatus,
    PeerIdentityEvidenceStatus,
    PeerMethodDataStatus,
    PeerSecurityEligibilityStatus,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
    Provenance,
    ProviderSymbol,
    stable_observation_id,
    stable_peer_candidate_id,
)
from stock_analyser.providers import FiscalAdapter, ProviderId, ProviderPeerCandidate
from stock_analyser.services import (
    PeerCandidateInput,
    PeerCompanyEvidence,
    assess_peer_method_data_readiness,
    classify_peer_security_eligibility,
    evaluate_peer_candidate,
    select_peer_set,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def company(symbol: str, issuer_id: str, *, industry: str = "Software") -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=f"security-{symbol.lower()}",
        issuer_id=issuer_id,
        company_name=f"Synthetic {symbol}",
        issuer_domicile="US",
        listing_country="US",
        exchange="NASDAQ",
        sector="Technology",
        industry=industry,
        security_type="common stock",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
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
        security_type=None,
        provider_relationship="direct_competitor",
    )
    values.update(changes)
    return ProviderPeerCandidate(**values)


def provenance(provider: str = "fiscal", symbol: str = "SYN", dataset: str = "peer_summary") -> Provenance:
    return Provenance(
        provider=provider,
        endpoint_or_dataset=dataset,
        provider_symbol=symbol,
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric="security_type",
    )


def revenue(symbol: str, year: int, value: float) -> MetricObservation:
    period_start, period_end = date(year, 1, 1), date(year, 12, 31)
    source = provenance(symbol=symbol, dataset="fiscal_standardized_actuals")
    return MetricObservation(
        observation_id=stable_observation_id(
            metric_id=MetricId.REVENUE.value,
            provider="fiscal",
            provider_symbol=symbol,
            frequency=Frequency.ANNUAL.value,
            observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value,
            period_start=str(period_start),
            period_end=str(period_end),
            as_of_at=NOW.isoformat(),
        ),
        metric_id=MetricId.REVENUE,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency="USD",
        frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=NOW,
        as_of_at=NOW,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=year,
        provenance=source,
    )


def financials(symbol: str) -> tuple[MetricObservation, ...]:
    return revenue(symbol, 2024, 100), revenue(symbol, 2025, 110)


def peer_input(identity, *, security_type: str | None = None, exchange: str = "NASDAQ", forward=()):
    candidate_provenance = provenance(symbol=identity.provider_symbol)
    candidate = PeerCandidate(
        candidate_id=stable_peer_candidate_id(
            target_security_id=TARGET.security_id,
            candidate_security_id=identity.security_id,
            provider="fiscal",
            provider_symbol=identity.provider_symbol,
            candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        ),
        target_security_id=TARGET.security_id,
        target_issuer_id=TARGET.issuer_id,
        candidate_security_id=identity.security_id,
        candidate_issuer_id=identity.issuer_id,
        provider="fiscal",
        provider_symbol=identity.provider_symbol,
        canonical_symbol=identity.canonical_symbol,
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_as_of=NOW,
        retrieved_at=NOW,
        provenance=candidate_provenance,
        source_rank=1,
    )
    eligibility = classify_peer_security_eligibility(
        identity,
        security_type=security_type,
        provenance=candidate_provenance,
        listing_symbol=identity.provider_symbol,
        exchange=exchange,
    )
    return PeerCandidateInput(candidate, PeerCompanyEvidence(
        identity=identity,
        actual_observations=financials(identity.provider_symbol),
        forward_observations=tuple(forward),
        security_eligibility=eligibility,
    ))


def target_evidence() -> PeerCompanyEvidence:
    return PeerCompanyEvidence(TARGET, financials("TGT"))


def test_stable_fiscal_ids_and_listing_establish_identity_without_security_type():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    assert identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert identity.issuer_id == "issuer:fiscal:FSCLC-SYN"
    assert identity.security_id == "security:fiscal:FSCLS-SYN"
    assert identity.security_type is None


def test_missing_security_type_is_separate_unverified_eligibility():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    item = peer_input(identity)
    result = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    security = result.evidence.criterion(PeerCriterion.SECURITY_TYPE)
    assert result.evidence.identity.status is PeerIdentityEvidenceStatus.SUMMARY_VERIFIED
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.UNVERIFIED
    assert security.status is PeerCriterionStatus.UNRESOLVED
    assert result.status is PeerSelectionStatus.UNVERIFIED
    assert PeerSelectionReason.SECURITY_ELIGIBILITY_UNVERIFIED in result.reasons


@pytest.mark.parametrize("descriptive_field", [
    {"company_name": "Common Equity Corporation"},
    {"provider_relationship": "ordinary-equity peer"},
])
def test_descriptive_metadata_does_not_prove_security_class(descriptive_field):
    identity = FiscalAdapter.build_summary_peer_identity(discovered(**descriptive_field))
    result = evaluate_peer_candidate(target_evidence(), peer_input(identity), analysis_as_of=NOW)
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.UNVERIFIED


def test_operating_company_does_not_prove_common_equity():
    candidate = discovered()
    # Fiscal companyType is intentionally not present in the normalized security-type field.
    assert candidate.security_type is None
    identity = FiscalAdapter.build_summary_peer_identity(candidate)
    result = evaluate_peer_candidate(target_evidence(), peer_input(identity), analysis_as_of=NOW)
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.UNVERIFIED


@pytest.mark.parametrize("security_type", ["ordinary share", "common stock", "common_share", "EQUITY"])
def test_verified_common_equity_permits_security_criterion_to_pass(security_type):
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(identity, security_type=security_type), analysis_as_of=NOW,
    )
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.VERIFIED_COMMON_EQUITY
    assert result.evidence.criterion(PeerCriterion.SECURITY_TYPE).status is PeerCriterionStatus.PASS
    assert result.status is PeerSelectionStatus.INCLUDED


@pytest.mark.parametrize("security_type", ["ETF", "fund", "warrant", "bond", "right"])
def test_verified_non_equity_is_excluded(security_type):
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(identity, security_type=security_type), analysis_as_of=NOW,
    )
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.VERIFIED_NON_EQUITY
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.WRONG_SECURITY_TYPE in result.reasons


def test_verified_preferred_equity_is_other_equity_and_excluded_by_policy():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    result = evaluate_peer_candidate(
        target_evidence(), peer_input(identity, security_type="preferred share"), analysis_as_of=NOW,
    )
    assert result.evidence.security_eligibility.status is PeerSecurityEligibilityStatus.VERIFIED_OTHER_EQUITY
    assert result.status is PeerSelectionStatus.EXCLUDED


@pytest.mark.parametrize("listing_symbol,exchange", [("OTHER", "NASDAQ"), ("SYN", "NYSE")])
def test_external_listing_incompatibility_blocks_security_enrichment(listing_symbol, exchange):
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    evidence = classify_peer_security_eligibility(
        identity,
        security_type="EQUITY",
        provenance=provenance(provider="yahoo", symbol=listing_symbol, dataset="yahoo_identity_metadata"),
        listing_symbol=listing_symbol,
        exchange=exchange,
    )
    assert evidence.status is PeerSecurityEligibilityStatus.UNVERIFIED
    assert (evidence.security_id, evidence.issuer_id) == (identity.security_id, identity.issuer_id)


@pytest.mark.parametrize("external_exchange", ["NMS", "NGM", "NCM", "XNAS"])
def test_documented_nasdaq_exchange_aliases_are_listing_compatible(external_exchange):
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    evidence = classify_peer_security_eligibility(
        identity,
        security_type="EQUITY",
        provenance=provenance(provider="yahoo", symbol="SYN", dataset="yahoo_identity_metadata"),
        listing_symbol="SYN",
        exchange=external_exchange,
    )
    assert evidence.status is PeerSecurityEligibilityStatus.VERIFIED_COMMON_EQUITY


def test_external_enrichment_cannot_redefine_canonical_identity():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    evidence = classify_peer_security_eligibility(
        identity,
        security_type="EQUITY",
        provenance=provenance(provider="yahoo", symbol="SYN", dataset="yahoo_identity_metadata"),
        listing_symbol="SYN",
        exchange="NASDAQ",
    )
    assert evidence.status is PeerSecurityEligibilityStatus.VERIFIED_COMMON_EQUITY
    assert evidence.issuer_id.startswith("issuer:fiscal:")
    assert evidence.security_id.startswith("security:fiscal:")


def test_mismatched_security_evidence_identity_binding_fails_closed():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    other = FiscalAdapter.build_summary_peer_identity(discovered(
        provider_symbol="OTHER", provider_issuer_id="OTHER-I", provider_security_id="OTHER-S",
    ))
    other_evidence = classify_peer_security_eligibility(
        other,
        security_type="common stock",
        provenance=provenance(symbol="OTHER"),
        listing_symbol="OTHER",
        exchange="NASDAQ",
    )
    with pytest.raises(ValueError, match="bound to the company identity"):
        PeerCompanyEvidence(identity=identity, security_eligibility=other_evidence)


def test_same_issuer_duplicate_listings_still_count_once():
    first = FiscalAdapter.build_summary_peer_identity(discovered())
    second = FiscalAdapter.build_summary_peer_identity(discovered(
        provider_symbol="SYN.A", provider_security_id="FSCLS-SYN-A",
    ))
    peer_set = select_peer_set(
        target_evidence(),
        (peer_input(first, security_type="common stock"), peer_input(second, security_type="common stock")),
        analysis_as_of=NOW,
    )
    assert len(peer_set.included_peer_issuer_ids) == 1
    assert any(PeerSelectionReason.DUPLICATE_ISSUER in item.reasons for item in peer_set.selections)


def test_economic_inclusion_is_independent_of_ev_ebitda_readiness():
    identity = FiscalAdapter.build_summary_peer_identity(discovered())
    item = peer_input(identity, security_type="common stock")
    selection = evaluate_peer_candidate(target_evidence(), item, analysis_as_of=NOW)
    readiness = assess_peer_method_data_readiness(item.candidate, item.company, analysis_as_of=NOW)
    assert selection.status is PeerSelectionStatus.INCLUDED
    assert readiness.status is PeerMethodDataStatus.UNAVAILABLE


def test_three_independent_included_issuers_remain_required_for_usable():
    inputs = []
    for index in range(3):
        identity = FiscalAdapter.build_summary_peer_identity(discovered(
            provider_symbol=f"SYN{index}",
            provider_issuer_id=f"FSCLC-{index}",
            provider_security_id=f"FSCLS-{index}",
        ))
        inputs.append(peer_input(identity, security_type="common stock"))
    assert select_peer_set(target_evidence(), inputs[:2], analysis_as_of=NOW).status is PeerSetStatus.PARTIAL
    assert select_peer_set(target_evidence(), inputs, analysis_as_of=NOW).status is PeerSetStatus.USABLE


def test_8a2_contracts_contain_no_valuation_or_ui_outputs():
    import stock_analyser.domain.peers as peer_domain
    import stock_analyser.services.peer_selection as peer_service

    forbidden_fields = {
        "peer_multiple", "fair_value", "current_price", "aggregation", "dcf", "stance",
        "own_history_distribution",
    }
    assert forbidden_fields.isdisjoint({field.name for field in fields(peer_domain.PeerSecurityEligibilityEvidence)})
    source = (inspect.getsource(peer_domain) + inspect.getsource(peer_service)).lower()
    assert "streamlit" not in source
    assert not any(f'== "{ticker.lower()}"' in source for ticker in ("META", "MSFT", "NVDA"))
