# V1 Test Matrix

All unit/regression tests use synthetic data. Integration tests use mocked transport. Live smoke tests are opt-in, secret-safe, and never part of the default suite.

| Required case | Planned test / assertion | Layer |
|---|---|---|
| GBp to GBP; quote units | `test_gbp_pence_to_pounds`; prices scale 0.01 while share counts/fundamentals do not | domain/provider |
| ADR handling | `test_adr_ratio_and_currency_bridge`; explicit ADR ratio, no one-for-one assumption | domain |
| Annual vs LTM; annual vs quarterly | reject or keep separate when frequency/period differs | domain/reconciliation |
| Actual vs estimate | comparison/aggregation requires explicit type and never overwrites actual | domain |
| Next-four-quarter NTM | exactly four forward non-overlapping fiscal quarters summed; gaps withhold NTM | consensus |
| Low/average/high | ordered comparable cases accepted; inversion emits blocking issue | validation |
| Analyst counts; dispersion | preserved per period/metric; low coverage/wide range lowers confidence | consensus/confidence |
| EV-to-equity bridge; net debt sign | claims deducted; net cash (negative net debt) increases equity value | valuation |
| Implied diluted shares | net income/EPS calculation, invalid/negative/zero denominator handling, cross-check only | consensus |
| Negative earnings/EBITDA | P/E excludes non-positive earnings; invalid EBITDA methods withheld | history/peers |
| Historical multiple outliers | documented robust filter, sample count and percentiles stable | history |
| Minimum peers; selection | fewer than 3 credible peers unavailable; exclusions explainable | peers |
| Bank/insurer/REIT families | correct family selected and ordinary FCFF disabled | registry |
| Source disagreement/conflict | configured bands; canonical unchanged; confidence downgraded/issues emitted | reconciliation |
| Missing entitlement | endpoint `LOCKED`, other capabilities preserved, no crash | provider |
| Timeout/429/outage | bounded retry/backoff policy; isolation; structured status | provider/cache |
| Unknown FCF definition | cash-flow DCF unavailable with explicit reason | valuation |
| Missing DCF inputs | structured unavailable, no default substitution | valuation |
| Currency-inappropriate risk-free | non-USD DCF blocked absent matching rate or explicit override | macro/valuation |
| One method only | aggregation `UNRESOLVED`, central absent | aggregation |
| Excessive dispersion | `WIDE`/`UNRESOLVED`, independent values visible, central absent | aggregation |
| FMP DCF excluded | reference tagged and cannot enter eligible method collection | aggregation |
| Analyst targets excluded | all target cases reference-only and zero aggregation weight | aggregation |
| Ollama excluded | numeric domain/valuation packages do not import/call interpretation; payload cannot mutate inputs | architecture |

## Milestone 1 implemented coverage

`test_v1_domain_contracts.py` uses only synthetic observations and covers USD identity, GBp quote scaling, security/issuer/ADR identity, annual and quarterly actuals, annual estimate cases, ordered and inverted estimate ranges, LTM/annual separation, actual/estimate separation, historical rows from an estimate endpoint, finite/currency/period/as-of validation, deterministic observation IDs, derived provenance, distinct EBIT/operating-income and cash-flow concepts, unavailable valuation results, and structural exclusion of external DCF/target references.

`test_v1_architecture.py` scans production AST conditionals for literal ticker branches and scans the new domain plus numeric valuation modules for Ollama interpretation imports. The exact pre-existing `BRK.A`/`BRK.B` normalization exception is characterized because Milestone 1 may not change legacy behavior; any additional literal-ticker branch fails the suite.

## Milestone 2 implemented coverage

`test_v1_provider_infrastructure.py` adds 35 collected cases covering independent AVAILABLE/LOCKED/UNAVAILABLE/ERROR capabilities, authentication versus entitlement, timeout policy, connection/500/503/429 retry behavior, permanent 400/401/403/404 behavior, integer and HTTP-date `Retry-After`, deterministic bounded backoff/jitter with no sleeping, query/header/free-text redaction, safe error bodies and representations, deterministic credential-independent cache keys, symbol/endpoint/schema separation, HIT/MISS/EXPIRED behavior, metadata safety, and scoped delete/clear operations.

Three additional architecture cases in `test_v1_architecture.py` protect domain-to-provider dependency direction, keep V1 provider DTOs out of legacy UI/valuation, and prohibit a live network client in the Milestone 2 provider package. All transport outcomes are synthetic.

## Milestone 3 implemented coverage

`test_v1_identity_resolution.py` covers deterministic field precedence and conflicts, canonical/provider-symbol separation, missing provider candidates, USD and GBp identities, quote-only scaling, traded/issuer/underlying ADR separation, and an explicitly partial unknown ADR ratio.

`test_v1_actual_adapters.py` uses injected synthetic sources only. Yahoo cases cover identity metadata, GBp price normalization, unscaled market cap/shares, beta/volume, missing fields, provenance, and endpoint failure isolation. Fiscal cases cover annual/quarterly standardized actuals, source metrics, CapEx sign normalization, provider-defined FCF, operating-income/EBIT separation, unsupported/malformed rows, and secret-safe authentication failures. SEC cases cover ticker-to-CIK separation, annual/quarterly/instant facts, filing versus period dates, taxonomy/tag provenance, unsupported tags, and isolation. A cross-provider case proves Fiscal and SEC actuals coexist with distinct IDs and provenance.

Architecture coverage additionally keeps provider field names inside adapters, prohibits adapter cache writes in this milestone, keeps V1 imports out of legacy app/analysis/scoring/valuation, and keeps Ollama and live clients outside normalization.

## Milestone 4 implemented coverage

`test_v1_fmp_consensus.py` uses an injected synthetic transport and adds coverage for controlled FMP capabilities, independent AVAILABLE/LOCKED/UNAVAILABLE/ERROR outcomes, credential-safe requests/cache identities, bounded annual mapping, generic net-income/EPS semantics, metric-specific analyst counts, annual period construction, mandatory reporting/quote currencies, unscaled statement consensus, partial rows, and blocking range inversions.

The same suite verifies historical estimate rows remain estimates but stay outside forward horizons, unfinished-current-year FY1 semantics, FY1/NTM separation, deterministic year ordering, partial metric survival, average-only adjacent revenue growth, same-period EBIT/EBITDA margins, derived input provenance, range-not-scenario semantics, and canonical-only service inputs.

Price target low/median/consensus/high and standard DCF tests enforce `ExternalValuationReference`, structural exclusion from `ValuationResult`, quote-currency requirements, ignored FMP stock-price output, and endpoint isolation. Architecture tests keep FMP fields out of services, V1 imports out of legacy/valuation, live clients out of providers, and raw payloads out of caches. All fixtures are fictitious and synthetic.

## Milestone 5 implemented coverage

`test_v1_finnhub.py` uses an injected synthetic transport to cover all eight independent annual capabilities, revenue/EBIT/EBITDA/generic-income/generic-EPS/OCF mapping, metric-specific counts, explicit CapEx sign conversion, range validation, historical estimate semantics, partial rows, locked-capability isolation, safe request/cache identity, generic FCF non-promotion, and verified cash-flow evidence.

`test_v1_alpha_vantage.py` covers annual and quarterly revenue/generic-EPS estimates, period separation, sourced counts, partial cases, separate 30-day revision records, missing revision fields, historical estimate semantics, invalid ranges, shared-endpoint failure status, naive snapshot rejection, and API-key-safe identities.

`test_v1_fred_macro.py` covers DGS10 mapping, percent-to-decimal conversion, missing-value skipping, observation/retrieval/vintage separation, latest-on-or-before selection, future exclusion, USD resolution, structured GBP/EUR/JPY unavailability, no USD fallback, provider failure, and API-key-safe identities. `test_v1_m5_domain_contracts.py` directly enforces cash-flow evidence, revision, and macro invariants. Architecture tests prevent provider-field leakage, cache writes, live clients, V1-to-legacy/UI wiring, and new ticker branches.

