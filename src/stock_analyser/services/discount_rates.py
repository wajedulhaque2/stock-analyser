"""Provider-independent discount-rate evidence and prerequisite readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable

from stock_analyser.domain import (
    AnnualizationBasis,
    BetaDefinition,
    BetaEvidence,
    BetaSourceMethod,
    CostOfEquityResult,
    CurrencyApplicability,
    DebtValueDefinition,
    DiscountRateEvidenceStatus,
    DiscountRateReadiness,
    DiscountRateReadinessStatus,
    EquityRiskPremiumEvidence,
    EquityRiskPremiumObservation,
    Frequency,
    MacroMetric,
    MacroObservation,
    MetricId,
    MetricObservation,
    MetricUnit,
    ObservationType,
    Provenance,
    RateEvidenceType,
    RiskFreeRateEvidence,
    WaccComponentStatus,
    WaccInputReadiness,
    stable_beta_evidence_id,
    stable_cost_of_equity_id,
    stable_discount_readiness_id,
    stable_erp_evidence_id,
    stable_risk_free_evidence_id,
    stable_wacc_readiness_id,
)


@dataclass(frozen=True, slots=True)
class DiscountRatePolicy:
    maximum_risk_free_age_calendar_days: int = 7
    maximum_erp_age_calendar_days: int = 62
    allow_provider_defined_beta: bool = False
    policy_id: str = "discount-rate-evidence-v1"

    def __post_init__(self) -> None:
        for name in ("maximum_risk_free_age_calendar_days", "maximum_erp_age_calendar_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isinstance(self.allow_provider_defined_beta, bool):
            raise TypeError("allow_provider_defined_beta must be boolean")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        object.__setattr__(self, "policy_id", self.policy_id.strip())


DEFAULT_DISCOUNT_RATE_POLICY = DiscountRatePolicy()

CANONICAL_US_ERP_METHOD = "Implied ERP - trailing 12 month with adjusted payout"
CANONICAL_US_ERP_RISK_FREE_CONVENTION = (
    "Full observed US Treasury risk-free rate; no sovereign-default-spread subtraction"
)


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _currency(value: str) -> str:
    if not isinstance(value, str) or len(value.strip()) != 3 or not value.strip().isalpha():
        raise ValueError("valuation_currency must be a three-letter code")
    return value.strip().upper()


def build_risk_free_rate_evidence(
    valuation_currency: str,
    observations: Iterable[MacroObservation],
    *,
    analysis_as_of: datetime,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> RiskFreeRateEvidence:
    """Select fresh USD DGS10 evidence; every non-USD currency fails closed."""
    _aware(analysis_as_of, "analysis_as_of")
    currency = _currency(valuation_currency)
    if not isinstance(policy, DiscountRatePolicy):
        raise TypeError("policy must use DiscountRatePolicy")
    candidates = tuple(
        item for item in observations
        if isinstance(item, MacroObservation)
        and item.provider.lower() == "fred"
        and item.series_id == "DGS10"
        and item.metric is MacroMetric.TREASURY_YIELD
        and item.unit is MetricUnit.PERCENT_DECIMAL
        and item.currency == "USD"
        and item.observation_date <= analysis_as_of.date()
        and item.as_of_at <= analysis_as_of
    )
    selected = max(candidates, key=lambda item: item.observation_date, default=None)
    if currency != "USD":
        return RiskFreeRateEvidence(
            evidence_id=stable_risk_free_evidence_id(currency, analysis_as_of.isoformat(), "unavailable", policy.policy_id),
            valuation_currency=currency, evidence_currency="USD", analysis_as_of=analysis_as_of,
            evidence_type=RateEvidenceType.USD_TREASURY_10Y_CONSTANT_MATURITY,
            value=None, unit=MetricUnit.PERCENT_DECIMAL,
            annualization_basis=AnnualizationBasis.ANNUAL_PERCENT_DECIMAL,
            observation_date=None, source_as_of=None, source_name="FRED",
            source_dataset="DGS10", methodology_label="USD Treasury 10-year constant-maturity yield",
            currency_applicability=CurrencyApplicability.MISMATCHED,
            status=DiscountRateEvidenceStatus.UNAVAILABLE,
            reason=f"FRED DGS10 is USD-only; no approved {currency} sovereign risk-free source is configured.",
            provenance=(), policy_id=policy.policy_id,
        )
    if selected is None:
        return RiskFreeRateEvidence(
            evidence_id=stable_risk_free_evidence_id(currency, analysis_as_of.isoformat(), "missing", policy.policy_id),
            valuation_currency=currency, evidence_currency="USD", analysis_as_of=analysis_as_of,
            evidence_type=RateEvidenceType.USD_TREASURY_10Y_CONSTANT_MATURITY,
            value=None, unit=MetricUnit.PERCENT_DECIMAL,
            annualization_basis=AnnualizationBasis.ANNUAL_PERCENT_DECIMAL,
            observation_date=None, source_as_of=None, source_name="FRED",
            source_dataset="DGS10", methodology_label="USD Treasury 10-year constant-maturity yield",
            currency_applicability=CurrencyApplicability.MATCHED,
            status=DiscountRateEvidenceStatus.UNAVAILABLE,
            reason="No eligible FRED DGS10 observation exists on or before analysis_as_of.",
            provenance=(), policy_id=policy.policy_id,
        )
    age = (analysis_as_of.date() - selected.observation_date).days
    status = (
        DiscountRateEvidenceStatus.ELIGIBLE
        if age <= policy.maximum_risk_free_age_calendar_days
        else DiscountRateEvidenceStatus.STALE
    )
    reason = (
        "Latest eligible FRED DGS10 observation is within the centralized freshness policy."
        if status is DiscountRateEvidenceStatus.ELIGIBLE
        else f"Latest FRED DGS10 observation is {age} calendar days old, beyond the {policy.maximum_risk_free_age_calendar_days}-day policy."
    )
    return RiskFreeRateEvidence(
        evidence_id=stable_risk_free_evidence_id(
            currency, selected.observation_date.isoformat(), selected.value, selected.provenance.endpoint_or_dataset,
            analysis_as_of.isoformat(), policy.policy_id,
        ),
        valuation_currency=currency, evidence_currency="USD", analysis_as_of=analysis_as_of,
        evidence_type=RateEvidenceType.USD_TREASURY_10Y_CONSTANT_MATURITY,
        value=selected.value, unit=MetricUnit.PERCENT_DECIMAL,
        annualization_basis=AnnualizationBasis.ANNUAL_PERCENT_DECIMAL,
        observation_date=selected.observation_date, source_as_of=selected.as_of_at,
        source_name="FRED", source_dataset="DGS10",
        methodology_label="Market yield on U.S. Treasury securities at 10-year constant maturity, quoted on an investment basis",
        currency_applicability=CurrencyApplicability.MATCHED, status=status,
        reason=reason, provenance=(selected.provenance,), policy_id=policy.policy_id,
    )


def configure_equity_risk_premium(
    value: float,
    *,
    applicable_currency: str,
    market_scope: str,
    source_name: str,
    source_date: date,
    source_as_of: datetime,
    analysis_as_of: datetime,
    methodology_label: str,
    provenance: Provenance,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> EquityRiskPremiumEvidence:
    """Create explicit configured ERP evidence; there is deliberately no default."""
    _aware(source_as_of, "source_as_of")
    _aware(analysis_as_of, "analysis_as_of")
    currency = _currency(applicable_currency)
    return EquityRiskPremiumEvidence(
        evidence_id=stable_erp_evidence_id(
            currency, market_scope, source_name, source_date.isoformat(), value,
            methodology_label, analysis_as_of.isoformat(), policy.policy_id,
        ),
        value=value, unit=MetricUnit.PERCENT_DECIMAL, applicable_currency=currency,
        market_scope=market_scope, source_name=source_name, source_date=source_date,
        source_as_of=source_as_of, analysis_as_of=analysis_as_of,
        methodology_label=methodology_label,
        evidence_type=RateEvidenceType.CONFIGURED_EXTERNAL_EQUITY_RISK_PREMIUM,
        annualization_basis=AnnualizationBasis.ANNUAL_PERCENT_DECIMAL,
        status=DiscountRateEvidenceStatus.ELIGIBLE,
        provenance=(provenance,), policy_id=policy.policy_id,
    )


def build_sourced_us_erp_evidence(
    observations: Iterable[EquityRiskPremiumObservation],
    *,
    analysis_as_of: datetime,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> EquityRiskPremiumEvidence | None:
    """Select only the fixed NYU Stern adjusted-payout US implied ERP series."""
    _aware(analysis_as_of, "analysis_as_of")
    if not isinstance(policy, DiscountRatePolicy):
        raise TypeError("policy must use DiscountRatePolicy")
    candidates = tuple(
        item for item in observations
        if isinstance(item, EquityRiskPremiumObservation)
        and item.provenance.provider == "damodaran_nyu_stern"
        and item.applicable_currency == "USD"
        and item.market_scope == "US broad equity market"
        and item.unit is MetricUnit.PERCENT_DECIMAL
        and item.methodology_label == CANONICAL_US_ERP_METHOD
        and item.risk_free_convention == CANONICAL_US_ERP_RISK_FREE_CONVENTION
        and item.source_date <= analysis_as_of.date()
        and item.source_as_of <= analysis_as_of
    )
    selected = max(candidates, key=lambda item: (item.source_date, item.observation_id), default=None)
    if selected is None:
        return None
    age = (analysis_as_of.date() - selected.source_date).days
    status = (
        DiscountRateEvidenceStatus.ELIGIBLE
        if age <= policy.maximum_erp_age_calendar_days
        else DiscountRateEvidenceStatus.STALE
    )
    return EquityRiskPremiumEvidence(
        evidence_id=stable_erp_evidence_id(
            selected.observation_id, analysis_as_of.isoformat(), policy.policy_id,
        ),
        value=selected.value, unit=MetricUnit.PERCENT_DECIMAL,
        applicable_currency="USD", market_scope=selected.market_scope,
        source_name=selected.source_name, source_date=selected.source_date,
        source_as_of=selected.source_as_of, analysis_as_of=analysis_as_of,
        methodology_label=selected.methodology_label,
        evidence_type=RateEvidenceType.SOURCED_US_IMPLIED_EQUITY_RISK_PREMIUM,
        annualization_basis=AnnualizationBasis.ANNUAL_PERCENT_DECIMAL,
        status=status, provenance=(selected.provenance,), policy_id=policy.policy_id,
    )


def provider_defined_beta_evidence(
    security_id: str,
    issuer_id: str,
    observation: MetricObservation | None,
    *,
    analysis_as_of: datetime,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> BetaEvidence:
    """Retain a provider beta while exposing unknown benchmark/lookback/frequency."""
    _aware(analysis_as_of, "analysis_as_of")
    if observation is None:
        return BetaEvidence(
            evidence_id=stable_beta_evidence_id(security_id, issuer_id, analysis_as_of.isoformat(), "missing", policy.policy_id),
            security_id=security_id, issuer_id=issuer_id, value=None, unit=MetricUnit.RATIO,
            definition=BetaDefinition.UNVERIFIED, source_method=BetaSourceMethod.PROVIDER_FIELD,
            provider="unavailable", provider_symbol="unavailable", observation_date=None,
            source_as_of=None, analysis_as_of=analysis_as_of, benchmark=None, lookback=None,
            return_frequency=None, methodology_label="No beta methodology or observation available",
            status=DiscountRateEvidenceStatus.UNAVAILABLE, reason="Beta evidence is unavailable.",
            provenance=(), policy_id=policy.policy_id,
        )
    if not isinstance(observation, MetricObservation):
        raise TypeError("observation must use MetricObservation")
    valid_shape = (
        observation.metric_id is MetricId.BETA
        and observation.unit is MetricUnit.RATIO
        and observation.frequency is Frequency.POINT_IN_TIME
        and observation.observation_type is ObservationType.ACTUAL
        and observation.period_end is not None
        and observation.period_end <= analysis_as_of.date()
        and observation.as_of_at <= analysis_as_of
    )
    provider = observation.provenance.provider
    symbol = observation.provenance.provider_symbol
    value = observation.value
    eligible_by_override = (
        valid_shape and policy.allow_provider_defined_beta and value > 0
    )
    status = (
        DiscountRateEvidenceStatus.ELIGIBLE
        if eligible_by_override else DiscountRateEvidenceStatus.UNVERIFIED
        if valid_shape else DiscountRateEvidenceStatus.UNAVAILABLE
    )
    definition = BetaDefinition.PROVIDER_DEFINED
    return BetaEvidence(
        evidence_id=stable_beta_evidence_id(
            security_id, issuer_id, observation.observation_id, analysis_as_of.isoformat(), policy.policy_id,
        ),
        security_id=security_id, issuer_id=issuer_id, value=value, unit=MetricUnit.RATIO,
        definition=definition, source_method=BetaSourceMethod.PROVIDER_FIELD,
        provider=provider, provider_symbol=symbol, observation_date=observation.period_end,
        source_as_of=observation.as_of_at, analysis_as_of=analysis_as_of,
        benchmark=None, lookback=None, return_frequency=None,
        methodology_label="Provider-defined beta; benchmark, lookback, frequency, and adjustment method are not documented",
        status=status,
        reason=(
            "Explicit policy accepts this provider-defined beta despite unknown methodology."
            if eligible_by_override else
            "Provider beta is retained but ineligible by default because its methodology is not verified."
            if valid_shape else "Beta evidence is future dated or has incompatible canonical semantics."
        ),
        provenance=(observation.provenance,), policy_id=policy.policy_id,
    )


def configure_verified_beta(
    security_id: str,
    issuer_id: str,
    value: float,
    *,
    provider: str,
    provider_symbol: str,
    observation_date: date,
    source_as_of: datetime,
    analysis_as_of: datetime,
    benchmark: str,
    lookback: str,
    return_frequency: str,
    methodology_label: str,
    provenance: Provenance,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> BetaEvidence:
    _aware(source_as_of, "source_as_of")
    _aware(analysis_as_of, "analysis_as_of")
    status = (
        DiscountRateEvidenceStatus.ELIGIBLE
        if value > 0 and observation_date <= analysis_as_of.date() and source_as_of <= analysis_as_of
        else DiscountRateEvidenceStatus.UNVERIFIED
    )
    return BetaEvidence(
        evidence_id=stable_beta_evidence_id(
            security_id, issuer_id, provider, provider_symbol, observation_date.isoformat(), value,
            benchmark, lookback, return_frequency, policy.policy_id,
        ),
        security_id=security_id, issuer_id=issuer_id, value=value, unit=MetricUnit.RATIO,
        definition=BetaDefinition.VERIFIED_METHOD, source_method=BetaSourceMethod.VERIFIED_EXTERNAL_METHOD,
        provider=provider, provider_symbol=provider_symbol, observation_date=observation_date,
        source_as_of=source_as_of, analysis_as_of=analysis_as_of, benchmark=benchmark,
        lookback=lookback, return_frequency=return_frequency, methodology_label=methodology_label,
        status=status,
        reason=("Beta methodology is explicit and eligible." if status is DiscountRateEvidenceStatus.ELIGIBLE
                else "Non-positive or future beta evidence is retained but ineligible."),
        provenance=(provenance,), policy_id=policy.policy_id,
    )


def calculate_cost_of_equity(
    security_id: str,
    issuer_id: str,
    valuation_currency: str,
    risk_free: RiskFreeRateEvidence | None,
    erp: EquityRiskPremiumEvidence | None,
    beta: BetaEvidence | None,
    *,
    analysis_as_of: datetime,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> CostOfEquityResult:
    """Calculate CAPM only from eligible, same-snapshot, same-currency evidence."""
    _aware(analysis_as_of, "analysis_as_of")
    currency = _currency(valuation_currency)
    reasons: list[str] = []
    warnings: list[str] = []
    if risk_free is None:
        reasons.append("Currency-appropriate risk-free evidence is unavailable.")
    elif risk_free.analysis_as_of != analysis_as_of or risk_free.valuation_currency != currency:
        reasons.append("Risk-free evidence currency or analysis snapshot does not match valuation.")
    elif risk_free.status is not DiscountRateEvidenceStatus.ELIGIBLE:
        reasons.append(f"Risk-free evidence is {risk_free.status.value}: {risk_free.reason}")
    if erp is None:
        reasons.append("Explicit equity-risk-premium evidence is unavailable; no default was used.")
    elif erp.analysis_as_of != analysis_as_of or erp.applicable_currency != currency:
        reasons.append("ERP currency scope or analysis snapshot does not match valuation.")
    elif erp.status is not DiscountRateEvidenceStatus.ELIGIBLE:
        reasons.append(f"ERP evidence is {erp.status.value}.")
    if beta is None:
        reasons.append("Eligible beta evidence is unavailable.")
    elif beta.security_id != security_id or beta.issuer_id != issuer_id or beta.analysis_as_of != analysis_as_of:
        reasons.append("Beta identity or analysis snapshot does not match valuation.")
    elif beta.status is not DiscountRateEvidenceStatus.ELIGIBLE:
        reasons.append(f"Beta evidence is {beta.status.value}: {beta.reason}")
        if beta.definition is BetaDefinition.PROVIDER_DEFINED:
            warnings.append("Provider-defined beta methodology remains visible and was not relabeled as regression beta.")
    ready = not reasons
    partial = (
        not ready and risk_free is not None and risk_free.status is DiscountRateEvidenceStatus.ELIGIBLE
        and risk_free.analysis_as_of == analysis_as_of and risk_free.valuation_currency == currency
        and erp is not None and erp.status is DiscountRateEvidenceStatus.ELIGIBLE
        and erp.analysis_as_of == analysis_as_of and erp.applicable_currency == currency
        and beta is not None and beta.value is not None
        and beta.security_id == security_id and beta.issuer_id == issuer_id
        and beta.analysis_as_of == analysis_as_of
    )
    status = (
        DiscountRateReadinessStatus.READY if ready
        else DiscountRateReadinessStatus.PARTIAL if partial
        else DiscountRateReadinessStatus.NOT_READY
    )
    value = risk_free.value + beta.value * erp.value if ready else None
    supporting = tuple(item for item in (
        risk_free.evidence_id if risk_free else None,
        erp.evidence_id if erp else None,
        beta.evidence_id if beta else None,
    ) if item is not None)
    provenance = tuple(dict.fromkeys((
        *((risk_free.provenance if risk_free else ())),
        *((erp.provenance if erp else ())),
        *((beta.provenance if beta else ())),
    )))
    return CostOfEquityResult(
        result_id=stable_cost_of_equity_id(
            security_id, issuer_id, currency, analysis_as_of.isoformat(), *supporting, policy.policy_id,
        ),
        security_id=security_id, issuer_id=issuer_id, analysis_as_of=analysis_as_of,
        valuation_currency=currency,
        risk_free_evidence_id=risk_free.evidence_id if risk_free else None,
        risk_free_rate=risk_free.value if risk_free else None,
        erp_evidence_id=erp.evidence_id if erp else None,
        equity_risk_premium=erp.value if erp else None,
        beta_evidence_id=beta.evidence_id if beta else None,
        beta=beta.value if beta else None, cost_of_equity=value, status=status,
        blocking_reasons=tuple(reasons), warnings=tuple(warnings), supporting_ids=supporting,
        policy_id=policy.policy_id, provenance=provenance,
    )


def assess_wacc_input_readiness(
    cost_of_equity: CostOfEquityResult,
    *,
    equity_market_value_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    debt_value_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    debt_value_definition: DebtValueDefinition = DebtValueDefinition.UNAVAILABLE,
    pretax_cost_of_debt_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    tax_rate_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    capital_structure_weights_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    preferred_equity_treatment_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    nci_treatment_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    currency_alignment_status: WaccComponentStatus = WaccComponentStatus.UNAVAILABLE,
    book_debt_proxy_approved: bool = False,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> WaccInputReadiness:
    """Report missing WACC concepts without calculating WACC or proxies."""
    components = (
        (cost_of_equity.status is DiscountRateReadinessStatus.READY, "eligible cost of equity"),
        (equity_market_value_status is WaccComponentStatus.VERIFIED, "verified equity market value"),
        (debt_value_status is WaccComponentStatus.VERIFIED and (
            debt_value_definition is DebtValueDefinition.MARKET_VALUE
            or debt_value_definition is DebtValueDefinition.BOOK_VALUE_PROXY and book_debt_proxy_approved
        ), "verified market-value debt evidence or an explicitly approved proxy policy"),
        (pretax_cost_of_debt_status is WaccComponentStatus.VERIFIED, "explicit pre-tax cost of debt"),
        (tax_rate_status is WaccComponentStatus.VERIFIED, "explicit tax-rate evidence"),
        (capital_structure_weights_status is WaccComponentStatus.VERIFIED, "validated capital-structure weights"),
        (preferred_equity_treatment_status in {WaccComponentStatus.VERIFIED, WaccComponentStatus.NOT_APPLICABLE}, "preferred-equity treatment"),
        (nci_treatment_status in {WaccComponentStatus.VERIFIED, WaccComponentStatus.NOT_APPLICABLE}, "non-controlling-interest treatment"),
        (currency_alignment_status is WaccComponentStatus.VERIFIED, "currency alignment"),
    )
    missing = tuple(label for okay, label in components if not okay)
    status = (
        DiscountRateReadinessStatus.READY if not missing
        else DiscountRateReadinessStatus.PARTIAL if any(okay for okay, _ in components)
        else DiscountRateReadinessStatus.NOT_READY
    )
    warnings = ()
    if debt_value_definition is DebtValueDefinition.BOOK_VALUE_PROXY:
        warnings = ((
            "Book gross debt is an explicitly approved non-financial-company proxy and is not called market debt."
            if book_debt_proxy_approved else
            "Book debt is retained as an unapproved proxy and is not called market debt."
        ),)
    return WaccInputReadiness(
        readiness_id=stable_wacc_readiness_id(cost_of_equity.result_id, *missing, policy.policy_id),
        security_id=cost_of_equity.security_id, issuer_id=cost_of_equity.issuer_id,
        analysis_as_of=cost_of_equity.analysis_as_of,
        valuation_currency=cost_of_equity.valuation_currency,
        cost_of_equity_status=cost_of_equity.status,
        equity_market_value_status=equity_market_value_status,
        debt_value_status=debt_value_status, debt_value_definition=debt_value_definition,
        pretax_cost_of_debt_status=pretax_cost_of_debt_status, tax_rate_status=tax_rate_status,
        capital_structure_weights_status=capital_structure_weights_status,
        preferred_equity_treatment_status=preferred_equity_treatment_status,
        nci_treatment_status=nci_treatment_status,
        currency_alignment_status=currency_alignment_status,
        status=status, missing_requirements=missing, warnings=warnings,
        supporting_ids=(cost_of_equity.result_id,), policy_id=policy.policy_id,
    )


def assess_discount_rate_readiness(
    risk_free: RiskFreeRateEvidence,
    erp: EquityRiskPremiumEvidence | None,
    beta: BetaEvidence | None,
    cost_of_equity: CostOfEquityResult,
    wacc: WaccInputReadiness,
    *,
    policy: DiscountRatePolicy = DEFAULT_DISCOUNT_RATE_POLICY,
) -> DiscountRateReadiness:
    missing = tuple(dict.fromkeys((
        *(cost_of_equity.blocking_reasons),
        *(wacc.missing_requirements),
    )))
    status = (
        DiscountRateReadinessStatus.READY
        if cost_of_equity.status is DiscountRateReadinessStatus.READY and wacc.status is DiscountRateReadinessStatus.READY
        else DiscountRateReadinessStatus.PARTIAL
        if risk_free.status is DiscountRateEvidenceStatus.ELIGIBLE or cost_of_equity.status is DiscountRateReadinessStatus.PARTIAL
        else DiscountRateReadinessStatus.NOT_READY
    )
    supporting = tuple(dict.fromkeys((
        risk_free.evidence_id,
        *((erp.evidence_id,) if erp else ()),
        *((beta.evidence_id,) if beta else ()),
        cost_of_equity.result_id, wacc.readiness_id,
    )))
    return DiscountRateReadiness(
        readiness_id=stable_discount_readiness_id(
            cost_of_equity.security_id, cost_of_equity.issuer_id,
            cost_of_equity.analysis_as_of.isoformat(), *supporting, policy.policy_id,
        ),
        security_id=cost_of_equity.security_id, issuer_id=cost_of_equity.issuer_id,
        analysis_as_of=cost_of_equity.analysis_as_of,
        valuation_currency=cost_of_equity.valuation_currency,
        risk_free_status=risk_free.status,
        erp_status=erp.status if erp else DiscountRateEvidenceStatus.UNAVAILABLE,
        beta_status=beta.status if beta else DiscountRateEvidenceStatus.UNAVAILABLE,
        cost_of_equity_status=cost_of_equity.status, wacc_status=wacc.status,
        status=status, missing_requirements=missing, supporting_ids=supporting,
        policy_id=policy.policy_id,
    )
