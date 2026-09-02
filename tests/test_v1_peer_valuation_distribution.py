from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import inspect
import math
import re
import socket

import pytest

from stock_analyser.domain import (
    EstimateCase,
    ForwardPeriodSelection,
    MetricId,
    MetricUnit,
    PeerCentralStatistic,
    PeerDistributionStatus,
    PeerMethod,
    PeerMethodEvidenceStatus,
    PeerOutlierPolicy,
    PeerQuantileConvention,
    PeerSet,
    PeerSetStatus,
    PeerValuationObservation,
    PeerValuationSubset,
    PeerValuationSubsetStatus,
    Provenance,
    ValuationBasis,
    stable_peer_valuation_observation_id,
    stable_peer_valuation_subset_id,
)
from stock_analyser.services import (
    PeerDistributionPolicy,
    build_peer_multiple_distribution,
    linear_quantile,
)
from stock_analyser.services.statistics import linear_quantile as shared_linear_quantile


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
TARGET_SECURITY = "security-target-synthetic"
TARGET_ISSUER = "issuer-target-synthetic"
PEER_SET_ID = "peer-set:synthetic-8c"


def provenance(peer_number: int, currency: str) -> tuple[Provenance, ...]:
    as_of = NOW - timedelta(days=1)
    return (
        Provenance(
            provider="fiscal",
            endpoint_or_dataset="synthetic_current_tev",
            provider_symbol=f"FISCAL-P{peer_number}",
            retrieved_at=as_of,
            as_of_at=as_of,
            source_metric="calculated_tev",
        ),
        Provenance(
            provider="fmp",
            endpoint_or_dataset="synthetic_forward_ebitda",
            provider_symbol=f"FMP-P{peer_number}",
            retrieved_at=as_of,
            as_of_at=as_of,
            source_metric=f"ebitdaAvg-{currency}",
        ),
    )


def observation(
    peer_number: int,
    multiple: float,
    *,
    currency: str = "USD",
    forward_period_end: date | None = None,
    forward_period: ForwardPeriodSelection = ForwardPeriodSelection.FY1,
    peer_set_id: str = PEER_SET_ID,
) -> PeerValuationObservation:
    end = forward_period_end or date(2027, 12, 31)
    tev = multiple * 100
    observation_id = stable_peer_valuation_observation_id(
        TARGET_SECURITY, TARGET_ISSUER, peer_set_id, f"issuer-peer-{peer_number}",
        str(multiple), currency, end.isoformat(), forward_period.value,
    )
    source = provenance(peer_number, currency)
    return PeerValuationObservation(
        observation_id=observation_id,
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        peer_set_id=peer_set_id,
        peer_security_id=f"security-peer-{peer_number}",
        peer_issuer_id=f"issuer-peer-{peer_number}",
        peer_selection_result_id=f"peer-selection:synthetic-{peer_number}",
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        enterprise_value=tev,
        forward_denominator=MetricId.EBITDA,
        forward_ebitda=100,
        forward_period=forward_period,
        estimate_case=EstimateCase.AVERAGE,
        ev_ebitda_multiple=multiple,
        unit=MetricUnit.RATIO,
        currency=currency,
        enterprise_value_observation_id=f"metric-observation:tev-{peer_number}",
        forward_ebitda_observation_id=f"metric-observation:ebitda-{peer_number}",
        enterprise_value_date=date(2026, 8, 27),
        forward_period_end=end,
        forward_estimate_as_of=NOW - timedelta(days=1),
        analysis_as_of=NOW,
        provenance=source,
        policy_id="peer-ev-ebitda-inputs-v1",
    )


