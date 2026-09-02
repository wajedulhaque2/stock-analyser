"""Explicit CLI for one safe Milestone 9B.3 USD WACC evidence-gap audit."""

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

from stock_analyser.live_smoke import load_dotenv_safely  # noqa: E402
from stock_analyser.live_wacc_audit import render_live_wacc_audit, run_live_wacc_audit  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in, secret-safe USD production-WACC evidence audit.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--valuation-currency", required=True)
    parser.add_argument("--security-id", required=True)
    parser.add_argument("--issuer-id", required=True)
    args = parser.parse_args(argv)
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    outcome = run_live_wacc_audit(
        args.symbol, args.valuation_currency, environment=os.environ,
        analysis_as_of=datetime.now(timezone.utc), target_security_id=args.security_id,
        target_issuer_id=args.issuer_id,
    )
    print(render_live_wacc_audit(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
