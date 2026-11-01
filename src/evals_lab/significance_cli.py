"""Command-line paired statistical comparison, with no network access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .significance import compare_pairs, read_pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two models using paired binary success outcomes")
    parser.add_argument("--dataset", type=Path, required=True, help="JSONL of matched model A/B outcomes per case")
    parser.add_argument("--model-a", default="Model A")
    parser.add_argument("--model-b", default="Model B")
    parser.add_argument("--resamples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    try:
        report = compare_pairs(read_pairs(args.dataset), model_a=args.model_a,
                               model_b=args.model_b, confidence=args.confidence,
                               alpha=args.alpha, resamples=args.resamples, seed=args.seed)
        body = json.dumps(report.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(body, encoding="utf-8")
        sys.stdout.write(body)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
