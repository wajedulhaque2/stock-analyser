from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date
import ast
import importlib
import inspect
import socket
from types import SimpleNamespace

import pytest

from stock_analyser.domain import (
    AggregationStatus,
    DataAvailability,
    EstimateCase,
    Frequency,
    MetricUnit,
    ProviderSymbol,
    PublicationEvidenceType,
    PublicationSupplementalEvidence,
    REPORT_SECTION_ORDER,
    ReportDataQualityCategory,
    ReportExpectationState,
    ReportNumberSemantic,
    ReportStatus,
    ReverseDcfExecutionMode,
    ReverseDcfFormulation,
    ReverseDcfPublicationEligibility,
    ReverseDcfReadiness,
    ReverseDcfReadinessStage,
    ReverseDcfReadinessStatus,
    ReverseDcfScenarioAssumption,
    ReverseDcfScenarioAssumptionType,
    ReverseDcfScenarioSourceCategory,
    StockResearchReport,
    ValuationFamily,
    ValuationMethodStatus,
    stable_publication_supplemental_evidence_id,
)
from stock_analyser.services import build_stock_research_report
from test_v1_market_comparison import (
    ISSUER,
    NOW,
    SECURITY,
    expectation,
    family,
    price,
    provenance,
    publication,
    resolved_publication,
    unresolved_publication,
    wide_publication,
)
from test_v1_reverse_dcf_solver import forward_trajectory as reverse_forward_trajectory


def identity(*, symbol="SYN", security=SECURITY, issuer=ISSUER):
    from stock_analyser.domain import CompanyIdentity

    return CompanyIdentity(
        canonical_symbol=symbol,
        security_id=security,
        issuer_id=issuer,
        company_name="Synthetic Research plc",
        issuer_domicile="GB",
        listing_country="GB",
        exchange="London Stock Exchange",
        sector="Communication Services",
        industry="Internet Content & Information",
        security_type="Ordinary share",
        reporting_currency="USD",
        quote_currency="USD",
        quote_unit="USD",
        price_scale=1.0,
        fiscal_year_end="12-31",
        provider_symbols=(
            ProviderSymbol("yahoo", symbol),
            ProviderSymbol("fiscal", symbol),
            ProviderSymbol("fmp", symbol),
        ),
    )


def trajectory(*, security=SECURITY, issuer=ISSUER, analysis_as_of=NOW, partial=False):
    source = reverse_forward_trajectory()
    periods = tuple(
        replace(
            item,
            target_security_id=security,
            target_issuer_id=issuer,
            analysis_as_of=analysis_as_of,
            ebitda=None if partial else item.revenue * 0.3,
            ebitda_observation_id=None if partial else f"obs:report:ebitda:{item.fiscal_year}",
            revenue_analyst_count=None if item.fiscal_period == "FY3" else item.revenue_analyst_count,
            status=(ReverseDcfReadinessStatus.PARTIAL if partial else item.status),
        )
        for item in source.periods
    )
    return replace(
        source,
        target_security_id=security,
        target_issuer_id=issuer,
        analysis_as_of=analysis_as_of,
        periods=periods,
        status=ReverseDcfReadinessStatus.PARTIAL if partial else ReverseDcfReadinessStatus.READY,
        issues=("EBITDA unavailable for part of consensus",) if partial else (),
    )


def not_ready(*, security=SECURITY, issuer=ISSUER, analysis_as_of=NOW):
    return ReverseDcfReadiness(
        readiness_id="readiness:report:not-ready",
        target_security_id=security,
        target_issuer_id=issuer,
        analysis_as_of=analysis_as_of,
        valuation_currency="USD",
        actual_base_period=date(2025, 12, 31),
        actual_base_status=ReverseDcfReadinessStatus.READY,
        forward_trajectory_id="trajectory:report",
        forward_period_count=5,
        forward_trajectory_status=ReverseDcfReadinessStatus.READY,
        market_enterprise_value_anchor_id="anchor:report",
        market_anchor_status=ReverseDcfReadinessStatus.READY,
        operating_tax_status=ReverseDcfReadinessStatus.READY,
        reinvestment_status=ReverseDcfReadinessStatus.NOT_READY,
        discount_rate_status=ReverseDcfReadinessStatus.NOT_READY,
        terminal_policy_status=ReverseDcfReadinessStatus.READY,
        formulation_readiness=(),
        supported_formulations=(),
        blocked_formulations=(ReverseDcfFormulation.MARKET_IMPLIED_TERMINAL_GROWTH,),
        earliest_blocking_stage=ReverseDcfReadinessStage.FY1_REINVESTMENT_BASE,
        status=ReverseDcfReadinessStatus.NOT_READY,
        issues=(
            "FY1 reinvestment base unavailable",
            "Sales-to-capital unavailable",
            "Production WACC unavailable",
        ),
        warnings=("Canonical execution remains withheld",),
        supporting_ids=("trajectory:report", "anchor:report"),
        policy_ids=("reverse:report",),
        provenance=(provenance(provider="fmp", metric="forward_consensus"),),
    )


