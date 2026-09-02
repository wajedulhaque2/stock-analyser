# V1 Live Provider Smoke Testing

## Purpose

`scripts/v1_provider_smoke.py` is an explicit local contract-verification tool. It calls selected real provider APIs through the existing V1 adapters and reports only safe capability and normalization metadata. It is not part of Streamlit, normal application execution, pytest, or CI.

## Run it explicitly

From the repository root:

```powershell
python scripts/v1_provider_smoke.py --symbol META
python scripts/v1_provider_smoke.py --symbol MSFT --providers yahoo,sec,alpha
python scripts/v1_provider_smoke.py --symbol META --strict
python scripts/v1_provider_smoke.py --symbol META --json-summary
```

The default provider set is `yahoo,fiscal,sec,fmp,finnhub,alpha,fred`. `--strict` returns a nonzero status for execution/schema/normalization failures; `LOCKED`, `UNAVAILABLE`, and missing-credential `SKIPPED` results remain nonfatal. The JSON form contains the same allowlisted summary metadata and no raw response.

## Credentials and quota

The script reads `.env` only when explicitly executed and recognizes:

- `FISCAL_API_KEY`
- `FMP_API_KEY`
- `FINNHUB_API_KEY`
- `ALPHAVANTAGE_API_KEY`
- `FRED_API_KEY`
- optional `SEC_USER_AGENT`

Yahoo and SEC do not require API keys. Each selected live endpoint consumes real provider quota. The tool makes bounded requests, reuses responses within a provider run, and does not automatically run a second symbol.

FRED documents its API key as exactly 32 lowercase alphanumeric characters. The smoke runner validates that shape before constructing a transport; a malformed configured value produces a safe configuration `ERROR` and consumes no FRED quota.

## Safety guarantees

- Credential values or fragments are never rendered, represented, cached, or added to request identities.
- Complete URLs, authorization metadata, raw bodies, and arbitrary provider exception text are never printed.
- Text and JSON output contain only provider/capability states, counts, canonical metric IDs, period counts, normalization state, and short allowlisted notes.
- Responses remain in memory. The tool does not write payloads, fixtures, caches, or documentation.
- A provider failure is isolated from unrelated providers.
- `LOCKED` and `UNAVAILABLE` are valid evidence of plan or coverage limits.

Never invoke this script from pytest and never commit provider payloads captured while diagnosing a live mismatch. Represent a confirmed mismatch with a minimal fictitious synthetic regression fixture instead.

## Milestone 6D access audit

The August 2026 access audit verified the request contracts without printing credentials or bodies:

- Fiscal uses `https://api.fiscal.ai`, `X-Api-Key`, the proven StockAnalyser user agent, and documented `compact`/`pageNumber` company-list parameters. The configured access still returned HTTP 403, which remains an unclassified access `ERROR`.
- FMP stable and documented v3 annual-estimate requests both returned HTTP 401 with the configured project credential. The stable adapter contract remains `apikey` in the query.
- Finnhub query-token auth, documented `X-Finnhub-Token` header auth, and both documented/official SDK hosts returned HTTP 401. This is authentication failure, not entitlement.
- FRED's endpoint and required `series_id=DGS10`, `file_type=json`, and `api_key` parameters are correct. The configured value failed the documented key-format preflight, explaining the prior HTTP 400.

These outcomes indicate local credential/account configuration blockers rather than normalization changes. Correct or rotate the affected provider credentials before another targeted live run. Do not repeatedly rerun successful or quota-limited providers while doing so.

## Milestone 6E Fiscal repair

The Fiscal failure was subsequently isolated to the V1 live source rather than the credential. Its prepared company-list request used `User-Agent: StockAnalyser/1.0`; the proven request using `StockAnalyser/0.7.1` returned HTTP 200 with the same resolved key and Python HTTP library. After restoring that exact user agent, endpoint tracing found that profile and standardized requests also require the proven `companyKey=<companyKey>` contract in this environment. Sending `company=<companyFiscalIdentifier>` returned HTTP 400.

Live standardized rows use `reportDate` as their period end. The Fiscal live boundary aliases that verified field only for smoke normalization, leaving legacy parsing unchanged. When the live row has no timezone-aware source as-of timestamp, the canonical adapter uses retrieval time and records that substitution explicitly in provenance.

The final targeted META Fiscal smoke passed with identity and standardized history available: 1,141 observations, 17 annual periods, and 46 quarterly periods. No response body, credential material, or provider payload was persisted.

## Milestone 7A Fiscal historical valuation verification

