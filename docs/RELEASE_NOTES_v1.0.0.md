# Stock Analyser v1.0.0

## Release summary

Version 1.0.0 makes the canonical stock research report the default interface. It completes the V1 path from stable identity and normalized provider evidence through own-history and peer valuation, publication, market comparison, reverse-DCF expectations, explicit scenarios, and a provider-independent Streamlit report.

## Highlights

- Canonical issuer/security identity with venue, security-eligibility, and stable-ID conflict controls.
- Immutable observations for actuals, estimates, market data, valuation, discount rates, and provenance.
- Deterministic cross-provider reconciliation and structured coverage/readiness.
- Own-history valuation using historical multiple distributions and aligned forward denominators.
- Comparable-company EV/EBITDA valuation with separate candidate, economic-membership, and method-readiness gates.
- Central publication states: resolved, unresolved, wide, and unavailable.
- Currency-aware USD production WACC evidence with explicit unavailable states for unsupported currencies or missing components.
- Consensus-anchored reverse DCF and an isolated three-input user scenario mode.
- Provider-independent research-report contracts and a restrained evidence/readiness UI.
- V1 default routing with `USE_LEGACY_UI=true` as the explicit emergency rollback.

## Security and unit handling

- Quote subunits are normalized only for eligible price evidence; financial statements, enterprise value, market capitalization, and shares are not rescaled by quote-unit rules.
- No implicit FX conversion is performed.
- Secrets are loaded lazily and excluded from cache keys, provenance, screenshots, and safe errors.
- Raw provider responses, headers, authenticated URLs, and proprietary payloads are not persisted by normal execution.
- Startup is idle until Analyse is selected.

## Validation

Milestone 13A approved version 1.0.0 after **1,788 provider-free tests passed**. The suite covers domain validation, adapters, provider failure states, reconciliation, identity, valuation, WACC, reverse DCF, report assembly, scenario isolation, Streamlit rendering, startup behavior, and legacy rollback. Normal pytest makes zero live network calls.

## Known limitations

- Live canonical identity currently depends on Fiscal access for the submitted security; access denial is reported rather than bypassed.
- Provider entitlements and company coverage vary.
- Peer candidate financial coverage can leave the peer method unavailable even when candidate identity is known.
- Stable cross-provider identity requirements can withhold FMP peer forward EBITDA.
- Production WACC is currently USD-only and can be withheld by missing or conflicting accounting evidence.
- At least three independent included issuers are required for the peer family.
- Specialist valuation models for banks, insurers, REITs, pre-revenue companies, and binary clinical/regulatory cases are outside V1.
- The retained legacy route remains temporarily available for rollback; it is not the default.

## Run and rollback

Start the release with:

```bash
streamlit run app.py
```

Select the retained legacy route only when required:

```bash
USE_LEGACY_UI=true streamlit run app.py
```

The deprecated `ENABLE_V1_REPORT_UI` variable is ignored.
