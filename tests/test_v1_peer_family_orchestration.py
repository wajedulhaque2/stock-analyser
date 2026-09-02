from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import inspect
import socket

import pytest

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CompanyIdentity,
    DirectEnterpriseEquityBridge,
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerCandidate,
    PeerCandidateSource,
    PeerFamilyFailureStage,
    PeerFamilyStatus,
    PeerIdentityBindingBasis,
    PeerMetricEvidence,
    PeerProviderIdentityBinding,
    PeerSelectionReason,
    PeerSelectionStatus,
    PeerSetStatus,
    PeerTargetValuationSelection,
    PeerValuationSubsetStatus,
    Provenance,
    ProviderSymbol,
    ShareCountBasis,
    ShareCountSemantics,
    ValuationMethodStatus,
    stable_direct_bridge_id,
    stable_observation_id,
    stable_peer_candidate_id,
    stable_peer_identity_binding_id,
    stable_peer_target_selection_id,
)
from stock_analyser.live_peer_audit import LivePeerAuditOutcome
from stock_analyser.live_peer_family_audit import render_live_peer_family_audit
from stock_analyser.services import (
    PeerCandidateInput,
    PeerCompanyEvidence,
    PeerFamilyOrchestrationPolicy,
    PeerMethodDataInput,
    build_peer_multiple_distribution,
    build_peer_valuation_subset,
    orchestrate_peer_family,
    select_peer_set,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)


def identity(
    symbol: str = "TGT",
    issuer: str = "issuer-target",
    *,
    security: str | None = None,
    reporting: str = "USD",
    quote: str = "USD",
    quote_unit: str | None = None,
    scale: float = 1,
    fye: str = "12-31",
) -> CompanyIdentity:
    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=security or f"security-{symbol.lower()}",
        issuer_id=issuer,
        company_name=f"Synthetic {symbol}",
        issuer_domicile="US" if not symbol.endswith(".L") else "GB",
        listing_country="US" if not symbol.endswith(".L") else "GB",
        exchange="Synthetic Exchange",
        sector="Technology",
        industry="Software",
        security_type="Ordinary share",
        reporting_currency=reporting,
        quote_currency=quote,
        quote_unit=quote_unit or quote,
        price_scale=scale,
        fiscal_year_end=fye,
        provider_symbols=(ProviderSymbol("fiscal", f"F-{symbol}"),),
    )


TARGET = identity()


def provenance(provider: str, symbol: str, as_of: datetime, metric: str) -> Provenance:
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_8e",
        provider_symbol=symbol,
        retrieved_at=as_of,
        as_of_at=as_of,
        source_metric=metric,
    )


def annual_actual(company: CompanyIdentity, metric: MetricId, value: float, year: int) -> MetricObservation:
    observed = NOW - timedelta(days=30)
    start, end = date(year, 1, 1), date(year, 12, 31)
    prov = provenance("fiscal", f"F-{company.canonical_symbol}", observed, metric.value)
    return MetricObservation(
        observation_id=stable_observation_id(
            metric_id=metric.value, provider="fiscal", provider_symbol=prov.provider_symbol,
            frequency=Frequency.ANNUAL.value, observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value, period_start=str(start),
            period_end=str(end), as_of_at=observed.isoformat(),
        ),
        metric_id=metric, value=value, unit=MetricUnit.CURRENCY, currency=company.reporting_currency,
        frequency=Frequency.ANNUAL, observation_type=ObservationType.ACTUAL,
        estimate_case=EstimateCase.NOT_APPLICABLE, retrieved_at=observed, as_of_at=observed,
        period_start=start, period_end=end, fiscal_year=year, provenance=prov,
    )


def company_evidence(company: CompanyIdentity) -> PeerCompanyEvidence:
    return PeerCompanyEvidence(
        identity=company,
        actual_observations=(
            annual_actual(company, MetricId.REVENUE, 100, 2024),
            annual_actual(company, MetricId.REVENUE, 110, 2025),
            annual_actual(company, MetricId.EBITDA, 33, 2025),
        ),
    )


