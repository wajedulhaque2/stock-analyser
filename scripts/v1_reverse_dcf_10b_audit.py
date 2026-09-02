"""Explicit CLI for one bounded, secret-safe Milestone 10B readiness audit."""

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

from stock_analyser.live_reverse_dcf_10b_audit import (  # noqa: E402
    render_live_reverse_dcf_10b_audit,
    run_live_reverse_dcf_10b_audit,
)
from stock_analyser.live_smoke import load_dotenv_safely  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in Milestone 10B reverse-DCF readiness audit.",
    )
    parser.add_argument("--symbol", required=True)
    args = parser.parse_args(argv)
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    outcome = run_live_reverse_dcf_10b_audit(
        args.symbol,
        environment=os.environ,
        analysis_as_of=datetime.now(timezone.utc),
    )
    print(render_live_reverse_dcf_10b_audit(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
