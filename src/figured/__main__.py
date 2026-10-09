"""Command line: figured "answer text" --rows rows.json [--tolerance 0.015] [--json]

Exits 1 when any figure is untraceable, so it can gate a CI step or a pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from figured.core import trace


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="figured", description="Check that the numbers in a text trace to rows.")
    p.add_argument("text", help="the generated text, or '-' to read it from stdin")
    p.add_argument(
        "--rows", required=True, help="JSON file: a list of objects, a list of arrays, or {columns, rows}"
    )
    p.add_argument("--tolerance", type=float, default=None, help="relative tolerance, default 0.015")
    p.add_argument("--strict-percent", action="store_true", help="flag percentages that match nothing")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.text == "-" else args.text
    rows = json.loads(Path(args.rows).read_text())
    overrides: dict[str, Any] = {}
    if args.tolerance is not None:
        overrides["rel_tolerance"] = args.tolerance
    if args.strict_percent:
        overrides["unmatched_percent"] = "flag"
    report = trace(text, rows, **overrides)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.explain())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