def peer_input(company: CompanyIdentity, target: CompanyIdentity = TARGET) -> PeerCandidateInput:
    symbol = f"F-{company.canonical_symbol}"
    source = PeerCandidateSource.PROVIDER_PROFILE_PEERS
    prov = provenance("fiscal", symbol, NOW, "peers")
    candidate = PeerCandidate(
        candidate_id=stable_peer_candidate_id(
            target_security_id=target.security_id, candidate_security_id=company.security_id,
            provider="fiscal", provider_symbol=symbol, candidate_source=source,
        ),
        target_security_id=target.security_id, target_issuer_id=target.issuer_id,
        candidate_security_id=company.security_id, candidate_issuer_id=company.issuer_id,
        provider="fiscal", provider_symbol=symbol, canonical_symbol=company.canonical_symbol,
        candidate_source=source, source_as_of=NOW, retrieved_at=NOW, provenance=prov,
    )
    return PeerCandidateInput(candidate, company_evidence(company))


def peer_set(count: int = 3, target: CompanyIdentity = TARGET):
    inputs = tuple(peer_input(identity(f"P{i}", f"issuer-{i}"), target) for i in range(count))
    return select_peer_set(company_evidence(target), inputs, analysis_as_of=NOW)


def point_in_time(selection, value: float = 1000, currency: str = "USD") -> PeerMetricEvidence:
    observed = NOW.date() - timedelta(days=1)
    as_of = datetime.combine(observed, time(12), tzinfo=timezone.utc)
    symbol = selection.candidate.provider_symbol
    prov = provenance("fiscal", symbol, as_of, "calculated_tev")
    observation = MetricObservation(
        observation_id=stable_observation_id(
            metric_id=MetricId.ENTERPRISE_VALUE.value, provider="fiscal", provider_symbol=symbol,
            frequency=Frequency.POINT_IN_TIME.value, observation_type=ObservationType.ACTUAL.value,
            estimate_case=EstimateCase.NOT_APPLICABLE.value, period_start="",
            period_end=str(observed), as_of_at=as_of.isoformat(),
        ),
        metric_id=MetricId.ENTERPRISE_VALUE, value=value, unit=MetricUnit.CURRENCY,
        currency=currency, frequency=Frequency.POINT_IN_TIME,
        observation_type=ObservationType.ACTUAL, estimate_case=EstimateCase.NOT_APPLICABLE,
        retrieved_at=as_of, as_of_at=as_of, period_end=observed, provenance=prov,
    )
    return PeerMetricEvidence(
        selection.candidate.candidate_security_id,
        selection.candidate.candidate_issuer_id,
        observation,
    )


def forward(
    selection, value: float = 100, *, year: int = 2027, month: int = 12,
    day: int = 31, currency: str = "USD", provider_symbol: str | None = None,
) -> PeerMetricEvidence:
    snapshot = NOW - timedelta(days=1)
    symbol = provider_symbol or f"FMP-{selection.candidate.canonical_symbol}-STABLE"
    end = date(year, month, day)
    prov = provenance("fmp", symbol, snapshot, "ebitdaAverage")
    observation = MetricObservation(
        observation_id=stable_observation_id(
            metric_id=MetricId.EBITDA.value, provider="fmp", provider_symbol=symbol,
            frequency=Frequency.ANNUAL.value, observation_type=ObservationType.ESTIMATE.value,
            estimate_case=EstimateCase.AVERAGE.value,
            period_start=str(date(year - 1, month, day) + timedelta(days=1)),
            period_end=str(end), as_of_at=snapshot.isoformat(),
        ),
        metric_id=MetricId.EBITDA, value=value, unit=MetricUnit.CURRENCY, currency=currency,
        frequency=Frequency.ANNUAL, observation_type=ObservationType.ESTIMATE,
        estimate_case=EstimateCase.AVERAGE, retrieved_at=snapshot, as_of_at=snapshot,
        period_start=date(year - 1, month, day) + timedelta(days=1),
        period_end=end, fiscal_year=year, provenance=prov,
    )
    return PeerMetricEvidence(
        selection.candidate.candidate_security_id,
        selection.candidate.candidate_issuer_id,
        observation,
    )


def binding(selection, *, symbol: str | None = None) -> PeerProviderIdentityBinding:
    provider_symbol = symbol or f"FMP-{selection.candidate.canonical_symbol}-STABLE"
    verified = NOW - timedelta(days=2)
    identifier = str(100000 + int(selection.candidate.canonical_symbol[1:]))
    prov = provenance("fmp", provider_symbol, verified, "cik")
    return PeerProviderIdentityBinding(
        binding_id=stable_peer_identity_binding_id(
            selection.candidate.candidate_issuer_id, "fmp", "cik", identifier,
        ),
        peer_security_id=selection.candidate.candidate_security_id,
        peer_issuer_id=selection.candidate.candidate_issuer_id,
        provider="fmp", provider_symbol=provider_symbol,
        identifier_basis=PeerIdentityBindingBasis.CIK,
        canonical_identifier=identifier, provider_identifier=f"000{identifier}",
        verified_at=verified, provenance=prov,
    )


