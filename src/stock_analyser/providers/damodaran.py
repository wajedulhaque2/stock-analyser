"""Bounded NYU Stern adapter for the dated US implied ERP headline."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser
from html import unescape
import re
from typing import Protocol

from stock_analyser.domain import (
    CapabilityResult,
    CapabilityStatus,
    DataIssue,
    CompanyClassScope,
    EquityRiskPremiumObservation,
    IssueSeverity,
    MetricUnit,
    MarginalTaxRateObservation,
    Provenance,
    SyntheticRatingBand,
    stable_wacc_evidence_id,
    stable_erp_observation_id,
)

from .base import BaseProviderAdapter
from .contracts import (
    ProviderCapability, ProviderErpResult, ProviderMarginalTaxResult,
    ProviderSyntheticRatingResult,
)
from .identity import ProviderId


DAMODARAN_HOME_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
DAMODARAN_RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.htm"
DAMODARAN_TAX_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/countrytaxrates.html"
RATINGS_SOURCE_DATE = date(2026, 1, 9)
US_IMPLIED_ERP_METHOD = "Implied ERP - trailing 12 month with adjusted payout"
US_IMPLIED_ERP_MARKET_SCOPE = "US broad equity market"
US_IMPLIED_ERP_RISK_FREE_CONVENTION = "Full observed US Treasury risk-free rate; no sovereign-default-spread subtraction"


class DamodaranDataSource(Protocol):
    def current_us_implied_erp_page(self) -> str: ...
    def current_synthetic_rating_page(self) -> str: ...
    def current_country_tax_page(self) -> str: ...


_HEADLINE_RE = re.compile(
    r"Implied ERP on\s+(?P<date>[A-Za-z]+\s+\d{1,2},\s+\d{4})\s*=\s*"
    r"(?P<percent>\d+(?:\.\d+)?)%\s*"
    r"\(Trailing 12 month,\s*with adjusted payout\)",
    re.IGNORECASE,
)


def _visible_text(payload: str) -> str:
    text = unescape(re.sub(r"(?is)<[^>]+>", " ", payload))
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    return re.sub(r"\s+", " ", text).strip()


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, ...]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", unescape("".join(self._cell))).strip())
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            self.rows.append(tuple(self._row))
            self._row = None


def _table_rows(payload: str) -> tuple[tuple[str, ...], ...]:
    parser = _TableParser()
    parser.feed(payload)
    return tuple(parser.rows)


def _number(text: str) -> float:
    return float(text.strip().replace(",", ""))


def _percent(text: str) -> float:
    stripped = text.strip()
    if not stripped.endswith("%"):
        raise ValueError("percentage marker is required")
    return _number(stripped[:-1]) / 100.0


class DamodaranAdapter(BaseProviderAdapter):
    provider = ProviderId.DAMODARAN

    def __init__(self, source: DamodaranDataSource, *, clock=None) -> None:
        super().__init__(clock=clock)
        self._source = source

    def _probe_capability(self, capability: str) -> CapabilityResult:
        return self.capability(capability)

    def fetch_us_implied_erp(self) -> ProviderErpResult:
        capability_name = ProviderCapability.US_EQUITY_RISK_PREMIUM.value
        retrieved_at = self._clock()
        try:
            payload = self._source.current_us_implied_erp_page()
        except Exception:
            capability = self.record_capability(
                capability_name, CapabilityStatus.ERROR,
                reason="NYU Stern implied ERP source retrieval failed",
            )
            return ProviderErpResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="equity_risk_premium",
                provider=self.provider.value, reason="NYU Stern implied ERP source retrieval failed",
            ),))
        if not isinstance(payload, str) or not payload.strip():
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="NYU Stern implied ERP page was empty",
            )
            return ProviderErpResult(capabilities=(capability,))
        match = _HEADLINE_RE.search(_visible_text(payload))
        if match is None:
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="The fixed adjusted-payout implied ERP headline could not be parsed",
            )
            return ProviderErpResult(capabilities=(capability,), issues=(DataIssue(
                severity=IssueSeverity.ERROR, metric="equity_risk_premium",
                provider=self.provider.value,
                reason="NYU Stern page did not match the approved adjusted-payout ERP schema",
                action="withhold ERP rather than select an alternative series or default",
            ),))
        try:
            source_date = datetime.strptime(match.group("date"), "%B %d, %Y").date()
            value = float(match.group("percent")) / 100.0
            source_as_of = datetime.combine(source_date, datetime.min.time(), tzinfo=timezone.utc)
            provenance = Provenance(
                provider=self.provider.value,
                endpoint_or_dataset="nyu_stern_current_implied_erp_headline",
                provider_symbol="US_IMPLIED_ERP_T12M_ADJUSTED_PAYOUT",
                retrieved_at=retrieved_at,
                as_of_at=source_as_of,
                transformation_steps=(
                    "selected only the dated trailing-12-month adjusted-payout headline",
                    "divided source percentage by 100 to canonical decimal rate",
                    "retained the source full-US-Treasury risk-free convention without default-spread adjustment",
                ),
                source_metric=US_IMPLIED_ERP_METHOD,
            )
            observation = EquityRiskPremiumObservation(
                observation_id=stable_erp_observation_id(
                    "damodaran_nyu_stern", source_date.isoformat(), value, US_IMPLIED_ERP_METHOD,
                ),
                value=value, unit=MetricUnit.PERCENT_DECIMAL, applicable_currency="USD",
                market_scope=US_IMPLIED_ERP_MARKET_SCOPE, source_name="Aswath Damodaran / NYU Stern",
                source_dataset=DAMODARAN_HOME_URL, source_date=source_date,
                source_as_of=source_as_of, retrieved_at=retrieved_at,
                methodology_label=US_IMPLIED_ERP_METHOD,
                risk_free_convention=US_IMPLIED_ERP_RISK_FREE_CONVENTION,
                provenance=provenance,
            )
        except (TypeError, ValueError, OverflowError):
            capability = self.record_capability(
                capability_name, CapabilityStatus.UNAVAILABLE,
                reason="The fixed adjusted-payout ERP headline had invalid date or value semantics",
            )
            return ProviderErpResult(capabilities=(capability,))
        capability = self.record_capability(capability_name, CapabilityStatus.AVAILABLE)
        return ProviderErpResult(observations=(observation,), capabilities=(capability,))

    def fetch_large_nonfinancial_rating_bands(self) -> ProviderSyntheticRatingResult:
        """Normalize only the first, large non-financial table; never the financial table."""
        capability_name = ProviderCapability.SYNTHETIC_RATING_DEFAULT_SPREADS.value
        retrieved_at = self._clock()
        try:
            payload = self._source.current_synthetic_rating_page()
            rows = _table_rows(payload)
        except Exception:
            capability = self.record_capability(capability_name, CapabilityStatus.ERROR,
                                                reason="NYU Stern synthetic-rating source retrieval failed")
            return ProviderSyntheticRatingResult(capabilities=(capability,))
        observations: list[SyntheticRatingBand] = []
        for cells in rows:
            # The official sheet places large non-financial columns first and the
            # separate financial-company columns later in the same row.
            cleaned = tuple(cell for cell in cells if cell)
            if len(cleaned) < 4:
                continue
            try:
                lower, upper = _number(cleaned[0]), _number(cleaned[1])
                rating, spread = cleaned[2], _percent(cleaned[3])
            except (TypeError, ValueError, OverflowError):
                continue
            source_as_of = datetime.combine(RATINGS_SOURCE_DATE, datetime.min.time(), tzinfo=timezone.utc)
            provenance = Provenance(
                provider=self.provider.value,
                endpoint_or_dataset="nyu_stern_ratings_interest_coverage_default_spreads",
                provider_symbol="LARGE_NON_FINANCIAL_SERVICE_FIRMS",
                retrieved_at=retrieved_at, as_of_at=source_as_of,
                transformation_steps=(
                    "selected only the official large non-financial service-firm coverage columns",
                    "normalized source percentage spread to canonical decimal rate",
                    "retained sourced lower-greater-than and upper-less-than-or-equal boundary semantics",
                ),
                source_metric="interest coverage ratio to synthetic rating and default spread",
            )
            observations.append(SyntheticRatingBand(
                evidence_id=stable_wacc_evidence_id("ratingband", lower, upper, rating, spread, RATINGS_SOURCE_DATE),
                coverage_lower_bound=lower, coverage_upper_bound=upper,
                lower_inclusive=False, upper_inclusive=True,
                synthetic_rating=rating, default_spread=spread,
                source_date=RATINGS_SOURCE_DATE,
                company_class_scope=CompanyClassScope.LARGE_US_NON_FINANCIAL_OPERATING,
                source_name="Aswath Damodaran / NYU Stern", source_dataset=DAMODARAN_RATINGS_URL,
                provenance=provenance,
            ))
        if not observations:
            capability = self.record_capability(capability_name, CapabilityStatus.UNAVAILABLE,
                                                reason="approved large non-financial rating rows could not be parsed")
            return ProviderSyntheticRatingResult(capabilities=(capability,))
        capability = self.record_capability(capability_name, CapabilityStatus.AVAILABLE)
        return ProviderSyntheticRatingResult(tuple(observations), (capability,))

    def fetch_country_marginal_tax_rates(self) -> ProviderMarginalTaxResult:
        capability_name = ProviderCapability.COUNTRY_MARGINAL_TAX_RATES.value
        retrieved_at = self._clock()
        try:
            payload = self._source.current_country_tax_page()
            visible = _visible_text(payload)
            match = re.search(r"From\s*:\s*(January\s+\d{4})\s+Update", visible, re.IGNORECASE)
            if match is None or not re.search(r"Source\s*:\s*PWC", visible, re.IGNORECASE):
                raise ValueError("dated PwC source label missing")
            source_date = datetime.strptime(match.group(1), "%B %Y").date().replace(day=1)
            rows = _table_rows(payload)
        except Exception:
            capability = self.record_capability(capability_name, CapabilityStatus.ERROR,
                                                reason="NYU Stern marginal-tax source retrieval or schema validation failed")
            return ProviderMarginalTaxResult(capabilities=(capability,))
        observations: list[MarginalTaxRateObservation] = []
        for cells in rows:
            cleaned = tuple(cell for cell in cells if cell)
            if len(cleaned) < 2:
                continue
            try:
                jurisdiction, value = cleaned[0], _percent(cleaned[1])
            except (TypeError, ValueError, OverflowError):
                continue
            if not jurisdiction or jurisdiction.lower() == "country":
                continue
            source_as_of = datetime.combine(source_date, datetime.min.time(), tzinfo=timezone.utc)
            provenance = Provenance(
                provider=self.provider.value,
                endpoint_or_dataset="nyu_stern_country_marginal_tax_rates",
                provider_symbol=jurisdiction,
                retrieved_at=retrieved_at, as_of_at=source_as_of,
                transformation_steps=("selected corporate marginal tax rate, not effective tax rate",
                                      "normalized source percentage to canonical decimal rate"),
                source_metric="Corporate Tax Rate",
            )
            observations.append(MarginalTaxRateObservation(
                observation_id=stable_wacc_evidence_id("taxobs", jurisdiction, source_date, value),
                jurisdiction=jurisdiction, value=value, source_date=source_date,
                source_name="Aswath Damodaran / NYU Stern", source_dataset=DAMODARAN_TAX_URL,
                underlying_source="PwC", provenance=provenance,
            ))
        if not observations:
            capability = self.record_capability(capability_name, CapabilityStatus.UNAVAILABLE,
                                                reason="dated country marginal-tax rows could not be parsed")
            return ProviderMarginalTaxResult(capabilities=(capability,))
        capability = self.record_capability(capability_name, CapabilityStatus.AVAILABLE)
        return ProviderMarginalTaxResult(tuple(observations), (capability,))
