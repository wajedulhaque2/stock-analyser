"""Application services for the isolated V1 migration path."""

from .consensus import ForwardConsensus, ForwardConsensusPeriod, build_forward_consensus
from .coverage import (
    AnalystCoveragePolicy,
    CoverageInputs,
    CoveragePolicy,
    CriticalIssuePolicy,
    DEFAULT_COVERAGE_POLICY,
    ForwardCoveragePolicy,
    HistoricalCoveragePolicy,
    assess_coverage,
)
from .identity import IdentityResolution, IdentitySeed, resolve_company_identity
from .actual_selection import select_canonical_actual
from .macro import RiskFreeRateResult, latest_macro_on_or_before, resolve_risk_free_rate
from .discount_rates import (
    DEFAULT_DISCOUNT_RATE_POLICY,
    DiscountRatePolicy,
    assess_discount_rate_readiness,
    assess_wacc_input_readiness,
    build_risk_free_rate_evidence,
    build_sourced_us_erp_evidence,
    calculate_cost_of_equity,
    configure_equity_risk_premium,
    configure_verified_beta,
    provider_defined_beta_evidence,
)
from .beta import (
    DEFAULT_REGRESSION_BETA_POLICY,
    SPY_TOTAL_RETURN_BENCHMARK,
    RegressionBetaPolicy,
    calculate_regression_beta,
)
from .wacc import (
    DEFAULT_WACC_POLICY, WaccPolicy, build_capital_structure_weights,
    build_company_class_eligibility, build_damodaran_operating_profit_equivalence,
    build_debt_value_evidence, build_equity_value_evidence,
    build_interest_coverage_evidence, build_marginal_tax_evidence,
    build_other_claims_evidence, build_synthetic_cost_of_debt, calculate_wacc,
)
from .reverse_dcf_inputs import (
    DEFAULT_REVERSE_DCF_INPUT_POLICY,
    ReverseDcfInputPolicy,
    assess_reverse_dcf_readiness,
    build_forward_operating_trajectory,
    build_market_enterprise_value_anchor,
    build_operating_readiness,
    build_reinvestment_readiness,
    build_reverse_dcf_actual_base,
    build_terminal_readiness,
)
from .reverse_dcf_cashflows import (
    DEFAULT_REVERSE_DCF_CASH_FLOW_POLICY,
    ReverseDcfCashFlowPolicy,
    build_reverse_dcf_cash_flow_path,
    configure_sales_to_capital_evidence,
)
from .reverse_dcf_solver import (
    DEFAULT_REVERSE_DCF_SOLVER_POLICY,
    ReverseDcfSolverPolicy,
    TerminalGrowthEvaluation,
    discount_rate_input_from_production_wacc,
    evaluate_market_implied_terminal_growth,
    solve_market_implied_terminal_growth,
)
from .reverse_dcf_execution import (
    EXECUTION_POLICY_ID,
    SCENARIO_ASSUMPTION_POLICY_ID,
    configure_reverse_dcf_scenario_assumption,
    execute_reverse_dcf,
)
from .valuation_publication import (
    DEFAULT_VALUATION_PUBLICATION_POLICY,
    ValuationPublicationPolicy,
    family_evidence_from_generic_result,
    family_evidence_from_own_history,
    family_evidence_from_peer_orchestration,
    family_evidence_from_peer_target,
    publish_cross_family_valuation,
    supplemental_evidence_from_external_reference,
    supplemental_evidence_from_reverse_dcf,
)
from .market_comparison import (
    DEFAULT_MARKET_COMPARISON_POLICY,
    DEFAULT_MARKET_PRICE_POLICY,
    MarketComparisonPolicy,
    MarketPricePolicy,
    build_market_price_evidence,
    compare_publication_to_market,
    expectation_gap_from_reverse_dcf,
)
from .research_report import REPORT_POLICY_ID, build_stock_research_report
from .peer_selection import (
    DEFAULT_PEER_SELECTION_POLICY,
    PeerCandidateInput,
    PeerCompanyEvidence,
    PeerSelectionPolicy,
    evaluate_peer_candidate,
    assess_peer_method_data_readiness,
    classify_peer_security_eligibility,
    select_peer_set,
)
from .peer_valuation_inputs import (
    DEFAULT_PEER_VALUATION_POLICY,
    PeerMethodDataInput,
    PeerValuationPolicy,
    assess_peer_method_data,
    build_peer_valuation_subset,
)
from .peer_valuation_distribution import (
    DEFAULT_PEER_DISTRIBUTION_POLICY,
    PeerDistributionPolicy,
    build_peer_multiple_distribution,
)
from .peer_target_valuation import (
    DEFAULT_PEER_TARGET_VALUATION_POLICY,
    PeerTargetValuationPolicy,
    calculate_peer_target_valuation,
)
from .peer_family_orchestration import (
    DEFAULT_PEER_FAMILY_ORCHESTRATION_POLICY,
    PeerFamilyOrchestrationPolicy,
    orchestrate_peer_family,
)
from .own_history_inputs import (
    DEFAULT_OWN_HISTORY_INPUT_POLICY,
    OwnHistoryInputPolicy,
    assess_own_history_method_readiness,
    build_capital_structure_snapshot,
    build_direct_enterprise_equity_bridge,
    build_forward_denominator_alignment,
    select_forward_denominator,
)
from .own_history_valuation import (
    DEFAULT_OWN_HISTORY_VALUATION_POLICY,
    OwnHistoryValuationPolicy,
    calculate_own_history_valuation,
)
from .own_history_orchestration import (
    OwnHistoryArithmeticAudit,
    OwnHistoryAuditStage,
    OwnHistoryMethodAudit,
    OwnHistoryOrchestrationResult,
    OwnHistoryScaleAudit,
    assemble_own_history_valuation,
)
from .reconciliation import (
    CanonicalSourceRule,
    DEFAULT_RECONCILIATION_POLICY,
    ObservationSource,
    ReconciliationAudit,
    ReconciliationPolicy,
    ToleranceBand,
    reconcile_many,
    reconcile_sources,
)
from .valuation_history import (
    DEFAULT_HISTORICAL_DISTRIBUTION_POLICY,
    HistoricalDistributionPolicy,
    HistoricalWindowMinimum,
    build_historical_distribution,
    build_historical_window_availability,
    linear_quantile,
)

