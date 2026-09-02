from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import inspect
import math
import re
import socket

import pytest

from stock_analyser.domain import (
    CompanyIdentity,
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerCandidate,
    PeerCandidateSource,
    PeerIdentityBindingBasis,
    PeerMethodEvidenceReason,
    PeerMethodEvidenceStatus,
    PeerMetricEvidence,
    PeerProviderIdentityBinding,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
    PeerValuationSubsetStatus,
    Provenance,
    ProviderSymbol,
    stable_observation_id,
    stable_peer_candidate_id,
    stable_peer_identity_binding_id,
)
from stock_analyser.services import (
    PeerCandidateInput,
    PeerCompanyEvidence,
    PeerMethodDataInput,
    PeerValuationPolicy,
    assess_peer_method_data,
    build_peer_valuation_subset,
    evaluate_peer_candidate,
    select_peer_set,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def identity(symbol: str, issuer: str, *, security: str | None = None, industry: str = "Software", fye: str = "12-31"):
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=security or f"security-{symbol.lower()}",
        issuer_id=issuer,
        company_name=f"Synthetic {symbol}",
        issuer_domicile="US",
        listing_country="US",
        exchange="Synthetic Exchange",
        sector="Technology",
        industry=industry,
        security_type="Ordinary share",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        fiscal_year_end=fye,
        provider_symbols=(ProviderSymbol("fiscal", f"F-{symbol}"),),
    )


TARGET = identity("TGT", "issuer-target")


def provenance(provider: str, symbol: str, as_of: datetime, *, source_metric: str) -> Provenance:
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_8b",
        provider_symbol=symbol,
        retrieved_at=as_of,
        as_of_at=as_of,
        source_metric=source_metric,
    )


def annual_actual(symbol: str, metric: MetricId, value: float, year: int) -> MetricObservation:
    as_of = NOW - timedelta(days=30)
    start, end = date(year, 1, 1), date(year, 12, 31)
    prov = provenance("fiscal", f"F-{symbol}", as_of, source_metric=metric.value)
    return MetricObservation(
        observation_id=stable_observation_id(
            metric_id=metric.value, provider="fiscal", provider_symbol=f"F-{symbol}",
            frequency=Frequency.ANNUAL.value, observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value, period_start=str(start),
            period_end=str(end), as_of_at=as_of.isoformat(),
        ),
        metric_id=metric,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency="USD",
        frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=as_of,
        as_of_at=as_of,
        period_start=start,
        period_end=end,
        fiscal_year=year,
        provenance=prov,
    )


def company_evidence(company: CompanyIdentity) -> PeerCompanyEvidence:
    return PeerCompanyEvidence(
        identity=company,
        actual_observations=(
            annual_actual(company.canonical_symbol, MetricId.REVENUE, 100, 2024),
            annual_actual(company.canonical_symbol, MetricId.REVENUE, 110, 2025),
            annual_actual(company.canonical_symbol, MetricId.EBITDA, 33, 2025),
        ),
    )


def peer_input(company: CompanyIdentity, *, rank: int = 1) -> PeerCandidateInput:
    symbol = f"F-{company.canonical_symbol}"
    prov = provenance("fiscal", symbol, NOW, source_metric="peers")
    source = PeerCandidateSource.PROVIDER_PROFILE_PEERS
    candidate = PeerCandidate(
        candidate_id=stable_peer_candidate_id(
            target_security_id=TARGET.security_id,
            candidate_security_id=company.security_id,
            provider="fiscal",
            provider_symbol=symbol,
            candidate_source=source,
        ),
        target_security_id=TARGET.security_id,
        target_issuer_id=TARGET.issuer_id,
        candidate_security_id=company.security_id,
        candidate_issuer_id=company.issuer_id,
        provider="fiscal",
        provider_symbol=symbol,
        canonical_symbol=company.canonical_symbol,
        candidate_source=source,
        source_as_of=NOW,
        retrieved_at=NOW,
        provenance=prov,
        source_rank=rank,
    )
    return PeerCandidateInput(candidate, company_evidence(company))


