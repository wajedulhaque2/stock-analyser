from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import inspect

import pytest

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    CompanyIdentity,
    DataAvailability,
    EstimateCase,
    Frequency,
    IndustryComparability,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerCandidate,
    PeerCandidateSource,
    PeerCriterion,
    PeerCriterionStatus,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
    Provenance,
    ProviderSymbol,
    stable_observation_id,
    stable_peer_candidate_id,
)
from stock_analyser.providers import (
    FiscalAdapter, IdentityCandidate, ProviderError, ProviderErrorCategory, ProviderId,
    ProviderLookupStatus, ProviderPeerCandidate, ProviderPeerDiscoveryResult,
)
from stock_analyser.services import (
    PeerCandidateInput,
    PeerCompanyEvidence,
    evaluate_peer_candidate,
    select_peer_set,
)


NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)


def identity(
    symbol: str,
    issuer: str,
    *,
    security: str | None = None,
    sector: str = "Technology",
    industry: str = "Software",
    security_type: str = "Ordinary share",
    currency: str = "USD",
    availability: DataAvailability = DataAvailability.AVAILABLE,
    underlying: str | None = None,
    adr_ratio: float | None = None,
) -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=security or f"security-{symbol.lower()}",
        issuer_id=issuer,
        company_name=f"Synthetic {symbol} Company",
        issuer_domicile="US",
        listing_country="US",
        exchange="Synthetic Exchange",
        sector=sector,
        industry=industry,
        security_type=security_type,
        reporting_currency=currency,
        quote_currency=currency,
        quote_unit=currency,
        price_scale=1,
        fiscal_year_end="12-31",
        provider_symbols=(ProviderSymbol("fiscal", f"F-{symbol}"), ProviderSymbol("fmp", f"M-{symbol}")),
        identity_availability=availability,
        underlying_security_id=underlying,
        adr_ratio=adr_ratio,
    )


TARGET = identity("TGT", "issuer-target")


def observation(
    symbol: str,
    metric: MetricId,
    value: float,
    year: int,
    *,
    currency: str = "USD",
    estimate: bool = False,
    as_of: datetime = NOW,
) -> MetricObservation:
    case = EstimateCase.AVERAGE if estimate else EstimateCase.NOT_APPLICABLE
    kind = ObservationType.ESTIMATE if estimate else ObservationType.ACTUAL
    provider = "fmp" if estimate else "fiscal"
    provider_symbol = f"{'M' if estimate else 'F'}-{symbol}"
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{kind.value}",
        provider_symbol=provider_symbol,
        retrieved_at=NOW,
        as_of_at=as_of,
        source_metric=metric.value,
    )
    start, end = date(year, 1, 1), date(year, 12, 31)
    return MetricObservation(
        observation_id=stable_observation_id(
            metric_id=metric.value,
            provider=provider,
            provider_symbol=provider_symbol,
            frequency=Frequency.ANNUAL.value,
            observation_type=kind.value,
            estimate_case=case.value,
            period_start=str(start),
            period_end=str(end),
            as_of_at=as_of.isoformat(),
        ),
        metric_id=metric,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency=currency,
        frequency=Frequency.ANNUAL,
        observation_type=kind,
        estimate_case=case,
        retrieved_at=NOW,
        as_of_at=as_of,
        period_start=start,
        period_end=end,
        fiscal_year=year,
        provenance=provenance,
    )


def financials(symbol: str, *, currency: str = "USD", scale: float = 1.0, growth: float = 0.10, margin: float = 0.30):
    prior_revenue = 100.0 * scale
    current_revenue = prior_revenue * (1 + growth)
    return (
        observation(symbol, MetricId.REVENUE, prior_revenue, 2024, currency=currency),
        observation(symbol, MetricId.REVENUE, current_revenue, 2025, currency=currency),
        observation(symbol, MetricId.EBITDA, current_revenue * margin, 2025, currency=currency),
    )


def company_evidence(
    company: CompanyIdentity,
    *,
    actuals=None,
    forward: bool = True,
    business_model: str | None = None,
) -> PeerCompanyEvidence:
    provenance = None
    if business_model is not None:
        provenance = Provenance(
            provider="fiscal", endpoint_or_dataset="synthetic_business_model",
            provider_symbol=f"F-{company.canonical_symbol}", retrieved_at=NOW,
            as_of_at=NOW, source_metric="business_model",
        )
    return PeerCompanyEvidence(
        identity=company,
        actual_observations=tuple(financials(company.canonical_symbol, currency=company.reporting_currency) if actuals is None else actuals),
        forward_observations=(
            observation(company.canonical_symbol, MetricId.EBITDA, 50, 2027, currency=company.reporting_currency, estimate=True),
        ) if forward else (),
        business_model=business_model,
        business_model_provenance=provenance,
    )


