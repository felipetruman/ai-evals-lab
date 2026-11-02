"""No-network contamination report, optional CI gate for evidence of leakage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contamination import read_corpus, scan, review_against_labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Check overlap between training and evaluation JSONL datasets")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--review-labels", type=Path, help="Optional independently adjudicated JSONL pair labels")
    parser.add_argument("--ngram-size", type=int, default=3)
    parser.add_argument("--min-shared-ngrams", type=int, default=2)
    parser.add_argument("--jaccard-threshold", type=float, default=0.8)
    parser.add_argument("--containment-threshold", type=float, default=0.95)
    parser.add_argument("--cosine-threshold", type=float, default=0.97)
    parser.add_argument("--max-pairs", type=int, default=250_000)
    parser.add_argument("--fail-on", choices=["any", "high", "none"], default="any")
    args = parser.parse_args()
    try:
        train = read_corpus(args.train)
        evaluation = read_corpus(args.eval)
        report = scan(train, evaluation,
                      ngram_size=args.ngram_size, min_shared_ngrams=args.min_shared_ngrams,
                      jaccard_threshold=args.jaccard_threshold, containment_threshold=args.containment_threshold,
                      cosine_threshold=args.cosine_threshold, max_pairs=args.max_pairs)
        data = report.to_dict()
        if args.review_labels:
            data["labeled_review"] = review_against_labels(report, train, evaluation, args.review_labels).to_dict()
        payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        print(payload)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        if args.fail_on == "any" and report.flagged_pair_count:
            return 2
        if args.fail_on == "high" and report.high_severity_pairs:
            return 2
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