def selections(count: int = 3):
    inputs = tuple(peer_input(identity(f"P{i}", f"issuer-{i}")) for i in range(count))
    peer_set = select_peer_set(company_evidence(TARGET), inputs, analysis_as_of=NOW)
    return peer_set, peer_set.selections


def point_in_time(
    selection,
    value: float = 1000,
    *,
    days_old: int = 1,
    currency: str = "USD",
    metric: MetricId = MetricId.ENTERPRISE_VALUE,
    unit: MetricUnit = MetricUnit.CURRENCY,
    security_id: str | None = None,
    issuer_id: str | None = None,
) -> PeerMetricEvidence:
    observed = NOW.date() - timedelta(days=days_old)
    as_of = datetime.combine(observed, time(12), tzinfo=timezone.utc)
    symbol = selection.candidate.provider_symbol
    prov = provenance("fiscal", symbol, as_of, source_metric="calculated_tev")
    observation = MetricObservation(
        observation_id=stable_observation_id(
            metric_id=metric.value, provider="fiscal", provider_symbol=symbol,
            frequency=Frequency.POINT_IN_TIME.value, observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value, period_start="", period_end=str(observed),
            as_of_at=as_of.isoformat(),
        ),
        metric_id=metric,
        value=value,
        unit=unit,
        currency=currency if unit is MetricUnit.CURRENCY else None,
        frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=as_of,
        as_of_at=as_of,
        period_end=observed,
        provenance=prov,
    )
    return PeerMetricEvidence(
        peer_security_id=security_id or selection.candidate.candidate_security_id,
        peer_issuer_id=issuer_id or selection.candidate.candidate_issuer_id,
        observation=observation,
    )


def forward(
    selection,
    value: float = 100,
    *,
    year: int = 2027,
    month: int = 12,
    day: int = 31,
    currency: str = "USD",
    estimate_case: EstimateCase = EstimateCase.AVERAGE,
    frequency: Frequency = Frequency.ANNUAL,
    as_of: datetime | None = None,
    provider_symbol: str | None = None,
    security_id: str | None = None,
    issuer_id: str | None = None,
) -> PeerMetricEvidence:
    snapshot = as_of or NOW - timedelta(days=1)
    symbol = provider_symbol or f"FMP-{selection.candidate.canonical_symbol}-STABLE"
    end = date(year, month, day)
    start = date(year - 1, month, day) + timedelta(days=1)
    prov = provenance("fmp", symbol, snapshot, source_metric=f"ebitda{estimate_case.value.title()}")
    observation = MetricObservation(
        observation_id=stable_observation_id(
            metric_id=MetricId.EBITDA.value, provider="fmp", provider_symbol=symbol,
            frequency=frequency.value, observation_type=ObservationType.ESTIMATE.value,
            estimate_case=estimate_case.value, period_start=str(start), period_end=str(end),
            as_of_at=snapshot.isoformat(),
        ),
        metric_id=MetricId.EBITDA,
        value=value,
        unit=MetricUnit.CURRENCY,
        currency=currency,
        frequency=frequency,
        observation_type=ObservationType.ESTIMATE,
        estimate_case=estimate_case,
        retrieved_at=snapshot,
        as_of_at=snapshot,
        period_start=start,
        period_end=end,
        fiscal_year=year if frequency is Frequency.ANNUAL else None,
        provenance=prov,
    )
    return PeerMetricEvidence(
        peer_security_id=security_id or selection.candidate.candidate_security_id,
        peer_issuer_id=issuer_id or selection.candidate.candidate_issuer_id,
        observation=observation,
    )


def binding(selection, *, provider_symbol: str | None = None) -> PeerProviderIdentityBinding:
    symbol = provider_symbol or f"FMP-{selection.candidate.canonical_symbol}-STABLE"
    as_of = NOW - timedelta(days=2)
    identifier = str(100000 + int(selection.candidate.canonical_symbol[1:]))
    prov = provenance("fmp", symbol, as_of, source_metric="cik")
    return PeerProviderIdentityBinding(
        binding_id=stable_peer_identity_binding_id(
            selection.candidate.candidate_issuer_id, "fmp", "cik", identifier,
        ),
        peer_security_id=selection.candidate.candidate_security_id,
        peer_issuer_id=selection.candidate.candidate_issuer_id,
        provider="fmp",
        provider_symbol=symbol,
        identifier_basis=PeerIdentityBindingBasis.CIK,
        canonical_identifier=identifier,
        provider_identifier=f"000{identifier}",
        verified_at=as_of,
        provenance=prov,
    )


