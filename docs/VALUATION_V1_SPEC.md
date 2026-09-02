# Valuation V1 Architecture and Methodology

## Purpose and invariants

V1 is a multi-source valuation and evidence engine. It normalizes, maps, reconciles, validates, values, reverse-solves, compares, assesses coverage, and presents sourced information. It does not rebuild a universal company forecast engine. Externally sourced consensus supplies forward operating estimates where reliable. Every value is period-, case-, currency-, unit-, and provenance-aware.

The system fails closed: missing or semantically uncertain inputs produce structured partial/unavailable results. Price targets and FMP's standard DCF are external references with zero central-valuation weight. Ollama interprets selected transcript evidence only.

## Canonical domain contracts

Milestone 1 adds an isolated `stock_analyser.domain` package without wiring it into the legacy application. Records are frozen standard-library dataclasses and status concepts are separate enum types:

- `DataAvailability`: available, partial, unavailable.
- `CapabilityStatus`: available, locked, unavailable, error.
- `ValuationMethodStatus`: valid, partial, unavailable.
- `AggregationStatus`: resolved, wide, unresolved, unavailable.
- `CoverageLevel`: high, medium, limited, insufficient.
- `IssueSeverity`: info, warning, error, blocking.

These states are not interchangeable. Resolved/wide are aggregation states and never describe a single method.

`CompanyIdentity` distinguishes `security_id` from `issuer_id`, and `issuer_domicile` from `listing_country`. A depositary receipt carries a distinct traded security ID and underlying security ID. Its sourced ADR ratio is positive when known; an unknown ratio stays absent and makes identity explicitly partial rather than silently one-for-one. Quote currency and quote unit are separate, and price scale applies only to traded quote prices.

Every `MetricObservation` has a deterministic observation ID, `retrieved_at`, and `as_of_at`. Retrieval time records when the application received a value; as-of time records when the observation is valid/current. They need not match. Using retrieval time as a missing provider snapshot time is an explicit provenance transformation. Provenance contains source/dataset/symbol, transformations, input observation IDs, and configuration/override identity, but has no secret, authorization-header, or raw-payload field. Derived observations must cite input observation IDs.

`ExternalValuationReference` is not a subtype of `ValuationResult`. FMP standard DCF and analyst targets cannot enter an internal method-result collection through the canonical boundary.

## Layered architecture

1. `domain`: immutable canonical identity, observation, issue, capability, provenance, and valuation-result contracts plus validation.
2. `providers`: one adapter per provider; provider fields never escape this layer.
3. `services`: symbol resolution, historical fundamentals, consensus construction, reconciliation, macro inputs, coverage, and caching.
4. `valuation`: family registry and independent own-history, peer, verified-cash-flow, reverse, bank, insurer, and REIT engines.
5. `research`: transcripts and bounded Ollama interpretation, disconnected from numeric valuation.
6. `ui`: consumes view models; no provider mapping or valuation formula in Streamlit call sites.

## Provider infrastructure boundary

Milestone 2 adds an isolated `stock_analyser.providers` package without live transports or business endpoints. `ProviderId` is the controlled provider identity inside this layer. `BaseProviderAdapter` owns independent capability records and error-to-capability translation but imposes no common financial business methods. A failed probe changes only its endpoint result.

Generic `Transport` retrieves normalized HTTP response envelopes; adapters interpret provider payloads. `RetryingTransport` applies central timeout and bounded retry policies with injected sleep/clock/jitter. It retries configured timeouts, transient connections, HTTP 429, and 5xx; it does not retry ordinary 4xx. Generic 403 remains unclassified until a provider-specific adapter distinguishes authentication from entitlement.

`ProviderError` categories are authentication, entitlement, rate limit, timeout, connection, server, not found, invalid request, invalid response, empty response, provider unavailable, and unknown. Error records and request identities contain only redacted/safe metadata and never raw headers or response bodies.

The cache protocol is schema-versioned. Deterministic keys use controlled provider, safe endpoint, method, provider symbol, normalized non-secret parameters, and schema version. Credentials and request headers never participate. The reference memory cache distinguishes hit, miss, and expired; expiry never masquerades as fresh data. Persistent caching and stale fallback are later service decisions.

## Identity and actual-data adapters

Milestone 3 adds a deterministic identity service and injected-source Yahoo, Fiscal, and SEC adapters while leaving all legacy execution paths unchanged. Seed application/security/issuer identifiers are authoritative; provider candidates fill canonical metadata by documented field precedence and same-field disagreement becomes a `DataIssue`. Provider lookup symbols are explicit and may be absent independently.

Yahoo maps identity and point-in-time market facts, scaling only price-like GBp quotes. Fiscal maps only a bounded standardized historical-actual vocabulary, retains operating income separately from EBIT, labels generic FCF as provider-defined, and records justified CapEx sign conversion. SEC maps a bounded characterized tag set as reported-actual verification, uses CIK as provider identity, and preserves reported period, filing/as-of time, and taxonomy/tag separately. Neither adapter fabricates unsupported metrics, estimates, LTM/NTM, fallback data, or reconciliation. Adapter output is canonical observations/capabilities/issues; raw response bodies cannot enter V1 cache values.

## Forward consensus

Milestone 4 makes FMP canonical for annual revenue, EBIT, EBITDA, generic net income, and generic EPS low/average/high estimates. Generic net income is not asserted as common-shareholder income and generic EPS is not asserted as basic or diluted. Revenue analyst counts apply only to revenue cases; EPS counts only to EPS cases; other counts remain absent. Monetary levels require resolved reporting currency and never receive quote-subunit scaling.

An analyst-estimate endpoint does not make every returned row forward. Historical rows remain `ESTIMATE` for audit and are excluded from forward periods. FY1 is the first annual fiscal period ending after the explicit as-of date, including the unfinished current fiscal year when applicable; FY2 is the next. FY1 is not NTM. Quarterly estimates and NTM construction are deferred.

Low/average/high describe per-metric consensus ranges, not coherent bear/base/bull analyst paths. Inversions create blocking issues and are never reordered, clamped, or averaged. Missing cases/counts remain missing without invalidating unrelated levels.

The canonical-only consensus service derives average adjacent-year revenue growth and same-period average EBIT or EBITDA margins when periods, cases, units, currencies, and snapshot semantics align. Derived provenance cites input observation IDs. It does not derive low/high paths, EPS growth, implied diluted shares, reconciliation, coverage, or valuation.

Milestone 5 adds Finnhub and Alpha Vantage as independent validators only. Finnhub can supply annual revenue, EBIT, EBITDA, generic net income, generic EPS, OCF, CapEx, and generic FCF observations by independent capability. Alpha can supply annual/quarterly revenue and generic EPS plus separate revision evidence. FMP remains canonical for the Milestone 4 annual operating-consensus metrics. Milestone 6A compares eligible aligned annual validators independently; it never averages or overwrites FMP and does not create quarterly canonical consensus.

FMP analyst targets and standard DCF are structurally separate `ExternalValuationReference` records in resolved quote currency. They cannot enter internal valuation-result collections and have zero aggregation weight. The DCF endpoint's accompanying stock price is ignored; Yahoo remains the intended canonical market-price source.

## Valuation-family registry

Eligibility is driven by canonical classification and economics, never ticker. Standard profitable corporates/industrials: P/E, EV/EBITDA, EV/EBIT and verified cash flow. Software/platforms and semiconductors: P/E, EV/EBITDA, EV/FCF where valid. Energy/materials: EV/EBITDA and cash-flow/yield methods with cycle context. Banks: P/E, P/TBV and ROE/ROTCE; ordinary enterprise FCFF disabled. Insurers: P/E, P/B and ROE; inappropriate enterprise FCFF disabled. REITs: P/FFO, P/AFFO, NAV where supported; standard corporate FCFF disabled. Pre-profit growth: EV/revenue and defensible EV/gross profit. Utilities/telecom: P/E, EV/EBITDA, and suitable cash-flow/dividend methods.

Registry output lists eligible, ineligible, and conditionally eligible methods with reasons and required inputs. Sector presets never supply missing forecasts.

## Method A: own-history forward valuation

### Milestone 7A implemented input boundary

Milestone 7A implements only the dated input layer. `HistoricalValuationObservation` carries canonical security/issuer identity, provider provenance, controlled multiple/basis/denominator/sampling semantics, finite dimensionless value, distinct observation/period/as-of/retrieval dates, currency audit context, and explicit future-distribution eligibility. It is not a `ValuationResult` and has no fair-value, target, upside/downside, percentile, or forward-estimate field.

