# V1 Provider Matrix

Provider adapters own authentication, capability detection, provider symbols, field mapping, units, period parsing, retries, and error translation. Services receive canonical observations only.

## Milestone 3 actual-data boundary

The read-only V1 adapters use injected sources and are not wired to the legacy application. Yahoo supplies traded-security identity plus point-in-time price, market cap, shares outstanding, beta, and volume. Only share price receives explicit quote-unit scaling; statement values, shares, and market cap do not. Price history remains unsupported in this milestone.

Fiscal supplies a bounded standardized-actual map for revenue, gross profit, operating income, explicitly named EBIT, EBITDA, net income common, operating cash flow, CapEx cash outflow, depreciation/amortization, cash, gross debt, basic/diluted/outstanding shares, and provider-defined FCF. Operating income is not duplicated to EBIT. Negative CapEx cash outflow is converted to a positive canonical use of cash with provenance. Generic FCF remains `provider_defined_fcf`. As-reported Fiscal data is not mixed into this dataset.

SEC supplies bounded US reported-actual verification for characterized revenue, operating income, net income, operating cash flow, PP&E acquisition payments, cash, and entity common shares tags. The SEC provider identifier is CIK, not ticker. Filing/as-of time remains distinct from the reported period end, and provenance retains taxonomy and tag. Unsupported tags are skipped without fuzzy matching.

Yahoo capabilities are `identity`, `current_price`, `market_snapshot`, and the controlled but currently unsupported `price_history`; Fiscal capabilities are `identity` and `historical_standardized_financials`; SEC capabilities are `identity` and `reported_actuals`. No adapter calls another provider or reconciles observations.

## Milestone 4 FMP forward boundary

FMP uses the injected retrying transport and lazy secret boundary. Its independent capabilities are `annual_analyst_estimates`, `analyst_price_targets`, and `external_standard_dcf`. Generic transport 403 remains `ERROR` unless stable provider-specific evidence has already produced a structured entitlement error; endpoint failure never clears another capability.

The annual adapter maps only revenue, EBIT, EBITDA, generic net income, and generic EPS low/average/high levels. Revenue and EPS alone receive their explicitly named analyst counts. Generic net income is not relabeled `net_income_common`; generic EPS is not relabeled basic or diluted. Fiscal period start is constructed only when the explicit provider period end aligns with `CompanyIdentity.fiscal_year_end`. Reporting currency is mandatory and quote subunit scaling is not applied.

Milestone 10A consumes this existing FMP surface without changing the adapter. Only annual `AVERAGE` revenue and EBIT form the reverse-DCF operating trajectory; EBITDA remains optional context, and low/high remain dispersion evidence. Observations must share target symbol, fiscal period, snapshot, reporting currency, and base monetary unit. Missing years and metrics remain missing; no provider averaging, FX, scaling, or forecast interpolation occurs.

The consensus service consumes canonical observations only. It preserves historical endpoint rows as estimates but excludes periods ending on/before the snapshot date from FY1/FY2 and derivations. FY1 is the first annual period ending after as-of, not NTM. It derives only aligned average-case adjacent revenue growth and same-period EBIT/EBITDA margins. Low/high cases remain ranges, not scenarios. Quarterly estimates, NTM, implied diluted shares, reconciliation, coverage, and valuation are deferred.

Price target low/median/consensus/high and standard FMP DCF are external references in resolved quote currency. FMP's accompanying returned stock-price field is ignored; Yahoo remains the intended market-price source. Missing currency yields structured unavailability, never USD defaulting.

## Milestone 5 validator and macro boundary

Finnhub exposes independent annual capabilities for revenue, EBIT, EBITDA, generic net income, generic EPS, operating cash flow, CapEx, and FCF. Each capability uses its own provider request and status, so a locked FCF endpoint does not invalidate revenue. Low/average/high remain distributions and metric counts are used only when explicitly returned. CapEx sign conversion requires an explicit sign contract. Generic FCF maps to `provider_defined_fcf` with safe definition evidence; it maps to FCFF/FCFE only when injected verified semantic evidence establishes that economic definition.

Alpha Vantage exposes the shared `earnings_estimates` and `estimate_revisions` capabilities from its bounded earnings-estimates contract. It maps annual and quarterly revenue and generic EPS levels, while revision window/count/prior-estimate evidence is normalized into separate immutable records. It does not promote EPS to basic/diluted or compare with FMP.