def candidate(
    company: CompanyIdentity,
    *,
    provider: str = "fiscal",
    provider_symbol: str | None = None,
    rank: int | None = 1,
) -> PeerCandidate:
    source = PeerCandidateSource.PROVIDER_PROFILE_PEERS
    symbol = provider_symbol or f"{provider.upper()}-{company.canonical_symbol}"
    provenance = Provenance(
        provider=provider,
        endpoint_or_dataset="synthetic_peer_candidates",
        provider_symbol=symbol,
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric="peers",
    )
    candidate_id = stable_peer_candidate_id(
        target_security_id=TARGET.security_id,
        candidate_security_id=company.security_id,
        provider=provider,
        provider_symbol=symbol,
        candidate_source=source,
    )
    return PeerCandidate(
        candidate_id=candidate_id,
        target_security_id=TARGET.security_id,
        target_issuer_id=TARGET.issuer_id,
        candidate_security_id=company.security_id,
        candidate_issuer_id=company.issuer_id,
        provider=provider,
        provider_symbol=symbol,
        canonical_symbol=company.canonical_symbol,
        candidate_source=source,
        source_rank=rank,
        source_as_of=NOW,
        retrieved_at=NOW,
        provenance=provenance,
    )


def peer_input(company: CompanyIdentity, **kwargs) -> PeerCandidateInput:
    evidence_keys = {key: kwargs.pop(key) for key in tuple(kwargs) if key in {"actuals", "forward", "business_model"}}
    return PeerCandidateInput(candidate(company, **kwargs), company_evidence(company, **evidence_keys))


def evaluate(item: PeerCandidateInput, *, target=None):
    return evaluate_peer_candidate(
        target or company_evidence(TARGET), item, analysis_as_of=NOW,
    )


def test_target_cannot_be_its_own_peer():
    same_issuer = identity("TGT2", TARGET.issuer_id, security="security-secondary")
    result = evaluate(peer_input(same_issuer))
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.SELF_REFERENCE in result.reasons


def test_same_issuer_through_another_listing_cannot_double_count():
    first = identity("ONE", "issuer-one", security="security-one-primary")
    second = identity("ONEA", "issuer-one", security="security-one-secondary")
    peer_set = select_peer_set(
        company_evidence(TARGET), (peer_input(first, rank=1), peer_input(second, rank=2)),
        analysis_as_of=NOW,
    )
    assert len(peer_set.included_peer_issuer_ids) == 1
    assert any(PeerSelectionReason.DUPLICATE_ISSUER in item.reasons for item in peer_set.selections)


def test_canonical_issuer_identity_controls_duplicate_detection_not_symbol_text():
    one = identity("ALPHA", "issuer-shared")
    two = identity("COMPLETELY-DIFFERENT", "issuer-shared", security="security-other")
    peer_set = select_peer_set(
        company_evidence(TARGET), (peer_input(one), peer_input(two, provider="fmp")),
        analysis_as_of=NOW,
    )
    assert len(peer_set.included_peer_issuer_ids) == 1


def test_provider_symbol_is_provenance_only():
    item = peer_input(identity("ONE", "issuer-one"), provider_symbol="PROVIDER-ONLY")
    result = evaluate(item)
    assert result.status is PeerSelectionStatus.INCLUDED
    assert result.candidate.provider_symbol == "PROVIDER-ONLY"
    assert result.candidate.candidate_issuer_id == "issuer-one"


def test_different_provider_symbols_resolve_to_one_canonical_peer():
    company = identity("ONE", "issuer-one")
    fiscal = peer_input(company, provider="fiscal", provider_symbol="FISCAL-ONE")
    fmp = peer_input(company, provider="fmp", provider_symbol="FMP-ALT")
    peer_set = select_peer_set(company_evidence(TARGET), (fiscal, fmp), analysis_as_of=NOW)
    included = next(item for item in peer_set.selections if item.status is PeerSelectionStatus.INCLUDED)
    assert included.evidence.provider_agreement == ("fiscal", "fmp")
    assert len(peer_set.included_peer_issuer_ids) == 1


