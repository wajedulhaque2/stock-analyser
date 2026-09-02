"""Deterministic macro selection and USD-only risk-free-rate resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from stock_analyser.domain import DataAvailability, MacroMetric, MacroObservation


@dataclass(frozen=True, slots=True)
class RiskFreeRateResult:
    currency: str
    availability: DataAvailability
    observation: MacroObservation | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be an uppercase three-letter code")
        if self.availability is DataAvailability.AVAILABLE:
            if self.observation is None or self.reason is not None:
                raise ValueError("available risk-free result requires an observation and no unavailable reason")
        elif self.observation is not None or not self.reason:
            raise ValueError("unavailable risk-free result requires a reason and no observation")


def latest_macro_on_or_before(
    observations: Iterable[MacroObservation],
    requested_date: date,
) -> MacroObservation | None:
    eligible = tuple(item for item in observations if item.observation_date <= requested_date)
    return max(eligible, key=lambda item: item.observation_date, default=None)


def resolve_risk_free_rate(
    currency: str,
    observations: Iterable[MacroObservation],
    *,
    as_of_at: datetime,
) -> RiskFreeRateResult:
    if as_of_at.tzinfo is None or as_of_at.utcoffset() is None:
        raise ValueError("as_of_at must be timezone-aware")
    if not isinstance(currency, str):
        raise ValueError("currency must be a string")
    normalized = currency.upper()
    if normalized != "USD":
        return RiskFreeRateResult(
            currency=normalized,
            availability=DataAvailability.UNAVAILABLE,
            reason=f"No approved {normalized} risk-free-rate source configured.",
        )
    candidates = tuple(
        item for item in observations
        if item.provider.lower() == "fred"
        and item.series_id == "DGS10"
        and item.metric is MacroMetric.TREASURY_YIELD
        and item.currency == "USD"
    )
    selected = latest_macro_on_or_before(candidates, as_of_at.date())
    if selected is None:
        return RiskFreeRateResult(
            currency="USD",
            availability=DataAvailability.UNAVAILABLE,
            reason="No valid FRED DGS10 observation exists on or before the requested as-of date.",
        )
    return RiskFreeRateResult(
        currency="USD",
        availability=DataAvailability.AVAILABLE,
        observation=selected,
    )
