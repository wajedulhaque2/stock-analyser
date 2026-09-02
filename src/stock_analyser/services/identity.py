"""Deterministic canonical identity resolution without symbol guessing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from stock_analyser.domain import (
    CompanyIdentity,
    DataAvailability,
    DataIssue,
    IssueSeverity,
    ProviderSymbol,
)
from stock_analyser.providers.contracts import IdentityCandidate
from stock_analyser.providers.identity import ProviderId


@dataclass(frozen=True, slots=True)
class IdentitySeed:
    canonical_symbol: str
    security_id: str
    issuer_id: str


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    identity: CompanyIdentity
    issues: tuple[DataIssue, ...]
    field_sources: tuple[tuple[str, ProviderId], ...]


_FIELDS = (
    "company_name", "issuer_domicile", "listing_country", "exchange", "sector",
    "industry", "security_type", "reporting_currency", "quote_currency",
    "quote_unit", "price_scale", "fiscal_year_end", "underlying_security_id", "adr_ratio",
    "company_type",
)
_DEFAULT_ORDER = (ProviderId.FISCAL, ProviderId.YAHOO, ProviderId.SEC)
_LISTING_ORDER = (ProviderId.YAHOO, ProviderId.FISCAL, ProviderId.SEC)
_DOMICILE_ORDER = (ProviderId.FISCAL, ProviderId.SEC, ProviderId.YAHOO)
_PRECEDENCE = {
    "issuer_domicile": _DOMICILE_ORDER,
    "listing_country": _LISTING_ORDER,
    "exchange": _LISTING_ORDER,
    "security_type": _LISTING_ORDER,
    "quote_currency": _LISTING_ORDER,
    "quote_unit": _LISTING_ORDER,
    "price_scale": _LISTING_ORDER,
}


def _ordered(candidates: tuple[IdentityCandidate, ...], field_name: str) -> tuple[IdentityCandidate, ...]:
    order = _PRECEDENCE.get(field_name, _DEFAULT_ORDER)
    rank = {provider: index for index, provider in enumerate(order)}
    return tuple(sorted(candidates, key=lambda item: (rank.get(item.provider, len(rank)), item.provider.value)))


def resolve_company_identity(seed: IdentitySeed, candidates: Iterable[IdentityCandidate]) -> IdentityResolution:
    """Resolve field-by-field using fixed precedence and surface disagreements."""
    items = tuple(candidates)
    if not items:
        raise ValueError("at least one identity candidate is required")
    duplicate_providers = {item.provider for item in items if sum(x.provider is item.provider for x in items) > 1}
    if duplicate_providers:
        names = ", ".join(sorted(item.value for item in duplicate_providers))
        raise ValueError(f"at most one identity candidate per provider is allowed: {names}")

    values: dict[str, object] = {}
    sources: list[tuple[str, ProviderId]] = []
    issues: list[DataIssue] = []
    for field_name in _FIELDS:
        available = tuple(item for item in _ordered(items, field_name) if getattr(item, field_name) is not None)
        if not available:
            continue
        selected = available[0]
        selected_value = getattr(selected, field_name)
        values[field_name] = selected_value
        sources.append((field_name, selected.provider))
        distinct = {repr(getattr(item, field_name)) for item in available}
        if len(distinct) > 1:
            issues.append(DataIssue(
                severity=IssueSeverity.WARNING,
                metric=f"identity.{field_name}",
                provider=selected.provider.value,
                reason=f"providers disagree on {field_name}; deterministic precedence selected {selected.provider.value}",
                observed=", ".join(f"{item.provider.value}={getattr(item, field_name)!r}" for item in available),
                action="retain the selected canonical field and expose the disagreement",
            ))

    required = (
        "company_name", "issuer_domicile", "listing_country", "exchange", "sector", "industry",
        "security_type", "reporting_currency", "quote_currency", "quote_unit", "price_scale", "fiscal_year_end",
    )
    missing = [name for name in required if name not in values]
    if missing:
        raise ValueError(f"identity candidates do not provide required fields: {', '.join(missing)}")

    is_depositary = "adr" in str(values["security_type"]).lower() or "depositary" in str(values["security_type"]).lower()
    availability = DataAvailability.AVAILABLE
    if is_depositary and values.get("adr_ratio") is None:
        availability = DataAvailability.PARTIAL
        issues.append(DataIssue(
            severity=IssueSeverity.WARNING,
            metric="identity.adr_ratio",
            provider="identity_resolver",
            reason="depositary-receipt ratio is unknown and was not defaulted",
            expected="an explicit positive ADR ratio",
            observed="missing",
            action="retain partial identity until a sourced ratio is available",
        ))

    identity = CompanyIdentity(
        canonical_symbol=seed.canonical_symbol,
        security_id=seed.security_id,
        issuer_id=seed.issuer_id,
        provider_symbols=tuple(
            ProviderSymbol(item.provider.value, item.provider_symbol)
            for item in sorted(items, key=lambda item: item.provider.value)
        ),
        identity_availability=availability,
        **values,
    )
    return IdentityResolution(identity=identity, issues=tuple(issues), field_sources=tuple(sources))
