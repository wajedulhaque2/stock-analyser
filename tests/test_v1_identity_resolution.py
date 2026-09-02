from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_analyser.domain import DataAvailability, MetricId
from stock_analyser.providers import IdentityCandidate, ProviderId
from stock_analyser.services import IdentitySeed, resolve_company_identity


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)


def candidate(provider=ProviderId.YAHOO, symbol="Y-SYN", **changes):
    values = dict(
        provider=provider,
        provider_symbol=symbol,
        retrieved_at=NOW,
        company_name="Synthetic plc",
        issuer_domicile="GB",
        listing_country="GB",
        exchange="Synthetic London Exchange",
        sector="Industrials",
        industry="Synthetic Engineering",
        security_type="Ordinary share",
        reporting_currency="GBP",
        quote_currency="GBP",
        quote_unit="GBp",
        price_scale=0.01,
        fiscal_year_end="12-31",
    )
    values.update(changes)
    return IdentityCandidate(**values)


def seed(symbol="CANON"):
    return IdentitySeed(canonical_symbol=symbol, security_id="security-1", issuer_id="issuer-1")


def test_usd_identity_and_provider_symbol_are_explicit():
    result = resolve_company_identity(seed("CANON-US"), [candidate(
        symbol="YAHOO-US", issuer_domicile="US", listing_country="US",
        reporting_currency="USD", quote_currency="USD", quote_unit="USD",
        price_scale=1.0,
    )])
    assert result.identity.canonical_symbol == "CANON-US"
    assert result.identity.provider_symbols[0].symbol == "YAHOO-US"
    assert result.identity.provider_symbols[0].symbol != result.identity.canonical_symbol
    assert result.identity.price_scale == 1.0


def test_identity_precedence_is_field_specific_and_conflicts_are_issues():
    yahoo = candidate(exchange="Yahoo Exchange", reporting_currency="USD", quote_currency="USD", quote_unit="USD", price_scale=1)
    fiscal = candidate(
        ProviderId.FISCAL, "F-SYN", exchange="Fiscal Exchange", reporting_currency="EUR",
        quote_currency=None, quote_unit=None, price_scale=None,
    )
    result = resolve_company_identity(seed(), [fiscal, yahoo])
    assert result.identity.exchange == "Yahoo Exchange"
    assert result.identity.reporting_currency == "EUR"
    assert any(issue.metric == "identity.exchange" for issue in result.issues)
    # Reporting and quote currencies describe different facts and are allowed to differ.
    assert result.identity.reporting_currency == "EUR"
    assert result.identity.quote_currency == "USD"


def test_missing_provider_candidate_does_not_invalidate_identity():
    identity = resolve_company_identity(seed(), [candidate()]).identity
    assert {item.provider for item in identity.provider_symbols} == {"yahoo"}


def test_london_quote_scale_does_not_modify_identity_financial_or_share_facts():
    identity = resolve_company_identity(seed(), [candidate()]).identity
    assert identity.normalize_quote_price(585.0) == pytest.approx(5.85)
    revenue = 1_000_000.0
    shares = 250_000.0
    assert revenue == 1_000_000.0
    assert shares == 250_000.0
    assert MetricId.REVENUE is not MetricId.SHARE_PRICE


def test_adr_identity_distinguishes_security_issuer_underlying_and_currencies():
    adr = resolve_company_identity(seed("ADR-CANON"), [candidate(
        security_type="ADR",
        issuer_domicile="TW",
        listing_country="US",
        reporting_currency="TWD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1,
        underlying_security_id="ordinary-security-99",
        adr_ratio=5,
    )]).identity
    assert adr.security_id == "security-1"
    assert adr.issuer_id == "issuer-1"
    assert adr.underlying_security_id == "ordinary-security-99"
    assert adr.adr_ratio == 5
    assert adr.reporting_currency == "TWD" and adr.quote_currency == "USD"


def test_unknown_adr_ratio_stays_none_and_marks_identity_partial():
    result = resolve_company_identity(seed("ADR-CANON"), [candidate(
        security_type="Depositary receipt",
        underlying_security_id="ordinary-security-99",
        adr_ratio=None,
    )])
    assert result.identity.adr_ratio is None
    assert result.identity.identity_availability is DataAvailability.PARTIAL
    assert any(issue.metric == "identity.adr_ratio" for issue in result.issues)


def test_canonical_seed_identifiers_cannot_be_overwritten_by_provider_metadata():
    result = resolve_company_identity(seed("APP-ID"), [candidate(provider_issuer_id="provider-issuer")])
    assert result.identity.canonical_symbol == "APP-ID"
    assert result.identity.issuer_id == "issuer-1"


def test_duplicate_provider_candidates_fail_deterministically():
    with pytest.raises(ValueError, match="at most one"):
        resolve_company_identity(seed(), [candidate(symbol="ONE"), candidate(symbol="TWO")])
