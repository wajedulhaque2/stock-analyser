"""Explicit CLI for one safe Milestone 9B.1 USD CAPM readiness audit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_analyser.live_discount_rate_audit import (  # noqa: E402
    render_live_discount_rate_audit,
    run_live_discount_rate_audit,
)
from stock_analyser.live_smoke import load_dotenv_safely  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in, secret-safe V1 discount-rate readiness audit.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--valuation-currency", required=True)
    parser.add_argument("--security-id")
    parser.add_argument("--issuer-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.security_id) != bool(args.issuer_id):
        raise SystemExit("--security-id and --issuer-id must be supplied together")
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    outcome = run_live_discount_rate_audit(
        args.symbol, args.valuation_currency, environment=os.environ,
        analysis_as_of=datetime.now(timezone.utc),
        target_security_id=args.security_id, target_issuer_id=args.issuer_id,
    )
    print(render_live_discount_rate_audit(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
