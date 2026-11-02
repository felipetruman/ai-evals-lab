"""Reproducible context-retention simulation and report generator."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .context_eviction import benchmark, read_cases

def main() -> int:
    parser = argparse.ArgumentParser(description="Compare context-window eviction policies on offline synthetic cases")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-accuracy", type=float, default=0.0,
                        help="Require minimum accuracy for each strategy (0..1); default runs without gate")
    args = parser.parse_args()
    if not 0 <= args.min_accuracy <= 1:
        parser.error("--min-accuracy must be in 0..1")
    try:
        report = benchmark(read_cases(args.dataset))
        serialized = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        print(serialized)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        return 0 if all(s.accuracy >= args.min_accuracy for s in report.summaries) else 2
    except (ValueError, OSError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