@pytest.mark.parametrize("security_type", ["ETF", "Fund", "Preferred share", "Warrant", "Bond"])
def test_non_equity_candidate_is_excluded(security_type):
    result = evaluate(peer_input(identity("BAD", "issuer-bad", security_type=security_type)))
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.WRONG_SECURITY_TYPE in result.reasons


def test_same_industry_evidence_supports_inclusion():
    result = evaluate(peer_input(identity("ONE", "issuer-one", industry=TARGET.industry)))
    assert result.status is PeerSelectionStatus.INCLUDED
    assert result.evidence.industry_comparability is IndustryComparability.SAME_INDUSTRY


def test_sector_only_does_not_become_strongest_industry_match():
    result = evaluate(peer_input(identity("ONE", "issuer-one", industry="Semiconductors")))
    assert result.status is PeerSelectionStatus.UNVERIFIED
    assert result.evidence.industry_comparability is IndustryComparability.SECTOR_ONLY
    assert PeerSelectionReason.SECTOR_ONLY in result.reasons


def test_industry_mismatch_excludes():
    result = evaluate(peer_input(identity("ONE", "issuer-one", sector="Energy", industry="Integrated Energy")))
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.INDUSTRY_MISMATCH in result.reasons


def test_unknown_industry_is_unverified_not_fabricated():
    result = evaluate(peer_input(identity("ONE", "issuer-one", industry="Unknown")))
    assert result.status is PeerSelectionStatus.UNVERIFIED
    assert result.evidence.industry_comparability is IndustryComparability.UNRESOLVED


def test_business_model_is_not_invented_from_company_name():
    result = evaluate(peer_input(identity("SOFTWARE-NAMED", "issuer-one")))
    criterion = result.evidence.criterion(PeerCriterion.BUSINESS_MODEL)
    assert criterion.status is PeerCriterionStatus.UNRESOLVED
    assert criterion.candidate_value is None


def test_explicit_business_model_mismatch_excludes():
    target = company_evidence(TARGET, business_model="subscription software")
    item = peer_input(identity("ONE", "issuer-one"), business_model="semiconductor fabrication")
    result = evaluate(item, target=target)
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert PeerSelectionReason.BUSINESS_MODEL_MISMATCH in result.reasons


def test_same_currency_scale_comparison_is_evaluated():
    result = evaluate(peer_input(identity("ONE", "issuer-one")))
    scale = result.evidence.criterion(PeerCriterion.SCALE)
    assert scale.status is PeerCriterionStatus.PASS
    assert scale.currency == "USD" and scale.comparison_value == pytest.approx(1)


def test_cross_currency_absolute_scale_is_unresolved_without_fx():
    gbp = identity("ONE", "issuer-one", currency="GBP")
    result = evaluate(peer_input(gbp))
    scale = result.evidence.criterion(PeerCriterion.SCALE)
    assert scale.status is PeerCriterionStatus.UNRESOLVED
    assert scale.comparison_value is None and scale.currency is None
    assert "no FX" in scale.note


def test_dimensionless_margin_comparison_operates_cross_currency():
    gbp = identity("ONE", "issuer-one", currency="GBP")
    result = evaluate(peer_input(gbp))
    assert result.evidence.criterion(PeerCriterion.MARGIN).status is PeerCriterionStatus.PASS


def test_dimensionless_growth_comparison_operates_cross_currency():
    gbp = identity("ONE", "issuer-one", currency="GBP")
    result = evaluate(peer_input(gbp))
    assert result.evidence.criterion(PeerCriterion.GROWTH).status is PeerCriterionStatus.PASS


def test_missing_target_metric_does_not_become_zero():
    result = evaluate(peer_input(identity("ONE", "issuer-one")), target=company_evidence(TARGET, actuals=()))
    scale = result.evidence.criterion(PeerCriterion.SCALE)
    assert scale.target_value is None and scale.status is PeerCriterionStatus.UNRESOLVED


def test_missing_candidate_metric_does_not_become_zero():
    result = evaluate(peer_input(identity("ONE", "issuer-one"), actuals=()))
    scale = result.evidence.criterion(PeerCriterion.SCALE)
    assert scale.candidate_value is None and scale.status is PeerCriterionStatus.UNRESOLVED