def valuation_subset(
    observations=(),
    *,
    status: PeerValuationSubsetStatus | None = None,
    unavailable_peer_ids: tuple[str, ...] = (),
    peer_set_id: str = PEER_SET_ID,
) -> PeerValuationSubset:
    items = tuple(observations)
    resolved_status = status or (
        PeerValuationSubsetStatus.USABLE if len(items) >= 3
        else PeerValuationSubsetStatus.INSUFFICIENT if items
        else PeerValuationSubsetStatus.UNAVAILABLE
    )
    return PeerValuationSubset(
        subset_id=stable_peer_valuation_subset_id(
            peer_set_id, NOW.isoformat(), resolved_status.value, *(item.observation_id for item in items),
        ),
        target_security_id=TARGET_SECURITY,
        target_issuer_id=TARGET_ISSUER,
        peer_set_id=peer_set_id,
        method=PeerMethod.EV_EBITDA,
        analysis_as_of=NOW,
        selected_forward_period=ForwardPeriodSelection.FY1,
        estimate_case=EstimateCase.AVERAGE,
        included_economic_peer_count=len(items) + len(unavailable_peer_ids),
        method_data_available_peer_issuer_ids=tuple(item.peer_issuer_id for item in items),
        method_data_unavailable_peer_issuer_ids=unavailable_peer_ids,
        valid_observation_ids=tuple(item.observation_id for item in items),
        valid_observation_count=len(items),
        minimum_required_valid_observations=3,
        status=resolved_status,
        method_data_evidence=(),
        observations=items,
        policy_id="peer-ev-ebitda-inputs-v1",
        provenance=tuple(dict.fromkeys(p for item in items for p in item.provenance)),
    )


def three_peer_subset(values=(10.0, 15.0, 20.0)):
    return valuation_subset(tuple(observation(index + 1, value) for index, value in enumerate(values)))


def test_three_peer_usable_subset_produces_type7_distribution_and_raw_diagnostics():
    distribution = build_peer_multiple_distribution(three_peer_subset())
    assert distribution.status is PeerDistributionStatus.USABLE
    assert distribution.sample_count == 3
    assert distribution.minimum == 10
    assert distribution.p25 == 12.5
    assert distribution.median == 15
    assert distribution.p75 == 17.5
    assert distribution.maximum == 20
    assert distribution.mean == 15
    assert distribution.population_standard_deviation == pytest.approx(math.sqrt(50 / 3))
    assert distribution.iqr == 5
    assert distribution.iqr_to_median == pytest.approx(1 / 3)
    assert distribution.max_to_median == pytest.approx(4 / 3)
    assert distribution.min_to_median == pytest.approx(2 / 3)


def test_type7_convention_is_explicit_and_shared_with_historical_distributions():
    distribution = build_peer_multiple_distribution(three_peer_subset())
    assert distribution.quantile_convention is PeerQuantileConvention.TYPE_7_LINEAR
    assert distribution.central_statistic is PeerCentralStatistic.MEDIAN
    assert linear_quantile is shared_linear_quantile
    assert linear_quantile((10.0, 15.0, 20.0), 0.25) == 12.5


def test_observation_and_peer_issuer_ids_are_preserved_in_input_order():
    source = three_peer_subset()
    distribution = build_peer_multiple_distribution(source)
    assert distribution.valid_observation_ids == source.valid_observation_ids
    assert distribution.peer_issuer_ids == source.method_data_available_peer_issuer_ids


def test_enterprise_basis_and_ev_ebitda_method_are_preserved():
    distribution = build_peer_multiple_distribution(three_peer_subset())
    assert distribution.multiple_type is PeerMethod.EV_EBITDA
    assert distribution.valuation_basis is ValuationBasis.ENTERPRISE
    assert distribution.unit is MetricUnit.RATIO


def _corrupt_observation(field: str, value, *, index: int = 1):
    source = three_peer_subset()
    items = list(source.observations)
    object.__setattr__(items[index], field, value)
    object.__setattr__(source, "observations", tuple(items))
    return source


def test_duplicate_issuer_fails_closed_in_8c_without_averaging_listings():
    source = _corrupt_observation("peer_issuer_id", "issuer-peer-1")
    with pytest.raises(ValueError, match="duplicate peer issuer"):
        build_peer_multiple_distribution(source)


