from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import ast
import inspect
import importlib
import math
import socket

import pytest

from stock_analyser.domain import (
    AggregationStatus,
    ExternalReferenceType,
    ExternalValuationReference,
    FamilyValuationEvidence,
    PublicationCentralEstimator,
    PublicationEvidenceType,
    PublicationRangeSemantics,
    PublicationSupplementalEvidence,
    Provenance,
    ValuationFamily,
    ValuationMethodStatus,
    ValuationResult,
    stable_family_valuation_evidence_id,
    stable_publication_supplemental_evidence_id,
)
from stock_analyser.services import (
    family_evidence_from_generic_result,
    publish_cross_family_valuation,
    supplemental_evidence_from_external_reference,
)
from stock_analyser.live_publication_audit import (
    LivePublicationAuditOutcome,
    render_live_publication_audit,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
SECURITY = "security:synthetic:target"
ISSUER = "issuer:synthetic:target"


def provenance(provider="synthetic", source="family"):
    return Provenance(
        provider=provider,
        endpoint_or_dataset=f"{source}_canonical_result",
        provider_symbol="SYN",
        retrieved_at=NOW,
        as_of_at=NOW,
        source_metric="per_share_valuation",
    )


def family(
    valuation_family=ValuationFamily.OWN_HISTORY,
    lower=90.0,
    central=110.0,
    upper=130.0,
    *,
    method=None,
    source_result_id=None,
    status=ValuationMethodStatus.VALID,
    eligible=True,
    security=SECURITY,
    issuer=ISSUER,
    as_of=NOW,
    currency="USD",
    unit="USD/share",
    supporting=("support:shared:fmp", "support:shared:fiscal"),
):
    method = method or ("EV_EBITDA" if valuation_family is ValuationFamily.PEER else "P_E")
    source_result_id = source_result_id or f"result:{valuation_family.value}:{method}"
    return FamilyValuationEvidence(
        evidence_id=stable_family_valuation_evidence_id(
            security, issuer, valuation_family.value, source_result_id, as_of.isoformat(),
        ),
        target_security_id=security,
        target_issuer_id=issuer,
        valuation_family=valuation_family,
        selected_method=method,
        source_result_id=source_result_id,
        analysis_as_of=as_of,
        currency=currency,
        per_share_unit=unit,
        lower_value=lower,
        central_value=central,
        upper_value=upper,
        family_status=status,
        central_valuation_eligible=eligible,
        supporting_ids=supporting,
        policy_ids=(f"policy:{valuation_family.value}",),
        provenance=(provenance(source=valuation_family.value),),
    )


def supplement(evidence_type, suffix="one"):
    evidence_id = stable_publication_supplemental_evidence_id(
        SECURITY, ISSUER, evidence_type.value, suffix,
    )
    return PublicationSupplementalEvidence(
        evidence_id=evidence_id,
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        evidence_type=evidence_type,
        source_result_id=f"source:{suffix}",
        evidence_as_of=NOW,
        central_valuation_eligible=False,
        supporting_ids=(f"support:{suffix}",),
        policy_ids=(f"policy:{suffix}",),
        provenance=(provenance(source=suffix),),
    )


def publish(*families, supplemental=(), **changes):
    values = dict(
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        analysis_as_of=NOW,
        family_candidates=tuple(families),
        supplemental_evidence=tuple(supplemental),
    )
    values.update(changes)
    return publish_cross_family_valuation(**values)


def test_own_history_and_peer_are_the_only_supported_central_families():
    own = family()
    peer = family(ValuationFamily.PEER, 100, 115, 140)
    result = publish(own, peer)
    assert result.eligible_family_ids == (ValuationFamily.OWN_HISTORY, ValuationFamily.PEER)
    with pytest.raises(ValueError, match="OWN_HISTORY and PEER"):
        family(ValuationFamily.REVERSE_CASH_FLOW)


def test_synthetic_resolved_example_uses_threshold_free_common_intersection():
    result = publish(
        family(lower=90, central=110, upper=130),
        family(ValuationFamily.PEER, lower=100, central=115, upper=140),
    )
    assert result.publication_status is AggregationStatus.RESOLVED
    assert result.overall_central_value == 112.5
    assert result.central_estimator is PublicationCentralEstimator.MEDIAN_OF_FAMILY_CENTRALS
    assert (result.envelope_lower, result.envelope_upper) == (90, 140)
    assert result.envelope_semantics is PublicationRangeSemantics.FAMILY_ENVELOPE
    assert (result.overlap_lower, result.overlap_upper) == (100, 130)
    assert result.overlap_semantics is PublicationRangeSemantics.COMMON_OVERLAP


def test_synthetic_wide_example_withholds_central():
    result = publish(
        family(lower=90, central=95, upper=120),
        family(ValuationFamily.PEER, lower=110, central=115, upper=140),
    )
    assert result.publication_status is AggregationStatus.WIDE
    assert (result.overlap_lower, result.overlap_upper) == (110, 120)
    assert result.overall_central_value is None and result.central_estimator is None


def test_synthetic_conflict_example_is_unresolved_and_retains_descriptive_envelope():
    result = publish(
        family(lower=80, central=90, upper=100),
        family(ValuationFamily.PEER, lower=110, central=120, upper=130),
    )
    assert result.publication_status is AggregationStatus.UNRESOLVED
    assert result.overall_central_value is None
    assert (result.envelope_lower, result.envelope_upper) == (80, 130)
    assert result.overlap_lower is None and result.overlap_upper is None


def test_zero_families_is_unavailable_without_range_or_hidden_central():
    result = publish()
    assert result.publication_status is AggregationStatus.UNAVAILABLE
    assert result.eligible_family_count == 0
    assert result.overall_central_value is None
    assert result.envelope_lower is None and result.overlap_lower is None


@pytest.mark.parametrize("valuation_family", [ValuationFamily.OWN_HISTORY, ValuationFamily.PEER])
def test_one_complete_family_is_unresolved_and_its_range_is_honestly_labeled(valuation_family):
    result = publish(family(valuation_family))
    assert result.publication_status is AggregationStatus.UNRESOLVED
    assert result.eligible_family_count == 1
    assert result.overall_central_value is None
    assert result.envelope_semantics is PublicationRangeSemantics.SINGLE_FAMILY_RANGE


def test_two_methods_inside_own_history_do_not_satisfy_family_minimum():
    result = publish(
        family(method="P_E", source_result_id="own:pe"),
        family(method="EV_EBITDA", source_result_id="own:ev"),
    )
    assert result.publication_status is AggregationStatus.UNAVAILABLE
    assert result.eligible_family_count == 0
    assert any("multiple candidates" in reason for reason in result.blocking_reasons)


def test_duplicate_family_fails_closed_without_selecting_high_low_or_nearest():
    result = publish(
        family(central=100, method="P_E", source_result_id="own:a"),
        family(central=120, method="EV_EBITDA", source_result_id="own:b"),
        family(ValuationFamily.PEER, 90, 110, 130),
    )
    assert result.eligible_family_ids == (ValuationFamily.PEER,)
    assert result.publication_status is AggregationStatus.UNRESOLVED


@pytest.mark.parametrize(
    "changed,reason",
    [
        ({"security": "security:other"}, "target identity"),
        ({"issuer": "issuer:other"}, "target identity"),
        ({"as_of": NOW - timedelta(days=1)}, "analysis snapshot"),
    ],
)
def test_identity_and_snapshot_mismatch_exclude_only_the_defective_family(changed, reason):
    result = publish(
        family(),
        family(ValuationFamily.PEER, 100, 115, 140, **changed),
        currency="USD",
        per_share_unit="USD/share",
    )
    assert result.eligible_family_count == 1
    assert result.publication_status is AggregationStatus.UNRESOLVED
    assert any(reason in item for item in result.blocking_reasons)


def test_currency_conflict_fails_closed_without_implicit_fx():
    result = publish(
        family(currency="USD", unit="USD/share"),
        family(ValuationFamily.PEER, 100, 115, 140, currency="GBP", unit="GBP/share"),
    )
    assert result.publication_status is AggregationStatus.UNAVAILABLE
    assert result.currency is None and result.per_share_unit is None
    assert any("no FX" in reason for reason in result.blocking_reasons)


def test_explicit_publication_currency_excludes_mismatched_family_without_conversion():
    result = publish(
        family(currency="USD", unit="USD/share"),
        family(ValuationFamily.PEER, 100, 115, 140, currency="GBP", unit="GBP/share"),
        currency="USD",
        per_share_unit="USD/share",
    )
    assert result.eligible_family_ids == (ValuationFamily.OWN_HISTORY,)
    assert result.publication_status is AggregationStatus.UNRESOLVED


def test_gbp_per_share_and_gbpence_per_share_are_not_interchangeable():
    result = publish(
        family(currency="GBP", unit="GBP/share"),
        family(ValuationFamily.PEER, 100, 115, 140, currency="GBP", unit="GBp/share"),
    )
    assert result.publication_status is AggregationStatus.UNAVAILABLE
    assert result.eligible_family_count == 0


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"lower": float("nan")}, "finite"),
        ({"central": float("inf")}, "finite"),
        ({"upper": -1}, "positive"),
        ({"lower": 120, "central": 110, "upper": 130}, "never reordered"),
        ({"lower": 90, "central": 140, "upper": 130}, "never reordered"),
    ],
)
def test_invalid_family_points_fail_at_the_contract_and_are_never_repaired(changes, match):
    with pytest.raises(ValueError, match=match):
        family(**changes)


