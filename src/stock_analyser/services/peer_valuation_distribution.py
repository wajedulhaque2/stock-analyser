"""Cross-sectional peer multiple distributions over valid 8B observations only."""

from __future__ import annotations

from dataclasses import dataclass
import math

from stock_analyser.domain import (
    EstimateCase,
    MetricUnit,
    PeerCentralStatistic,
    PeerDistributionStatus,
    PeerMethod,
    PeerMethodEvidenceStatus,
    PeerMultipleDistribution,
    PeerOutlierPolicy,
    PeerQuantileConvention,
    PeerValuationSubset,
    PeerValuationSubsetStatus,
    ValuationBasis,
    stable_peer_multiple_distribution_id,
)

from .statistics import linear_quantile


@dataclass(frozen=True, slots=True)
class PeerDistributionPolicy:
    minimum_valid_observations: int = 3
    quantile_convention: PeerQuantileConvention = PeerQuantileConvention.TYPE_7_LINEAR
    central_statistic: PeerCentralStatistic = PeerCentralStatistic.MEDIAN
    outlier_policy: PeerOutlierPolicy = PeerOutlierPolicy.NO_AUTOMATIC_REMOVAL
    policy_id: str = "peer-ev-ebitda-distribution-v1"

    def __post_init__(self) -> None:
        if self.minimum_valid_observations != 3:
            raise ValueError("Milestone 8C requires exactly three independent issuer observations")
        if self.quantile_convention is not PeerQuantileConvention.TYPE_7_LINEAR:
            raise ValueError("Milestone 8C supports Type-7 linear quantiles only")
        if self.central_statistic is not PeerCentralStatistic.MEDIAN:
            raise ValueError("median is the primary peer multiple")
        if self.outlier_policy is not PeerOutlierPolicy.NO_AUTOMATIC_REMOVAL:
            raise ValueError("Milestone 8C performs no automatic outlier removal")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")


DEFAULT_PEER_DISTRIBUTION_POLICY = PeerDistributionPolicy()


def _validate_input_contract(subset: PeerValuationSubset, policy: PeerDistributionPolicy) -> None:
    if subset.method is not PeerMethod.EV_EBITDA:
        raise ValueError("Milestone 8C supports EV_EBITDA only")
    if subset.estimate_case is not EstimateCase.AVERAGE:
        raise ValueError("peer distributions require AVERAGE observations")
    if subset.minimum_required_valid_observations != policy.minimum_valid_observations:
        raise ValueError("8B and 8C minimum-observation policies must match")
    observations = tuple(subset.observations)
    if len(observations) != subset.valid_observation_count:
        raise ValueError("8B subset observation count is inconsistent")
    if tuple(item.observation_id for item in observations) != tuple(subset.valid_observation_ids):
        raise ValueError("8B subset valid observation IDs are inconsistent")
    if len({item.peer_issuer_id for item in observations}) != len(observations):
        raise ValueError("duplicate peer issuer IDs fail the distribution closed")
    for observation in observations:
        if (
            observation.target_security_id != subset.target_security_id
            or observation.target_issuer_id != subset.target_issuer_id
        ):
            raise ValueError("all peer observations must share the subset target identity")
        if observation.peer_set_id != subset.peer_set_id:
            raise ValueError("all peer observations must share the subset peer_set_id")
        if observation.analysis_as_of != subset.analysis_as_of:
            raise ValueError("all peer observations must share the subset analysis snapshot")
        if observation.multiple_type is not subset.method or observation.multiple_type is not PeerMethod.EV_EBITDA:
            raise ValueError("all peer observations must use EV_EBITDA")
        if observation.valuation_basis is not ValuationBasis.ENTERPRISE:
            raise ValueError("all peer observations must preserve enterprise basis")
        if observation.forward_period is not subset.selected_forward_period:
            raise ValueError("FY1 and FY2 observations cannot be mixed")
        if observation.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("LOW/HIGH observations cannot enter the peer distribution")
        if observation.unit is not MetricUnit.RATIO:
            raise ValueError("peer multiples must be dimensionless ratios")
        if observation.status is not PeerMethodEvidenceStatus.AVAILABLE:
            raise ValueError("only available 8B observations may enter the distribution")
        value = observation.ev_ebitda_multiple
        if isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0:
            raise ValueError("valid 8B peer multiple must be finite and positive")
    if subset.status is PeerValuationSubsetStatus.UNAVAILABLE and observations:
        raise ValueError("an unavailable 8B subset cannot contain valid observations")
    if subset.status is PeerValuationSubsetStatus.USABLE and len(observations) < policy.minimum_valid_observations:
        raise ValueError("a usable 8B subset must contain the configured minimum observations")


