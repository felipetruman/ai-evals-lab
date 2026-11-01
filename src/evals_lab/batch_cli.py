"""Batch CLI: exit 0 if threshold met, 2 if labels regress, 1 if inputs are bad."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .batch import evaluate_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate labeled JSONL traces against a policy DAG")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-agreement", type=float, default=1.0,
                        help="Minimum fraction of labeled outcomes reproduced (0..1); default 1.0")
    args = parser.parse_args()
    if not 0 <= args.min_agreement <= 1:
        parser.error("--min-agreement must be between 0 and 1")
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        report = evaluate_dataset(policy, args.dataset)
        serialized = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        print(serialized)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        return 0 if report.agreement >= args.min_agreement else 2
    except (ValueError, OSError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
