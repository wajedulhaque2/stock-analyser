from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import math
import socket

import pytest

from stock_analyser.domain import (
    CapitalStructureCompleteness,
    CapitalStructureSnapshot,
    DirectEnterpriseEquityBridge,
    EnterpriseAdjustmentRequirement,
    EnterpriseBridgeMethod,
    EstimateCase,
    ForwardPeriodSelection,
    Frequency,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    PeerDistributionStatus,
    PeerMethod,
    PeerMethodEvidenceStatus,
    PeerSet,
    PeerSetStatus,
    PeerTargetValuationPoint,
    PeerTargetValuationResult,
    PeerTargetValuationSelection,
    PeerValuationObservation,
    PeerValuationStatistic,
    PeerValuationSubset,
    PeerValuationSubsetStatus,
    Provenance,
    ShareCountBasis,
    ShareCountSemantics,
    ValuationBasis,
    ValuationMethodStatus,
    stable_capital_structure_id,
    stable_direct_bridge_id,
    stable_peer_target_selection_id,
    stable_peer_valuation_observation_id,
    stable_peer_valuation_subset_id,
)
from stock_analyser.services import (
    build_peer_multiple_distribution,
    calculate_peer_target_valuation,
)


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
TARGET_SECURITY = "security-target-8d"
TARGET_ISSUER = "issuer-target-8d"
PEER_SET_ID = "peer-set:synthetic-8d"


def prov(provider: str, metric: str, symbol: str = "SYN") -> Provenance:
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"synthetic_{provider}_{metric}",
        provider_symbol=symbol,
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric=metric,
    )


def peer_observation(number: int, multiple: float, period=ForwardPeriodSelection.FY1):
    period_end = date(2027 if period is ForwardPeriodSelection.FY1 else 2028, 12, 31)
    provenance = (prov("fiscal", "calculated_tev", f"P{number}"), prov("fmp", "ebitda", f"P{number}"))
    return PeerValuationObservation(
        observation_id=stable_peer_valuation_observation_id(str(number), str(multiple), period.value),
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        peer_set_id=PEER_SET_ID,
        peer_security_id=f"security-peer-{number}",
        peer_issuer_id=f"issuer-peer-{number}",
        peer_selection_result_id=f"peer-selection:8d-{number}",
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        enterprise_value=multiple * 100,
        forward_denominator=MetricId.EBITDA,
        forward_ebitda=100,
        forward_period=period,
        estimate_case=EstimateCase.AVERAGE,
        ev_ebitda_multiple=multiple,
        unit=MetricUnit.RATIO,
        currency="USD",
        enterprise_value_observation_id=f"metric:tev:{number}",
        forward_ebitda_observation_id=f"metric:fwd-ebitda:{number}",
        enterprise_value_date=NOW.date(),
        forward_period_end=period_end,
        forward_estimate_as_of=NOW,
        analysis_as_of=NOW,
        provenance=provenance,
        policy_id="peer-ev-ebitda-inputs-v1",
        status=PeerMethodEvidenceStatus.AVAILABLE,
    )


def usable_distribution(period=ForwardPeriodSelection.FY1):
    observations = tuple(peer_observation(index, multiple, period) for index, multiple in enumerate((10, 15, 20), 1))
    subset = PeerValuationSubset(
        subset_id=stable_peer_valuation_subset_id("8d", period.value),
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        peer_set_id=PEER_SET_ID,
        method=PeerMethod.EV_EBITDA,
        analysis_as_of=NOW,
        selected_forward_period=period,
        estimate_case=EstimateCase.AVERAGE,
        included_economic_peer_count=3,
        method_data_available_peer_issuer_ids=tuple(item.peer_issuer_id for item in observations),
        method_data_unavailable_peer_issuer_ids=(),
        valid_observation_ids=tuple(item.observation_id for item in observations),
        valid_observation_count=3,
        minimum_required_valid_observations=3,
        status=PeerValuationSubsetStatus.USABLE,
        method_data_evidence=(),
        observations=observations,
        policy_id="peer-ev-ebitda-inputs-v1",
        provenance=tuple(dict.fromkeys(p for item in observations for p in item.provenance)),
    )
    return build_peer_multiple_distribution(subset)


