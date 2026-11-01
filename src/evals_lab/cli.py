"""CLI: report a trajectory verdict and nonzero exit on regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import grade_trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate agent tool-call trajectory against a DAG")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Write structured JSON report")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        trace = json.loads(args.trace.read_text(encoding="utf-8"))
        grade = grade_trajectory(policy, trace)
        report = json.dumps(grade.to_dict(), ensure_ascii=False, indent=2)
        print(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report + "\n", encoding="utf-8")
        return 0 if grade.passed else 2
    except (ValueError, OSError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
