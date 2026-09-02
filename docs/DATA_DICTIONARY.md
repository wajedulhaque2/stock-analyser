# V1 Data Dictionary

## Canonical records

### CompanyIdentity

| Field | Contract |
|---|---|
| `canonical_symbol` | Application identity; never a provider lookup string. |
| `security_id`, `issuer_id` | Stable traded-security and issuer identities; never assumed identical. |
| `company_name` | Canonical display name. |
| `exchange`, `listing_country` | Listing venue and listing jurisdiction using controlled codes. |
| `issuer_domicile` | Issuer domicile, separate from listing country. |
| `sector`, `industry`, `security_type` | Classification used to select, not fabricate, valuation methods. |
| `reporting_currency` | Currency of financial statements. |
| `quote_currency` | Currency represented after quote-unit normalization. |
| `quote_unit` | Market unit such as `USD`, `GBP`, or `GBp`; distinct from currency. |
| `price_scale` | Multiplier from raw quote unit to quote currency; e.g. GBp to GBP is `0.01`. |
| `fiscal_year_end` | Month/day defining fiscal periods. |
| `provider_symbols` | Map for Yahoo, Fiscal, FMP, Finnhub, Alpha Vantage, and SEC/CIK. |
| `identity_availability` | `AVAILABLE` for complete identity metadata; `PARTIAL` when an otherwise valid depositary-receipt identity lacks a sourced ADR ratio. |

ADRs/depositary receipts identify the traded security, issuer, and underlying security separately. A known ADR ratio must be positive. If the ratio is unknown it remains `None` and identity availability is `PARTIAL`; it is never defaulted to one. Conversion is deferred.

### MetricObservation

Required fields are stable `observation_id`, `metric_id`, finite `value`, `unit`, `frequency`, `observation_type`, `retrieved_at`, `as_of_at`, and `provenance`. Monetary observations also require `currency`. Flow observations require `period_start` and `period_end`; point-in-time observations require a source period end and no period start. A filing can make an instant fact known after its reported period end, so `period_end` and `as_of_at` are deliberately distinct. Fiscal labels (`fiscal_year`, `fiscal_quarter`) supplement dates and never replace them.

`retrieved_at` is when the application retrieved the value; `as_of_at` is when the value is considered valid/current. They are not assumed equal. If retrieval time substitutes for an unavailable source snapshot time, provenance records that explicit transformation. Optional estimate fields include `analyst_count` and distribution metadata; `estimate_case` is required semantically (`low`, `average`, `high`, or `not_applicable` for actuals). `provenance` contains provider, endpoint/dataset, provider symbol, exact provider source metric/tag, both timestamps, transformation steps, input observation identifiers, and configuration/override identity. It must not contain secrets, authorization headers, or proprietary raw payloads. Derived observations cite their input observation IDs.

### Identity resolution

Canonical seed identifiers (`canonical_symbol`, `security_id`, `issuer_id`) are authoritative. Provider symbols remain explicit and optional per source. Field assembly is deterministic: Yahoo leads listing/quote/security fields; Fiscal leads company, classification, reporting-currency, and fiscal-year fields; Fiscal then SEC then Yahoo leads domicile. Same-field disagreement produces a `DataIssue`; reporting currency differing from quote currency is not itself a conflict.

### DataIssue

Fields: `severity` (`INFO`, `WARNING`, `ERROR`, `BLOCKING`), `metric`, `provider`, `reason`, `expected`, `observed`, and `action`. Issues are data, not log strings, and survive to the audit UI.

### ValuationResult

Fields: `method`, `valuation_family`, `low`/`central`/`high`, `currency`, `ValuationMethodStatus` (`VALID`, `PARTIAL`, `UNAVAILABLE`), confidence where applicable, input observation IDs, provenance, warnings/issues, and reason. Unavailable methods contain no monetary values and always state why. `RESOLVED`, `WIDE`, and `UNRESOLVED` are aggregation states and cannot describe this record.

### ExternalValuationReference

A structurally separate record for FMP standard DCF and analyst target low/median/consensus/high references. It carries reference type, value/range, currency, provider, as-of time, and provenance. It neither inherits from nor enters collections of `ValuationResult`.

### HistoricalValuationObservation

Immutable provider-calculated historical multiple evidence, separate from both `MetricObservation` and `ValuationResult`. Required semantic identity is stable `observation_id`, canonical `security_id` and `issuer_id`, provider/provider symbol, controlled `multiple_type`, controlled equity/enterprise `valuation_basis`, controlled denominator, finite dimensionless ratio value, economic `observation_date`, controlled sampling basis, source metric, as-of/retrieval timestamps, provenance, and future-distribution eligibility. Optional `period_end` remains distinct from the observation date; quote/reporting currencies are audit context only and never turn the ratio into a monetary unit.

Milestone 7A supports only `P_E`, `EV_EBITDA`, and `EV_EBIT`. `P_E` is equity-based and uses Fiscal's documented diluted-EPS denominator without inferring forward, normalized, or other P/E variants. `EV_EBITDA` is enterprise-based with EBITDA. `EV_EBIT` is enterprise-based and records Fiscal's specific documented denominator as `provider_operating_profit_as_ebit`; this does not equate generic operating income and EBIT elsewhere.

Eligibility is `ELIGIBLE`, `INELIGIBLE`, or `UNVERIFIED`. Positive, finite, economically verified supported ratios may be eligible. Zero and negative ratios are preserved as source evidence only when finite and are explicitly ineligible; NaN and infinity are rejected. High finite values are retained unchanged—Milestone 7A performs no winsorization, truncation, interpolation, median, percentile, or historical-window calculation. P/FCF and EV/FCF are withheld because Fiscal's generic FCF label does not establish FCFE/FCFF economics.

### HistoricalMultipleDistribution

Immutable, non-valuing descriptive result for one canonical security/issuer, one provider, one multiple, one sampling basis, one explicit window, and one aware `analysis_as_of`. It preserves controlled multiple basis/denominator semantics and carries a stable distribution ID that does not depend on provider-symbol spelling. `HistoricalWindow` is `THREE_YEAR | FIVE_YEAR | TEN_YEAR`; policy defaults to `FIVE_YEAR` but never substitutes a different window.

The inclusive boundary is `window_start <= observation_date <= analysis_as_of.date()`, where the start is a calendar-year offset (including explicit leap-day handling), not an observation-count approximation. Required statistics are p25, median, and p75; diagnostics are minimum, maximum, mean, population standard deviation, IQR, observed span, requested span, span coverage, calendar-month count, candidate/eligible/ineligible counts, eligible fraction, duplicate counts, and supporting observation IDs. Ratios remain dimensionless and no FX conversion occurs.

`HistoricalDistributionUsability` is `USABLE | PARTIAL | INSUFFICIENT`. Default daily policies are 3Y: 500 observations/80% span, 5Y: 750/80%, and 10Y: 1,250/80%. Both tests must pass for `USABLE`; one passing yields `PARTIAL`; neither, no eligible evidence, or a duplicate conflict yields `INSUFFICIENT`. The thresholds are initial engineering controls, not economic truths. There is no ineligible-share rejection threshold in 7B; the fraction is diagnostic only.

Quantiles use explicit Type-7 linear interpolation: sorted position `(n - 1)q`, with linear interpolation between adjacent values. Mean is descriptive only and does not replace the median as the future central historical statistic. The engine performs no winsorization, trimming, clipping, sigma/IQR deletion, interpolation, monthly resampling, regime/cycle adjustment, or window blending. Exact semantic-date duplicates are counted once; conflicting values on the same canonical provider/date key fail the numeric result closed. Providers are never mixed.