def test_missing_forward_ebitda_does_not_change_economic_inclusion():
    result = evaluate(peer_input(identity("ONE", "issuer-one"), forward=False))
    assert result.status is PeerSelectionStatus.INCLUDED
    assert PeerSelectionReason.FORWARD_DATA_UNAVAILABLE not in result.reasons
    assert all(item.criterion is not PeerCriterion.FORWARD_EBITDA for item in result.evidence.criteria)


def test_provider_rank_is_provenance_not_quality_score():
    low_rank = evaluate(peer_input(identity("ONE", "issuer-one"), rank=1))
    high_rank = evaluate(peer_input(identity("TWO", "issuer-two"), rank=99))
    assert low_rank.status is high_rank.status is PeerSelectionStatus.INCLUDED
    assert not hasattr(low_rank.evidence, "peer_score")


def test_no_weighted_peer_score_contract_exists():
    import stock_analyser.domain.peers as peers
    import stock_analyser.services.peer_selection as service
    source = (inspect.getsource(peers) + inspect.getsource(service)).lower()
    assert "weighted" not in source and "peer_score" not in source


def test_three_independent_included_issuers_make_set_usable():
    items = tuple(peer_input(identity(f"P{i}", f"issuer-{i}")) for i in range(3))
    peer_set = select_peer_set(company_evidence(TARGET), items, analysis_as_of=NOW)
    assert peer_set.status is PeerSetStatus.USABLE
    assert len(peer_set.included_peer_issuer_ids) == 3


def test_two_included_issuers_do_not_satisfy_minimum_three():
    items = tuple(peer_input(identity(f"P{i}", f"issuer-{i}")) for i in range(2))
    peer_set = select_peer_set(company_evidence(TARGET), items, analysis_as_of=NOW)
    assert peer_set.status is PeerSetStatus.PARTIAL
    assert peer_set.minimum_required_peers == 3


def test_duplicate_listing_cannot_be_used_to_reach_minimum():
    companies = (
        identity("A", "issuer-a"), identity("AA", "issuer-a", security="security-aa"),
        identity("B", "issuer-b"), identity("C", "issuer-c", sector="Energy", industry="Energy"),
    )
    peer_set = select_peer_set(
        company_evidence(TARGET), tuple(peer_input(item) for item in companies), analysis_as_of=NOW,
    )
    assert peer_set.status is PeerSetStatus.PARTIAL
    assert len(peer_set.included_peer_issuer_ids) == 2


def test_empty_available_provider_response_is_insufficient():
    capability = CapabilityResult("fiscal", "peer_candidates", CapabilityStatus.AVAILABLE, NOW)
    peer_set = select_peer_set(
        company_evidence(TARGET), (), analysis_as_of=NOW, provider_capabilities=(capability,),
    )
    assert peer_set.status is PeerSetStatus.INSUFFICIENT


def test_candidate_provider_failure_fails_closed_as_unavailable():
    capability = CapabilityResult(
        "fiscal", "peer_candidates", CapabilityStatus.ERROR, NOW,
        reason="synthetic safe provider failure",
    )
    peer_set = select_peer_set(
        company_evidence(TARGET), (), analysis_as_of=NOW, provider_capabilities=(capability,),
    )
    assert peer_set.status is PeerSetStatus.UNAVAILABLE


def test_provider_agreement_is_retained_as_audit_evidence():
    company = identity("ONE", "issuer-one")
    peer_set = select_peer_set(
        company_evidence(TARGET),
        (peer_input(company, provider="fiscal"), peer_input(company, provider="fmp")),
        analysis_as_of=NOW,
    )
    assert all(item.evidence.provider_agreement == ("fiscal", "fmp") for item in peer_set.selections)


def test_provider_agreement_does_not_automatically_include_candidate():
    company = identity("ONE", "issuer-one", sector="Energy", industry="Integrated Energy")
    peer_set = select_peer_set(
        company_evidence(TARGET),
        (peer_input(company, provider="fiscal"), peer_input(company, provider="fmp")),
        analysis_as_of=NOW,
    )
    assert not peer_set.included_peer_issuer_ids
    assert any(PeerSelectionReason.INDUSTRY_MISMATCH in item.reasons for item in peer_set.selections)


def test_candidate_evidence_preserves_analysis_as_of():
    result = evaluate(peer_input(identity("ONE", "issuer-one")))
    assert result.evidence.analysis_as_of == NOW