__all__ = [
    "AnalystCoveragePolicy", "CoverageInputs", "CoveragePolicy", "CriticalIssuePolicy",
    "DEFAULT_COVERAGE_POLICY", "ForwardConsensus", "ForwardConsensusPeriod", "ForwardCoveragePolicy",
    "DiscountRatePolicy", "HistoricalCoveragePolicy", "HistoricalDistributionPolicy", "HistoricalWindowMinimum", "IdentityResolution", "IdentitySeed", "OwnHistoryInputPolicy", "OwnHistoryValuationPolicy", "OwnHistoryArithmeticAudit", "OwnHistoryAuditStage", "OwnHistoryMethodAudit", "OwnHistoryOrchestrationResult", "OwnHistoryScaleAudit", "PeerCandidateInput", "PeerCompanyEvidence", "PeerDistributionPolicy", "PeerFamilyOrchestrationPolicy", "PeerMethodDataInput", "PeerSelectionPolicy", "PeerTargetValuationPolicy", "PeerValuationPolicy", "RegressionBetaPolicy", "ReverseDcfCashFlowPolicy", "ReverseDcfInputPolicy", "ReverseDcfSolverPolicy", "TerminalGrowthEvaluation", "WaccPolicy", "EXECUTION_POLICY_ID", "SCENARIO_ASSUMPTION_POLICY_ID",
    "CanonicalSourceRule", "DEFAULT_RECONCILIATION_POLICY", "ObservationSource",
    "ReconciliationAudit", "ReconciliationPolicy", "RiskFreeRateResult", "ToleranceBand",
    "DEFAULT_DISCOUNT_RATE_POLICY", "DEFAULT_HISTORICAL_DISTRIBUTION_POLICY", "DEFAULT_OWN_HISTORY_INPUT_POLICY", "DEFAULT_OWN_HISTORY_VALUATION_POLICY", "DEFAULT_PEER_DISTRIBUTION_POLICY", "DEFAULT_PEER_FAMILY_ORCHESTRATION_POLICY", "DEFAULT_PEER_SELECTION_POLICY", "DEFAULT_PEER_TARGET_VALUATION_POLICY", "DEFAULT_PEER_VALUATION_POLICY", "DEFAULT_REGRESSION_BETA_POLICY", "DEFAULT_REVERSE_DCF_CASH_FLOW_POLICY", "DEFAULT_REVERSE_DCF_INPUT_POLICY", "DEFAULT_REVERSE_DCF_SOLVER_POLICY", "DEFAULT_WACC_POLICY", "SPY_TOTAL_RETURN_BENCHMARK", "assemble_own_history_valuation", "assess_coverage", "assess_discount_rate_readiness", "assess_own_history_method_readiness", "assess_peer_method_data", "assess_peer_method_data_readiness", "assess_reverse_dcf_readiness", "assess_wacc_input_readiness", "build_capital_structure_snapshot", "build_capital_structure_weights", "build_company_class_eligibility", "build_damodaran_operating_profit_equivalence", "build_debt_value_evidence", "build_direct_enterprise_equity_bridge", "build_equity_value_evidence", "build_forward_consensus", "build_forward_denominator_alignment", "build_forward_operating_trajectory", "build_interest_coverage_evidence", "build_marginal_tax_evidence", "build_market_enterprise_value_anchor", "build_operating_readiness", "build_other_claims_evidence", "build_peer_multiple_distribution", "build_peer_valuation_subset", "build_reinvestment_readiness", "build_reverse_dcf_actual_base", "build_reverse_dcf_cash_flow_path", "build_risk_free_rate_evidence", "build_sourced_us_erp_evidence", "build_synthetic_cost_of_debt", "build_terminal_readiness", "calculate_cost_of_equity", "calculate_own_history_valuation", "calculate_peer_target_valuation", "calculate_regression_beta", "calculate_wacc", "classify_peer_security_eligibility", "configure_equity_risk_premium", "configure_sales_to_capital_evidence", "configure_verified_beta", "evaluate_market_implied_terminal_growth", "evaluate_peer_candidate",
    "build_historical_distribution", "build_historical_window_availability", "latest_macro_on_or_before", "linear_quantile", "reconcile_many", "select_forward_denominator",
    "orchestrate_peer_family", "provider_defined_beta_evidence", "reconcile_sources", "resolve_company_identity", "resolve_risk_free_rate", "select_canonical_actual", "select_peer_set", "solve_market_implied_terminal_growth", "discount_rate_input_from_production_wacc", "configure_reverse_dcf_scenario_assumption", "execute_reverse_dcf",
]

__all__ += [
    "DEFAULT_VALUATION_PUBLICATION_POLICY", "ValuationPublicationPolicy",
    "family_evidence_from_generic_result", "family_evidence_from_own_history",
    "family_evidence_from_peer_orchestration", "family_evidence_from_peer_target",
    "publish_cross_family_valuation", "supplemental_evidence_from_external_reference",
    "supplemental_evidence_from_reverse_dcf",
]

__all__ += ["REPORT_POLICY_ID", "build_stock_research_report"]

__all__ += [
    "DEFAULT_MARKET_COMPARISON_POLICY", "DEFAULT_MARKET_PRICE_POLICY",
    "MarketComparisonPolicy", "MarketPricePolicy", "build_market_price_evidence",
    "compare_publication_to_market", "expectation_gap_from_reverse_dcf",
]