Daily observations are serially dependent and do not represent independent fundamental regimes. `sampling` remains `DAILY`, `independent_regime_count` remains `None`/unknown, and unique calendar months are a coverage diagnostic only. Missing market days remain missing. The result contains no forward estimate, fair value, target price, upside/downside, peer, DCF, or aggregation field.

### CashFlowDefinitionEvidence

Safe audit metadata linking a provider metric/endpoint to a controlled `CashFlowDefinition`: `FCFF`, `FCFE`, `OCF_LESS_CAPEX`, `PROVIDER_DEFINED`, or `UNKNOWN`. Verification status is `VERIFIED` or `UNVERIFIED`. A verified definition requires an aware `verified_at` timestamp and a short safe reference; raw documentation and provider payloads are forbidden. Future valuation code asks `is_verified_fcff` or `is_verified_fcfe` rather than inferring economics from a field name.

### EstimateRevision

Immutable annual or quarterly revision evidence, separate from estimate-level `MetricObservation`. It records metric, fiscal period, revision window, current/prior estimate when supplied, up/down revision counts when supplied, metric-specific analyst count, currency/unit, timestamps, provider, and provenance. At least one revision evidence field is required; absent provider fields remain `None`.

### MacroObservation and risk-free result

`MacroObservation` is not company-specific. It records series ID, controlled macro metric, finite canonical value/unit, currency, observation date, frequency, source/retrieval timestamps, provenance, and optional provider real-time dates. Milestone 5 implements only FRED `DGS10` as a daily USD `TREASURY_YIELD`. Provider percent-per-annum values are divided by 100 and stored as `percent_decimal` (for example, `4.70` becomes `0.047`). Missing `.` observations are skipped, never zero-filled.

The risk-free resolver returns a structured availability result. USD selects the latest valid FRED DGS10 observation on or before the requested aware as-of date. GBP, EUR, JPY, and all other currencies are unavailable until an approved currency-specific source exists; USD is never substituted.

### ReconciliationResult

Immutable audit record for one canonical observation and at most one validator observation. It retains canonical/validator observation IDs, stable security and issuer IDs, providers and provider symbols, standardized/reported/consensus dataset identities, semantic dimensions, `comparison_as_of`, both source/retrieval timestamps, values, differences, controlled status/agreement, applied tolerance, mismatch reasons, capability availability, and structured issues. Provider symbols are audit metadata only; they are never equality keys.

`SourceAgreementLevel` is `CONFIRMED | WARNING | CONFLICT | COMPARABLE_UNSCORED | NOT_COMPARABLE | NO_VALIDATOR`. `ReconciliationStatus` distinguishes a completed comparison, semantic non-comparability, normal validator absence, and unavailable validator capability. Capability `LOCKED`/`ERROR` is not a financial conflict.

Semantic comparison requires the same stable security and issuer identity, economic metric, observation type, frequency, fiscal period, estimate case, currency, and unit. Provider-defined FCF is not equivalent to FCFF/FCFE without linked verified evidence. Retrieval timestamps may differ. Only observations with `as_of_at <= comparison_as_of` are eligible, and the latest otherwise identical eligible observation is selected for each provider. The default policy deliberately configures no maximum as-of gap; a caller may set one explicitly.

Absolute difference is `abs(validator - canonical)`. Relative difference is `abs(validator - canonical) / abs(canonical)` when canonical is nonzero. Zero versus zero is confirmed exact with relative difference zero. Zero versus nonzero is conflict with no relative percentage. Reconciliation never mutates or averages the canonical value; multiple validators produce independent records and count-only audit summaries.

### Layer-specific statuses

`DataAvailability` = `AVAILABLE | PARTIAL | UNAVAILABLE`; `CapabilityStatus` = `AVAILABLE | LOCKED | UNAVAILABLE | ERROR`; `ValuationMethodStatus` = `VALID | PARTIAL | UNAVAILABLE`; `AggregationStatus` = `RESOLVED | WIDE | UNRESOLVED | UNAVAILABLE`; `HistoricalDistributionUsability` = `USABLE | PARTIAL | INSUFFICIENT`; `DenominatorCompatibilityStatus` = `EXACT | VERIFIED_EQUIVALENT | INCOMPATIBLE | UNVERIFIED | UNAVAILABLE`; `CapitalStructureCompleteness` = `COMPLETE | PARTIAL | UNAVAILABLE`; `OwnHistoryMethodStatus` = `READY | PARTIAL | NOT_READY`; `CoverageLevel` = `HIGH | MEDIUM | LIMITED | INSUFFICIENT`; `EvidenceConfidence` = `HIGH | MEDIUM | LOW | UNKNOWN`; `ValuationReadiness` = `READY | PARTIAL | NOT_READY`; `DimensionStatus` = `PASS | PARTIAL | FAIL | NOT_AVAILABLE | NOT_EVALUATED`; `IssueSeverity` = `INFO | WARNING | ERROR | BLOCKING`. Equal spellings do not make these enum types interchangeable.

## Period and estimate semantics

| Dimension | Allowed values and rule |
|---|---|
| `frequency` | `annual`, `quarterly`, `LTM`, `NTM`, `point_in_time`. |
| `observation_type` | `actual` or `estimate`; never inferred from a column or endpoint name downstream. |
| `estimate_case` | `low`, `average`, `high`, `not_applicable`. Validate `low <= average <= high` when values are comparable. |
| FY1/FY2 | First/second forward fiscal year relative to an explicit as-of date. They are not synonyms for NTM. |
| NTM | Sum of the next four fiscal-quarter flow estimates after the as-of date. Require four appropriate, non-overlapping quarters. |
| LTM | Sum of the last four reported fiscal quarters. Do not mix with annual actuals implicitly. |
| point-in-time | Balance-sheet, share-price, share-count, debt, cash, and market-cap observations as of a date. |

Derived observations retain input provenance. Revenue growth is derived from like-for-like consensus revenue periods; EBIT margin from EBIT/revenue of the same period/case/currency; EBITDA margin analogously. No separately invented growth or margin is needed when the levels exist.

## Milestone 10A reverse-DCF evidence contracts

`ForwardOperatingPeriod` retains canonical target IDs, explicit `FY<n>` fiscal horizon and dates, FMP `AVERAGE` revenue/EBIT/EBITDA levels, input observation IDs, same-snapshot derived EBIT margin, consecutive revenue growth, analyst counts where sourced, currency/base unit, timestamps, status, issues, warnings, and provenance. `ForwardOperatingTrajectory` preserves every supplied annual fiscal period, identifies missing fiscal years without interpolation, and reports revenue, EBIT, margin, and overall readiness separately. FY1 is the first annual period ending after `analysis_as_of`, never NTM.

`ReverseDcfActualBase` uses the existing `select_canonical_actual` service for the latest Fiscal annual revenue period. Same-period explicit EBIT, Operating Income, and EBITDA remain separate fields. Operating Income cannot establish actual-to-forward EBIT continuity. FY1 growth is derived only when canonical actual revenue is exactly the preceding fiscal year in the same currency/base unit.

`MarketEnterpriseValueAnchor` accepts only positive, fresh, dated Fiscal `calculated_tev` point-in-time evidence in valuation currency. `ReverseDcfReinvestmentReadiness` inventories forward CapEx, D&A, change-NWC, verified FCFF/FCFE, generic FCF, historical components, sales-to-capital evidence, and an explicitly approved methodology. Generic FCF and OCF-minus-CapEx are not FCFF. `ReverseDcfTerminalReadiness` contains policy statuses only; it has no default terminal-growth or margin number.