def test_future_financial_evidence_is_excluded():
    future_actual = observation("ONE", MetricId.REVENUE, 999, 2027)
    result = evaluate(peer_input(identity("ONE", "issuer-one"), actuals=(future_actual,)))
    assert result.evidence.criterion(PeerCriterion.SCALE).candidate_value is None


def test_adr_without_conversion_remains_unverified():
    adr = identity(
        "ADR", "issuer-adr", security_type="ADR",
        availability=DataAvailability.PARTIAL, underlying="underlying-security",
    )
    result = evaluate(peer_input(adr))
    assert result.status is PeerSelectionStatus.UNVERIFIED
    assert PeerSelectionReason.ADR_CONVERSION_UNRESOLVED in result.reasons


def test_scale_growth_and_margin_failures_have_controlled_reasons():
    target = company_evidence(TARGET)
    bad_actuals = financials("BAD", scale=20, growth=1.0, margin=0.90)
    result = evaluate(peer_input(identity("BAD", "issuer-bad"), actuals=bad_actuals), target=target)
    assert result.status is PeerSelectionStatus.EXCLUDED
    assert {
        PeerSelectionReason.SCALE_NOT_COMPARABLE,
        PeerSelectionReason.GROWTH_NOT_COMPARABLE,
        PeerSelectionReason.MARGIN_NOT_COMPARABLE,
    }.issubset(result.reasons)


def test_peer_contracts_contain_no_valuation_outputs():
    result = evaluate(peer_input(identity("ONE", "issuer-one")))
    peer_set = select_peer_set(company_evidence(TARGET), (peer_input(identity("ONE", "issuer-one")),), analysis_as_of=NOW)
    forbidden = {
        "valuation", "multiple", "median_multiple", "target_price", "fair_value",
        "upside", "downside", "premium", "discount", "stance", "aggregate_value",
    }
    assert forbidden.isdisjoint(result.__dataclass_fields__)
    assert forbidden.isdisjoint(peer_set.__dataclass_fields__)


def test_peer_service_does_not_consume_own_history_dcf_aggregation_price_or_ui():
    import stock_analyser.live_peer_audit as live
    import stock_analyser.services.peer_selection as service
    source = (inspect.getsource(service) + inspect.getsource(live)).lower()
    forbidden = (
        "own_history", "historicalmultipledistribution", "peer_median", "fair_value",
        "target_price", "reverse_dcf", "streamlit", "current_price", "fetch_market_snapshot",
        "investment_stance", "aggregationstatus",
    )
    assert all(value not in source for value in forbidden)


def test_no_ticker_specific_branch_or_production_peer_list_exists():
    import stock_analyser.live_peer_audit as live
    import stock_analyser.services.peer_selection as service
    source = (inspect.getsource(service) + inspect.getsource(live)).lower()
    assert all(f'== "{ticker.lower()}"' not in source for ticker in ("META", "MSFT", "NVDA", "SHEL.L", "RR.L"))
    assert "manual_peer" not in source and "curated_peer" not in source


class FiscalPeerSource:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or {"updatedAt": 1787832000, "peers": []}
        self.fail = fail

    def peer_candidates(self, symbol, *, credential=None):
        if self.fail:
            raise ProviderError(
                ProviderId.FISCAL, "fiscal:profile", ProviderErrorCategory.CONNECTION,
                retryable=False, safe_message="synthetic safe failure",
            )
        return self.payload


def fiscal_target() -> CompanyIdentity:
    return replace(TARGET, provider_symbols=(ProviderSymbol("fiscal", "F-TGT"),))


def peer_row(**changes):
    row = {
        "peerRelationship": "direct_competitor",
        "companyFiscalIdentifier": "FSCLC-SYN-ONE",
        "displayNameEnglish": "Synthetic One",
        "headquartersCountryCode": "US",
        "sector": "Information Technology",
        "industry": "Software",
        "primaryListing": {
            "ticker": "SYN1", "exchangeCode": "NASDAQ", "exchangeCountryCode": "US",
            "securityFiscalIdentifier": "FSCLS-SYN-ONE", "securityType": "common_stock",
        },
    }
    row.update(changes)
    return row