FRED exposes `usd_treasury_yield` for DGS10 only. Values are daily USD Treasury yields normalized from percent to decimal, with observation and real-time dates preserved independently from retrieval time. Missing values are skipped. The macro resolver selects the latest valid observation on/before the requested date; non-USD currencies return structured unavailable and never receive a USD fallback. No WACC, ERP, beta, valuation, or company-fundamental behavior is present.

## Milestone 6A reconciliation boundary

Reconciliation operates only on canonical domain observations bound to resolved `CompanyIdentity`. Provider symbols remain visible in audit output but are never match keys. The default canonical policy is Yahoo for current price, Fiscal standardized actuals with comparable SEC reported validators, and FMP annual forward operating consensus with Finnhub plus Alpha for revenue/generic EPS. No canonical forward FCFF/FCFE source is established.

Each validator is compared independently after exact identity, metric, type, frequency, period, case, currency, and unit alignment. Only snapshots at/before `comparison_as_of` are eligible. Missing or unavailable validators remain absence/availability states, not zero and not disagreement. Standardized-versus-reported dataset identity remains explicit. Reconciliation records differences and issues but never averages, replaces, withholds, scores coverage, or changes application behavior.

The shared V1 infrastructure uses controlled `ProviderId` values for Yahoo, Fiscal, FMP, Finnhub, FRED, Alpha Vantage, and SEC. Existing Milestone 1 provenance/provider strings remain unchanged at the domain boundary to avoid churn; adapters serialize `ProviderId.value` when constructing those records.

| Metric/capability | Canonical provider | Validators | Entitlement/failure expectation | Fallback | Valuation use |
|---|---|---|---|---|---|
| Current price, history, returns, volume, liquidity | Yahoo | FMP/Finnhub where available | Endpoint gaps/transient errors | Method unavailable if price is required | Yes, after quote normalization |
| Market cap, beta | Yahoo | FMP/Finnhub | Missing fields common | Deterministic beta only with documented benchmark/method; otherwise unavailable | Yes/cross-check |
| Historical standardized statements and ratios | Fiscal.ai | SEC for US; Yahoo secondary | Key/endpoint capability may vary | Yahoo may be explicitly labeled lower-quality fallback during migration | Yes |
| As-reported US actuals and filing periods | SEC | Fiscal | US only; filing taxonomy differences | Fiscal historical | Verification, not forecasts |
| Segments, KPIs, filings, IR, events, transcripts | Fiscal.ai | SEC/issuer material | Transcript/feature may be locked independently | Preserve accessible bundle; mark feature unavailable | Evidence; numeric actuals only when mapped |
| Annual forward revenue/EBIT/EBITDA/net income/EPS ranges and counts | FMP | Finnhub; Alpha Vantage for revenue/EPS | Plan/endpoint/date coverage varies | No invented forecast; partial metric table | Yes, FMP canonical consensus |
| Quarterly forward estimates | Not selected | Alpha validator where supplied | Sparse horizons likely | Remain independent; never synthesize NTM | No canonical quarterly consensus yet |
| Forward FCF/OCF/CapEx | Not selected | Finnhub observations where entitled and defined | Frequently locked; `FCF` definition may be opaque | No canonical fallback | Reconciliation/valuation unavailable until policy and definitions are approved |
| Analyst price targets | FMP | Yahoo/Finnhub | Coverage may be absent | Unavailable reference | Reference only; zero aggregation weight |
| Provider standard DCF | FMP | None | May be absent | Unavailable reference | Reference only; zero aggregation weight |
| Peer candidates | FMP and/or Fiscal | Canonical metadata and target/peer fundamentals | Candidate lists can be noisy | Peer method unavailable below credible minimum | Candidate generation only |

Milestones 8A–8A.2 implement the Fiscal half of this row only. The documented source is `GET /v3/company/profile`; its embedded `peers` array supplies ordered candidates with relationship/reasoning assertions, stable `companyFiscalIdentifier`, primary-listing `securityFiscalIdentifier` and ticker/exchange/FIGI identifiers, company type/geography, classification hierarchy, and market-cap metadata. Stable Fiscal company/security IDs plus ticker/exchange establish bounded canonical issuer/security identity without requiring security type. Full profile is optional enrichment, not an unconditional gate. Raw DTO fields remain inside the boundary; reasoning and provider market cap never become selection inputs.