Fiscal daily ratios are the initial canonical source. P/E is equity-based and follows Fiscal's documented share-price/diluted-EPS formula without inferring forward or normalized P/E. EV/EBITDA is enterprise-based with EBITDA. Fiscal's EV/EBIT series is enterprise-based but explicitly records the provider's documented Operating Profit denominator; generic operating income remains distinct from EBIT elsewhere. P/FCF and EV/FCF are not supported because provider-generic FCF has not been verified as FCFE or FCFF.

The source samples each trading day using the most recently reported fundamental inputs available to Fiscal on that date. The real response provides an observation date and ratio only: denominator period end is absent, provider snapshot as-of falls back explicitly to retrieval time, and sampling remains daily. Positive finite supported values may be eligible; non-positive finite values remain visible but ineligible; non-finite/malformed rows fail closed. Finite outliers are preserved without winsorization. The explicit `analysis_as_of` filter removes later economic dates without interpolation.

### Milestone 7B implemented distribution boundary

Milestone 7B consumes only canonical 7A observations and builds one provider-specific distribution for one explicit multiple/window. `HistoricalWindow` is independently selected as 3Y, 5Y, or 10Y; default policy is 5Y. The boundary is inclusive at both ends and uses an exact calendar-year subtraction from aware `analysis_as_of`. Results for the three windows are calculated independently and are never averaged, weighted, selected opportunistically, or silently replaced by a shorter available window.

The deterministic quantile convention is Type-7 linear interpolation at sorted position `(n - 1)q`. Results expose p25, median, p75, minimum, maximum, IQR, arithmetic mean, and population standard deviation. Mean is diagnostic only. The primary daily sample is unchanged: no winsorization, clipping, trimming, IQR/sigma deletion, interpolation, monthly resampling, regime normalization, cycle adjustment, or sector override occurs. Missing market dates are not manufactured.

Default minimums require both observation count and actual eligible-date span: 500/80% for 3Y, 750/80% for 5Y, and 1,250/80% for 10Y. Both produce `USABLE`; exactly one produces `PARTIAL`; neither produces `INSUFFICIENT`. No eligible data or a conflicting duplicate also produces `INSUFFICIENT`. Descriptive statistics remain visible for non-conflicting partial/insufficient samples, but status prevents future code from treating them as fully usable. No ineligible-share rejection threshold exists; candidate, eligible, ineligible, and eligible-fraction diagnostics remain visible.

Fiscal daily ratios are serially dependent market observations sharing reported fundamentals. Sample count is not an independent-regime count. The result preserves `DAILY`, reports unique calendar months as coverage only, and leaves independent fundamental regimes unknown because the source lacks denominator period ends. Identical canonical provider/date observations are deduplicated; conflicting values fail closed and are never averaged. Other providers are excluded rather than combined.

Milestone 7B is descriptive, not a `ValuationResult`. It does not consume FMP consensus, apply a multiple, create fair value/target/upside, build peers or DCF, aggregate methods, alter coverage, or change application output. Milestone 7C adds only semantic and bridge prerequisites; Milestone 7D is the first milestone allowed to apply an authorized usable distribution to aligned forward consensus.

Milestone 7D consumes one explicitly selected usable Fiscal distribution and aligned forward evidence. It never blends windows. Its primary historical band holds the selected annual `AVERAGE` denominator constant: lower = P25 multiple × average denominator, central = median × average denominator, and upper = P75 × average denominator. Analyst low/high remain consensus-dispersion evidence and are not bear/bull valuation scenarios. P/E produces per-share equity value directly. EV/EBITDA and verified EV/EBIT produce enterprise value, subtract exactly one approved enterprise adjustment, and divide by the exact approved current share evidence. Non-positive denominators fail closed.

## Method B: forward peer valuation

Milestones 8A–8A.2 implement selection and readiness evidence only. Fiscal's documented v3 company-profile `peers` array generates candidates; a provider label never makes a candidate comparable. Stable Fiscal company/security IDs plus primary-listing ticker/exchange create `SUMMARY_VERIFIED` identity without security type or a second profile. `PROFILE_VERIFIED` means the optional enrichment path completed. Missing profile fields are never fabricated, and name/ticker/reasoning cannot seed identity. Canonical issuer ID still removes self and duplicate listings.

Security eligibility is a separate evidence contract bound to the already anchored identity. Missing type is `UNVERIFIED`, verified common equity may pass, and verified unsupported/non-equity instruments fail. Fiscal profile and listing-compatible Yahoo metadata may enrich classification but cannot redefine identity; operating-company metadata, name, relationship, reasoning, and ticker alone prove nothing about security class. The economic policy additionally requires `SAME_INDUSTRY` or explicitly configured `RELATED_INDUSTRY` and at least one supported actual financial criterion without a contradictory evaluated criterion. Forward EBITDA availability is not an economic criterion. `SECTOR_ONLY` and unknown industry remain unverified. Same-currency revenue scale uses 0.1x–10x candidate/target bounds; revenue-growth and EBITDA-margin gaps are dimensionless under aligned canonical definitions; cross-currency scale remains unresolved without FX. No opaque score exists.

`PeerSetStatus` is `USABLE` only with at least three included independent issuer IDs. `CanonicalActualSelection` removes compatible exact duplicates and selects different values only when a unique latest provider source/revision timestamp is genuinely explicit. Retrieval time, row order, min/max, averaging, and provider replacement are forbidden. Unordered conflict remains `UNRESOLVED`.

`PeerMethodDataReadiness` separately reports preliminary EV/EBITDA forward-denominator and future required-input availability as `READY`, `PARTIAL`, or `UNAVAILABLE`. An economically included peer may be method-data unavailable, and a method-data-ready issuer may still be economically excluded.

Milestone 8B adds the first numeric peer observation without changing membership. One observation is the peer's latest eligible positive canonical point-in-time enterprise value divided by that same peer's selected positive annual forward EBITDA. Default TEV freshness is seven calendar days. The denominator is annual `AVERAGE` FY1 by default, or explicit FY2, selected from the peer's own fiscal calendar and an estimate snapshot at/before analysis. FY1 is not NTM; low/high are not bounds and are never substituted.

FMP forward evidence requires a verified stable CIK, ISIN, or CUSIP binding to the canonical peer. Ticker, company name, Fiscal/FMP symbol equality, GOOG/GOOGL aliases, and venue rewrites are forbidden. TEV and EBITDA must be canonical base-unit currency observations for the same peer with identical currency; no FX or provider scale compensation is performed. Values and the resulting dimensionless ratio must be finite and positive. High valid multiples are retained because outlier policy is deferred.

`PeerValuationSubset` is downstream of immutable 8A selections and counts at most one representative security observation per included issuer. Three independent valid issuer observations are required for `USABLE`. A usable economic set may have a partial method subset, while a partial economic set cannot be rescued with excluded or unverified data. Milestone 8B calculates no peer median/percentile, target implied value, current-price comparison, own-history input, DCF, aggregation, stance, or UI result; these remain deferred. Current live META validly returns zero observations and an unavailable subset because it has zero economically included peers.

Milestone 8C consumes one 8B subset without rediscovery, provider access, identity resolution, economic-membership changes, or date reassessment. `PeerMultipleDistribution` verifies one target, peer set, analysis snapshot, method, enterprise basis, FY1/FY2 policy, and `AVERAGE` case, plus one observation per independent issuer. Different actual peer fiscal period ends remain valid under the same forward-period policy. Already validated ratios are dimensionless and may coexist across underlying currencies without FX or comparison of absolute TEV/EBITDA values.

The primary cross-sectional statistic is median. P25, median, and P75 use the explicit Type-7 linear rule with position `(n - 1) × q`; mean is descriptive only. Minimum, maximum, population standard deviation, IQR, IQR/median, maximum/median, and minimum/median expose raw dispersion. The policy has no clipping, winsorization, IQR/sigma deletion, arbitrary cap, threshold-based invalidation, or automatic observation removal. A wide but otherwise valid sample remains observable, while non-positive/non-finite ratios, duplicate issuers, and mixed contracts fail closed.

Three independent valid issuer observations are required for a usable distribution, and sample count remains explicit for small samples. Zero observations produce `UNAVAILABLE` with no invented statistics; an upstream partial subset cannot become a usable 8C result. Current live META therefore remains `UNAVAILABLE` with zero observations. Milestone 8C does not consume target EBITDA, enterprise/equity/share bridge inputs, current price, own-history evidence, DCF, aggregation, stance, or UI state. Target application is deferred to 8D.

Milestone 8D performs target-side application only. Normal execution requires one `USABLE` 8C EV/EBITDA distribution and one identity-bound target annual `AVERAGE` EBITDA observation whose explicit FY1/FY2 selection equals the peer distribution policy. It never substitutes LOW/HIGH, NTM, EBIT, operating income, revenue, generic FCF, or constructed EBITDA. Target identity, analysis snapshot, distribution, peer-set, subset, observation, bridge, and share references must all match exactly.