def test_fiscal_v3_profile_peer_schema_normalizes_without_raw_dto_leakage():
    source = FiscalPeerSource({"updatedAt": 1787832000, "peers": [peer_row()]})
    result = FiscalAdapter(source, clock=lambda: NOW).fetch_peer_candidates(fiscal_target())
    item = result.candidates[0]
    assert item.provider_issuer_id == "FSCLC-SYN-ONE"
    assert item.provider_security_id == "FSCLS-SYN-ONE"
    assert item.provider_symbol == "SYN1" and item.source_rank == 1
    assert item.source_as_of == datetime.fromtimestamp(1787832000, timezone.utc)
    assert result.capabilities[0].status is CapabilityStatus.AVAILABLE
    assert not hasattr(item, "peerReasoning") and not hasattr(item, "marketCapUsd")


def test_fiscal_peer_row_without_stable_identity_is_skipped():
    broken = peer_row(companyFiscalIdentifier=None)
    result = FiscalAdapter(
        FiscalPeerSource({"updatedAt": 1787832000, "peers": [broken]}), clock=lambda: NOW,
    ).fetch_peer_candidates(fiscal_target())
    assert not result.candidates
    assert result.capabilities[0].status is CapabilityStatus.UNAVAILABLE
    assert any("stable company/security identifier" in item.reason for item in result.issues)


def test_fiscal_missing_source_as_of_explicitly_uses_retrieval_time():
    result = FiscalAdapter(
        FiscalPeerSource({"updatedAt": None, "peers": [peer_row()]}), clock=lambda: NOW,
    ).fetch_peer_candidates(fiscal_target())
    assert result.candidates[0].source_as_of == NOW
    assert result.candidates[0].source_as_of_substituted


def test_fiscal_candidate_provider_failure_is_endpoint_isolated_and_safe():
    result = FiscalAdapter(FiscalPeerSource(fail=True), clock=lambda: NOW).fetch_peer_candidates(fiscal_target())
    assert not result.candidates
    assert result.capabilities[0].status is CapabilityStatus.ERROR
    assert "synthetic safe failure" not in repr(result.issues)


def test_live_audit_retains_summary_identity_and_bounded_yahoo_classification_attempt(monkeypatch):
    import stock_analyser.live_peer_audit as live
    from types import SimpleNamespace

    target_candidate = IdentityCandidate(
        provider=ProviderId.FISCAL, provider_symbol="META", retrieved_at=NOW,
        company_name="Synthetic Target", issuer_domicile="US", listing_country="US",
        exchange="NASDAQ", sector="Technology", industry="Software",
        security_type="Ordinary share", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1, fiscal_year_end="12-31",
        provider_issuer_id="FSCLC-TARGET", provider_security_id="FSCLS-TARGET",
    )
    discovered = ProviderPeerCandidate(
        provider=ProviderId.FISCAL, provider_symbol="700",
        provider_issuer_id="FSCLC-700", provider_security_id="FSCLS-700",
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_rank=1, source_as_of=NOW, retrieved_at=NOW,
        company_name="Synthetic 700", listing_country="HK", exchange="HKEX",
        sector="Technology", industry="Software", security_type="common_stock",
    )
    available = CapabilityResult("fiscal", "peer_candidates", CapabilityStatus.AVAILABLE, NOW)

    class FakeFiscal:
        def fetch_identity(self, symbol):
            if symbol == "META":
                return SimpleNamespace(candidate=target_candidate, issues=())
            return SimpleNamespace(candidate=None, issues=())

        def fetch_peer_candidates(self, target):
            return ProviderPeerDiscoveryResult((discovered,), (available,), ())

        def fetch_standardized_actuals(self, target):
            return SimpleNamespace(observations=(), issues=())

        def resolve_peer_financial_lookup(self, candidate):
            return SimpleNamespace(
                status=ProviderLookupStatus.UNAVAILABLE,
                source_dataset=None,
                reason="synthetic unavailable",
            )

    yahoo_calls = []

    class FakeYahoo:
        def fetch_identity(self, symbol):
            yahoo_calls.append(symbol)
            return SimpleNamespace(candidate=None, issues=())

    class FakeFmp:
        def fetch_annual_estimates(self, target, source_as_of_at=None):
            return SimpleNamespace(observations=(), issues=())

    monkeypatch.setattr(live, "LiveFiscalSource", lambda: object())
    monkeypatch.setattr(live, "FiscalAdapter", lambda *args, **kwargs: FakeFiscal())
    monkeypatch.setattr(live, "YahooAdapter", lambda *args, **kwargs: FakeYahoo())
    monkeypatch.setattr(live, "FmpAdapter", lambda *args, **kwargs: FakeFmp())
    monkeypatch.setattr(live, "build_live_transport", lambda: object())

    outcome = live.run_live_peer_audit(
        "META", environment={"FISCAL_API_KEY": "synthetic", "FMP_API_KEY": "synthetic"},
        analysis_as_of=NOW,
    )
    assert outcome.succeeded
    assert yahoo_calls == ["META", "700"]
    assert not outcome.unresolved_candidates
    assert outcome.resolved_candidate_count == 1
    assert outcome.peer_set.selections[0].evidence.identity.status.value == "summary_verified"