The 2026-08-28 verification established that live `primaryListing` exposed ticker, exchange, operating MIC, `securityFiscalIdentifier`, and FIGI identifiers but no security-type field; peer `companyType=operating_company` is issuer classification and is not repurposed as equity-security type. Milestone 8A.2 therefore separates identity from `PeerSecurityEligibilityEvidence`. Missing security type preserves `SUMMARY_VERIFIED` identity while eligibility and economic selection fail closed as `UNVERIFIED`. Verified full-profile security class or listing-symbol/exchange-compatible Yahoo `quoteType` may enrich eligibility without changing Fiscal-anchored IDs. No `Ordinary share` default or ticker-based cross-provider identity is fabricated.

Fiscal standardized actuals remain canonical financial evidence. The new actual selector deduplicates compatible equal observations and selects conflicting values only under explicit, unique provider revision/source chronology; otherwise it returns `UNRESOLVED`. Retrieval time and SEC are not replacement rules. Annual EBITDA remains missing when absent and is not built from operating income plus D&A.

For reverse-DCF readiness, Fiscal remains the canonical actual-base and market-anchor source. Explicit annual revenue may anchor FY1 growth; explicit EBIT and Operating Income remain separate. The enterprise market anchor is the latest eligible positive `calculated_tev` under the centralized 45-calendar-day policy. Market cap, current share price, guessed debt, Yahoo accounting fields, and reconstructed enterprise value are ineligible. Frozen production WACC remains a required external dependency and is not recalculated by 10A.

FMP officially documents stable `search-symbol`, `search-cik`, `search-cusip`, `search-isin`, `profile`, and `profile-cik` endpoints. Fiscal summary IDs are provider-specific and the verified live summary supplied no CIK/CUSIP/ISIN suitable for a stable cross-provider join. No FMP resolver was therefore added, no Fiscal ticker was copied into FMP identity, and ambiguous provider-symbol readiness fails closed. Forward EBITDA is method-data readiness only and never economic peer membership.
| US risk-free and macro | FRED | Trusted market source | Series lag/outage | Explicit user override with provenance or DCF unavailable | USD methods only unless currency matches |
| Non-US currency risk-free | Currency-specific configured source (to be selected) | Secondary sovereign source | Provider not yet selected | Explicit override or DCF unavailable | Yes only when currency-appropriate |
| ERP | External configured dataset or explicit configuration | Secondary published source | Licensing/update cadence | Explicit override with provenance; otherwise method unavailable | Yes |
| EPS/revenue revisions and analyst counts | Alpha Vantage | FMP/Finnhub | Rate limits/coverage | Mark validation unavailable | Confidence/momentum; canonical only if policy changes |
| Ollama transcript interpretation | Local Ollama | Deterministic evidence IDs | Local server/model absent | Interpretation unavailable | Never numeric valuation |

## Canonical and validation policy

FMP remains the canonical annual forward-consensus observation when it is semantically valid. Finnhub and Alpha Vantage observations remain independent validators. Milestone 6A calculates pairwise agreement only after strict alignment, emits issues for warning/conflict bands, and preserves FMP unchanged. It performs no validator averaging or canonical replacement. Milestone 6B consumes these immutable results for coverage and confidence without rerunning comparisons.

## Milestone 6B coverage boundary

The coverage service is provider-call-free and consumes canonical observations, capability results, Milestone 6A reconciliation results, cash-flow definition evidence, and the Milestone 5 risk-free result. It accepts no provider DTO or raw response. Evidence after explicit `analysis_as_of` is excluded.

Market breadth uses canonical current price plus available market-cap/share inputs. History uses annual actuals only. Forward breadth uses FMP canonical future annual average observations only. Finnhub/Alpha remain validators; missing or locked validation reduces independent evidence but never becomes disagreement. Analyst counts remain sourced per revenue/EPS horizon and are not averaged. Linked verified Finnhub cash-flow economics can improve the cash-flow-evidence dimension but cannot create discount-rate readiness.

FRED DGS10 supports USD macro readiness only. Non-USD remains unavailable and never falls back to USD; this affects cash-flow readiness, not own-history readiness. Peer and historical-valuation dimensions are explicitly not evaluated because those V1 layers do not exist. No provider fallback, forecast, valuation, aggregation, UI, or legacy behavior is introduced.

## Milestone 6C live contract boundary