Milestone 8E is orchestration only. It consumes one canonical target identity and the approved 8A evidence, builds 8B and 8C through their existing services, and invokes 8D only when the distribution is `USABLE` and explicit target denominator/bridge/share evidence is present. It reports the earliest controlled failure rather than a later unavailable symptom. `VALID`, `PARTIAL`, `INSUFFICIENT`, `UNAVAILABLE`, and `EXPECTED_UNAVAILABLE` are all controlled outcomes; a numeric peer value is not required for a successful portability audit.

Cross-stock execution does not alter rules. Provider symbols remain provenance, stable FMP binding remains mandatory, each peer retains its own fiscal calendar under the shared FY1/FY2 policy, and high finite ratios remain in the unchanged no-automatic-removal distribution. Reporting currency, quote currency, quote unit, and quote price scale remain distinct. Peer-family valuation performs no FX or GBP/GBp conversion and consumes no current price, risk-free rate, own-history output, family weight, aggregation, stance, or UI state.

Lower, central, and upper use the unchanged peer P25, median, and P75 respectively against the same target EBITDA. Mean, minimum, and maximum are not standard valuation points. For each point, `implied enterprise value = peer statistic × target EBITDA`, `implied equity value = implied enterprise value - exactly one approved enterprise adjustment`, and `per-share value = implied equity value / exact approved shares`. The direct bridge retains Fiscal `TEV - market cap`; the component bridge retains validated net debt. They are explicit alternatives and are never combined, averaged, or selected by attractiveness.

Target EBITDA and bridge monetary evidence must use the same canonical base currency. No FX or provider-specific scale correction occurs, and a GBP/share result remains GBP/share rather than being multiplied into GBp. Missing/incomplete bridge or share evidence, unresolved ADS conversion, non-positive/non-finite EBITDA or shares, malformed peer statistics, and identity/period/currency mismatches fail closed. Points are evaluated independently: non-positive equity is retained in the trace without flooring or per-share output; one or two remaining valid points yield `PARTIAL`, while three ordered valid points yield `VALID`. A missing median prevents a resolved central value.

The peer result embeds the generic `ValuationResult` only when a monetary trace exists and remains structurally independent from own-history results. It consumes no current price, analyst target, external FMP DCF, DCF/reverse DCF, aggregation, method weight, stance, or UI state. Current META correctly returns an unavailable peer target valuation because its 8C distribution has zero observations; no target number is emitted and no new provider request is required.

## Method C: consensus cash-flow valuation

Prefer sourced forward cash flows. A provider `FCF` observation is unusable until documented as FCFF, FCFE, OCF less CapEx, or provider-defined. FCFF is discounted at currency-consistent WACC and bridged from enterprise to equity. FCFE is discounted at cost of equity and directly produces equity value. OCF less CapEx is not assumed to be either without a verified definition and treatment of financing/minority/preferred claims.

Inputs require explicit forecast periods, terminal methodology, discount-rate provenance/as-of date, and matching currency. No fallback may resurrect automatic D&A, CapEx, working-capital, margin, or revenue forecasts. Unknown definition, insufficient horizon, missing macro input, or inconsistent bridge returns unavailable.

## Method D: reverse valuation

Reverse DCF/valuation solves for the operating or cash-flow outcome required by current enterprise/equity price under disclosed, internally consistent mechanics. Suitable outputs include required FCF CAGR, terminal economics, margin, or earnings. Compare the market-implied result with external consensus, history, and management guidance. The headline is the expectation gap, not an additional target price. Reverse methods obey the same cash-flow-definition and currency rules as forward DCF.

## Discount rate

Risk-free rate must match cash-flow currency, not listing country. FRED supplies US macro/rates for USD only. ERP is externally configured with source and as-of date. Beta preference is trusted provider, validated by another provider; deterministic calculation is a documented fallback using an appropriate benchmark, return frequency, lookback, treatment of dividends/outliers, and minimum observations. Every component, capital weight, tax rate, and override is auditable. Missing currency-appropriate inputs withhold the method or require an explicit user override; no silent floors, clamps, spreads, or global constants.

The implemented Milestone 5 input layer maps FRED DGS10 to a daily USD Treasury-yield `MacroObservation` in decimal units and selects only the latest valid observation on/before the requested date. It returns structured unavailable for every non-USD currency; it does not implement overrides. This source layer does not calculate WACC, cost of equity, ERP, beta, or a valuation result.

## Source reconciliation and coverage

The canonical observation survives validation unchanged. Provider symbols are audit metadata; stable security/issuer identity and exact economic dimensions determine comparability. Each validator yields an independent record under an explicit `comparison_as_of`. Future observations are excluded; latest eligible otherwise identical observations are selected. Retrieval-time differences alone do not block comparison.

Relative difference uses the canonical magnitude denominator. Centralized bands are price/reported revenue `<1%`, `1–3%`, `>3%`; forward revenue `<3%`, `3–10%`, `>10%`; and forward generic EPS `<5%`, `5–15%`, `>15%`, with lower boundaries entering warning and upper boundaries remaining warning. Zero/nonzero is conflict without a percentage. Conflicts create issues but do not replace canonical data or automatically withhold a method in Milestone 6A.

Milestone 6B implements a separate evidence contract: `CoverageLevel` describes breadth, `EvidenceConfidence` describes reliability, and `ValuationReadiness` describes prerequisite state for each future family. These types are never interchangeable and no weighted 0–100 score exists.

Default breadth rules are observable and centralized. Market data requires current price for semantic usability. Historical financials count complete annual actual periods only: 5+ continuous is strong, 3–4 medium/partial, 1–2 limited/partial, and zero unavailable. Forward consensus counts future annual FMP average periods with revenue plus EBIT, EBITDA, generic net income, or generic EPS: 3+ is strong, two medium/partial, one limited/partial, and zero unavailable. Revenue-only remains partial. Analyst bands are 20+ strong, 8–19 medium, 3–7 limited, below 3 weak, and missing unknown; FY1 controls the dimension and every horizon remains visible.

Agreement consumes Milestone 6A records unchanged. Confirmed comparisons support high confidence; warnings lower it; conflicts lower it further; no validator or locked capability is absence, not conflict. Critical policy includes missing current price, no forward consensus, identity/currency semantic mismatch, supplied blocking issues, and near-term forward revenue/EPS conflict. Distant conflicts remain dimension-level evidence and do not by themselves make total breadth insufficient.

Readiness never authorizes a calculation. Milestone 7C can mark an individual method's prerequisite contract `READY`, but the existing coverage service remains intentionally unchanged and family readiness remains conservative until 7D implements forward application. Peer is `NOT_READY` because V1 peer selection is absent. Cash flow is at best `PARTIAL` when verified FCFF/FCFE, aligned forward horizon, and currency-consistent macro evidence exist, because ERP/WACC/cost-of-equity and bridge machinery are absent. Reverse cash flow is explicitly `NOT_READY` and deferred. Coverage classifications contain no monetary result or investment stance and do not change application output.

### Milestone 7C implemented prerequisite boundary

One method selects exactly one 3Y/5Y/10Y distribution (5Y default), one annual FY1/FY2 period (FY1 default), and one analyst estimate case (`AVERAGE` default). FY1 follows the existing consensus rule—first annual period end after the explicit analysis snapshot—and is never NTM. Low/high remain analyst range cases and are not scenarios.

Economic alignment is controlled. Fiscal diluted-EPS P/E versus FMP generic EPS and Fiscal Operating Profit EV/EBIT versus FMP generic EBIT fail closed as `UNVERIFIED` unless explicit semantic evidence establishes equivalence or incompatibility. Fiscal/FMP canonical EBITDA alignment is `EXACT` under the approved policy. `operating_income` is never substituted for EBIT.

Enterprise methods require a current canonical capital snapshot. The service selects latest eligible actual cash, gross debt, and the explicitly required share concept on/before `analysis_as_of`. Missing values are not zero, share concepts are not interchangeable, and no quote/reporting currency or FX assumption is invented. Net debt is prepared only as gross debt less cash when both currencies match. Different source dates are disclosed; no default staleness threshold exists, though a configured maximum gap can downgrade completeness. Missing required ADR/share-class conversion blocks readiness.

`OwnHistoryMethodReadiness` classifies each method independently. Semantic, distribution, identity, currency, or ADR failures are `NOT_READY`; an otherwise aligned enterprise method with incomplete bridge evidence is `PARTIAL`; complete prerequisites are `READY`. It carries IDs, reasons, warnings, and provenance only. No historical multiple is multiplied by any denominator and no valuation result exists in 7C.