def empty_distribution():
    subset = PeerValuationSubset(
        subset_id=stable_peer_valuation_subset_id("8d", "empty"),
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        peer_set_id=PEER_SET_ID,
        method=PeerMethod.EV_EBITDA,
        analysis_as_of=NOW,
        selected_forward_period=ForwardPeriodSelection.FY1,
        estimate_case=EstimateCase.AVERAGE,
        included_economic_peer_count=0,
        method_data_available_peer_issuer_ids=(),
        method_data_unavailable_peer_issuer_ids=(),
        valid_observation_ids=(),
        valid_observation_count=0,
        minimum_required_valid_observations=3,
        status=PeerValuationSubsetStatus.UNAVAILABLE,
        method_data_evidence=(),
        observations=(),
        policy_id="peer-ev-ebitda-inputs-v1",
        provenance=(),
    )
    return build_peer_multiple_distribution(subset)


def forward(period=ForwardPeriodSelection.FY1, value=100.0, currency="USD", case=EstimateCase.AVERAGE):
    year = 2027 if period is ForwardPeriodSelection.FY1 else 2028
    provenance = prov("fmp", "ebitda", "TARGET")
    return MetricObservation(
        observation_id=f"metric:target-ebitda:{period.value}:{case.value}",
        metric_id=MetricId.EBITDA,
        value=value,
        unit=MetricUnit.CURRENCY,
        frequency=Frequency.ANNUAL,
        observation_type=ObservationType.ESTIMATE,
        estimate_case=case,
        retrieved_at=NOW,
        as_of_at=NOW,
        provenance=provenance,
        currency=currency,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        fiscal_year=year,
    )


def direct_bridge(adjustment=50.0, shares=10.0, currency="USD", semantics=ShareCountSemantics.ISSUER_SHARES):
    bridge_id = stable_direct_bridge_id("8d", str(adjustment), str(shares), currency)
    source_ids = ("metric:target-tev", "metric:target-market-cap", "metric:target-shares")
    return DirectEnterpriseEquityBridge(
        bridge_id=bridge_id,
        bridge_method=EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE,
        security_id=TARGET_SECURITY,
        issuer_id=TARGET_ISSUER,
        analysis_as_of=NOW,
        enterprise_value=adjustment + 100,
        market_cap=100,
        enterprise_equity_adjustment=adjustment,
        currency=currency,
        enterprise_value_observation_id=source_ids[0],
        market_cap_observation_id=source_ids[1],
        enterprise_value_observation_date=NOW.date(),
        market_cap_observation_date=NOW.date(),
        provider="fiscal",
        provider_symbols=("TARGET",),
        shares_outstanding=shares,
        shares_observation_id=source_ids[2],
        shares_observation_date=NOW.date(),
        share_basis=ShareCountBasis.SHARES_OUTSTANDING,
        share_count_semantics=semantics,
        completeness_status=CapitalStructureCompleteness.COMPLETE,
        observation_date_gap_days=0,
        maximum_date_gap_days=None,
        date_gap_exceeded=False,
        source_observation_ids=source_ids,
        provenance=(prov("fiscal", "target_bridge", "TARGET"),),
        issues=(),
        missing_requirements=(),
        warnings=(),
        policy_id="own-history-inputs-v1-fy1-average-fiscal-actuals",
    )


def component_bridge():
    source_ids = ("metric:target-cash", "metric:target-debt", "metric:target-shares")
    return CapitalStructureSnapshot(
        snapshot_id=stable_capital_structure_id("8d-component"),
        security_id=TARGET_SECURITY,
        issuer_id=TARGET_ISSUER,
        analysis_as_of=NOW,
        cash_and_equivalents=30,
        cash_observation_id=source_ids[0],
        cash_source_date=NOW.date(),
        gross_debt=80,
        debt_observation_id=source_ids[1],
        debt_source_date=NOW.date(),
        net_debt=50,
        shares=10,
        share_observation_id=source_ids[2],
        share_source_date=NOW.date(),
        share_basis=ShareCountBasis.SHARES_OUTSTANDING,
        currency="USD",
        completeness_status=CapitalStructureCompleteness.COMPLETE,
        source_observation_ids=source_ids,
        provenance=(prov("fiscal", "component_bridge", "TARGET"),),
        provider_symbols=("TARGET",),
        cash_debt_date_gap_days=0,
        maximum_date_gap_days=None,
        date_gap_exceeded=False,
        missing_requirements=(),
        warnings=(),
        policy_id="own-history-inputs-v1-fy1-average-fiscal-actuals",
        required_additional_adjustments=(),
    )