`ReverseDcfReadiness` reports controlled stages from `IDENTITY` through `READY_FOR_SOLVER` and retains separate `READY`/`PARTIAL`/`NOT_READY` formulation results for consensus FCFF, revenue/margin/reinvestment, market-implied growth, and market-implied terminal-margin families. It contains no solved assumption, present value, terminal value, fair value, or current-price field.

Rows from an estimate endpoint are not inherently forward. A forward row must be an estimate and have a fiscal period ending after its explicit as-of date. Historical rows in the same response remain historical.

## Milestone 10B reverse-DCF contracts

`SalesToCapitalEvidence` is immutable, provider-independent, and dimensionless. It records target identity, a finite positive value, economic definition, controlled source type, source and methodology, dated snapshot, optional currency scope, required company or industry scope for configured evidence, status, issues/warnings, policy, and provenance. Eligible source types are verified external, verified company-derived, and explicitly configured external. Configured evidence has no numeric default and requires caller-supplied value, source, date, methodology, scope, and provenance.

`ReverseDcfCashFlowPeriod` retains unchanged consensus revenue, EBIT, and margin plus marginal tax, NOPAT, preceding-revenue evidence, revenue change, sales-to-capital, reinvestment, and FCFF. `ReverseDcfCashFlowPath` binds periods to one identity, snapshot, currency, trajectory, discrete annual timing convention, and separate FY1-base/tax/sales-to-capital readiness. FY1 is discount period 1. A path is `READY` only when every explicit annual FCFF is complete; missing FY1 base is never zero reinvestment.

`MarketImpliedTerminalGrowthResult` is an immutable enterprise-expectations result, not fair value. It retains fixed trajectory/path/TEV/tax/sales-to-capital/WACC IDs, final-consensus-margin policy, numerical search domain, initial function values, final bracket, implied terminal growth when solved, terminal economics, explicit and terminal present values, modeled and observed enterprise values, residual, iterations, controlled status, issues, policies, supporting IDs, and provenance. Non-solved results never expose an implied terminal growth.

## Milestone 10C execution and scenario contracts

`ReverseDcfScenarioAssumption` is immutable provider-independent scenario evidence. Controlled assumption types are only `PRECEDING_ANNUAL_REVENUE`, `SALES_TO_CAPITAL`, and `WACC`; controlled source categories are user-supplied, external research, and configured scenario. Every READY assumption requires explicit source/methodology/rationale/entered-by metadata and provenance. Revenue must be positive base-currency annual evidence for an explicit fiscal year and period end; LTM/NTM/quarterly inputs are invalid. Sales-to-capital is a positive dimensionless ratio. WACC is a positive decimal rate below one. None has a numeric default.

`ReverseDcfPrecedingRevenueInput`, `ReverseDcfSalesToCapitalInput`, and `ReverseDcfDiscountRateInput` bind the value actually used to either canonical evidence or a scenario-assumption ID. The discount-rate source distinguishes `PRODUCTION_WACC` from `SCENARIO_WACC` and always retains the separate production-WACC status. A scenario rate never constructs or changes `WaccResult`.

`ReverseDcfExecutionInputs` records the explicit `CANONICAL_EVIDENCE` or `EXPLICIT_SCENARIO` mode, target/snapshot/currency, fixed trajectory/TEV/tax/terminal policy, selected economic input IDs, canonical evidence IDs/statuses, scenario assumption IDs, policies, issues, and provenance. There is no auto/fallback mode. In scenario mode, an explicitly supplied authorized assumption overrides that concept for one execution while the canonical reference remains visible.

`ReverseDcfExecutionResult` embeds the unchanged 10B solver result and execution inputs, retains canonical blockers, safe scenario labels, final consensus growth/margin comparators, and publication eligibility. `CANONICAL` requires a solved all-canonical execution with no scenario IDs. A solved explicit-scenario execution is permanently `SCENARIO_ONLY`; incomplete execution is `UNAVAILABLE`. Every reverse-DCF expectation result has `central_valuation_eligible=false` and no fair-value field.

## Milestone 11A cross-family publication contracts

`FamilyValuationEvidence` is the immutable provider-independent publication view of exactly one upstream-selected `OWN_HISTORY` or `PEER` result. It binds target security/issuer, selected method and source-result ID, aware analysis snapshot, currency and explicit per-share unit, lower/central/upper values, `ValuationMethodStatus`, central eligibility, issues, warnings, supporting/policy IDs, and provenance. A central-eligible family must be complete, finite, positive, ordered, and `VALID`. Partial and unavailable results remain visible but never count; malformed ranges are rejected rather than reordered.

`PublicationSupplementalEvidence` classifies `REVERSE_DCF_CANONICAL_EXPECTATION`, `REVERSE_DCF_SCENARIO`, `EXTERNAL_ANALYST_TARGET`, and `EXTERNAL_PROVIDER_DCF_REFERENCE`. These categories are structurally and categorically central-ineligible. They remain disclosed through exact IDs and provenance but cannot rescue the family minimum or affect publication mathematics.

`PublicationDispersionDiagnostics` records family count, minimum/maximum/Type-7 median family central, central spread and spread/median when defined, family-envelope width, and common-overlap width. These are transparent diagnostics, not a confidence/agreement score and not a status threshold.

`ValuationPublicationResult` binds one target and analysis snapshot to all retained family/supplemental evidence, eligible family IDs/count, currency/unit, `AggregationStatus`, resolved central and its `MEDIAN_OF_FAMILY_CENTRALS` label, explicitly labeled `FAMILY_ENVELOPE`, `COMMON_OVERLAP`, or `SINGLE_FAMILY_RANGE`, diagnostics, blockers, issues/warnings, policy, and provenance. Its central exists only for `RESOLVED`; `WIDE`, `UNRESOLVED`, and `UNAVAILABLE` contain no hidden central. No market-price, price-comparison, margin-of-safety, score, stance, recommendation, or UI field exists.

## Canonical metrics

### Market and identity

`share_price`, `volume`, `average_daily_value`, `market_cap`, `enterprise_value`, `beta`, `shares_basic`, `shares_diluted`, `shares_outstanding`, `adr_ratio`, `net_debt`, `cash_and_equivalents`, and `gross_debt`.

### Income statement and profitability

`revenue`, `gross_profit`, `operating_income`, `EBIT`, `EBITDA`, generic `net_income`, `net_income_common`, generic `EPS`, `EPS_basic`, `EPS_diluted`, `tax_expense`, `interest_expense`, `revenue_growth`, `EBIT_margin`, `operating_margin`, `EBITDA_margin`, `net_margin`, `ROIC`, `ROE`, `ROTCE`, `book_value`, and `tangible_book_value`. Generic FMP net income and EPS remain distinct because the initial provider contract does not establish common-shareholder net income or diluted/basic EPS. `operating_income` and `EBIT` are likewise distinct.

### Cash flow and reinvestment

`operating_cash_flow`, `capital_expenditure`, `depreciation_amortization`, `change_in_working_capital`, `stock_based_compensation`, `FCFF`, `FCFE`, `OCF_less_CapEx`, `provider_defined_fcf`, `FFO`, and `AFFO` are distinct identities. Provider-generic FCF cannot be promoted to FCFF/FCFE or support P/FCF or EV/FCF eligibility until its economic definition is documented. Sign convention: CapEx and working-capital investment are positive uses of cash in canonical valuation formulas, regardless of provider presentation.