def test_valid_family_requires_all_three_publication_points():
    with pytest.raises(ValueError, match="requires lower"):
        family(upper=None)


def test_partial_family_is_retained_but_never_counts():
    partial = family(
        upper=None,
        status=ValuationMethodStatus.PARTIAL,
        eligible=False,
    )
    result = publish(partial, family(ValuationFamily.PEER, 100, 115, 140))
    assert partial in result.family_evidence
    assert result.eligible_family_ids == (ValuationFamily.PEER,)
    assert result.publication_status is AggregationStatus.UNRESOLVED


def test_partial_or_unavailable_evidence_cannot_claim_central_eligibility():
    with pytest.raises(ValueError, match="central-eligible"):
        family(upper=None, status=ValuationMethodStatus.PARTIAL, eligible=True)
    with pytest.raises(ValueError, match="central-eligible"):
        family(
            lower=None, central=None, upper=None,
            status=ValuationMethodStatus.UNAVAILABLE, eligible=True,
        )


def test_common_overlap_bounds_use_maximum_lower_and_minimum_upper():
    result = publish(
        family(lower=80, central=110, upper=150),
        family(ValuationFamily.PEER, 100, 120, 130),
    )
    assert result.overlap_lower == 100
    assert result.overlap_upper == 130


def test_touching_ranges_have_a_nonempty_zero_width_overlap():
    result = publish(
        family(lower=80, central=100, upper=110),
        family(ValuationFamily.PEER, 110, 110, 140),
    )
    assert result.publication_status is AggregationStatus.WIDE
    assert result.overlap_lower == result.overlap_upper == 110
    assert result.diagnostics.overlap_width == 0