def reference(kind: PublicationEvidenceType):
    return PublicationSupplementalEvidence(
        evidence_id=stable_publication_supplemental_evidence_id(SECURITY, kind.value),
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        evidence_type=kind,
        source_result_id=f"external:{kind.value}",
        evidence_as_of=NOW,
        supporting_ids=(f"support:{kind.value}",),
        policy_ids=("reference:only",),
        provenance=(provenance(provider="fmp", metric=kind.value),),
    )


def scenario_assumption(kind: ReverseDcfScenarioAssumptionType):
    values = {
        ReverseDcfScenarioAssumptionType.PRECEDING_ANNUAL_REVENUE: dict(
            value=90.0,
            unit=MetricUnit.CURRENCY,
            currency="USD",
            frequency=Frequency.ANNUAL,
            fiscal_year=2025,
            period_end=date(2025, 12, 31),
        ),
        ReverseDcfScenarioAssumptionType.SALES_TO_CAPITAL: dict(
            value=2.0,
            unit=MetricUnit.RATIO,
            currency=None,
            frequency=None,
            fiscal_year=None,
            period_end=None,
        ),
        ReverseDcfScenarioAssumptionType.WACC: dict(
            value=0.1,
            unit=MetricUnit.PERCENT_DECIMAL,
            currency=None,
            frequency=None,
            fiscal_year=None,
            period_end=None,
        ),
    }[kind]
    return ReverseDcfScenarioAssumption(
        assumption_id=f"scenario:report:{kind.value}",
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        analysis_as_of=NOW,
        assumption_type=kind,
        source_category=ReverseDcfScenarioSourceCategory.USER_SUPPLIED,
        source_label="Fictitious explicit scenario",
        source_date=NOW.date(),
        methodology_label="Explicit synthetic research assumption",
        rationale="Exercise report metadata retention",
        entered_by="synthetic analyst",
        status=ReverseDcfReadinessStatus.READY,
        issues=(),
        warnings=(),
        policy_id="scenario:report",
        provenance=(provenance(provider="explicit-scenario", metric=kind.value),),
        **values,
    )


def report(
    *,
    publication_result=None,
    market=None,
    comparison=None,
    consensus=None,
    readiness=None,
    assumptions=(),
    target_identity=None,
):
    publication_result = publication_result or unresolved_publication()
    target_identity = target_identity or identity()
    return build_stock_research_report(
        identity=target_identity,
        analysis_as_of=NOW,
        publication=publication_result,
        market_price=market,
        market_comparison=comparison,
        forward_trajectory=consensus,
        reverse_dcf_readiness=readiness,
        scenario_assumptions=tuple(assumptions),
    )


def complete_report():
    from stock_analyser.services import compare_publication_to_market

    publication_result = publication(
        family(lower=80, central=95, upper=110),
        family(ValuationFamily.PEER, lower=90, central=105, upper=120),
        supplemental=(
            reference(PublicationEvidenceType.EXTERNAL_ANALYST_TARGET),
            reference(PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE),
        ),
    )
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    canonical = expectation(ReverseDcfExecutionMode.CANONICAL_EVIDENCE)
    comparison = replace(comparison, expectation_evidence=(canonical,))
    return report(
        publication_result=publication_result,
        market=market,
        comparison=comparison,
        consensus=trajectory(),
    )


def partial_report():
    market = price(obs=None, value=0)
    return report(
        market=market,
        consensus=trajectory(partial=True),
        readiness=not_ready(),
    )


def test_complete_report_constructs_with_every_section_ready():
    result = complete_report()
    assert isinstance(result, StockResearchReport)
    assert result.status is ReportStatus.READY
    assert result.market.status is ReportStatus.READY
    assert result.valuation_summary.status is ReportStatus.READY
    assert all(item.status is ReportStatus.READY for item in result.valuation_families)
    assert result.consensus.status is ReportStatus.READY
    assert result.expectations.status is ReportStatus.READY