### Consensus and reference metrics

Milestone 4 supports annual FMP estimate cases for revenue, EBIT, EBITDA, generic net income, and generic EPS. Low/average/high are independent consensus range cases, not bear/base/bull paths. `numAnalystsRevenue` applies only to revenue cases and `numAnalystsEps` only to generic EPS cases; EBIT, EBITDA, and generic net income retain `analyst_count=None` until explicitly sourced. Monetary statement estimates use resolved reporting currency, EPS uses currency-per-share, and quote-unit scaling is not applied.

FY1 is the first annual fiscal period whose `period_end` is after the explicit snapshot `as_of_at`; an unfinished current fiscal year can therefore be FY1. FY1 is not NTM. Historical rows returned by an estimate endpoint remain estimates for audit but are excluded from forward horizons and derivations. Quarterly estimates and NTM construction are deferred.

Allowed Milestone 4 derivations are average-case adjacent-year revenue growth and same-period average EBIT/revenue or EBITDA/revenue margins. They use aligned annual inputs only and provenance cites every input observation ID. No low/high growth or margin paths are constructed.

Milestone 5 adds independent Finnhub validator estimates for annual revenue, EBIT, EBITDA, generic net income, generic EPS, operating cash flow, CapEx, and generic FCF where the corresponding endpoint capability is available. CapEx becomes a positive use of cash only under an explicit provider sign contract; range endpoints are inverted when converting a negative-outflow distribution. Generic FCF remains `provider_defined_fcf` with unverified definition evidence unless an injected semantic contract explicitly verifies FCFF, FCFE, or OCF-less-CapEx. The adapter never derives OCF less CapEx.

Alpha Vantage contributes independent annual and quarterly revenue/generic-EPS validator levels plus separate 30-day revision evidence. Its adapter accepts the characterized split synthetic contract and the verified live unified `estimates` collection; live fiscal-year/fiscal-quarter rows are separated by the provider horizon, and snake-case source fields remain in provenance. The verified live response supplies EPS 30-day prior/revision evidence but no equivalent revenue revision fields, so revenue revisions remain absent. Generic EPS is not basic or diluted EPS. Milestone 6A can compare aligned annual FMP canonical observations independently with Finnhub or Alpha validators; it never overwrites or averages FMP. Quarterly canonical consensus remains unapproved.

`analyst_target_low`, `analyst_target_median`, `analyst_target_consensus`, `analyst_target_high`, and `external_fmp_dcf` are `ExternalValuationReference` records only. They use resolved quote currency, carry zero valuation weight, and cannot enter a `ValuationResult` collection.

### Valuation multiples

Equity multiples: `PE`, `price_to_book`, `price_to_tangible_book`, `price_to_FCF`, `price_to_FFO`, and `price_to_AFFO` multiply equity-denominator estimates and produce equity value. Enterprise multiples: `EV_to_EBITDA`, `EV_to_EBIT`, `EV_to_revenue`, and verified `EV_to_FCFF` produce enterprise value and require an explicit enterprise-to-equity bridge.

The preceding list is the future methodology vocabulary, not the Milestone 7A supported ingestion set. The implemented historical input boundary is deliberately limited to `P_E`, `EV_EBITDA`, and `EV_EBIT`; it calculates no value and applies no multiple to a forecast.

## Enterprise/equity and shares

`equity_value = enterprise_value - net_debt - preferred_equity - noncontrolling_interests + nonoperating_assets` using consistently dated, currency-matched inputs. Canonical `net_debt = gross_debt - cash_and_equivalents`; negative net debt means net cash and therefore increases equity value. Never silently coerce missing net debt to zero.

Per-share value uses the share concept appropriate to the denominator. Historical actual EPS uses reported weighted-average shares; forward valuation normally uses consensus diluted EPS directly or a documented forward diluted-share assumption. Where both forecast net income and diluted EPS exist, `implied_diluted_shares = net_income_common / EPS_diluted` is a cross-check, not an automatic overwrite.

## Currency and units

Currency uses ISO 4217 codes where possible. Unit is independent: `currency`, `currency_per_share`, `shares`, `ratio`, `percent_decimal`, or physical/KPI unit. Scale (`ones`, `thousands`, `millions`, `billions`) is normalized before reconciliation and retained in provenance. Quote subunits such as GBp are units, not reporting currencies. FX translation requires an as-of date, rate source, currency pair, and transformation record. Do not compare or aggregate mismatched currencies.

## Source agreement and coverage

Source agreement is configured by metric family, not scattered constants. Initial bands: price/reported revenue confirmed `<1%`, warning `>=1%` through `3%`, conflict `>3%`; forward revenue confirmed `<3%`, warning `>=3%` through `10%`, conflict `>10%`; forward EPS confirmed `<5%`, warning `>=5%` through `15%`, conflict `>15%`. Comparable metrics without an approved band are `COMPARABLE_UNSCORED`.

Milestone 6A records agreement and issues only. Milestone 6B consumes those records unchanged and classifies evidence breadth separately from reliability and future-method readiness. It does not calculate a valuation, monetary result, aggregation state, or investment stance.

### CoverageAssessment

Immutable audit output with explicit `analysis_as_of`, policy ID, `CoverageLevel`, `EvidenceConfidence`, dimension results, per-family readiness, critical issues, warnings, supporting observation/reconciliation IDs, and reasons. A `CoverageDimensionResult` carries a controlled dimension/status, reason, counts, relevant dates, available/missing metrics, evidence IDs, and per-horizon analyst records where relevant. Milestone 7C leaves the 6B coverage service unchanged: historical valuation and peers remain `NOT_EVALUATED` there until a separately authorized integration. A 7C method-prerequisite result may be `READY`, but family coverage remains conservative because the 7D application engine does not yet exist.

### ForwardDenominatorSelection and ForwardDenominatorAlignment

`ForwardDenominatorSelection` identifies exactly one `FY1` or `FY2` annual FMP consensus period and one explicit `LOW`, `AVERAGE`, or `HIGH` case. Policy defaults to FY1 average. FY1 is the first annual fiscal period ending after `analysis_as_of`; it is never NTM. All available analyst range cases remain auditable, but no bear/base/bull scenario is created.

`ForwardDenominatorAlignment` records one historical multiple/denominator, its canonical forward metric, controlled compatibility status, both economic definitions, reason, policy, distribution ID, forward observation IDs, provenance, and optional explicit semantic-evidence ID. Fiscal diluted EPS versus FMP generic EPS defaults to `UNVERIFIED`; Fiscal provider-documented Operating Profit versus FMP generic EBIT defaults to `UNVERIFIED`; canonical EBITDA versus canonical FMP EBITDA is `EXACT` under the approved V1 policy. `DenominatorSemanticEvidence` can explicitly establish only `VERIFIED_EQUIVALENT` or `INCOMPATIBLE`; matching names or free text alone cannot.

### CapitalStructureSnapshot

Immutable current bridge evidence for one canonical security/issuer and aware `analysis_as_of`. It independently selects the latest eligible canonical actual cash, gross debt, and explicitly requested share-count concept on or before the snapshot. `ShareCountBasis` distinguishes `SHARES_OUTSTANDING`, `BASIC_WEIGHTED_AVERAGE`, and `DILUTED_WEIGHTED_AVERAGE`; the default enterprise-method requirement is shares outstanding. Provider symbols remain audit metadata.

