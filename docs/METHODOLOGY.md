# V1 Methodology

## Research objective

Stock Analyser V1 assembles a reproducible equity-research snapshot from normalized evidence. It is designed to answer three separate questions:

1. What evidence is available for this exact issuer, security, and analysis timestamp?
2. Which valuation methods are semantically eligible on that evidence?
3. What may be published without hiding disagreement or inventing missing inputs?

The application does not convert those answers into an investment recommendation.

## Canonical identity before analysis

Every observation is bound to a canonical issuer and, where relevant, a canonical traded security. Stable provider identifiers and listing evidence establish identity; ticker and company name alone do not.

Issuer identity, security identity, and security eligibility are different facts. A provider can establish a company and listing while omitting security type. In that case identity remains verified but common-equity eligibility is unverified. An issuer classification such as `operating_company` does not prove common equity.

The system fails closed on stable-ID conflicts and does not rewrite tickers, substitute another listing, or match companies by name to rescue a method.

## Evidence normalization

Provider adapters preserve:

- metric definition;
- actual versus estimate;
- annual, quarterly, trailing, or point-in-time frequency;
- economic period and revision chronology;
- observation and retrieval timestamps;
- currency and unit;
- estimate case and analyst count where supplied;
- source metric and provenance.

Quote subunit conversion applies only to explicitly normalized prices. It is not applied to statement values, enterprise value, market capitalization, or shares. No implicit FX conversion is performed.

## Reconciliation and evidence quality

FMP remains canonical for semantically valid annual forward operating consensus. Finnhub and Alpha Vantage are independent validators where their evidence aligns exactly. Validators are compared, not averaged, and cannot replace the canonical series.

Reconciliation requires matching identity, metric, definition, frequency, period, case, currency, unit, and snapshot. Missing validator coverage is absence, not disagreement. Standardized Fiscal actuals and as-reported SEC facts retain their distinct dataset identities.

Readiness is expressed through structured dimensions and blockers. There is no aggregate data-quality score.

## Own-history valuation

The own-history family uses canonical historical multiple observations for the target and a compatible forward denominator. The workflow is:

1. select eligible point-in-time multiple observations inside the approved window;
2. preserve multiple and denominator semantics;
3. apply deterministic outlier and minimum-sample policy;
4. calculate the historical distribution;
5. apply that distribution to the aligned forward denominator;
6. for enterprise multiples, use independently sourced enterprise-to-equity bridge evidence;
7. divide by eligible shares only after identity, currency, date, and unit gates pass.

P/E is not executed when the provider's EPS definition cannot be established as compatible. EV/EBIT is not executed when provider operating profit has not been proven economically equivalent to the selected forward EBIT. Missing facts remain withheld.

## Peer valuation

Peer candidate discovery is not peer selection. Each candidate passes through separate gates:

- canonical issuer and security identity;
- verified common-equity eligibility;
- industry and financial comparability;
- independent economic membership;
- actual enterprise-value evidence;
- compatible forward EBITDA evidence;
- currency, unit, date, and identity alignment.

Forward EBITDA availability does not decide whether a company is economically comparable, and it cannot rescue an invalid peer. Conversely, an included economic peer can remain method-unready. The final peer distribution requires at least three independent included issuers.

## Cross-family publication

Own-history and peer results are the two valuation families. The publication service receives only their upstream ranges and eligibility states. It does not treat reverse DCF, analyst targets, or provider standard DCF as additional families.

- `RESOLVED` means the approved cross-family policy supplied a central value and range.
- `UNRESOLVED` means available family evidence cannot support one central result.
- `WIDE` means dispersion is too broad for an ordinary resolved presentation.
- `UNAVAILABLE` means no family can publish.

Family ranges remain visible even when the overall result is withheld. Market gaps appear only when supplied by the upstream market-comparison service.

## Production WACC evidence

Production WACC is currency-aware and currently implemented only for compatible USD valuation evidence. Its components are independently sourced:

- FRED DGS10 for the USD risk-free rate;
- a dated NYU Stern implied US equity risk premium;
- a five-year/60-month OLS regression beta from compatible adjusted target and SPY return histories, with a 36-observation minimum;
- Fiscal equity market value and eligible gross-debt/accounting evidence;
- NYU Stern synthetic rating/default spread policy;
- NYU Stern/PwC country marginal tax evidence.

The model does not use provider-defined beta by default, reconstruct debt from net debt or total liabilities, assume missing debt is zero, or substitute DGS10 for a non-USD risk-free rate. If method-specific evidence is absent or conflicting, WACC remains unavailable.

## Reverse DCF

The reverse DCF solves for the terminal revenue growth rate implied by the current enterprise-value anchor under a fixed annual operating trajectory and production WACC. It does not create an intrinsic-value target and is not included in family aggregation.

Canonical execution requires compatible identity, analysis snapshot, valuation currency, annual actual base, forward consensus, enterprise-value anchor, tax evidence, WACC, and approved operating bridge inputs. Missing prerequisites are reported as blockers.

## Explicit scenario mode

The UI exposes WACC, sales-to-capital, and preceding annual revenue as explicit scenario inputs. The scenario service reruns the approved reverse-DCF equation against those user-supplied overrides. Results are labeled scenario-only, retain the canonical context for comparison, and do not alter report provenance or publication.

## Report construction and UI

The report builder consumes already completed publication, market comparison, consensus, expectations, reference, provenance, and readiness results. It validates that sections refer to the same target and analysis timestamp. Known valuation families remain present even when unavailable, with `null` values and blockers rather than zero placeholders.

The Streamlit renderer displays the immutable report. It contains no provider access and no valuation, publication, gap, or reverse-DCF arithmetic.

## Fail-closed policy

The system withholds rather than guesses when it encounters:

- unresolved or conflicting identity;
- unsupported security class;
- ambiguous financial definitions;
- mismatched period, snapshot, currency, or unit;
- stale evidence;
- insufficient historical samples or peers;
- absent enterprise-to-equity bridge evidence;
- missing WACC components;
- unsupported sector architecture;
- unavailable, denied, or failed provider capability.

These are research limitations made visible to the user, not errors to conceal with a fallback.

## Scope and limitations

V1 supports conventional listed operating companies when the required provider evidence is available. Banks, insurers, REITs, pre-revenue firms, and binary clinical/regulatory cases need specialist models. Non-USD production WACC remains unavailable until approved currency-specific rate sources exist. Provider coverage and entitlements can leave an otherwise valid security unavailable.

Detailed formulas and policy IDs live in [VALUATION_V1_SPEC.md](VALUATION_V1_SPEC.md); provider implementation evidence lives in [PROVIDER_MATRIX.md](PROVIDER_MATRIX.md).