All Milestone 5 tests are synthetic. No optional live smoke entry point was added.

## Milestone 6A implemented coverage

`test_v1_reconciliation.py` adds synthetic coverage for stable security/issuer matching with different provider symbols; every required metric/type/frequency/period/case/currency/unit mismatch; FCF semantic separation; aligned OCF/CapEx; FMP-to-Finnhub/Alpha annual comparisons; generic EPS; snapshot exclusion and latest eligible selection; retrieval-time independence; explicit staleness configuration; all exact threshold boundaries; zero-base behavior; differences; unscored metrics; warning/conflict issues; missing/locked/error validators; independent multiple-validator records; canonical immutability; Fiscal-standardized versus SEC-reported audit context; and raw-field/secret exclusion.

Architecture tests ensure reconciliation imports canonical domain contracts only, has no provider DTO, coverage, valuation, or UI dependencies, remains absent from legacy execution, introduces no ticker branch, and performs no network calls. The complete default suite passes 283 tests: the approved 229-test baseline plus 54 Milestone 6A tests.

## Milestone 6B implemented coverage

`test_v1_coverage.py` adds synthetic coverage for immutable audit contracts; 5+/3–4/1–2/zero annual-history rules; annual continuity and quarterly exclusion; 3+/2/1/zero forward rules; historical estimate exclusion; revenue-plus-profitability completeness; revenue-only partial state; exact analyst bands and unknown counts; preserved horizon decay; confirmed/warning/conflict/no-validator/locked agreement states; distant versus near-term conflict severity; provider-defined FCF, OCF/CapEx, verified FCFF, and verified FCFE; USD and non-USD macro behavior; own-history/peer/cash-flow/reverse readiness; all four overall levels; critical price/forward/identity/blocking issues; explicit snapshots and future exclusion; input immutability; custom policy behavior; and absence of monetary outputs or investment language.

Architecture assertions keep coverage free of raw provider DTOs, live clients, valuation formulas, UI imports, wall-clock dates, ticker branches, and legacy wiring. The 46-test Milestone 6B suite is fully synthetic and performs no network calls. The complete default suite passes 329 tests: the approved 283-test baseline plus 46 Milestone 6B tests.

## Milestone 6C implemented coverage

`test_v1_live_smoke.py` covers guarded import/no automatic execution, safe `.env` loading and credential presence, exact environment-variable secret resolution, missing credentials, suppressed arbitrary exception/payload text, nonfatal locked/unavailable states, strict failure exit status, provider selection and isolation, allowlisted JSON metadata, symbol-input parity, structural text output, no repository writes, and absence from application imports.

Provider regressions remain synthetic. `test_v1_finnhub.py` covers documented metric-specific endpoints, nested `data`, live range/count field names, and explicit no-I/O unavailability for undocumented cash-flow estimate paths. `test_v1_alpha_vantage.py` covers the verified unified snake-case `estimates` shape, fiscal horizon separation, canonical quarterly-period derivation, source-field provenance, analyst counts, EPS 30-day revisions, and absence of unsupported revenue revision records. Fiscal regression coverage preserves explicit provider source metrics and both validated CapEx sign conventions.

The complete default suite passes 349 tests: the approved 329-test baseline plus 20 Milestone 6C tests. It performs zero live calls; live verification is available only through the explicit script.

## Milestone 6D implemented coverage

`test_v1_live_smoke.py` adds a synthetic request-capture regression proving the Fiscal live boundary sends the proven `X-Api-Key`, JSON accept header, StockAnalyser user agent, and only documented company-list parameters. It also proves malformed FRED key configuration fails safely before a transport is constructed.

Existing provider tests now assert the complete approved authentication/request shapes for FMP annual estimates, Finnhub documented revenue estimates, and FRED DGS10, while preserving secret-free safe request identities and credential-independent cache keys. The complete default suite passes 351 tests: the 349-test Milestone 6C baseline plus 2 Milestone 6D regressions. It performs zero live calls.

## Milestone 6E implemented coverage

`test_v1_live_smoke.py` adds prepared-request coverage proving that the real resolved Fiscal secret reaches `X-Api-Key`, never becomes the environment-variable name, and retains the exact working `StockAnalyser/0.7.1` user agent. Additional regressions prove profile and all three standardized-statement requests use `companyKey` rather than `company`, while the stable Fiscal identifier remains issuer metadata. A fictitious synthetic fixture characterizes the verified live `reportDate`/`metricsValues` response shape and its bounded revenue normalization.

`test_v1_actual_adapters.py` adds explicit provenance coverage for source-as-of absence and retrieval-time substitution. The complete default suite passes 355 tests: the approved 351-test Milestone 6D baseline plus 4 Milestone 6E regressions. It performs zero live calls.

## Milestone 7A implemented coverage

`test_v1_historical_valuation.py` characterizes the dedicated immutable input record and Fiscal adapter boundary. It verifies controlled P/E, EV/EBITDA, and EV/EBIT types; explicit equity/enterprise basis; diluted-EPS, EBITDA, and provider-specific Operating Profit-as-EBIT denominator semantics; and prevents generic operating-income promotion. It also verifies canonical security/issuer identity, source-only provider symbols, deterministic immutable IDs, dimensionless ratio units, distinct observation/period/as-of/retrieval dates, currency context without FX, and exact annual/quarterly/daily sampling preservation.

Eligibility cases cover positive supported observations, explicit negative/zero ineligibility, non-finite rejection, future-date exclusion at an aware `analysis_as_of`, missing source metric, unknown economic definition, and preservation of an extremely high finite multiple without winsorization. P/FCF and EV/FCF fixtures prove generic FCF naming cannot establish FCFE/FCFF and produces a structured withholding issue. Output cardinality proves no interpolation.

The same file uses a fictitious bare-list Fiscal response to prove the exact `StockAnalyser/0.7.1` user agent, `companyKey`, three bounded daily endpoints, normalized adapter DTO, and no secret leakage. It verifies the separate safe smoke block. Two `test_v1_architecture.py` cases prohibit forward-consensus, distribution, FMP, UI, coverage, and legacy wiring from entering the 7A input layer. Existing static guards continue to prohibit ticker branches, provider DTO leakage, provider-side live clients, raw persistence, Streamlit imports, and default-test network calls.

The complete normal suite passes **381 tests**: the approved 355-test Milestone 6E baseline plus 26 Milestone 7A cases. It performs zero live calls.

## Milestone 7B implemented coverage

`test_v1_historical_distributions.py` covers inclusive calendar-based 3Y/5Y/10Y boundaries, exact start/end inclusion, 5Y policy default, independent window results, no blending, no short-window fallback, and explicit future-date exclusion. It verifies that only positive finite `ELIGIBLE` canonical observations enter numeric statistics while negative, zero, and `UNVERIFIED` observations remain visible in candidate/ineligible diagnostics.

Statistical cases prove Type-7 linear p25/median/p75 interpolation, odd/even medians, minimum, maximum, mean, population standard deviation, IQR, deterministic ordering, and preservation of an extreme finite value. Cardinality and audit flags prove there is no winsorization, clipping, trimming, IQR/sigma deletion, interpolation, missing-day manufacture, or monthly replacement. Daily sampling remains `DAILY`; calendar months are diagnostic and independent fundamental regime count remains unknown.

Policy cases prove centralized 500/750/1,250 daily minimum counts and 80% span requirements, count and span independence, usable samples for every supported window, short-history partial/insufficient outcomes, and no automatic 5Y-to-3Y fallback. Duplicate tests count identical canonical provider/date evidence once and fail conflicting values closed without averaging. Provider-isolation and provider-symbol tests prove another provider cannot increase the sample and symbol spelling does not change canonical distribution identity. Basis, denominator, unit, immutability, policy, explicit-aware-as-of, and absence of valuation-output fields are also enforced.

