"""Pure assembly of canonical outputs into the Milestone 11C report view model."""

from __future__ import annotations

from collections import defaultdict

from stock_analyser.domain.enums import (
    AggregationStatus,
    DataAvailability,
    IssueSeverity,
    PublicationEvidenceType,
    ReportDataQualityCategory,
    ReportExpectationState,
    ReportNumberSemantic,
    ReportSourceCategory,
    ReportStatus,
    ReverseDcfExecutionMode,
    ReverseDcfReadinessStatus,
    ValuationFamily,
    ValuationMethodStatus,
)
from stock_analyser.domain.market_comparison import MarketComparisonResult, MarketPriceEvidence
from stock_analyser.domain.models import CompanyIdentity, DataIssue, Provenance, _validate_aware_datetime
from stock_analyser.domain.publication import FamilyValuationEvidence, ValuationPublicationResult
from stock_analyser.domain.research_report import (
    REPORT_SECTION_ORDER,
    ReportConsensusPeriod,
    ReportConsensusSection,
    ReportDataQualityRow,
    ReportDataQualitySection,
    ReportExpectationEntry,
    ReportExpectationsSection,
    ReportFieldSemantic,
    ReportIdentitySection,
    ReportIssue,
    ReportMarketSection,
    ReportReferenceSection,
    ReportScenarioAssumption,
    ReportSourceReference,
    ReportValuationFamilySection,
    ReportValuationSummarySection,
    StockResearchReport,
    stable_stock_research_report_id,
)
from stock_analyser.domain.reverse_dcf import (
    ForwardOperatingTrajectory,
    ReverseDcfReadiness,
    ReverseDcfScenarioAssumption,
)


REPORT_POLICY_ID = "v1-11c-provider-independent-research-report"

_SOURCE_LABELS = {
    "yahoo": "Yahoo",
    "fiscal": "Fiscal",
    "fmp": "FMP",
    "fred": "FRED",
    "damodaran": "NYU Stern / Damodaran",
    "nyu": "NYU Stern / Damodaran",
    "nyu_stern": "NYU Stern / Damodaran",
    "sec": "SEC",
    "alpha_vantage": "Alpha Vantage",
    "finnhub": "Finnhub",
    "explicit-scenario": "Explicit scenario",
}

_PUBLICATION_LABELS = {
    AggregationStatus.RESOLVED: "Resolved",
    AggregationStatus.WIDE: "Wide",
    AggregationStatus.UNRESOLVED: "Unresolved",
    AggregationStatus.UNAVAILABLE: "Unavailable",
}


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _readiness_stage_label(value: str) -> str:
    """Render stage names without lower-casing canonical finance acronyms."""
    return _label(value).replace("Fy1", "FY1")


def _source_label(provider: str) -> str:
    normalized = provider.strip().lower()
    return _SOURCE_LABELS.get(normalized, _label(normalized))


def _issues(values) -> tuple[ReportIssue, ...]:
    result = []
    for value in values:
        if isinstance(value, DataIssue):
            result.append(ReportIssue(
                message=value.reason,
                severity=value.severity,
                source_label=_source_label(value.provider),
            ))
        else:
            result.append(ReportIssue(message=str(value)))
    return tuple(dict.fromkeys(result))


def _blocking_from_data_issues(values: tuple[DataIssue, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value.reason for value in values
        if value.severity in {IssueSeverity.ERROR, IssueSeverity.BLOCKING}
    ))


def _sources(
    provenance: tuple[Provenance, ...],
    category: ReportSourceCategory,
    evidence_ids: tuple[str, ...],
) -> tuple[ReportSourceReference, ...]:
    grouped: dict[tuple[str, object], list[Provenance]] = defaultdict(list)
    for item in provenance:
        grouped[(_source_label(item.provider), item.as_of_at)].append(item)
    return tuple(
        ReportSourceReference(
            display_name=name,
            source_category=category,
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            observation_as_of=as_of,
            provenance=tuple(dict.fromkeys(values)),
        )
        for (name, as_of), values in sorted(grouped.items(), key=lambda item: (item[0][0], str(item[0][1])))
    )