`net_debt = gross_debt - cash_and_equivalents` is derived only when both values exist and currencies match, with both observation IDs and provenance retained. Missing cash/debt/shares remain `None`, never zero. Currency mismatch performs no FX and prevents net-debt derivation. Cash/debt date gaps are always visible; the default invents no staleness threshold, while an explicit maximum-gap policy can downgrade completeness. `EnterpriseAdjustmentRequirement` can explicitly require preferred equity, non-controlling interest, or other enterprise adjustments; because approved canonical observations do not yet supply them, such a requirement remains missing and is never treated as zero. Completeness is `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`.

### OwnHistoryMethodReadiness

Immutable, non-valuing prerequisite result for one multiple, one selected historical window, one FY period, and one estimate case. It links the distribution, forward selection/alignment, required capital snapshot, status, blocking reasons, warnings, supporting observations, provenance, and policy. P/E requires a usable distribution and verified compatible EPS but no enterprise bridge. EV/EBITDA and EV/EBIT additionally require a complete currency-compatible bridge and suitable explicit share basis; EV/EBIT also requires verified Operating Profit/EBIT equivalence. Missing ADR conversion data blocks affected methods. The result contains no fair value, target, multiple application, upside/downside, weight, aggregation, or stance.

### DirectEnterpriseEquityBridge

Immutable Fiscal aggregate bridge evidence for one canonical security/issuer and aware `analysis_as_of`. `EnterpriseBridgeMethod` distinguishes `COMPONENT_BRIDGE` from `DIRECT_TEV_MARKET_CAP_BRIDGE`; the two methods are never averaged. The direct equation is `enterprise_equity_adjustment = enterprise_value - market_cap`, where Fiscal documents TEV as market cap plus net debt, preferred stock, and minority interests. The aggregate therefore avoids pretending missing component debt/preferred/NCI observations are zero.

Direct inputs must be canonical point-in-time actuals from the Fiscal symbol bound to the same canonical identity, dated at or before the snapshot, finite, positive where economically required, and currency-matched. Daily TEV and market cap require an exact common date by default; only a named maximum-gap policy can permit a mismatch, which remains visible. No FX is performed. `ShareCountSemantics` distinguishes `ISSUER_SHARES`, documented Fiscal `ADS_CONVERTED` shares, and `UNVERIFIED`; unsuitable ADR/share semantics keep the bridge incomplete. The contract carries input IDs/dates, source provenance, completeness, issues, diagnostics, and policy, but no valuation output.

### OwnHistoryValuationPoint and OwnHistoryValuationResult

`HistoricalStatistic` is controlled as `P25`, `MEDIAN`, or `P75`. Each immutable `OwnHistoryValuationPoint` applies exactly one statistic from the readiness-selected `USABLE` distribution to the same selected annual `AVERAGE` forward observation. The point records historical multiple, forward denominator, equity/enterprise basis, derived enterprise value where applicable, exactly one enterprise adjustment, implied equity value, approved share basis/count, per-share value, currency, status, issues, observation/distribution/bridge IDs, policy, aware analysis timestamp, and canonical/derived provenance.

`OwnHistoryValuationResult` holds the ordered lower/P25, central/median, and upper/P75 points for one multiple, window, FY period, and readiness result. It embeds the existing generic `ValuationResult`; `VALID`, `PARTIAL`, and `UNAVAILABLE` mirror the valid point values exactly. All three points use the same `AVERAGE` denominator—analyst low/high are not scenarios and do not drive the historical band. Windows and FY1/FY2 are never blended.

P/E is already per-share: `per_share_value = P/E statistic × compatible forward EPS`; it carries no bridge or share division. Enterprise points calculate `implied_enterprise_value = multiple × forward EBITDA/EBIT`, `implied_equity_value = implied_enterprise_value - enterprise_equity_adjustment`, and `per_share_value = implied_equity_value / approved shares`. A direct bridge retains Fiscal `TEV - market cap`; a component bridge uses its validated net debt. Exactly the bridge named by readiness is accepted.

Canonical monetary values must already be normalized to base currency units upstream. The service accepts only the expected canonical monetary unit and one matching currency; it contains no provider-specific thousand/million/billion conversion and performs no FX. Non-finite/non-positive denominators, shares, or historical statistics fail closed. A non-positive enterprise equity point remains unavailable with its un-floored derived value; other independently valid points may produce a `PARTIAL` method. No current price, target, upside/downside, external reference, aggregation, or stance is present.

### Own-history orchestration audit

`OwnHistoryOrchestrationResult` is an immutable, non-aggregated assembly result over one resolved identity, one explicit historical window, one explicit FY1/FY2 period, and the `AVERAGE` estimate case. Each `OwnHistoryMethodAudit` retains its distribution, exact forward observation, readiness, optional 7D valuation, failure stage, canonical unit/currency/scale checks, and independent arithmetic assertions. `NOT_READY` and `PARTIAL` methods remain visible but are not executed. The result has no current price, external valuation reference, overall fair value, method weight, stance, or UI state.

`PortabilityClassification` is the live-audit-only controlled taxonomy for `PROVIDER_BOUNDARY`, `IDENTITY`, `HISTORICAL_DISTRIBUTION`, `FORWARD_CONSENSUS`, `DENOMINATOR_ALIGNMENT`, `CAPITAL_BRIDGE`, `SHARE_SEMANTICS`, `CURRENCY_UNIT`, `ORCHESTRATION`, and `EXPECTED_UNAVAILABLE`. Classification follows the earliest failed canonical stage rather than a downstream missing-input symptom.

Canonical listing symbols retain venue suffixes such as `.L`. Provider-specific lookup symbols may differ and stay explicit in `CompanyIdentity.provider_symbols` and provenance. Reporting currency, quote currency, and quote unit remain separate: a GBP enterprise valuation divided by shares yields GBP/share even when the security is quoted in GBp. `price_scale` applies only to quote prices and never to financial observations or own-history valuation outputs.

### CanonicalActualSelection

`CanonicalActualSelection` is the reusable fail-closed policy result for repeated canonical reported actuals. It records metric/frequency/period, analysis cut-off, candidate count/IDs, deduplicated IDs, all provenance, controlled status/reason, and at most one selected `MetricObservation`. Exact same-value observations with compatible source semantics deduplicate deterministically. Different values select only when a unique latest observation has explicit provider source/revision chronology. Retrieval time, array order, largest/smallest value, and averaging are never selectors. Different providers, datasets, provider symbols, or provider source metrics remain incompatible; SEC never silently replaces Fiscal.

### ProviderPeerFinancialResolution

`ProviderPeerFinancialResolution` is provider-bound lookup metadata, not identity. For Fiscal it binds the already established provider issuer/security IDs and provider symbol to an opaque `companyKey` only when the same provider record carries an exact `companyFiscalIdentifier` match. Status is `AVAILABLE`, `UNAVAILABLE`, or `AMBIGUOUS`; duplicate stable-ID claims, identifier/listing conflicts, missing keys, and unmatched records fail closed. A `companyKey` never becomes a canonical issuer/security ID and is not rendered by the safe audit. Ticker, name, relationship, reasoning, Yahoo, and FMP are prohibited resolution keys.

Both summary-verified and profile-enriched peers use the existing Fiscal standardized-financial normalizer and canonical `MetricObservation` contract. Annual revenue scale, two-period revenue growth, and same-period revenue/EBITDA margin continue through unchanged peer-selection and `select_canonical_actual` rules. No annual value is constructed from quarters, no conflicting actuals are averaged, no FX is introduced, and EBITDA is never derived from operating income plus D&A.