Two additional `test_v1_architecture.py` cases prove the service imports no provider DTO, network client, FMP/consensus, valuation, UI, or Streamlit dependency and is not wired into coverage or legacy execution. Existing ticker and default-network guards remain active. No live call was required because 7B consumes the live-verified 7A canonical contract.

The complete normal suite passes **407 tests**: the approved 381-test Milestone 7A baseline plus 26 Milestone 7B cases. It performs zero live calls.

## Milestone 7C implemented coverage

`test_v1_own_history_inputs.py` covers controlled denominator compatibility; generic EPS versus diluted EPS; Operating Profit versus generic EBIT; the prohibited operating-income shortcut; explicit verification/incompatibility evidence; exact EBITDA alignment; missing denominators; explicit FY1/FY2 and 3Y/5Y/10Y selections; 5Y/FY1/average defaults; no window blending, NTM relabeling, or low/high scenario conversion; and rejection of partial historical distributions.

Capital-structure cases prove latest-at-or-before selection, future exclusion, missing cash/debt without zero fill, deterministic net debt with both input provenances, currency mismatch without FX, visible source-date gaps, no default staleness threshold, explicit maximum-gap downgrade, and distinct shares-outstanding/basic/diluted bases. Readiness cases cover P/E without an enterprise bridge, EV/EBITDA and EV/EBIT bridge requirements, missing debt/shares, currency, ADR, provider-symbol/canonical-identity checks, structured statuses and reasons, immutability, provenance, and absence of valuation outputs.

Two architecture cases prove the 7C service consumes canonical contracts only, imports no provider/network/UI/legacy valuation dependency, contains no multiplication operation, and is not wired into legacy execution. The complete normal suite passes **439 tests**: the approved 407-test Milestone 7B baseline plus 32 Milestone 7C cases. Normal pytest performs zero live calls.

## Milestone 7C.1 implemented coverage

Milestone 7C.1 adds synthetic coverage for required Fiscal TEV/market-cap inputs, exact subtraction, same-provider and canonical-symbol identity isolation, currency and future-date rejection, same-date preference, visible/configurable gaps, non-positive TEV failure, full provenance, bounded provider-field leakage, optional documented debt/net-debt mappings, share/ADR semantics, component/direct separation without averaging, and EV/EBITDA readiness without zero-filled debt. It also proves unsuitable shares block, P/E and EV/EBIT remain unverified, the service contains no multiple application, and both direct/readiness contracts contain no fair-value/target/upside fields.

Live request tests remain synthetic and verify the exact Fiscal paths/parameters plus bounded `{date, ratio}` and shares DTO normalization. Default pytest makes zero network calls. The final complete normal suite passed **458 tests in 2.72s**, preserving the 439-test baseline and adding 19 Milestone 7C.1 cases.

## Milestone 9B.3 WACC evidence-gap coverage

`test_v1_wacc_evidence_gap.py` adds 25 synthetic cases. They cover direct period-based `calculated_total_debt` and `calculated_net_debt` normalization, exact documented definitions, point-in-time/date/currency semantics, independent provider coverage-ratio normalization, future/unknown row rejection, endpoint failure isolation, the single documented request contract, safe timestamp substitution at the audit cutoff, and no credential/payload leakage.

The same file proves Operating Income is never globally accepted as EBIT; only Fiscal standardized Operating Profit plus explicit method-equivalence evidence may supply the Damodaran coverage numerator. Explicit EBIT wins within a shared period, same-period annual interest expense remains mandatory, non-Fiscal/provider-boundary lookalikes fail, exact duplicates deduplicate, conflicting values fail closed independent of response order, and the direct provider ratio cannot replace missing underlying values. Company-class tests cover US/non-US domicile, operating-company type, non-financial/financial classification, complete industry evidence, the sourced USD 5 billion boundary, generic identity propagation, no ticker behavior, no preferred/NCI lookalikes, no FMP/Yahoo accounting expansion, no FX, and no DCF/peer/own-history/aggregation/stance/UI additions.

The final normal suite passed **1,060 tests in 5.12s**, preserving all 1,035 approved baseline tests and adding 25 Milestone 9B.3 tests. Normal pytest is fully synthetic and made zero network calls.

## Milestone 7D implemented coverage

`test_v1_own_history_valuation.py` adds 44 synthetic cases covering `NOT_READY`/`PARTIAL` withholding, independently executable `READY` methods, exact 3Y/5Y/10Y and FY1/FY2 preservation, FY-not-NTM behavior, `AVERAGE`-only denominator use, P25/median/P75 roles, synthetic verified P/E, direct and component EV bridges, EV/EBITDA and verified EV/EBIT mechanics, exact enterprise/equity/per-share traces, single-bridge enforcement, missing/wrong bridge and share evidence, ADS-converted ADR shares, currency/unit/no-FX rules, and exact distribution/forward identity checks.

Failure cases cover zero/negative/non-finite denominators and shares, non-finite or misordered statistics without repair, non-positive equity without flooring, partial lower/central behavior, full unavailability, range ordering, unrounded precision, stable supporting IDs, generic `ValuationResult` compatibility, provenance, and immutability. Two architecture cases prove the numeric service imports only canonical contracts, contains no provider-scale constants or external-reference/current-price/aggregation shortcuts, and remains unwired from coverage, Streamlit, and legacy execution.

The complete normal suite passes **504 tests in 3.41s**: the approved 458-test Milestone 7C.1 baseline plus 46 Milestone 7D cases. Normal pytest performs zero live network calls.

## Milestone 7D.1 implemented coverage

`test_v1_own_history_orchestration.py` adds 25 synthetic cases. They cover default 5Y/FY1/`AVERAGE`, explicit 3Y/5Y/10Y and FY1/FY2 preservation, one READY method without a two-method requirement, READY-only numeric-engine calls, exact distribution/forward/bridge/share ID flow, canonical unit/currency/base-scale gates, independent EV/adjustment/equity/per-share reconstruction, future historical/FMP/bridge exclusion, mismatched bridge-date blocking, invalid distribution/denominator failure, PARTIAL withholding, structured semantic reasons, immutable/safe output, no market price/external references/aggregation/stance/UI/ticker branch/provider scale constants, and zero network access on the normal pytest path.

The complete normal suite passes **529 tests in 6.59s**: the approved 504-test Milestone 7D baseline plus 25 Milestone 7D.1 cases. The live audit is a separate explicit CLI command and is never invoked by pytest.

## Milestone 7E implemented coverage

`test_v1_own_history_portability.py` adds 15 synthetic cases. The three live-discovered defects have explicit regressions: absent Fiscal fiscal-year-end is not fabricated and Yahoo's sourced field can win; a `.L` canonical symbol resolves only to an explicitly London-listed Fiscal row and never a same-base US listing; and earliest-stage historical/forward failures are not mislabeled as downstream currency/share failures.

Additional cases prove the same orchestration executes independent USD and GBP securities; canonical security/issuer joins survive different Yahoo/Fiscal/FMP symbols; `.L` remains canonical; GBP financial values and GBP/share results remain distinct from GBp quote units without 100x conversion; USD-reporting/UK-listing output remains USD without FX; unresolved ADR conversion blocks; a 250x synthetic high observation remains unaltered; energy does not change the default 5Y window; one unavailable security does not invalidate another; and the path contains no FRED/risk-free/current-price/ranking/aggregation/Streamlit dependency. Existing suites continue to cover no ticker branches, no provider outlier treatment, explicit windows, safe failure statuses, and zero-network default pytest.

The complete normal suite passes **544 tests in 2.89s**: the approved 529-test Milestone 7D.1 baseline plus 15 Milestone 7E cases. Live calls remain explicit CLI-only.

## Milestone 8A implemented coverage