The opt-in `scripts/v1_provider_smoke.py` entry point invokes existing V1 adapters only when explicitly run. It loads credentials at runtime, isolates endpoint/provider failures, supports selected providers, strict exit semantics, and an allowlisted JSON summary, and never persists or prints raw provider responses or authentication material. Normal pytest remains fully synthetic and network-free.

Live verification confirmed Yahoo identity/market normalization, SEC ticker-to-CIK and bounded reported actuals, and Alpha Vantage estimates/revisions for META. Alpha's real response is a unified snake-case `estimates` collection distinguished by `fiscal year` and `fiscal quarter` horizons, rather than the earlier split synthetic sections. The adapter now supports that provider-boundary shape while preserving canonical revenue/generic-EPS and separate revision semantics. The live response exposed 30-day EPS revision evidence and no corresponding revenue revision fields; nothing was invented.

The current local provider configuration returned HTTP 403 for Fiscal, authentication `ERROR` for FMP and Finnhub, and request/configuration `ERROR` for FRED; those boundaries could not be normalized further. Under the final generic-403 policy, Fiscal's 403 remains an unclassified access `ERROR`, not an assumed entitlement lock. Documented Finnhub operating-estimate endpoints are capability-specific and return nested `data`; an unclassified 403 on exactly one of those documented premium paths is translated by the Finnhub adapter to `LOCKED`. Undocumented OCF, CapEx, and FCF paths are not probed and remain `UNAVAILABLE`. FMP remains canonical forward consensus, Alpha/Finnhub remain validators, external targets/DCF remain reference-only, and generic FCF remains unclassified economically.

## Milestone 6D access/configuration boundary

Provider-contract comparison and minimum targeted live calls established that FMP's stable query authentication and endpoint path are correctly constructed; the configured credential is rejected with HTTP 401 on both stable and documented v3 annual-estimate paths. Finnhub likewise rejects the configured credential with HTTP 401 using either documented token placement and either provider host. These are authentication/account-configuration outcomes, not schema or entitlement evidence, so adapter financial semantics remain unchanged.

At the Milestone 6D checkpoint, Fiscal's V1 source sent `StockAnalyser/1.0` and removed the undocumented `pageSize` parameter; its then-current company-list HTTP 403 remained an unclassified access `ERROR`, never an automatic entitlement lock. The Milestone 6E evidence below supersedes that access diagnosis. FRED's documented DGS10 endpoint and parameters were confirmed; the local smoke boundary rejects a configured key that does not match FRED's documented format before network I/O. No provider fallback, reconciliation, coverage, valuation, or application wiring changed.

## Milestone 6E Fiscal live boundary

New direct evidence superseded the Milestone 6D Fiscal access conclusion. Fiscal's gateway distinguishes the proven `StockAnalyser/0.7.1` user agent from the V1 smoke's former `StockAnalyser/1.0`; restoring the exact legacy user agent resolves the company-list 403 with the same credential. Profile and standardized endpoints use `companyKey` with the returned company key in the proven live contract. The stable Fiscal identifier remains canonical issuer metadata and is not substituted for that endpoint lookup key.

The verified live standardized schema supplies `reportDate` as period end. A live-boundary-only alias feeds the established bounded parser without modifying legacy execution. Annual and quarterly observations retain currency, source metric, period, retrieval/as-of distinction, and provenance. The final META run mapped revenue, operating income, net income common, operating cash flow, depreciation/amortization, cash, basic shares, diluted shares, and outstanding shares. It did not infer missing explicit EBIT, EBITDA, CapEx, or debt observations.

## Milestone 7A Fiscal historical valuation boundary

Fiscal is the canonical provider for the initial historical-multiple observation layer. The source is the documented daily ratio endpoint `/v1/company/ratios/daily/{ratioId}`, using the already verified `StockAnalyser/0.7.1` user agent, `X-Api-Key`, and `companyKey`. It is a distinct `historical_valuation_ratios` capability and does not alter Fiscal standardized-actual availability.

Milestone 8A.3 adds a peer-financial lookup boundary within Fiscal only. Exact same-record `companyFiscalIdentifier -> companyKey` evidence from the bounded v3 company list, or the already normalized peer-summary record itself, may unlock the existing standardized-financial endpoints. Multiple matches, stable-ID conflicts, and material primary-listing conflicts are `AMBIGUOUS`; missing evidence is `UNAVAILABLE`. The opaque key remains lookup metadata and is never a cross-provider key, canonical identity, or safe-output field. Compact rows may omit reporting currency; the adapter uses an explicit normalized financial-row currency or cached profile currency and never assumes USD for this peer path.