The Fiscal smoke runner now makes three additional bounded daily-ratio requests for the approved source metrics only: P/E, EV/EBITDA, and EV/EBIT. It emits a separate `FISCAL HISTORICAL VALUATION` block with normalization state, observation count, controlled multiple types, earliest/latest dates, and daily/annual/quarterly counts. JSON contains the same allowlisted nested summary. It never prints ratios or provider payloads.

Live characterization found a bare list of `{date, ratio}` objects with 2,680 rows per ratio, not the legacy nested ratio structure. The boundary now normalizes that shape with `companyKey` and the proven `StockAnalyser/0.7.1` user agent. The final 2026-08-27 META run passed with 8,040 canonical daily observations spanning 2015-12-31 through 2026-08-27 across `P_E`, `EV_EBITDA`, and `EV_EBIT`; annual and quarterly counts were both zero by design. Fiscal standardized actuals remained independently available at 1,141 observations, 17 annual periods, and 46 quarterly periods.

No real response was saved as a fixture. The matching regression uses fictitious list rows, runs without network access, and asserts request contract, sampling, safe DTO shape, and secret-free output.

## Milestone 7C.1 Fiscal enterprise bridge verification

The Fiscal runner makes only three additional bounded requests for the direct bridge: daily `calculated_tev`, daily `calculated_market_cap`, and `shares-outstanding`, all using the previously verified `companyKey` and user-agent contract. Live META responses on 2026-08-27 were bare lists: each daily bridge series contained 2,680 `{date, ratio}` rows, and shares contained 117 `{date, shareClasses, totalSharesOutstanding}` rows. The live boundary forwards only source metric, date/as-of, numeric value, reporting currency where monetary, and total shares; it does not forward share-class payloads.

Normalization passed with the `enterprise_equity_bridge` capability `AVAILABLE`. A separate status-only META prerequisite audit produced:

- P/E: distribution `USABLE`, denominator `UNVERIFIED`, readiness `NOT_READY`.
- EV/EBITDA: distribution `USABLE`, denominator `EXACT`, direct bridge `COMPLETE`, observation alignment `PASS`, shares `AVAILABLE`, readiness `READY`.
- EV/EBIT: distribution `USABLE`, denominator `UNVERIFIED`, readiness `NOT_READY`.

No fair value or bridge input value was printed. No raw provider response, share-class payload, or credential was persisted. Normal pytest remains network-free; rerun the live audit only when an adapter contract changes.

## Milestone 7D.1 own-history valuation audit

Run explicitly from the repository root:

```text
python scripts/v1_own_history_audit.py --symbol META
```

The command uses Fiscal and Yahoo identity facts, but invokes no Yahoo market-snapshot/current-price call. It then uses Fiscal historical ratios and the direct TEV/market-cap/share bridge plus FMP annual estimates. Each required endpoint is requested once per audit where normal retry policy does not require a retry. Output is limited to normalized canonical values, IDs, dates, diagnostics, and derived values; credentials and raw responses are never rendered or persisted.

The 2026-08-27 META audit passed. P/E was `NOT_READY` because generic FMP EPS is not verified as Fiscal diluted EPS. EV/EBIT was `NOT_READY` because generic FMP EBIT is not verified as Fiscal Operating Profit. EV/EBITDA was `READY` and `VALID`: the 5Y distribution had 1,256 eligible daily observations spanning 1,826 days, with P25/median/P75 of approximately `13.58x / 16.48x / 18.76x`. Selected FY1 average EBITDA was approximately USD 115.745bn for 2026-12-31. Fiscal TEV was approximately USD 1.478493tn, market cap USD 1.456435tn, the direct adjustment USD 22.058bn, and approved shares 2.547506bn. Resulting per-share method points were approximately USD `608.23 / 740.14 / 843.59`. Unit, currency, scale, bridge, share, date, and all independent arithmetic checks passed. These are one method's live audit values, not an overall fair value or investment recommendation.

## Milestone 7E portability runs

The same command was run separately for META, MSFT, NVDA, SHEL.L, and RR.L. The audit now displays canonical security/issuer identity, listing, security type, reporting currency, quote currency/unit/scale, fiscal-year-end, provider symbols, all three 5Y distributions, all three FY1 denominator availabilities/alignments, bridge completeness, readiness, numeric READY results only, earliest failure stage, and controlled portability classification.