`test_v1_peer_selection.py` adds 44 network-free cases covering canonical self removal; alternate-listing and cross-provider issuer deduplication; provider-symbol/rank/agreement provenance; ETF/fund/preferred/warrant/bond exclusion; same/sector-only/mismatched/unknown industry; explicit and absent business-model evidence; same-currency scale; no-FX cross-currency scale; cross-currency dimensionless growth/margin; missing target/candidate metrics; forward-EBITDA availability; no weighted score; three-issuer minimum; empty/error discovery; analysis-as-of filtering; ADR uncertainty; controlled scale/growth/margin failures; no valuation fields; no own-history/DCF/aggregation/current-price/UI/ticker branch; Fiscal v3 peer schema normalization; stable-ID requirements; source-as-of substitution; endpoint failure isolation; and the live-runner short circuit that prevents Yahoo/raw 404 output after Fiscal identity failure.

The complete normal suite passes **588 tests in 2.58s**: the approved 544-test Milestone 7E baseline plus 44 Milestone 8A cases. Default pytest makes zero network calls; live META discovery remains explicit CLI-only.

## Milestone 8A.1 implemented coverage

`test_v1_peer_identity_actual_readiness.py` adds 49 network-free scenarios covering stable Fiscal summary issuer/security seeds; summary versus profile verification; missing profile enrichment; missing stable IDs/required listing evidence; name/ticker/reasoning rejection; security-class characterization subsequently refined by 8A.2; summary classification; summary inclusion; duplicate issuer listings; exact/equal-value deduplication; explicit latest revision; unordered conflicts; no averaging/first/last/retrieval ordering; source-metric and Fiscal/SEC isolation; deterministic scale/growth; no EBITDA fabrication; economic inclusion independent of forward EBITDA; separate EV/EBITDA readiness; same/no-FX currency rules; unchanged three-peer minimum; fail-closed FMP symbol identity; and no valuation/price/DCF/aggregation/stance/UI/network behavior. `test_v1_peer_selection.py` was updated to characterize summary survival after profile failure and the removal of forward EBITDA from economic status.

The final complete normal suite passes **637 tests in 3.54s**: the approved 588-test Milestone 8A baseline plus 49 Milestone 8A.1 scenarios. Default pytest makes zero network calls; all live checks remain explicit CLI-only.

## Milestone 8A.2 implemented coverage

`test_v1_peer_security_eligibility.py` and updated 8A/8A.1 peer tests add 29 network-free cases covering Fiscal summary identity without security type or listing country; explicit identity-versus-security eligibility; missing-type `UNVERIFIED` economic behavior; no operating-company/name/relationship inference; common, other-equity, and non-equity classification; exact and controlled Nasdaq exchange compatibility; incompatible external listing rejection; identity-bound enrichment; optional profile failure; stable-ID conflict rejection; safe suppression of third-party console payloads; duplicate issuer handling; independent EV/EBITDA readiness; unchanged three-issuer minimum; and absence of valuation, current-price, DCF, aggregation, stance, UI, ticker branches, or network behavior. Existing actual-selection tests continue proving exact deduplication, explicit revision chronology, unordered-conflict withholding, and no averaging/provider replacement.

The final complete normal suite passes **666 tests in 3.26s**: the approved 637-test Milestone 8A.1 baseline plus 29 Milestone 8A.2 cases. Default pytest makes zero network calls; live Fiscal/META verification remains explicit CLI-only.

## Existing baseline coverage to retain

`test_currency.py` covers GBp scaling. `test_valuation.py`, `test_v03_integrity.py`, `test_v04_underwriting.py`, and `test_v06_earnings_intelligence.py` characterize the legacy simplified DCF. `test_v09_adaptive_valuation.py`, `test_v092_terminal_value.py`, `test_v093_forecast_validation.py`, and `test_v094_cross_sector.py` characterize explicit FCFF, WACC, framework selection, Fiscal snapshots, bridges, reverse solving, peers, terminal economics, dispersion withholding, and cross-sector fixes. `test_v07_fiscal.py` covers Fiscal client isolation and synthetic responses; `test_v08_local_interpretation.py` covers bounded Ollama grounding; `test_v061_sec_form.py` and `test_v06_earnings_intelligence.py` cover SEC extraction.

These tests are characterization assets, not proof that legacy forecast methodology is acceptable. Preserve them until replacements prove intentional parity or documented behavior change.

## Cross-sector regression universe

Use synthetic archetypes plus optional live smoke symbols for META, MSFT, NVDA, GOOGL, AMZN, JPM, an insurer, a REIT, XOM, SHEL.L, RR.L, TSM, KO, healthcare, and loss-making growth. Assertions target units, periods, method eligibility, withholding, provenance, and resilience—not closeness to market price. Add a static test forbidding ticker comparisons in production code.

### Milestone 8A.3 peer financial resolution

`test_v1_peer_financial_resolution.py` adds 17 synthetic cases for exact stable-issuer-to-company-key resolution, summary-record lookup metadata, prohibited ticker/name matching, duplicate/mismatched/missing/conflicting evidence, conflicting key ownership, material listing conflicts, summary-only and full-profile normalization parity, identity preservation through lookup failure, identity-bound fetch rejection, missing-EBITDA non-fabrication, and the company-key-only live-source request shape. The live-source regression proves normalized financial-row currency remains usable when the compact company record omits reporting currency, without an implicit currency default.

Existing actual-selection and peer-selection suites continue to cover annual-only scale/growth/margin evidence, same-currency scale, cross-currency withholding, revision chronology, retrieval-order independence, no averaging, incompatible source metrics, security and industry gates, forward-readiness separation, duplicate issuers, and the three-independent-peer minimum. Architecture guards continue to prohibit network calls in normal pytest, peer valuation, current price, own-history distribution consumption, DCF, aggregation, stance, Streamlit wiring, and ticker-specific production branches. Final result: 683 passing synthetic tests; default pytest performed zero live network calls.

### Milestone 8A.4 provider-boundary diagnosis

Six additional regressions cover issuer-level statement binding without security/provider-symbol equality, safe HTTP 403 endpoint isolation with identity preservation, exact target-versus-candidate request parity and distinct company keys, verified profile-currency fallback, missing-all-currency withholding with no USD default, and the post-retrieval live-audit cutoff used for substituted source times. Together with existing 8A.3 cases they prove the resolved candidate key reaches the final request, issuer ID/ticker/literal field name/target key are not substituted, target and peer share one parser, explicit row currency is accepted, identity conflicts still fail closed, and missing EBITDA remains missing.

Final targeted result: 69 passing provider/peer tests. Final complete result: 689 passing synthetic tests from the approved 683 baseline. Default pytest made zero live network calls; existing architecture guards for ticker branches, GOOG/GOOGL rewrites, current price, peer multiples, fair value, DCF, aggregation, stance, and UI wiring all passed.

### Milestone 8B peer EV/EBITDA inputs

`test_v1_peer_valuation_inputs.py` adds 39 network-free cases covering the immutable `INCLUDED` gate; rejection of `UNVERIFIED`, `EXCLUDED`, and `UNAVAILABLE`; peer issuer/security and selection-reference preservation; provider-symbol provenance; same-peer TEV/EBITDA arithmetic; target-input rejection; latest eligible TEV, future and centralized staleness handling; estimate snapshot cut-off; peer-specific FY1/FY2 and non-NTM semantics; `AVERAGE`-only selection; currency/unit/base-scale rules; non-positive/non-finite input rejection; high-multiple retention; stable CIK/ISIN/CUSIP identity requirements; ticker-only rejection; duplicate issuer control; three-observation usability; two-observation insufficiency; method-subset partiality; non-rescue by unverified candidates; zero-peer safe audit output; deferred-scope guards; and no network/provider-scaling implementation.

Final targeted result: **39 passed in 0.30s**. Final complete result: **728 passed in 3.64s**, the approved 689-test baseline plus 39 Milestone 8B cases. Normal pytest performed zero live network calls.

### Milestone 8C peer EV/EBITDA distribution