def test_both_family_centrals_must_lie_in_common_overlap_for_resolved():
    resolved = publish(
        family(lower=90, central=110, upper=130),
        family(ValuationFamily.PEER, 100, 115, 140),
    )
    wide = publish(
        family(lower=90, central=95, upper=130),
        family(ValuationFamily.PEER, 100, 115, 140),
    )
    assert resolved.publication_status is AggregationStatus.RESOLVED
    assert wide.publication_status is AggregationStatus.WIDE


def test_type7_two_family_median_is_deterministic_and_not_labeled_as_a_split():
    result = publish(
        family(lower=90, central=101, upper=130),
        family(ValuationFamily.PEER, 90, 114, 140),
    )
    assert result.overall_central_value == 107.5
    assert result.central_estimator.value == "median_of_family_centrals"


def test_envelope_is_not_replaced_by_narrower_overlap():
    result = publish(
        family(lower=90, central=110, upper=130),
        family(ValuationFamily.PEER, 100, 115, 140),
    )
    assert (result.envelope_lower, result.envelope_upper) == (90, 140)
    assert (result.overlap_lower, result.overlap_upper) == (100, 130)


def test_dispersion_diagnostics_are_exact_and_descriptive_only():
    result = publish(
        family(lower=90, central=110, upper=130),
        family(ValuationFamily.PEER, 100, 115, 140),
    )
    diagnostic = result.diagnostics
    assert diagnostic.family_count == 2
    assert diagnostic.minimum_family_central == 110
    assert diagnostic.maximum_family_central == 115
    assert diagnostic.median_family_central == 112.5
    assert diagnostic.central_spread == 5
    assert diagnostic.central_spread_to_median == pytest.approx(5 / 112.5)
    assert diagnostic.envelope_width == 50
    assert diagnostic.overlap_width == 30