- META regression control: EV/EBITDA `VALID`, USD/share `608.23 / 740.14 / 843.59`; P/E and EV/EBIT remained denominator-alignment withheld.
- MSFT after generic fiscal-year-end repair: 5Y EV/EBITDA `19.35x / 22.42x / 24.28x`, FY1 EBITDA USD 213.929bn, complete USD bridge, and `VALID` USD/share `560.30 / 648.67 / 702.11`.
- NVDA: high observations were preserved (5Y EV/EBITDA max about 192.05x); distribution `37.97x / 47.34x / 63.43x`, FY1 EBITDA USD 202.050bn, complete USD bridge, and `VALID` USD/share `320.83 / 399.42 / 534.35`.
- SHEL.L: canonical `.L`, LSE, USD reporting, GBP quote currency, GBp quote unit, scale 0.01. Fiscal historical/bridge and FMP FY1 coverage were unavailable, so all methods remained `NOT_READY` with `HISTORICAL_DISTRIBUTION`; no value or FX was created.
- RR.L: canonical `.L`, LSE, GBP reporting, GBP quote currency, GBp quote unit, scale 0.01. Fiscal historical/bridge and FMP FY1 coverage were unavailable, so all methods remained `NOT_READY` with `HISTORICAL_DISTRIBUTION`; no GBP-to-GBp valuation conversion or value was created.

Each company was evaluated independently. No cross-stock ranking/comparison, current-price input, aggregation, macro input, outlier clipping, sector window change, raw payload, or secret was introduced.

## Milestone 8A–8A.2 peer-discovery audit

Run explicitly from the repository root:

```text
python scripts/v1_peer_audit.py --symbol META
```

The command uses Fiscal's documented v3 company-profile `peers` array. Stable summary issuer/security identifiers plus primary-listing ticker/exchange seed bounded identity without security type; full profile is optional enrichment. Security eligibility, economic comparability, and EV/EBITDA data readiness print separately. Yahoo identity metadata may enrich security class only after exact symbol and controlled exchange compatibility with the Fiscal-anchored listing; it never creates or changes identity and never fetches a market snapshot. The command does not calculate a peer multiple, use current price, consume own-history distributions, calculate target value, aggregate methods, create a stance, or touch Streamlit/legacy execution.

The first 8A.1 META run discovered all nine candidates and proved all nine stable issuer/security IDs remained available, but the then-coupled summary-security validator rejected identity because `security_type` was absent. That mismatch motivated 8A.2. Two bounded follow-ups printed schema keys only and then symbol plus normalized `companyType`; no raw record was printed or stored. Live `primaryListing` exposed ticker, exchange, operating MIC, `securityFiscalIdentifier`, and FIGI identifiers, but no security-type field. Every peer reported `companyType=operating_company`, which is issuer classification and was not reinterpreted as listed-equity security type.

The final 8A.2 access check returned Fiscal identity `AVAILABLE`. The corrected safe META audit then discovered nine candidates and resolved all nine canonical identities: GOOG, AMZN, and AAPL were `PROFILE_VERIFIED`; SNAP, PINS, RDDT, 700, 1024, and RBLX remained valid `SUMMARY_VERIFIED` identities. A failed optional profile did not destroy a summary identity, while the synthetic conflict path still rejects mismatched stable IDs. Third-party Yahoo console diagnostics are captured and discarded so raw error payloads are not emitted or persisted.

Security eligibility was `VERIFIED_COMMON_EQUITY` for GOOG, SNAP, PINS, RDDT, AMZN, AAPL, and RBLX. GOOG/AMZN/AAPL used matched Fiscal full-profile security evidence; SNAP/PINS/RDDT/RBLX used listing-compatible Yahoo identity metadata. 700 and 1024 remained `UNVERIFIED` because no compatible security-class evidence was available. `companyType=operating_company`, company name, relationship, reasoning, and ticker were not used as substitutes.

No candidate was economically included. AMZN and AAPL were excluded for controlled industry mismatch. GOOG, SNAP, PINS, and RDDT remained unverified for insufficient canonical financial evidence; 700 and 1024 remained unverified for both security eligibility and financial evidence; RBLX remained unverified for sector-only industry plus financial evidence. EV/EBITDA method-data readiness was separately `UNAVAILABLE` for all nine because forward denominators and required multiple inputs were unavailable. The peer set was `PARTIAL`, with zero included versus the unchanged minimum of three independent issuers. One safe endpoint-isolated Fiscal identity issue was reported; summary identity survived. No peer multiple, fair value, current-price comparison, raw response, credential, or proprietary payload was emitted or stored.

FMP documentation review verified stable symbol/name/CIK/CUSIP/ISIN search plus profile/profile-by-CIK surfaces. The Fiscal summary supplied no stable cross-provider CIK/CUSIP/ISIN, so no FMP candidate-symbol resolver or live candidate estimate call was added. Ambiguous mapping fails closed. No live payload, authenticated URL, or credential was persisted or printed.

## Milestone 8A.3 peer-financial audit