`test_v1_peer_valuation_distribution.py` adds 31 network-free cases covering a usable three-issuer distribution; shared explicit Type-7 P25/median/P75; minimum, maximum, descriptive mean, population standard deviation, IQR, and transparent ratios; stable IDs; observation and issuer preservation; duplicate-issuer rejection; mixed target, peer-set, snapshot, method, basis, FY policy, and estimate-case rejection; different peer fiscal ends under one FY policy; dimensionless cross-currency coexistence without FX or absolute-value comparisons; retention of high finite ratios; no clipping, winsorization, IQR/sigma filtering, arbitrary caps, or membership mutation; fail-closed non-positive/non-finite inputs; insufficient, partial, and unavailable states; deferred target/current-price/own-history/DCF/aggregation/stance/UI guards; safe zero-distribution rendering; and zero network access. Existing 8B cases also verify the new peer-set binding invariant.

Final targeted 8B+8C result: **70 passed in 0.36s**. Final complete result: **759 passed in 3.19s**, the approved 728-test baseline plus 31 Milestone 8C cases. Normal pytest performed zero live network calls.

### Milestone 8D target peer EV/EBITDA application

`test_v1_peer_target_valuation.py` adds 52 network-free cases covering a synthetic 8B-to-8C-to-8D path; usable/partial/insufficient/unavailable distributions; target/distribution/set/subset/snapshot identity; enterprise-basis EV/EBITDA only; P25/median/P75 application and non-use of mean/minimum/maximum; explicit FY1/FY2 alignment and non-NTM semantics; `AVERAGE`-only target EBITDA; canonical EBITDA/unit/positivity; direct and component bridge reuse without averaging; exact TEV-minus-market-cap adjustment; currency/no-FX and GBP/share preservation; bridge/share identity, basis, completeness, positivity, and ADR semantics; exact enterprise/equity/per-share arithmetic; non-positive equity trace and partial/central behavior; stable supporting IDs and provenance; immutable contracts; deferred-scope architecture guards; zero network access; and safe META unavailable rendering.

Final targeted 8D result: **52 passed in 0.29s**. Final targeted 7D+8C+8D compatibility result: **127 passed in 0.44s**. Final complete result: **811 passed in 4.49s**, the approved 759-test baseline plus 52 Milestone 8D cases. Normal pytest performed zero live network calls.

### Milestone 8E peer-family orchestration and portability

`test_v1_peer_family_orchestration.py` adds 32 network-free cases covering the complete 8A→8D path; non-usable and expected-unavailable early stops; `INCLUDED`-only evidence; independent target runs; earliest identity, discovery, security, economic, stable-provider-identity, enterprise-value, forward-denominator, peer-set/subset/distribution, target-denominator, bridge, share, and application stages; unchanged layer IDs and snapshot-specific orchestration IDs; ticker-only binding rejection; provider-symbol provenance; `.L` preservation; peer-specific fiscal calendars; FY1-not-NTM behavior; high finite multiple retention; no outlier removal; USD/GBP/GBp separation; no FX or 100x conversion; exact bridge selection; safe output; forbidden ticker/sector/macro/current-price/own-history/aggregation/stance/UI dependencies; and zero network access.

Final targeted 8E result: **32 passed in 0.27s**. Final complete result: **843 passed in 3.22s**, the approved 811-test baseline plus 32 Milestone 8E cases. Normal pytest performed zero live network calls; the five live portability audits were separate explicit CLI invocations.

## Test organization

- `tests/unit/domain`: models and invariants.
- `tests/unit/providers`: mapping/capabilities with synthetic fixtures.
- `tests/unit/services`: consensus, reconciliation, macro, cache.
- `tests/unit/valuation`: each family and aggregation.
- `tests/regression`: every discovered bug and cross-sector archetype.
- `tests/integration`: mocked multi-provider pipelines and Streamlit view models.
- `tests/live_smoke`: explicit opt-in capability checks; no assertions on proprietary values and no secret output.

## Milestone 11B current-market-price evidence and comparison

`tests/test_v1_market_comparison.py` and `tests/test_v1_live_market_comparison_audit.py` add 71 network-free cases covering immutable contracts and stable IDs; exact target security/issuer and snapshot binding; Yahoo-only canonical observation acceptance; provider-field adapter isolation; future and three-calendar-day freshness boundaries; Friday-to-Monday eligibility; finite positive price rules; raw/normalized reconstruction; quote currency versus quote unit; USD scale 1 and GBp/GBX-to-GBP scale 0.01; missing/inconsistent metadata; no ticker rule or FX; exact resolved overall envelope/central comparison; overall withholding for `WIDE`, `UNRESOLVED`, and `UNAVAILABLE`; valid family comparison under nonresolved publication; partial-family retention without arithmetic; exact `V-P` and `V/P-1` with no rounding or judgment; reverse-DCF expectation-only evidence; reference-only external evidence; nested identity/snapshot/evidence validation; no feedback loop; safe CLI output; Streamlit isolation; and zero normal-network calls.

Final targeted result: **71 passed in 0.68s**. Final complete normal result: **1,351 passed** (the approved 1,280-test baseline plus 71 Milestone 11B cases). Normal pytest performed zero live network calls. The META audit was one separate explicit CLI invocation.

## Milestone 11C provider-independent research report

`tests/test_v1_research_report.py` adds 89 synthetic cases covering immutable contracts and stable report IDs; canonical target and aware snapshot retention; display-symbol/non-identity behavior including `.L`; reporting/quote currency and quote-unit separation; exact market-evidence copying and null-without-fallback behavior; controlled publication labels; resolved-only overall central and overall comparison; separate family envelope/common overlap; mandatory own-history and peer rows; unavailable-family visibility; exact 11B family/overall gap copying; full FY1–FY5 consensus fields and null preservation; safe FMP source labeling; canonical/scenario/not-ready/not-run expectations states; scenario-assumption metadata; reference-only external evidence; structured data-quality rows with no score; issues/warnings/blockers; safe deduplicated sources; supporting IDs/provenance; unrounded numerics and semantic hints; strict mixed-target/mixed-snapshot failures; partial and complete reports; deterministic section order; no HTML/CSS/Streamlit/judgment/narrative; AST guards against valuation, price-gap, and reverse-DCF recalculation; provider isolation; mocked explicit live-wrapper boundaries; and zero network access under normal pytest.

Targeted result: **89 passed in 0.46s**. Complete normal result: **1,440 passed in 11.12s**, the approved 1,351-test baseline plus 89 Milestone 11C cases. Normal pytest made zero live network calls. The META report audit was one separate bounded explicit CLI invocation.

## Milestone 12A feature-flagged Streamlit research-report renderer

`tests/test_v1_report_ui.py` adds 68 synthetic cases covering acceptance and non-mutation of `StockResearchReport`; exact-contract compatibility across Streamlit hot reloads; controlled enum-value rendering across reloads; Streamlit isolation to UI/legacy entry layers; no provider or report-builder imports/calls; AST guards against valuation, gap, and reverse-DCF arithmetic; zero normal-network access; resolved and withheld overall states; family-central non-promotion; mandatory ordered Own History/Peer rows; unavailable-family blockers; supplied-only family/overall gaps; unchanged consensus order and values; missing analyst-count nulls; FMP labeling; canonical/scenario/not-ready/not-run expectations; read-only scenario assumption metadata and no controls; reference-only separation; readiness rows without scores; safe sources; no internal company key/secret access; null-without-zero behavior; GBP/GBp separation; no ticker conversion; visual-only formatting; explicit flag parsing; default legacy route; pre-built session-report injection; empty state; and absence of stance, judgment, and generated narrative.

Targeted result: **68 passed in 0.93s**. Combined 11C/12A boundary result before final visual fixes: **155 passed in 1.19s**. Final complete normal result: **1,508 passed in 5.05s**, the approved 1,440-test baseline plus 68 Milestone 12A cases. Normal pytest made zero live network calls. Browser validation used synthetic complete/partial reports and one separate cache-bounded META audit; no screenshot fixture or raw provider payload was retained.

## Milestone 12B report integration coordinator and route states