def test_partial_report_constructs_and_preserves_section_states():
    result = partial_report()
    assert result.status is ReportStatus.PARTIAL
    assert result.market.status is ReportStatus.UNAVAILABLE
    assert result.valuation_summary.publication_status is AggregationStatus.UNRESOLVED
    assert result.consensus.status is ReportStatus.PARTIAL
    assert result.expectations.display_state is ReportExpectationState.NOT_READY


def test_stock_research_report_is_immutable():
    result = complete_report()
    with pytest.raises(FrozenInstanceError):
        result.status = ReportStatus.PARTIAL
    with pytest.raises(FrozenInstanceError):
        result.market.normalized_market_price = 0


def test_canonical_identity_fields_are_retained_exactly():
    result = complete_report()
    assert result.target_security_id == result.identity.canonical_security_id == SECURITY
    assert result.target_issuer_id == result.identity.canonical_issuer_id == ISSUER
    assert result.identity.company_name == "Synthetic Research plc"
    assert result.identity.exchange == "London Stock Exchange"
    assert result.identity.issuer_domicile == result.identity.listing_country == "GB"


def test_display_symbol_is_not_identity_and_london_suffix_is_preserved():
    result = report(target_identity=identity(symbol="SYN.L"))
    assert result.identity.display_symbol == "SYN.L"
    assert result.target_security_id == SECURITY
    assert result.identity.display_symbol not in {result.target_security_id, result.target_issuer_id}


def test_identity_currency_quote_unit_scale_and_snapshot_are_separate():
    target = replace(
        identity(), reporting_currency="USD", quote_currency="GBP",
        quote_unit="GBp", price_scale=0.01,
    )
    result = report(target_identity=target)
    assert result.identity.reporting_currency == "USD"
    assert result.identity.quote_currency == "GBP"
    assert result.identity.quote_unit == "GBp"
    assert result.identity.quote_price_scale == 0.01
    assert result.identity.analysis_as_of == NOW


def test_market_section_copies_11b_values_without_recalculation():
    market = price(value=7.5, raw=750, currency="GBP", quote_unit="GBp", scale=0.01)
    result = report(market=market)
    assert result.market.normalized_market_price is market.normalized_price_per_share == 7.5
    assert result.market.raw_market_quote is market.raw_quote_value == 750
    assert result.market.quote_scale == market.quote_unit_scale == 0.01
    assert result.market.observation_timestamp == market.observation_timestamp


def test_available_market_source_label_and_freshness_are_explicit():
    result = report(market=price())
    assert result.market.source_label == "Yahoo"
    assert result.market.availability is DataAvailability.AVAILABLE
    assert result.market.freshness_label == "Eligible under upstream price policy"
    assert {item.display_name for item in result.market.source_references} == {"Yahoo"}


def test_unavailable_market_price_remains_null_with_no_fallback():
    result = partial_report()
    assert result.market.normalized_market_price is None
    assert result.market.raw_market_quote is not None
    assert result.valuation_families[0].central_value == 110
    assert result.market.normalized_market_price != result.valuation_families[0].central_value


@pytest.mark.parametrize(
    "publication_factory,expected_status,expected_label",
    [
        (resolved_publication, AggregationStatus.RESOLVED, "Resolved"),
        (wide_publication, AggregationStatus.WIDE, "Wide"),
        (unresolved_publication, AggregationStatus.UNRESOLVED, "Unresolved"),
        (publication, AggregationStatus.UNAVAILABLE, "Unavailable"),
    ],
)
def test_publication_status_and_controlled_labels_are_preserved(publication_factory, expected_status, expected_label):
    result = report(publication_result=publication_factory())
    assert result.valuation_summary.publication_status is expected_status
    assert result.valuation_summary.publication_label == expected_label


def test_resolved_overall_central_is_exposed_only_as_existing_upstream_value():
    source = resolved_publication()
    result = report(publication_result=source)
    assert result.valuation_summary.overall_value_label == "Overall fair value"
    assert result.valuation_summary.overall_central_value == source.overall_central_value == 100
    assert result.valuation_summary.central_estimator is source.central_estimator


@pytest.mark.parametrize("publication_factory", [unresolved_publication, wide_publication, publication])
def test_nonresolved_overall_central_is_null_and_withheld(publication_factory):
    result = report(publication_result=publication_factory())
    assert result.valuation_summary.overall_central_value is None
    assert result.valuation_summary.overall_value_label == "Withheld"
    assert result.valuation_summary.overall_central_value != 0