def method_input(
    selection,
    *,
    ev=(),
    estimates=(),
    identity_binding=None,
    fye: str = "12-31",
) -> PeerMethodDataInput:
    return PeerMethodDataInput(
        candidate_id=selection.candidate.candidate_id,
        peer_security_id=selection.candidate.candidate_security_id,
        peer_issuer_id=selection.candidate.candidate_issuer_id,
        peer_fiscal_year_end=fye,
        enterprise_value_evidence=tuple(ev) or (point_in_time(selection),),
        forward_ebitda_evidence=tuple(estimates) or (forward(selection),),
        forward_identity_binding=identity_binding or binding(selection),
    )


def assess(selection, item=None, *, period=ForwardPeriodSelection.FY1, policy=PeerValuationPolicy()):
    return assess_peer_method_data(
        TARGET.security_id, TARGET.issuer_id, selection,
        item if item is not None else method_input(selection),
        analysis_as_of=NOW, peer_set_id="peer-set:synthetic-direct",
        forward_period=period, policy=policy,
    )


@pytest.mark.parametrize(
    "status,reason",
    (
        (PeerSelectionStatus.UNVERIFIED, PeerSelectionReason.INSUFFICIENT_FINANCIAL_DATA),
        (PeerSelectionStatus.EXCLUDED, PeerSelectionReason.INDUSTRY_MISMATCH),
        (PeerSelectionStatus.UNAVAILABLE, PeerSelectionReason.PROVIDER_UNAVAILABLE),
    ),
)
def test_only_included_economic_peers_can_construct_observations(status, reason):
    _, (included, *_) = selections()
    rejected = replace(included, status=status, reasons=(reason,))
    evidence, observation = assess(rejected)
    assert observation is None
    assert evidence.economic_selection_status is status
    assert evidence.reasons == (PeerMethodEvidenceReason.ECONOMIC_MEMBERSHIP_REQUIRED,)


def test_peer_identity_security_and_selection_reference_are_preserved():
    _, (selection, *_) = selections()
    evidence, observation = assess(selection)
    assert evidence.status is PeerMethodEvidenceStatus.AVAILABLE
    assert observation.peer_issuer_id == selection.candidate.candidate_issuer_id
    assert observation.peer_security_id == selection.candidate.candidate_security_id
    assert observation.peer_selection_result_id == evidence.peer_selection_result_id


def test_provider_symbol_is_provenance_only_after_stable_identity_binding():
    _, (selection, *_) = selections()
    odd_symbol = "UNRELATED-PROVIDER-LOOKUP-TEXT"
    estimate = forward(selection, provider_symbol=odd_symbol)
    item = method_input(
        selection, estimates=(estimate,), identity_binding=binding(selection, provider_symbol=odd_symbol),
    )
    _, observation = assess(selection, item)
    assert observation is not None
    assert observation.peer_issuer_id == selection.candidate.candidate_issuer_id
    assert any(item.provider_symbol == odd_symbol for item in observation.provenance)


def test_multiple_is_peer_tev_divided_by_peer_forward_ebitda_and_is_dimensionless():
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, ev=(point_in_time(selection, 1250),), estimates=(forward(selection, 125),),
    ))
    assert observation.enterprise_value == 1250
    assert observation.forward_ebitda == 125
    assert observation.ev_ebitda_multiple == 10
    assert observation.unit is MetricUnit.RATIO
    with pytest.raises(ValueError, match="positive EV and EBITDA"):
        replace(evidence, enterprise_value=-1)