### PeerIdentityEvidence, peer selection, and method-data readiness

`PeerCandidate` is a canonical-identity-bound discovery record, not a validated comparable. `PeerIdentityEvidence` explicitly distinguishes `SUMMARY_VERIFIED` from `PROFILE_VERIFIED`. Fiscal summary identity requires stable `companyFiscalIdentifier` and `securityFiscalIdentifier` plus primary-listing ticker and exchange; security type is not an identity requirement. Missing optional profile enrichment does not erase a sufficient summary identity, and summary identity never pretends to contain domicile, currencies, quote unit/scale, or fiscal-year-end fields that were absent. Company name, ticker text, relationship, and provider reasoning cannot establish identity or security class.

`PeerSecurityEligibilityEvidence` is a separate provenance-bound record attached to an already established canonical issuer/security. Its status is `VERIFIED_COMMON_EQUITY`, `VERIFIED_OTHER_EQUITY`, `VERIFIED_NON_EQUITY`, or `UNVERIFIED`. Missing type and incompatible external listing symbol/exchange remain `UNVERIFIED`; they never erase or redefine identity. Controlled common-equity evidence may pass. ETF, fund, preferred, warrant, bond, right, and other unsupported classes exclude under the explicit security criterion. `companyType=operating_company` is issuer metadata and is never treated as security-class evidence.

`PeerComparabilityEvidence` retains the separate identity and security-eligibility records plus controlled economic criteria for self reference, industry, business model, scale, growth, margin, and provider agreement. Forward EBITDA is deliberately absent from comparability. Criterion status is `PASS`, `FAIL`, `UNRESOLVED`, or `NOT_EVALUATED`; industry level is `SAME_INDUSTRY`, `RELATED_INDUSTRY`, `SECTOR_ONLY`, `MISMATCH`, or `UNRESOLVED`. Financial criteria consume only deterministically selected actuals.

`PeerSelectionResult` is `INCLUDED`, `EXCLUDED`, `UNVERIFIED`, or `UNAVAILABLE` with controlled economic reason codes. Hard exclusions cover canonical identity mismatch, self reference, duplicate issuer, verified wrong security type, industry/business-model mismatch, and evaluated scale/growth/margin mismatch. Unknown security eligibility, sector-only or unknown industry, unresolved ADR conversion, and insufficient actual financial evidence remain unverified. Missing forward EBITDA never changes economic membership.

`PeerMethodDataReadiness` is a structurally separate EV/EBITDA input-coverage record with `READY`, `PARTIAL`, or `UNAVAILABLE`, explicit forward-denominator and future required-input booleans, controlled reasons, supporting observation IDs, and provenance. It contains no multiple or valuation. `PeerSet` holds one readiness record per candidate while retaining the unchanged minimum of three independent economically included issuers. No peer statistic, peer value, target fair value, current price, premium/discount, aggregation, or stance exists in these contracts.

### Peer EV/EBITDA method-data contracts

`PeerProviderIdentityBinding` proves that a provider security/issuer maps to one canonical peer through an equal normalized CIK, ISIN, or CUSIP. Provider symbol is retained as lookup provenance and is never the join key. `PeerMetricEvidence` binds each canonical observation to its peer security and issuer so target-company inputs cannot be substituted.

`PeerMethodDataEvidence` records the immutable 8A selection status, target and peer identities, selection reference, analysis cut-off, selected FY1/FY2 policy, `AVERAGE` case, chosen TEV and EBITDA evidence, stable identity binding, status, controlled reasons, and provenance. Only an `INCLUDED` selection can become `AVAILABLE`. Point-in-time TEV must be on/before the snapshot and at most seven calendar days old by default. Forward EBITDA must be annual, positive, on an estimate snapshot at/before analysis, and selected from the peer's own fiscal calendar; FY1 is the first annual end after analysis and is not NTM.

`PeerValuationObservation` is one same-peer `enterprise_value / forward_ebitda` ratio. It retains exact dates, observation IDs, matching currency, canonical base-unit inputs, dimensionless `RATIO` output, policy, issues, warnings, and provenance. It contains no target value. Non-positive/non-finite inputs, currency mismatch, future/stale evidence, missing stable FMP identity, and conflicting latest evidence withhold the observation. High finite positive ratios remain unchanged.

`PeerValuationSubset` partitions only economically included issuer IDs into method-data available/unavailable sets, retains at most one valid observation per issuer, and requires three independent observations for `USABLE`. It may be `PARTIAL`, `INSUFFICIENT`, or `UNAVAILABLE` independently of `PeerSetStatus`; non-included candidates can never rescue it. No median, percentile, outlier rule, target application, current price, DCF, aggregation, stance, or UI output exists in Milestone 8B.

### Peer EV/EBITDA distribution contract

`PeerMultipleDistribution` is the immutable provider-independent cross-sectional result for one 8B subset. It retains target identity, peer-set and subset IDs, `EV_EBITDA`, enterprise basis, analysis snapshot, one FY1/FY2 policy, `AVERAGE` case, the exact observation and independent issuer IDs, sample count, upstream status, controlled usability, provenance, issues, and warnings. Its dimensionless descriptive evidence is minimum, Type-7 P25, median, Type-7 P75, maximum, mean, population standard deviation, IQR, and transparent IQR/median, maximum/median, and minimum/median ratios.

Median is the primary statistic; mean is descriptive only. The initial policy performs no clipping, winsorization, IQR/sigma deletion, cap, or other automatic observation removal, and records that no observations were altered or excluded. Every finite positive valid 8B observation is retained. Duplicate issuers or mixed target, peer-set, snapshot, method, basis, FY policy, or estimate case fail closed. Different peer fiscal period ends are valid under one FY policy, and already validated dimensionless ratios may coexist across underlying currencies without FX or comparison of absolute inputs. Three independent issuers are required for `USABLE`; zero observations produce `UNAVAILABLE` without manufactured statistics. Target denominator, capital bridge, fair value, current price, own-history combination, DCF, aggregation, stance, and UI remain outside 8C.

### Target peer EV/EBITDA valuation contracts

`PeerTargetValuationSelection` binds one target security/issuer and analysis snapshot to the exact 8C distribution, peer set, 8B subset, annual FY1/FY2 `AVERAGE` target EBITDA observation, approved bridge method/ID, and approved share basis/observation. The selected target FY must equal the distribution FY policy; FY1 remains the first annual period after analysis and is not NTM.

`PeerTargetValuationPoint` records one controlled peer statistic (`P25`, `MEDIAN`, or `P75`), dimensionless peer multiple, canonical target EBITDA, implied enterprise value, exactly one enterprise-equity adjustment, implied equity value, approved share count/basis, per-share value when positive, result currency, bridge identity, all supporting IDs, issues, and derived provenance. Its equations are `implied EV = peer multiple × target EBITDA`, `implied equity = implied EV - adjustment`, and `per share = implied equity / approved shares`.

`PeerTargetValuationResult` retains the ordered lower/P25, central/median, and upper/P75 point traces and an exact generic `ValuationResult` view for the independent peer family. Three valid points are `VALID`; one or two valid points are `PARTIAL`; none are `UNAVAILABLE`. Non-positive equity remains in the safe point trace without a per-share value and is never floored. An unusable distribution returns `UNAVAILABLE` without requesting target inputs or manufacturing points. Direct TEV-minus-market-cap and component net-debt bridges remain separate and are never averaged. Target EBITDA and bridge currency must match; no FX, scale compensation, or GBP-to-GBp conversion occurs. Current price, external references, own-history combination, DCF, aggregation, stance, and UI are absent.