def test_family_envelope_and_overlap_are_copied_as_separate_concepts():
    source = resolved_publication()
    result = report(publication_result=source)
    summary = result.valuation_summary
    assert (summary.envelope_lower, summary.envelope_upper, summary.envelope_semantics) == (
        source.envelope_lower, source.envelope_upper, source.envelope_semantics,
    )
    assert (summary.overlap_lower, summary.overlap_upper, summary.overlap_semantics) == (
        source.overlap_lower, source.overlap_upper, source.overlap_semantics,
    )
    assert (summary.envelope_lower, summary.envelope_upper) != (summary.overlap_lower, summary.overlap_upper)


def test_known_family_rows_are_deterministic_and_never_hidden():
    result = report()
    assert tuple(item.family for item in result.valuation_families) == (
        ValuationFamily.OWN_HISTORY, ValuationFamily.PEER,
    )
    peer = result.valuation_families[1]
    assert peer.family_status is ValuationMethodStatus.UNAVAILABLE
    assert peer.lower_value is peer.central_value is peer.upper_value is None
    assert peer.blocking_reasons


def test_own_history_family_values_and_method_are_retained():
    source = unresolved_publication().family_evidence[0]
    own = report().valuation_families[0]
    assert own.method_label == source.selected_method
    assert (own.lower_value, own.central_value, own.upper_value) == (
        source.lower_value, source.central_value, source.upper_value,
    )
    assert own.source_result_id == source.source_result_id
    assert own.central_eligible is True


def test_unavailable_peer_upstream_evidence_retains_reason_and_ids():
    unavailable = family(
        ValuationFamily.PEER,
        lower=None, central=None, upper=None,
        status=ValuationMethodStatus.UNAVAILABLE,
        eligible=False,
        suffix="unavailable-peer",
    )
    result = report(publication_result=publication(family(), unavailable))
    peer = result.valuation_families[1]
    assert peer.status is ReportStatus.UNAVAILABLE
    assert peer.source_result_id == unavailable.source_result_id
    assert unavailable.evidence_id in peer.supporting_ids


def test_family_price_gaps_are_copied_unchanged_from_11b():
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price(value=100)
    comparison = compare_publication_to_market(publication_result, market)
    result = report(publication_result=publication_result, market=market, comparison=comparison)
    source = comparison.family_comparisons[0]
    own = result.valuation_families[0]
    assert (own.lower_gap, own.central_gap, own.upper_gap) == (
        source.lower_gap, source.central_gap, source.upper_gap,
    )
    assert (own.lower_gap_percent, own.central_gap_percent, own.upper_gap_percent) == (
        source.lower_gap_percent, source.central_gap_percent, source.upper_gap_percent,
    )


def test_overall_price_gaps_are_exposed_only_when_existing_comparison_is_available():
    from stock_analyser.services import compare_publication_to_market

    publication_result = resolved_publication()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    result = report(publication_result=publication_result, market=market, comparison=comparison)
    assert result.valuation_summary.overall_comparison_status is DataAvailability.AVAILABLE
    assert result.valuation_summary.overall_central_gap == comparison.overall_comparison.central_gap
    assert result.valuation_summary.overall_central_gap_percent == comparison.overall_comparison.central_gap_percent


@pytest.mark.parametrize("publication_factory", [unresolved_publication, wide_publication])
def test_nonresolved_overall_comparison_gaps_remain_null(publication_factory):
    from stock_analyser.services import compare_publication_to_market

    publication_result = publication_factory()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    summary = report(publication_result=publication_result, market=market, comparison=comparison).valuation_summary
    assert summary.overall_comparison_status is DataAvailability.UNAVAILABLE
    assert summary.overall_lower_gap is summary.overall_central_gap is summary.overall_upper_gap is None


def test_family_central_is_never_promoted_to_overall_central():
    result = report()
    assert result.valuation_families[0].central_value == 110
    assert result.valuation_summary.overall_central_value is None


def test_forward_consensus_periods_and_values_are_retained_unchanged():
    source = trajectory()
    result = report(consensus=source).consensus
    assert result.period_count == source.period_count == 5
    for shown, upstream in zip(result.periods, source.periods):
        assert shown.horizon_label == upstream.fiscal_period
        assert shown.fiscal_period_end == upstream.fiscal_period_end
        assert shown.revenue == upstream.revenue
        assert shown.ebit == upstream.ebit
        assert shown.ebitda == upstream.ebitda
        assert shown.ebit_margin == upstream.operating_margin
        assert shown.revenue_growth == upstream.revenue_growth
        assert shown.estimate_as_of == upstream.estimate_as_of


