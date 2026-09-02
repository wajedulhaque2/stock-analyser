"""Run one opt-in, secret-safe ticker-to-report integration diagnostic."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_analyser.application import build_live_stock_research_report  # noqa: E402
from stock_analyser.live_smoke import load_dotenv_safely  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One bounded V1 live identity and report integration audit.",
    )
    parser.add_argument("--symbol", default="META", help="Runtime symbol (default: META).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    result = build_live_stock_research_report(args.symbol, environment=os.environ)
    diagnostic = result.identity_diagnostic
    print("V1 FINAL ENGINEERING ACCEPTANCE LIVE REPORT AUDIT")
    print(f"requested symbol: {result.requested_symbol}")
    if diagnostic is None:
        print("identity diagnostic: unavailable")
    else:
        print(f"identity provider: {diagnostic.provider}")
        print(f"configuration: {diagnostic.configuration_status.value}")
        print(f"configuration source: {diagnostic.configuration_source.value}")
        print(f"client: {diagnostic.client_status.value}")
        print(f"request attempted: {str(diagnostic.request_attempted).lower()}")
        print(f"HTTP category: {diagnostic.http_status_category.value}")
        print(f"parse: {diagnostic.parse_status.value}")
        print(f"normalization: {diagnostic.normalization_status.value}")
        print(f"canonical identity: {diagnostic.canonical_identity_status.value}")
        print(f"identity stage: {diagnostic.blocking_stage.value}")
        print(f"safe reason: {diagnostic.safe_reason}")
        print(f"candidate count: {diagnostic.candidate_count}")
        print(f"pages examined: {diagnostic.pages_examined}")
        print(f"venue evidence found: {str(diagnostic.venue_evidence_found).lower()}")
        print(f"candidate resolution: {diagnostic.candidate_resolution_status.value}")
        print(
            "profile enrichment attempted: "
            f"{str(diagnostic.profile_enrichment_attempted).lower()}"
        )
        print(f"bounded search exhausted: {str(diagnostic.bounded_search_exhausted).lower()}")
    print(f"build status: {result.status.value}")
    print(f"pipeline stage: {result.stage.value}")
    print(f"normalized semantic fetches: {result.semantic_provider_call_count}")
    print(f"normalized reuses: {result.normalized_reuse_count}")
    if result.report is None:
        print("report: WITHHELD")
        print(f"safe blocker: {result.blocking_reason or 'unavailable'}")
        return 1
    report = result.report
    print(f"canonical security ID: {report.target_security_id}")
    print(f"canonical issuer ID: {report.target_issuer_id}")
    print(f"report: {report.status.value}")
    print("renderer input contract: StockResearchReport")
    print(f"market: {report.market.availability.value}")
    print(f"reporting currency: {report.identity.reporting_currency}")
    print(f"quote currency: {report.identity.quote_currency}")
    print(f"quote unit: {report.identity.quote_unit}")
    print(f"quote price scale: {report.identity.quote_price_scale:g}")
    print(f"normalized market currency: {report.market.normalized_currency or 'unavailable'}")
    print(f"normalized market price: {'published' if report.market.normalized_market_price is not None else 'withheld'}")
    print(f"publication: {report.valuation_summary.publication_label}")
    print(f"overall central: {'published' if report.valuation_summary.overall_central_value is not None else 'withheld'}")
    for family in report.valuation_families:
        print(f"family {family.family.value}: {family.family_status.value}")
    print(
        "consensus: "
        f"{report.consensus.readiness_status.value if report.consensus.readiness_status is not None else report.consensus.status.value}"
    )
    print(f"reverse DCF: {report.expectations.display_label}")
    print(f"scenario context: {'available' if result.scenario_context is not None else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
