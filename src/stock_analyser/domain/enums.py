"""Canonical V1 domain enumerations.

The status enums are deliberately separate types.  Their similarly named members
describe different layers and must never be passed interchangeably.
"""

from __future__ import annotations

from enum import Enum


class DomainEnum(str, Enum):
    """String-valued enum with stable serialization values."""


class DataAvailability(DomainEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CapabilityStatus(DomainEnum):
    AVAILABLE = "available"
    LOCKED = "locked"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ValuationMethodStatus(DomainEnum):
    VALID = "valid"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class AggregationStatus(DomainEnum):
    RESOLVED = "resolved"
    WIDE = "wide"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"


class PublicationEvidenceType(DomainEnum):
    REVERSE_DCF_CANONICAL_EXPECTATION = "reverse_dcf_canonical_expectation"
    REVERSE_DCF_SCENARIO = "reverse_dcf_scenario"
    EXTERNAL_ANALYST_TARGET = "external_analyst_target"
    EXTERNAL_PROVIDER_DCF_REFERENCE = "external_provider_dcf_reference"


class PublicationCentralEstimator(DomainEnum):
    MEDIAN_OF_FAMILY_CENTRALS = "median_of_family_centrals"


class PublicationRangeSemantics(DomainEnum):
    FAMILY_ENVELOPE = "family_envelope"
    COMMON_OVERLAP = "common_overlap"
    SINGLE_FAMILY_RANGE = "single_family_range"


class MarketPriceSemantic(DomainEnum):
    YAHOO_REGULAR_MARKET_PRICE = "yahoo_regular_market_price"


class MarketComparisonScope(DomainEnum):
    OVERALL_RESOLVED = "overall_resolved"
    INDIVIDUAL_FAMILY = "individual_family"
    EXPECTATION_REFERENCE = "expectation_reference"


class ReportStatus(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ReportNumberSemantic(DomainEnum):
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    MULTIPLE = "multiple"
    INTEGER = "integer"
    DATE = "date"


class ReportSourceCategory(DomainEnum):
    IDENTITY = "identity"
    MARKET_DATA = "market_data"
    FORWARD_CONSENSUS = "forward_consensus"
    VALUATION = "valuation"
    EXPECTATIONS = "expectations"
    REFERENCE = "reference"


class ReportExpectationState(DomainEnum):
    CANONICAL_EXPECTATIONS = "canonical_expectations"
    SCENARIO_EXPECTATIONS = "scenario_expectations"
    NOT_READY = "not_ready"
    NOT_RUN = "not_run"


class ReportDataQualityCategory(DomainEnum):
    MARKET_PRICE = "market_price"
    OWN_HISTORY_VALUATION = "own_history_valuation"
    PEER_VALUATION = "peer_valuation"
    FORWARD_CONSENSUS = "forward_consensus"
    REVERSE_DCF = "reverse_dcf"
    OVERALL_VALUATION_PUBLICATION = "overall_valuation_publication"


class CoverageLevel(DomainEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"


class EvidenceConfidence(DomainEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ValuationReadiness(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_READY = "not_ready"


class CoverageDimension(DomainEnum):
    MARKET_DATA = "market_data"
    HISTORICAL_FINANCIALS = "historical_financials"
    HISTORICAL_VALUATION = "historical_valuation"
    FORWARD_CONSENSUS = "forward_consensus"
    ANALYST_COVERAGE = "analyst_coverage"
    SOURCE_AGREEMENT = "source_agreement"
    CASH_FLOW_EVIDENCE = "cash_flow_evidence"
    MACRO_READINESS = "macro_readiness"
    PEER_READINESS = "peer_readiness"


class DimensionStatus(DomainEnum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    NOT_AVAILABLE = "not_available"
    NOT_EVALUATED = "not_evaluated"


class AnalystCoverageBand(DomainEnum):
    STRONG = "strong"
    MEDIUM = "medium"
    LIMITED = "limited"
    WEAK = "weak"
    UNKNOWN = "unknown"


class ValuationFamily(DomainEnum):
    OWN_HISTORY = "own_history"
    PEER = "peer"
    CASH_FLOW = "cash_flow"
    REVERSE_CASH_FLOW = "reverse_cash_flow"


class HistoricalMultipleType(DomainEnum):
    P_E = "P_E"
    EV_EBITDA = "EV_EBITDA"
    EV_EBIT = "EV_EBIT"


class HistoricalStatistic(DomainEnum):
    P25 = "p25"
    MEDIAN = "median"
    P75 = "p75"


class ValuationBasis(DomainEnum):
    EQUITY = "equity"
    ENTERPRISE = "enterprise"


class HistoricalValuationDenominator(DomainEnum):
    DILUTED_EPS = "diluted_eps"
    EBITDA = "ebitda"
    PROVIDER_OPERATING_PROFIT_AS_EBIT = "provider_operating_profit_as_ebit"


class HistoricalValuationEligibility(DomainEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNVERIFIED = "unverified"


class HistoricalValuationSampling(DomainEnum):
    DAILY = "daily"
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    REPORT_PERIOD = "report_period"


class HistoricalWindow(DomainEnum):
    THREE_YEAR = "3y"
    FIVE_YEAR = "5y"
    TEN_YEAR = "10y"


class HistoricalDistributionUsability(DomainEnum):
    USABLE = "usable"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class HistoricalQuantileConvention(DomainEnum):
    LINEAR = "linear"


class HistoricalDuplicateHandling(DomainEnum):
    DEDUPLICATE_IDENTICAL_FAIL_CONFLICT = "deduplicate_identical_fail_conflict"


class DenominatorCompatibilityStatus(DomainEnum):
    EXACT = "exact"
    VERIFIED_EQUIVALENT = "verified_equivalent"
    INCOMPATIBLE = "incompatible"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class ForwardPeriodSelection(DomainEnum):
    FY1 = "FY1"
    FY2 = "FY2"


class CapitalStructureCompleteness(DomainEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ShareCountBasis(DomainEnum):
    SHARES_OUTSTANDING = "shares_outstanding"
    BASIC_WEIGHTED_AVERAGE = "basic_weighted_average"
    DILUTED_WEIGHTED_AVERAGE = "diluted_weighted_average"


class EnterpriseAdjustmentRequirement(DomainEnum):
    PREFERRED_EQUITY = "preferred_equity"
    NON_CONTROLLING_INTEREST = "non_controlling_interest"
    OTHER_ENTERPRISE_ADJUSTMENTS = "other_enterprise_adjustments"


class EnterpriseBridgeMethod(DomainEnum):
    COMPONENT_BRIDGE = "component_bridge"
    DIRECT_TEV_MARKET_CAP_BRIDGE = "direct_tev_market_cap_bridge"


class ShareCountSemantics(DomainEnum):
    ISSUER_SHARES = "issuer_shares"
    ADS_CONVERTED = "ads_converted"
    UNVERIFIED = "unverified"


class OwnHistoryMethodStatus(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_READY = "not_ready"


class PeerSelectionStatus(DomainEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class PeerSetStatus(DomainEnum):
    USABLE = "usable"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class PeerIdentityEvidenceStatus(DomainEnum):
    SUMMARY_VERIFIED = "summary_verified"
    PROFILE_VERIFIED = "profile_verified"
    UNRESOLVED = "unresolved"


class PeerSecurityEligibilityStatus(DomainEnum):
    VERIFIED_COMMON_EQUITY = "verified_common_equity"
    VERIFIED_OTHER_EQUITY = "verified_other_equity"
    VERIFIED_NON_EQUITY = "verified_non_equity"
    UNVERIFIED = "unverified"


class PeerMethod(DomainEnum):
    EV_EBITDA = "EV_EBITDA"


class PeerMethodDataStatus(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class PeerMethodDataReason(DomainEnum):
    METHOD_INPUTS_AVAILABLE = "method_inputs_available"
    FORWARD_DENOMINATOR_UNAVAILABLE = "forward_denominator_unavailable"
    REQUIRED_MULTIPLE_INPUTS_UNAVAILABLE = "required_multiple_inputs_unavailable"


class PeerIdentityBindingBasis(DomainEnum):
    CIK = "cik"
    ISIN = "isin"
    CUSIP = "cusip"


class PeerMethodEvidenceStatus(DomainEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class PeerMethodEvidenceReason(DomainEnum):
    METHOD_DATA_AVAILABLE = "method_data_available"
    ECONOMIC_MEMBERSHIP_REQUIRED = "economic_membership_required"
    IDENTITY_MISMATCH = "identity_mismatch"
    ENTERPRISE_VALUE_UNAVAILABLE = "enterprise_value_unavailable"
    ENTERPRISE_VALUE_FUTURE = "enterprise_value_future"
    ENTERPRISE_VALUE_STALE = "enterprise_value_stale"
    ENTERPRISE_VALUE_INVALID = "enterprise_value_invalid"
    FORWARD_IDENTITY_UNVERIFIED = "forward_identity_unverified"
    FORWARD_EBITDA_UNAVAILABLE = "forward_ebitda_unavailable"
    FORWARD_ESTIMATE_FUTURE = "forward_estimate_future"
    FORWARD_PERIOD_UNAVAILABLE = "forward_period_unavailable"
    FORWARD_ESTIMATE_CASE_UNAVAILABLE = "forward_estimate_case_unavailable"
    FORWARD_EBITDA_INVALID = "forward_ebitda_invalid"
    FISCAL_PERIOD_MISMATCH = "fiscal_period_mismatch"
    UNIT_MISMATCH = "unit_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    CONFLICTING_OBSERVATIONS = "conflicting_observations"


class PeerValuationSubsetStatus(DomainEnum):
    USABLE = "usable"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class PeerDistributionStatus(DomainEnum):
    USABLE = "usable"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class PeerValuationStatistic(DomainEnum):
    P25 = "p25"
    MEDIAN = "median"
    P75 = "p75"


class PeerFamilyStatus(DomainEnum):
    VALID = "valid"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"
    EXPECTED_UNAVAILABLE = "expected_unavailable"


class PeerFamilyFailureStage(DomainEnum):
    IDENTITY = "identity"
    CANDIDATE_DISCOVERY = "candidate_discovery"
    SECURITY_ELIGIBILITY = "security_eligibility"
    ECONOMIC_COMPARABILITY = "economic_comparability"
    PEER_SET = "peer_set"
    PEER_PROVIDER_IDENTITY = "peer_provider_identity"
    PEER_ENTERPRISE_VALUE = "peer_enterprise_value"
    PEER_FORWARD_DENOMINATOR = "peer_forward_denominator"
    PEER_VALUATION_SUBSET = "peer_valuation_subset"
    PEER_DISTRIBUTION = "peer_distribution"
    TARGET_FORWARD_DENOMINATOR = "target_forward_denominator"
    TARGET_BRIDGE = "target_bridge"
    TARGET_SHARES = "target_shares"
    TARGET_APPLICATION = "target_application"
    COMPLETE = "complete"


class RateEvidenceType(DomainEnum):
    USD_TREASURY_10Y_CONSTANT_MATURITY = "usd_treasury_10y_constant_maturity"
    CONFIGURED_EXTERNAL_EQUITY_RISK_PREMIUM = "configured_external_equity_risk_premium"
    SOURCED_US_IMPLIED_EQUITY_RISK_PREMIUM = "sourced_us_implied_equity_risk_premium"


class BetaDefinition(DomainEnum):
    VERIFIED_METHOD = "verified_method"
    REGRESSION_EQUITY_BETA = "regression_equity_beta"
    PROVIDER_DEFINED = "provider_defined"
    UNVERIFIED = "unverified"


class BetaSourceMethod(DomainEnum):
    VERIFIED_EXTERNAL_METHOD = "verified_external_method"
    US_5Y_MONTHLY_MARKET_REGRESSION = "us_5y_monthly_market_regression"
    PROVIDER_FIELD = "provider_field"
    CONFIGURED_EXTERNAL = "configured_external"


class MarketReturnFrequency(DomainEnum):
    MONTHLY = "monthly"


class ReturnConvention(DomainEnum):
    SIMPLE_TOTAL_RETURN = "simple_total_return"


class PriceReturnSemantics(DomainEnum):
    SPLIT_AND_DISTRIBUTION_ADJUSTED = "split_and_distribution_adjusted"
    PRICE_ONLY_UNADJUSTED = "price_only_unadjusted"


class CurrencyApplicability(DomainEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNVERIFIED = "unverified"


class DiscountRateEvidenceStatus(DomainEnum):
    ELIGIBLE = "eligible"
    STALE = "stale"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class DiscountRateReadinessStatus(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"


class AnnualizationBasis(DomainEnum):
    ANNUAL_PERCENT_DECIMAL = "annual_percent_decimal"
    NOT_APPLICABLE = "not_applicable"


class WaccComponentStatus(DomainEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class DebtValueDefinition(DomainEnum):
    MARKET_VALUE = "market_value"
    BOOK_VALUE_PROXY = "book_value_proxy"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class CapitalComponent(DomainEnum):
    EQUITY = "equity"
    DEBT = "debt"


class CompanyClassScope(DomainEnum):
    LARGE_US_NON_FINANCIAL_OPERATING = "large_us_non_financial_operating"
    UNSUPPORTED = "unsupported"


class CoverageNumeratorDefinition(DomainEnum):
    EXPLICIT_EBIT = "explicit_ebit"
    DAMODARAN_OPERATING_PROFIT_EQUIVALENT = "damodaran_operating_profit_equivalent"
    UNAVAILABLE = "unavailable"


class OtherClaimsStatus(DomainEnum):
    VERIFIED_ZERO = "verified_zero"
    UNRESOLVED_NONZERO = "unresolved_nonzero"
    UNAVAILABLE = "unavailable"


class ReverseDcfReadinessStatus(DomainEnum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"


class ReverseDcfReadinessStage(DomainEnum):
    IDENTITY = "identity"
    ACTUAL_BASE = "actual_base"
    FY1_REINVESTMENT_BASE = "fy1_reinvestment_base"
    FORWARD_REVENUE = "forward_revenue"
    FORWARD_EBIT = "forward_ebit"
    FORWARD_TRAJECTORY = "forward_trajectory"
    OPERATING_TAX = "operating_tax"
    REINVESTMENT = "reinvestment"
    MARKET_ENTERPRISE_VALUE = "market_enterprise_value"
    DISCOUNT_RATE = "discount_rate"
    TERMINAL_POLICY = "terminal_policy"
    READY_FOR_SOLVER = "ready_for_solver"


class ReverseDcfFormulation(DomainEnum):
    CONSENSUS_FCFF_TRAJECTORY = "consensus_fcff_trajectory"
    CONSENSUS_REVENUE_MARGIN_WITH_REINVESTMENT = "consensus_revenue_margin_with_reinvestment"
    MARKET_IMPLIED_REVENUE_GROWTH = "market_implied_revenue_growth"
    MARKET_IMPLIED_TERMINAL_GROWTH = "market_implied_terminal_growth"
    MARKET_IMPLIED_TERMINAL_MARGIN = "market_implied_terminal_margin"


class SalesToCapitalSourceType(DomainEnum):
    VERIFIED_EXTERNAL = "verified_external"
    VERIFIED_COMPANY_DERIVED = "verified_company_derived"
    CONFIGURED_EXTERNAL = "configured_external"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class ReverseDcfTimingConvention(DomainEnum):
    DISCRETE_ANNUAL_FISCAL_INDEX = "discrete_annual_fiscal_index"


class TerminalMarginPolicy(DomainEnum):
    HOLD_FINAL_CONSENSUS_MARGIN = "hold_final_consensus_margin"


class ReverseDcfSolverStatus(DomainEnum):
    SOLVED = "solved"
    NOT_READY = "not_ready"
    NO_SOLUTION_IN_DOMAIN = "no_solution_in_domain"
    NUMERICAL_FAILURE = "numerical_failure"
    UNAVAILABLE = "unavailable"


class ReverseDcfExecutionMode(DomainEnum):
    CANONICAL_EVIDENCE = "canonical_evidence"
    EXPLICIT_SCENARIO = "explicit_scenario"


class ReverseDcfScenarioAssumptionType(DomainEnum):
    PRECEDING_ANNUAL_REVENUE = "preceding_annual_revenue"
    SALES_TO_CAPITAL = "sales_to_capital"
    WACC = "wacc"


class ReverseDcfScenarioSourceCategory(DomainEnum):
    USER_SUPPLIED = "user_supplied"
    EXTERNAL_RESEARCH = "external_research"
    CONFIGURED_SCENARIO = "configured_scenario"


class ReverseDcfExecutionInputSource(DomainEnum):
    CANONICAL_EVIDENCE = "canonical_evidence"
    SCENARIO_ASSUMPTION = "scenario_assumption"


class ReverseDcfDiscountRateSource(DomainEnum):
    PRODUCTION_WACC = "production_wacc"
    SCENARIO_WACC = "scenario_wacc"


class ReverseDcfPublicationEligibility(DomainEnum):
    CANONICAL = "canonical"
    SCENARIO_ONLY = "scenario_only"
    UNAVAILABLE = "unavailable"


class PeerQuantileConvention(DomainEnum):
    TYPE_7_LINEAR = "type_7_linear"


class PeerCentralStatistic(DomainEnum):
    MEDIAN = "median"


class PeerOutlierPolicy(DomainEnum):
    NO_AUTOMATIC_REMOVAL = "no_automatic_removal"


class ActualSelectionStatus(DomainEnum):
    SELECTED = "selected"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"


class ActualSelectionReason(DomainEnum):
    SINGLE_OBSERVATION = "single_observation"
    IDENTICAL_DUPLICATES_DEDUPLICATED = "identical_duplicates_deduplicated"
    LATEST_VERIFIED_REVISION = "latest_verified_revision"
    NO_ELIGIBLE_OBSERVATION = "no_eligible_observation"
    INCOMPATIBLE_SOURCE_SEMANTICS = "incompatible_source_semantics"
    CONFLICTING_VALUES_WITHOUT_VERIFIED_CHRONOLOGY = "conflicting_values_without_verified_chronology"
    OBSERVATION_ID_COLLISION = "observation_id_collision"


class PeerSelectionReason(DomainEnum):
    INCLUSION_RULES_SATISFIED = "inclusion_rules_satisfied"
    IDENTITY_MISMATCH = "identity_mismatch"
    WRONG_SECURITY_TYPE = "wrong_security_type"
    SECURITY_ELIGIBILITY_UNVERIFIED = "security_eligibility_unverified"
    SELF_REFERENCE = "self_reference"
    DUPLICATE_ISSUER = "duplicate_issuer"
    INDUSTRY_MISMATCH = "industry_mismatch"
    INDUSTRY_UNRESOLVED = "industry_unresolved"
    SECTOR_ONLY = "sector_only"
    BUSINESS_MODEL_MISMATCH = "business_model_mismatch"
    INSUFFICIENT_FINANCIAL_DATA = "insufficient_financial_data"
    SCALE_NOT_COMPARABLE = "scale_not_comparable"
    GROWTH_NOT_COMPARABLE = "growth_not_comparable"
    MARGIN_NOT_COMPARABLE = "margin_not_comparable"
    PROFITABILITY_NOT_COMPARABLE = "profitability_not_comparable"
    FORWARD_DATA_UNAVAILABLE = "forward_data_unavailable"
    CURRENCY_COMPARABILITY_UNRESOLVED = "currency_comparability_unresolved"
    SHARE_CLASS_UNRESOLVED = "share_class_unresolved"
    ADR_CONVERSION_UNRESOLVED = "adr_conversion_unresolved"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class PeerCandidateSource(DomainEnum):
    PROVIDER_PROFILE_PEERS = "provider_profile_peers"
    PROVIDER_STOCK_PEERS = "provider_stock_peers"


class PeerCriterion(DomainEnum):
    IDENTITY = "identity"
    SELF_REFERENCE = "self_reference"
    SECURITY_TYPE = "security_type"
    INDUSTRY = "industry"
    BUSINESS_MODEL = "business_model"
    SCALE = "scale"
    GROWTH = "growth"
    MARGIN = "margin"
    FORWARD_EBITDA = "forward_ebitda"
    PROVIDER_AGREEMENT = "provider_agreement"


class PeerCriterionStatus(DomainEnum):
    PASS = "pass"
    FAIL = "fail"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


class IndustryComparability(DomainEnum):
    SAME_INDUSTRY = "same_industry"
    RELATED_INDUSTRY = "related_industry"
    SECTOR_ONLY = "sector_only"
    MISMATCH = "mismatch"
    UNRESOLVED = "unresolved"


class IssueSeverity(DomainEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


class Frequency(DomainEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    LTM = "ltm"
    NTM = "ntm"
    POINT_IN_TIME = "point_in_time"


class ObservationType(DomainEnum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"


class EstimateCase(DomainEnum):
    LOW = "low"
    AVERAGE = "average"
    HIGH = "high"
    NOT_APPLICABLE = "not_applicable"


class MetricUnit(DomainEnum):
    CURRENCY = "currency"
    CURRENCY_PER_SHARE = "currency_per_share"
    SHARES = "shares"
    RATIO = "ratio"
    PERCENT_DECIMAL = "percent_decimal"
    COUNT = "count"
    PHYSICAL = "physical"


class MetricId(DomainEnum):
    SHARE_PRICE = "share_price"
    VOLUME = "volume"
    MARKET_CAP = "market_cap"
    ENTERPRISE_VALUE = "enterprise_value"
    BETA = "beta"
    SHARES_BASIC = "shares_basic"
    SHARES_DILUTED = "shares_diluted"
    SHARES_OUTSTANDING = "shares_outstanding"
    REVENUE = "revenue"
    GROSS_PROFIT = "gross_profit"
    OPERATING_INCOME = "operating_income"
    EBIT = "ebit"
    INTEREST_EXPENSE = "interest_expense"
    INTEREST_COVERAGE = "interest_coverage"
    EBITDA = "ebitda"
    NET_INCOME = "net_income"
    NET_INCOME_COMMON = "net_income_common"
    EPS = "eps"
    EPS_BASIC = "eps_basic"
    EPS_DILUTED = "eps_diluted"
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPITAL_EXPENDITURE = "capital_expenditure"
    DEPRECIATION_AMORTIZATION = "depreciation_amortization"
    CHANGE_IN_WORKING_CAPITAL = "change_in_working_capital"
    STOCK_BASED_COMPENSATION = "stock_based_compensation"
    FCFF = "fcff"
    FCFE = "fcfe"
    OCF_LESS_CAPEX = "ocf_less_capex"
    PROVIDER_DEFINED_FCF = "provider_defined_fcf"
    FFO = "ffo"
    AFFO = "affo"
    GROSS_DEBT = "gross_debt"
    CASH_AND_EQUIVALENTS = "cash_and_equivalents"
    NET_DEBT = "net_debt"
    BOOK_VALUE = "book_value"
    TANGIBLE_BOOK_VALUE = "tangible_book_value"
    REVENUE_GROWTH = "revenue_growth"
    EBIT_MARGIN = "ebit_margin"
    OPERATING_MARGIN = "operating_margin"
    EBITDA_MARGIN = "ebitda_margin"
    ROIC = "roic"
    ROE = "roe"
    ROTCE = "rotce"


class ExternalReferenceType(DomainEnum):
    FMP_STANDARD_DCF = "fmp_standard_dcf"
    ANALYST_TARGET_LOW = "analyst_target_low"
    ANALYST_TARGET_MEDIAN = "analyst_target_median"
    ANALYST_TARGET_CONSENSUS = "analyst_target_consensus"
    ANALYST_TARGET_HIGH = "analyst_target_high"
    ANALYST_TARGET_RANGE = "analyst_target_range"


class CashFlowDefinition(DomainEnum):
    FCFF = "fcff"
    FCFE = "fcfe"
    OCF_LESS_CAPEX = "ocf_less_capex"
    PROVIDER_DEFINED = "provider_defined"
    UNKNOWN = "unknown"


class DefinitionVerificationStatus(DomainEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class MacroMetric(DomainEnum):
    TREASURY_YIELD = "treasury_yield"


class MacroFrequency(DomainEnum):
    DAILY = "daily"


class SourceAgreementLevel(DomainEnum):
    CONFIRMED = "confirmed"
    WARNING = "warning"
    CONFLICT = "conflict"
    COMPARABLE_UNSCORED = "comparable_unscored"
    NOT_COMPARABLE = "not_comparable"
    NO_VALIDATOR = "no_validator"


class ReconciliationStatus(DomainEnum):
    COMPARED = "compared"
    NOT_COMPARABLE = "not_comparable"
    NO_VALIDATOR = "no_validator"
    VALIDATOR_UNAVAILABLE = "validator_unavailable"


class ReconciliationDatasetType(DomainEnum):
    MARKET = "market"
    STANDARDIZED_ACTUAL = "standardized_actual"
    REPORTED_ACTUAL = "reported_actual"
    FORWARD_CONSENSUS = "forward_consensus"
    VALIDATOR_ESTIMATE = "validator_estimate"
    CASH_FLOW_ESTIMATE = "cash_flow_estimate"


class ReconciliationStream(DomainEnum):
    CURRENT_MARKET_PRICE = "current_market_price"
    HISTORICAL_STANDARDIZED_FINANCIALS = "historical_standardized_financials"
    FORWARD_OPERATING_CONSENSUS = "forward_operating_consensus"
    FORWARD_CASH_FLOW = "forward_cash_flow"
