from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class StockData:
    ticker: str
    info: dict[str, Any] = field(default_factory=dict)
    fast_info: dict[str, Any] = field(default_factory=dict)
    history_metadata: dict[str, Any] = field(default_factory=dict)
    price_currency: str | None = None
    financial_currency: str | None = None
    price_scale_applied: float = 1.0
    risk_free_rate: float | None = None
    history: pd.DataFrame = field(default_factory=pd.DataFrame)
    income: pd.DataFrame = field(default_factory=pd.DataFrame)
    ttm_income: pd.DataFrame = field(default_factory=pd.DataFrame)
    balance: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarterly_balance: pd.DataFrame = field(default_factory=pd.DataFrame)
    cashflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    ttm_cashflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarterly_income: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarterly_cashflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    earnings_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    earnings_estimate: pd.DataFrame = field(default_factory=pd.DataFrame)
    revenue_estimate: pd.DataFrame = field(default_factory=pd.DataFrame)
    eps_trend: pd.DataFrame = field(default_factory=pd.DataFrame)
    eps_revisions: pd.DataFrame = field(default_factory=pd.DataFrame)
    growth_estimates: pd.DataFrame = field(default_factory=pd.DataFrame)
    analyst_targets: dict[str, Any] = field(default_factory=dict)
    recommendations: pd.DataFrame = field(default_factory=pd.DataFrame)
    valuation_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    errors: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Canonical display name used by downstream integrations."""
        return str(self.info.get("longName") or self.info.get("shortName") or self.ticker)
