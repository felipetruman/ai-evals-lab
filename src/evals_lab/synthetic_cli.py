"""CLI for synthetic cases, optional model generation, and explicit human curation."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .synthetic import (generate_candidates, read_candidates, promote_reviewed,
                        validate_candidates, write_new_jsonl)
from .synthetic_llm import generate_model_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic adversarial evaluation dataset pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--policy", type=Path, required=True)
    generate.add_argument("--seed-trace", type=Path, required=True)
    generate.add_argument("--count", type=int, default=24)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--source", choices=("deterministic", "llm"), default="deterministic")
    generate.add_argument("--endpoint", default="")
    generate.add_argument("--model", default="")
    generate.add_argument("--allow-network", action="store_true",
                          help="Explicitly allow sending synthetic policy and trace to API")
    promote = commands.add_parser("promote")
    promote.add_argument("--policy", type=Path, required=True)
    promote.add_argument("--candidates", type=Path, required=True)
    promote.add_argument("--reviews", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        if args.command == "generate":
            trace = json.loads(args.seed_trace.read_text(encoding="utf-8"))
            if args.source == "llm":
                if not args.allow_network:
                    raise ValueError("LLM requests require --allow-network; use only synthetic data")
                candidates = generate_model_candidates(
                    policy, trace, endpoint=args.endpoint, model=args.model,
                    api_key=os.environ.get("EVALS_LLM_API_KEY", ""),
                    count=args.count, seed=args.seed)
            else:
                candidates = generate_candidates(policy, trace, count=args.count, seed=args.seed)
            validate_candidates(policy, candidates)
            write_new_jsonl(args.output, candidates)
            print(json.dumps({"generated": len(candidates), "pending_review": len(candidates),
                              "output": str(args.output), "source": args.source}))
        else:
            candidates = read_candidates(args.candidates)
            reviews = read_candidates(args.reviews)
            curated = promote_reviewed(policy, candidates, reviews)
            write_new_jsonl(args.output, curated)
            print(json.dumps({"approved": len(curated), "output": str(args.output),
                              "warning": "golden dataset remains unchanged"}))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
