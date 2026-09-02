"""Fail-closed USD WACC evidence assembly for large non-financial companies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Iterable

from stock_analyser.domain import (
    ActualSelectionStatus,
    CapitalComponent, CapitalStructureWeights, CapitalValueEvidence,
    CompanyClassEligibilityEvidence, CompanyClassScope, CompanyIdentity,
    CostOfDebtEvidence, CostOfEquityResult, CoverageNumeratorDefinition,
    CoverageNumeratorEligibilityEvidence,
    DebtValueDefinition, DiscountRateEvidenceStatus, DiscountRateReadinessStatus,
    Frequency, InterestCoverageEvidence, MarginalTaxRateEvidence,
    MarginalTaxRateObservation, MetricId, MetricObservation, MetricUnit,
    ObservationType, OtherClaimsStatus, OtherEnterpriseClaimsEvidence,
    RiskFreeRateEvidence, SyntheticRatingBand, WaccComponentStatus,
    WaccInputReadiness, WaccResult, stable_wacc_evidence_id,
)
from .actual_selection import select_canonical_actual
from .discount_rates import assess_wacc_input_readiness


@dataclass(frozen=True, slots=True)
class WaccPolicy:
    maximum_capital_value_age_calendar_days: int = 45
    maximum_tax_age_calendar_days: int = 400
    allow_book_gross_debt_proxy_for_large_us_nonfinancial: bool = True
    numeric_tolerance: float = 1e-12
    large_company_minimum_market_cap_usd: float = 5_000_000_000
    policy_id: str = "usd-large-nonfinancial-wacc-v1"


DEFAULT_WACC_POLICY = WaccPolicy()


def build_company_class_eligibility(
    identity: CompanyIdentity,
    market_cap: CapitalValueEvidence,
    *,
    policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CompanyClassEligibilityEvidence:
    """Establish the sourced Damodaran company class without ticker inference."""
    issues: list[str] = []
    us = _country_key(identity.issuer_domicile) in {
        "us", "usa", "unitedstates", "unitedstatesofamerica",
    }
    if not us:
        issues.append("Canonical issuer domicile does not establish a US company.")
    company_type = re.sub(r"[^a-z0-9]+", "_", (identity.company_type or "").strip().lower()).strip("_")
    if company_type not in {"operating_company", "operatingcompany"}:
        issues.append("Canonical company-type evidence does not establish an operating company.")
    classification = f"{identity.sector} {identity.industry}".lower()
    financial_tokens = (
        "financial", "bank", "insurance", "capital markets", "broker", "asset management",
        "consumer finance", "mortgage", "reinsurance",
    )
    if not identity.sector.strip() or not identity.industry.strip() or "unknown" in classification:
        issues.append("Canonical sector and industry evidence is incomplete.")
    elif any(token in classification for token in financial_tokens):
        issues.append("Canonical sector/industry classifies the issuer as a financial company.")
    if (
        market_cap.status is not DiscountRateEvidenceStatus.ELIGIBLE
        or market_cap.currency != "USD" or market_cap.value is None
    ):
        issues.append("Eligible canonical USD market capitalization is unavailable for the large-company test.")
    elif market_cap.value < policy.large_company_minimum_market_cap_usd:
        issues.append("Canonical market capitalization is below the sourced USD 5 billion large-company threshold.")
    eligible = not issues
    return CompanyClassEligibilityEvidence(
        evidence_id=stable_wacc_evidence_id(
            "companyclass", identity.issuer_id, identity.issuer_domicile, identity.sector,
            identity.industry, identity.company_type, market_cap.evidence_id, policy.policy_id,
        ),
        scope=(CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING if eligible else CompanyClassScope.UNSUPPORTED),
        status=(DiscountRateEvidenceStatus.ELIGIBLE if eligible else DiscountRateEvidenceStatus.UNAVAILABLE),
        issuer_domicile=identity.issuer_domicile, sector=identity.sector, industry=identity.industry,
        company_type=identity.company_type, market_cap_evidence_id=market_cap.evidence_id,
        market_cap_value=market_cap.value, market_cap_currency=market_cap.currency,
        analysis_as_of=market_cap.analysis_as_of, issues=tuple(issues), policy_id=policy.policy_id,
    )


def build_damodaran_operating_profit_equivalence(
    *, policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CoverageNumeratorEligibilityEvidence:
    """Approve Fiscal Operating Profit only for Damodaran synthetic-rating coverage."""
    provider_definition = "Fiscal Operating Profit is the income-statement operating-profit measure."
    methodology_definition = "Damodaran synthetic-rating interest coverage uses operating income divided by interest expense."
    return CoverageNumeratorEligibilityEvidence(
        evidence_id=stable_wacc_evidence_id(
            "coverageequivalence", "fiscal_operating_profit", "damodaran_synthetic_rating",
            policy.policy_id,
        ),
        definition=CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT,
        status=DiscountRateEvidenceStatus.ELIGIBLE,
        provider_metric="Fiscal Operating Profit / Operating Income",
        provider_definition=provider_definition,
        methodology_name="NYU Stern Damodaran synthetic rating interest coverage",
        methodology_definition=methodology_definition,
        issues=(), policy_id=policy.policy_id,
    )


def _valid_point(obs: MetricObservation, metric: MetricId, currency: str, as_of: datetime) -> bool:
    return (
        isinstance(obs, MetricObservation) and obs.metric_id is metric
        and obs.unit is MetricUnit.CURRENCY and obs.currency == currency
        and obs.frequency is Frequency.POINT_IN_TIME
        and obs.observation_type is ObservationType.ACTUAL
        and obs.period_end is not None and obs.period_end <= as_of.date()
        and obs.as_of_at <= as_of
    )


def build_equity_value_evidence(
    observation: MetricObservation | None, *, analysis_as_of: datetime,
    valuation_currency: str = "USD", policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CapitalValueEvidence:
    issues: list[str] = []
    valid = observation is not None and _valid_point(observation, MetricId.MARKET_CAP, valuation_currency, analysis_as_of)
    if not valid:
        issues.append("Canonical current market-cap evidence is unavailable, future dated, or currency-incompatible.")
    elif observation.value <= 0:
        valid = False; issues.append("Market capitalization must be positive.")
    elif (analysis_as_of.date() - observation.period_end).days > policy.maximum_capital_value_age_calendar_days:
        valid = False; issues.append("Market capitalization evidence is stale.")
    status = DiscountRateEvidenceStatus.ELIGIBLE if valid else DiscountRateEvidenceStatus.UNAVAILABLE
    return CapitalValueEvidence(
        evidence_id=stable_wacc_evidence_id("capital", "equity", getattr(observation, "observation_id", None), analysis_as_of, policy.policy_id),
        component=CapitalComponent.EQUITY, definition=DebtValueDefinition.MARKET_VALUE,
        value=observation.value if valid else None, currency=valuation_currency,
        observation_date=observation.period_end if valid else None,
        analysis_as_of=analysis_as_of,
        source_observation_id=observation.observation_id if valid else None,
        provider=observation.provenance.provider if valid else "unavailable",
        status=status, issues=tuple(issues), policy_id=policy.policy_id,
        provenance=(observation.provenance,) if valid else (),
    )


def build_debt_value_evidence(
    observation: MetricObservation | None, *, analysis_as_of: datetime,
    company_class_scope: CompanyClassScope | None = None,
    company_class_evidence: CompanyClassEligibilityEvidence | None = None,
    valuation_currency: str = "USD", policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CapitalValueEvidence:
    issues: list[str] = []
    valid = observation is not None and _valid_point(observation, MetricId.GROSS_DEBT, valuation_currency, analysis_as_of)
    if not valid:
        issues.append("Canonical gross interest-bearing debt evidence is unavailable, future dated, or currency-incompatible.")
    elif observation.value < 0:
        valid = False; issues.append("Gross debt cannot be negative.")
    elif not (
        observation.provenance.source_metric == "calculated_total_debt"
        or observation.provenance.endpoint_or_dataset == "fiscal_standardized_financials"
        and "debt" in str(observation.provenance.source_metric).lower()
    ):
        valid = False; issues.append("Debt source semantics do not establish Fiscal total interest-bearing debt.")
    elif company_class_evidence is not None and (
        company_class_evidence.status is not DiscountRateEvidenceStatus.ELIGIBLE
        or company_class_evidence.scope is not CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING
    ):
        valid = False; issues.append("Book gross-debt proxy is unsupported by canonical company-class evidence.")
    elif company_class_evidence is None and company_class_scope is not CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING:
        valid = False; issues.append("Book gross-debt proxy is unsupported for this company class.")
    elif not policy.allow_book_gross_debt_proxy_for_large_us_nonfinancial:
        valid = False; issues.append("Book gross-debt proxy policy is disabled.")
    elif (analysis_as_of.date() - observation.period_end).days > policy.maximum_capital_value_age_calendar_days:
        valid = False; issues.append("Gross-debt evidence is stale.")
    status = DiscountRateEvidenceStatus.ELIGIBLE if valid else DiscountRateEvidenceStatus.UNAVAILABLE
    return CapitalValueEvidence(
        evidence_id=stable_wacc_evidence_id("capital", "debt", getattr(observation, "observation_id", None), analysis_as_of, policy.policy_id),
        component=CapitalComponent.DEBT, definition=DebtValueDefinition.BOOK_VALUE_PROXY if valid else DebtValueDefinition.UNAVAILABLE,
        value=observation.value if valid else None, currency=valuation_currency,
        observation_date=observation.period_end if valid else None,
        analysis_as_of=analysis_as_of,
        source_observation_id=observation.observation_id if valid else None,
        provider=observation.provenance.provider if valid else "unavailable",
        status=status, issues=tuple(issues), policy_id=policy.policy_id,
        provenance=((observation.provenance,) if not valid else (
            observation.provenance.__class__(
                provider=observation.provenance.provider,
                endpoint_or_dataset=observation.provenance.endpoint_or_dataset,
                provider_symbol=observation.provenance.provider_symbol,
                retrieved_at=observation.provenance.retrieved_at,
                as_of_at=observation.provenance.as_of_at,
                transformation_steps=observation.provenance.transformation_steps + (
                    "retained canonical Fiscal gross total debt as explicitly approved BOOK_VALUE_PROXY; not market debt",
                ),
                input_observation_ids=(observation.observation_id,),
                configuration_or_override_id=policy.policy_id,
                source_metric=observation.provenance.source_metric,
            ),
        )) if observation is not None else (),
    )


def build_interest_coverage_evidence(
    observations: Iterable[MetricObservation], *, analysis_as_of: datetime,
    operating_profit_equivalence: CoverageNumeratorEligibilityEvidence | None = None,
    valuation_currency: str = "USD", policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> InterestCoverageEvidence:
    annuals = tuple(o for o in observations if isinstance(o, MetricObservation)
                    and o.frequency is Frequency.ANNUAL and o.observation_type is ObservationType.ACTUAL
                    and o.unit is MetricUnit.CURRENCY and o.currency == valuation_currency
                    and o.period_end is not None and o.period_end <= analysis_as_of.date() and o.as_of_at <= analysis_as_of)
    selected_pair = None
    selection_issue = None
    for end in sorted({o.period_end for o in annuals}, reverse=True):
        interest_selection = select_canonical_actual(
            annuals, metric_id=MetricId.INTEREST_EXPENSE, frequency=Frequency.ANNUAL,
            period_end=end, analysis_as_of=analysis_as_of,
        )
        if interest_selection.status is not ActualSelectionStatus.SELECTED:
            if any(o.metric_id is MetricId.INTEREST_EXPENSE and o.period_end == end for o in annuals):
                selection_issue = "Conflicting same-period interest-expense actuals could not be selected canonically."
                break
            continue
        ebit_selection = select_canonical_actual(
            annuals, metric_id=MetricId.EBIT, frequency=Frequency.ANNUAL,
            period_end=end, analysis_as_of=analysis_as_of,
        )
        definition = CoverageNumeratorDefinition.EXPLICIT_EBIT
        eligibility_id = None
        numerator_selection = ebit_selection
        if ebit_selection.status is not ActualSelectionStatus.SELECTED:
            if any(o.metric_id is MetricId.EBIT and o.period_end == end for o in annuals):
                selection_issue = "Conflicting same-period EBIT actuals could not be selected canonically."
                break
            if (
                operating_profit_equivalence is not None
                and operating_profit_equivalence.status is DiscountRateEvidenceStatus.ELIGIBLE
                and operating_profit_equivalence.definition
                is CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT
            ):
                numerator_selection = select_canonical_actual(
                    annuals, metric_id=MetricId.OPERATING_INCOME, frequency=Frequency.ANNUAL,
                    period_end=end, analysis_as_of=analysis_as_of,
                )
                definition = CoverageNumeratorDefinition.DAMODARAN_OPERATING_PROFIT_EQUIVALENT
                eligibility_id = operating_profit_equivalence.evidence_id
                if numerator_selection.status is ActualSelectionStatus.SELECTED:
                    candidate = numerator_selection.selected_observation
                    if not (
                        candidate.provenance.provider.lower() == "fiscal"
                        and candidate.provenance.endpoint_or_dataset == "fiscal_standardized_financials"
                    ):
                        numerator_selection = ebit_selection
                elif any(o.metric_id is MetricId.OPERATING_INCOME and o.period_end == end for o in annuals):
                    selection_issue = "Conflicting same-period Operating Profit actuals could not be selected canonically."
                    break
        if numerator_selection.status is ActualSelectionStatus.SELECTED:
            selected_pair = (
                numerator_selection.selected_observation,
                interest_selection.selected_observation,
                definition,
                eligibility_id,
            )
            break
    if selected_pair is None:
        issues = (selection_issue or "Same-period canonical annual EBIT and explicit interest-expense magnitude are unavailable.",)
        return InterestCoverageEvidence(
            stable_wacc_evidence_id("coverage", analysis_as_of, "missing", policy.policy_id), None, None, None,
            None, None, None, valuation_currency, analysis_as_of,
            DiscountRateEvidenceStatus.UNAVAILABLE, issues, (), policy.policy_id,
        )
    numerator, interest, definition, eligibility_id = selected_pair
    if interest.value <= 0:
        return InterestCoverageEvidence(
            stable_wacc_evidence_id("coverage", numerator.observation_id, interest.observation_id, policy.policy_id),
            numerator.observation_id if definition is CoverageNumeratorDefinition.EXPLICIT_EBIT else None,
            interest.observation_id, numerator.period_end,
            numerator.value if definition is CoverageNumeratorDefinition.EXPLICIT_EBIT else None,
            interest.value, None, valuation_currency, analysis_as_of,
            DiscountRateEvidenceStatus.UNAVAILABLE,
            ("Interest expense must be a positive canonical expense magnitude; division by zero is forbidden.",),
            (numerator.provenance, interest.provenance), policy.policy_id,
            numerator.observation_id, numerator.metric_id.value, definition, numerator.value, eligibility_id,
        )
    ratio = numerator.value / interest.value
    return InterestCoverageEvidence(
        stable_wacc_evidence_id("coverage", numerator.observation_id, interest.observation_id, ratio, definition, policy.policy_id),
        numerator.observation_id if definition is CoverageNumeratorDefinition.EXPLICIT_EBIT else None,
        interest.observation_id, numerator.period_end,
        numerator.value if definition is CoverageNumeratorDefinition.EXPLICIT_EBIT else None,
        interest.value, ratio, valuation_currency, analysis_as_of,
        DiscountRateEvidenceStatus.ELIGIBLE, (), (numerator.provenance, interest.provenance), policy.policy_id,
        numerator.observation_id, numerator.metric_id.value, definition, numerator.value, eligibility_id,
    )


def build_synthetic_cost_of_debt(
    coverage: InterestCoverageEvidence, bands: Iterable[SyntheticRatingBand],
    risk_free: RiskFreeRateEvidence, *, company_class_scope: CompanyClassScope | None = None,
    company_class_evidence: CompanyClassEligibilityEvidence | None = None,
    policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CostOfDebtEvidence:
    issues: list[str] = []
    matches: list[SyntheticRatingBand] = []
    resolved_scope = company_class_evidence.scope if company_class_evidence is not None else company_class_scope
    if company_class_evidence is not None and company_class_evidence.status is not DiscountRateEvidenceStatus.ELIGIBLE:
        issues.append("Canonical company-class evidence is not eligible for the synthetic-rating methodology.")
    elif resolved_scope is not CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING:
        issues.append("Synthetic rating methodology is unavailable outside large US non-financial operating companies.")
    elif coverage.status is not DiscountRateEvidenceStatus.ELIGIBLE or coverage.ratio is None:
        issues.append("Eligible same-period annual interest coverage is unavailable.")
    else:
        matches = [b for b in bands if b.company_class_scope is resolved_scope and
                   (coverage.ratio > b.coverage_lower_bound if not b.lower_inclusive else coverage.ratio >= b.coverage_lower_bound) and
                   (coverage.ratio <= b.coverage_upper_bound if b.upper_inclusive else coverage.ratio < b.coverage_upper_bound)]
        if len(matches) != 1:
            issues.append("Interest coverage did not map to exactly one sourced rating band; no cap, interpolation, or nearest band was used.")
    if risk_free.status is not DiscountRateEvidenceStatus.ELIGIBLE or risk_free.valuation_currency != "USD" or risk_free.value is None:
        issues.append("Eligible USD DGS10 evidence is unavailable.")
    ready = not issues
    band = matches[0] if ready else None
    pretax = risk_free.value + band.default_spread if ready else None
    provenance = tuple(dict.fromkeys((*coverage.provenance, *((band.provenance,) if band else ()), *risk_free.provenance)))
    return CostOfDebtEvidence(
        stable_wacc_evidence_id("costdebt", coverage.evidence_id, getattr(band, "evidence_id", None), risk_free.evidence_id, policy.policy_id),
        risk_free.evidence_id if ready else None, coverage.evidence_id if ready else None,
        band.evidence_id if band else None, band.synthetic_rating if band else None,
        band.default_spread if band else None, pretax, "USD", coverage.analysis_as_of,
        DiscountRateEvidenceStatus.ELIGIBLE if ready else DiscountRateEvidenceStatus.UNAVAILABLE,
        tuple(issues), provenance, policy.policy_id,
    )


def _country_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def build_marginal_tax_evidence(
    issuer_domicile: str | None, observations: Iterable[MarginalTaxRateObservation],
    *, analysis_as_of: datetime, policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> MarginalTaxRateEvidence:
    source = "Aswath Damodaran / NYU Stern"
    method = "Domicile-selected corporate marginal tax rate for the interest tax shield"
    if not issuer_domicile:
        selected = None; issues = ("Canonical issuer domicile is unavailable; listing and quote currency were not substituted.",)
    else:
        domicile = _country_key(issuer_domicile)
        aliases = {"us", "usa", "unitedstates", "unitedstatesofamerica"}
        eligible_names = aliases if domicile in aliases else {domicile}
        candidates = [o for o in observations if _country_key(o.jurisdiction) in eligible_names
                      and o.source_date <= analysis_as_of.date()]
        selected = max(candidates, key=lambda o: (o.source_date, o.observation_id), default=None)
        issues = () if selected else ("No dated marginal-tax observation matches canonical issuer domicile.",)
    if selected is not None and (analysis_as_of.date() - selected.source_date).days > policy.maximum_tax_age_calendar_days:
        issues = ("Marginal-tax evidence exceeds the centralized 400-day annual-data freshness policy.",)
        status = DiscountRateEvidenceStatus.STALE
    else:
        status = DiscountRateEvidenceStatus.ELIGIBLE if selected else DiscountRateEvidenceStatus.UNAVAILABLE
    return MarginalTaxRateEvidence(
        stable_wacc_evidence_id("tax", issuer_domicile, getattr(selected, "observation_id", None), analysis_as_of, policy.policy_id),
        selected.jurisdiction if selected else issuer_domicile, selected.value if selected else None,
        source, selected.source_date if selected else None, analysis_as_of, method, "PwC",
        status, tuple(issues), policy.policy_id, (selected.provenance,) if selected else (),
    )


def build_other_claims_evidence(
    enterprise_value: MetricObservation | None, market_cap: MetricObservation | None,
    net_debt: MetricObservation | None, *, analysis_as_of: datetime,
    valuation_currency: str = "USD", policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> OtherEnterpriseClaimsEvidence:
    rows = (enterprise_value, market_cap, net_debt)
    valid = all(row is not None for row in rows) and all(
        _valid_point(row, metric, valuation_currency, analysis_as_of)
        for row, metric in zip(rows, (MetricId.ENTERPRISE_VALUE, MetricId.MARKET_CAP, MetricId.NET_DEBT))
    )
    valid = bool(valid and len({row.period_end for row in rows}) == 1)
    if not valid:
        return OtherEnterpriseClaimsEvidence(
            stable_wacc_evidence_id("otherclaims", analysis_as_of, "missing", policy.policy_id),
            None, valuation_currency, None, analysis_as_of, OtherClaimsStatus.UNAVAILABLE, (),
            ("Aligned same-date TEV, market cap, and net debt are required; missing preferred/NCI is not zero.",), (), policy.policy_id,
        )
    value = enterprise_value.value - market_cap.value - net_debt.value
    status = OtherClaimsStatus.VERIFIED_ZERO if math.isclose(value, 0.0, rel_tol=0, abs_tol=policy.numeric_tolerance) else OtherClaimsStatus.UNRESOLVED_NONZERO
    value = 0.0 if status is OtherClaimsStatus.VERIFIED_ZERO else value
    return OtherEnterpriseClaimsEvidence(
        stable_wacc_evidence_id("otherclaims", *(r.observation_id for r in rows), value, policy.policy_id),
        value, valuation_currency, enterprise_value.period_end, analysis_as_of, status,
        tuple(r.observation_id for r in rows),
        (() if status is OtherClaimsStatus.VERIFIED_ZERO else
         ("Aggregate other enterprise claims are nonzero; they are not mislabeled as preferred equity or NCI and their required return is unresolved.",)),
        tuple(r.provenance for r in rows), policy.policy_id,
    )


def build_capital_structure_weights(
    equity: CapitalValueEvidence, debt: CapitalValueEvidence,
    *, policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> CapitalStructureWeights | None:
    if (equity.status is not DiscountRateEvidenceStatus.ELIGIBLE
        or debt.status is not DiscountRateEvidenceStatus.ELIGIBLE
        or equity.currency != debt.currency or equity.analysis_as_of != debt.analysis_as_of
        or equity.value is None or debt.value is None):
        return None
    total = equity.value + debt.value
    if total <= 0:
        return None
    we, wd = equity.value / total, debt.value / total
    return CapitalStructureWeights(
        stable_wacc_evidence_id("weights", equity.evidence_id, debt.evidence_id, policy.policy_id),
        equity.evidence_id, debt.evidence_id, equity.value, debt.value, we, wd,
        equity.currency, equity.analysis_as_of, debt.definition,
        DiscountRateReadinessStatus.READY, (), policy.policy_id,
        tuple(dict.fromkeys((*equity.provenance, *debt.provenance))),
    )


def calculate_wacc(
    cost_of_equity: CostOfEquityResult, equity: CapitalValueEvidence,
    debt: CapitalValueEvidence, coverage: InterestCoverageEvidence,
    cost_of_debt: CostOfDebtEvidence, tax: MarginalTaxRateEvidence,
    other_claims: OtherEnterpriseClaimsEvidence,
    *, policy: WaccPolicy = DEFAULT_WACC_POLICY,
) -> tuple[WaccResult, WaccInputReadiness]:
    weights = build_capital_structure_weights(equity, debt, policy=policy)
    currency_ok = all(x == cost_of_equity.valuation_currency for x in (equity.currency, debt.currency, cost_of_debt.currency))
    claims_ok = other_claims.status is OtherClaimsStatus.VERIFIED_ZERO
    readiness = assess_wacc_input_readiness(
        cost_of_equity,
        equity_market_value_status=WaccComponentStatus.VERIFIED if equity.status is DiscountRateEvidenceStatus.ELIGIBLE else WaccComponentStatus.UNAVAILABLE,
        debt_value_status=WaccComponentStatus.VERIFIED if debt.status is DiscountRateEvidenceStatus.ELIGIBLE else WaccComponentStatus.UNAVAILABLE,
        debt_value_definition=debt.definition,
        pretax_cost_of_debt_status=WaccComponentStatus.VERIFIED if (
            cost_of_debt.status is DiscountRateEvidenceStatus.ELIGIBLE
            and coverage.status is DiscountRateEvidenceStatus.ELIGIBLE
            and cost_of_debt.interest_coverage_evidence_id == coverage.evidence_id
        ) else WaccComponentStatus.UNAVAILABLE,
        tax_rate_status=WaccComponentStatus.VERIFIED if tax.status is DiscountRateEvidenceStatus.ELIGIBLE else WaccComponentStatus.UNAVAILABLE,
        capital_structure_weights_status=WaccComponentStatus.VERIFIED if weights else WaccComponentStatus.UNAVAILABLE,
        preferred_equity_treatment_status=WaccComponentStatus.NOT_APPLICABLE if claims_ok else WaccComponentStatus.UNAVAILABLE,
        nci_treatment_status=WaccComponentStatus.NOT_APPLICABLE if claims_ok else WaccComponentStatus.UNAVAILABLE,
        currency_alignment_status=WaccComponentStatus.VERIFIED if currency_ok else WaccComponentStatus.UNAVAILABLE,
        book_debt_proxy_approved=(debt.definition is DebtValueDefinition.BOOK_VALUE_PROXY and debt.status is DiscountRateEvidenceStatus.ELIGIBLE),
    )
    ready = readiness.status is DiscountRateReadinessStatus.READY
    after_tax = cost_of_debt.pretax_cost_of_debt * (1 - tax.value) if ready else None
    value = weights.equity_weight * cost_of_equity.cost_of_equity + weights.debt_weight * after_tax if ready else None
    supporting = tuple(x for x in (
        cost_of_equity.result_id, equity.evidence_id, debt.evidence_id, coverage.evidence_id,
        cost_of_debt.evidence_id, tax.evidence_id, weights.evidence_id if weights else None,
        other_claims.evidence_id, readiness.readiness_id,
    ) if x)
    issues = tuple(dict.fromkeys((*readiness.missing_requirements, *equity.issues, *debt.issues,
                                 *coverage.issues, *cost_of_debt.issues, *tax.issues, *other_claims.issues)))
    result = WaccResult(
        stable_wacc_evidence_id("wacc", *supporting, policy.policy_id), cost_of_equity.security_id,
        cost_of_equity.issuer_id, cost_of_equity.analysis_as_of, cost_of_equity.valuation_currency,
        cost_of_equity.result_id, equity.evidence_id, debt.evidence_id, coverage.evidence_id,
        cost_of_debt.evidence_id, tax.evidence_id, weights.evidence_id if weights else None,
        other_claims.evidence_id, cost_of_equity.cost_of_equity,
        cost_of_debt.pretax_cost_of_debt, tax.value, after_tax,
        weights.equity_weight if weights else None, weights.debt_weight if weights else None,
        value, DiscountRateReadinessStatus.READY if ready else DiscountRateReadinessStatus.NOT_READY,
        issues, readiness.warnings, supporting, (policy.policy_id, cost_of_equity.policy_id,
        debt.policy_id, tax.policy_id),
        tuple(dict.fromkeys((*cost_of_equity.provenance, *equity.provenance, *debt.provenance,
                            *coverage.provenance, *cost_of_debt.provenance, *tax.provenance,
                            *other_claims.provenance))), readiness.readiness_id,
    )
    return result, readiness