`tests/test_v1_research_integration.py` adds 29 synthetic cases covering ticker acceptance and empty rejection; unchanged GOOG/GOOGL/London symbols; one aware snapshot; naive and mixed-snapshot failure; complete and partial reports; nonfatal market/peer/reverse-DCF/reference gaps; fatal identity and report-contract outcomes; earliest controlled stage; safe nested audit issue retention; exception-detail withholding; safe result fields; cache hit, expiry, bound, ticker isolation, report-version isolation, retry invalidation, and one-attempt behavior; AST guards against duplicate valuation/gap/reverse-DCF arithmetic; reuse of the existing live report chain; empty/loading/success/fatal/retry UI states; one submit/one build; normal rerun/no build; new ticker/new build; report-only renderer handoff; safe session state; no provider/financial policy/scenario/stance in UI; feature-flag/legacy retention; ticker-branch exclusion; and zero normal-network access.

Final targeted 11C/12A/12B result: **186 passed**. Final complete normal result: **1,537 passed**, the approved 1,508-test baseline plus 29 Milestone 12B cases. Normal pytest makes zero live network calls; all live work is explicit and separate. Browser validation at 1280×720 covered the production empty state, a provider-free restrained loading state, a provider-free successful report, the controlled live identity-failure/retry state, and a normal input rerun that retained the current report without a coordinator call. Temporary preview files and browser state were removed.

## Milestone 12C integration hardening and normalized evidence reuse

`tests/test_v1_integration_hardening.py` adds 21 synthetic cases (with parameterized branches) covering one aware per-build snapshot; exact semantic keys; canonical scalar/date/decimal acceptance; rejection of mappings, bytes, raw payload fields, URLs, secret-bearing text, and opaque company keys; exact-request reuse; provider/capability/target/snapshot/parameter separation; ordered aware nonnegative stage timing; safe blockers/counts; three-unique-fetch versus five-request performance instrumentation; identical-build single-flight success; shared safe failure; no waiter deadlock; retry recovery; context-construction failure; truthful no-cancellation state; controlled UI diagnostics; identity-fatal downstream stop; no ticker branches; and no new economic orchestration. Existing 12B tests continue to cover partial peer/reverse/reference failures, report-cache policy, retry, renderer-only handoff, default-off feature flag, legacy availability, and zero-network normal execution.

Targeted 12C/12B result: **50 passed in 1.30s**. Touched live-audit/adjacent-economic result after the only compatibility fix: **183 passed**. Final complete normal result: **1,558 passed in 11.75s**, the approved 1,537-test baseline plus 21 Milestone 12C cases. Normal pytest made zero live network calls under the existing network-denial guard.

## Milestone 12D live identity/configuration regression integration

`tests/test_v1_live_identity_integration.py` adds 16 collected cases covering ambient `.env` resolution, explicit-injection isolation, missing configuration before request work, safe construction/transport/HTTP/parse/normalization/canonical-assembly diagnostics, successful stable Fiscal identity, request-contract preservation, normalized-registry admission, secret/raw-field exclusion, Fiscal-before-Yahoo behavior, direct/coordinator parity, and absence of ticker-specific identity logic.

Targeted result: **85 passed in 1.61s**. Complete normal result: **1,574 passed in 7.10s**, the approved 1,558-test baseline plus 16 Milestone 12D cases. Normal pytest made zero live network calls. One separate bounded META execution verified canonical identity and a coherent partial report.

## Milestone 12E overview and valuation UX polish

`tests/test_v1_report_valuation_ux.py` adds 16 collected presentation regressions covering the compact report-driven header; explicit `x of 2` family count; reverse-DCF exclusion; human family/method labels; retained unavailable peer row; supplied-only family and overall gaps; exact supplied range/central/current-market chart data; absence of fake peer, market, and unresolved-overall marks; resolved-only overall envelope; no chart calculation transforms; factual methodology; safe sources; report non-mutation; institutional visual tokens; and absence of stance language. Existing renderer/integration cases continue to cover default-off routing, legacy availability, provider isolation, no financial recomputation, null/withheld preservation, and zero-network normal execution.

Final targeted renderer/valuation/integration result: **113 passed in 1.90s**. Final complete normal result: **1,590 passed in 8.12s**, the approved 1,574-test baseline plus 16 Milestone 12E cases. Normal pytest made zero live network calls. Browser QA used provider-free synthetic RESOLVED, UNRESOLVED, and market-unavailable reports at 1280×720 and 1440×900; temporary QA files and browser state were removed.

## Milestone 12F forward consensus and market-expectations UX

`tests/test_v1_report_forecast_expectations_ux.py` adds 22 collected presentation regressions covering explicit external-consensus language and FMP source retention; unchanged period order, dates, supplied financial values, estimate timestamps, EBITDA/count nulls, signed growth, and tabular/right-aligned presentation; supplied-only revenue-growth/EBIT-margin chart records; missing-point omission; no extrapolated periods or Altair calculation transforms; distinct canonical/scenario/not-ready/not-run expectation states; exact supplied comparator values; withheld growth and safe blockers; complete scenario assumption/provenance presentation; no scenario controls, expectation judgment, narrative, scores, price language, consensus/reverse-DCF arithmetic, provider/application dependency, report mutation, or network access; and unchanged default-off feature flag/legacy availability.

Final targeted 12F/renderer/valuation/integration result: **135 passed in 2.64s**. Final complete normal result: **1,612 passed in 9.00s**, the approved 1,590-test baseline plus 22 Milestone 12F cases. Normal pytest made zero live network calls. Browser QA covered synthetic complete/canonical, partial/not-ready, and scenario states at 1280×720 and 1440×900; temporary QA files and browser state were removed.

## Milestone 12G methodology, evidence, readiness, and diagnostics UX

`tests/test_v1_report_evidence_methodology_ux.py` adds 24 collected presentation regressions covering reference-only separation and labels; non-promotion to family or overall value; empty-reference visibility; exact six-row readiness/status retention; concise and full blockers; safe source/date context; explicit absence of score/percentage/grade/gauge semantics; issue/warning separation; concise static methodology and reverse-DCF separation; source deduplication; distinct analysis/market/consensus timestamps; supporting-ID and unsafe-metadata suppression; safe stage/status/duration/blocker diagnostics; raw-exception suppression; operational timing without scoring; no generated diagnosis, narrative, stance, scenario controls, provider imports/calls, or valuation arithmetic; report non-mutation; default-off feature flag; legacy route; and zero normal network access.

Final targeted 12G/renderer/valuation/integration result: **180 passed in 3.14s**. Final complete normal result: **1,636 passed in 14.77s**, the approved 1,612-test baseline plus 24 Milestone 12G cases. Normal pytest made zero live network calls. Browser QA covered synthetic complete, partial, and safe technical-diagnostics states at both 1280×720 and 1440×900. Reference evidence remained visibly non-central, readiness and blockers remained readable, issues and warnings stayed distinct, methodology stayed restrained, diagnostics stayed secondary, and no score/gauge/marketing treatment appeared. Temporary QA files, local server state, and browser overrides were removed.

## Milestone 12H explicit reverse-DCF scenario UI

`tests/test_v1_reverse_dcf_scenario_ui.py` adds 42 collected synthetic cases covering immutable provider-independent context retention; exact security/issuer/snapshot/currency binding; context absence when normalized inputs are insufficient; three blank required fields; finite positive input validation; one-time WACC percentage and revenue-billions normalization; exact preceding fiscal period; the existing 10C known-root solution and approved no-solution domain; exactly three `USER_SUPPLIED` assumptions with provenance; `EXPLICIT_SCENARIO`, `SCENARIO_ONLY`, and permanent central ineligibility; unchanged canonical publication and family outputs; canonical solved plus scenario and canonical `NOT_READY` plus solved scenario; safe mismatch, validation, unavailable, no-solution, and numerical-state rendering; explicit-submit-only execution; reset, ticker, Analyse, and rebuild clearing; one active result; displayed executed assumptions rather than edited widget values; no defaults, sensitivity, presets, saved history, provider call, economic solver duplication, valuation language, stance, narrative, global cutover, or legacy removal; and a socket-denial network guard.

