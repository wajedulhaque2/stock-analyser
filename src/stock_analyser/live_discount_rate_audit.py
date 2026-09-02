"""Explicit, safe live/synthetic audit for Milestone 9B.1 cost-of-equity readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

from stock_analyser.domain import (
    BetaEvidence,
    CostOfEquityResult,
    DiscountRateReadiness,
    DiscountRateReadinessStatus,
    EquityRiskPremiumEvidence,
    RiskFreeRateEvidence,
    WaccInputReadiness,
)
from stock_analyser.live_smoke import build_live_transport
from stock_analyser.live_smoke import LiveYahooSource
from stock_analyser.providers import DamodaranAdapter, FredAdapter, SecretReference, YahooAdapter
from stock_analyser.providers.damodaran import (
    DAMODARAN_HOME_URL, DAMODARAN_RATINGS_URL, DAMODARAN_TAX_URL,
)
from stock_analyser.services import (
    SPY_TOTAL_RETURN_BENCHMARK,
    assess_discount_rate_readiness,
    assess_wacc_input_readiness,
    build_risk_free_rate_evidence,
    build_sourced_us_erp_evidence,
    calculate_cost_of_equity,
    calculate_regression_beta,
)


@dataclass(frozen=True, slots=True)
class LiveDiscountRateAuditOutcome:
    symbol: str
    valuation_currency: str
    target_security_id: str | None
    target_issuer_id: str | None
    risk_free: RiskFreeRateEvidence
    erp: EquityRiskPremiumEvidence | None
    beta: BetaEvidence | None
    cost_of_equity: CostOfEquityResult | None
    wacc: WaccInputReadiness | None
    readiness: DiscountRateReadiness | None
    safe_issues: tuple[str, ...]


class _LiveDamodaranSource:
    @staticmethod
    def _get(url: str) -> str:
        import requests
        response = requests.get(
            url,
            headers={"User-Agent": "stock-analyser-v1-safe-erp-audit/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        return response.text

    def current_us_implied_erp_page(self) -> str:
        return self._get(DAMODARAN_HOME_URL)

    def current_synthetic_rating_page(self) -> str:
        return self._get(DAMODARAN_RATINGS_URL)

    def current_country_tax_page(self) -> str:
        return self._get(DAMODARAN_TAX_URL)


def _month_start(day: date, months_back: int) -> date:
    index = day.year * 12 + day.month - 1 - months_back
    return date(index // 12, index % 12 + 1, 1)


def run_live_discount_rate_audit(
    symbol: str,
    valuation_currency: str,
    *,
    environment: Mapping[str, str],
    analysis_as_of,
    target_security_id: str | None = None,
    target_issuer_id: str | None = None,
) -> LiveDiscountRateAuditOutcome:
    """Fetch only the approved USD risk-free, ERP, and adjusted-history beta evidence."""
    normalized_symbol = symbol.strip().upper()
    currency = valuation_currency.strip().upper()
    issues: list[str] = []
    observations = ()
    erp = None
    beta = None
    if currency == "USD":
        if environment.get("FRED_API_KEY"):
            adapter = FredAdapter(
                build_live_transport(),
                credential=SecretReference("fred", lambda: environment["FRED_API_KEY"]),
            )
            result = adapter.fetch_dgs10(source_as_of_at=analysis_as_of)
            observations = result.observations
            issues.extend(item.reason for item in result.issues)
        else:
            issues.append("FRED credential configuration is unavailable.")
        erp_result = DamodaranAdapter(_LiveDamodaranSource()).fetch_us_implied_erp()
        issues.extend(item.reason for item in erp_result.issues)
        erp = build_sourced_us_erp_evidence(
            erp_result.observations, analysis_as_of=analysis_as_of,
        )
        if erp is None:
            issues.append("Approved NYU Stern adjusted-payout US implied ERP evidence is unavailable.")
        if target_security_id and target_issuer_id:
            history_start = _month_start(analysis_as_of.date(), 60)
            history_end = analysis_as_of.date() + timedelta(days=1)
            yahoo = YahooAdapter(LiveYahooSource())
            target_history = yahoo.fetch_adjusted_price_history(
                target_security_id, normalized_symbol,
                start=history_start, end=history_end, expected_currency="USD",
            )
            benchmark_history = yahoo.fetch_adjusted_price_history(
                SPY_TOTAL_RETURN_BENCHMARK.benchmark_id,
                SPY_TOTAL_RETURN_BENCHMARK.symbol,
                start=history_start, end=history_end, expected_currency="USD",
            )
            issues.extend(item.reason for item in (*target_history.issues, *benchmark_history.issues))
            beta = calculate_regression_beta(
                target_security_id, target_issuer_id, normalized_symbol,
                target_history.observations, benchmark_history.observations,
                analysis_as_of=analysis_as_of,
            )
    risk_free = build_risk_free_rate_evidence(
        currency, observations, analysis_as_of=analysis_as_of,
    )
    cost = wacc = readiness = None
    if target_security_id and target_issuer_id:
        cost = calculate_cost_of_equity(
            target_security_id, target_issuer_id, currency,
            risk_free, erp, beta, analysis_as_of=analysis_as_of,
        )
        wacc = assess_wacc_input_readiness(cost)
        readiness = assess_discount_rate_readiness(risk_free, erp, beta, cost, wacc)
    else:
        issues.append("Canonical target security/issuer identity was not supplied; cost-of-equity assembly was withheld.")
    return LiveDiscountRateAuditOutcome(
        symbol=normalized_symbol, valuation_currency=currency,
        target_security_id=target_security_id, target_issuer_id=target_issuer_id,
        risk_free=risk_free, erp=erp, beta=beta, cost_of_equity=cost,
        wacc=wacc, readiness=readiness, safe_issues=tuple(dict.fromkeys(issues)),
    )


def _text(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def render_live_discount_rate_audit(outcome: LiveDiscountRateAuditOutcome) -> str:
    risk = outcome.risk_free
    cost_status = (
        outcome.cost_of_equity.status.value
        if outcome.cost_of_equity else DiscountRateReadinessStatus.NOT_READY.value
    )
    wacc_status = (
        outcome.wacc.status.value if outcome.wacc else DiscountRateReadinessStatus.NOT_READY.value
    )
    missing = (
        outcome.readiness.missing_requirements
        if outcome.readiness else ("canonical target identity", "explicit ERP evidence", "eligible beta evidence")
    )
    erp = outcome.erp
    beta = outcome.beta
    lines = (
        "V1 DISCOUNT-RATE READINESS AUDIT",
        f"Target symbol: {outcome.symbol}",
        f"Target security ID: {_text(outcome.target_security_id)}",
        f"Target issuer ID: {_text(outcome.target_issuer_id)}",
        f"Valuation cash-flow currency: {outcome.valuation_currency}",
        f"Risk-free status: {risk.status.value}",
        f"Risk-free source: {risk.source_name} / {risk.source_dataset}",
        f"Risk-free observation date: {_text(risk.observation_date)}",
        f"Risk-free decimal value: {_text(risk.value)}",
        f"Risk-free currency applicability: {risk.currency_applicability.value}",
        f"Risk-free reason: {risk.reason}",
        f"ERP status: {erp.status.value if erp else 'unavailable'}",
        f"ERP source: {erp.source_name if erp else 'unavailable'}",
        f"ERP methodology: {erp.methodology_label if erp else 'unavailable'}",
        f"ERP observation date: {_text(erp.source_date if erp else None)}",
        f"ERP decimal value: {_text(erp.value if erp else None)}",
        f"Beta status: {beta.status.value if beta else 'unavailable'}",
        f"Beta method: {beta.source_method.value if beta else 'unavailable'}",
        f"Beta benchmark: {_text(beta.benchmark_name if beta else None)} ({_text(beta.benchmark_symbol if beta else None)})",
        f"Beta lookback/frequency: {_text(beta.lookback if beta else None)} / {_text(beta.return_frequency if beta else None)}",
        f"Beta return convention: {_text(beta.return_convention.value if beta and beta.return_convention else None)}",
        f"Beta sample count: {_text(beta.sample_count if beta else None)}",
        f"Beta regression period: {_text(beta.regression_start if beta else None)} / {_text(beta.regression_end if beta else None)}",
        f"Beta value: {_text(beta.value if beta else None)}",
        f"Beta alpha: {_text(beta.alpha if beta else None)}",
        f"Beta R-squared: {_text(beta.r_squared if beta else None)}",
        f"Cost-of-equity readiness: {cost_status}",
        f"Cost of equity: {_text(outcome.cost_of_equity.cost_of_equity if outcome.cost_of_equity else None)}",
        f"WACC prerequisite readiness: {wacc_status}",
        "Production WACC status: not_ready (calculation is deferred; no numeric WACC exists)",
        f"Missing requirements: {'; '.join(missing)}",
        f"Safe issue count: {len(outcome.safe_issues)}",
        "No WACC, DCF, reverse DCF, current price, FX conversion, valuation, aggregation, stance, or UI output was produced.",
    )
    return "\n".join(lines)
