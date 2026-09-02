# V1 Data Sources

## Source policy

V1 assigns providers narrow capabilities. A provider is not a universal fallback, and an accessible field is not automatically eligible for valuation. Identity, definition, period, timestamp, currency, unit, and provenance gates apply before evidence enters a method.

Provider availability is evaluated per capability. Missing configuration, authentication failure, access denial, entitlement lock, empty coverage, rate limiting, and provider failure remain distinct controlled outcomes.

## Provider roles

### Fiscal.ai

Approved roles include canonical Fiscal issuer/security identity, standardized actual financials, historical ratio observations, daily enterprise-value and market-cap evidence, shares evidence, peer candidates, and selected company research resources when the configured credential and security permit access.

Fiscal stable identifiers establish identity; opaque `companyKey` values remain lookup metadata and never appear in safe UI or documentation output. Standardized actuals retain period, currency, source metric, and revision evidence. The system does not construct absent EBITDA, CapEx, debt, or EBIT from convenient neighboring fields.

Fiscal peer profile candidates remain candidates until security eligibility and economic comparability pass independently. Candidate financial endpoint access has proven uneven in live audits; access failure does not authorize ticker matching or provider substitution.

### Yahoo Finance / yfinance

Approved V1 roles include traded-security listing metadata, normalized current market price, historical price/volume evidence, and compatible adjusted return histories used by regression beta. Yahoo can also provide selected market observations where explicitly named by policy.

Quote-unit handling is explicit, including GBp/GBX to GBP for price evidence. The scale is not applied to statements, shares, enterprise value, or market capitalization. Yahoo identity evidence does not overwrite Fiscal stable identifiers, and Yahoo accounting fields are not used to reconstruct missing canonical Fiscal enterprise bridges.

Yahoo/yfinance is suitable for local research and demonstration; it is not an institutional market-data feed.

### Financial Modeling Prep

FMP is the canonical provider for semantically valid annual forward operating consensus: revenue, EBIT, EBITDA, generic net income, and generic EPS low/average/high observations where available. Period, reporting currency, estimate case, metric definition, and stable identity binding must pass before use.

FMP analyst targets and provider standard DCF are reference-only with zero aggregation weight. Generic EPS is not relabeled diluted EPS. External provider DCF is never treated as the application's own valuation.

FMP identity binding for Fiscal-discovered peers remains fail-closed where a stable cross-provider identifier is absent; ticker and company name are insufficient.

### Finnhub

Finnhub supplies independent validation and documented capability-specific cash-flow evidence where configured and entitled. It does not replace canonical FMP forward consensus.

Generic HTTP 403 remains an unclassified transport access failure. Only the Finnhub adapter can translate a 403 on a documented premium operating-estimate path into a locked entitlement outcome. Undocumented cash-flow paths are not probed.

### Alpha Vantage

Alpha Vantage supplies independent annual/quarterly revenue and generic-EPS estimates plus revision evidence where the provider returns it. It remains a validator/reference source; its values are not averaged into canonical FMP consensus.

Live normalization has verified the provider's unified estimate collection and separate revision records. Missing revenue revisions are not inferred from EPS revision fields.

### SEC EDGAR

SEC supplies independent US ticker-to-CIK identity support, filing metadata, as-reported company facts, and bounded reported-actual validation. It is used to cross-check reported evidence, not to supply market price or forecasts.

Standardized Fiscal observations and as-reported SEC observations retain their distinct dataset identities. SEC taxonomy differences are not resolved by averaging.

### FRED

FRED DGS10 supplies the USD risk-free-rate observation. Provider percent values are normalized to canonical decimals and must pass the analysis snapshot and seven-calendar-day freshness policy.

DGS10 is not used for GBP, EUR, JPY, or another non-USD valuation currency. A missing FRED observation or credential leaves that WACC input unavailable.

### NYU Stern / Aswath Damodaran

Official dated NYU Stern sources supply:

- the selected US implied equity-risk-premium series;
- the large non-financial synthetic rating/default-spread table; and
- country marginal corporate tax rates derived from the referenced PwC source.

The application selects only the explicitly approved series, table scope, interval, currency/market, and freshness policy. It does not interpolate, pick the nearest rating, or fall back to a hard-coded constant.

## Central, validator, and reference evidence

| Evidence | Canonical/central source | Validator or reference | Important constraint |
|---|---|---|---|
| Canonical Fiscal identity | Fiscal.ai | Yahoo listing evidence where explicitly compatible | No ticker/name-only rescue |
| Current market price | Yahoo | Other providers only where separately approved | Explicit currency and quote unit |
| Standardized actuals | Fiscal.ai | SEC as-reported checks for US issuers | Dataset identity preserved |
| Annual forward consensus | FMP | Finnhub and Alpha Vantage | Exact semantic alignment; no averaging |
| Historical multiples | Fiscal.ai | None | Point-in-time history; no fabricated period end |
| Peer candidates | Fiscal.ai | Canonical metadata/fundamentals | Discovery is not inclusion |
| USD risk-free rate | FRED DGS10 | None | USD only, freshness-gated |
| US implied ERP/default spread/tax | NYU Stern | Source-specific cross-checks | Dated and scope-gated |
| Analyst targets/provider DCF | FMP | Other provider references where available | Reference only, zero aggregation weight |
| Reported US actuals | SEC | Fiscal standardized actuals | Verification rather than forecast |

## Configuration

The supported local variables are:

```text
FISCAL_API_KEY
FMP_API_KEY
FINNHUB_API_KEY
FRED_API_KEY
ALPHAVANTAGE_API_KEY
USE_LEGACY_UI
```

Only `FISCAL_API_KEY` is required for the live canonical Fiscal identity path after a ticker is submitted. The other provider keys are capability-specific. `USE_LEGACY_UI` is not a secret.

Credentials are loaded lazily. They never enter cache keys, safe errors, provenance, screenshots, or repository documentation. The committed `.env.example` contains names and blank placeholders only.

## Live and synthetic boundaries

Normal pytest is synthetic and makes zero network calls. Explicit utilities in `scripts/` perform bounded live verification and emit only allowlisted summaries. Raw provider responses, request headers, authenticated URLs, secrets, and proprietary payloads are not persisted.

For endpoint-level verification history, status semantics, and detailed capability matrices, see [PROVIDER_MATRIX.md](PROVIDER_MATRIX.md) and [LIVE_SMOKE_TESTING.md](LIVE_SMOKE_TESTING.md).