def method_input(
    selection, *, value: float = 1000, year: int = 2027, month: int = 12,
    day: int = 31, currency: str = "USD", with_binding=True,
):
    return PeerMethodDataInput(
        candidate_id=selection.candidate.candidate_id,
        peer_security_id=selection.candidate.candidate_security_id,
        peer_issuer_id=selection.candidate.candidate_issuer_id,
        peer_fiscal_year_end=f"{month:02d}-{day:02d}",
        enterprise_value_evidence=(point_in_time(selection, value, currency),),
        forward_ebitda_evidence=(
            forward(selection, 100, year=year, month=month, day=day, currency=currency),
        ),
        forward_identity_binding=binding(selection) if with_binding else None,
    )


def target_forward(target: CompanyIdentity = TARGET, value: float = 100, currency: str | None = None):
    prov = provenance("fmp", target.canonical_symbol, NOW, "ebitdaAverage")
    return MetricObservation(
        observation_id=f"metric:target:{target.security_id}:fy1",
        metric_id=MetricId.EBITDA, value=value, unit=MetricUnit.CURRENCY,
        currency=currency or target.reporting_currency, frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ESTIMATE, estimate_case=EstimateCase.AVERAGE,
        retrieved_at=NOW, as_of_at=NOW, period_start=date(2027, 1, 1),
        period_end=date(2027, 12, 31), fiscal_year=2027, provenance=prov,
    )


def direct_bridge(target: CompanyIdentity = TARGET, *, currency: str | None = None):
    bridge_id = stable_direct_bridge_id("8e", target.security_id, target.reporting_currency)
    ids = ("metric:target-tev", "metric:target-market-cap", "metric:target-shares")
    return DirectEnterpriseEquityBridge(
        bridge_id=bridge_id, bridge_method=EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE,
        security_id=target.security_id, issuer_id=target.issuer_id, analysis_as_of=NOW,
        enterprise_value=150, market_cap=100, enterprise_equity_adjustment=50,
        currency=currency or target.reporting_currency,
        enterprise_value_observation_id=ids[0], market_cap_observation_id=ids[1],
        enterprise_value_observation_date=NOW.date(), market_cap_observation_date=NOW.date(),
        provider="fiscal", provider_symbols=(target.canonical_symbol,), shares_outstanding=10,
        shares_observation_id=ids[2], shares_observation_date=NOW.date(),
        share_basis=ShareCountBasis.SHARES_OUTSTANDING,
        share_count_semantics=ShareCountSemantics.ISSUER_SHARES,
        completeness_status=CapitalStructureCompleteness.COMPLETE,
        observation_date_gap_days=0, maximum_date_gap_days=None, date_gap_exceeded=False,
        source_observation_ids=ids,
        provenance=(provenance("fiscal", target.canonical_symbol, NOW, "bridge"),),
        issues=(), missing_requirements=(), warnings=(), policy_id="bridge-policy-v1",
    )


def selection(distribution, denominator, bridge):
    return PeerTargetValuationSelection(
        selection_id=stable_peer_target_selection_id(
            distribution.distribution_id, denominator.observation_id, bridge.bridge_id,
        ),
        target_security_id=distribution.target_security_id,
        target_issuer_id=distribution.target_issuer_id,
        analysis_as_of=distribution.analysis_as_of,
        distribution_id=distribution.distribution_id,
        peer_set_id=distribution.peer_set_id,
        peer_valuation_subset_id=distribution.peer_valuation_subset_id,
        selected_forward_period=distribution.forward_period_policy,
        target_forward_observation_id=denominator.observation_id,
        target_forward_period_end=denominator.period_end,
        estimate_case=EstimateCase.AVERAGE,
        bridge_method=EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE,
        bridge_id=bridge.bridge_id, share_basis=bridge.share_basis,
        share_observation_id=bridge.shares_observation_id,
        policy_id="peer-target-selection-v1",
    )