def test_forward_consensus_analyst_count_null_is_preserved():
    result = report(consensus=trajectory()).consensus
    fy3 = next(item for item in result.periods if item.horizon_label == "FY3")
    assert fy3.revenue_analyst_count is None


def test_consensus_source_is_labeled_fmp_and_not_internal_forecast():
    result = report(consensus=trajectory()).consensus
    assert result.source_label == "FMP forward consensus"
    assert {item.display_name for item in result.source_references} == {"FMP"}
    assert "generated" not in result.source_label.lower()


def test_missing_consensus_is_visible_and_does_not_invalidate_other_sections():
    result = report(market=price())
    assert result.consensus.period_count == 0
    assert result.consensus.status is ReportStatus.UNAVAILABLE
    assert result.market.status is ReportStatus.READY
    assert result.identity.status is ReportStatus.READY


def test_canonical_reverse_dcf_is_labeled_expectations_not_fair_value():
    result = complete_report().expectations
    assert result.display_state is ReportExpectationState.CANONICAL_EXPECTATIONS
    assert result.entries[0].state_label == "CANONICAL EXPECTATIONS"
    assert result.entries[0].publication_eligibility is ReverseDcfPublicationEligibility.CANONICAL
    assert "fair value" not in result.display_label.lower()
    assert result.entries[0].implied_terminal_growth == 0.03


def test_reverse_dcf_not_ready_retains_all_upstream_blockers():
    result = partial_report().expectations
    assert result.display_state is ReportExpectationState.NOT_READY
    assert result.entries == ()
    assert "FY1 Reinvestment Base" in result.blocking_reasons
    assert "FY1 reinvestment base unavailable" in result.blocking_reasons
    assert "Sales-to-capital unavailable" in result.blocking_reasons
    assert "Production WACC unavailable" in result.blocking_reasons
    assert all("implied" not in item.message.lower() for item in result.issues)


def test_reverse_dcf_not_run_is_distinct_from_not_ready():
    result = report().expectations
    assert result.display_state is ReportExpectationState.NOT_RUN
    assert result.display_label == "NOT RUN"
    assert result.readiness_status is None


def test_scenario_expectations_prominently_retain_all_assumption_metadata():
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    assumptions = tuple(scenario_assumption(kind) for kind in ReverseDcfScenarioAssumptionType)
    scenario = replace(
        expectation(ReverseDcfExecutionMode.EXPLICIT_SCENARIO),
        scenario_assumption_ids=tuple(item.assumption_id for item in assumptions),
    )
    comparison = replace(comparison, expectation_evidence=(scenario,))
    result = report(
        publication_result=publication_result,
        market=market,
        comparison=comparison,
        assumptions=assumptions,
    ).expectations
    assert result.display_state is ReportExpectationState.SCENARIO_EXPECTATIONS
    assert result.display_label == "SCENARIO EXPECTATIONS"
    entry = result.entries[0]
    assert entry.publication_eligibility is ReverseDcfPublicationEligibility.SCENARIO_ONLY
    assert {item.assumption_type for item in entry.scenario_assumptions} == set(ReverseDcfScenarioAssumptionType)
    assert {item.value for item in entry.scenario_assumptions} == {90.0, 2.0, 0.1}
    assert all(item.methodology_label and item.rationale and item.entered_by for item in entry.scenario_assumptions)


def test_scenario_expectation_fails_closed_when_assumption_metadata_is_missing():
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    comparison = replace(
        comparison,
        expectation_evidence=(expectation(ReverseDcfExecutionMode.EXPLICIT_SCENARIO),),
    )
    with pytest.raises(ValueError, match="every explicit scenario assumption"):
        report(publication_result=publication_result, market=market, comparison=comparison)


@pytest.mark.parametrize(
    "kind",
    [
        PublicationEvidenceType.EXTERNAL_ANALYST_TARGET,
        PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE,
    ],
)
def test_external_evidence_is_retained_as_reference_only(kind):
    publication_result = publication(family(), supplemental=(reference(kind),))
    result = report(publication_result=publication_result)
    assert len(result.references) == 1
    shown = result.references[0]
    assert shown.reference_type is kind
    assert shown.reference_only_label == "REFERENCE ONLY"
    assert shown.central_valuation_eligible is False
    assert shown.source_result_id == f"external:{kind.value}"


def test_references_cannot_appear_as_valuation_families_or_overall_value():
    publication_result = publication(
        family(),
        supplemental=(reference(PublicationEvidenceType.EXTERNAL_ANALYST_TARGET),),
    )
    result = report(publication_result=publication_result)
    assert tuple(item.family for item in result.valuation_families) == (
        ValuationFamily.OWN_HISTORY, ValuationFamily.PEER,
    )
    assert result.valuation_summary.overall_central_value is None
    assert result.references[0].source_result_id not in result.valuation_summary.supporting_ids