Milestone 8A.4 traced target and candidate requests to the same `GET /v1/company/financials/{statement}/standardized` helper with identical host, method, parameter names, statement scope, headers, user agent, session, timeout, and redirect behavior; only the verified `companyKey` value differs. A bounded SNAP direct control proved the candidate key was non-empty and was neither `companyFiscalIdentifier`, ticker, literal `companyKey`, nor META's key, but Fiscal returned HTTP 403 before normalization. The final audit repeated that provider result for SNAP, PINS, RDDT, 700, and RBLX; 1024 returned HTTP 404. GOOG returned `AVAILABLE` and 429 normalized annual observations, proving the generic request/parser path works. These are endpoint-level provider access/data outcomes, not permission to rematch by ticker or classify generic 403 as entitlement.

Fiscal standardized statements are issuer-level. The adapter therefore requires the verified Fiscal issuer binding and company-key resolution but does not require a representative security ID or provider-symbol string to equal issuer-level financial identifiers. Identity conflicts still fail closed. Target and candidate currency handling is now identical: explicit normalized row currency, then verified cached/profile reporting currency, otherwise unavailable—never an implicit USD or quote-currency fallback.

Milestone 8B consumes canonical provider outputs without adding a provider endpoint. Current peer enterprise-value support is the already normalized Fiscal `calculated_tev` point-in-time observation; the peer service does not reconstruct TEV from Yahoo market cap or arbitrary components and contains no provider scale logic. The current FMP estimate adapter normalizes annual EBITDA estimates and explicit retrieval-time source-as-of substitution, but its approved identity/search path exposes no verified CIK, ISIN, or CUSIP binding for Fiscal-discovered peers. FMP forward EBITDA therefore fails closed for live peers until a separate stable cross-provider binding exists; ticker or company-name equality is insufficient.

The single 2026-08-28 META readiness audit reused the existing bounded 8A path. It found 9 canonical candidates, 0 economically included peers, a `PARTIAL` economic set, 0 EV/EBITDA observations, and an `UNAVAILABLE` 8B subset. The 8B layer made no additional provider request and did not borrow GOOG, SNAP, PINS, RDDT, or other unverified candidates.

The verified response is a bare list of daily `{date, ratio}` records rather than the legacy nested report-period shape. The live source maps that shape to a bounded adapter DTO; only canonical `HistoricalValuationObservation` records, safe source metrics, capabilities, and issues cross the adapter boundary. The daily economic date is preserved. The response provides neither financial denominator period end nor provider snapshot timestamp, so `period_end` remains absent and retrieval-time substitution for source snapshot as-of is explicitly recorded in provenance. Daily rows are never relabeled annual or quarterly.

Supported source metrics are `ratio_price_to_earnings`, `ratio_ev_to_ebitda`, and `ratio_ev_to_ebit`. Fiscal documents their formulas as share price/diluted EPS, TEV/EBITDA, and TEV/Operating Profit, respectively. Accordingly EV/EBIT's provider-specific denominator is retained without globally promoting operating income to EBIT. Cash-flow multiples remain withheld because their economic FCF definition is unverified. No FX, outlier treatment, historical windows, forward consensus, or fair-value calculation occurs here.

### Milestone 7C.1 Fiscal enterprise bridge

| Fiscal source metric | Canonical mapping | Verification | Constraint |
|---|---|---|---|
| `calculated_tev` | `enterprise_value`, currency | Official formula evidence plus live META daily `{date, ratio}` list | Same Fiscal identity/date/currency only |
| `calculated_market_cap` | `market_cap`, currency | Official definition plus live META daily `{date, ratio}` list | Same Fiscal identity/date/currency only |
| `market_data_total_shares_outstanding` | `shares_outstanding`, shares | Official ADR/ADS semantics plus live META `{date, shareClasses, totalSharesOutstanding}` list | Adapter maps only date and ADS-aware total; no invented conversion |
| `calculated_total_debt` | `gross_debt`, currency | Official formula and synthetic adapter coverage | Optional audit mapping; not needed or zero-filled for the direct bridge |
| `calculated_net_debt` | `net_debt`, currency | Official formula and synthetic adapter coverage | Optional audit mapping; not a substitute for mismatched bridge evidence |

