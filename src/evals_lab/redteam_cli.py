"""CLI for offline agent red-team simulation. Exit 0=match, 2=regression, 1=input error."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .redteam import evaluate_dataset, generate_cases, write_new_jsonl

def main() -> int:
    parser = argparse.ArgumentParser(description="Offline agent tool-boundary red-team fuzzer")
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--count", type=int, default=40)
    g.add_argument("--output", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--dataset", type=Path, required=True)
    r.add_argument("--output", type=Path)
    r.add_argument("--max-steps", type=int, default=8)
    r.add_argument("--max-identical", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            cases=generate_cases(args.count, args.seed)
            write_new_jsonl(args.output, cases)
            print(json.dumps({"generated":len(cases),"seed":args.seed,"output":str(args.output)}))
            return 0
        report=evaluate_dataset(args.dataset,max_steps=args.max_steps,max_identical=args.max_identical)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({k:report[k] for k in ("total","matched","mismatched","agreement")},indent=2))
        return 0 if not report["mismatched"] else 2
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        print(f"INPUT_ERROR: {exc}",file=sys.stderr)
        return 1

if __name__=="__main__":
    sys.exit(main())
