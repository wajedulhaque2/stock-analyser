from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import inspect

from stock_analyser.domain import DataAvailability
from stock_analyser.live_market_comparison_audit import (
    render_live_market_comparison_audit,
    run_live_market_comparison_audit,
)
from stock_analyser.live_publication_audit import LivePublicationAuditOutcome
from stock_analyser.live_smoke import LiveYahooSource
from stock_analyser.providers import ProviderDataResult
from test_v1_actual_adapters import identity
from test_v1_market_comparison import NOW, observation, unresolved_publication


def publication_outcome():
    publication = unresolved_publication()
    target = replace(
        identity(quote_unit="USD"),
        security_id=publication.target_security_id,
        issuer_id=publication.target_issuer_id,
        reporting_currency="USD",
        quote_currency="USD",
    )
    return LivePublicationAuditOutcome(
        symbol="SYN",
        analysis_as_of=NOW,
        own_history=None,
        peer_family=SimpleNamespace(target_identity=target),
        publication=publication,
        reverse_dcf_canonical_status="NOT_READY",
        reverse_dcf_scenario_status="NOT_SUPPLIED",
    )


def test_bounded_live_audit_reuses_canonical_yahoo_adapter_and_calls_snapshot_once(monkeypatch):
    calls = []

    class FakeYahooAdapter:
        def __init__(self, source):
            assert source == "fake-source"

        def fetch_market_snapshot(self, target):
            calls.append(target.security_id)
            return ProviderDataResult(observations=(observation(),))

    monkeypatch.setattr(
        "stock_analyser.live_market_comparison_audit.run_live_publication_audit",
        lambda *args, **kwargs: publication_outcome(),
    )
    monkeypatch.setattr(
        "stock_analyser.live_market_comparison_audit.LiveYahooSource",
        lambda: "fake-source",
    )
    monkeypatch.setattr(
        "stock_analyser.live_market_comparison_audit.YahooAdapter",
        FakeYahooAdapter,
    )
    outcome = run_live_market_comparison_audit("syn", environment={}, analysis_as_of=NOW)
    assert calls == [outcome.comparison.target_security_id]
    assert outcome.market_price.status is DataAvailability.AVAILABLE
    assert outcome.market_price.normalized_price_per_share == 90
    assert outcome.comparison.family_comparisons[0].status is DataAvailability.AVAILABLE
    assert outcome.comparison.overall_comparison.status is DataAvailability.UNAVAILABLE


def test_safe_renderer_shows_evidence_arithmetic_and_no_judgment(monkeypatch):
    class FakeYahooAdapter:
        def __init__(self, source):
            pass

        def fetch_market_snapshot(self, target):
            return ProviderDataResult(observations=(observation(),))

    monkeypatch.setattr(
        "stock_analyser.live_market_comparison_audit.run_live_publication_audit",
        lambda *args, **kwargs: publication_outcome(),
    )
    monkeypatch.setattr("stock_analyser.live_market_comparison_audit.YahooAdapter", FakeYahooAdapter)
    output = render_live_market_comparison_audit(
        run_live_market_comparison_audit("SYN", environment={}, analysis_as_of=NOW),
    ).lower()
    assert "price semantic: yahoo_regular_market_price" in output
    assert "normalized price: 90 usd/share" in output
    assert "own_history" in output and "gap percentages" in output
    assert "overall comparison status: unavailable" in output
    assert "reverse-dcf canonical expectation: not_ready" in output
    assert "api_key" not in output and "authorization" not in output and "raw payload" not in output


def test_live_yahoo_source_does_not_relabel_current_price_as_regular_market_price():
    source = inspect.getsource(LiveYahooSource.market_snapshot)
    assert 'row.get("regularMarketPrice")' in source
    assert 'row.get("currentPrice")' not in source


def test_live_entry_point_is_explicit_and_not_wired_to_streamlit():
    import stock_analyser.live_market_comparison_audit as module

    source = inspect.getsource(module)
    app_source = open("app.py", encoding="utf-8").read()
    assert "run_live_market_comparison_audit(" in source
    assert "YahooAdapter(LiveYahooSource())" in source
    assert "live_market_comparison_audit" not in app_source