@pytest.mark.parametrize(
    "field,value,message",
    (
        ("target_security_id", "security-other-target", "target identity"),
        ("target_issuer_id", "issuer-other-target", "target identity"),
        ("peer_set_id", "peer-set:other", "peer_set_id"),
        ("analysis_as_of", NOW - timedelta(days=1), "analysis snapshot"),
        ("multiple_type", "P_E", "EV_EBITDA"),
        ("valuation_basis", ValuationBasis.EQUITY, "enterprise basis"),
        ("forward_period", ForwardPeriodSelection.FY2, "FY1 and FY2"),
        ("estimate_case", EstimateCase.LOW, "LOW/HIGH"),
        ("estimate_case", EstimateCase.HIGH, "LOW/HIGH"),
    ),
)
def test_mixed_target_peer_set_snapshot_method_basis_period_or_case_fails_closed(field, value, message):
    source = _corrupt_observation(field, value)
    with pytest.raises(ValueError, match=message):
        build_peer_multiple_distribution(source)


def test_same_fy1_policy_allows_different_peer_fiscal_period_ends():
    items = (
        observation(1, 10, forward_period_end=date(2027, 6, 30)),
        observation(2, 15, forward_period_end=date(2027, 9, 30)),
        observation(3, 20, forward_period_end=date(2027, 12, 31)),
    )
    distribution = build_peer_multiple_distribution(valuation_subset(items))
    assert distribution.status is PeerDistributionStatus.USABLE
    assert distribution.forward_period_policy is ForwardPeriodSelection.FY1


def test_dimensionless_ratios_from_different_underlying_currencies_coexist_without_fx():
    items = (
        observation(1, 10, currency="USD"),
        observation(2, 15, currency="EUR"),
        observation(3, 20, currency="JPY"),
    )
    distribution = build_peer_multiple_distribution(valuation_subset(items))
    assert distribution.median == 15
    assert distribution.unit is MetricUnit.RATIO


def test_distribution_service_neither_compares_absolute_inputs_nor_performs_fx():
    import stock_analyser.services.peer_valuation_distribution as service

    source = inspect.getsource(service).lower()
    assert ".enterprise_value" not in source
    assert ".forward_ebitda" not in source
    assert ".currency" not in source
    assert all(token not in source for token in ("exchange_rate", "forex", "currency_conversion"))


def test_high_finite_multiple_is_retained_and_no_observation_is_altered_or_excluded():
    source = three_peer_subset((10.0, 15.0, 1_000_000.0))
    distribution = build_peer_multiple_distribution(source)
    assert distribution.maximum == 1_000_000
    assert distribution.sample_count == 3
    assert distribution.valid_observation_ids == source.valid_observation_ids
    assert distribution.outlier_policy_id == PeerOutlierPolicy.NO_AUTOMATIC_REMOVAL.value
    assert not distribution.observations_altered
    assert not distribution.excluded_observation_ids
    assert not distribution.automatic_outlier_removal_applied


@pytest.mark.parametrize("value", [-1.0, 0.0, math.inf, -math.inf, math.nan])
def test_invalid_claimed_valid_multiple_fails_the_entire_distribution_closed(value):
    source = _corrupt_observation("ev_ebitda_multiple", value)
    with pytest.raises(ValueError, match="finite and positive"):
        build_peer_multiple_distribution(source)


def test_two_valid_observations_are_descriptive_but_not_usable():
    source = valuation_subset((observation(1, 10), observation(2, 20)))
    distribution = build_peer_multiple_distribution(source)
    assert distribution.status is PeerDistributionStatus.INSUFFICIENT
    assert distribution.sample_count == 2
    assert distribution.median == 15
    assert distribution.minimum_required_observations == 3


