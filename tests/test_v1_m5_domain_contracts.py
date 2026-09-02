from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from stock_analyser.domain import (
    CashFlowDefinition,
    CashFlowDefinitionEvidence,
    DefinitionVerificationStatus,
    EstimateRevision,
    Frequency,
    MacroFrequency,
    MacroMetric,
    MacroObservation,
    MetricId,
    MetricUnit,
    Provenance,
)


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)


def provenance(dataset="synthetic"):
    return Provenance(
        provider="synthetic", endpoint_or_dataset=dataset,
        provider_symbol="SYN", retrieved_at=NOW, as_of_at=NOW,
    )


def test_verified_cash_flow_definition_requires_machine_readable_audit_evidence():
    with pytest.raises(ValueError, match="verified_at and definition_reference"):
        CashFlowDefinitionEvidence(
            provider="synthetic", provider_metric="freeCashFlow", endpoint_or_dataset="estimates",
            definition=CashFlowDefinition.FCFF,
            verification_status=DefinitionVerificationStatus.VERIFIED,
        )
    evidence = CashFlowDefinitionEvidence(
        provider="synthetic", provider_metric="freeCashFlow", endpoint_or_dataset="estimates",
        definition=CashFlowDefinition.FCFF,
        verification_status=DefinitionVerificationStatus.VERIFIED,
        definition_reference="semantic-contract:synthetic-fcff-v1", verified_at=NOW,
    )
    assert evidence.is_verified_fcff and not evidence.is_verified_fcfe


def test_unverified_provider_fcf_cannot_claim_verified_fcff_or_fcfe():
    evidence = CashFlowDefinitionEvidence(
        provider="synthetic", provider_metric="freeCashFlow", endpoint_or_dataset="estimates",
        definition=CashFlowDefinition.PROVIDER_DEFINED,
        verification_status=DefinitionVerificationStatus.UNVERIFIED,
    )
    assert not evidence.is_verified_fcff and not evidence.is_verified_fcfe


def test_estimate_revision_requires_revision_evidence_and_valid_quarter():
    kwargs = dict(
        metric_id=MetricId.EPS, frequency=Frequency.QUARTERLY,
        period_start=date(2027, 1, 1), period_end=date(2027, 3, 31), fiscal_year=2027,
        fiscal_quarter=1, revision_window="30_days", unit=MetricUnit.CURRENCY_PER_SHARE,
        currency="USD", provider="synthetic", retrieved_at=NOW, as_of_at=NOW,
        provenance=provenance("revisions"),
    )
    with pytest.raises(ValueError, match="at least one supplied"):
        EstimateRevision(**kwargs)
    assert EstimateRevision(**kwargs, up_revisions=2).up_revisions == 2
    with pytest.raises(ValueError, match="fiscal_quarter"):
        EstimateRevision(**{**kwargs, "fiscal_quarter": 5}, up_revisions=2)


def test_macro_observation_requires_decimal_rate_unit_and_consistent_vintage():
    kwargs = dict(
        series_id="DGS10", metric=MacroMetric.TREASURY_YIELD, value=0.047,
        unit=MetricUnit.PERCENT_DECIMAL, currency="USD", observation_date=date(2026, 8, 25),
        frequency=MacroFrequency.DAILY, as_of_at=NOW, retrieved_at=NOW,
        provider="synthetic", provenance=provenance("fred_dgs10"),
        realtime_start=date(2026, 8, 1), realtime_end=date(2026, 8, 31),
    )
    assert MacroObservation(**kwargs).value == 0.047
    with pytest.raises(ValueError, match="percent_decimal"):
        MacroObservation(**{**kwargs, "unit": MetricUnit.RATIO})
    with pytest.raises(ValueError, match="realtime_start"):
        MacroObservation(**{
            **kwargs, "realtime_start": date(2026, 9, 1), "realtime_end": date(2026, 8, 31),
        })
