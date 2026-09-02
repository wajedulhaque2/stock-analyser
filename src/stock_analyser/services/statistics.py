"""Pure deterministic descriptive-statistics helpers shared by valuation families."""

from __future__ import annotations

import math


def linear_quantile(sorted_values: tuple[float, ...], quantile: float) -> float:
    """Type-7/linear quantile: position=(n-1)q with linear interpolation."""
    if not sorted_values:
        raise ValueError("quantile requires at least one value")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight)