def test_live_audit_full_profile_stable_id_conflict_fails_identity_closed(monkeypatch):
    import stock_analyser.live_peer_audit as live
    from types import SimpleNamespace

    target_candidate = IdentityCandidate(
        provider=ProviderId.FISCAL, provider_symbol="META", retrieved_at=NOW,
        company_name="Synthetic Target", issuer_domicile="US", listing_country="US",
        exchange="NASDAQ", sector="Technology", industry="Software",
        security_type="Ordinary share", reporting_currency="USD", quote_currency="USD",
        quote_unit="USD", price_scale=1, fiscal_year_end="12-31",
        provider_issuer_id="FSCLC-TARGET", provider_security_id="FSCLS-TARGET",
    )
    discovered = ProviderPeerCandidate(
        provider=ProviderId.FISCAL, provider_symbol="SYN",
        provider_issuer_id="FSCLC-SUMMARY", provider_security_id="FSCLS-SUMMARY",
        candidate_source=PeerCandidateSource.PROVIDER_PROFILE_PEERS,
        source_rank=1, source_as_of=NOW, retrieved_at=NOW,
        company_name="Synthetic Peer", listing_country="US", exchange="NASDAQ",
        sector="Technology", industry="Software", security_type=None,
    )
    conflicting_profile = replace(
        target_candidate,
        provider_symbol="SYN",
        provider_issuer_id="FSCLC-CONFLICT",
        provider_security_id="FSCLS-CONFLICT",
    )
    available = CapabilityResult("fiscal", "peer_candidates", CapabilityStatus.AVAILABLE, NOW)

    class FakeFiscal:
        def fetch_identity(self, symbol):
            candidate = target_candidate if symbol == "META" else conflicting_profile
            return SimpleNamespace(candidate=candidate, issues=())

        def fetch_peer_candidates(self, target):
            return ProviderPeerDiscoveryResult((discovered,), (available,), ())

        def fetch_standardized_actuals(self, target):
            return SimpleNamespace(observations=(), issues=())

    class FakeYahoo:
        def fetch_identity(self, symbol):
            return SimpleNamespace(candidate=None, issues=())

    class FakeFmp:
        def fetch_annual_estimates(self, target, source_as_of_at=None):
            return SimpleNamespace(observations=(), issues=())

    monkeypatch.setattr(live, "LiveFiscalSource", lambda: object())
    monkeypatch.setattr(live, "FiscalAdapter", lambda *args, **kwargs: FakeFiscal())
    monkeypatch.setattr(live, "YahooAdapter", lambda *args, **kwargs: FakeYahoo())
    monkeypatch.setattr(live, "FmpAdapter", lambda *args, **kwargs: FakeFmp())
    monkeypatch.setattr(live, "build_live_transport", lambda: object())

    outcome = live.run_live_peer_audit(
        "META", environment={"FISCAL_API_KEY": "synthetic", "FMP_API_KEY": "synthetic"},
        analysis_as_of=NOW,
    )
    assert outcome.succeeded
    assert outcome.resolved_candidate_count == 0
    assert len(outcome.unresolved_candidates) == 1
    assert "do not match" in outcome.unresolved_candidates[0].reason


def test_live_audit_suppresses_third_party_yahoo_console_payloads(monkeypatch, capsys):
    import stock_analyser.live_peer_audit as live
    from types import SimpleNamespace

    class NoisyYahoo:
        def fetch_identity(self, symbol):
            print('HTTP Error 404: {"proprietary":"payload"}')
            return SimpleNamespace(candidate=None, issues=())

    result = live._fetch_yahoo_identity_safely(NoisyYahoo(), "SYN")
    captured = capsys.readouterr()
    assert result.candidate is None
    assert captured.out == "" and captured.err == ""