def complete(target: CompanyIdentity = TARGET, *, values=(1000, 1500, 2000)):
    peers = peer_set(3, target)
    inputs = tuple(method_input(item, value=value, currency=target.reporting_currency) for item, value in zip(peers.selections, values))
    distribution = build_peer_multiple_distribution(build_peer_valuation_subset(peers, inputs))
    denominator = target_forward(target)
    bridge = direct_bridge(target)
    chosen = selection(distribution, denominator, bridge)
    result = orchestrate_peer_family(
        target, peers, inputs, analysis_as_of=NOW, target_selection=chosen,
        target_forward_ebitda=denominator, direct_enterprise_bridge=bridge,
    )
    return result, peers, inputs


def test_complete_path_executes_8a_through_8d_and_flows_all_ids_unchanged():
    result, peers, _ = complete()
    assert result.status is PeerFamilyStatus.VALID
    assert result.failure_stage is PeerFamilyFailureStage.COMPLETE
    assert result.peer_set is peers
    assert result.peer_set_id == result.peer_subset.peer_set_id == result.peer_distribution.peer_set_id == result.target_peer_valuation.peer_set_id
    assert result.peer_subset_id == result.peer_distribution.peer_valuation_subset_id == result.target_peer_valuation.peer_valuation_subset_id
    assert result.peer_distribution_id == result.target_peer_valuation.distribution_id
    assert result.target_peer_valuation_id == result.target_peer_valuation.result_id
    assert result.target_peer_valuation.status is ValuationMethodStatus.VALID
    assert result.peer_set_id in result.supporting_ids
    assert result.peer_subset_id in result.supporting_ids
    assert result.target_peer_valuation.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
    assert result.target_peer_valuation.bridge_id == result.target_peer_valuation.central_point.bridge_id


@pytest.mark.parametrize("count,expected_status", ((0, PeerSetStatus.INSUFFICIENT), (1, PeerSetStatus.PARTIAL), (2, PeerSetStatus.PARTIAL)))
def test_nonusable_peer_sets_stop_safely_without_borrowing_observations(count, expected_status):
    peers = peer_set(count)
    result = orchestrate_peer_family(TARGET, peers, (), analysis_as_of=NOW)
    assert peers.status is expected_status
    assert result.failure_stage in {PeerFamilyFailureStage.CANDIDATE_DISCOVERY, PeerFamilyFailureStage.PEER_SET}
    assert result.valid_peer_observation_count == 0
    assert result.target_peer_valuation is None


def test_explicit_upstream_unavailable_preserves_earliest_stage_and_expected_status():
    result = orchestrate_peer_family(
        TARGET, None, analysis_as_of=NOW,
        upstream_failure_stage=PeerFamilyFailureStage.IDENTITY,
        upstream_blocking_reasons=("canonical identity unavailable",),
        policy=PeerFamilyOrchestrationPolicy(unavailable_is_expected=True),
    )
    assert result.status is PeerFamilyStatus.EXPECTED_UNAVAILABLE
    assert result.failure_stage is PeerFamilyFailureStage.IDENTITY
    assert result.blocking_reasons == ("canonical identity unavailable",)
    later = orchestrate_peer_family(
        TARGET, None, analysis_as_of=NOW + timedelta(seconds=1),
        upstream_failure_stage=PeerFamilyFailureStage.IDENTITY,
        upstream_blocking_reasons=("canonical identity unavailable",),
        policy=PeerFamilyOrchestrationPolicy(unavailable_is_expected=True),
    )
    assert later.orchestration_id != result.orchestration_id


def test_included_only_gate_and_stable_binding_requirement_are_preserved():
    peers = peer_set(3)
    excluded = replace(
        peers.selections[2], status=PeerSelectionStatus.EXCLUDED,
        reasons=(PeerSelectionReason.INDUSTRY_MISMATCH,),
    )
    altered = replace(
        peers,
        included_peer_issuer_ids=peers.included_peer_issuer_ids[:2],
        included_security_ids=peers.included_security_ids[:2],
        excluded_candidate_ids=(excluded.candidate.candidate_id,),
        selections=(*peers.selections[:2], excluded),
        status=PeerSetStatus.PARTIAL,
    )
    inputs = tuple(method_input(item) for item in altered.selections)
    result = orchestrate_peer_family(TARGET, altered, inputs, analysis_as_of=NOW)
    assert result.peer_subset.included_economic_peer_count == 2
    assert excluded.candidate.candidate_issuer_id not in result.peer_subset.method_data_available_peer_issuer_ids
    unbound = tuple(method_input(item, with_binding=False) for item in peers.selections)
    identity_failure = orchestrate_peer_family(TARGET, peers, unbound, analysis_as_of=NOW)
    assert identity_failure.failure_stage is PeerFamilyFailureStage.PEER_PROVIDER_IDENTITY
    assert identity_failure.valid_peer_observation_count == 0