def build_peer_multiple_distribution(
    subset: PeerValuationSubset,
    *,
    policy: PeerDistributionPolicy = DEFAULT_PEER_DISTRIBUTION_POLICY,
) -> PeerMultipleDistribution:
    """Describe every valid observation in one 8B subset without altering the sample."""
    if not isinstance(subset, PeerValuationSubset):
        raise TypeError("subset must be a PeerValuationSubset")
    _validate_input_contract(subset, policy)
    observations = tuple(subset.observations)
    count = len(observations)
    if not count:
        status = PeerDistributionStatus.UNAVAILABLE
        statistics = (None,) * 11
    else:
        values = tuple(sorted(float(item.ev_ebitda_multiple) for item in observations))
        p25 = linear_quantile(values, 0.25)
        median = linear_quantile(values, 0.50)
        p75 = linear_quantile(values, 0.75)
        minimum = values[0]
        maximum = values[-1]
        mean = math.fsum(values) / count
        population_variance = math.fsum((value - mean) ** 2 for value in values) / count
        population_standard_deviation = math.sqrt(population_variance)
        iqr = p75 - p25
        statistics = (
            p25, median, p75, minimum, maximum, mean,
            population_standard_deviation, iqr, iqr / median,
            maximum / median, minimum / median,
        )
        if subset.status is PeerValuationSubsetStatus.USABLE:
            status = PeerDistributionStatus.USABLE
        elif subset.status is PeerValuationSubsetStatus.PARTIAL:
            status = PeerDistributionStatus.PARTIAL
        else:
            status = PeerDistributionStatus.INSUFFICIENT
    warnings = []
    if count and count <= policy.minimum_valid_observations:
        warnings.append(
            f"Small cross-sectional sample: {count} independent issuer observations; statistics are descriptive."
        )
    if subset.status is not PeerValuationSubsetStatus.USABLE:
        warnings.append(
            f"Upstream peer valuation subset is {subset.status.value}; distribution cannot become USABLE."
        )
    warnings.append("No valid observation was altered or automatically excluded.")
    provenance = tuple(dict.fromkeys(
        item for observation in observations for item in observation.provenance
    ))
    return PeerMultipleDistribution(
        distribution_id=stable_peer_multiple_distribution_id(
            subset.subset_id, subset.peer_set_id, subset.method.value,
            subset.analysis_as_of.isoformat(), subset.selected_forward_period.value,
            policy.policy_id, *subset.valid_observation_ids,
        ),
        target_security_id=subset.target_security_id,
        target_issuer_id=subset.target_issuer_id,
        peer_set_id=subset.peer_set_id,
        peer_valuation_subset_id=subset.subset_id,
        multiple_type=PeerMethod.EV_EBITDA,
        valuation_basis=ValuationBasis.ENTERPRISE,
        analysis_as_of=subset.analysis_as_of,
        forward_period_policy=subset.selected_forward_period,
        estimate_case=EstimateCase.AVERAGE,
        valid_observation_ids=tuple(item.observation_id for item in observations),
        peer_issuer_ids=tuple(item.peer_issuer_id for item in observations),
        sample_count=count,
        p25=statistics[0],
        median=statistics[1],
        p75=statistics[2],
        minimum=statistics[3],
        maximum=statistics[4],
        mean=statistics[5],
        population_standard_deviation=statistics[6],
        iqr=statistics[7],
        iqr_to_median=statistics[8],
        max_to_median=statistics[9],
        min_to_median=statistics[10],
        minimum_required_observations=policy.minimum_valid_observations,
        source_subset_status=subset.status,
        status=status,
        quantile_convention=policy.quantile_convention,
        central_statistic=policy.central_statistic,
        outlier_policy_id=policy.outlier_policy.value,
        policy_id=policy.policy_id,
        provenance=provenance,
        issues=subset.issues,
        warnings=tuple(warnings),
    )