The safe META audit now reports Fiscal financial resolution, canonical annual-revenue selection periods, each financial criterion, economic membership, and EV/EBITDA method readiness separately. It never prints the opaque company key. Exact provider-internal stable-ID resolution was `AVAILABLE` for GOOG through the bounded company list and for SNAP, PINS, RDDT, 700, 1024, and RBLX through same-record peer-summary evidence. AMZN and AAPL were `NOT_EVALUATED` for financial retrieval after their existing definitive industry mismatches, avoiding unnecessary quota.

The first bounded run exposed a generic normalization defect: compact Fiscal lookup rows can omit reporting currency even when normalized standardized-financial rows carry it. The boundary was corrected to prefer explicit row currency and then cached profile currency, with no USD inference, and a synthetic regression was added. After targeted and complete suites passed, the final bounded run still returned a safe Fiscal standardized-actual retrieval failure. Consequently annual revenue was `UNAVAILABLE` for all seven evaluated candidates, scale/growth/margin stayed `UNRESOLVED`, and no GOOG observation conflict could be re-characterized from this run. No retry was made after the final audit.

All nine canonical identities remained resolved. GOOG, SNAP, PINS, and RDDT were unverified for insufficient financial data; 700 and 1024 were additionally blocked by unverified security eligibility; RBLX retained sector-only industry evidence; AMZN and AAPL remained excluded for industry mismatch. Included peers remained zero, the three-independent-issuer minimum was unchanged, and the peer set remained `PARTIAL`. EV/EBITDA method readiness remained `UNAVAILABLE` for all candidates because forward denominators and required multiple inputs were unavailable. No raw payload, secret, authenticated URL, provider company key, peer multiple, fair value, current price, aggregation, stance, or UI behavior was emitted or persisted.

## Milestone 8A.4 candidate standardized-actual diagnosis

One bounded SNAP control traced the real outgoing request. Stable company identifier resolution, non-empty company-key resolution, peer-summary provenance, and final `companyKey` placement all passed. The key was not the field name, candidate issuer ID, candidate ticker, or target META key. Scheme, host, path, method, query names/scope, API-key presence, accept header, `StockAnalyser/0.7.1`, session, redirect default, and 20-second timeout matched the known target path. Fiscal returned HTTP 403 on the first income-statement request before response normalization, so the earliest controlled stage was `PROVIDER_HTTP`; no raw response or key was emitted.

After synthetic validation, the single final META audit found nine candidates, nine identities, and seven security-eligible candidates. GOOG standardized financials were `AVAILABLE` with 429 normalized annual observations. SNAP, PINS, RDDT, 700, and RBLX were `ERROR` on provider HTTP 403 with zero annual observations; 1024 was `UNAVAILABLE` on HTTP 404; AMZN/AAPL remained `NOT_EVALUATED` after definitive industry mismatch. The five 403 results remain generic provider HTTP errors, not automatically `LOCKED`/entitlement.

The final audit also exposed a safe-render timing defect: observations with explicitly substituted retrieval timestamps were compared to the audit start rather than the later effective cutoff. This was corrected and regression-tested without rerunning all candidates. One targeted GOOG re-check then reported 103 annual revenue observations across 21 periods, but each recent period had five distinct source metrics with substituted as-of time and failed canonical selection as `observation_id_collision`. Revenue therefore correctly remains `UNRESOLVED`; no first/last choice, averaging, source-metric merge, or GOOG/GOOGL rewrite was used.

No peer was included. AMZN/AAPL remained excluded for industry mismatch; GOOG/SNAP/PINS/RDDT remained unverified for financial evidence; 700/1024 additionally retained unverified security eligibility; RBLX retained sector-only evidence. The peer set remained `PARTIAL` against the unchanged three-independent-issuer minimum. EV/EBITDA readiness remained separately `UNAVAILABLE` for all nine. No peer multiple, value, current price, DCF, aggregation, stance, UI behavior, raw payload, provider key, or secret was added or persisted.

## Milestone 8E peer-family portability audit

Run one target explicitly from the repository root:

```text
python scripts/v1_peer_family_audit.py --symbol META
```

The command is opt-in, is not invoked by pytest or Streamlit, and uses the same generic 8A→8E implementation for every symbol. It prints only canonical IDs, target currency/unit metadata, aggregate layer counts/statuses, numerical P25/median/P75 values when the complete chain is usable, earliest failure stage/reasons, and a safe issue count. It never prints raw provider payloads, credentials, authenticated URLs, opaque provider keys, or environment values.

The final independent one-shot runs were:

| Symbol | PeerSet | Included | 8B observations | 8C | 8D | Final status | Earliest stage |
|---|---:|---:|---:|---|---|---|---|
| META | PARTIAL (9 candidates) | 0 | 0 | UNAVAILABLE | UNAVAILABLE | EXPECTED_UNAVAILABLE | SECURITY_ELIGIBILITY |
| MSFT | PARTIAL (8 candidates) | 0 | 0 | UNAVAILABLE | UNAVAILABLE | EXPECTED_UNAVAILABLE | ECONOMIC_COMPARABILITY |
| NVDA | PARTIAL (8 candidates) | 0 | 0 | UNAVAILABLE | UNAVAILABLE | EXPECTED_UNAVAILABLE | ECONOMIC_COMPARABILITY |
| SHEL.L | UNAVAILABLE | 0 | 0 | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | IDENTITY |
| RR.L | UNAVAILABLE | 0 | 0 | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE | IDENTITY |

META, MSFT, and NVDA identities retained USD reporting/quote currency and USD quote units. META resolved nine candidate identities, seven with verified security eligibility; MSFT and NVDA each resolved eight, all security eligible, but no candidate passed every unchanged economic-comparability gate. SHEL.L and RR.L stopped safely when Fiscal identity metadata retrieval failed, so no listing/currency/unit claims were manufactured from absent live evidence. Synthetic portability regressions independently preserve SHEL.L/RR.L symbols, USD-versus-GBP reporting, GBP quote currency, GBp quote unit, and the rule that GBP/share is never multiplied by 100 or implicitly converted by FX.

## Milestone 9A discount-rate readiness audit

`python scripts/v1_discount_rate_audit.py --symbol SYMBOL --valuation-currency ISO_CURRENCY` is explicit and opt-in. Optional canonical security/issuer IDs must be supplied together. The command prints only allowlisted normalized readiness fields and never raw payloads, credentials, environment values, authenticated URLs, current price, valuation, or proprietary response content. Normal pytest does not invoke it.

One bounded META/USD run used the prior canonical target IDs and made one FRED DGS10 request. Risk-free evidence was `ELIGIBLE`: observation 2026-08-27, decimal value 0.0467, USD applicability matched. ERP and eligible beta were unavailable by design, so cost of equity and WACC readiness were `NOT_READY`; no fallback or numeric result was manufactured.

The RR.L/GBP readiness example made no provider request. DGS10 applicability was mismatched and GBP risk-free evidence was `UNAVAILABLE`; cost of equity and WACC were `NOT_READY`. Target identity was intentionally not forced after the prior Fiscal boundary. The command performed no USD fallback, FX conversion, GBP/GBp transformation, DCF, reverse DCF, or current-price access.

## Milestone 9B.1 META/USD live CAPM audit

The existing opt-in `v1_discount_rate_audit.py` command now reads only the approved USD evidence chain. The single bounded META run returned: FRED DGS10 2026-08-27 at 0.0467 (`ELIGIBLE`); NYU Stern adjusted-payout implied ERP 2026-08-01 at 0.0428 (`ELIGIBLE`); and a Yahoo adjusted-history META/SPY OLS beta of 1.2383084543593788 from 60 aligned monthly returns spanning 2021-09-30 through 2026-08-28 (`ELIGIBLE`). Regression alpha was 0.000826314752990628 and R-squared was 0.22288354439053526; low R-squared remained diagnostic only.

Exact CAPM produced decimal cost of equity 0.09969960184658141 (approximately 9.96996%) and status `READY`. WACC prerequisite coverage became `PARTIAL` because cost of equity is complete, but production WACC remains explicitly `NOT_READY` and no numeric WACC exists. Missing evidence remains verified equity market value for weights, market-value debt or approved proxy, pre-tax debt cost, tax rate, weights, preferred-equity treatment, NCI treatment, and final currency alignment. One nonblocking safe issue was counted; no raw ERP page, full price series, provider payload, secret, or current-price valuation input was printed or persisted. The live call was not repeated after success.

## Milestone 9B.2 META/USD WACC audit

`python scripts/v1_wacc_audit.py --symbol META --valuation-currency USD --security-id CANONICAL_SECURITY_ID --issuer-id CANONICAL_ISSUER_ID` is opt-in and allowlisted. It prints normalized evidence/statuses only and is never invoked by pytest or Streamlit.

The first bounded 2026-08-29 run failed safely: the live cost-of-equity refresh and a mistakenly broadened Fiscal bridge were unavailable, so no WACC was produced. The audit nevertheless live-normalized the January 2026 NYU/PwC United States marginal tax rate to 0.2563. The bridge mistake was corrected without repeating the full Yahoo/FRED/NYU run: unverified daily debt routes were removed so they cannot erase verified TEV/market-cap evidence.