def _identity_sources(identity: CompanyIdentity) -> tuple[ReportSourceReference, ...]:
    return tuple(
        ReportSourceReference(
            display_name=_source_label(item.provider),
            source_category=ReportSourceCategory.IDENTITY,
            evidence_ids=(identity.security_id, identity.issuer_id),
            observation_as_of=None,
            provenance=(),
        )
        for item in identity.provider_symbols
    )


def _status_from_availability(value: DataAvailability) -> ReportStatus:
    return {
        DataAvailability.AVAILABLE: ReportStatus.READY,
        DataAvailability.PARTIAL: ReportStatus.PARTIAL,
        DataAvailability.UNAVAILABLE: ReportStatus.UNAVAILABLE,
    }[value]


def _status_from_method(value: ValuationMethodStatus) -> ReportStatus:
    return {
        ValuationMethodStatus.VALID: ReportStatus.READY,
        ValuationMethodStatus.PARTIAL: ReportStatus.PARTIAL,
        ValuationMethodStatus.UNAVAILABLE: ReportStatus.UNAVAILABLE,
    }[value]


def _status_from_readiness(value: ReverseDcfReadinessStatus | None) -> ReportStatus:
    if value is ReverseDcfReadinessStatus.READY:
        return ReportStatus.READY
    if value is ReverseDcfReadinessStatus.PARTIAL:
        return ReportStatus.PARTIAL
    return ReportStatus.UNAVAILABLE


def _assert_target_snapshot(
    value,
    *,
    security_id: str,
    issuer_id: str,
    analysis_as_of,
    name: str,
) -> None:
    if (
        value.target_security_id != security_id
        or value.target_issuer_id != issuer_id
        or value.analysis_as_of != analysis_as_of
    ):
        raise ValueError(f"{name} target security, issuer, and analysis snapshot must match the report")