def test_zero_observations_produce_unavailable_without_statistics():
    distribution = build_peer_multiple_distribution(valuation_subset())
    assert distribution.status is PeerDistributionStatus.UNAVAILABLE
    assert distribution.sample_count == 0
    assert distribution.p25 is distribution.median is distribution.p75 is None
    assert distribution.minimum is distribution.maximum is distribution.mean is None
    assert distribution.population_standard_deviation is distribution.iqr is None


def test_safe_meta_shape_renders_unavailable_distribution_without_statistics():
    from stock_analyser.live_peer_audit import LivePeerAuditOutcome, render_live_peer_audit

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
        status=PeerSetStatus.INSUFFICIENT,
        policy_id="synthetic-peer-set-policy",
        selections=(),
        method_data_readiness=(),
    )
    subset = valuation_subset()
    distribution = build_peer_multiple_distribution(subset)
    rendered = render_live_peer_audit(LivePeerAuditOutcome(
        symbol="META",
        peer_set=peer_set,
        discovered_candidate_count=0,
        resolved_candidate_count=0,
        candidate_providers=(),
        candidate_contexts=(),
        unresolved_candidates=(),
        issues=(),
        peer_valuation_subset=subset,
        peer_multiple_distribution=distribution,
    ))
    assert "Peer EV/EBITDA distribution status: unavailable" in rendered
    assert "Peer EV/EBITDA distribution sample count: 0" in rendered
    assert "Peer EV/EBITDA P25: unavailable" in rendered
    assert "Peer EV/EBITDA median: unavailable" in rendered
    assert "Peer EV/EBITDA P75: unavailable" in rendered


def test_partial_8b_subset_with_three_valid_observations_cannot_become_usable():
    source = valuation_subset(
        three_peer_subset().observations,
        status=PeerValuationSubsetStatus.PARTIAL,
        unavailable_peer_ids=("issuer-peer-4",),
    )
    distribution = build_peer_multiple_distribution(source)
    assert distribution.source_subset_status is PeerValuationSubsetStatus.PARTIAL
    assert distribution.status is PeerDistributionStatus.PARTIAL
    assert distribution.sample_count == 3


def test_distribution_does_not_contain_or_mutate_economic_membership_statuses():
    import stock_analyser.services.peer_valuation_distribution as service

    source = three_peer_subset()
    before = source
    distribution = build_peer_multiple_distribution(source)
    assert source == before
    assert not hasattr(distribution, "peer_selection_status")
    assert "PeerSelectionStatus" not in inspect.getsource(service)


def test_distribution_policy_has_no_dispersion_threshold_or_alternative_sample_size():
    with pytest.raises(ValueError, match="exactly three"):
        PeerDistributionPolicy(minimum_valid_observations=2)
    policy = PeerDistributionPolicy()
    assert not hasattr(policy, "maximum_iqr_to_median")
    assert not hasattr(policy, "maximum_multiple")


def test_8c_contract_has_no_target_application_or_other_valuation_families():
    import stock_analyser.domain.peer_valuation as contracts
    import stock_analyser.services.peer_valuation_distribution as service

    source = (inspect.getsource(contracts) + inspect.getsource(service)).lower()
    forbidden = (
        "target_ebitda", "target_enterprise_value", "target_equity_value",
        "target_per_share", "fair_value", "current_price", "upside", "downside",
        "premium", "discounted_cash_flow", "own_history", "aggregation", "stance",
        "streamlit", "goog", "googl", "london", "peer_quality_score", "dispersion_score",
        "winsor", "sigma_filter", "iqr_deletion", "multiple_cap",
    )
    assert all(re.search(rf"\b{re.escape(token)}\b", source) is None for token in forbidden)


def test_8c_service_is_pure_and_default_path_makes_no_network_call(monkeypatch):
    import stock_analyser.services.peer_valuation_distribution as service

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    distribution = service.build_peer_multiple_distribution(three_peer_subset())
    source = inspect.getsource(service).lower()
    assert distribution.status is PeerDistributionStatus.USABLE
    assert all(token not in source for token in ("requests", "httpx", "urllib", "socket", "yfinance"))