### Milestone 7C.1 direct Fiscal bridge

Fiscal's documented TEV definition is market cap plus net debt, preferred stock, and minority interests. The preferred aggregate bridge is therefore `enterprise_equity_adjustment = Fiscal calculated TEV - Fiscal calculated market cap`. It preserves the provider's enterprise-value methodology without zero-filling unavailable debt, preferred-stock, or NCI components. The component `gross debt - cash` bridge remains a separate method; it is neither deleted nor averaged with the direct bridge.

Direct evidence must belong to the Fiscal symbol attached to the same canonical identity, be at or before `analysis_as_of`, use compatible currency, and satisfy the explicit date policy. Daily TEV and market cap use the latest common date by default; a mismatch is not combined unless a maximum gap is explicitly configured. No FX is added. Current shares must have suitable issuer or documented ADS-converted semantics. An ADR with unverified share conversion remains blocked.

A complete direct bridge can satisfy EV/EBITDA's enterprise-to-equity prerequisite when the 5Y distribution is usable, FMP EBITDA alignment is exact, and the requested FY/case and shares are valid. It does not weaken denominator policy: FMP generic EPS remains unverified against Fiscal diluted EPS and generic EBIT remains unverified against Fiscal Operating Profit because bounded official documentation did not explicitly prove either equivalence. No multiple, implied enterprise/equity value, per-share result, fair value, target, or upside/downside is calculated before 7D.

### Milestone 7D numeric own-history method

The numeric service requires exactly one `READY` prerequisite, its exact `USABLE` distribution, and the exact selected canonical annual forward observation. The estimate case must be `AVERAGE`; FY1/FY2, historical window, identity, as-of time, metric, period, unit, currency, provider provenance, distribution ID, bridge ID, share basis, and share observation ID must all match readiness. Inputs are immutable and provider independent.

For P/E, each point is `historical P/E statistic × compatible forward EPS`; no enterprise bridge or share division is used. For EV/EBITDA and semantically verified EV/EBIT, each point is `implied EV = statistic × forward denominator`, `implied equity = implied EV - selected enterprise adjustment`, and `per-share = implied equity / selected shares`. Direct Fiscal bridge arithmetic remains `TEV - market cap`; component net debt is an alternative selected method, never an averaged input.

Milestone 7D.1 composes those contracts without duplicating their economics. The canonical orchestrator resolves identity, builds exactly one selected-window distribution per requested method, selects one annual `AVERAGE` FY denominator, constructs alignment and the approved direct Fiscal bridge, assesses readiness, and calls the numeric engine only for `READY` methods. The opt-in live audit independently reconstructs each valid point and exposes unit, currency, scale, date, distribution, bridge, and share diagnostics. It does not fetch a Yahoo market snapshot, compare with current price, aggregate methods, or publish an investment stance.

Milestone 7E confirms the same path is portable without requiring every stock to produce a number. A correct structured absence is a successful validation outcome. Canonical `.L` identity is never globally stripped; a provider boundary may resolve a different provider ticker only from explicit venue-compatible metadata. A London quote environment can coexist with USD reporting and USD/share valuation, or GBP reporting and GBP/share valuation. GBp remains a quote presentation unit and `price_scale=0.01` never enters own-history arithmetic. High historical multiples remain untrimmed, energy remains on the explicitly selected 5Y window, and own-history valuation requires no macro or risk-free-rate input.

The service assumes canonical monetary observations have already been normalized to base currency magnitude. It verifies the expected canonical unit and matching currency, contains no provider-name scaling constant, and performs no FX or quote/reporting-currency substitution. Historical statistics and forward denominators must be finite, positive, and ordered. Share count must be finite, positive, and exactly the approved basis. Intermediate values are unrounded.

### Milestone 8A.3 peer financial evidence

Peer financial retrieval is downstream of canonical Fiscal identity. An exact same-record `companyFiscalIdentifier` may resolve the opaque `companyKey` needed by Fiscal standardized-financial endpoints; ticker/name matching and cross-provider substitution are forbidden. The key remains provider lookup metadata. Summary-verified and profile-enriched peers then use the same existing standardized-financial normalization and `select_canonical_actual` policy as the target.

Milestone 8A.4 clarifies that standardized financial statements bind to the canonical issuer, not to one representative security/listing. A verified Fiscal issuer-to-companyKey relationship is required and an issuer conflict fails closed; security eligibility and representative listing identity remain separate economic gates. Provider symbol remains provenance and is not required to equal an issuer-level identifier. Target and peer requests share one Fiscal helper and parser. Explicit financial-row currency is preferred, verified cached/profile reporting currency is the only fallback, and missing currency remains unavailable without USD, quote-currency, or FX inference.

Economic comparability remains unchanged: annual same-currency revenue can support scale; two compatible selected annual revenue periods can support dimensionless growth; annual revenue and actual EBITDA for one selected period can support margin. Quarterly annualization, implicit FX, source-metric merging, averaging, EBIT substitution, and EBITDA construction are prohibited. At least one valid financial criterion must pass, while unresolved security eligibility or sector-only/industry-mismatch evidence retains its independent gate. Forward FMP EBITDA is method-readiness evidence only and is not required for economic membership. Three independent included issuers remain required for a `USABLE` peer set; peer multiples and target application remain deferred to 8B.

P25, median, and P75 points are assessed independently. A non-positive implied equity value is retained in the safe trace but has no per-share output and is never floored to zero. Remaining valid points yield `PARTIAL`; three valid ordered points yield `VALID`; no valid point yields `UNAVAILABLE`. The result embeds the generic V1 `ValuationResult` while retaining the detailed own-history calculation trace and provenance. It consumes no current price, analyst target, external FMP DCF, peer result, aggregation input, or stance. Coverage and live orchestration remain unchanged and deferred.

## Aggregation

Milestone 11A admits exactly one upstream-selected contribution from each approved central family: `OWN_HISTORY` and `PEER`. Multiple methods within one family do not create independence; duplicate same-family candidates without a unique upstream selection fail that family closed. A central publication requires at least two complete contributions aligned to the same canonical target, aware `analysis_as_of`, valuation currency, and explicit per-share unit. No FX or GBP/GBp quote-unit conversion is performed.

Agreement is threshold-free. The family envelope is `min(lower)` to `max(upper)`; the common overlap is `max(lower)` to `min(upper)` when nonempty. `RESOLVED` requires at least two families, a common overlap, and every family central inside that overlap. Its overall central is the shared Type-7 median of family centrals and its published descriptive range is the wider family envelope. `WIDE` has overlap but at least one central outside it and therefore no overall central. `UNRESOLVED` covers one family or conflicting ranges; `UNAVAILABLE` means zero complete eligible families. Diagnostics are descriptive and do not change status through a percentage threshold.

Canonical reverse DCF is expectation evidence, explicit reverse DCF is scenario evidence, and analyst targets/provider DCFs are external references. None increments family count or affects the median, envelope, overlap, or status. There are no family or confidence weights. Milestone 11A consumes no current market price and produces no price comparison or investment stance.

## Presentation and audit

The eventual valuation page has coverage/capability header, forward consensus table, own-history windows, visible peers, eligible cash-flow valuation, reverse expectations, separate external references, and final resolution. Every displayed input can reveal canonical source, source metric, analysts, period/case, currency/unit, retrieval time, transformations, validators, agreement, warnings, and downstream methods.

Fundamental quality, valuation, estimate momentum, and market behavior remain separate. A stance can use valuation only when resolved; otherwise it must say valuation is withheld.

## Current v0.9.4 audit

Valuation modules are `valuation.py` (legacy simplified DCF, reverse DCF, Yahoo multiple context), `adaptive_valuation.py` (`ForecastFramework`, automatic assumptions, dynamic WACC, explicit FCFF, reverse FCFF, history/peer valuation, range and stance), `fiscal_valuation.py` (Fiscal fetching/flattening/snapshot/ratio/KPI/peer/share helpers), `analysis.py` (share/net-debt resolution, gates, orchestration, scenarios, scoring), `scoring.py` (valuation score/confidence/view), `charts.py` (DCF sensitivities), `yahoo_data.py` (market/statements/estimates/targets/ratios and US Treasury proxy), and `app.py` (loaders, editable assumptions, status, triangulation, DCF/history/peer/reverse/WACC UI). The new `domain` package is migration-only and has no legacy call site. Tests covering these are enumerated in `TEST_MATRIX.md` and `MIGRATION_PLAN.md`.

Reusable foundations include endpoint isolation, GBp normalization, Fiscal symbol/profile and tolerant payload parsing, period sorting, standardized snapshot extraction, SEC verification, transcript evidence selection/grounding, reverse-solve mechanics, EV-to-equity concepts, missing-data gates, minimum peer count, method-dispersion withholding, and existing regression fixtures. Reuse is conceptual and should be wrapped behind canonical contracts before extension.