def selection(distribution=None, denominator=None, bridge=None, method=None):
    distribution = distribution or usable_distribution()
    denominator = denominator or forward(distribution.forward_period_policy)
    bridge = bridge or direct_bridge()
    chosen_method = method or (
        EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
        if isinstance(bridge, DirectEnterpriseEquityBridge)
        else EnterpriseBridgeMethod.COMPONENT_BRIDGE
    )
    bridge_id = bridge.bridge_id if isinstance(bridge, DirectEnterpriseEquityBridge) else bridge.snapshot_id
    share_id = bridge.shares_observation_id if isinstance(bridge, DirectEnterpriseEquityBridge) else bridge.share_observation_id
    share_basis = bridge.share_basis
    return PeerTargetValuationSelection(
        selection_id=stable_peer_target_selection_id(distribution.distribution_id, denominator.observation_id, bridge_id),
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
        bridge_method=chosen_method,
        bridge_id=bridge_id,
        share_basis=share_basis,
        share_observation_id=share_id,
        policy_id="peer-target-selection-v1",
    )


def calculate(*, distribution=None, denominator=None, bridge=None, selected=None):
    distribution = distribution or usable_distribution()
    denominator = denominator or forward(distribution.forward_period_policy)
    bridge = bridge or direct_bridge()
    selected = selected or selection(distribution, denominator, bridge)
    kwargs = (
        {"direct_enterprise_bridge": bridge}
        if isinstance(bridge, DirectEnterpriseEquityBridge)
        else {"capital_structure_snapshot": bridge}
    )
    return calculate_peer_target_valuation(distribution, selected, denominator, **kwargs)


def test_synthetic_three_peer_end_to_end_target_application_is_exact_and_auditable():
    distribution = usable_distribution()
    object.__setattr__(distribution, "mean", 999.0)
    result = calculate(distribution=distribution)
    assert result.status is ValuationMethodStatus.VALID
    assert (result.lower_point.statistic, result.central_point.statistic, result.upper_point.statistic) == (
        PeerValuationStatistic.P25, PeerValuationStatistic.MEDIAN, PeerValuationStatistic.P75,
    )
    assert tuple(point.peer_multiple for point in (result.lower_point, result.central_point, result.upper_point)) == (12.5, 15, 17.5)
    assert tuple(point.implied_enterprise_value for point in (result.lower_point, result.central_point, result.upper_point)) == (1250, 1500, 1750)
    assert tuple(point.implied_equity_value for point in (result.lower_point, result.central_point, result.upper_point)) == (1200, 1450, 1700)
    assert (result.valuation_result.low, result.valuation_result.central, result.valuation_result.high) == (120, 145, 170)
    assert result.central_point.peer_multiple == distribution.median
    assert result.central_point.peer_multiple != distribution.mean


def test_documented_10_15_20_distribution_example_with_100_50_10_inputs_is_95_145_195_when_points_are_direct_stats():
    distribution = usable_distribution()
    object.__setattr__(distribution, "p25", 10.0)
    object.__setattr__(distribution, "median", 15.0)
    object.__setattr__(distribution, "p75", 20.0)
    result = calculate(distribution=distribution)
    assert (result.valuation_result.low, result.valuation_result.central, result.valuation_result.high) == (95, 145, 195)


def test_distribution_minimum_and_maximum_are_not_used_as_standard_target_bounds():
    distribution = usable_distribution()
    object.__setattr__(distribution, "minimum", 1.0)
    object.__setattr__(distribution, "maximum", 100.0)
    result = calculate(distribution=distribution)
    assert (result.lower_point.peer_multiple, result.upper_point.peer_multiple) == (
        distribution.p25, distribution.p75,
    )


@pytest.mark.parametrize("status", [PeerDistributionStatus.PARTIAL, PeerDistributionStatus.INSUFFICIENT])
def test_partial_or_insufficient_distribution_cannot_produce_numeric_valuation(status):
    result = calculate_peer_target_valuation(replace(usable_distribution(), status=status))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.lower_point is result.central_point is result.upper_point is None
    assert result.valuation_result is None