Dedicated 12H result: **42 passed in 1.68s**. Broader targeted 12H/10C/renderer/integration result before the two interrupted-test expectation corrections: **260 passed, 2 test-only failures**; both corrected assertions then passed in the dedicated run. Final complete normal result: **1,678 passed in 15.36s**, the approved 1,636-test baseline plus 42 Milestone 12H cases. Normal pytest made zero live network calls. A provider-free Streamlit visual harness was started for the six requested states, but the managed desktop browser policy blocked recovery from an internal connection-error URL and explicitly prohibited alternate browser workarounds. Consequently no claim of completed screenshot inspection is made; temporary harness/server state was removed and the six states remain covered by provider-free structural renderer tests.

## Milestone 9A discount-rate evidence and readiness

`tests/test_v1_discount_rates.py` adds 52 synthetic cases covering DGS10 USD-only mapping and decimal units; snapshot selection, future/stale/missing-day behavior, and centralized freshness; non-USD rejection without fallback; explicit provenance-bound ERP and no default; finite and uncapped beta; conservative provider-defined beta policy; exact CAPM without extra premiums; identity/snapshot/currency mismatch blocking; WACC prerequisite diagnostics without calculation; book-versus-market debt semantics; London-listing and GBp/valuation-currency separation; forbidden DCF, reverse DCF, current-price, valuation-family, aggregation, stance, ticker, and UI dependencies; immutable stable IDs; safe GBP audit output; and zero network access in normal execution.

Targeted result: **52 passed in 0.29s**. Complete normal result: **895 passed in 9.96s**, the approved 843-test baseline plus 52 Milestone 9A cases. Normal pytest performed zero live network calls; live readiness audits were separate explicit CLI runs.

## Milestone 9B.1 sourced ERP, regression beta, and live CAPM

`tests/test_v1_discount_rates_9b1.py` adds 73 synthetic cases covering the exact NYU adjusted-payout headline parser and percentage normalization; fixed series selection; latest non-future observation; centralized 62-day freshness; stale/missing/source/schema failure; provenance, USD/US scope, and full-Treasury convention; adjusted Yahoo price-history normalization and incompatible price/currency failure; explicit SPY benchmark identity; 60-month simple-return alignment; no interpolation/forward-fill/gap bridging/future data; exact OLS slope, intercept, R-squared, variance, errors, dates, and source IDs; 36-observation minimum; no weekly fallback; zero variance; finite, high, and non-positive beta policies; no adjustment/cap/provider averaging; exact CAPM; missing/stale/non-USD failures; unchanged production-WACC withholding; architecture exclusions; and zero normal-network calls.

Final targeted result: **73 passed in 0.31s**. Final complete normal result: **968 passed in 3.26s**, the approved 895-test baseline plus 73 Milestone 9B.1 cases. Normal pytest performed zero live network calls; the META audit was a separate explicit CLI invocation.

## Milestone 9B.2 USD production-WACC evidence

`tests/test_v1_wacc.py` adds 67 synthetic cases covering market-cap eligibility/freshness/currency; gross-debt versus net-debt semantics; explicit zero and missing debt; visible book proxy scope/provenance; fictitious NYU rating/tax HTML normalization; exact rating boundaries, overlaps, outside-range withholding, and no interpolation; canonical Fiscal interest-expense sign normalization; annual same-period EBIT coverage; no operating-income/quarterly/zero-interest substitution; exact DGS10-plus-spread debt cost; domicile-only marginal-tax selection and 400-day freshness; aligned TEV residuals without preferred/NCI relabeling; explicit zero/nonzero/unavailable claims; exact non-negative capital weights; after-tax debt cost and WACC arithmetic; every readiness blocker; immutability; no rounding; no network; and forbidden DCF/current-price/family/aggregation/stance/UI/ticker behavior.

Targeted result: **67 passed in 0.34s**. Affected Fiscal/live-smoke verification passed **88 tests in 1.25s**. Complete normal result: **1,035 passed in 4.80s**, the approved 968-test baseline plus 67 Milestone 9B.2 cases. Normal pytest performs zero live network calls; official-source and Fiscal checks are explicit opt-in executions only.

## Milestone 10A reverse-DCF operating evidence/readiness

`tests/test_v1_reverse_dcf_inputs.py` adds 51 synthetic cases covering immutable contracts; stable IDs; annual FMP `AVERAGE` revenue/EBIT/EBITDA selection; no low/high fallback; FY1 fiscal semantics; ordering and gap exposure; positive revenue and finite/negative EBIT; unclamped margin and consecutive growth arithmetic; exact preceding actual-base rules; reuse of canonical Fiscal actual selection; Operating Income/EBIT separation; snapshot/provider/identity/currency/base-unit gates; analyst counts; fresh positive Fiscal `calculated_tev`; no enterprise-value reconstruction/current price; generic FCF and OCF-minus-CapEx separation from FCFF; verified FCFF definition evidence; reinvestment inventory/methodology separation; no historical-percentage forecast; explicit terminal policies with no defaults; frozen WACC and canonical marginal-tax compatibility; formulation-specific `READY`/`PARTIAL`/`NOT_READY`; earliest blocker; immutability; no solver/PV/implied assumption/fair value/ticker/UI/network behavior; and singular/plural live capability-result normalization.

Targeted result: **51 passed in 0.44s**. Final complete normal result: **1,111 passed in 4.57s**, the approved 1,060-test baseline plus 51 Milestone 10A cases. Normal pytest performed zero live network calls; the META audit was a separate explicit CLI invocation.

## Milestone 10B consensus-anchored enterprise reverse DCF

`tests/test_v1_reverse_dcf_solver.py` adds 61 synthetic cases covering the sole authorized terminal-growth formulation; unchanged consensus revenue/EBIT; no annual growth or margin solve; marginal-tax eligibility and NOPAT arithmetic; positive provenance-bound sales-to-capital evidence with no default; configured-evidence requirements; FY1 preceding-revenue and explicit blocker semantics; reinvestment/FCFF arithmetic including negative results; exclusion of D&A/CapEx/NWC/generic FCF; READY production-WACC gating; discrete annual discounting; final-consensus-margin and terminal sales-to-capital economics; terminal-value/enterprise-value equations; bounded deterministic bisection; known negative, moderate, and high-positive roots; exact boundaries; no bracket/domain widening; monotonicity/finite-arithmetic/iteration failures; complete audit fields; no plausibility/fair-value/current-price/aggregation/UI/provider dependencies; and a zero-network bounded-audit regression.

Targeted 10A/10B result: **112 passed in 0.47s**. Final complete normal result: **1,172 passed** against the approved 1,111-test baseline, adding 61 Milestone 10B cases. Normal pytest makes zero live network calls; the META audit is a separate explicit CLI invocation.

## Milestone 10C reverse-DCF execution modes and publication separation

`tests/test_v1_reverse_dcf_execution.py` adds 56 synthetic cases covering the exact two-mode enum with no fallback; the three authorized assumption types and source categories; mandatory provenance and no numeric defaults; WACC decimal semantics without fake `WaccResult`; separate production-WACC status; finite positive dimensionless sales-to-capital; positive currency-aligned annual preceding revenue; LTM rejection and exact FY1 predecessor calendar; identity/snapshot binding; duplicate/missing assumptions; immutable contracts; canonical no-fallback behavior; deterministic explicit overrides with canonical references retained; unchanged FMP trajectory/TEV/tax/final margin; shared 10B reinvestment/FCFF/terminal/bisection mathematics; canonical and scenario publication statuses; permanent central-valuation ineligibility; safe labels/comparators without interpretation or scoring; negative/high solved growth retention; no promotion/sensitivity/provider/price/fair-value/family/aggregation/stance/ticker/UI behavior; and zero-network audit/execution paths.

Targeted 10B/10C result: **117 passed in 0.47s**. Final complete normal result: **1,228 passed in 13.23s**, the approved 1,172-test baseline plus 56 Milestone 10C cases. Normal pytest made zero live network calls; the assumption-free META audit was a separate explicit CLI invocation.