Automatic forecast construction in `build_adaptive_assumptions`, transcript guidance extraction as a numeric forecast input, framework operating defaults, auto D&A/CapEx/NWC/other-cash adjustments, `dynamic_wacc` fallbacks/floors/global ERP, both forward DCF engines as central methods, scenario generation, and direct valuation scoring from those outputs are deprecation candidates. Preserve them until characterized and replaced.

## Milestone 9A discount-rate prerequisite specification

The discount-rate currency is the modeled cash-flow/valuation currency, not automatically the listing exchange, quote currency, or quote unit. USD cash flows may use eligible FRED DGS10 evidence even for a London listing; GBP cash flows may not use DGS10 until an approved GBP sovereign source exists. No FX conversion or country-spread substitution is allowed.

The initial cost-of-equity method is CAPM only: `cost of equity = eligible risk-free rate + eligible beta × explicit eligible ERP`. Rates are decimals. No anonymous ERP, beta cap, regression beta, size premium, country premium, liquidity premium, or company-specific premium is permitted. Provider-defined beta whose benchmark, lookback, frequency, and adjustment method are unknown is auditable but ineligible under the default policy.

Future WACC construction requires separately verified cost of equity, equity market value, debt value semantics, pre-tax debt cost, tax rate, capital-structure weights, preferred-equity/NCI treatment, and currency alignment. The intended basic formula is `E/(D+E) × cost of equity + D/(D+E) × after-tax cost of debt`, extended only when explicitly required. Milestone 9A reports prerequisites and does not calculate WACC, DCF, reverse DCF, implied expectations, current-price comparisons, or valuation outputs.

## Milestone 9B.1 USD cost-of-equity completion

The canonical sourced US ERP is the dated NYU Stern implied premium using trailing-12-month adjusted payout. It is selected without switching among alternative published variants, normalized from percent to decimal, usable for USD/US broad-market scope under a 62-calendar-day policy, and kept consistent with the full observed DGS10 Treasury convention. There is no sovereign-default-spread subtraction or fallback ERP.

The canonical beta method uses SPY adjusted Yahoo history as an explicit S&P 500 exposure proxy. Target and benchmark use split-and-distribution-adjusted daily Close evidence, the final common observation in each calendar month, simple returns, approximately five years/60 requested returns, and OLS with intercept. Thirty-six aligned returns are required. The output is an unadjusted observed levered equity beta; no Blume adjustment, shrinkage, industry/leverage adjustment, cap, provider-beta average, interpolation, forward-fill, or weekly fallback exists.

Eligible DGS10, sourced ERP, and regression beta feed the existing exact CAPM equation only. A ready cost of equity does not authorize WACC: missing debt value/cost, tax, capital weights, preferred equity/NCI, and currency evidence keep production WACC `NOT_READY`. DCF, reverse DCF, implied expectations, current-price comparison, and UI remain deferred.

## Milestone 9B.2 USD production-WACC specification

The initial production scope is explicitly classified large US non-financial operating companies. `E` is eligible canonical Fiscal current market cap. `D` is eligible non-negative canonical gross interest-bearing debt and may be used only as a visibly labeled `BOOK_VALUE_PROXY` under the centralized policy; net debt is never a weight. Missing debt is not zero, while explicit zero debt is valid.

Pre-tax debt cost is eligible USD DGS10 plus the exact sourced default spread selected from the official January 2026 NYU Stern large-non-financial interest-coverage table. Coverage is latest same-period annual canonical `EBIT / INTEREST_EXPENSE` using a positive normalized expense magnitude. No operating-income/EBITDA/quarterly/cross-period substitute, spread interpolation, cap, nearest rating, or extra premium exists.

The WACC tax shield uses the official January 2026 NYU/PwC corporate marginal tax rate selected from canonical issuer domicile under a 400-calendar-day policy. It never uses listing country, quote currency, effective/cash tax, or a hard-coded default. Preferred equity, NCI, and other claims cannot disappear: only explicit zero or an aligned exact-zero aggregate `TEV - market cap - net debt` permits the initial two-component formula; any nonzero or unavailable treatment blocks WACC.

Weights are exactly `wE = E/(D+E)` and `wD = D/(D+E)`, with `E > 0`, `D >= 0`, and a deterministic sum-to-one check. After-tax debt cost is `pretax_cost_of_debt × (1 - marginal_tax_rate)`. A numeric result exists only when the existing `WaccInputReadiness` is `READY`: `WACC = wE × cost_of_equity + wD × pretax_cost_of_debt × (1 - marginal_tax_rate)`. No rounding, FX, DCF, reverse DCF, implied expectations, current-price comparison, own-history/peer combination, aggregation, stance, or UI behavior is authorized.
## Milestone 9B.3 final USD WACC evidence determination

The 9B.2 formula and readiness gates are frozen. Fiscal period ratios may supply direct gross-debt and net-debt observations only when the documented metric is actually returned with complete date/currency semantics. Gross debt remains a visible book-value proxy for the debt weight; net debt never becomes the debt weight. A direct coverage ratio is a validator, not a substitute for its annual numerator and interest expense.

For the synthetic-rating method only, Fiscal Operating Profit may be accepted as the Damodaran operating-income coverage numerator through explicit `DAMODARAN_COVERAGE_OPERATING_PROFIT_EQUIVALENT` evidence. The source observation remains `OPERATING_INCOME`; this rule does not change EV/EBIT, own-history, peer, or generic canonical semantics. Coverage still requires a deterministic same-period annual interest-expense magnitude.

Production company-class eligibility is no longer a caller assertion. It requires canonical US domicile, non-financial sector/industry, explicit operating-company type, and eligible USD market capitalization at or above USD 5 billion. Banks, insurers, incomplete classifications, non-US issuers, and sub-threshold companies fail closed.

The final META evidence result is Outcome B: the approved Fiscal boundary was reachable but did not return direct total debt or net debt; it returned only three old coverage-ratio observations, latest FY2011 at 41.8095. Explicit EBIT was unavailable and standardized interest-expense candidates conflicted under canonical actual selection. Preferred equity and NCI were not directly available, and aligned residual treatment therefore remained unavailable. No numeric production WACC is authorized.

## Milestone 10A reverse-DCF expectations readiness

Reverse DCF is an expectations engine: a future authorized solver may explain which operating assumptions reconcile to observed enterprise value, but it must not invent a forecast. Milestone 10A therefore builds evidence and readiness only. Its forward path uses one coherent annual FMP canonical consensus snapshot at or before analysis time and `EstimateCase.AVERAGE` only. FY1 is the first target-fiscal period ending after analysis, gaps are exposed, and no period is interpolated. Same-period EBIT/revenue produces an unclamped operating margin; revenue growth is calculated only across consecutive periods, with FY1 anchored only by an exact preceding canonical Fiscal annual revenue observation.

Fiscal actual Revenue, explicit EBIT, Operating Income, and EBITDA remain semantically separate. The narrow Damodaran Operating Profit equivalence from 9B.3 does not apply. The initial enterprise market anchor is only a fresh, positive, currency-aligned Fiscal `calculated_tev`; current price and reconstructed enterprise value are excluded.

Enterprise/FCFF readiness requires explicit marginal operating-tax evidence, defensible reinvestment or verified external FCFF semantics, frozen production WACC, and explicit currency-compatible terminal growth/margin/steady-state reinvestment policies. Generic FCF and OCF-minus-CapEx do not establish FCFF. Historical CapEx/D&A/NWC and ROIC are context only and never auto-forecast. Each controlled formulation retains its own blockers and may be `READY`, `PARTIAL`, or `NOT_READY`. No reverse solver, root finding, cash-flow PV, terminal value, implied growth/margin, modeled enterprise value, fair value, current-price comparison, family aggregation, stance, or UI behavior exists in 10A.

## Milestone 10B market-implied terminal-growth formulation

The only implemented solver formulation is `MARKET_IMPLIED_TERMINAL_GROWTH`. Annual FMP `AVERAGE` consensus Revenue and EBIT remain fixed; the service solves neither annual growth nor margin. Each explicit period derives `NOPAT = EBIT × (1 - eligible marginal tax)`, `reinvestment = change in Revenue / eligible SalesToCapital`, and `FCFF = NOPAT - reinvestment`. D&A, CapEx, working capital, and generic provider FCF are not forecast or substituted. Negative EBIT, NOPAT, reinvestment, and FCFF are retained when finite.