def test_unavailable_zero_observation_distribution_is_successfully_unavailable_without_target_inputs():
    result = calculate_peer_target_valuation(empty_distribution())
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.distribution_id == empty_distribution().distribution_id
    assert any("requires USABLE" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("field,value", [
    ("target_security_id", "other-security"),
    ("target_issuer_id", "other-issuer"),
    ("distribution_id", "other-distribution"),
    ("peer_set_id", "other-peer-set"),
    ("peer_valuation_subset_id", "other-subset"),
    ("analysis_as_of", NOW - timedelta(days=1)),
])
def test_target_and_distribution_identity_snapshot_references_must_match(field, value):
    dist, denominator, bridge = usable_distribution(), forward(), direct_bridge()
    selected = selection(dist, denominator, bridge)
    object.__setattr__(selected, field, value)
    result = calculate(distribution=dist, denominator=denominator, bridge=bridge, selected=selected)
    assert result.status is ValuationMethodStatus.UNAVAILABLE


@pytest.mark.parametrize("field,value,reason", [
    ("multiple_type", "P_E", "EV_EBITDA"),
    ("valuation_basis", ValuationBasis.EQUITY, "enterprise basis"),
    ("p25", 0.0, "finite and positive"),
    ("median", math.nan, "finite and positive"),
    ("p75", -1.0, "finite and positive"),
])
def test_malformed_distribution_is_not_repaired(field, value, reason):
    dist = usable_distribution()
    object.__setattr__(dist, field, value)
    result = calculate(distribution=dist)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any(reason in issue.reason for issue in result.issues)


def test_misordered_distribution_is_not_sorted():
    dist = usable_distribution()
    object.__setattr__(dist, "p25", 16.0)
    result = calculate(distribution=dist)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("not reordered" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("period", [ForwardPeriodSelection.FY1, ForwardPeriodSelection.FY2])
def test_distribution_and_target_use_the_same_explicit_fy_policy_and_never_ntm(period):
    dist = usable_distribution(period)
    result = calculate(distribution=dist, denominator=forward(period))
    assert result.status is ValuationMethodStatus.VALID
    assert result.selected_forward_period is period
    assert result.target_forward_period_end.year == (2027 if period is ForwardPeriodSelection.FY1 else 2028)


def test_fy1_distribution_cannot_use_fy2_target_and_fy1_is_not_ntm():
    dist, denominator, bridge = usable_distribution(), forward(ForwardPeriodSelection.FY2), direct_bridge()
    selected = selection(dist, denominator, bridge)
    object.__setattr__(selected, "selected_forward_period", ForwardPeriodSelection.FY2)
    result = calculate(distribution=dist, denominator=denominator, bridge=bridge, selected=selected)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("FY policy" in issue.reason for issue in result.issues)
    assert denominator.frequency is Frequency.ANNUAL


@pytest.mark.parametrize("case", [EstimateCase.LOW, EstimateCase.HIGH])
def test_low_and_high_target_estimates_are_not_substituted_for_average(case):
    dist, denominator, bridge = usable_distribution(), forward(case=case), direct_bridge()
    selected = selection(dist, denominator, bridge)
    result = calculate(distribution=dist, denominator=denominator, bridge=bridge, selected=selected)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("AVERAGE" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
def test_nonpositive_or_nonfinite_target_ebitda_is_rejected(value):
    denominator = forward()
    object.__setattr__(denominator, "value", value)
    result = calculate(denominator=denominator)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("finite and positive" in issue.reason for issue in result.issues)


@pytest.mark.parametrize("field,value", [
    ("metric_id", MetricId.EBIT),
    ("unit", MetricUnit.SHARES),
    ("frequency", Frequency.NTM),
    ("observation_type", ObservationType.ACTUAL),
])
def test_target_denominator_semantics_are_canonical_without_substitution_or_scaling(field, value):
    denominator = forward()
    object.__setattr__(denominator, field, value)
    result = calculate(denominator=denominator)
    assert result.status is ValuationMethodStatus.UNAVAILABLE


def test_direct_bridge_is_reused_exactly_and_adjustment_is_tev_minus_market_cap():
    bridge = direct_bridge()
    result = calculate(bridge=bridge)
    assert bridge.enterprise_equity_adjustment == bridge.enterprise_value - bridge.market_cap == 50
    assert result.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
    assert result.bridge_id == bridge.bridge_id
    assert all(point.enterprise_equity_adjustment == 50 for point in (result.lower_point, result.central_point, result.upper_point))


def test_complete_component_bridge_is_an_explicit_alternative_and_is_never_averaged_with_direct():
    component = component_bridge()
    result = calculate(bridge=component)
    assert result.status is ValuationMethodStatus.VALID
    assert result.bridge_method is EnterpriseBridgeMethod.COMPONENT_BRIDGE
    assert result.bridge_id == component.snapshot_id
    assert result.central_point.enterprise_equity_adjustment == component.net_debt == 50
    dist, denominator = usable_distribution(), forward()
    selected = selection(dist, denominator, component)
    mixed = calculate_peer_target_valuation(
        dist, selected, denominator,
        capital_structure_snapshot=component,
        direct_enterprise_bridge=direct_bridge(),
    )
    assert mixed.status is ValuationMethodStatus.UNAVAILABLE


@pytest.mark.parametrize("mutation,value", [
    ("bridge_id", "directbridge:" + "0" * 32),
    ("share_basis", ShareCountBasis.BASIC_WEIGHTED_AVERAGE),
    ("share_observation_id", "metric:wrong-shares"),
])
def test_exact_approved_bridge_and_share_evidence_must_match(mutation, value):
    dist, denominator, bridge = usable_distribution(), forward(), direct_bridge()
    selected = selection(dist, denominator, bridge)
    object.__setattr__(selected, mutation, value)
    result = calculate(distribution=dist, denominator=denominator, bridge=bridge, selected=selected)
    assert result.status is ValuationMethodStatus.UNAVAILABLE


def test_missing_bridge_or_missing_target_denominator_is_unavailable():
    dist = usable_distribution()
    assert calculate_peer_target_valuation(dist).status is ValuationMethodStatus.UNAVAILABLE
    assert calculate_peer_target_valuation(dist, selection(dist, forward(), direct_bridge())).status is ValuationMethodStatus.UNAVAILABLE


def test_missing_share_evidence_is_not_treated_as_zero():
    dist, denominator, complete = usable_distribution(), forward(), direct_bridge()
    selected = selection(dist, denominator, complete)
    object.__setattr__(complete, "shares_outstanding", None)
    object.__setattr__(complete, "shares_observation_id", None)
    result = calculate(distribution=dist, denominator=denominator, bridge=complete, selected=selected)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("share" in issue.reason.lower() for issue in result.issues)


@pytest.mark.parametrize("shares", [0.0, -1.0, math.inf, math.nan])
def test_invalid_approved_shares_are_rejected(shares):
    bridge = direct_bridge()
    object.__setattr__(bridge, "shares_outstanding", shares)
    result = calculate(bridge=bridge)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("share count" in issue.reason for issue in result.issues)


def test_unresolved_adr_ads_share_semantics_block_per_share_value():
    bridge = direct_bridge(semantics=ShareCountSemantics.UNVERIFIED)
    result = calculate(bridge=bridge)
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert any("ADR/ADS" in issue.reason for issue in result.issues)


def test_currency_mismatch_fails_without_fx_and_gbp_remains_gbp_per_share():
    mismatch = calculate(bridge=direct_bridge(currency="EUR"))
    assert mismatch.status is ValuationMethodStatus.UNAVAILABLE
    assert any("no FX" in issue.reason for issue in mismatch.issues)
    denominator, bridge = forward(currency="GBP"), direct_bridge(currency="GBP")
    result = calculate(denominator=denominator, bridge=bridge)
    assert result.currency == "GBP"
    assert result.valuation_result.central == 145


def test_nonpositive_implied_equity_is_retained_in_trace_not_floored_and_can_be_partial():
    result = calculate(bridge=direct_bridge(adjustment=1300))
    assert result.status is ValuationMethodStatus.PARTIAL
    assert result.lower_point.status is ValuationMethodStatus.UNAVAILABLE
    assert result.lower_point.implied_equity_value == -50
    assert result.lower_point.per_share_value is None
    assert result.central_point.status is ValuationMethodStatus.VALID


def test_invalid_median_prevents_central_value_but_retains_valid_upper_as_partial():
    result = calculate(bridge=direct_bridge(adjustment=1550))
    assert result.status is ValuationMethodStatus.PARTIAL
    assert result.central_point.status is ValuationMethodStatus.UNAVAILABLE
    assert result.valuation_result.central is None
    assert result.upper_point.status is ValuationMethodStatus.VALID


def test_all_nonpositive_equity_points_make_method_unavailable_without_flooring():
    result = calculate(bridge=direct_bridge(adjustment=2000))
    assert result.status is ValuationMethodStatus.UNAVAILABLE
    assert result.valuation_result.low is result.valuation_result.central is result.valuation_result.high is None
    assert all(point.implied_equity_value <= 0 for point in (result.lower_point, result.central_point, result.upper_point))


def test_output_identity_supporting_ids_currency_and_provenance_are_complete():
    dist, denominator, bridge = usable_distribution(), forward(), direct_bridge()
    result = calculate(distribution=dist, denominator=denominator, bridge=bridge)
    assert result.distribution_id == dist.distribution_id
    assert result.peer_set_id == dist.peer_set_id
    assert result.peer_valuation_subset_id == dist.peer_valuation_subset_id
    assert result.target_forward_observation_id == denominator.observation_id
    assert result.bridge_id == bridge.bridge_id
    assert result.share_observation_id == bridge.shares_observation_id
    assert result.currency == "USD"
    assert set(dist.valid_observation_ids).issubset(result.supporting_observation_ids)
    assert any(item.endpoint_or_dataset == "v1_peer_target_ev_ebitda_valuation" for item in result.provenance)


def test_contracts_are_immutable_and_exclude_deferred_fields():
    result = calculate()
    with pytest.raises(FrozenInstanceError):
        result.status = ValuationMethodStatus.UNAVAILABLE
    forbidden = {
        "current_price", "upside", "downside", "premium", "discount", "margin_of_safety",
        "analyst_target", "fmp_dcf", "own_history", "aggregation", "weight", "stance",
    }
    assert not forbidden.intersection(field.name for field in fields(PeerTargetValuationPoint))
    assert not forbidden.intersection(field.name for field in fields(PeerTargetValuationResult))


def test_numeric_service_has_no_provider_network_ui_scaling_or_aggregation_dependencies():
    import stock_analyser.services.peer_target_valuation as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "requests", "urllib", "httpx", "streamlit", "current_price", "price_target",
        "standard_dcf", "reverse_dcf", "own_history_valuation", "from .aggregation", "import aggregation", "1000",
        "1000000", "1000000000", "gbpence",
    )
    assert all(token not in source for token in forbidden)


def test_normal_peer_target_valuation_makes_zero_network_calls(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("normal synthetic 8D valuation attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked)
    assert calculate().status is ValuationMethodStatus.VALID


def test_safe_meta_audit_exposes_unavailable_8d_without_numbers_or_provider_requests():
    from stock_analyser.live_peer_audit import LivePeerAuditOutcome, render_live_peer_audit

    distribution = empty_distribution()
    valuation = calculate_peer_target_valuation(distribution)
    peer_set = PeerSet(
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        analysis_as_of=NOW,
        candidate_count=0,
        included_peer_issuer_ids=(),
        included_security_ids=(),
        excluded_candidate_ids=(),
        unverified_candidate_ids=(),
        unavailable_candidate_ids=(),
        minimum_required_peers=3,
        status=PeerSetStatus.PARTIAL,
        policy_id="synthetic-peer-set-policy",
        selections=(),
        method_data_readiness=(),
    )
    rendered = render_live_peer_audit(LivePeerAuditOutcome(
        symbol="META",
        peer_set=peer_set,
        discovered_candidate_count=9,
        resolved_candidate_count=9,
        candidate_providers=("fiscal",),
        candidate_contexts=(),
        unresolved_candidates=(),
        issues=(),
        peer_valuation_subset=None,
        peer_multiple_distribution=distribution,
        peer_target_valuation=valuation,
    ))
    assert "Target peer EV/EBITDA valuation status: unavailable" in rendered
    assert "requires USABLE" in rendered
    assert "Target peer EV/EBITDA lower" not in rendered
    assert valuation.valuation_result is None