@pytest.mark.parametrize(
    "evidence_type,category",
    [
        (PublicationEvidenceType.REVERSE_DCF_CANONICAL_EXPECTATION, "expectation_evidence_ids"),
        (PublicationEvidenceType.REVERSE_DCF_SCENARIO, "scenario_evidence_ids"),
        (PublicationEvidenceType.EXTERNAL_ANALYST_TARGET, "reference_evidence_ids"),
        (PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE, "reference_evidence_ids"),
    ],
)
def test_noncentral_evidence_is_retained_only_in_its_controlled_category(evidence_type, category):
    extra = supplement(evidence_type)
    result = publish(supplemental=(extra,))
    assert extra.evidence_id in getattr(result, category)
    assert result.eligible_family_count == 0
    assert result.publication_status is AggregationStatus.UNAVAILABLE


def test_reverse_dcf_canonical_and_scenario_do_not_increment_or_alter_family_status():
    own = family()
    baseline = publish(own)
    result = publish(
        own,
        supplemental=(
            supplement(PublicationEvidenceType.REVERSE_DCF_CANONICAL_EXPECTATION, "canonical"),
            supplement(PublicationEvidenceType.REVERSE_DCF_SCENARIO, "scenario"),
        ),
    )
    assert result.eligible_family_count == baseline.eligible_family_count == 1
    assert result.publication_status is baseline.publication_status is AggregationStatus.UNRESOLVED


def test_external_analyst_and_provider_dcf_references_cannot_rescue_gate():
    result = publish(
        family(),
        supplemental=(
            supplement(PublicationEvidenceType.EXTERNAL_ANALYST_TARGET, "target"),
            supplement(PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE, "dcf"),
        ),
    )
    assert result.eligible_family_count == 1
    assert result.publication_status is AggregationStatus.UNRESOLVED
    assert result.overall_central_value is None


@pytest.mark.parametrize(
    "reference_type,expected",
    [
        (ExternalReferenceType.FMP_STANDARD_DCF, PublicationEvidenceType.EXTERNAL_PROVIDER_DCF_REFERENCE),
        (ExternalReferenceType.ANALYST_TARGET_CONSENSUS, PublicationEvidenceType.EXTERNAL_ANALYST_TARGET),
    ],
)
def test_external_reference_adapter_preserves_reference_only_separation(reference_type, expected):
    prov = provenance(provider="fmp", source="external")
    reference = ExternalValuationReference(
        reference_type=reference_type,
        currency="USD",
        provider="fmp",
        as_of_at=NOW,
        provenance=prov,
        value=125,
    )
    extra = supplemental_evidence_from_external_reference(
        reference,
        reference_id=f"reference:{reference_type.value}",
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
    )
    assert extra.evidence_type is expected
    assert extra.central_valuation_eligible is False


@pytest.mark.parametrize(
    "family_name,expected",
    [("own_history", ValuationFamily.OWN_HISTORY), ("peer", ValuationFamily.PEER)],
)
def test_generic_approved_upstream_result_adapter_does_not_recalculate_values(family_name, expected):
    generic = ValuationResult(
        method="EV_EBITDA",
        valuation_family=family_name,
        currency="USD",
        status=ValuationMethodStatus.VALID,
        low=90,
        central=110,
        high=130,
        input_observation_ids=("observation:one",),
        provenance=(provenance(),),
    )
    adapted = family_evidence_from_generic_result(
        generic,
        target_security_id=SECURITY,
        target_issuer_id=ISSUER,
        source_result_id="source:generic",
        analysis_as_of=NOW,
    )
    assert adapted.valuation_family is expected
    assert (adapted.lower_value, adapted.central_value, adapted.upper_value) == (90, 110, 130)
    assert adapted.per_share_unit == "USD/share"


