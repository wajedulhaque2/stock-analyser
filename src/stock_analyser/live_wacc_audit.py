"""One opt-in, allowlisted live USD WACC evidence audit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from stock_analyser.domain import (
    ActualSelectionStatus, CapitalValueEvidence, CompanyClassEligibilityEvidence,
    CostOfDebtEvidence, Frequency, InterestCoverageEvidence, MarginalTaxRateEvidence,
    MetricId, MetricObservation,
    OtherEnterpriseClaimsEvidence, WaccInputReadiness, WaccResult,
)
from stock_analyser.live_discount_rate_audit import (
    LiveDiscountRateAuditOutcome, _LiveDamodaranSource, run_live_discount_rate_audit,
)
from stock_analyser.live_smoke import LiveFiscalSource, _provisional_identity
from stock_analyser.providers import DamodaranAdapter, FiscalAdapter, SecretReference
from stock_analyser.services import (
    build_debt_value_evidence, build_equity_value_evidence,
    build_company_class_eligibility, build_damodaran_operating_profit_equivalence,
    build_interest_coverage_evidence, build_marginal_tax_evidence,
    build_other_claims_evidence, build_synthetic_cost_of_debt, calculate_wacc,
    select_canonical_actual,
)


@dataclass(frozen=True, slots=True)
class LiveWaccAuditOutcome:
    discount_rate: LiveDiscountRateAuditOutcome
    equity: CapitalValueEvidence | None
    debt: CapitalValueEvidence | None
    coverage: InterestCoverageEvidence | None
    cost_of_debt: CostOfDebtEvidence | None
    tax: MarginalTaxRateEvidence | None
    other_claims: OtherEnterpriseClaimsEvidence | None
    result: WaccResult | None
    readiness: WaccInputReadiness | None
    safe_issues: tuple[str, ...]
    company_class: CompanyClassEligibilityEvidence | None = None
    accounting_observations: tuple[MetricObservation, ...] = ()
    standardized_observations: tuple[MetricObservation, ...] = ()
    accounting_capability_status: str = "unavailable"


def _latest(rows, metric, *, analysis_as_of, frequency=None):
    candidates = tuple(
        row for row in rows
        if row.metric_id is metric and (frequency is None or row.frequency is frequency)
        and row.period_end is not None and row.period_end <= analysis_as_of.date()
    )
    if not candidates:
        return None
    period_end = max(row.period_end for row in candidates)
    frequencies = {row.frequency for row in candidates if row.period_end == period_end}
    if frequency is None and len(frequencies) != 1:
        return None
    selected_frequency = frequency or next(iter(frequencies))
    selection = select_canonical_actual(
        candidates, metric_id=metric, frequency=selected_frequency,
        period_end=period_end, analysis_as_of=analysis_as_of,
    )
    return selection.selected_observation if selection.status is ActualSelectionStatus.SELECTED else None


def run_live_wacc_audit(
    symbol: str, valuation_currency: str, *, environment: Mapping[str, str],
    analysis_as_of, target_security_id: str, target_issuer_id: str,
) -> LiveWaccAuditOutcome:
    discount = run_live_discount_rate_audit(
        symbol, valuation_currency, environment=environment, analysis_as_of=analysis_as_of,
        target_security_id=target_security_id, target_issuer_id=target_issuer_id,
    )
    issues = list(discount.safe_issues)
    if valuation_currency.upper() != "USD":
        issues.append("Milestone 9B.2 supports USD only.")
        return LiveWaccAuditOutcome(discount, None, None, None, None, None, None, None, None,
                                    tuple(dict.fromkeys(issues)))
    if not environment.get("FISCAL_API_KEY") or discount.cost_of_equity is None:
        issues.append("Fiscal credential or canonical cost-of-equity result is unavailable.")
        return LiveWaccAuditOutcome(discount, None, None, None, None, None, None, None, None,
                                    tuple(dict.fromkeys(issues)))

    fiscal = FiscalAdapter(
        LiveFiscalSource(), credential=SecretReference("fiscal", lambda: environment["FISCAL_API_KEY"]),
    )
    identity_result = fiscal.fetch_identity(symbol)
    candidate = identity_result.candidate
    identity = replace(
        _provisional_identity(symbol), security_id=target_security_id, issuer_id=target_issuer_id,
        issuer_domicile=candidate.issuer_domicile if candidate and candidate.issuer_domicile else "Unknown",
        sector=candidate.sector if candidate and candidate.sector else "Unknown",
        industry=candidate.industry if candidate and candidate.industry else "Unknown",
        reporting_currency=candidate.reporting_currency if candidate and candidate.reporting_currency else "USD",
        company_type=candidate.company_type if candidate else None,
    )
    actuals = fiscal.fetch_standardized_actuals(identity, analysis_as_of=analysis_as_of)
    bridge = fiscal.fetch_enterprise_bridge(identity, analysis_as_of=analysis_as_of)
    accounting = fiscal.fetch_wacc_accounting_evidence(identity, analysis_as_of=analysis_as_of)
    issues.extend(item.reason for item in (*identity_result.issues, *actuals.issues, *bridge.issues, *accounting.issues))
    market_cap = _latest(bridge.observations, MetricId.MARKET_CAP, analysis_as_of=analysis_as_of)
    gross_debt = (
        _latest(accounting.observations, MetricId.GROSS_DEBT, analysis_as_of=analysis_as_of,
                frequency=Frequency.POINT_IN_TIME)
        or _latest(actuals.observations, MetricId.GROSS_DEBT, analysis_as_of=analysis_as_of)
    )
    tev = _latest(bridge.observations, MetricId.ENTERPRISE_VALUE, analysis_as_of=analysis_as_of)
    net_debt = _latest(
        accounting.observations, MetricId.NET_DEBT, analysis_as_of=analysis_as_of,
        frequency=Frequency.POINT_IN_TIME,
    )
    equity = build_equity_value_evidence(market_cap, analysis_as_of=analysis_as_of)
    company_class = build_company_class_eligibility(identity, equity)
    issues.extend(company_class.issues)
    debt = build_debt_value_evidence(
        gross_debt, analysis_as_of=analysis_as_of, company_class_evidence=company_class,
    )
    equivalence = build_damodaran_operating_profit_equivalence()
    coverage = build_interest_coverage_evidence(
        actuals.observations, analysis_as_of=analysis_as_of,
        operating_profit_equivalence=equivalence,
    )
    external = DamodaranAdapter(_LiveDamodaranSource())
    rating_result = external.fetch_large_nonfinancial_rating_bands()
    tax_result = external.fetch_country_marginal_tax_rates()
    issues.extend(item.reason for item in (*rating_result.issues, *tax_result.issues))
    cost_of_debt = build_synthetic_cost_of_debt(
        coverage, rating_result.observations, discount.risk_free,
        company_class_evidence=company_class,
    )
    tax = build_marginal_tax_evidence(
        candidate.issuer_domicile if candidate else None, tax_result.observations,
        analysis_as_of=analysis_as_of,
    )
    other_claims = build_other_claims_evidence(
        tev, market_cap, net_debt, analysis_as_of=analysis_as_of,
    )
    result, readiness = calculate_wacc(
        discount.cost_of_equity, equity, debt, coverage, cost_of_debt, tax, other_claims,
    )
    issues.extend(result.issues)
    return LiveWaccAuditOutcome(
        discount, equity, debt, coverage, cost_of_debt, tax, other_claims,
        result, readiness, tuple(dict.fromkeys(issues)), company_class,
        tuple(accounting.observations), tuple(actuals.observations),
        accounting.capabilities[0].status.value if accounting.capabilities else "unavailable",
    )


def _text(value) -> str:
    return "unavailable" if value is None else format(value, ".15g") if isinstance(value, float) else str(value)


def render_live_wacc_audit(outcome: LiveWaccAuditOutcome) -> str:
    d, e, debt, c, cod, tax, claims, result = (
        outcome.discount_rate, outcome.equity, outcome.debt, outcome.coverage,
        outcome.cost_of_debt, outcome.tax, outcome.other_claims, outcome.result,
    )
    audit_as_of = d.risk_free.analysis_as_of
    gross = _latest(outcome.accounting_observations, MetricId.GROSS_DEBT,
                    analysis_as_of=audit_as_of, frequency=Frequency.POINT_IN_TIME)
    net = _latest(outcome.accounting_observations, MetricId.NET_DEBT,
                  analysis_as_of=audit_as_of, frequency=Frequency.POINT_IN_TIME)
    explicit_ebit = _latest(outcome.standardized_observations, MetricId.EBIT,
                            analysis_as_of=audit_as_of, frequency=Frequency.ANNUAL)
    interest = _latest(outcome.standardized_observations, MetricId.INTEREST_EXPENSE,
                       analysis_as_of=audit_as_of, frequency=Frequency.ANNUAL)
    provider_coverage = _latest(outcome.accounting_observations, MetricId.INTEREST_COVERAGE,
                                analysis_as_of=audit_as_of, frequency=Frequency.ANNUAL)
    cls = outcome.company_class
    lines = [
        "V1 USD PRODUCTION-WACC EVIDENCE AUDIT",
        f"Target symbol: {d.symbol}",
        f"Target security ID: {_text(d.target_security_id)}",
        f"Target issuer ID: {_text(d.target_issuer_id)}",
        f"Cost of equity: {_text(d.cost_of_equity.cost_of_equity if d.cost_of_equity else None)} / {d.cost_of_equity.status.value if d.cost_of_equity else 'not_ready'}",
        f"Fiscal WACC accounting capability: {outcome.accounting_capability_status}; normalized observations={len(outcome.accounting_observations)}",
        f"Company class: {cls.scope.value if cls else 'unsupported'} / {cls.status.value if cls else 'unavailable'}; domicile={_text(cls.issuer_domicile if cls else None)}; sector={_text(cls.sector if cls else None)}; industry={_text(cls.industry if cls else None)}; company_type={_text(cls.company_type if cls else None)}",
        f"Equity market value: {_text(e.value if e else None)} / {_text(e.observation_date if e else None)} / {_text(e.currency if e else None)} / {e.status.value if e else 'unavailable'}",
        f"Gross debt candidate: provider_metric={_text(gross.provenance.source_metric if gross else None)}; value={_text(gross.value if gross else None)}; date={_text(gross.period_end if gross else None)}; currency={_text(gross.currency if gross else None)}; normalized={'yes' if gross else 'no'}",
        f"Debt evidence: {_text(debt.value if debt else None)} / {debt.definition.value if debt else 'unavailable'} / {_text(debt.observation_date if debt else None)} / {debt.status.value if debt else 'unavailable'}",
        f"Net debt candidate: provider_metric={_text(net.provenance.source_metric if net else None)}; value={_text(net.value if net else None)}; date={_text(net.period_end if net else None)}; currency={_text(net.currency if net else None)}; normalized={'yes' if net else 'no'}",
        f"Explicit EBIT: value={_text(explicit_ebit.value if explicit_ebit else None)}; period={_text(explicit_ebit.period_end if explicit_ebit else None)}; source={_text(explicit_ebit.provenance.source_metric if explicit_ebit else None)}",
        f"Interest expense: value={_text(interest.value if interest else None)}; period={_text(interest.period_end if interest else None)}; source={_text(interest.provenance.source_metric if interest else None)}",
        f"Provider coverage validator: value={_text(provider_coverage.value if provider_coverage else None)}; period={_text(provider_coverage.period_end if provider_coverage else None)}; source={_text(provider_coverage.provenance.source_metric if provider_coverage else None)}",
        f"Interest coverage: numerator_metric={_text(c.numerator_metric if c else None)}; numerator={_text(c.numerator_value if c else None)}; definition={c.numerator_definition.value if c else 'unavailable'}; interest expense={_text(c.interest_expense if c else None)}; period={_text(c.fiscal_period_end if c else None)}; ratio={_text(c.ratio if c else None)}; status={c.status.value if c else 'unavailable'}",
        f"Synthetic rating: {_text(cod.synthetic_rating if cod else None)}; default spread={_text(cod.default_spread if cod else None)}; status={cod.status.value if cod else 'unavailable'}",
        f"Pre-tax cost of debt: {_text(cod.pretax_cost_of_debt if cod else None)} / {cod.status.value if cod else 'unavailable'}",
        f"Marginal tax: {_text(tax.jurisdiction if tax else None)} / {_text(tax.source_date if tax else None)} / {_text(tax.value if tax else None)} / {tax.status.value if tax else 'unavailable'}",
        f"Other enterprise claims: {_text(claims.value if claims else None)} / {claims.status.value if claims else 'unavailable'}",
        "Preferred equity: unavailable as a separate approved canonical observation.",
        "Minority / non-controlling interest: unavailable as a separate approved canonical observation.",
        f"Weights: wE={_text(result.equity_weight if result else None)}; wD={_text(result.debt_weight if result else None)}",
        f"WACC: {_text(result.value if result else None)} / {result.status.value if result else 'not_ready'}",
        f"Exact blockers: {'; '.join(result.issues if result else outcome.safe_issues)}",
        f"Safe issue count: {len(outcome.safe_issues)}",
        "No DCF, reverse DCF, current-price comparison, FX conversion, valuation family, aggregation, stance, or UI output was produced.",
    ]
    return "\n".join(lines)