@pytest.mark.parametrize("target_component", ["tev", "ebitda"])
def test_target_company_inputs_cannot_be_used_for_peer_multiple(target_component):
    _, (selection, *_) = selections()
    if target_component == "tev":
        ev = point_in_time(
            selection, security_id=TARGET.security_id, issuer_id=TARGET.issuer_id,
        )
        item = method_input(selection, ev=(ev,))
    else:
        estimate = forward(
            selection, security_id=TARGET.security_id, issuer_id=TARGET.issuer_id,
        )
        item = method_input(selection, estimates=(estimate,))
    evidence, observation = assess(selection, item)
    assert observation is None
    assert PeerMethodEvidenceReason.IDENTITY_MISMATCH in evidence.reasons


def test_latest_current_peer_tev_on_or_before_analysis_as_of_is_selected():
    _, (selection, *_) = selections()
    old = point_in_time(selection, 900, days_old=5)
    latest = point_in_time(selection, 1100, days_old=1)
    evidence, _ = assess(selection, method_input(selection, ev=(old, latest)))
    assert evidence.enterprise_value == 1100
    assert evidence.enterprise_value_date == NOW.date() - timedelta(days=1)


def test_future_tev_is_rejected():
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, ev=(point_in_time(selection, days_old=-1),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.ENTERPRISE_VALUE_FUTURE in evidence.reasons


def test_centralized_enterprise_value_staleness_policy_is_enforced():
    _, (selection, *_) = selections()
    policy = PeerValuationPolicy(maximum_enterprise_value_age_days=2)
    evidence, observation = assess(
        selection,
        method_input(selection, ev=(point_in_time(selection, days_old=3),)),
        policy=policy,
    )
    assert observation is None
    assert PeerMethodEvidenceReason.ENTERPRISE_VALUE_STALE in evidence.reasons


def test_future_fmp_estimate_snapshot_is_rejected():
    _, (selection, *_) = selections()
    estimate = forward(selection, as_of=NOW + timedelta(seconds=1))
    evidence, observation = assess(selection, method_input(selection, estimates=(estimate,)))
    assert observation is None
    assert PeerMethodEvidenceReason.FORWARD_ESTIMATE_FUTURE in evidence.reasons


def test_fy1_uses_each_peers_own_fiscal_calendar_and_is_not_ntm():
    company = identity("P9", "issuer-9", fye="06-30")
    peer_set = select_peer_set(company_evidence(TARGET), (peer_input(company),), analysis_as_of=NOW)
    selection = peer_set.selections[0]
    ntm = forward(selection, 999, year=2027, month=6, day=30, frequency=Frequency.NTM)
    fy1 = forward(selection, 80, year=2027, month=6, day=30)
    fy2 = forward(selection, 90, year=2028, month=6, day=30)
    _, observation = assess(selection, method_input(
        selection, estimates=(ntm, fy2, fy1), fye="06-30",
    ))
    assert observation.forward_period is ForwardPeriodSelection.FY1
    assert observation.forward_period_end == date(2027, 6, 30)
    assert observation.forward_ebitda == 80


def test_fy2_may_be_selected_explicitly_without_averaging_periods():
    _, (selection, *_) = selections()
    fy1 = forward(selection, 100, year=2027)
    fy2 = forward(selection, 140, year=2028)
    _, observation = assess(
        selection, method_input(selection, estimates=(fy1, fy2)),
        period=ForwardPeriodSelection.FY2,
    )
    assert observation.forward_period is ForwardPeriodSelection.FY2
    assert observation.forward_period_end == date(2028, 12, 31)
    assert observation.forward_ebitda == 140


@pytest.mark.parametrize("case", [EstimateCase.LOW, EstimateCase.HIGH])
def test_low_or_high_is_not_substituted_for_average(case):
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, estimates=(forward(selection, estimate_case=case),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.FORWARD_ESTIMATE_CASE_UNAVAILABLE in evidence.reasons


def test_primary_policy_rejects_non_average_case():
    with pytest.raises(ValueError, match="AVERAGE"):
        PeerValuationPolicy(estimate_case=EstimateCase.LOW)


def test_currency_mismatch_withholds_multiple_and_performs_no_fx():
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection,
        ev=(point_in_time(selection, currency="USD"),),
        estimates=(forward(selection, currency="EUR"),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.CURRENCY_MISMATCH in evidence.reasons
    assert evidence.enterprise_value == 1000
    assert evidence.forward_ebitda is None


def test_non_currency_inputs_are_not_scaled_or_compensated_in_service():
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, ev=(point_in_time(selection, unit=MetricUnit.COUNT),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.UNIT_MISMATCH in evidence.reasons


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_forward_ebitda_is_rejected(value):
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, estimates=(forward(selection, value),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.FORWARD_EBITDA_INVALID in evidence.reasons


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_enterprise_value_is_rejected(value):
    _, (selection, *_) = selections()
    evidence, observation = assess(selection, method_input(
        selection, ev=(point_in_time(selection, value),),
    ))
    assert observation is None
    assert PeerMethodEvidenceReason.ENTERPRISE_VALUE_INVALID in evidence.reasons


@pytest.mark.parametrize("metric", ["tev", "ebitda"])
def test_non_finite_canonical_inputs_fail_at_domain_boundary(metric):
    _, (selection, *_) = selections()
    with pytest.raises(ValueError, match="finite"):
        if metric == "tev":
            point_in_time(selection, math.inf)
        else:
            forward(selection, math.nan)


def test_high_finite_positive_multiple_is_retained_without_clipping():
    _, (selection, *_) = selections()
    _, observation = assess(selection, method_input(
        selection,
        ev=(point_in_time(selection, 1_000_000_000_000),),
        estimates=(forward(selection, 1),),
    ))
    assert observation.ev_ebitda_multiple == 1_000_000_000_000


def test_three_independent_peer_observations_make_subset_usable():
    peer_set, selected = selections(3)
    subset = build_peer_valuation_subset(peer_set, tuple(method_input(item) for item in selected))
    assert peer_set.status is PeerSetStatus.USABLE
    assert subset.status is PeerValuationSubsetStatus.USABLE
    assert subset.valid_observation_count == 3
    assert len({item.peer_issuer_id for item in subset.observations}) == 3


def test_two_observations_do_not_satisfy_minimum_three():
    peer_set, selected = selections(2)
    subset = build_peer_valuation_subset(peer_set, tuple(method_input(item) for item in selected))
    assert peer_set.status is PeerSetStatus.PARTIAL
    assert subset.status is PeerValuationSubsetStatus.INSUFFICIENT
    assert subset.valid_observation_count == 2
    assert subset.minimum_required_valid_observations == 3


def test_usable_economic_peer_set_can_have_partial_method_subset():
    peer_set, selected = selections(3)
    subset = build_peer_valuation_subset(peer_set, tuple(method_input(item) for item in selected[:2]))
    assert peer_set.status is PeerSetStatus.USABLE
    assert subset.status is PeerValuationSubsetStatus.PARTIAL
    assert subset.valid_observation_count == 2
    assert len(subset.method_data_unavailable_peer_issuer_ids) == 1


def test_partial_economic_peer_set_cannot_be_rescued_by_unverified_candidate_data():
    included = peer_input(identity("P1", "issuer-1"))
    unverified = peer_input(identity("P2", "issuer-2", industry="Semiconductors"))
    peer_set = select_peer_set(company_evidence(TARGET), (included, unverified), analysis_as_of=NOW)
    subset = build_peer_valuation_subset(
        peer_set, tuple(method_input(item) for item in peer_set.selections),
    )
    assert peer_set.status is PeerSetStatus.PARTIAL
    assert subset.valid_observation_count == 1
    rejected = next(item for item in subset.method_data_evidence if item.economic_selection_status is PeerSelectionStatus.UNVERIFIED)
    assert rejected.reasons == (PeerMethodEvidenceReason.ECONOMIC_MEMBERSHIP_REQUIRED,)


def test_duplicate_listing_cannot_increase_valid_observation_count():
    first = identity("P1", "issuer-shared", security="security-primary")
    second = identity("P2", "issuer-shared", security="security-secondary")
    inputs = (peer_input(first, rank=1), peer_input(second, rank=2))
    peer_set = select_peer_set(company_evidence(TARGET), inputs, analysis_as_of=NOW)
    subset = build_peer_valuation_subset(
        peer_set, tuple(method_input(item) for item in peer_set.selections),
    )
    assert len(peer_set.included_peer_issuer_ids) == 1
    assert subset.valid_observation_count == 1


def test_missing_stable_fmp_identity_withholds_forward_denominator_even_when_tickers_match():
    _, (selection, *_) = selections()
    same_text = forward(selection, provider_symbol=selection.candidate.canonical_symbol)
    item = PeerMethodDataInput(
        candidate_id=selection.candidate.candidate_id,
        peer_security_id=selection.candidate.candidate_security_id,
        peer_issuer_id=selection.candidate.candidate_issuer_id,
        peer_fiscal_year_end="12-31",
        enterprise_value_evidence=(point_in_time(selection),),
        forward_ebitda_evidence=(same_text,),
        forward_identity_binding=None,
    )
    evidence, observation = assess(selection, item)
    assert observation is None
    assert PeerMethodEvidenceReason.FORWARD_IDENTITY_UNVERIFIED in evidence.reasons


@pytest.mark.parametrize("basis", ["ticker", "company_name", "exchange_symbol"])
def test_non_stable_identity_basis_is_rejected(basis):
    _, (selection, *_) = selections()
    with pytest.raises(TypeError, match="PeerIdentityBindingBasis"):
        replace(binding(selection), identifier_basis=basis)


def test_current_meta_shape_with_zero_included_peers_is_unavailable():
    mismatches = tuple(
        peer_input(identity(f"P{i}", f"issuer-{i}", industry="Energy")) for i in range(3)
    )
    peer_set = select_peer_set(company_evidence(TARGET), mismatches, analysis_as_of=NOW)
    subset = build_peer_valuation_subset(peer_set)
    assert not peer_set.included_peer_issuer_ids
    assert subset.included_economic_peer_count == 0
    assert subset.valid_observation_count == 0
    assert subset.status is PeerValuationSubsetStatus.UNAVAILABLE


def test_safe_cli_readiness_render_reports_zero_observations_without_borrowing_candidates():
    from stock_analyser.live_peer_audit import LivePeerAuditOutcome, render_live_peer_audit

    peer_set = select_peer_set(company_evidence(TARGET), (), analysis_as_of=NOW)
    subset = build_peer_valuation_subset(peer_set)
    rendered = render_live_peer_audit(LivePeerAuditOutcome(
        symbol="SYNTHETIC",
        peer_set=peer_set,
        discovered_candidate_count=0,
        resolved_candidate_count=0,
        candidate_providers=(),
        candidate_contexts=(),
        unresolved_candidates=(),
        issues=(),
        peer_valuation_subset=subset,
    ))
    assert "Included peer count: 0" in rendered
    assert "EV/EBITDA peer valuation observations: 0" in rendered
    assert "EV/EBITDA valuation-subset status: unavailable" in rendered


def test_no_distribution_target_valuation_or_legacy_concepts_enter_8b_contracts():
    import stock_analyser.domain.peer_valuation as contracts
    import stock_analyser.services.peer_valuation_inputs as service

    source = (
        inspect.getsource(contracts.PeerMethodDataEvidence)
        + inspect.getsource(contracts.PeerValuationObservation)
        + inspect.getsource(contracts.PeerValuationSubset)
        + inspect.getsource(service)
    ).lower()
    forbidden = (
        "peer_median", "percentile", "quantile", "winsor", "outlier", "fair_value",
        "target_price", "current_price", "own_history", "discounted_cash_flow",
        "aggregation", "stance", "streamlit", "goog", "googl", "london",
    )
    assert all(re.search(rf"\b{re.escape(value)}\b", source) is None for value in forbidden)
    _, (_, *_) = selections()


def test_service_has_no_provider_scale_or_network_implementation(monkeypatch):
    import stock_analyser.services.peer_valuation_inputs as service

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    peer_set, selected = selections(3)
    subset = service.build_peer_valuation_subset(peer_set, tuple(method_input(item) for item in selected))
    source = inspect.getsource(service).lower()
    assert subset.valid_observation_count == 3
    assert all(value not in source for value in ("requests", "httpx", "urllib", "socket", "x1000", "1_000_000"))