## Milestone 11A cross-family valuation publication

`tests/test_v1_valuation_publication.py` adds 52 synthetic cases covering approved `OWN_HISTORY`/`PEER` families; one contribution per family; duplicate ambiguity; two-family independence; identity/snapshot/currency/unit alignment; no FX or GBP/GBp conversion; finite positive ordered complete ranges; partial/unavailable disclosure; zero/one/two-family gates; threshold-free common overlap; `RESOLVED`, `WIDE`, `UNRESOLVED`, and `UNAVAILABLE`; shared Type-7 median; family envelope versus common overlap; exact descriptive diagnostics; reverse-DCF expectation/scenario separation; external analyst/provider-reference exclusion; generic upstream-result adaptation without recalculation; provenance/support retention; immutability; forbidden price/score/stance fields; no ticker behavior; safe CLI rendering; Streamlit isolation; and zero-network normal execution.

Targeted 11A result: **52 passed in 0.43s**. Final complete normal result: **1,280 passed in 6.28s**, the approved 1,228-test baseline plus 52 Milestone 11A cases. Normal pytest made zero live network calls. The separate one-time META CLI audit produced one eligible own-history family, an unavailable peer family, `UNRESOLVED`, and no overall central value.

## Milestone 12I final engineering acceptance

`tests/test_v1_final_engineering_acceptance.py` adds 33 acceptance regressions. They cover the visible ticker label and meaningful actions; US USD semantics; explicit UK GBP/GBp scale normalization; absence of `.L` arithmetic and FX; exact-security price gating; all four publication states; known-family retention and null unavailable values; available/unavailable/stale market states; ready/partial/missing consensus without fabrication; canonical, scenario, not-ready, not-run, solved, and no-solution expectation states; ticker/snapshot/rebuild scenario clearing; Analyse/rerun/retry build counts; unchanged 8-entry/300-second/300-second cache policy; fatal and partial rendering; structural semantics; contrast; responsive CSS; safe audit allowlisting; default-off/legacy retention; no stance/narrative; no UI business arithmetic; and zero network access from renderer/scenario paths.

The broader targeted acceptance group passed **479 tests in 4.87s**. Complete normal pytest passed **1,711 tests in 10.30s**, the approved 1,678 baseline plus 33 Milestone 12I cases. Normal pytest made zero live network calls. Existing concurrency, deterministic LRU, failed-flight release, session isolation, provider isolation, and all prior economic-policy suites remain green.

Provider-free real-browser QA exercised the 15 required states at 1280×720 and 1440×900 and a narrow 390×844 structural pass. Accessibility-tree inspection retained visible input labels, meaningful button/table/status text, scenario/reference labels, and nearby textual equivalents for charts. Keyboard order was Ticker, Analyse, Retry/Rebuild, expanders, Scenario analysis, WACC, Sales-to-capital, preceding annual revenue, Run scenario, Reset scenario. No screen-reader environment was available, so this is a structural semantic review rather than assistive-technology certification.

## Milestone 12J LSE identity portability

Synthetic coverage verifies unchanged one-page US resolution; exact London `ticker + XLON micCode` acquisition; primary/secondary listing selection; later-page fallback after exact 404; 16-page, 16,000-row, and 256-profile-listing bounds; no open-ended loop; suffix/base-ticker insufficiency; unique explicit LSE/XLON acceptance; non-London rejection; multiple/conflicting venue failure; stable issuer/security retention; stable-`fscl` profile enrichment; stable-ID conflict failure; no mass profile enrichment; HTTP-403 fan-out prevention; Yahoo non-substitution; safe controlled diagnostics; raw/secret/company-key exclusion; generic production code with no SHEL/RR branch; and no unit, FX, discount-rate, valuation, recommendation, feature-flag, legacy, cache, single-flight, scenario, or network regression.

Focused Fiscal identity/portability suites passed **56 tests**. The broader application/cache/scenario/currency/final-acceptance set passed **320 tests** before the final access-denial case was added; that case and all broader contracts are included in the final **1,736-test** normal suite. Normal pytest made zero live network calls. One live build each was performed after synthetic success: SHEL.L reached canonical identity and a partial report; RR.L stopped safely at Fiscal HTTP 403 during exact venue-scoped profile acquisition. META/MSFT/NVDA were not called again; their approved 12I live results were reused with the new US fast-path regression.

## Milestone 12K provider availability and access-denied acceptance

`tests/test_v1_provider_availability_cutover.py` adds 19 provider-free release-boundary cases. They cover application `AVAILABLE` for generic US/LSE success; distinct access-denied, not-found, authentication-failure, rate-limited, provider-failure, evidence-conflict, and unavailable classifications; no identity/report/supporting ID/scenario on denial; no Yahoo/listing substitution; safe provider-access wording; requested-symbol and Identity-stage presentation; explicit Retry; no automatic retry; one-action/one-attempt behavior; stale cross-ticker report and scenario clearing; no HTTP/credential/URL/company-key/raw-response/traceback rendering; generic entitlement limitation documentation; no RR branch/blacklist/provider bypass; default-off flag; legacy availability; no stance; and zero normal network access.

Focused 12K tests passed **19 tests**. The broader identity/route/isolation/scenario/currency/acceptance set passed **252 tests**. Complete normal pytest passed **1,755 tests in 15.92s**, the approved 1,736 baseline plus 19 Milestone 12K cases. Normal pytest made zero live network calls. No live check was performed in 12K.

## Milestone 13A release cutover and startup acceptance

`tests/test_v1_release_cutover.py` adds 33 collected provider-free cases. They cover V1-by-default routing; case-insensitive explicit legacy true values; absent/false/blank/invalid rollback; deterministic deprecated-flag behavior and rollback precedence; actual Streamlit AppTest startup for both routes; empty ticker and explicit Analyse; zero provider work at startup; no duplicate route; no silent fallback after controlled failure; access-denied isolation; partial-report validity; direct Altair/runtime declarations; canonical startup documentation; safe ignore patterns and `.env.example`; singular 1.0.0 Python version source; non-preview product naming; hidden framework stack traces; and no target branch, FX, provider, stance, or generated narrative change.

Focused 13A tests passed **33 tests in 3.07s**. The broader release/routing/cache/single-flight/identity/GBP/GBp/access-denied/scenario/redaction/architecture group passed **505 tests in 7.38s**. Complete normal pytest passed **1,788 tests in 9.57s**, the approved 1,755 baseline plus 33 Milestone 13A cases. Normal pytest made zero live network calls.

Browser startup QA separately verified the default empty research route and the explicit idle legacy rollback route. One permitted live META build rendered a coherent partial report. Synthetic access-denied and explicit three-input scenario paths remained green without live calls.

## Milestone 13B repository presentation and release assets

`tests/test_v1_repository_presentation.py` adds 9 provider-free repository contract checks. They cover the required README section order; canonical startup and idle behavior; singular 1.0.0 version source; explicit legacy rollback and deprecated-flag behavior; exact allowlisted `.env.example`; secret-pattern exclusion across public release documents; consistent provider roles; provider-free/fail-closed architecture statements; five safe 1440×900 release PNGs; and the required portfolio/release-note structures.

Focused presentation tests passed **9 tests in 0.06s**. The combined release-cutover/presentation group passed **42 tests in 3.26s**. Complete normal pytest passed **1,797 tests in 9.36s**, the approved 1,788 baseline plus 9 Milestone 13B cases. Normal pytest made zero live network calls.

Browser QA rendered immutable synthetic evidence through the released Streamlit V1 renderer at 1440×900. The five retained captures cover overview/valuation, forward consensus plus canonical expectations, readiness plus methodology/provenance, an explicit scenario result, and controlled provider access denial. The temporary provider-free preview and local server were removed. No live provider request, credential, raw response, authenticated URL, environment value, opaque provider key, or proprietary payload entered the assets.
