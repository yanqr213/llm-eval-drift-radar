from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .core import compare_runs, load_records, load_thresholds
from .reporting import render, write_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-eval-drift-radar",
        description="Compare two LLM eval result files and report drift/regressions.",
    )
    parser.add_argument("--baseline", required=True, help="Baseline eval result file (.jsonl or .csv).")
    parser.add_argument("--current", required=True, help="Current eval result file (.jsonl or .csv).")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv", "junit"),
        default="markdown",
        help="Report output format.",
    )
    parser.add_argument("--output", help="Write report to this path instead of stdout.")
    parser.add_argument("--thresholds", help="Thresholds JSON file.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when configured fail conditions are found; otherwise exit 0.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress report output unless --output is provided.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        thresholds = load_thresholds(args.thresholds)
        baseline = load_records(args.baseline)
        current = load_records(args.current)
        result = compare_runs(baseline, current, thresholds)
        report = render(result, args.format)
        if args.output:
            write_output(report, args.output)
        elif not args.quiet:
            write_output(report, None)
    except Exception as exc:
        print(f"llm-eval-drift-radar: error: {exc}", file=sys.stderr)
        return 2
    if args.check and result.should_fail:
        return 1
    return 0