Sales-to-capital is explicit, positive, provenance-bearing evidence with no permanent default. Configured external evidence requires value, source, date, methodology, scope, and provenance. FY1 reinvestment needs the exact preceding canonical annual revenue base. Its absence blocks this cash-flow method as `FY1_REINVESTMENT_BASE`; it does not restore the generic 10A `ACTUAL_BASE` requirement to every terminal-growth formulation and it never implies zero FY1 reinvestment.

Timing is discrete annual fiscal indexing: FY1 through FYN are discounted at indices 1 through N using a READY production WACC. Terminal margin holds the final consensus EBIT margin. Terminal revenue is `Revenue_N × (1 + g)`; terminal NOPAT uses the same tax; terminal reinvestment uses the same sales-to-capital ratio; terminal value is `FCFF_(N+1) / (WACC - g)` with strict `WACC > g`, no denominator floor, and no intermediate rounding.

The root function is `ModeledEV(g) - observed canonical Fiscal TEV`. Deterministic bisection searches only the centralized fixed numerical domain from -20% to `WACC - epsilon`, after finite-arithmetic and monotonicity checks. It accepts an exact boundary root, returns `NO_SOLUTION_IN_DOMAIN` without widening when unbracketed, and retains initial function values, final bracket, residual, and iterations. The solved growth is an expectation output only: there is no GDP/plausibility label, fair value, target price, current-price comparison, upside/downside, sensitivity, aggregation, stance, or UI behavior.

## Milestone 10C execution modes and publication eligibility

Reverse DCF has exactly two caller-selected modes. `CANONICAL_EVIDENCE` preserves every 10B prerequisite and never consumes scenario evidence. `EXPLICIT_SCENARIO` may use explicit assumptions only for the immediately preceding annual revenue, sales-to-capital, and WACC. There is no automatic or fallback mode. FMP revenue/EBIT, target identity, fiscal periods, canonical marginal tax, Fiscal TEV, and final-consensus terminal margin cannot be overridden.

Each scenario assumption is an immutable, identity/snapshot-bound, provenance-bearing research input with explicit source, methodology, rationale, and entered-by metadata. WACC uses decimal semantics and remains separate from the frozen production `WaccResult`; sales-to-capital retains the 10B economic definition; preceding revenue must be positive canonical-base currency for the exact annual fiscal period immediately before FY1. Scenario revenue never enters canonical actual selection or a historical store. No assumption has a default.

Scenario mode uses a supplied assumption as an explicit override for that execution and retains any canonical reference/status alongside it. Missing scenario inputs may be satisfied by valid canonical evidence, but any solved execution in scenario mode is `SCENARIO_ONLY`. Only a solved, all-canonical execution in canonical mode is publication-eligible as `CANONICAL`; no historical scenario is automatically promoted. Incomplete executions are `UNAVAILABLE`. All reverse-DCF outputs remain market-expectations evidence and `central_valuation_eligible=false`.

The cash-flow, terminal, and root-solving mathematics remain the single 10B implementation. Results may retain final consensus revenue growth, final consensus EBIT margin, and implied terminal growth as observable comparators, but 10C adds no reasonableness label, score, GDP comparison, sensitivity grid, fair value, current-price comparison, target, upside/downside, aggregation, stance, or UI.

## Milestone 11B post-publication market comparison

Current market price enters only after the immutable 11A publication has completed. The sole initial semantic is the Yahoo regular-market-price field normalized by the existing Yahoo adapter. No alternate-price fallback chain exists. Price evidence must match the valued canonical security and issuer, share class, aware snapshot, currency, and per-share basis; issuer-only or ticker-only evidence is insufficient. The observation cannot be future-dated and must be no more than three calendar days old. Missing weekends do not require interpolation.

Quote currency is not quote unit. Verified USD/USD uses scale 1. Verified GBp or GBX with GBP uses scale 0.01 at the comparison boundary, yielding GBP/share. This rule comes from explicit identity metadata, never a `.L` ticker suffix. Missing semantics block comparison. There is no FX, ADR ratio inference, alternate listing substitution, or automatic share-class bridge.

The comparison layer consumes exact published values and never recalculates them. For every point, absolute gap is `valuation per share - normalized market price`; percentage gap is `valuation per share / normalized market price - 1`. Full precision is retained. `RESOLVED` alone permits an overall comparison, using the published overall central and the published family envelope. `WIDE`, `UNRESOLVED`, and `UNAVAILABLE` have no overall gap. A complete valid individual own-history or peer family may still be compared transparently under a nonresolved publication; partial evidence has no derived gap.

Reverse DCF is represented only through expectation evidence: implied terminal growth, final consensus revenue growth, final consensus EBIT margin, and an optional raw growth-rate difference. It never receives a price gap and never becomes central valuation. Analyst targets and provider DCFs remain reference-only and are not compared in this milestone. No comparison changes family eligibility, publication status, valuation inputs, or upstream results. There is no cheap/expensive label, confidence score, margin-of-safety label, target-price label, investment stance, recommendation, legacy execution, or Streamlit behavior.

## Milestone 11C provider-independent research-report boundary

The canonical presentation input is one immutable `StockResearchReport` built by the pure `build_stock_research_report` service. It consumes already-built identity, 11B price/comparison, 11A publication, 10A forward trajectory, 10C expectations/readiness, and reference-only evidence. It never calls providers or reproduces valuation, publication-overlap, price-gap, or reverse-DCF calculations. Exact canonical target security/issuer IDs and one aware analysis snapshot are mandatory across supplied sections; inconsistent inputs fail closed.

The report is factual and section-preserving. Identity retains display and currency/unit metadata without ticker inference. Market values come only from supplied `MarketPriceEvidence`. The valuation summary exposes an overall central only when the upstream `RESOLVED` publication already contains one; otherwise the numeric field is null and the display state is withheld. `OWN_HISTORY` and `PEER` rows always remain present, so an unavailable family is visible with null values and upstream blockers. Family-level and overall comparisons are copied only where 11B already authorized them.

Forward consensus remains externally sourced FMP evidence with fiscal periods, observations, derived trajectory fields, analyst counts, dates, source IDs, and nulls unchanged. Reverse DCF is labeled market expectations, explicitly distinguishing canonical, scenario, not-ready, and not-run states. A solved implied terminal growth is never labeled fair value. External analyst targets and provider DCFs remain `REFERENCE_ONLY`, cannot populate a family or overall central, and receive no new comparison arithmetic.

All sections carry controlled status, safe source labels, supporting IDs, issues, warnings, blockers, and provenance where available. Values stay unrounded; semantic formatting hints may be supplied, but formatted currency strings, HTML, CSS, Markdown layout, and Streamlit objects are outside the domain. Missing evidence stays null rather than zero-filled, and the data-quality/readiness section is a status matrix with no score. The report contains no recommendation, stance, cheap/expensive judgment, generated investment narrative, or screen-layout policy.

## Milestone 12A feature-flagged Streamlit report shell

`ENABLE_V1_REPORT_UI` is an explicit default-off route in the existing Streamlit entrypoint. When disabled or absent, the legacy page executes unchanged. When enabled, the route reads only `v1_stock_research_report` from Streamlit session state, renders that pre-built `StockResearchReport`, and stops before legacy page execution. If no report is supplied, it presents a factual empty state. Report construction, provider access, caching policy, and provider/error orchestration are outside the 12A UI boundary.

`render_stock_research_report` lives only in `stock_analyser.ui`. It renders the deterministic 11C order: security/market header, valuation summary, valuation families, forward analyst consensus, market expectations, reference-only evidence, data quality/readiness, and expandable methodology/evidence details. The renderer copies status labels, source labels, nulls, withheld states, issues, warnings, and blockers. It visually formats existing numbers and dates but does not normalize quotes, derive growth/margins, calculate valuation/gaps, interpret reverse DCF, modify publication status, or mutate report objects.

The visual policy is a warm off-white institutional research surface with charcoal/navy text, thin borders, restrained text-bearing status chips, small-radius panels, tabular numerics, dense tables, and limited semantic accents. Status always has text and is never conveyed by color alone. Long family/readiness blockers appear below compact tables rather than in clipped columns. Laptop-width checks cover the complete, partial, and live META examples. The page contains no marketing surface, scenario input controls, recommendation, stance, generated narrative, provider secret/internal request metadata, or ticker-specific conversion.

## Milestone 12B ticker-to-report integration

`V1ResearchCoordinator` is an application boundary outside the renderer. One explicit user submission creates one timezone-aware `analysis_as_of` and passes that exact snapshot through the approved bounded live research-report audit chain. That chain continues to own identity, own-history, peer-family orchestration, reverse-DCF readiness, publication, market comparison, and the 11C report build. The coordinator neither reconstructs nor recalculates those outputs.