def test_data_quality_section_has_exact_controlled_categories_and_no_score():
    quality = partial_report().data_quality
    assert tuple(item.category for item in quality.rows) == tuple(ReportDataQualityCategory)
    assert not hasattr(quality, "score")
    assert not hasattr(quality, "confidence")
    assert all(not hasattr(item, "score") for item in quality.rows)


def test_data_quality_rows_preserve_section_statuses():
    result = partial_report()
    rows = {item.category: item for item in result.data_quality.rows}
    assert rows[ReportDataQualityCategory.MARKET_PRICE].status_code == "unavailable"
    assert rows[ReportDataQualityCategory.OWN_HISTORY_VALUATION].status_code == "valid"
    assert rows[ReportDataQualityCategory.PEER_VALUATION].status_code == "unavailable"
    assert rows[ReportDataQualityCategory.FORWARD_CONSENSUS].status_code == "partial"
    assert rows[ReportDataQualityCategory.REVERSE_DCF].status_code == "not_ready"
    assert rows[ReportDataQualityCategory.OVERALL_VALUATION_PUBLICATION].status_code == "unresolved"


def test_issues_warnings_and_blockers_remain_structurally_separate():
    result = partial_report()
    assert result.consensus.issues
    assert result.expectations.warnings == ("Canonical execution remains withheld",)
    assert result.expectations.blocking_reasons
    assert result.expectations.issues != result.expectations.warnings


def test_supporting_ids_policy_ids_and_provenance_are_retained():
    result = complete_report()
    assert result.supporting_ids
    assert result.policy_ids
    assert result.provenance
    assert result.market.market_price_evidence_id in result.supporting_ids
    assert result.valuation_summary.publication_id in result.supporting_ids
    assert all(item.supporting_ids for item in result.valuation_families)


def test_source_references_are_safe_and_deduplicated():
    result = complete_report()
    labels = {item.display_name for item in result.source_references}
    assert {"Yahoo", "FMP"}.issubset(labels)
    rendered = repr(result.source_references).lower()
    assert "api_key" not in rendered
    assert "authorization" not in rendered
    assert "companykey" not in rendered
    assert len(result.source_references) == len(set(result.source_references))


def test_unrounded_numeric_values_are_retained_exactly():
    from stock_analyser.services import compare_publication_to_market

    publication_result = publication(family(lower=100.000001, central=110.000003, upper=120.000007))
    market = price(value=97.000009)
    comparison = compare_publication_to_market(publication_result, market)
    result = report(publication_result=publication_result, market=market, comparison=comparison)
    own = result.valuation_families[0]
    assert own.central_value == 110.000003
    assert own.central_gap == comparison.family_comparisons[0].central_gap
    assert own.central_gap_percent == comparison.family_comparisons[0].central_gap_percent


def test_display_semantics_are_hints_not_formatted_currency_strings():
    result = complete_report()
    semantics = {item.semantic for item in result.valuation_summary.display_semantics}
    assert {ReportNumberSemantic.CURRENCY, ReportNumberSemantic.PERCENTAGE}.issubset(semantics)
    assert isinstance(result.valuation_summary.overall_central_value, float)
    assert "$" not in repr(result.valuation_summary.display_semantics)


@pytest.mark.parametrize(
    "forbidden",
    ["<html", "<style", "<div", "st.metric", "st.dataframe", "st.tabs"],
)
def test_report_contract_and_service_contain_no_html_css_or_streamlit(forbidden):
    domain_source = open("src/stock_analyser/domain/research_report.py", encoding="utf-8").read().lower()
    service_source = open("src/stock_analyser/services/research_report.py", encoding="utf-8").read().lower()
    assert forbidden not in domain_source
    assert forbidden not in service_source


@pytest.mark.parametrize(
    "forbidden",
    [
        "buy", "hold", "sell", "overweight", "underweight", "cheap", "expensive",
        "attractive", "investment_case", "bull_case", "bear_case", "catalyst_score",
        "risk_score", "generated_narrative", "ollama",
    ],
)
def test_report_contract_has_no_judgment_stance_or_generated_narrative_fields(forbidden):
    fields = set(StockResearchReport.__dataclass_fields__)
    for section_name in (
        "ReportValuationSummarySection", "ReportValuationFamilySection",
        "ReportExpectationsSection", "ReportDataQualitySection",
    ):
        fields |= set(getattr(importlib.import_module("stock_analyser.domain"), section_name).__dataclass_fields__)
    assert forbidden not in fields