Fiscal documents `calculated_tev = calculated_market_cap + calculated_net_debt + preferred stock + minority interests`. The direct bridge retains the aggregate `TEV - market cap`; the component bridge remains separately valid and the two are never averaged. The live boundary is limited to the two daily bridge series and current shares endpoint. Official references: [Fiscal ratios](https://docs.fiscal.ai/docs/reference/ratios) and [Fiscal API reference](https://docs.fiscal.ai/docs/api-reference).

The bounded FMP documentation review found field availability for generic EPS and EBIT estimates but no explicit proof that EPS is diluted EPS or that EBIT is economically identical to Fiscal Operating Profit. P/E and EV/EBIT therefore remain `UNVERIFIED`; names alone do not establish equivalence.

The targeted META run on 2026-08-27 passed: 8,040 daily canonical multiple observations, all three supported multiple types, earliest date 2015-12-31, latest date 2026-08-27, and zero annual/quarterly observations. Raw responses and credentials were not persisted or rendered.

Milestone 7D.1 reused these provider boundaries without changing their financial semantics. Canonical META identity required the existing Yahoo identity-only candidate because Fiscal did not normalize `issuer_domicile` or `listing_country`; the audit did not call Yahoo market snapshot. Fiscal and FMP values arrived in compatible USD base units, so no provider-specific scaling fix was needed. P/E and EV/EBIT stayed semantically withheld; only EV/EBITDA executed.

### Milestone 7E cross-listing findings

The Fiscal live boundary no longer fabricates `12-31` when `fiscalYearEnd` is absent. This fixed MSFT by allowing Yahoo's sourced `06-30` fiscal year-end to drive FMP annual-period validation. Fiscal `.L` resolution is venue-aware: it may map a canonical `.L` symbol to a different provider ticker only when exactly one companies-list row has the same base ticker and an explicit London venue (`LSE`, `XLON`, `LON`, or `LONDON STOCK EXCHANGE`). It propagates that actual provider symbol into identity/provenance and refuses a same-base NYSE row. Canonical `.L` identity is unchanged.

Live Fiscal coverage did not expose an eligible London row for SHEL.L or RR.L, and FMP annual estimates were unavailable for the canonical `.L` symbols. Those are structured provider-boundary limitations; neither issuer-name matching nor ticker rewriting was used. Yahoo identity safely established LSE listing metadata: SHEL.L reports in USD and quotes in GBP/GBp; RR.L reports in GBP and quotes in GBP/GBp. No current-price snapshot or FX conversion was invoked.

## Capability states and failures

Each adapter exposes endpoint-level `AVAILABLE`, `LOCKED`, `UNAVAILABLE`, or `ERROR`, with checked-at time and a safe reason. Authentication failure is distinct from entitlement lock, empty coverage, timeout, 429, invalid response, and provider outage. One capability failure must not discard successful capabilities or crash analysis.

Requests use a central positive timeout and bounded retry policy. Configured timeouts, connection failures, HTTP 429, and HTTP 5xx may retry; ordinary 4xx responses never retry. Backoff is exponential, capped, and jittered. Integer and HTTP-date `Retry-After` are supported and safety-capped; missing/invalid values use normal backoff. Sleep, clock, and jitter are injected for deterministic tests.

Generic 401 is an authentication error. Generic 403 is a non-retryable unclassified access failure—not entitlement. A provider adapter must use provider-specific evidence before producing `ENTITLEMENT` and capability `LOCKED`.

At the application identity boundary, controlled request outcomes are translated without changing provider transport semantics into `AVAILABLE`, `NOT_FOUND`, `ACCESS_DENIED`, `AUTHENTICATION_FAILURE`, `RATE_LIMITED`, `PROVIDER_FAILURE`, `EVIDENCE_CONFLICT`, or `UNAVAILABLE`. A correctly handled per-security `ACCESS_DENIED` result is a product coverage limitation rather than an application-portability failure when generic market support is independently proven and no identity/report fallback is produced. Security coverage depends on configured upstream entitlements; provider refusal never becomes an invalid-ticker claim or a permanent production blacklist.

Structured errors retain only provider, safe endpoint identifier, category, optional HTTP status, retryability, safe message, optional retry-after, and attempt count. They never retain raw request headers, raw proprietary bodies, or unsanitized URLs. Query/header names such as API key, authorization, token, access token, secret, and client secret are case-insensitively redacted or omitted from safe identity.

## Cache policy

Milestone 2 defines a cache protocol and in-memory reference implementation; persistence remains deferred. `CacheKey` contains provider, endpoint, method, provider symbol, sorted non-secret parameters, and the single configured V1 schema version. Authentication parameters and headers never enter the key, so secret rotation does not invalidate data caches. Parameter order is normalized.

Metadata contains created/expiry times, schema version, provider, endpoint, safe key, and optional retrieval/as-of times. Lookup explicitly returns `HIT`, `MISS`, or `EXPIRED`; expired value is never exposed through the fresh-value property. Future stale fallback must be an explicit service policy. Adapter cache values may contain normalized/canonical outputs only; raw proprietary response bodies are forbidden. Milestone 3, 4, and 5 adapters intentionally do not write caches.

## Milestone 9A discount-rate evidence

| Evidence | Approved source | Currency/scope | Initial status policy |
|---|---|---|---|
| Risk-free rate | FRED DGS10 | USD valuation cash flows only | Latest observation on/before snapshot; eligible within centralized seven-calendar-day freshness; no interpolation |
| Non-USD risk-free | None approved | GBP, EUR, JPY, and all other non-USD currencies | `UNAVAILABLE`; no DGS10 fallback, FX conversion, spread, or hard-coded sovereign yield |
| Equity risk premium | Explicit configured-external evidence only | Declared currency/market scope | No default; source, method, date, snapshot, decimal unit, and provenance required |
| Beta | Existing Yahoo provider field can be normalized as a ratio | Canonical security/issuer | Provider documentation does not establish benchmark, lookback, frequency, or raw/adjusted semantics; therefore `PROVIDER_DEFINED` and ineligible by default |
| Regression beta | No approved source/method | None | Deferred; no OLS implementation |
| Pre-tax cost of debt | None approved | None | `UNAVAILABLE`; no interest-expense/debt or arbitrary spread proxy |

FRED remains responsible for provider-percent-to-canonical-decimal normalization. The 9A service does not change that adapter behavior. A bounded live META/USD audit verified DGS10 observation 2026-08-27 at decimal 0.0467. It did not request beta or current-price data. Fiscal and FMP documentation inspected during 9A did not supply a sufficiently explicit beta methodology, so no new endpoint or provider adapter was added.

## Milestone 9B.1 sourced cost-of-equity evidence

| Evidence | Boundary | Approved semantics | Failure behavior |
|---|---|---|---|
| US implied ERP | Official Aswath Damodaran / NYU Stern current homepage headline | First dated `Trailing 12 month, with adjusted payout` series only; percent divided by 100; USD/US broad market; full Treasury convention; 62-day freshness | Withhold; never select cash-yield, normalized, net-cash-yield, historical-average, or configured fallback |
| Target return history | Existing injected Yahoo/yfinance source and `YahooAdapter` | `Ticker.history(interval="1d", auto_adjust=True, actions=False, repair=True)` adjusted Close, USD, split/distribution-adjusted | Skip incompatible rows; never substitute price-only data, forward-fill, or current price |
| Benchmark return history | Same Yahoo boundary, SPY | Controlled S&P 500 exposure proxy with identical adjustment/currency semantics | Beta unavailable if compatible aligned history cannot be established |
| Regression beta | Internal provider-independent service | 5Y/60 monthly simple returns, OLS with intercept, minimum 36, observed levered equity beta | Preserve diagnostics; no weekly fallback, provider beta promotion/averaging, adjustment, shrinkage, or cap |

The official monthly ERP workbook URL was inspected but its latest row was July 2022, so it is not the live canonical boundary. The official homepage supplied the current dated headline and exact methodology. On the one bounded 9B.1 run it reported 2026-08-01 and decimal 0.0428. Yahoo history produced 60 aligned META/SPY returns from 2021-09-30 through 2026-08-28 without accessing current price for valuation.

## Milestone 9B.2 WACC evidence sources

| Evidence | Canonical source | Boundary rule | Failure behavior |
|---|---|---|---|
| Equity market value | Fiscal daily `calculated_market_cap` | Latest non-future positive USD base-unit observation within 45 days | Unavailable; never price × shares or Yahoo fill |
| Gross debt proxy | Fiscal canonical standardized `gross_debt` or documented `calculated_total_debt` | Non-negative, explicit total-debt semantics, large US non-financial scope; label `BOOK_VALUE_PROXY` | Missing is not zero; net debt/total liabilities are rejected |
| Interest expense | Fiscal standardized income statement | Normalize only explicit negative-expense or positive-expense-magnitude conventions to canonical `INTEREST_EXPENSE` | Ambiguous sign is withheld |
| Synthetic rating/default spread | Official NYU Stern Ratings, Interest Coverage Ratios and Default Spreads | January 2026 large non-financial columns only; percent to decimal; exact interval lookup | No financial/small-company table, cap, nearest rating, or interpolation |
| Marginal tax | Official NYU Stern Corporate Marginal Tax Rates by Country, underlying PwC | January 2026 corporate marginal rate selected from canonical issuer domicile; 400-day policy | No listing/quote/effective-tax/default substitution |

Fiscal's verified live bridge remains intentionally limited to daily `calculated_tev`, daily `calculated_market_cap`, and current shares. An attempted expansion to unverified daily debt routes was rejected because one unavailable series must not erase verified bridge evidence. Canonical standardized gross debt is the debt source; net debt remains unavailable unless separately verified. The 2026-08-29 targeted META check returned 1,147 standardized actuals and 5,479 bridge observations, with market cap USD 1,472,509,548,175 and TEV USD 1,494,567,548,175 dated 2026-08-28. Gross debt, net debt, and EBIT were unavailable; interest expense existed only for FY2011. The live NYU tax boundary normalized United States of America at 0.2563 from January 2026. These gaps correctly withhold WACC.

## Milestone 9B.3 Fiscal WACC accounting inventory

| Fiscal capability / metric | Canonical treatment | Period/unit policy | Final META result |
|---|---|---|---|
| `GET /v1/company/ratios` | Separate endpoint capability `wacc_accounting_metrics` | One bounded request using stable `companyKey`; annual/quarterly periods; no raw response persistence | Endpoint `AVAILABLE`; 3 normalized observations |
| `calculated_total_debt` | Direct `GROSS_DEBT`; eligible debt weight only as visible `BOOK_VALUE_PROXY` after company-class/currentness gates | Point-in-time, reporting currency/base units; Fiscal's documented interest-bearing debt and lease components | No META observation returned; `UNAVAILABLE` |
| `calculated_net_debt` | Direct `NET_DEBT`; never debt weight | Point-in-time, reporting currency/base units; Total Debt less Total Cash and Cash Equivalents | No META observation returned; `UNAVAILABLE` |
| `ratio_ebit_to_interest_expense` | `INTEREST_COVERAGE` validator only; does not replace underlying annual values | Annual ratio; Fiscal documents Operating Profit / Interest Expense | 3 observations; latest FY2011 value 41.8095 |
| Explicit EBIT | Canonical `EBIT` only | Annual currency flow | No META observation; `UNAVAILABLE` |
| Fiscal Operating Profit | Canonical `OPERATING_INCOME`; eligible only through the Damodaran coverage-equivalence contract | Annual currency flow, same period/currency as explicit interest expense | Method approved, but META coverage remained unavailable because canonical interest expense conflicted |
| Preferred equity / NCI | No mapping found in the bounded approved source | Must remain separate same-date/currency evidence, or use only the existing aligned aggregate residual | `UNAVAILABLE` separately |

The endpoint and definitions are from the official [Fiscal ratios reference](https://docs.fiscal.ai/docs/reference/ratios) and [Fiscal API reference](https://docs.fiscal.ai/docs/api-reference). The method-specific coverage interpretation follows NYU Stern's [ratings data](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html), [capital-structure notes](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/capstr.html), and [definitions](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/definitions.html). No FMP historical role, Yahoo accounting fallback, or major SEC statement construction was added.

## Version 1.0 release configuration boundary

The default UI can load without provider credentials and performs no request before Analyse. `FISCAL_API_KEY` is required to establish live canonical Fiscal identity after submission. `FMP_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, and `ALPHAVANTAGE_API_KEY` remain capability-specific and optional at startup. Missing configuration, authentication failure, entitlement denial, empty coverage, rate limiting, and provider failure retain their existing controlled outcomes; none authorizes a legacy, alternate-security, alternate-provider, or fabricated-value fallback.

`.env.example` contains only these names plus the non-secret `USE_LEGACY_UI=false` placeholder. Actual `.env`, Streamlit secrets, provider/raw caches, logs, traces, and browser acceptance artifacts are ignored. Version 1.0 adds no provider and changes no endpoint or normalization rule.