One minimum targeted Fiscal re-check then returned `AVAILABLE` standardized actuals (1,147 observations) and bridge evidence (5,479 observations). Latest market cap was USD 1,472,509,548,175 and TEV USD 1,494,567,548,175, both dated 2026-08-28. Gross debt and net debt were unavailable. Canonical EBIT was unavailable; interest expense was available only for FY2011 at USD 42,000,000, so same-period coverage, synthetic rating, debt cost, weights, claims treatment, and numeric WACC were withheld. The approved 9B.1 META cost of equity remains 0.09969960184658141/`READY`, but it was not reconstructed from documentation as if it were a fresh live evidence object. Final production WACC is `NOT_READY`. No raw response, secret, current price, DCF, reverse DCF, FX, aggregation, stance, or UI output was emitted or persisted.
## Milestone 9B.3 bounded WACC evidence audit

Run only after synthetic tests pass:

```powershell
python scripts/v1_wacc_audit.py --symbol META --valuation-currency USD --security-id security:meta:class-a --issuer-id issuer:meta-platforms
```

The opt-in command uses the existing safe discount-rate sources plus Fiscal identity, standardized statements, daily TEV/market-cap bridge, and one period-based `/v1/company/ratios` request for `calculated_total_debt`, `calculated_net_debt`, and `ratio_ebit_to_interest_expense`. Output is limited to canonical IDs, safe capability/status/count summaries, normalized values/dates/currency/units, controlled definitions, and blockers. It never prints `companyKey`, credentials, headers, authenticated URLs, environment values, or raw provider payloads.

The 2026-08-29 META confirmation classified the Fiscal WACC accounting capability as `AVAILABLE` with 3 normalized observations, all old coverage-ratio evidence; latest was FY2011 at 41.8095. Total debt and net debt had zero normalized observations. Company class was `ELIGIBLE` from US domicile, Communication Services / Interactive Media & Services, `operating_company`, and eligible USD market cap. Explicit EBIT was unavailable; canonical interest expense failed closed on conflicting evidence; preferred equity and NCI were unavailable. WACC remained `NOT_READY`. No MSFT/NVDA run was made because generic behavior was already covered synthetically and another live request would not resolve META's provider evidence absence.

## Milestone 10A bounded META reverse-DCF evidence audit

Run only after synthetic tests pass:

```powershell
python scripts/v1_reverse_dcf_audit.py --symbol META
```

The command is explicit and opt-in. It reads canonical Fiscal identity/standardized actuals/`calculated_tev`, canonical FMP annual consensus, and the already approved marginal-tax source. It does not refresh the frozen WACC family and does not request current share price. Output contains only canonical IDs, normalized evidence values/dates/counts/statuses, formulation blockers, and a safe issue count—never raw payloads, credentials, environment values, authenticated URLs, or opaque provider keys.

The final 2026-08-29 META audit resolved Fiscal security `security:fiscal:FSCLS5RRW8UDP864` and issuer `issuer:fiscal:FSCLC8JQP8UCR678`, with USD reporting/valuation currency. Fiscal identity, standardized-actual, enterprise-bridge, and FMP consensus capabilities were `AVAILABLE`. No eligible latest canonical annual actual revenue survived selection, so actual base and actual-to-forward EBIT continuity were `NOT_READY` and FY1 growth remained unavailable. No older period, Operating Income, or fabricated EBIT was substituted.

FMP produced five ready annual periods, FY1 2026-12-31 through FY5 2030-12-31. Average revenue was approximately USD 254.123bn, 305.339bn, 359.750bn, 423.415bn, and 457.177bn; EBIT was 99.716bn, 113.881bn, 134.344bn, 153.317bn, and 165.134bn; EBITDA was 126.831bn, 150.258bn, 180.748bn, 210.804bn, and 233.111bn. Derived EBIT margins were approximately 39.24%, 37.30%, 37.34%, 36.21%, and 36.12%. Consecutive growth from FY2 onward was approximately 20.15%, 17.82%, 17.70%, and 7.97%. Revenue analyst counts were 42, 43, 40, 30, and 30; EBIT/EBITDA counts remained unavailable because FMP does not source them canonically.

Fiscal `calculated_tev` was `READY` at USD 1,494,567,548,175 dated 2026-08-28. The explicit January 2026 United States marginal-tax evidence was `ELIGIBLE` at 0.2563. External FCFF, external FCFE, generic FCF, forward CapEx, forward D&A, forward change-NWC, sales-to-capital, and an approved reinvestment methodology were unavailable; historical reinvestment evidence made the inventory `PARTIAL` but did not authorize a forecast. Production WACC remained frozen `NOT_READY`; terminal growth, terminal margin, and steady-state reinvestment policies remained `NOT_READY`. Every formulation remained `PARTIAL`, overall solver readiness was `NOT_READY`, and the earliest blocker was `ACTUAL_BASE`.