def test_service_has_no_valuation_price_gap_or_reverse_dcf_recalculation():
    module = importlib.import_module("stock_analyser.services.research_report")
    source = inspect.getsource(module)
    identifiers = {
        node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
    }
    forbidden = {
        "calculate_own_history_valuation", "calculate_peer_target_valuation",
        "publish_cross_family_valuation", "compare_publication_to_market",
        "expectation_gap_from_reverse_dcf", "execute_reverse_dcf",
        "solve_market_implied_terminal_growth",
    }
    assert identifiers.isdisjoint(forbidden)


@pytest.mark.parametrize(
    "source_name,changed",
    [
        ("publication", {"target_security_id": "security:other"}),
        ("publication", {"target_issuer_id": "issuer:other"}),
        ("publication", {"analysis_as_of": NOW.replace(day=30)}),
        ("market", {"target_security_id": "security:other"}),
        ("market", {"target_issuer_id": "issuer:other"}),
        ("market", {"analysis_as_of": NOW.replace(day=30)}),
        ("trajectory", {"target_security_id": "security:other"}),
        ("trajectory", {"target_issuer_id": "issuer:other"}),
        ("readiness", {"analysis_as_of": NOW.replace(day=30)}),
    ],
)
def test_mixed_target_or_snapshot_sections_fail_closed(source_name, changed):
    kwargs = {}
    if source_name == "publication":
        security = changed.get("target_security_id", SECURITY)
        issuer = changed.get("target_issuer_id", ISSUER)
        as_of = changed.get("analysis_as_of", NOW)
        kwargs["publication_result"] = publication(
            family(security=security, issuer=issuer, as_of=as_of),
            target_security_id=security,
            target_issuer_id=issuer,
            analysis_as_of=as_of,
        )
    elif source_name == "market":
        kwargs["market"] = replace(price(), **changed)
    elif source_name == "trajectory":
        kwargs["consensus"] = replace(trajectory(), **changed)
    else:
        kwargs["readiness"] = replace(not_ready(), **changed)
    with pytest.raises(ValueError, match="must match the report"):
        report(**kwargs)


def test_market_comparison_must_reference_exact_price_and_publication():
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    with pytest.raises(ValueError, match="exact supplied market-price"):
        report(publication_result=publication_result, market=price(value=91), comparison=comparison)
    with pytest.raises(ValueError, match="exact supplied publication"):
        report(
            publication_result=replace(publication_result, publication_id="publication:other"),
            market=market,
            comparison=comparison,
        )


def test_mismatched_family_evidence_inside_publication_fails_closed():
    mismatched = family(security="security:other", eligible=False)
    source = publication(mismatched)
    with pytest.raises(ValueError, match="valuation-family evidence"):
        report(publication_result=source)


def test_future_supplemental_evidence_fails_closed_but_earlier_reference_is_allowed():
    ref = reference(PublicationEvidenceType.EXTERNAL_ANALYST_TARGET)
    earlier = replace(ref, evidence_as_of=NOW.replace(day=30))
    assert report(publication_result=publication(family(), supplemental=(earlier,))).references
    future = replace(ref, evidence_as_of=NOW.replace(month=9, day=1))
    with pytest.raises(ValueError, match="eligible as-of"):
        report(publication_result=publication(family(), supplemental=(future,)))


def test_missing_optional_sections_keep_report_factual_and_displayable():
    result = report()
    assert result.status is ReportStatus.PARTIAL
    assert result.identity.status is ReportStatus.READY
    assert result.valuation_families[0].status is ReportStatus.READY
    assert result.market.normalized_market_price is None
    assert result.consensus.periods == ()
    assert result.expectations.entries == ()


def test_deterministic_section_order_has_no_layout_dimensions():
    result = complete_report()
    assert result.section_order == REPORT_SECTION_ORDER
    assert result.section_order == complete_report().section_order
    assert all(word not in " ".join(result.section_order) for word in ("grid", "column", "width", "tab"))


def test_report_id_is_deterministic_and_changes_with_snapshot_evidence():
    first = complete_report()
    second = complete_report()
    assert first.report_id == second.report_id
    changed_market = price(value=91)
    from stock_analyser.services import compare_publication_to_market
    publication_result = resolved_publication()
    changed = report(
        publication_result=publication_result,
        market=changed_market,
        comparison=compare_publication_to_market(publication_result, changed_market),
        consensus=trajectory(),
    )
    assert first.report_id != changed.report_id