### Peer-family orchestration contract

`PeerFamilyOrchestrationResult` is the immutable provider-independent 8E envelope over one canonical target and one analysis snapshot. It retains the exact 8A peer set, 8B subset, 8C distribution, and optional 8D valuation objects plus their unchanged IDs/statuses; candidate, included-issuer, and valid-observation counts; target reporting/quote currency and quote-unit/scale metadata; supporting and policy IDs; provenance; warnings; controlled overall status; earliest failure stage; and blocking reasons.

`PeerFamilyStatus` is `VALID`, `PARTIAL`, `INSUFFICIENT`, `UNAVAILABLE`, or `EXPECTED_UNAVAILABLE`. `PeerFamilyFailureStage` runs from `IDENTITY` and `CANDIDATE_DISCOVERY` through security/economic/peer-data/distribution/target-input stages to `COMPLETE`. The coordinator always constructs 8B from `INCLUDED` peers only, constructs 8C from that exact subset, and calls 8D only for a `USABLE` distribution with explicit target denominator and exact bridge/share evidence. It performs no provider lookup, peer selection, multiple arithmetic, quantile calculation, bridge arithmetic, FX, quote-unit conversion, current-price comparison, own-history combination, aggregation, stance, or UI work.

### Market-price and post-publication comparison contracts

`MarketPriceEvidence` is the immutable 11B wrapper around one canonical Yahoo regular-market-price observation. It binds exact target security and issuer IDs to one aware analysis snapshot and observation timestamp, preserves raw quote value, quote currency, quote unit, verified unit scale, normalized currency/share value, source observation ID, controlled status, issues/warnings, policy, and provenance. The observation must be on or before the snapshot and no more than three calendar days old. A Friday close may therefore remain eligible on Monday. No interpolation or alternate quote field is permitted.

Quote currency and quote unit are distinct. A USD quote uses scale 1. A verified `GBp` or `GBX` quote with GBP currency uses scale 0.01, so raw 750 pence becomes GBP 7.50/share. Missing or inconsistent unit metadata fails closed. No ticker suffix rule, FX conversion, ADR conversion, share-class substitution, or issuer-only alignment is allowed.

`ValuationPriceComparison` has controlled scope `OVERALL_RESOLVED`, `INDIVIDUAL_FAMILY`, or reserved `EXPECTATION_REFERENCE`. It retains the exact upstream valuation source, market evidence ID, lower/central/upper values, absolute gaps `V - P`, percentage gaps `V / P - 1`, status, issues/warnings, supporting IDs, policy, and provenance. Services do not round. An overall comparison exists only for a `RESOLVED` 11A publication and uses its exact overall central plus family envelope. A complete valid individual family may be compared when publication is `WIDE` or `UNRESOLVED`; partial families remain visible without gap arithmetic.

`ExpectationGapEvidence` retains canonical/scenario reverse-DCF execution mode, publication eligibility, implied terminal growth, final consensus revenue growth/margin, and the raw growth-rate difference. It is permanently central-valuation ineligible and contains no price gap, score, reasonableness label, stance, or recommendation. `MarketComparisonResult` bundles price, overall, family, expectation, and reference collections while enforcing common identity, snapshot, and market-evidence linkage. External targets and provider DCF references remain reference-only and are not compared in 11B.

The default historical policy requires complete annual actual groups for revenue; operating income or EBIT; net income or net income common; operating cash flow; and CapEx. Five or more complete continuous years pass; three to four and one to two are partial with medium/limited reasons; zero annual actuals is unavailable. Quarterly observations never fill annual years.

The default forward policy considers only FMP annual average estimates whose period ends after `analysis_as_of`. Revenue plus at least one of EBIT, EBITDA, generic net income, or generic EPS is core complete. Three or more complete future periods pass; two or one are partial; revenue-only periods remain partial; zero is unavailable and critical for forward-data coverage.

Analyst counts remain per fiscal horizon and only use sourced revenue/generic-EPS counts. Bands are strong at 20+, medium at 8–19, limited at 3–7, weak below 3, and unknown when absent. Counts are never averaged across horizons. Nearest-horizon depth controls the analyst dimension while distant decay remains visible.

Source agreement passes only when all available comparisons are confirmed. Warning/unscored/not-comparable evidence is partial; conflict fails the dimension; `NO_VALIDATOR`, `LOCKED`, and unavailable capability remain `NOT_AVAILABLE`, never conflict. A distant conflict is noncritical to overall breadth, while configured near-term revenue/EPS conflicts lower confidence.

Cash-flow evidence separately counts OCF, CapEx, provider-defined FCF, OCF-less-CapEx, verified FCFF, and verified FCFE. Only linked verified FCFF/FCFE evidence passes that dimension. Provider FCF or OCF plus CapEx without verified economics remains partial. USD macro readiness accepts a currency-matched DGS10 observation on/before the snapshot; unsupported currencies fail closed without USD fallback. Default macro policy invents no freshness horizon, but callers may configure one.

### Discount-rate evidence and readiness

`RiskFreeRateEvidence`, `EquityRiskPremiumEvidence`, and `BetaEvidence` are immutable, provider-independent, provenance-bearing inputs. Every rate is a decimal in `PERCENT_DECIMAL` units. Risk-free and ERP evidence retain economic definition, currency applicability, observation/source dates, aware analysis snapshot, controlled status, policy ID, and provenance. Beta additionally retains `VERIFIED_METHOD`, `PROVIDER_DEFINED`, or `UNVERIFIED` definition plus its source method, benchmark, lookback, return frequency, and methodology label when known.

The initial risk-free policy accepts only FRED DGS10 as a USD 10-year Treasury constant-maturity yield. It chooses the latest observation on or before `analysis_as_of`, does not interpolate missing market days, and applies one centralized seven-calendar-day freshness limit. DGS10 is never used for GBP, EUR, JPY, or converted through FX. An ERP has no default and can enter only as explicit configured-external evidence with source, methodology, date, currency scope, and provenance.

`CostOfEquityResult` calculates only `risk_free_rate + beta × equity_risk_premium` when all three inputs are eligible, identity/snapshot-compatible, and currency-aligned. Provider-defined beta with undocumented benchmark/lookback/frequency remains visible but is ineligible under the default policy; an explicit policy may accept that evidence level without relabeling it as verified. Betas are finite and never clamped or capped. No size, country, liquidity, or company-specific premium exists.

`WaccInputReadiness` records readiness for cost of equity, equity market value, debt value and definition, pre-tax debt cost, tax rate, capital-structure weights, preferred equity/NCI treatment, and currency alignment. Book debt is not market debt unless a future explicit proxy policy approves it. `DiscountRateReadiness` keeps cost-of-equity readiness separate from WACC readiness. Neither contract contains or calculates WACC, DCF, reverse DCF, current-price comparison, or valuation output.

### Provider-independent stock research report

`StockResearchReport` is the immutable Milestone 11C presentation boundary. It binds one canonical security and issuer to one aware `analysis_as_of` and contains a deterministic sequence of identity, market, valuation-summary, valuation-family, forward-consensus, market-expectations, external-reference, and data-quality sections. Every supplied upstream result must match the same target and analysis snapshot; mixed inputs fail closed. Individual market observations and source evidence may have earlier observation dates when they belong to that snapshot.

