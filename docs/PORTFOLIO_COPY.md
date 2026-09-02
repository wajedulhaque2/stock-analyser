# Portfolio Copy

## Short description

Stock Analyser is a local Python/Streamlit equity-research application that turns multi-provider financial evidence into a canonical, provenance-aware valuation report. It fails closed when identity, units, definitions, comparability, or provider coverage are insufficient rather than inventing a result.

## Medium description

Stock Analyser V1 is an evidence-aware public-equity research system built in Python and Streamlit. It normalizes identity, market, actual, consensus, peer, discount-rate, and reference evidence from multiple providers into immutable domain contracts. Deterministic services produce own-history and peer valuations, publish only eligible cross-family results, and use reverse DCF to expose market-implied expectations. The interface keeps unavailable data, blockers, provenance, currencies, and quote units visible instead of substituting zeros or silent fallbacks. A 1,788-test provider-free baseline covers contracts, provider normalization, reconciliation, valuation, orchestration, scenarios, UI rendering, and release routing.

## Tech stack

- Python 3
- Streamlit
- dataclasses and immutable domain contracts
- pandas and NumPy
- SciPy
- Altair and Plotly
- requests and Beautiful Soup
- yfinance
- pytest

## Key highlights

- Designed a provider-independent financial domain with explicit identity, period, unit, currency, revision, and provenance semantics.
- Built deterministic own-history and comparable-company valuation pipelines with fail-closed evidence gates.
- Separated canonical valuation publication, current-price comparison, reverse-DCF expectations, and user scenarios.
- Implemented capability-level provider errors and safe redaction without persisting raw responses or credentials.
- Preserved research uncertainty through structured unavailable, partial, unresolved, and wide states.
- Validated the release through a large synthetic, network-disabled regression suite and bounded opt-in live audits.

## Repository CTA

View source, architecture, methodology, and release evidence.

## CV / LinkedIn bullets

- Architected a Python/Streamlit public-equity research application that normalizes multi-provider identity, market, financial, consensus, and macro evidence into immutable, provenance-aware contracts.
- Implemented deterministic own-history and peer EV/EBITDA valuation workflows, cross-family publication policy, and reverse-DCF market-expectations analysis with explicit currency, unit, freshness, and comparability gates.
- Designed fail-closed provider and data-quality controls that preserve unavailable/conflicting states, isolate scenario inputs, and prevent silent ticker, provider, accounting-definition, or FX fallbacks.
- Built and maintained a provider-free pytest suite covering 1,788 release-baseline cases across adapters, reconciliation, valuation, orchestration, UI rendering, security redaction, and default/rollback routing.