@pytest.mark.parametrize("family_name", ["cash_flow", "reverse_cash_flow", "unknown"])
def test_generic_reverse_cashflow_or_unknown_result_cannot_enter_family_adapter(family_name):
    generic = ValuationResult(
        method="method",
        valuation_family=family_name,
        currency="USD",
        status=ValuationMethodStatus.VALID,
        low=90,
        central=110,
        high=130,
    )
    with pytest.raises(ValueError):
        family_evidence_from_generic_result(
            generic,
            target_security_id=SECURITY,
            target_issuer_id=ISSUER,
            source_result_id="source:generic",
            analysis_as_of=NOW,
        )


def test_shared_target_input_ids_do_not_merge_independent_families_or_duplicate_count():
    shared = ("fmp:fy1:ebitda", "fiscal:bridge", "fiscal:shares")
    result = publish(
        family(supporting=shared),
        family(ValuationFamily.PEER, 100, 115, 140, supporting=shared),
    )
    assert result.eligible_family_count == 2
    assert {item.supporting_ids for item in result.family_evidence} == {shared}


def test_supporting_policy_and_provenance_are_retained_unchanged():
    own = family()
    result = publish(own)
    retained = result.family_evidence[0]
    assert retained.supporting_ids == own.supporting_ids
    assert retained.policy_ids == own.policy_ids
    assert retained.provenance == own.provenance
    assert own.provenance[0] in result.provenance


def test_publication_contract_has_no_price_interpretation_score_or_stance_fields():
    names = set(publish(family()).__dataclass_fields__)
    forbidden = {
        "current_price", "share_price", "upside", "downside", "premium", "discount",
        "margin_of_safety", "stance", "recommendation", "confidence_score",
        "quality_score", "agreement_score", "family_weights",
    }
    assert names.isdisjoint(forbidden)


def test_service_has_no_magic_agreement_percentage_or_ticker_specific_behavior():
    module = importlib.import_module("stock_analyser.services.valuation_publication")

    source = inspect.getsource(module).lower()
    assert "== \"meta\"" not in source and "== 'meta'" not in source and "if ticker" not in source
    assert "0.10" not in source and "0.20" not in source and "0.25" not in source and "0.50" not in source
    identifiers = {
        node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
    }
    assert {"current_price", "upside", "stance"}.isdisjoint(identifiers)


def test_publication_contracts_are_immutable():
    result = publish(family())
    with pytest.raises(FrozenInstanceError):
        result.publication_status = AggregationStatus.RESOLVED
    with pytest.raises(FrozenInstanceError):
        result.family_evidence[0].central_value = 999


def test_nonresolved_result_cannot_be_replaced_with_a_hidden_central():
    result = publish(family())
    with pytest.raises(ValueError, match="non-resolved"):
        replace(result, overall_central_value=110)


def test_default_pytest_path_performs_no_network_call(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network called"))
    assert publish(family()).publication_status is AggregationStatus.UNRESOLVED


def test_publication_service_is_not_wired_into_legacy_streamlit():
    app_source = open("app.py", encoding="utf-8").read()
    assert "valuation_publication" not in app_source
    assert "publish_cross_family_valuation" not in app_source


def test_safe_publication_audit_renderer_exposes_only_controlled_results():
    result = publish(family())
    rendered = render_live_publication_audit(LivePublicationAuditOutcome(
        symbol="SYN",
        analysis_as_of=NOW,
        own_history=None,
        peer_family=None,
        publication=result,
        reverse_dcf_canonical_status="NOT_READY",
        reverse_dcf_scenario_status="NOT_SUPPLIED",
    ))
    lowered = rendered.lower()
    assert "publication status: unresolved" in lowered
    assert "overall central fair value: withheld" in lowered
    assert "api_key" not in lowered and "authorization" not in lowered and "raw payload" not in lowered


def test_live_publication_entry_point_is_explicit_and_not_imported_by_streamlit():
    import stock_analyser.live_publication_audit as module

    source = inspect.getsource(module)
    app_source = open("app.py", encoding="utf-8").read()
    assert "run_live_publication_audit(" in source
    assert "live_publication_audit" not in app_source