`ReportIdentitySection` retains canonical IDs, display symbol, company name, exchange/domicile metadata, reporting currency, quote currency, quote unit, and quote-price scale. A display symbol, including a canonical `.L` suffix, is presentation metadata and never an identity key. `ReportMarketSection` copies the existing `MarketPriceEvidence` raw and normalized values, unit metadata, observation time, source, status, issues, warnings, and blockers without normalization or fallback arithmetic.

`ReportValuationSummarySection` copies the 11A publication status, family count, resolved-only overall central, estimator label, family envelope, common overlap, currency/unit, and the existing 11B overall comparison. Withheld values remain `None`; an individual-family central is never promoted to the overall central. `ReportValuationFamilySection` always contains exactly one `OWN_HISTORY` row and one `PEER` row. Complete upstream ranges and authorized family price gaps are copied unchanged, while unavailable families remain visible with null numbers and structured blockers.

`ReportConsensusSection` and `ReportConsensusPeriod` copy the existing 10A annual FMP trajectory, including fiscal horizons, Revenue, EBIT, EBITDA, margins, growth, analyst counts, snapshot times, currency, source IDs, and provenance. Missing fields remain null. `ReportExpectationsSection` distinguishes `CANONICAL_EXPECTATIONS`, `SCENARIO_EXPECTATIONS`, `NOT_READY`, and `NOT_RUN`; implied terminal growth remains expectations evidence and is never fair value. Scenario assumptions remain explicitly labeled and provenance-bound.

`ReportReferenceSection` retains external analyst targets and external provider DCF evidence as `REFERENCE_ONLY`, structurally outside valuation families and the overall central. `ReportDataQualitySection` exposes controlled per-section status rows and blockers without a numeric score. `ReportSourceReference`, `ReportIssue`, and per-field supporting IDs/provenance preserve safe source labels and evidence traceability without URLs, keys, authenticated metadata, or provider-internal company keys. `ReportFieldSemantic` exposes formatting hints such as currency, percentage, multiple, integer, and date while all domain numerics remain unrounded.

`build_stock_research_report` is a pure organizer over already-built canonical outputs. It does not call providers, import Streamlit, recalculate valuation/publication/price-gap/reverse-DCF mathematics, create economic judgment, or generate narrative.

### Sourced US ERP and regression beta

`EquityRiskPremiumObservation` is the normalized provider-boundary record for one dated external ERP value. The initial canonical series is NYU Stern's US implied ERP, trailing 12 months with adjusted payout, expressed as a decimal rate and paired with the full observed US Treasury convention. `build_sourced_us_erp_evidence` selects only that exact method at or before the analysis snapshot and applies a centralized 62-calendar-day monthly freshness policy. Other headline variants are not substitutes; source/parse failure returns no evidence and never activates a numeric fallback.

`MarketBenchmark` defines SPY as a controlled USD S&P 500 exposure proxy with split-and-distribution-adjusted return semantics; it is not a company peer. `AdjustedPriceObservation` retains instrument identity, Yahoo symbol, date, positive adjusted close, USD currency, verified return semantics, timestamps, stable ID, and provenance. Price-only or incompatible series cannot enter the regression.

The initial regression beta is `US_5Y_MONTHLY_MARKET_REGRESSION`: 60 requested aligned monthly simple total returns, ordinary least squares with an intercept, and a minimum of 36 observations. Each calendar month uses the last date common to target and benchmark. Missing months are skipped without interpolation, forward-fill, or multi-month bridging. `BetaEvidence` retains the observed levered beta, alpha, R-squared, benchmark variance, optional residual/beta standard errors, sample count, regression dates, benchmark metadata, source IDs, and derived provenance. No Blume/shrinkage, industry/leverage adjustment, provider-beta averaging, or cap is applied. Non-positive beta is retained but `UNVERIFIED` by default; an explicit policy is required to accept it.

### USD production-WACC evidence

`CapitalValueEvidence` retains one equity or debt value, controlled definition, date, currency, source observation, status, policy, and provenance. Equity requires positive canonical Fiscal market cap and is `MARKET_VALUE`. Debt requires non-negative canonical gross total debt; initial eligible large-US-non-financial evidence remains visibly `BOOK_VALUE_PROXY`. Missing debt is not zero; explicit zero gross debt is eligible. Net debt never supplies the debt weight.

### Milestone 9B.3 WACC evidence-gap contracts

`CompanyIdentity.company_type` is optional canonical profile metadata and is separate from security type. `CompanyClassEligibilityEvidence` establishes the Damodaran large-US-non-financial-operating scope only from canonical US domicile, complete non-financial sector/industry, explicit `operating_company` type, and eligible USD market capitalization of at least USD 5 billion. It records the evidence used, status, scope, issues, as-of time, and policy; ticker text is never evidence.

`CoverageNumeratorEligibilityEvidence` records the narrow methodology conclusion that Fiscal Operating Profit is eligible as the numerator for the NYU Stern Damodaran synthetic-rating interest-coverage method. `CoverageNumeratorDefinition` distinguishes `EXPLICIT_EBIT`, `DAMODARAN_OPERATING_PROFIT_EQUIVALENT`, and `UNAVAILABLE`. A method-specific observation remains `MetricId.OPERATING_INCOME`; it is not relabeled `EBIT`, and the historical/peer EV/EBIT definitions are unchanged.

`InterestCoverageEvidence` now records the selected numerator observation, canonical metric, controlled numerator definition, value, and equivalence-evidence ID in addition to the interest-expense observation and exact ratio. Both numeric inputs must be deterministic canonical annual actuals for the same fiscal period and currency. The provider's direct `INTEREST_COVERAGE` ratio is retained only as independent method evidence and cannot replace missing numerator or denominator values.

Fiscal `calculated_total_debt` and `calculated_net_debt` from the documented period-ratio endpoint normalize independently as point-in-time `GROSS_DEBT` and `NET_DEBT`. Total Debt follows Fiscal's documented sum of short-term debt, current portions of long-term debt and leases, long-term debt, and leases. Net Debt is Total Debt less Total Cash and Cash Equivalents. No component reconstruction, cash-like expansion, implicit FX, preferred-equity mapping, or NCI mapping was added.

`SyntheticRatingBand` is a provider-normalized NYU row with explicit lower-greater-than and upper-less-than-or-equal coverage boundaries, rating, decimal default spread, January 2026 source date, large-non-financial scope, and provenance. `InterestCoverageEvidence` selects the latest same-period annual actual `EBIT / INTEREST_EXPENSE`; operating income, EBITDA, quarterly annualization, cross-period pairing, zero interest, and nearest/interpolated bands are forbidden. `CostOfDebtEvidence` is exactly eligible USD DGS10 plus one sourced spread.

`MarginalTaxRateObservation` and `MarginalTaxRateEvidence` retain the NYU/PwC country marginal-rate source, domicile-selected jurisdiction, January 2026 date, decimal value, 400-day annual freshness policy, and provenance. Listing, quote currency, effective tax, and hard-coded defaults are not substitutes.

`OtherEnterpriseClaimsEvidence` may retain only the aggregate aligned residual `TEV - market cap - net debt`; it never labels that residual preferred equity or NCI. Only exact verified zero permits the initial standard debt/equity formula. `CapitalStructureWeights` stores `E/(D+E)` and `D/(D+E)` with non-negative weights summing to one. `WaccResult` exposes a numeric value only when the existing `WaccInputReadiness` is `READY`; the formula is `wE × cost_of_equity + wD × pretax_cost_of_debt × (1 - marginal_tax_rate)` with no intermediate rounding.
