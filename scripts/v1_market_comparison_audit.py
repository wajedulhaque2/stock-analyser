"""Explicit CLI for one bounded, secret-safe V1 market-comparison audit."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_analyser.live_market_comparison_audit import (  # noqa: E402
    render_live_market_comparison_audit,
    run_live_market_comparison_audit,
)
from stock_analyser.live_smoke import load_dotenv_safely  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Opt-in V1 post-publication market-price comparison audit.",
    )
    parser.add_argument("--symbol", default="META", help="Runtime symbol (default: META).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    outcome = run_live_market_comparison_audit(args.symbol, environment=os.environ)
    print(render_live_market_comparison_audit(outcome))
    return 0 if outcome.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