The first invocation exposed only a generic audit result-shape defect (singular identity capability versus plural data capabilities); it was fixed with a synthetic regression. The one confirmation rerun produced the safe result above. A duplicate tax-jurisdiction string comparison was then removed because the frozen canonical tax service had already resolved `US` to `United States of America` and marked the evidence eligible; no further provider request was needed. No solver, PV, terminal value, implied assumption, current price, fair value, aggregation, stance, UI, raw payload, or secret was produced or persisted.

## Milestone 12B flagged-route META integration check

After the complete synthetic suite passed, the default-off route was enabled locally and `META` was submitted once through the production ticker input. The coordinator ran the existing bounded live research-report chain once and failed closed at `IDENTITY`: canonical identity was unavailable during that provider-boundary attempt, so no report was fabricated. The page displayed the controlled stage, the short blocker, a safe-details expander, and one explicit Retry action. No live retry was made, avoiding repeated provider quota consumption after the boundary failure.

The same browser session confirmed that the empty state does not auto-run a symbol. Separate provider-free synthetic previews confirmed the restrained `Building V1 research report...` loading state, successful report handoff, compact ticker control at 1280×720, and report retention on a normal widget rerun without a build. The temporary preview scripts, local tabs, and servers were removed. No raw provider response, credential, authenticated URL, header, environment value, or internal company key appeared in UI or documentation.

## Milestone 12C bounded live and visual checks

After **1,558** normal synthetic tests passed, the generic 12C coordinator was invoked once for `META`. The result was `UNAVAILABLE` at `IDENTITY`, cache `MISS`, report `WITHHELD`, with controlled events for input, failed identity, skipped peer/market data, and pure unavailable publication/comparison assembly. The environment available to this run did not admit any normalized live provider result to the registry (`0` registered semantic fetches, reuses, or duplicates avoided). No retry was made for META.

Per the approved fallback rule, the exact same coordinator was then invoked once for `MSFT`. It produced the same `UNAVAILABLE`/`IDENTITY` provider-configuration boundary and no report. No further live symbols or retries were attempted. Because neither identity resolved, there was no successful/partial end-to-end report or normal-rerun cache observation to report; no prior report or fake identity was substituted.

The production app was launched locally with `ENABLE_V1_REPORT_UI=1` and the isolated V1 empty route was verified. A temporary provider-free preview then visually verified the generic `Building V1 research report...` state, fatal `Stopped at IDENTITY` state, explicit Retry action, retry re-entry into loading, safe technical detail, and compact build diagnostics (`Input — Completed`, `Identity — Failed`, aware/nonnegative timing, truthful cancellation limitation). No request count, URL, key, payload, or exception appeared. The temporary preview, browser tab, and both local servers were removed.

## Milestone 12I bounded cross-stock acceptance

The generic `build_live_stock_research_report` path was invoked exactly once for each required symbol; no verification retry was made. Output was restricted to controlled identity/build statuses, canonical IDs, report section statuses, currency/unit labels, publication availability, scenario-context availability, and normalized fetch/reuse counts.

| Symbol | Identity | Report / market | Own history / peer | Consensus / reverse DCF | Publication / central | Currency and scenario | Fetches / reuses |
|---|---|---|---|---|---|---|---|
| META | Canonical Fiscal identity succeeded | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD, quote unit USD; scenario context available | 8 / 4 |
| MSFT | Canonical Fiscal identity succeeded | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD, quote unit USD; scenario context available | 8 / 4 |
| NVDA | Canonical Fiscal identity succeeded | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD, quote unit USD; scenario context available | 8 / 4 |
| SHEL.L | HTTP and JSON parsing succeeded; normalization failed closed | Withheld / not reached | Not reached | Not reached | Unavailable / withheld | Canonical currency/unit unavailable; no inference or scenario context | 1 / 0 |
| RR.L | HTTP and JSON parsing succeeded; normalization failed closed | Withheld / not reached | Not reached | Not reached | Unavailable / withheld | Canonical currency/unit unavailable; no inference or scenario context | 1 / 0 |

The UK outcome is acceptance evidence, not a smoke-runner crash. The approved resolver consumes one Fiscal company-list result set and maps a canonical `.L` symbol only when exactly one base ticker carries explicit London venue evidence. Neither live UK submission satisfied that proof threshold, so profile enrichment and report construction did not proceed. No further probing was performed and no identity fallback was added.