`ResearchReportBuildResult` carries the requested symbol, snapshot, `READY | PARTIAL | FAILED | UNAVAILABLE`, controlled pipeline stage, optional canonical report, one short blocking reason, safe issues/warnings, cache state, supporting IDs, provenance, and policy IDs. Identity absence is fatal at `IDENTITY`; target/snapshot contradiction and builder-contract failure are fatal at `REPORT_BUILD`. Missing market price, unavailable peer evidence, not-ready reverse DCF, and absent references remain nonfatal whenever 11C can produce a coherent partial report. No unavailable section is fabricated merely to make the build succeed.

The application cache holds only successful canonical report results. It is bounded to eight entries, expires after five minutes, and keys by normalized requested symbol, five-minute snapshot bucket, and report-policy version. It does not replace provider/domain freshness rules. Failed executions are not cached. A normal Streamlit rerun reads session state and performs no build; a changed ticker runs only after Analyse; Retry/Rebuild invalidates every cached bucket for the current ticker and makes exactly one fresh attempt.

The Streamlit route owns ticker collection, factual loading/failure/empty states, safe session-state handoff, and explicit action buttons only. It does not resolve provider fields, choose peers, calculate valuation/publication/gaps, or interpret reverse DCF. Session state contains the build result, canonical report, requested symbol, and analysis timestamp, never raw payloads or authentication material. `render_stock_research_report` remains unchanged in its core boundary and still receives only `StockResearchReport`. The flag stays default-off and the legacy path remains intact.

## Milestone 12C integration execution policy

Each uncached report attempt owns one application-only `ResearchBuildContext` bound to one requested symbol and one aware analysis snapshot. Its registry is keyed by provider, canonical capability, canonical target, snapshot, and economically relevant parameters. It admits only immutable normalized/canonical contracts and explicitly rejects raw mappings, bytes, sessions, responses, credentials, headers, URLs, environment values, and opaque Fiscal company keys. This is per-build reuse, not a provider cache and not an economic domain model.

Where existing interfaces permit incremental injection, downstream paths reuse the resolved canonical target identity, Fiscal standardized actuals, Fiscal enterprise bridge, and FMP annual estimates. Distinct capabilities, targets, snapshots, and parameters never share entries. Peer discovery remains outside the registry because its normalized adapter contract intentionally carries an opaque provider lookup field for immediate adapter-local use. No identity is replaced with ticker equality and no provider fallback was introduced.

One process-local single-flight exists per completed-report cache key. A concurrent identical caller waits for the owner and receives the same safe success or failure for that attempt; it never starts a duplicate live chain. Building is distinct from cached. Only successful canonical reports enter the unchanged eight-entry, five-minute, ticker/snapshot-bucket/report-version cache. Failure releases waiters, is not cached, and permits one explicit future retry.

Operational events reuse the 12B stage taxonomy and contain only controlled status, aware start/end, nonnegative duration, optional safe blocker, issue/warning counts, and normalized-reuse count. Duration is not evidence quality. The compact diagnostics expander displays stage/status/timing only and never request totals, provider URLs, keys, or exception detail. Cooperative cancellation of future stages was not added because current blocking provider calls cannot be truthfully terminated at this boundary; `cancellation_supported` remains false.

## Milestone 12D live identity/configuration integration boundary

Ambient coordinator execution resolves project configuration through the same explicit application boundary as the approved live audits. Fiscal configuration is sufficient to attempt canonical identity; missing downstream FMP configuration cannot pre-empt that identity attempt. Canonical identity still requires stable Fiscal issuer/security identifiers and listing evidence, with Yahoo used only as supplementary evidence. One immutable diagnostic exposes controlled configuration, client, request, HTTP, parse, normalization, and canonical-assembly states without URLs, headers, credentials, response bodies, exception representations, or opaque provider keys.

## Milestone 12E overview and valuation presentation

The overview is a presentation-only view over `StockResearchReport`. It places company/security identity, analysis timestamp, canonical market status/value/time/source, reporting currency, quote currency/unit, and quote scale in a compact factual header. The valuation strip always shows publication status, upstream overall central or `Withheld`, upstream market value or `Unavailable`, eligible central families as `x of 2`, and upstream overall price gap or `Withheld`. The denominator is permanently the two central families—own history and peers; reverse DCF remains expectations evidence and is never counted.

Both known valuation families remain visible in a dense table with controlled status, human-readable method, supplied lower/central/upper values, supplied family gap, currency/per-share unit, and a safe evidence or blocker label. Long blockers are repeated below the table so truncation in a narrow data cell cannot hide them. A family central is never promoted to an overall central and unavailable values remain em dashes rather than zero placeholders.

The optional Altair range chart is constructed only from supplied upstream points. Each eligible family may contribute its supplied lower/central/upper values; an unavailable family contributes no point. A supplied current market value may contribute a separate marker only when market evidence is available and currency-compatible. An overall row is permitted only for a `RESOLVED` publication with a supplied lower envelope, central value, and upper envelope. No means, medians, interpolations, envelopes, price gaps, valuation values, or publication decisions are calculated in the UI. Empty marker layers are omitted rather than represented as fake data.

The visual language remains restrained equity research: warm off-white surface, charcoal/navy text, conventional system fonts, thin rules, compact status labels, tabular numerics, and no gradients or marketing cards. Status and withheld meaning are textual, not color-only. The default-off feature flag, report-only renderer input, provider isolation, absence of scenario/recommendation controls, and legacy route are unchanged.

## Milestone 12F forward consensus and market-expectations presentation

Forward analyst consensus is explicitly labeled external analyst consensus and retains the supplied safe source and per-period estimate snapshot. The renderer preserves report period order and uses the supplied horizon label as the fiscal-year label; it never infers a fiscal year from ticker, current date, or period end. The dense semantic table copies supplied period end, revenue, revenue growth, EBIT, EBIT margin, EBITDA, and revenue analyst count. Financial values are tabular and right-aligned, supplied growth uses an explicit sign, and null EBITDA/counts remain em dashes. No operating-income relabeling, EBITDA derivation, count inference, confidence score, or forecast narrative is permitted.

The single optional consensus visual uses only supplied revenue-growth and EBIT-margin percentage points on one semantic scale. It has no calculation transform, line interpolation, moving average, hidden normalization, low/high confidence band, or extrapolated period. Missing metrics contribute no point. The table remains the accessible textual equivalent and factual tooltips retain fiscal label, period end, supplied value, and estimate snapshot.

Market expectations remain structurally separate from valuation. The controlled states are canonical expectations, scenario expectations, not ready, and not run. A supplied canonical or scenario entry may show implied terminal growth, final consensus revenue growth, final consensus EBIT margin, and the supplied raw growth-rate difference; none is calculated or interpreted in the UI. Canonical execution and publication eligibility are explicitly labeled. Scenario output uses a restrained warning-style treatment, an explicit `SCENARIO` label, and the existing supplied assumption value/currency/period/source/method metadata. It remains read-only.

`NOT READY` presents implied terminal growth as `Withheld` and then copies upstream safe blockers instead of showing empty comparator tiles. `NOT RUN` states factually that reverse DCF was not run for the snapshot and is not presented as an error. Reverse DCF is never called fair value, target price, intrinsic value, upside, or downside; it receives no aggressive/conservative judgment, expectation score, generated commentary, solver invocation, FCFF construction, WACC application, or scenario control. Source claims appear only when already supplied through report evidence.

Milestone 12F changes no `StockResearchReport` contract, consensus or reverse-DCF economics, application coordinator, build context, normalized registry, single-flight, report cache, retry, identity/configuration, provider adapter, default-off feature flag, or legacy route.

## Milestone 12G methodology, evidence, and readiness presentation

Reference-only evidence remains analytically separate from central valuation. Every supplied analyst-target or external-provider-DCF reference row carries its upstream type, controlled status, safe source, supplied evidence timestamp, and explicit textual `REFERENCE ONLY` eligibility. The renderer does not calculate a reference value, place references beside overall fair value, or promote them into a valuation family. The section remains visible with a factual empty state when no references exist.

The readiness matrix is a direct presentation of the six existing controlled report rows: market price, own-history valuation, peer valuation, forward consensus, reverse DCF, and overall valuation publication. Status labels are copied without semantic collapsing. Each row shows its first safe upstream blocker plus source and observation/snapshot context derived only from the report sections already bound to that readiness area; all supplied blockers remain available below the table. No aggregate quality, readiness, confidence, freshness, performance, percentage, letter grade, star, gauge, or traffic-light score exists.