def test_production_service_has_no_provider_calls_ticker_branches_or_ui_imports():
    module = importlib.import_module("stock_analyser.services.research_report")
    source = inspect.getsource(module).lower()
    assert "stock_analyser.providers" not in source
    assert "requests" not in source and "yfinance" not in source and "streamlit" not in source
    assert "if ticker" not in source and "== \"meta\"" not in source and "endswith(\".l\")" not in source


def test_report_builder_remains_outside_feature_flagged_streamlit_route():
    app_source = open("app.py", encoding="utf-8").read()
    assert "build_stock_research_report" not in app_source
    assert "stock_analyser.services.research_report" not in app_source
    assert "run_live_research_report_audit" not in app_source
    assert app_source.index("if is_v1_report_ui_enabled():") < app_source.index(
        "render_v1_report_route()"
    )


def test_default_pytest_path_makes_zero_network_calls(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    result = complete_report()
    assert result.status is ReportStatus.READY


def test_live_audit_wrapper_reuses_existing_bounded_audits_once(monkeypatch):
    from stock_analyser.live_research_report_audit import run_live_research_report_audit
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price()
    comparison = compare_publication_to_market(publication_result, market)
    market_outcome = SimpleNamespace(
        publication_outcome=SimpleNamespace(
            own_history=SimpleNamespace(result=SimpleNamespace(identity=identity())),
            peer_family=None,
            publication=publication_result,
        ),
        market_price=market,
        comparison=comparison,
    )
    reverse_outcome = SimpleNamespace(identity=identity(), trajectory=trajectory(), readiness=not_ready())
    calls = []
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_market_comparison_audit",
        lambda *args, **kwargs: calls.append("market") or market_outcome,
    )
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_reverse_dcf_audit",
        lambda *args, **kwargs: calls.append("reverse") or reverse_outcome,
    )
    outcome = run_live_research_report_audit("syn", environment={}, analysis_as_of=NOW)
    assert calls == ["market", "reverse"]
    assert outcome.report is not None
    assert outcome.report.consensus.period_count == 5
    assert outcome.report.expectations.display_state is ReportExpectationState.NOT_READY


def test_live_audit_fails_closed_on_cross_audit_identity_mismatch(monkeypatch):
    from stock_analyser.live_research_report_audit import run_live_research_report_audit
    from stock_analyser.services import compare_publication_to_market

    publication_result = unresolved_publication()
    market = price()
    market_outcome = SimpleNamespace(
        publication_outcome=SimpleNamespace(
            own_history=SimpleNamespace(result=SimpleNamespace(identity=identity())),
            peer_family=None,
            publication=publication_result,
        ),
        market_price=market,
        comparison=compare_publication_to_market(publication_result, market),
    )
    reverse_outcome = SimpleNamespace(
        identity=identity(security="security:other"), trajectory=None, readiness=None,
    )
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_market_comparison_audit",
        lambda *args, **kwargs: market_outcome,
    )
    monkeypatch.setattr(
        "stock_analyser.live_research_report_audit.run_live_reverse_dcf_audit",
        lambda *args, **kwargs: reverse_outcome,
    )
    outcome = run_live_research_report_audit("SYN", environment={}, analysis_as_of=NOW)
    assert outcome.report is None
    assert any("did not match" in item for item in outcome.safe_notes)


def test_safe_report_renderer_is_structured_secret_free_and_nonjudgmental():
    from stock_analyser.live_research_report_audit import (
        LiveResearchReportAuditOutcome,
        render_live_research_report_audit,
    )

    result = partial_report()
    output = render_live_research_report_audit(LiveResearchReportAuditOutcome(
        symbol="SYN",
        analysis_as_of=NOW,
        report=result,
        market_audit=SimpleNamespace(),
        reverse_dcf_audit=SimpleNamespace(),
    )).lower()
    for section in ("identity", "market", "valuation summary", "families", "consensus", "expectations", "references", "data quality"):
        assert section in output
    assert "overall fair value: withheld" in output
    assert "reverse dcf: not ready" in output
    assert "no valuation recalculation" in output
    assert "api_key" not in output and "authorization" not in output and "companykey" not in output


def test_live_report_entry_point_is_explicit_and_not_imported_by_streamlit():
    import stock_analyser.live_research_report_audit as module

    source = inspect.getsource(module)
    app_source = open("app.py", encoding="utf-8").read()
    assert "run_live_research_report_audit(" in source
    assert "live_research_report_audit" not in app_source
