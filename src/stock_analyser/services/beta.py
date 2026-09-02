"""Auditable five-year monthly market-regression beta construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable

from stock_analyser.domain import (
    AdjustedPriceObservation,
    BetaDefinition,
    BetaEvidence,
    BetaSourceMethod,
    DiscountRateEvidenceStatus,
    MarketBenchmark,
    MarketReturnFrequency,
    MetricUnit,
    PriceReturnSemantics,
    Provenance,
    ReturnConvention,
    stable_beta_evidence_id,
)


SPY_TOTAL_RETURN_BENCHMARK = MarketBenchmark(
    benchmark_id="market-benchmark:us:spy-adjusted-total-return",
    symbol="SPY",
    name="SPDR S&P 500 ETF Trust adjusted total-return proxy",
    market="United States broad large-cap equity market",
    currency="USD",
    return_semantics=PriceReturnSemantics.SPLIT_AND_DISTRIBUTION_ADJUSTED,
)


@dataclass(frozen=True, slots=True)
class RegressionBetaPolicy:
    horizon_months: int = 60
    minimum_aligned_returns: int = 36
    allow_non_positive_beta: bool = False
    policy_id: str = "us-5y-monthly-market-regression-v1"

    def __post_init__(self) -> None:
        for name in ("horizon_months", "minimum_aligned_returns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError(f"{name} must be an integer of at least two")
        if self.minimum_aligned_returns > self.horizon_months:
            raise ValueError("minimum aligned returns cannot exceed the requested horizon")
        if not isinstance(self.allow_non_positive_beta, bool):
            raise TypeError("allow_non_positive_beta must be boolean")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")


DEFAULT_REGRESSION_BETA_POLICY = RegressionBetaPolicy()


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _deduplicate(
    observations: Iterable[AdjustedPriceObservation],
    *,
    instrument_id: str,
    analysis_as_of: datetime,
    currency: str,
    semantics: PriceReturnSemantics,
) -> dict[date, AdjustedPriceObservation]:
    selected: dict[date, AdjustedPriceObservation] = {}
    for item in observations:
        if not isinstance(item, AdjustedPriceObservation):
            raise TypeError("price evidence must use AdjustedPriceObservation")
        if (
            item.instrument_id != instrument_id
            or item.observation_date > analysis_as_of.date()
            or item.as_of_at > analysis_as_of
            or item.currency != currency
            or item.return_semantics is not semantics
        ):
            continue
        previous = selected.get(item.observation_date)
        if previous is not None and not math.isclose(previous.adjusted_close, item.adjusted_close, rel_tol=0, abs_tol=0):
            raise ValueError("conflicting adjusted prices exist for one instrument/date")
        if previous is None or item.observation_id < previous.observation_id:
            selected[item.observation_date] = item
    return selected


def _unavailable_beta(
    security_id: str,
    issuer_id: str,
    analysis_as_of: datetime,
    provider_symbol: str,
    benchmark: MarketBenchmark,
    policy: RegressionBetaPolicy,
    reason: str,
) -> BetaEvidence:
    return BetaEvidence(
        evidence_id=stable_beta_evidence_id(
            security_id, issuer_id, analysis_as_of.isoformat(), benchmark.benchmark_id, reason, policy.policy_id,
        ),
        security_id=security_id, issuer_id=issuer_id, value=None, unit=MetricUnit.RATIO,
        definition=BetaDefinition.UNVERIFIED,
        source_method=BetaSourceMethod.US_5Y_MONTHLY_MARKET_REGRESSION,
        provider="yahoo", provider_symbol=provider_symbol,
        observation_date=None, source_as_of=None, analysis_as_of=analysis_as_of,
        benchmark=benchmark.name, lookback=f"{policy.horizon_months} months",
        return_frequency=MarketReturnFrequency.MONTHLY.value,
        methodology_label=(
            "OLS with intercept on aligned month-end simple total returns; no Blume adjustment, "
            "shrinkage, industry/leverage adjustment, or cap"
        ),
        status=DiscountRateEvidenceStatus.UNAVAILABLE, reason=reason,
        provenance=(), policy_id=policy.policy_id,
    )


def calculate_regression_beta(
    security_id: str,
    issuer_id: str,
    provider_symbol: str,
    target_prices: Iterable[AdjustedPriceObservation],
    benchmark_prices: Iterable[AdjustedPriceObservation],
    *,
    analysis_as_of: datetime,
    benchmark: MarketBenchmark = SPY_TOTAL_RETURN_BENCHMARK,
    policy: RegressionBetaPolicy = DEFAULT_REGRESSION_BETA_POLICY,
) -> BetaEvidence:
    """Calculate one unadjusted OLS equity beta from aligned monthly simple returns."""
    if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
        raise ValueError("analysis_as_of must be timezone-aware")
    if not isinstance(benchmark, MarketBenchmark):
        raise TypeError("benchmark must use MarketBenchmark")
    if not isinstance(policy, RegressionBetaPolicy):
        raise TypeError("policy must use RegressionBetaPolicy")
    target = _deduplicate(
        target_prices, instrument_id=security_id, analysis_as_of=analysis_as_of,
        currency=benchmark.currency, semantics=benchmark.return_semantics,
    )
    market = _deduplicate(
        benchmark_prices, instrument_id=benchmark.benchmark_id, analysis_as_of=analysis_as_of,
        currency=benchmark.currency, semantics=benchmark.return_semantics,
    )
    common_dates = sorted(set(target).intersection(market))
    if not common_dates:
        return _unavailable_beta(
            security_id, issuer_id, analysis_as_of, provider_symbol, benchmark, policy,
            "No aligned compatible target/benchmark adjusted price observations were available.",
        )
    latest_month = min(_month_index(analysis_as_of.date()), max(_month_index(day) for day in common_dates))
    first_price_month = latest_month - policy.horizon_months
    month_ends: dict[int, date] = {}
    for day in common_dates:
        month = _month_index(day)
        if first_price_month <= month <= latest_month:
            month_ends[month] = max(day, month_ends.get(month, day))
    aligned_returns: list[tuple[date, float, float, tuple[str, str, str, str]]] = []
    ordered_months = sorted(month_ends)
    for previous_month, current_month in zip(ordered_months, ordered_months[1:]):
        if current_month != previous_month + 1:
            continue
        previous_day = month_ends[previous_month]
        current_day = month_ends[current_month]
        target_return = target[current_day].adjusted_close / target[previous_day].adjusted_close - 1.0
        benchmark_return = market[current_day].adjusted_close / market[previous_day].adjusted_close - 1.0
        if not math.isfinite(target_return) or not math.isfinite(benchmark_return):
            continue
        aligned_returns.append((
            current_day, target_return, benchmark_return,
            (
                target[previous_day].observation_id, target[current_day].observation_id,
                market[previous_day].observation_id, market[current_day].observation_id,
            ),
        ))
    if len(aligned_returns) > policy.horizon_months:
        aligned_returns = aligned_returns[-policy.horizon_months:]
    sample_count = len(aligned_returns)
    if sample_count < 2:
        return _unavailable_beta(
            security_id, issuer_id, analysis_as_of, provider_symbol, benchmark, policy,
            "Fewer than two aligned consecutive monthly returns were available; no regression was calculated.",
        )
    target_returns = [item[1] for item in aligned_returns]
    benchmark_returns = [item[2] for item in aligned_returns]
    x_mean = sum(benchmark_returns) / sample_count
    y_mean = sum(target_returns) / sample_count
    sxx = sum((value - x_mean) ** 2 for value in benchmark_returns)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(benchmark_returns, target_returns))
    benchmark_variance = sxx / (sample_count - 1)
    beta = sxy / sxx if sxx > 0 and math.isfinite(sxx) else None
    alpha = y_mean - beta * x_mean if beta is not None else y_mean
    fitted = [alpha + (beta or 0.0) * value for value in benchmark_returns]
    residuals = [actual - estimate for actual, estimate in zip(target_returns, fitted)]
    sse = sum(value * value for value in residuals)
    sst = sum((value - y_mean) ** 2 for value in target_returns)
    r_squared = 1.0 - sse / sst if sst > 0 else (1.0 if math.isclose(sse, 0.0, abs_tol=1e-15) else 0.0)
    residual_standard_error = math.sqrt(sse / (sample_count - 2)) if sample_count > 2 else None
    beta_standard_error = (
        residual_standard_error / math.sqrt(sxx)
        if residual_standard_error is not None and sxx > 0 else None
    )
    source_ids = tuple(dict.fromkeys(identifier for item in aligned_returns for identifier in item[3]))
    used_observations = tuple(
        item for item in (*target.values(), *market.values()) if item.observation_id in source_ids
    )
    retrieved_at = max(item.retrieved_at for item in used_observations)
    source_as_of = max(item.as_of_at for item in used_observations)
    provenance = Provenance(
        provider="internal_regression",
        endpoint_or_dataset="yahoo_adjusted_price_history_monthly_regression",
        provider_symbol=f"{provider_symbol}|{benchmark.symbol}",
        retrieved_at=retrieved_at, as_of_at=source_as_of,
        transformation_steps=(
            "selected the final common target/benchmark adjusted close in each calendar month",
            "did not interpolate, forward-fill, or bridge missing calendar months",
            "calculated aligned simple total returns as current adjusted close divided by prior adjusted close minus one",
            "estimated ordinary least squares with intercept",
            "applied no Blume adjustment, shrinkage, industry adjustment, leverage adjustment, or beta cap",
        ),
        input_observation_ids=source_ids,
        configuration_or_override_id=policy.policy_id,
        source_metric=BetaSourceMethod.US_5Y_MONTHLY_MARKET_REGRESSION.value,
    )
    finite_regression = beta is not None and all(math.isfinite(value) for value in (beta, alpha, r_squared, benchmark_variance))
    enough = sample_count >= policy.minimum_aligned_returns
    positive_or_allowed = beta is not None and (beta > 0 or policy.allow_non_positive_beta)
    eligible = finite_regression and benchmark_variance > 0 and enough and positive_or_allowed
    if not finite_regression or benchmark_variance <= 0:
        reason = "Benchmark variance was zero/non-finite or regression diagnostics were not finite; beta is not eligible."
    elif not enough:
        reason = (
            f"Only {sample_count} aligned monthly returns were available, below the centralized "
            f"minimum of {policy.minimum_aligned_returns}."
        )
    elif not positive_or_allowed:
        reason = "Non-positive regression beta is retained but ineligible under the initial operating-equity CAPM policy."
    else:
        reason = "Five-year monthly adjusted-total-return OLS beta satisfies the centralized eligibility policy."
    return BetaEvidence(
        evidence_id=stable_beta_evidence_id(
            security_id, issuer_id, benchmark.benchmark_id, aligned_returns[0][0].isoformat(),
            aligned_returns[-1][0].isoformat(), sample_count, beta, policy.policy_id,
        ),
        security_id=security_id, issuer_id=issuer_id, value=beta, unit=MetricUnit.RATIO,
        definition=BetaDefinition.REGRESSION_EQUITY_BETA,
        source_method=BetaSourceMethod.US_5Y_MONTHLY_MARKET_REGRESSION,
        provider="yahoo", provider_symbol=provider_symbol,
        observation_date=aligned_returns[-1][0], source_as_of=source_as_of,
        analysis_as_of=analysis_as_of, benchmark=benchmark.name,
        lookback=f"{policy.horizon_months} months",
        return_frequency=MarketReturnFrequency.MONTHLY.value,
        methodology_label=(
            "OLS with intercept on aligned month-end simple total returns from Yahoo auto-adjusted history; "
            "observed levered equity beta with no adjustment or cap"
        ),
        status=DiscountRateEvidenceStatus.ELIGIBLE if eligible else DiscountRateEvidenceStatus.UNVERIFIED,
        reason=reason, provenance=(provenance,), policy_id=policy.policy_id,
        benchmark_id=benchmark.benchmark_id, benchmark_symbol=benchmark.symbol,
        benchmark_name=benchmark.name, benchmark_market=benchmark.market,
        benchmark_currency=benchmark.currency,
        return_convention=ReturnConvention.SIMPLE_TOTAL_RETURN,
        return_semantics=benchmark.return_semantics,
        sample_count=sample_count, regression_start=aligned_returns[0][0],
        regression_end=aligned_returns[-1][0], alpha=alpha, r_squared=r_squared,
        residual_standard_error=residual_standard_error,
        beta_standard_error=beta_standard_error,
        benchmark_variance=benchmark_variance,
        source_observation_ids=source_ids,
    )