def _build_market(price: MarketPriceEvidence | None) -> ReportMarketSection:
    if price is None:
        return ReportMarketSection(
            status=ReportStatus.UNAVAILABLE,
            availability=DataAvailability.UNAVAILABLE,
            source_label="Yahoo",
            normalized_market_price=None,
            raw_market_quote=None,
            normalized_currency=None,
            normalized_per_share_unit=None,
            quote_currency=None,
            quote_unit=None,
            quote_scale=None,
            observation_timestamp=None,
            freshness_label="Unavailable",
            market_price_evidence_id=None,
            issues=(ReportIssue("Canonical market-price evidence was not supplied"),),
            blocking_reasons=("Canonical market-price evidence was not supplied",),
            display_semantics=(
                ReportFieldSemantic("normalized_market_price", ReportNumberSemantic.CURRENCY),
                ReportFieldSemantic("raw_market_quote", ReportNumberSemantic.CURRENCY),
                ReportFieldSemantic("observation_timestamp", ReportNumberSemantic.DATE),
            ),
        )
    source_ids = tuple(value for value in (price.evidence_id, price.source_observation_id) if value)
    blockers = _blocking_from_data_issues(price.issues)
    return ReportMarketSection(
        status=_status_from_availability(price.status),
        availability=price.status,
        source_label="Yahoo",
        normalized_market_price=price.normalized_price_per_share,
        raw_market_quote=price.raw_quote_value,
        normalized_currency=price.normalized_currency,
        normalized_per_share_unit=price.normalized_per_share_unit,
        quote_currency=price.quote_currency,
        quote_unit=price.quote_unit,
        quote_scale=price.quote_unit_scale,
        observation_timestamp=price.observation_timestamp,
        freshness_label="Eligible under upstream price policy" if price.status is DataAvailability.AVAILABLE else "Unavailable under upstream price policy",
        market_price_evidence_id=price.evidence_id,
        source_references=_sources(price.provenance, ReportSourceCategory.MARKET_DATA, source_ids),
        supporting_ids=source_ids,
        issues=_issues(price.issues),
        warnings=price.warnings,
        blocking_reasons=blockers,
        provenance=price.provenance,
        display_semantics=(
            ReportFieldSemantic("normalized_market_price", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("raw_market_quote", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("observation_timestamp", ReportNumberSemantic.DATE),
        ),
    )


def _build_summary(
    publication: ValuationPublicationResult,
    comparison: MarketComparisonResult | None,
) -> ReportValuationSummarySection:
    overall = comparison.overall_comparison if comparison is not None else None
    comparison_status = overall.status if overall is not None else DataAvailability.UNAVAILABLE
    comparison_issues = overall.issues if overall is not None else ("Overall market comparison was not supplied",)
    available = comparison_status is DataAvailability.AVAILABLE
    resolved = publication.publication_status is AggregationStatus.RESOLVED
    return ReportValuationSummarySection(
        status=(
            ReportStatus.READY if resolved
            else ReportStatus.PARTIAL if publication.eligible_family_count > 0
            else ReportStatus.UNAVAILABLE
        ),
        publication_status=publication.publication_status,
        publication_label=_PUBLICATION_LABELS[publication.publication_status],
        eligible_family_count=publication.eligible_family_count,
        overall_value_label="Overall fair value" if resolved else "Withheld",
        overall_central_value=publication.overall_central_value,
        central_estimator=publication.central_estimator,
        envelope_lower=publication.envelope_lower,
        envelope_upper=publication.envelope_upper,
        envelope_semantics=publication.envelope_semantics,
        overlap_lower=publication.overlap_lower,
        overlap_upper=publication.overlap_upper,
        overlap_semantics=publication.overlap_semantics,
        currency=publication.currency,
        per_share_unit=publication.per_share_unit,
        overall_comparison_status=comparison_status,
        overall_market_price=overall.market_price if available else None,
        overall_lower_gap=overall.lower_gap if available else None,
        overall_central_gap=overall.central_gap if available else None,
        overall_upper_gap=overall.upper_gap if available else None,
        overall_lower_gap_percent=overall.lower_gap_percent if available else None,
        overall_central_gap_percent=overall.central_gap_percent if available else None,
        overall_upper_gap_percent=overall.upper_gap_percent if available else None,
        publication_id=publication.publication_id,
        source_references=_sources(
            tuple(dict.fromkeys((*publication.provenance, *(overall.provenance if overall else ())))),
            ReportSourceCategory.VALUATION,
            (publication.publication_id, *(overall.supporting_ids if overall else ())),
        ),
        supporting_ids=tuple(dict.fromkeys((
            publication.publication_id,
            *(item.evidence_id for item in publication.family_evidence),
            *(overall.supporting_ids if overall else ()),
        ))),
        issues=(*_issues(publication.issues), *_issues(comparison_issues)),
        warnings=tuple(dict.fromkeys((
            *publication.warnings,
            *(overall.warnings if overall else ()),
        ))),
        blocking_reasons=tuple(dict.fromkeys((
            *publication.blocking_reasons,
            *(comparison_issues if not available else ()),
        ))),
        provenance=tuple(dict.fromkeys((*publication.provenance, *(overall.provenance if overall else ())))),
        display_semantics=(
            ReportFieldSemantic("overall_central_value", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("envelope_lower", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("envelope_upper", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("overlap_lower", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("overlap_upper", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("overall_central_gap", ReportNumberSemantic.CURRENCY),
            ReportFieldSemantic("overall_central_gap_percent", ReportNumberSemantic.PERCENTAGE),
        ),
    )


def _family_placeholder(family: ValuationFamily) -> ReportValuationFamilySection:
    reason = f"No upstream {family.value} family evidence was supplied"
    return ReportValuationFamilySection(
        status=ReportStatus.UNAVAILABLE,
        family=family,
        family_status=ValuationMethodStatus.UNAVAILABLE,
        method_label=None,
        central_eligible=False,
        lower_value=None,
        central_value=None,
        upper_value=None,
        currency=None,
        per_share_unit=None,
        source_result_id=None,
        comparison_status=DataAvailability.UNAVAILABLE,
        lower_gap=None,
        central_gap=None,
        upper_gap=None,
        lower_gap_percent=None,
        central_gap_percent=None,
        upper_gap_percent=None,
        issues=(ReportIssue(reason),),
        blocking_reasons=(reason,),
        display_semantics=_family_semantics(),
    )


def _family_semantics() -> tuple[ReportFieldSemantic, ...]:
    return (
        ReportFieldSemantic("lower_value", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("central_value", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("upper_value", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("lower_gap", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("central_gap", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("upper_gap", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("lower_gap_percent", ReportNumberSemantic.PERCENTAGE),
        ReportFieldSemantic("central_gap_percent", ReportNumberSemantic.PERCENTAGE),
        ReportFieldSemantic("upper_gap_percent", ReportNumberSemantic.PERCENTAGE),
    )


def _build_family(
    evidence: FamilyValuationEvidence,
    comparison: MarketComparisonResult | None,
) -> ReportValuationFamilySection:
    comparison_item = next((
        item for item in (comparison.family_comparisons if comparison else ())
        if item.valuation_family is evidence.valuation_family
        and item.valuation_source_id == evidence.source_result_id
    ), None)
    comparison_status = comparison_item.status if comparison_item else DataAvailability.UNAVAILABLE
    comparison_issues = comparison_item.issues if comparison_item else ("Family market comparison was not supplied",)
    available = comparison_status is DataAvailability.AVAILABLE
    provenance = tuple(dict.fromkeys((
        *evidence.provenance,
        *(comparison_item.provenance if comparison_item else ()),
    )))
    supporting = tuple(dict.fromkeys((
        evidence.evidence_id, evidence.source_result_id, *evidence.supporting_ids,
        *(comparison_item.supporting_ids if comparison_item else ()),
    )))
    blockers = tuple(dict.fromkeys((
        *_blocking_from_data_issues(evidence.issues),
        *(comparison_issues if not available else ()),
    )))
    return ReportValuationFamilySection(
        status=_status_from_method(evidence.family_status),
        family=evidence.valuation_family,
        family_status=evidence.family_status,
        method_label=evidence.selected_method,
        central_eligible=evidence.central_valuation_eligible,
        lower_value=evidence.lower_value,
        central_value=evidence.central_value,
        upper_value=evidence.upper_value,
        currency=evidence.currency,
        per_share_unit=evidence.per_share_unit,
        source_result_id=evidence.source_result_id,
        comparison_status=comparison_status,
        lower_gap=comparison_item.lower_gap if available else None,
        central_gap=comparison_item.central_gap if available else None,
        upper_gap=comparison_item.upper_gap if available else None,
        lower_gap_percent=comparison_item.lower_gap_percent if available else None,
        central_gap_percent=comparison_item.central_gap_percent if available else None,
        upper_gap_percent=comparison_item.upper_gap_percent if available else None,
        source_references=_sources(provenance, ReportSourceCategory.VALUATION, supporting),
        supporting_ids=supporting,
        issues=(*_issues(evidence.issues), *_issues(comparison_issues)),
        warnings=tuple(dict.fromkeys((
            *evidence.warnings,
            *(comparison_item.warnings if comparison_item else ()),
        ))),
        blocking_reasons=blockers,
        provenance=provenance,
        display_semantics=_family_semantics(),
    )


def _build_consensus(trajectory: ForwardOperatingTrajectory | None) -> ReportConsensusSection:
    semantics = (
        ReportFieldSemantic("revenue", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("ebit", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("ebitda", ReportNumberSemantic.CURRENCY),
        ReportFieldSemantic("ebit_margin", ReportNumberSemantic.PERCENTAGE),
        ReportFieldSemantic("revenue_growth", ReportNumberSemantic.PERCENTAGE),
        ReportFieldSemantic("revenue_analyst_count", ReportNumberSemantic.INTEGER),
        ReportFieldSemantic("fiscal_period_end", ReportNumberSemantic.DATE),
        ReportFieldSemantic("estimate_as_of", ReportNumberSemantic.DATE),
    )
    if trajectory is None:
        return ReportConsensusSection(
            status=ReportStatus.UNAVAILABLE,
            readiness_status=None,
            source_label="FMP forward consensus",
            period_count=0,
            periods=(),
            currency=None,
            issues=(ReportIssue("Forward operating trajectory was not supplied"),),
            blocking_reasons=("Forward operating trajectory was not supplied",),
            display_semantics=semantics,
        )
    periods = tuple(
        ReportConsensusPeriod(
            horizon_label=item.fiscal_period,
            fiscal_period_end=item.fiscal_period_end,
            revenue=item.revenue,
            ebit=item.ebit,
            ebitda=item.ebitda,
            ebit_margin=item.operating_margin,
            revenue_growth=item.revenue_growth,
            revenue_analyst_count=item.revenue_analyst_count,
            estimate_as_of=item.estimate_as_of,
            currency=item.currency,
            status=item.status,
            supporting_ids=tuple(value for value in (
                item.period_id, item.revenue_observation_id, item.ebit_observation_id,
                item.ebitda_observation_id, item.growth_base_observation_id,
            ) if value),
            issues=_issues(item.issues),
            warnings=item.warnings,
            provenance=item.provenance,
            display_semantics=semantics,
        )
        for item in trajectory.periods
    )
    return ReportConsensusSection(
        status=_status_from_readiness(trajectory.status),
        readiness_status=trajectory.status,
        source_label="FMP forward consensus",
        period_count=trajectory.period_count,
        periods=periods,
        currency=trajectory.valuation_currency,
        source_references=_sources(
            trajectory.provenance, ReportSourceCategory.FORWARD_CONSENSUS,
            (trajectory.trajectory_id, *trajectory.supporting_ids),
        ),
        supporting_ids=(trajectory.trajectory_id, *trajectory.supporting_ids),
        issues=_issues(trajectory.issues),
        warnings=trajectory.warnings,
        blocking_reasons=trajectory.issues if trajectory.status is not ReverseDcfReadinessStatus.READY else (),
        provenance=trajectory.provenance,
        display_semantics=semantics,
    )


def _build_assumption(item: ReverseDcfScenarioAssumption) -> ReportScenarioAssumption:
    return ReportScenarioAssumption(
        assumption_id=item.assumption_id,
        assumption_type=item.assumption_type,
        value=item.value,
        currency=item.currency,
        fiscal_year=item.fiscal_year,
        period_end=item.period_end,
        source_label=item.source_label,
        methodology_label=item.methodology_label,
        rationale=item.rationale,
        entered_by=item.entered_by,
        status=item.status,
        issues=_issues(item.issues),
        warnings=item.warnings,
        provenance=item.provenance,
    )


def _build_expectations(
    comparison: MarketComparisonResult | None,
    readiness: ReverseDcfReadiness | None,
    assumptions: tuple[ReverseDcfScenarioAssumption, ...],
) -> ReportExpectationsSection:
    evidences = comparison.expectation_evidence if comparison else ()
    assumption_map = {item.assumption_id: item for item in assumptions}
    entries = []
    for item in evidences:
        state = (
            ReportExpectationState.CANONICAL_EXPECTATIONS
            if item.execution_mode is ReverseDcfExecutionMode.CANONICAL_EVIDENCE
            else ReportExpectationState.SCENARIO_EXPECTATIONS
        )
        selected_assumptions = tuple(
            _build_assumption(assumption_map[assumption_id])
            for assumption_id in item.scenario_assumption_ids
            if assumption_id in assumption_map
        )
        if state is ReportExpectationState.SCENARIO_EXPECTATIONS and len(selected_assumptions) != len(item.scenario_assumption_ids):
            raise ValueError("scenario expectation requires every explicit scenario assumption")
        entries.append(ReportExpectationEntry(
            state=state,
            state_label="CANONICAL EXPECTATIONS" if state is ReportExpectationState.CANONICAL_EXPECTATIONS else "SCENARIO EXPECTATIONS",
            execution_mode=item.execution_mode,
            publication_eligibility=item.publication_eligibility,
            implied_terminal_growth=item.implied_terminal_growth,
            final_consensus_revenue_growth=item.final_consensus_revenue_growth,
            final_consensus_ebit_margin=item.final_consensus_ebit_margin,
            growth_rate_difference=item.growth_rate_difference,
            scenario_assumptions=selected_assumptions,
            reverse_dcf_result_id=item.reverse_dcf_result_id,
            source_references=_sources(
                item.provenance, ReportSourceCategory.EXPECTATIONS,
                (item.evidence_id, *item.supporting_ids),
            ),
            supporting_ids=(item.evidence_id, *item.supporting_ids, *item.scenario_assumption_ids),
            issues=_issues(item.issues),
            warnings=item.warnings,
            provenance=item.provenance,
            display_semantics=(
                ReportFieldSemantic("implied_terminal_growth", ReportNumberSemantic.PERCENTAGE),
                ReportFieldSemantic("final_consensus_revenue_growth", ReportNumberSemantic.PERCENTAGE),
                ReportFieldSemantic("final_consensus_ebit_margin", ReportNumberSemantic.PERCENTAGE),
                ReportFieldSemantic("growth_rate_difference", ReportNumberSemantic.PERCENTAGE),
            ),
        ))
    if entries:
        display_state = (
            ReportExpectationState.SCENARIO_EXPECTATIONS
            if any(item.state is ReportExpectationState.SCENARIO_EXPECTATIONS for item in entries)
            else ReportExpectationState.CANONICAL_EXPECTATIONS
        )
        label = "SCENARIO EXPECTATIONS" if display_state is ReportExpectationState.SCENARIO_EXPECTATIONS else "CANONICAL EXPECTATIONS"
        all_provenance = tuple(dict.fromkeys(value for item in entries for value in item.provenance))
        all_ids = tuple(dict.fromkeys(value for item in entries for value in item.supporting_ids))
        return ReportExpectationsSection(
            status=ReportStatus.READY if all(item.implied_terminal_growth is not None for item in entries) else ReportStatus.PARTIAL,
            display_state=display_state,
            display_label=label,
            entries=tuple(entries),
            readiness_status=readiness.status if readiness else None,
            source_references=_sources(all_provenance, ReportSourceCategory.EXPECTATIONS, all_ids),
            supporting_ids=all_ids,
            issues=tuple(value for item in entries for value in item.issues),
            warnings=tuple(dict.fromkeys(value for item in entries for value in item.warnings)),
            blocking_reasons=(),
            provenance=all_provenance,
        )
    if readiness is None:
        return ReportExpectationsSection(
            status=ReportStatus.UNAVAILABLE,
            display_state=ReportExpectationState.NOT_RUN,
            display_label="NOT RUN",
            entries=(),
            readiness_status=None,
            issues=(ReportIssue("Reverse DCF readiness or execution evidence was not supplied"),),
            blocking_reasons=("Reverse DCF was not run",),
        )
    blockers = tuple(dict.fromkeys((
        _readiness_stage_label(readiness.earliest_blocking_stage.value),
        *readiness.issues,
    )))
    return ReportExpectationsSection(
        status=ReportStatus.UNAVAILABLE,
        display_state=ReportExpectationState.NOT_READY,
        display_label="NOT READY",
        entries=(),
        readiness_status=readiness.status,
        source_references=_sources(
            readiness.provenance, ReportSourceCategory.EXPECTATIONS,
            (readiness.readiness_id, *readiness.supporting_ids),
        ),
        supporting_ids=(readiness.readiness_id, *readiness.supporting_ids),
        issues=_issues(readiness.issues),
        warnings=readiness.warnings,
        blocking_reasons=blockers,
        provenance=readiness.provenance,
    )


def _build_references(publication: ValuationPublicationResult) -> tuple[ReportReferenceSection, ...]:
    references = []
    for item in publication.supplemental_evidence:
        if item.evidence_type not in {
            PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
            PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
        }:
            continue
        supporting = (item.evidence_id, item.source_result_id, *item.supporting_ids)
        references.append(ReportReferenceSection(
            status=ReportStatus.READY,
            reference_type=item.evidence_type,
            reference_only_label="REFERENCE ONLY",
            source_result_id=item.source_result_id,
            evidence_as_of=item.evidence_as_of,
            central_valuation_eligible=False,
            source_references=_sources(item.provenance, ReportSourceCategory.REFERENCE, supporting),
            supporting_ids=supporting,
            issues=_issues(item.issues),
            warnings=item.warnings,
            provenance=item.provenance,
            display_semantics=(ReportFieldSemantic("evidence_as_of", ReportNumberSemantic.DATE),),
        ))
    return tuple(references)


def _build_quality(
    market: ReportMarketSection,
    families: tuple[ReportValuationFamilySection, ...],
    consensus: ReportConsensusSection,
    expectations: ReportExpectationsSection,
    summary: ReportValuationSummarySection,
) -> ReportDataQualitySection:
    family_map = {item.family: item for item in families}
    rows = (
        ReportDataQualityRow(
            ReportDataQualityCategory.MARKET_PRICE,
            market.availability.value,
            _label(market.availability.value),
            market.blocking_reasons,
            market.supporting_ids,
        ),
        ReportDataQualityRow(
            ReportDataQualityCategory.OWN_HISTORY_VALUATION,
            family_map[ValuationFamily.OWN_HISTORY].family_status.value,
            _label(family_map[ValuationFamily.OWN_HISTORY].family_status.value),
            family_map[ValuationFamily.OWN_HISTORY].blocking_reasons,
            family_map[ValuationFamily.OWN_HISTORY].supporting_ids,
        ),
        ReportDataQualityRow(
            ReportDataQualityCategory.PEER_VALUATION,
            family_map[ValuationFamily.PEER].family_status.value,
            _label(family_map[ValuationFamily.PEER].family_status.value),
            family_map[ValuationFamily.PEER].blocking_reasons,
            family_map[ValuationFamily.PEER].supporting_ids,
        ),
        ReportDataQualityRow(
            ReportDataQualityCategory.FORWARD_CONSENSUS,
            consensus.readiness_status.value if consensus.readiness_status else "unavailable",
            _label(consensus.readiness_status.value if consensus.readiness_status else "unavailable"),
            consensus.blocking_reasons,
            consensus.supporting_ids,
        ),
        ReportDataQualityRow(
            ReportDataQualityCategory.REVERSE_DCF,
            expectations.display_state.value,
            expectations.display_label.title(),
            expectations.blocking_reasons,
            expectations.supporting_ids,
        ),
        ReportDataQualityRow(
            ReportDataQualityCategory.OVERALL_VALUATION_PUBLICATION,
            summary.publication_status.value,
            summary.publication_label,
            summary.blocking_reasons,
            summary.supporting_ids,
        ),
    )
    status = ReportStatus.READY if all(not item.blocking_reasons for item in rows) else ReportStatus.PARTIAL
    supporting = tuple(dict.fromkeys(value for item in rows for value in item.supporting_ids))
    return ReportDataQualitySection(status=status, rows=rows, supporting_ids=supporting)


def build_stock_research_report(
    *,
    identity: CompanyIdentity,
    analysis_as_of,
    publication: ValuationPublicationResult,
    market_price: MarketPriceEvidence | None = None,
    market_comparison: MarketComparisonResult | None = None,
    forward_trajectory: ForwardOperatingTrajectory | None = None,
    reverse_dcf_readiness: ReverseDcfReadiness | None = None,
    scenario_assumptions: tuple[ReverseDcfScenarioAssumption, ...] = (),
) -> StockResearchReport:
    """Build a complete factual report without provider calls or economic calculations."""
    if not isinstance(identity, CompanyIdentity):
        raise TypeError("identity must be CompanyIdentity")
    if not isinstance(publication, ValuationPublicationResult):
        raise TypeError("publication must be ValuationPublicationResult")
    _validate_aware_datetime(analysis_as_of, "analysis_as_of")
    security_id, issuer_id = identity.security_id, identity.issuer_id
    _assert_target_snapshot(
        publication, security_id=security_id, issuer_id=issuer_id,
        analysis_as_of=analysis_as_of, name="publication",
    )
    if market_price is not None:
        _assert_target_snapshot(
            market_price, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="market price",
        )
    if market_comparison is not None:
        _assert_target_snapshot(
            market_comparison, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="market comparison",
        )
        if market_price is None or market_comparison.market_price_evidence != market_price:
            raise ValueError("market comparison must bundle the exact supplied market-price evidence")
        if market_comparison.overall_comparison.valuation_source_id != publication.publication_id:
            raise ValueError("market comparison must reference the exact supplied publication")
    if forward_trajectory is not None:
        _assert_target_snapshot(
            forward_trajectory, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="forward trajectory",
        )
    if reverse_dcf_readiness is not None:
        _assert_target_snapshot(
            reverse_dcf_readiness, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="reverse-DCF readiness",
        )
    for item in scenario_assumptions:
        _assert_target_snapshot(
            item, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="scenario assumption",
        )
    for item in publication.family_evidence:
        _assert_target_snapshot(
            item, security_id=security_id, issuer_id=issuer_id,
            analysis_as_of=analysis_as_of, name="valuation-family evidence",
        )
    for item in publication.supplemental_evidence:
        if (
            item.target_security_id != security_id
            or item.target_issuer_id != issuer_id
            or item.evidence_as_of > analysis_as_of
        ):
            raise ValueError("supplemental evidence target and eligible as-of must match the report")

    identity_section = ReportIdentitySection(
        status=ReportStatus.READY,
        canonical_security_id=security_id,
        canonical_issuer_id=issuer_id,
        display_symbol=identity.canonical_symbol,
        company_name=identity.company_name,
        exchange=identity.exchange,
        issuer_domicile=identity.issuer_domicile,
        listing_country=identity.listing_country,
        reporting_currency=identity.reporting_currency,
        quote_currency=identity.quote_currency,
        quote_unit=identity.quote_unit,
        quote_price_scale=identity.price_scale,
        analysis_as_of=analysis_as_of,
        source_references=_identity_sources(identity),
        supporting_ids=(security_id, issuer_id),
        display_semantics=(ReportFieldSemantic("analysis_as_of", ReportNumberSemantic.DATE),),
    )
    market = _build_market(market_price)
    summary = _build_summary(publication, market_comparison)
    evidence_map = {item.valuation_family: item for item in publication.family_evidence}
    families = tuple(
        _build_family(evidence_map[family], market_comparison)
        if family in evidence_map else _family_placeholder(family)
        for family in (ValuationFamily.OWN_HISTORY, ValuationFamily.PEER)
    )
    consensus = _build_consensus(forward_trajectory)
    expectations = _build_expectations(
        market_comparison, reverse_dcf_readiness, tuple(scenario_assumptions),
    )
    references = _build_references(publication)
    quality = _build_quality(market, families, consensus, expectations, summary)
    sections = (identity_section, market, summary, *families, consensus, expectations, *references, quality)
    provenance = tuple(dict.fromkeys(value for section in sections for value in section.provenance))
    sources = tuple(dict.fromkeys(value for section in sections for value in section.source_references))
    supporting = tuple(dict.fromkeys(value for section in sections for value in section.supporting_ids))
    issues = tuple(value for section in sections for value in section.issues)
    warnings = tuple(dict.fromkeys(value for section in sections for value in section.warnings))
    status = (
        ReportStatus.READY
        if all(section.status is ReportStatus.READY for section in (market, summary, *families, consensus, expectations))
        else ReportStatus.PARTIAL
    )
    report_id = stable_stock_research_report_id(
        security_id, issuer_id, analysis_as_of.isoformat(), publication.publication_id,
        market_price.evidence_id if market_price else "no-market",
        forward_trajectory.trajectory_id if forward_trajectory else "no-consensus",
        *(item.evidence_id for item in (market_comparison.expectation_evidence if market_comparison else ())),
        REPORT_POLICY_ID,
    )
    policy_ids = tuple(dict.fromkeys((
        REPORT_POLICY_ID,
        *(value for item in publication.family_evidence for value in item.policy_ids),
        *(value for item in publication.supplemental_evidence for value in item.policy_ids),
        *((market_price.policy_id,) if market_price else ()),
        *((market_comparison.policy_id,) if market_comparison else ()),
        *((forward_trajectory.policy_id,) if forward_trajectory else ()),
        *(reverse_dcf_readiness.policy_ids if reverse_dcf_readiness else ()),
    )))
    return StockResearchReport(
        report_id=report_id,
        target_security_id=security_id,
        target_issuer_id=issuer_id,
        analysis_as_of=analysis_as_of,
        status=status,
        section_order=REPORT_SECTION_ORDER,
        identity=identity_section,
        market=market,
        valuation_summary=summary,
        valuation_families=families,
        consensus=consensus,
        expectations=expectations,
        references=references,
        data_quality=quality,
        source_references=sources,
        supporting_ids=supporting,
        policy_ids=policy_ids,
        issues=issues,
        warnings=warnings,
        provenance=provenance,
    )
