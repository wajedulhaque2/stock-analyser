"""Immutable prerequisites for future own-history multiple application.

These contracts deliberately contain no valuation output.  They make semantic
alignment, forward-period selection, and the current capital bridge auditable
before any later milestone is allowed to apply a multiple.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math
import re

from .enums import (
    CapitalStructureCompleteness,
    DenominatorCompatibilityStatus,
    EnterpriseBridgeMethod,
    EnterpriseAdjustmentRequirement,
    EstimateCase,
    ForwardPeriodSelection,
    HistoricalMultipleType,
    HistoricalStatistic,
    HistoricalValuationDenominator,
    HistoricalWindow,
    MetricId,
    OwnHistoryMethodStatus,
    ShareCountBasis,
    ShareCountSemantics,
    ValuationBasis,
    ValuationMethodStatus,
)
from .models import (
    DataIssue,
    Provenance,
    ValuationResult,
    _require_text,
    _validate_aware_datetime,
    _validate_currency,
)


_ALIGNMENT_ID_RE = re.compile(r"^denalign:[0-9a-f]{32}$")
_SELECTION_ID_RE = re.compile(r"^fwdsel:[0-9a-f]{32}$")
_CAPITAL_ID_RE = re.compile(r"^capsnap:[0-9a-f]{32}$")
_DIRECT_BRIDGE_ID_RE = re.compile(r"^directbridge:[0-9a-f]{32}$")
_READINESS_ID_RE = re.compile(r"^ownready:[0-9a-f]{32}$")
_VALUATION_POINT_ID_RE = re.compile(r"^ownpoint:[0-9a-f]{32}$")
_VALUATION_RESULT_ID_RE = re.compile(r"^ownvalue:[0-9a-f]{32}$")


def _stable_id(prefix: str, *parts: object) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


@dataclass(frozen=True, slots=True)
class DenominatorSemanticEvidence:
    """Explicit evidence that may verify or reject one economic pairing."""

    evidence_id: str
    historical_multiple_type: HistoricalMultipleType
    historical_denominator: HistoricalValuationDenominator
    forward_metric_id: MetricId
    compatibility_status: DenominatorCompatibilityStatus
    historical_definition: str
    forward_definition: str
    reason: str
    source_references: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        for value, enum_type, name in (
            (self.historical_multiple_type, HistoricalMultipleType, "historical_multiple_type"),
            (self.historical_denominator, HistoricalValuationDenominator, "historical_denominator"),
            (self.forward_metric_id, MetricId, "forward_metric_id"),
            (self.compatibility_status, DenominatorCompatibilityStatus, "compatibility_status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        for name in (
            "evidence_id", "historical_definition", "forward_definition", "reason", "policy_id"
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        if self.compatibility_status not in {
            DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT,
            DenominatorCompatibilityStatus.INCOMPATIBLE,
        }:
            raise ValueError("semantic evidence may establish only verified equivalence or incompatibility")
        references = tuple(_require_text(value, "source_reference") for value in self.source_references)
        if not references or len(set(references)) != len(references):
            raise ValueError("semantic evidence requires unique source references")
        object.__setattr__(self, "source_references", references)


@dataclass(frozen=True, slots=True)
class ForwardDenominatorSelection:
    """One explicit annual forward metric/case selection; FY1 is never NTM."""

    selection_id: str
    period_selection: ForwardPeriodSelection
    estimate_case: EstimateCase
    forward_metric_id: MetricId
    observation_id: str | None
    fiscal_year: int | None
    period_start: date | None
    period_end: date | None
    currency: str | None
    provider: str | None
    provider_symbol: str | None
    provenance: tuple[Provenance, ...] = ()
    available_estimate_cases: tuple[EstimateCase, ...] = ()
    available_observation_ids: tuple[str, ...] = ()
    reason: str | None = None
    is_ntm: bool = False
    scenarios_created: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.selection_id, str) or not _SELECTION_ID_RE.fullmatch(self.selection_id):
            raise ValueError("selection_id must be a stable forward-selection identifier")
        if not isinstance(self.period_selection, ForwardPeriodSelection):
            raise TypeError("period_selection must use ForwardPeriodSelection")
        if not isinstance(self.estimate_case, EstimateCase):
            raise TypeError("estimate_case must use EstimateCase")
        if not isinstance(self.forward_metric_id, MetricId):
            raise TypeError("forward_metric_id must use MetricId")
        if self.estimate_case is EstimateCase.NOT_APPLICABLE:
            raise ValueError("forward selection requires an explicit estimate case")
        if self.is_ntm or self.scenarios_created:
            raise ValueError("FY selections are not NTM and estimate ranges are not scenarios")
        object.__setattr__(self, "provenance", tuple(self.provenance))
        cases = tuple(dict.fromkeys(self.available_estimate_cases))
        if any(case is EstimateCase.NOT_APPLICABLE for case in cases):
            raise ValueError("available forward cases cannot include NOT_APPLICABLE")
        object.__setattr__(self, "available_estimate_cases", cases)
        ids = tuple(_require_text(value, "available_observation_id") for value in self.available_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("available_observation_ids must be unique")
        object.__setattr__(self, "available_observation_ids", ids)
        if self.observation_id is None:
            if any(value is not None for value in (
                self.fiscal_year, self.period_start, self.period_end, self.currency,
                self.provider, self.provider_symbol,
            )):
                raise ValueError("an unavailable forward selection cannot carry selected observation details")
            object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        else:
            object.__setattr__(self, "observation_id", _require_text(self.observation_id, "observation_id"))
            if self.fiscal_year is None or self.period_start is None or self.period_end is None:
                raise ValueError("an available forward selection requires complete annual period identity")
            if self.period_start > self.period_end:
                raise ValueError("forward period_start must not follow period_end")
            _validate_currency(self.currency, "currency")
            object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
            object.__setattr__(self, "provider_symbol", _require_text(self.provider_symbol, "provider_symbol"))
            if self.observation_id not in ids:
                raise ValueError("selected observation must be included in available_observation_ids")
            if len(self.provenance) != 1:
                raise ValueError("an available forward selection requires exactly one provenance record")


@dataclass(frozen=True, slots=True)
class ForwardDenominatorAlignment:
    """Machine-readable historical-to-forward economic compatibility decision."""

    alignment_id: str
    historical_multiple_type: HistoricalMultipleType
    historical_denominator: HistoricalValuationDenominator
    forward_metric_id: MetricId
    compatibility_status: DenominatorCompatibilityStatus
    reason: str
    historical_definition: str
    forward_definition: str
    supporting_distribution_id: str | None
    supporting_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    policy_id: str
    semantic_evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.alignment_id, str) or not _ALIGNMENT_ID_RE.fullmatch(self.alignment_id):
            raise ValueError("alignment_id must be a stable denominator-alignment identifier")
        for name in ("reason", "historical_definition", "forward_definition", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.historical_multiple_type, HistoricalMultipleType, "historical_multiple_type"),
            (self.historical_denominator, HistoricalValuationDenominator, "historical_denominator"),
            (self.forward_metric_id, MetricId, "forward_metric_id"),
            (self.compatibility_status, DenominatorCompatibilityStatus, "compatibility_status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.supporting_distribution_id is not None:
            object.__setattr__(self, "supporting_distribution_id", _require_text(
                self.supporting_distribution_id, "supporting_distribution_id"
            ))
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting_observation_ids must be unique")
        object.__setattr__(self, "supporting_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        if self.semantic_evidence_id is not None:
            object.__setattr__(self, "semantic_evidence_id", _require_text(
                self.semantic_evidence_id, "semantic_evidence_id"
            ))
        if self.compatibility_status is DenominatorCompatibilityStatus.VERIFIED_EQUIVALENT:
            if self.semantic_evidence_id is None:
                raise ValueError("verified equivalence requires explicit semantic evidence")


@dataclass(frozen=True, slots=True)
class CapitalStructureSnapshot:
    """Current actual bridge evidence selected on or before one analysis timestamp."""

    snapshot_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    cash_and_equivalents: float | None
    cash_observation_id: str | None
    cash_source_date: date | None
    gross_debt: float | None
    debt_observation_id: str | None
    debt_source_date: date | None
    net_debt: float | None
    shares: float | None
    share_observation_id: str | None
    share_source_date: date | None
    share_basis: ShareCountBasis
    currency: str | None
    completeness_status: CapitalStructureCompleteness
    source_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    provider_symbols: tuple[str, ...]
    cash_debt_date_gap_days: int | None
    maximum_date_gap_days: int | None
    date_gap_exceeded: bool
    missing_requirements: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str
    required_additional_adjustments: tuple[EnterpriseAdjustmentRequirement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not _CAPITAL_ID_RE.fullmatch(self.snapshot_id):
            raise ValueError("snapshot_id must be a stable capital-structure identifier")
        for name in ("security_id", "issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        if not isinstance(self.share_basis, ShareCountBasis):
            raise TypeError("share_basis must use ShareCountBasis")
        if not isinstance(self.completeness_status, CapitalStructureCompleteness):
            raise TypeError("completeness_status must use CapitalStructureCompleteness")
        _validate_currency(self.currency, "currency", required=False)
        for name in ("cash_and_equivalents", "gross_debt", "net_debt", "shares"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite when present")
        if self.shares is not None and self.shares <= 0:
            raise ValueError("shares must be positive when present")
        pairs = (
            (self.cash_and_equivalents, self.cash_observation_id, self.cash_source_date, "cash"),
            (self.gross_debt, self.debt_observation_id, self.debt_source_date, "debt"),
            (self.shares, self.share_observation_id, self.share_source_date, "shares"),
        )
        for value, observation_id, source_date, name in pairs:
            if (value is None) != (observation_id is None) or (value is None) != (source_date is None):
                raise ValueError(f"{name} value, observation ID, and source date must be present together")
        if self.net_debt is not None:
            if self.gross_debt is None or self.cash_and_equivalents is None or self.currency is None:
                raise ValueError("net debt requires compatible gross debt, cash, and currency")
            if not math.isclose(self.net_debt, self.gross_debt - self.cash_and_equivalents):
                raise ValueError("net debt must equal gross debt minus cash")
        elif self.gross_debt is not None and self.cash_and_equivalents is not None and self.currency is not None:
            raise ValueError("compatible cash and gross debt must produce deterministic net debt")
        ids = tuple(_require_text(value, "source_observation_id") for value in self.source_observation_ids)
        expected_ids = tuple(value for value in (
            self.cash_observation_id, self.debt_observation_id, self.share_observation_id
        ) if value is not None)
        if set(ids) != set(expected_ids) or len(ids) != len(set(ids)):
            raise ValueError("source_observation_ids must identify each selected input exactly once")
        object.__setattr__(self, "source_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        symbols = tuple(sorted({_require_text(value, "provider_symbol") for value in self.provider_symbols}))
        object.__setattr__(self, "provider_symbols", symbols)
        missing = tuple(_require_text(value, "missing_requirement") for value in self.missing_requirements)
        object.__setattr__(self, "missing_requirements", tuple(dict.fromkeys(missing)))
        warnings = tuple(_require_text(value, "warning") for value in self.warnings)
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(warnings)))
        requirements = tuple(dict.fromkeys(self.required_additional_adjustments))
        if any(not isinstance(value, EnterpriseAdjustmentRequirement) for value in requirements):
            raise TypeError("required_additional_adjustments must use EnterpriseAdjustmentRequirement")
        object.__setattr__(self, "required_additional_adjustments", requirements)
        if self.cash_debt_date_gap_days is not None and self.cash_debt_date_gap_days < 0:
            raise ValueError("cash_debt_date_gap_days must be non-negative")
        if self.maximum_date_gap_days is not None and self.maximum_date_gap_days < 0:
            raise ValueError("maximum_date_gap_days must be non-negative")
        expected_exceeded = (
            self.maximum_date_gap_days is not None
            and self.cash_debt_date_gap_days is not None
            and self.cash_debt_date_gap_days > self.maximum_date_gap_days
        )
        if self.date_gap_exceeded != expected_exceeded:
            raise ValueError("date_gap_exceeded must reflect the explicit maximum-date-gap policy")
        if self.completeness_status is CapitalStructureCompleteness.COMPLETE:
            if self.net_debt is None or self.shares is None or self.date_gap_exceeded or missing:
                raise ValueError("complete capital snapshot requires bridge, shares, and no policy failure")
        if self.completeness_status is CapitalStructureCompleteness.UNAVAILABLE and ids:
            raise ValueError("unavailable capital snapshot cannot contain selected observations")


@dataclass(frozen=True, slots=True)
class DirectEnterpriseEquityBridge:
    """Fiscal TEV-minus-market-cap aggregate bridge; never an equity valuation."""

    bridge_id: str
    bridge_method: EnterpriseBridgeMethod
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    enterprise_value: float | None
    market_cap: float | None
    enterprise_equity_adjustment: float | None
    currency: str | None
    enterprise_value_observation_id: str | None
    market_cap_observation_id: str | None
    enterprise_value_observation_date: date | None
    market_cap_observation_date: date | None
    provider: str
    provider_symbols: tuple[str, ...]
    shares_outstanding: float | None
    shares_observation_id: str | None
    shares_observation_date: date | None
    share_basis: ShareCountBasis
    share_count_semantics: ShareCountSemantics
    completeness_status: CapitalStructureCompleteness
    observation_date_gap_days: int | None
    maximum_date_gap_days: int | None
    date_gap_exceeded: bool
    source_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    issues: tuple[DataIssue, ...]
    missing_requirements: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.bridge_id, str) or not _DIRECT_BRIDGE_ID_RE.fullmatch(self.bridge_id):
            raise ValueError("bridge_id must be a stable direct-bridge identifier")
        if self.bridge_method is not EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE:
            raise ValueError("direct bridge must use DIRECT_TEV_MARKET_CAP_BRIDGE")
        for name in ("security_id", "issuer_id", "provider", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency", required=False)
        if not isinstance(self.share_basis, ShareCountBasis):
            raise TypeError("share_basis must use ShareCountBasis")
        if not isinstance(self.share_count_semantics, ShareCountSemantics):
            raise TypeError("share_count_semantics must use ShareCountSemantics")
        if not isinstance(self.completeness_status, CapitalStructureCompleteness):
            raise TypeError("completeness_status must use CapitalStructureCompleteness")
        for name in ("enterprise_value", "market_cap", "enterprise_equity_adjustment", "shares_outstanding"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite when present")
        for value, observation_id, observation_date, name in (
            (self.enterprise_value, self.enterprise_value_observation_id, self.enterprise_value_observation_date, "enterprise value"),
            (self.market_cap, self.market_cap_observation_id, self.market_cap_observation_date, "market cap"),
            (self.shares_outstanding, self.shares_observation_id, self.shares_observation_date, "shares"),
        ):
            if (value is None) != (observation_id is None) or (value is None) != (observation_date is None):
                raise ValueError(f"{name} value, observation ID, and observation date must be present together")
        if self.enterprise_value is not None and self.enterprise_value <= 0:
            raise ValueError("selected enterprise value must be positive")
        if self.market_cap is not None and self.market_cap <= 0:
            raise ValueError("selected market cap must be positive")
        if self.shares_outstanding is not None and self.shares_outstanding <= 0:
            raise ValueError("selected shares outstanding must be positive")
        if self.enterprise_equity_adjustment is not None:
            if self.enterprise_value is None or self.market_cap is None or self.currency is None:
                raise ValueError("direct adjustment requires TEV, market cap, and compatible currency")
            if not math.isclose(
                self.enterprise_equity_adjustment, self.enterprise_value - self.market_cap,
            ):
                raise ValueError("enterprise_equity_adjustment must equal TEV minus market cap")
        ids = tuple(_require_text(value, "source_observation_id") for value in self.source_observation_ids)
        expected = tuple(value for value in (
            self.enterprise_value_observation_id, self.market_cap_observation_id, self.shares_observation_id,
        ) if value is not None)
        if set(ids) != set(expected) or len(ids) != len(set(ids)):
            raise ValueError("source_observation_ids must identify every selected input exactly once")
        object.__setattr__(self, "source_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        symbols = tuple(sorted({_require_text(value, "provider_symbol") for value in self.provider_symbols}))
        object.__setattr__(self, "provider_symbols", symbols)
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        object.__setattr__(self, "issues", issues)
        missing = tuple(_require_text(value, "missing_requirement") for value in self.missing_requirements)
        warnings = tuple(_require_text(value, "warning") for value in self.warnings)
        object.__setattr__(self, "missing_requirements", tuple(dict.fromkeys(missing)))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(warnings)))
        for name in ("observation_date_gap_days", "maximum_date_gap_days"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        expected_exceeded = (
            self.observation_date_gap_days is not None
            and (
                (self.maximum_date_gap_days is None and self.observation_date_gap_days != 0)
                or (
                    self.maximum_date_gap_days is not None
                    and self.observation_date_gap_days > self.maximum_date_gap_days
                )
            )
        )
        if self.date_gap_exceeded != expected_exceeded:
            raise ValueError("date_gap_exceeded must reflect exact-date/default or configured-gap policy")
        if self.completeness_status is CapitalStructureCompleteness.COMPLETE:
            if (
                self.enterprise_equity_adjustment is None
                or self.shares_outstanding is None
                or self.date_gap_exceeded
                or missing
            ):
                raise ValueError("complete direct bridge requires aligned adjustment and suitable shares")
        if self.completeness_status is CapitalStructureCompleteness.UNAVAILABLE and ids:
            raise ValueError("unavailable direct bridge cannot contain selected evidence")


@dataclass(frozen=True, slots=True)
class OwnHistoryMethodReadiness:
    """Prerequisite decision for one method, with no monetary valuation output."""

    readiness_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    multiple_type: HistoricalMultipleType
    selected_window: HistoricalWindow
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    distribution_id: str | None
    forward_selection: ForwardDenominatorSelection
    denominator_alignment: ForwardDenominatorAlignment
    capital_structure_snapshot_id: str | None
    status: OwnHistoryMethodStatus
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    supporting_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    policy_id: str
    enterprise_equity_bridge_id: str | None = None
    bridge_method: EnterpriseBridgeMethod | None = None
    selected_share_basis: ShareCountBasis | None = None
    selected_share_observation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.readiness_id, str) or not _READINESS_ID_RE.fullmatch(self.readiness_id):
            raise ValueError("readiness_id must be a stable own-history readiness identifier")
        for name in ("security_id", "issuer_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        for value, enum_type, name in (
            (self.multiple_type, HistoricalMultipleType, "multiple_type"),
            (self.selected_window, HistoricalWindow, "selected_window"),
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.status, OwnHistoryMethodStatus, "status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.estimate_case is EstimateCase.NOT_APPLICABLE:
            raise ValueError("own-history readiness requires an explicit estimate case")
        if self.distribution_id is not None:
            object.__setattr__(self, "distribution_id", _require_text(self.distribution_id, "distribution_id"))
        if self.capital_structure_snapshot_id is not None:
            object.__setattr__(self, "capital_structure_snapshot_id", _require_text(
                self.capital_structure_snapshot_id, "capital_structure_snapshot_id"
            ))
        if self.enterprise_equity_bridge_id is not None:
            object.__setattr__(self, "enterprise_equity_bridge_id", _require_text(
                self.enterprise_equity_bridge_id, "enterprise_equity_bridge_id"
            ))
        if self.bridge_method is not None and not isinstance(self.bridge_method, EnterpriseBridgeMethod):
            raise TypeError("bridge_method must use EnterpriseBridgeMethod")
        if self.selected_share_basis is not None and not isinstance(self.selected_share_basis, ShareCountBasis):
            raise TypeError("selected_share_basis must use ShareCountBasis")
        if self.selected_share_observation_id is not None:
            object.__setattr__(self, "selected_share_observation_id", _require_text(
                self.selected_share_observation_id, "selected_share_observation_id"
            ))
        if self.bridge_method is EnterpriseBridgeMethod.COMPONENT_BRIDGE and self.capital_structure_snapshot_id is None:
            raise ValueError("component bridge method requires capital_structure_snapshot_id")
        if (
            self.bridge_method is EnterpriseBridgeMethod.COMPONENT_BRIDGE
            and self.enterprise_equity_bridge_id is not None
        ):
            raise ValueError("component bridge method cannot reference a direct bridge")
        if (
            self.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
            and self.enterprise_equity_bridge_id is None
        ):
            raise ValueError("direct bridge method requires enterprise_equity_bridge_id")
        if (
            self.bridge_method is EnterpriseBridgeMethod.DIRECT_TEV_MARKET_CAP_BRIDGE
            and self.capital_structure_snapshot_id is not None
        ):
            raise ValueError("direct bridge method cannot reference a component bridge")
        if self.bridge_method is None and (
            self.capital_structure_snapshot_id is not None or self.enterprise_equity_bridge_id is not None
        ):
            raise ValueError("a referenced enterprise bridge requires controlled bridge_method")
        if self.bridge_method is None and (
            self.selected_share_basis is not None or self.selected_share_observation_id is not None
        ):
            raise ValueError("selected share evidence requires a controlled enterprise bridge")
        if self.bridge_method is not None and self.selected_share_basis is None:
            raise ValueError("enterprise bridge selection requires its approved share basis")
        if self.status is OwnHistoryMethodStatus.READY and self.bridge_method is not None:
            if self.selected_share_observation_id is None:
                raise ValueError("ready enterprise method requires its approved share observation")
        blocking = tuple(_require_text(value, "blocking_reason") for value in self.blocking_reasons)
        warnings = tuple(_require_text(value, "warning") for value in self.warnings)
        object.__setattr__(self, "blocking_reasons", tuple(dict.fromkeys(blocking)))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(warnings)))
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting_observation_ids must be unique")
        object.__setattr__(self, "supporting_observation_ids", ids)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        if self.status is OwnHistoryMethodStatus.READY and blocking:
            raise ValueError("ready method cannot carry blocking reasons")
        if self.status is not OwnHistoryMethodStatus.READY and not blocking:
            raise ValueError("non-ready method requires at least one blocking reason")


@dataclass(frozen=True, slots=True)
class OwnHistoryValuationPoint:
    """One independently auditable historical-statistic application."""

    point_id: str
    statistic: HistoricalStatistic
    multiple_type: HistoricalMultipleType
    valuation_basis: ValuationBasis
    analysis_as_of: datetime
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    distribution_id: str
    forward_observation_id: str
    historical_multiple: float | None
    forward_denominator: float | None
    implied_enterprise_value: float | None
    enterprise_equity_adjustment: float | None
    implied_equity_value: float | None
    share_count: float | None
    share_basis: ShareCountBasis | None
    share_observation_id: str | None
    per_share_value: float | None
    currency: str
    status: ValuationMethodStatus
    bridge_id: str | None
    supporting_observation_ids: tuple[str, ...]
    provenance: tuple[Provenance, ...]
    issues: tuple[DataIssue, ...]
    policy_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.point_id, str) or not _VALUATION_POINT_ID_RE.fullmatch(self.point_id):
            raise ValueError("point_id must be a stable own-history valuation-point identifier")
        for name in ("distribution_id", "forward_observation_id", "policy_id"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.statistic, HistoricalStatistic, "statistic"),
            (self.multiple_type, HistoricalMultipleType, "multiple_type"),
            (self.valuation_basis, ValuationBasis, "valuation_basis"),
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.status, ValuationMethodStatus, "status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("numeric own-history points require the AVERAGE estimate case")
        if self.status is ValuationMethodStatus.PARTIAL:
            raise ValueError("individual valuation points are valid or unavailable, never partial")
        for name in (
            "historical_multiple", "forward_denominator", "implied_enterprise_value",
            "enterprise_equity_adjustment", "implied_equity_value", "share_count",
            "per_share_value",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be finite when present")
        if self.historical_multiple is not None and self.historical_multiple <= 0:
            raise ValueError("historical_multiple must be positive when retained")
        if self.forward_denominator is not None and self.forward_denominator <= 0:
            raise ValueError("forward_denominator must be positive when retained")
        if self.share_count is not None and self.share_count <= 0:
            raise ValueError("share_count must be positive when retained")
        if (self.share_count is None) != (self.share_basis is None) or (
            (self.share_count is None) != (self.share_observation_id is None)
        ):
            raise ValueError("share value, basis, and observation ID must be present together")
        if self.share_basis is not None and not isinstance(self.share_basis, ShareCountBasis):
            raise TypeError("share_basis must use ShareCountBasis")
        if self.share_observation_id is not None:
            object.__setattr__(self, "share_observation_id", _require_text(
                self.share_observation_id, "share_observation_id"
            ))
        if self.bridge_id is not None:
            object.__setattr__(self, "bridge_id", _require_text(self.bridge_id, "bridge_id"))
        if self.valuation_basis is ValuationBasis.EQUITY:
            if any(value is not None for value in (
                self.implied_enterprise_value, self.enterprise_equity_adjustment,
                self.implied_equity_value, self.share_count,
            )) or self.bridge_id is not None:
                raise ValueError("equity-basis P/E points cannot carry enterprise bridge mechanics")
            if self.status is ValuationMethodStatus.VALID:
                if (
                    self.historical_multiple is None
                    or self.forward_denominator is None
                    or self.per_share_value is None
                ):
                    raise ValueError("valid P/E point requires multiple and forward EPS")
                if not math.isclose(
                    self.per_share_value, self.historical_multiple * self.forward_denominator,
                ):
                    raise ValueError("P/E per_share_value must equal multiple times forward EPS")
        elif self.status is ValuationMethodStatus.VALID and any(value is None for value in (
            self.implied_enterprise_value, self.enterprise_equity_adjustment,
            self.implied_equity_value, self.share_count, self.bridge_id,
        )):
            raise ValueError("valid enterprise points require complete bridge and share mechanics")
        if self.valuation_basis is ValuationBasis.ENTERPRISE:
            if self.implied_enterprise_value is not None:
                if self.historical_multiple is None or self.forward_denominator is None:
                    raise ValueError("implied enterprise value requires multiple and forward denominator")
                if not math.isclose(
                    self.implied_enterprise_value,
                    self.historical_multiple * self.forward_denominator,
                ):
                    raise ValueError("implied enterprise value must equal multiple times denominator")
            if self.implied_equity_value is not None:
                if self.implied_enterprise_value is None or self.enterprise_equity_adjustment is None:
                    raise ValueError("implied equity value requires enterprise value and adjustment")
                if not math.isclose(
                    self.implied_equity_value,
                    self.implied_enterprise_value - self.enterprise_equity_adjustment,
                ):
                    raise ValueError("implied equity value must equal enterprise value minus adjustment")
        if self.status is ValuationMethodStatus.VALID:
            if self.per_share_value is None or self.per_share_value <= 0:
                raise ValueError("valid valuation point requires a positive per-share value")
            if self.valuation_basis is ValuationBasis.ENTERPRISE and not math.isclose(
                self.per_share_value, self.implied_equity_value / self.share_count,
            ):
                raise ValueError("enterprise per_share_value must equal equity value divided by shares")
        elif self.per_share_value is not None:
            raise ValueError("unavailable valuation point cannot expose a per-share value")
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if self.forward_observation_id not in ids or len(ids) != len(set(ids)):
            raise ValueError("supporting IDs must uniquely include the forward observation")
        if self.share_observation_id is not None and self.share_observation_id not in ids:
            raise ValueError("enterprise point support must include its share observation")
        object.__setattr__(self, "supporting_observation_ids", ids)
        provenance = tuple(self.provenance)
        if not provenance or any(not isinstance(value, Provenance) for value in provenance):
            raise TypeError("valuation point requires Provenance records")
        object.__setattr__(self, "provenance", provenance)
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        if self.status is ValuationMethodStatus.UNAVAILABLE and not issues:
            raise ValueError("unavailable valuation point requires a structured issue")
        object.__setattr__(self, "issues", issues)


@dataclass(frozen=True, slots=True)
class OwnHistoryValuationResult:
    """One non-aggregated own-history method result with a generic V1 view."""

    result_id: str
    security_id: str
    issuer_id: str
    analysis_as_of: datetime
    multiple_type: HistoricalMultipleType
    selected_window: HistoricalWindow
    selected_forward_period: ForwardPeriodSelection
    estimate_case: EstimateCase
    distribution_id: str
    readiness_id: str
    lower_point: OwnHistoryValuationPoint
    central_point: OwnHistoryValuationPoint
    upper_point: OwnHistoryValuationPoint
    currency: str
    status: ValuationMethodStatus
    issues: tuple[DataIssue, ...]
    warnings: tuple[str, ...]
    supporting_observation_ids: tuple[str, ...]
    supporting_distribution_id: str
    bridge_id: str | None
    policy_id: str
    provenance: tuple[Provenance, ...]
    valuation_result: ValuationResult

    def __post_init__(self) -> None:
        if not isinstance(self.result_id, str) or not _VALUATION_RESULT_ID_RE.fullmatch(self.result_id):
            raise ValueError("result_id must be a stable own-history valuation-result identifier")
        for name in (
            "security_id", "issuer_id", "distribution_id", "readiness_id",
            "supporting_distribution_id", "policy_id",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        for value, enum_type, name in (
            (self.multiple_type, HistoricalMultipleType, "multiple_type"),
            (self.selected_window, HistoricalWindow, "selected_window"),
            (self.selected_forward_period, ForwardPeriodSelection, "selected_forward_period"),
            (self.estimate_case, EstimateCase, "estimate_case"),
            (self.status, ValuationMethodStatus, "status"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{name} must use {enum_type.__name__}")
        if self.estimate_case is not EstimateCase.AVERAGE:
            raise ValueError("numeric own-history results require AVERAGE consensus")
        _validate_aware_datetime(self.analysis_as_of, "analysis_as_of")
        _validate_currency(self.currency, "currency")
        points = (self.lower_point, self.central_point, self.upper_point)
        expected_statistics = (HistoricalStatistic.P25, HistoricalStatistic.MEDIAN, HistoricalStatistic.P75)
        if tuple(point.statistic for point in points) != expected_statistics:
            raise ValueError("valuation points must remain ordered P25, median, P75")
        for point in points:
            if (
                point.multiple_type is not self.multiple_type
                or point.distribution_id != self.distribution_id
                or point.selected_forward_period is not self.selected_forward_period
                or point.currency != self.currency
                or point.analysis_as_of != self.analysis_as_of
            ):
                raise ValueError("valuation points must match the method result dimensions")
        if self.supporting_distribution_id != self.distribution_id:
            raise ValueError("supporting_distribution_id must match distribution_id")
        if self.bridge_id is not None:
            object.__setattr__(self, "bridge_id", _require_text(self.bridge_id, "bridge_id"))
        point_bridge_ids = {point.bridge_id for point in points}
        if point_bridge_ids != {self.bridge_id}:
            raise ValueError("result bridge_id must match every valuation point")
        ids = tuple(_require_text(value, "supporting_observation_id") for value in self.supporting_observation_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("supporting_observation_ids must be unique")
        object.__setattr__(self, "supporting_observation_ids", ids)
        issues = tuple(self.issues)
        if any(not isinstance(value, DataIssue) for value in issues):
            raise TypeError("issues must contain DataIssue values")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "warnings", tuple(
            _require_text(value, "warning") for value in self.warnings
        ))
        provenance = tuple(self.provenance)
        if any(not isinstance(value, Provenance) for value in provenance):
            raise TypeError("provenance must contain Provenance values")
        object.__setattr__(self, "provenance", provenance)
        if not isinstance(self.valuation_result, ValuationResult):
            raise TypeError("valuation_result must use the generic ValuationResult contract")
        expected_values = tuple(
            point.per_share_value if point.status is ValuationMethodStatus.VALID else None
            for point in points
        )
        generic = self.valuation_result
        if (
            generic.method != self.multiple_type.value
            or generic.valuation_family != "own_history"
            or generic.currency != self.currency
            or generic.status is not self.status
            or (generic.low, generic.central, generic.high) != expected_values
            or generic.input_observation_ids != ids
            or generic.provenance != provenance
            or generic.issues != issues
            or generic.warnings != self.warnings
        ):
            raise ValueError("generic valuation result must exactly mirror the own-history result")
        if self.status is ValuationMethodStatus.VALID and any(
            point.status is not ValuationMethodStatus.VALID for point in points
        ):
            raise ValueError("valid method result requires three valid points")
        if self.status is ValuationMethodStatus.PARTIAL:
            if all(point.status is ValuationMethodStatus.VALID for point in points):
                raise ValueError("partial result requires at least one unavailable point")
            if not any(point.status is ValuationMethodStatus.VALID for point in points):
                raise ValueError("partial result requires at least one valid point")
        if self.status is ValuationMethodStatus.UNAVAILABLE and any(
            point.status is ValuationMethodStatus.VALID for point in points
        ):
            raise ValueError("unavailable result cannot contain valid valuation points")


def stable_forward_selection_id(*parts: object) -> str:
    return _stable_id("fwdsel", *parts)


def stable_denominator_alignment_id(*parts: object) -> str:
    return _stable_id("denalign", *parts)


def stable_capital_structure_id(*parts: object) -> str:
    return _stable_id("capsnap", *parts)


def stable_direct_bridge_id(*parts: object) -> str:
    return _stable_id("directbridge", *parts)


def stable_own_history_readiness_id(*parts: object) -> str:
    return _stable_id("ownready", *parts)


def stable_own_history_point_id(*parts: object) -> str:
    return _stable_id("ownpoint", *parts)


def stable_own_history_valuation_id(*parts: object) -> str:
    return _stable_id("ownvalue", *parts)