One restrained `Methodology & evidence` appendix separates the report analysis timestamp, market observation time, and consensus estimate snapshots. It contains short static factual labels for the approved own-history, peer, overall-publication, and reverse-DCF boundaries; a report-level source list deduplicated by safe display name with supplied observation/as-of dates; and distinct Issues, Warnings, and Blocking reasons sections. It does not expose supporting IDs prominently and never renders provider URLs, request headers, credentials, raw payloads, environment variables, opaque company keys, or raw exception detail.

The integration route reuses the existing controlled build-stage events in one visually secondary `Technical build diagnostics` expander. Only stage, controlled status, formatted duration, and safe blocker are shown. Duration and reuse remain operational metadata and are explicitly not research-quality measures. No second diagnostics architecture, coordinator change, cache change, provider call, report-contract expansion, economic calculation, generated diagnosis, scenario input, stance, global V1 enablement, or legacy-route change was introduced.

## Milestone 12H explicit reverse-DCF scenario execution UI

The scenario workflow is an application-owned extension immediately after the immutable canonical market-expectations section. It is collapsed by default and visually subordinate. The form exposes exactly three blank required inputs: WACC in percent, sales-to-capital in ratio units, and the exact preceding annual revenue in a fixed, visibly labeled valuation-currency billions scale. It supplies no defaults, provider-derived suggestions, presets, sensitivity controls, or hidden assumptions. Changing a field does not execute; one explicit `Run scenario` submit executes once, while `Reset scenario`, a new ticker result, Analyse, or Retry/Rebuild clears every scenario field and result.

`ReverseDcfScenarioExecutionContext` is a narrow immutable application contract retained alongside the coherent report result. It contains only already-normalized domain contracts required by the approved 10C execution: target-bound forward operating trajectory, market enterprise-value anchor, optional marginal tax, optional canonical actual base, optional canonical sales-to-capital, and optional production WACC. The context must match the exact canonical security ID, issuer ID, aware analysis snapshot, and valuation currency. It contains no raw payload, response, session, credential, header, URL, environment value, or opaque provider lookup key. Missing or contradictory context fails closed and never triggers provider access.

On explicit submission, the application boundary converts displayed WACC percent to decimal once and displayed annual revenue billions to base currency once. It then creates exactly three immutable, identity/snapshot-bound `USER_SUPPLIED` scenario assumptions carrying explicit source, method, rationale, user-entry, currency, frequency, fiscal-year, period-end, and provenance metadata. All reverse-DCF economics remain in the unchanged `execute_reverse_dcf` 10C service: fixed FMP trajectory, tax policy, TEV anchor, terminal-margin policy, reinvestment method, solver bounds, and bisection mathematics are not reproduced or modified in Streamlit or the application controller.

A solved interactive execution is always labeled `Scenario`, `Explicit scenario`, and `Scenario only`; it is permanently central-valuation ineligible and emits no fair value, target, upside/downside, recommendation, or plausibility judgment. The supplied implied terminal growth and executed assumptions are displayed separately from canonical evidence. A canonical solved expectation remains canonical; a canonical `NOT_READY` state and its blockers remain visible even when the explicit scenario solves. No scenario can mutate the report, valuation families, publication, current-price comparison, or canonical evidence, and only one active in-session scenario result is retained.

## Milestone 12I final acceptance and cutover contract

Final acceptance changes no valuation or publication rule. The Streamlit boundary continues to format and plot only values already supplied by `StockResearchReport`; it contains no valuation, publication, price-gap, reverse-DCF, peer-selection, provider-parsing, or canonical-identity implementation. `RESOLVED` alone may display the supplied overall central value. `WIDE`, `UNRESOLVED`, and `UNAVAILABLE` retain withheld/null overall values. Known own-history and peer rows remain visible, unavailable values remain null, scenarios remain `SCENARIO_ONLY`, and current price never feeds valuation.

Cross-stock portability requires canonical security resolution before currency, market, valuation, or scenario publication. The final live matrix proved the generic US path but not the representative LSE path: META, MSFT, and NVDA reached stable Fiscal-anchored identity and coherent partial reports; SHEL.L and RR.L reached Fiscal HTTP/parsing and then failed closed because the approved resolver did not establish unique base-ticker plus explicit London-venue evidence. No `.L` inference, ticker equality, guessed venue, implicit FX, Yahoo canonical substitution, or new provider was added. The default-cutover decision is therefore `NOT_READY_FOR_DEFAULT`; the feature flag remains default-off pending a separately authorized LSE identity portability/revalidation milestone.

## Milestone 12J canonical LSE evidence acquisition

LSE portability is an identity-acquisition rule, not a valuation or currency rule. A `.L` request identifies London as requested listing context only. Fiscal must return exactly one listing with the matching base ticker, explicit accepted London venue evidence (`LSE`, `XLON`, or the documented exchange name), stable issuer identity, and stable security identity. Ticker equality, suffix presence, company name, market cap, ordering, or Yahoo evidence cannot establish canonical binding.

Acquisition is deterministic: reuse the cached first compact company-list page; return immediately for an already-proven unambiguous symbol; for unresolved London context, perform one exact Fiscal profile lookup using base ticker plus `XLON` MIC; only exact not-found may activate company-list pagination. Pagination is capped at 16 pages and 16,000 rows, and profile inspection at 256 listings. Exceeded bounds, multiple plausible London listings, conflicting venue codes, or stable issuer/security/listing disagreement fail closed. Access denial stops without fan-out.

Profile enrichment for a company-list candidate uses its stable Fiscal identifier through `fscl`, never ticker/name as proof. Internal company-key caching remains available only for already-verified downstream Fiscal financial requests and never becomes canonical identity. Safe diagnostics expose counts, pages, venue-found, resolution status, enrichment attempt, and bound exhaustion only.

This milestone adds no `.L` quote arithmetic, FX, USD risk-free substitution, valuation, publication, gap, scenario economics, provider role, recommendation, or UI behavior. Quote currency/unit/scale continue to come from canonical provider metadata after identity. Non-USD reverse DCF remains fail closed when currency-appropriate discount-rate evidence is unavailable.

## Milestone 12K provider availability and default-cutover policy

Application portability failure and provider coverage limitation are separate release concepts. Portability failures include wrong/ambiguous security acceptance, crash, unsafe fallback, ticker-specific behavior, incorrect currency/unit handling, implicit FX, state leakage, retry loops, canonical/scenario contamination, incorrect publication, secret exposure, or an unusable primary route. Any such defect blocks cutover.

A controlled individual-security provider access denial does not block cutover when the approved request is made, the generic provider integration and same listing family are independently proven, no substitute security or provider is used, identity/report values remain withheld, the route presents a safe unavailable result with one explicit bounded Retry, cross-ticker/scenario state is cleared, and the full suite passes. This is a product coverage limitation, not a claim that the ticker is invalid or unsupported by the analyser architecture.

The application-facing classification is derived only from controlled identity diagnostics: `AVAILABLE`, `NOT_FOUND`, `ACCESS_DENIED`, `AUTHENTICATION_FAILURE`, `RATE_LIMITED`, `PROVIDER_FAILURE`, `EVIDENCE_CONFLICT`, and `UNAVAILABLE`. The primary view does not render HTTP status, provider URL, credentials, raw response, opaque company key, or exception detail. It displays the requested symbol, safe Identity stage, safe product reason, and Retry.

Approved evidence satisfies the release gate: META/MSFT/NVDA prove generic US behavior; SHEL.L proves generic LSE and GBP/GBp behavior; RR.L proves controlled access denial. The decision is `READY_FOR_DEFAULT`, but `ENABLE_V1_REPORT_UI` remains default-off until the separately authorized 13A release milestone. No valuation, publication, market comparison, peer, WACC, reverse-DCF, scenario mathematics, provider, or legacy behavior changes in 12K.

## Milestone 13A release route policy

Version 1.0.0 makes the existing canonical research-report coordinator and renderer the default Streamlit route. Startup presents one empty Ticker / Analyse surface and makes no provider request until explicit submission. Identity failure, provider access denial, partial evidence, and report unavailability remain controlled V1 states and never trigger a silent legacy fallback.

The only operator rollback is `USE_LEGACY_UI`. Accepted case-insensitive true values are `1`, `true`, `yes`, and `on`; absent, blank, false, and invalid values select V1. The old `ENABLE_V1_REPORT_UI` name remains as a deprecated import-compatible constant but has no routing authority. Therefore an explicit rollback always wins and contradictory old/new values cannot be ambiguous. Rollback mode displays a factual legacy label, waits for explicit Analyse, and retains the legacy implementation for the initial release window.

This release change does not alter identity proof, provider endpoints or roles, cache/single-flight policy, own-history or peer valuation, WACC, publication, price comparison, reverse-DCF or scenario mathematics, currency/unit normalization, or report contracts. Missing provider configuration and per-security entitlements continue to fail only the relevant submitted capability.