def test_ticker_equality_alone_cannot_replace_stable_cross_provider_binding():
    peers = peer_set(3)
    inputs = tuple(
        replace(
            method_input(item, with_binding=False),
            forward_ebitda_evidence=(
                forward(item, provider_symbol=item.candidate.canonical_symbol),
            ),
        )
        for item in peers.selections
    )
    result = orchestrate_peer_family(TARGET, peers, inputs, analysis_as_of=NOW)
    assert result.failure_stage is PeerFamilyFailureStage.PEER_PROVIDER_IDENTITY
    assert result.valid_peer_observation_count == 0


@pytest.mark.parametrize(
    "field,stage",
    (
        ("enterprise_value_evidence", PeerFamilyFailureStage.PEER_ENTERPRISE_VALUE),
        ("forward_ebitda_evidence", PeerFamilyFailureStage.PEER_FORWARD_DENOMINATOR),
    ),
)
def test_earliest_method_data_failure_stage_is_preserved(field, stage):
    peers = peer_set(3)
    inputs = tuple(replace(method_input(item), **{field: ()}) for item in peers.selections)
    result = orchestrate_peer_family(TARGET, peers, inputs, analysis_as_of=NOW)
    assert result.failure_stage is stage
    assert result.target_peer_valuation is None


def test_one_stock_failure_does_not_affect_another_result():
    failed = orchestrate_peer_family(TARGET, peer_set(0), analysis_as_of=NOW)
    valid, _, _ = complete(identity("ALT", "issuer-alt"))
    assert failed.status is not PeerFamilyStatus.VALID
    assert valid.status is PeerFamilyStatus.VALID
    assert valid.canonical_symbol == "ALT"


def test_same_target_identity_and_snapshot_are_required_throughout():
    peers = peer_set(3)
    with pytest.raises(ValueError, match="target identity"):
        orchestrate_peer_family(identity("OTHER", "issuer-other"), peers, analysis_as_of=NOW)
    with pytest.raises(ValueError, match="analysis_as_of"):
        orchestrate_peer_family(TARGET, peers, analysis_as_of=NOW + timedelta(seconds=1))


def test_provider_symbols_remain_provenance_only_and_no_ticker_rewrites_occur():
    result, _, _ = complete()
    assert any(item.provider_symbol.startswith("FMP-") for item in result.provenance)
    source = inspect.getsource(orchestrate_peer_family)
    assert "GOOGL" not in source and "GOOG" not in source and ".L" not in source


def test_peer_fy1_periods_may_differ_while_target_fy1_remains_target_specific():
    peers = peer_set(3)
    calendars = ((2027, 12, 31), (2027, 6, 30), (2027, 3, 31))
    inputs = tuple(
        method_input(item, year=year, month=month, day=day)
        for item, (year, month, day) in zip(peers.selections, calendars)
    )
    subset = build_peer_valuation_subset(peers, inputs)
    assert {item.forward_period_end for item in subset.observations} == {
        date(2027, 12, 31), date(2027, 6, 30), date(2027, 3, 31),
    }
    distribution = build_peer_multiple_distribution(subset)
    denominator = target_forward()
    bridge = direct_bridge()
    result = orchestrate_peer_family(
        TARGET, peers, inputs, analysis_as_of=NOW,
        target_selection=selection(distribution, denominator, bridge),
        target_forward_ebitda=denominator, direct_enterprise_bridge=bridge,
    )
    assert result.target_peer_valuation.target_forward_period_end == date(2027, 12, 31)
    assert result.peer_distribution.forward_period_policy is ForwardPeriodSelection.FY1


def test_high_finite_multiple_is_retained_with_no_cap_or_outlier_deletion():
    result, _, _ = complete(values=(1000, 1500, 1_000_000))
    assert result.peer_distribution.maximum == 10_000
    assert result.peer_distribution.sample_count == 3
    assert result.peer_distribution.excluded_observation_ids == ()
    assert result.peer_distribution.automatic_outlier_removal_applied is False