GBP/GBp behavior was therefore verified deterministically rather than claimed from live UK evidence: explicit quote currency GBP, quote unit GBp, and scale 0.01 normalized a raw 750 quote to GBP 7.50/share; GBP/share valuation remained on that basis; mismatched currencies withheld comparison; `.L` itself performed no division and no FX was fetched. No raw response, request URL, header, credential, environment value, exception representation, or opaque company key was printed or persisted.

## Milestone 12J bounded LSE revalidation

After synthetic success, `scripts/v1_identity_integration_audit.py` was run once for SHEL.L and once for RR.L. The CLI loads project configuration through the approved secret-safe loader and prints only controlled statuses, canonical IDs, currency/unit labels, section states, acquisition counts, and fetch/reuse totals. It never prints request URLs, headers, credentials, raw bodies, environment values, exception representations, or opaque company keys.

| Symbol | Identity | Report / market | Own history / peer | Consensus / reverse DCF | Publication / central | Currency and scenario | Fetches / reuses |
|---|---|---|---|---|---|---|---|
| META | Canonical Fiscal identity succeeded (12I evidence reused; not rerun) | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD reporting and quote; USD unit; scenario available | 8 / 4 |
| MSFT | Canonical Fiscal identity succeeded (12I evidence reused; not rerun) | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD reporting and quote; USD unit; scenario available | 8 / 4 |
| NVDA | Canonical Fiscal identity succeeded (12I evidence reused; not rerun) | Partial / available | Valid / unavailable | Ready / not ready | Unresolved / withheld | USD reporting and quote; USD unit; scenario available | 8 / 4 |
| SHEL.L | Canonical Fiscal identity succeeded; one explicit London listing; stable issuer/security | Partial / available | Unavailable / unavailable | Unavailable / not ready | Unavailable / withheld | USD reporting; GBP quote; GBp unit; 0.01 scale; scenario unavailable | 8 / 4 |
| RR.L | Page-one parse succeeded; exact Fiscal London profile returned HTTP 403; identity withheld | Withheld / not reached | Not reached | Not reached | Unavailable / withheld | Canonical currency/unit and scenario unavailable | 1 / 0 |

SHEL.L acquisition examined the first page plus one exact venue-scoped profile result, found one explicit London candidate, and did not exhaust a bound. It preserved provider metadata rather than inferring from `.L`: reporting currency USD, quote currency GBP, quote unit GBp, and scale 0.01. The same build published market evidence but no unavailable valuation, consensus, reverse-DCF, central, or scenario number.

RR.L received no stable entitled exact-profile identity evidence. The 403 stopped acquisition without pagination fan-out or retry, and Yahoo was not permitted to replace Fiscal. This is a provider access/evidence limitation, so the cutover decision remains `NOT_READY_FOR_DEFAULT`. No live US requests were repeated because the approved 12I matrix plus synthetic page-one regression was sufficient.

## Milestone 12K evidence reuse and release classification

Milestone 12K made zero live provider calls. The approved 12I/12J matrix was reused: META, MSFT, and NVDA remain generic US successes; SHEL.L remains a generic LSE success with correct provider-derived GBP/GBp semantics; RR.L remains one controlled Fiscal access-denied result with no canonical substitution or report fabrication.

Synthetic route acceptance proved that an injected access denial is classified separately from not-found and authentication failure, displays the requested symbol and a safe Identity-stage provider-access message, removes stale report/scenario state, exposes one explicit Retry, performs no automatic retry, and starts exactly one attempt for one Retry action. Under the 12K release policy, this current per-security coverage limitation is not an application portability defect. The final decision is `READY_FOR_DEFAULT`; actual flag enablement remains deferred to 13A.

## Milestone 13A release smoke

After all 1,788 normal tests passed, the canonical command `streamlit run app.py` was exercised locally with release development mode disabled. The default route rendered an empty Stock research report with visible Ticker and Analyse controls, no default symbol, no legacy content, no traceback, and no provider work. The same command with `USE_LEGACY_UI=true` rendered only the visibly labeled legacy interface, an empty ticker form, and the factual no-startup-request state. Both temporary servers and the browser tab were stopped and removed after inspection.

Configured provider presence was checked as booleans only; no value was printed. One permitted META submission was made through the default route and was not retried. It reached canonical identity and rendered a coherent partial report: market available at USD 578.54/share, own-history valid, peer unavailable, five-period consensus ready, reverse DCF not ready, publication unresolved, and overall fair value withheld. This is a successful release smoke; neither peer/reverse-DCF readiness nor a resolved central value is required.

RR.L was not called. The injected `ACCESS_DENIED` release test retained the requested ticker, safe Identity-stage reason, Retry, and empty report state without alternate security or legacy fallback. The provider-free three-input explicit scenario execution/reset/isolation suite remained green. No additional target matrix or live retry was run.
