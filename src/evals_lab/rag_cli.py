"""Offline adversarial RAG eval CLI, no model calls or external APIs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .rag import evaluate_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG answers and abstentions against structured evidence")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-agreement", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 <= args.min_agreement <= 1:
        parser.error("--min-agreement must be between 0 and 1")
    try:
        report = evaluate_jsonl(args.dataset)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        print(payload)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        return 0 if report.agreement >= args.min_agreement else 2
    except (OSError, ValueError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