@pytest.mark.parametrize(
    "target",
    (
        identity("USD", "issuer-usd"),
        identity("RR.L", "issuer-rr", reporting="GBP", quote="GBP", quote_unit="GBp", scale=0.01),
        identity("SHEL.L", "issuer-shell", reporting="USD", quote="GBP", quote_unit="GBp", scale=0.01),
    ),
)
def test_currency_and_quote_unit_metadata_are_preserved_without_fx_or_100x(target):
    result, _, _ = complete(target)
    valuation = result.target_peer_valuation
    assert result.reporting_currency == target.reporting_currency
    assert result.quote_currency == target.quote_currency
    assert result.quote_unit == target.quote_unit
    assert result.quote_price_scale == target.price_scale
    assert valuation.currency == target.reporting_currency
    assert valuation.central_point.per_share_value == 145


def test_currency_mismatch_fails_at_target_application_without_implicit_fx():
    peers = peer_set(3)
    inputs = tuple(method_input(item) for item in peers.selections)
    distribution = build_peer_multiple_distribution(build_peer_valuation_subset(peers, inputs))
    denominator = target_forward(currency="USD")
    bridge = direct_bridge(currency="GBP")
    result = orchestrate_peer_family(
        TARGET, peers, inputs, analysis_as_of=NOW,
        target_selection=selection(distribution, denominator, bridge),
        target_forward_ebitda=denominator, direct_enterprise_bridge=bridge,
    )
    assert result.failure_stage is PeerFamilyFailureStage.TARGET_APPLICATION
    assert result.target_peer_valuation.status is ValuationMethodStatus.UNAVAILABLE
    assert any("no FX" in reason for reason in result.blocking_reasons)


def test_missing_target_inputs_stop_at_earliest_target_stage_and_do_not_call_8d(monkeypatch):
    peers = peer_set(3)
    inputs = tuple(method_input(item) for item in peers.selections)
    import stock_analyser.services.peer_family_orchestration as module
    monkeypatch.setattr(module, "calculate_peer_target_valuation", lambda *a, **k: pytest.fail("8D must not run"))
    result = orchestrate_peer_family(TARGET, peers, inputs, analysis_as_of=NOW)
    assert result.failure_stage is PeerFamilyFailureStage.TARGET_FORWARD_DENOMINATOR
    assert result.target_peer_valuation is None


def test_selected_target_bridge_is_required_exactly_and_is_never_averaged():
    peers = peer_set(3)
    inputs = tuple(method_input(item) for item in peers.selections)
    distribution = build_peer_multiple_distribution(build_peer_valuation_subset(peers, inputs))
    denominator = target_forward()
    bridge = direct_bridge()
    result = orchestrate_peer_family(
        TARGET, peers, inputs, analysis_as_of=NOW,
        target_selection=selection(distribution, denominator, bridge),
        target_forward_ebitda=denominator,
    )
    assert result.failure_stage is PeerFamilyFailureStage.TARGET_BRIDGE
    assert result.target_peer_valuation is None


@pytest.mark.parametrize(
    "forbidden",
    ("current_price", "risk_free", "fred", "own_history", "aggregate", "stance", "streamlit", "sector", "energy", "nvda"),
)
def test_orchestration_has_no_forbidden_dependencies_or_company_specific_rules(forbidden):
    source = inspect.getsource(orchestrate_peer_family).lower()
    assert f'"{forbidden}"' not in source and f"'{forbidden}'" not in source


def test_default_synthetic_orchestration_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))
    result, _, _ = complete()
    assert result.status is PeerFamilyStatus.VALID


def test_safe_audit_contains_no_raw_payload_or_secret_and_reports_expected_unavailable():
    result = orchestrate_peer_family(
        TARGET, peer_set(0), analysis_as_of=NOW,
        policy=PeerFamilyOrchestrationPolicy(unavailable_is_expected=True),
    )
    outcome = LivePeerAuditOutcome(
        symbol="TGT", peer_set=result.peer_set, discovered_candidate_count=0,
        resolved_candidate_count=0, candidate_providers=("fiscal",), candidate_contexts=(),
        unresolved_candidates=(), issues=(), peer_valuation_subset=result.peer_subset,
        peer_multiple_distribution=result.peer_distribution,
        peer_target_valuation=result.target_peer_valuation, target_identity=TARGET,
        peer_family_orchestration=result,
    )
    rendered = render_live_peer_family_audit(outcome)
    assert "expected_unavailable" in rendered
    assert "raw provider payload" in rendered
    assert "api_key" not in rendered.lower() and "authorization" not in rendered.lower()
