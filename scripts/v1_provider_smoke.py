"""Explicit command-line entry point for V1 live provider contract smoke testing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_analyser.live_smoke import (  # noqa: E402
    build_live_runners,
    load_dotenv_safely,
    parse_provider_selection,
    render_json,
    render_text,
    run_selected,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Opt-in live verification of V1 provider normalization contracts.",
    )
    parser.add_argument("--symbol", default="META", help="US equity symbol used as smoke input (default: META).")
    parser.add_argument(
        "--providers",
        help="Comma-separated subset: yahoo,fiscal,sec,fmp,finnhub,alpha,fred. Default: all.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Return nonzero for normalization/schema failures; LOCKED/UNAVAILABLE remain nonfatal.",
    )
    parser.add_argument(
        "--json-summary", action="store_true",
        help="Emit only allowlisted normalized metadata as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv_safely((PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"))
    try:
        providers = parse_provider_selection(args.providers)
        result = run_selected(
            symbol=args.symbol,
            providers=providers,
            runners=build_live_runners(),
            strict=args.strict,
        )
    except ValueError as error:
        print(f"Smoke configuration error: {error}", file=sys.stderr)
        return 2
    print(render_json(result) if args.json_summary else render_text(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
