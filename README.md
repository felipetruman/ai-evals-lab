# AI Evals Lab

[![CI](https://github.com/felipetruman/ai-evals-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/felipetruman/ai-evals-lab/actions/workflows/ci.yml)

A reproducible evaluation engineering portfolio: **15 modules, one coherent methodology.**

> Current status: **Project 01: Trajectory Grading Engine (working prototype).** Remaining 14 modules are planned, not implemented.

## Problem

A successful final answer does not prove an agent followed the correct workflow. An agent may skip identity verification, invent an API argument or call a dangerous tool without being noticed by final-answer-only evaluations.

## Solution

A deterministic evaluator that compares a recorded tool-call trajectory to a policy DAG and fails closed on unknown parameters, incorrect tools, missing required steps or unsatisfied dependencies.

**No paid models, API keys, external services or private data required.** The examples are synthetic.

## Quickstart

Python >=3.11 required.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v

# Expected: passed=true, score=100.0
python -m evals_lab.cli --policy examples/policy.json --trace examples/valid.json

# Expected: exit code 2 and dependency/safety violations
python -m evals_lab.cli --policy examples/policy.json --trace examples/missing_safety.json

# Expected: exit code 2 and UNKNOWN_ARGUMENT
python -m evals_lab.cli --policy examples/policy.json --trace examples/hallucinated_parameter.json
```

Alternatively, with uv (which can manage Python environments):

```bash
uv run --python 3.12 python -m unittest discover -s tests -v
uv run --python 3.12 evals-lab --policy examples/policy.json --trace examples/valid.json --output reports/valid.json
```

## Sample policy

```text
orders.get -> identity.verify -> refunds.create
    required       safety gate          gated action
```

All three steps are required. The refund action must have a valid identity verification completed earlier in the trace. API arguments are validated by an allowlisted signature with primitive types.

## What is measured

- `passed`: strict boolean verdict. Any policy violation makes the entire run fail.
- `score`: diagnostic percentage of individual checks passed (not statistically calibrated).
- `required_coverage`: successfully completed required steps / required steps.
- `violations`: auditable per-step failure codes and messages.
- `steps`: step-by-step verdicts.

## Limitations and future work

- DAG enforces a known workflow: it does **not** assess open-ended alternative plans, factual correctness of tool results, or semantic equivalence of different strategies.
- Matching step identifiers and primitive parameter types is not a complete JSON Schema validator.
- Tool results and model-generated content are not inspected in v0.1.
- No PII is collected in the demo; real integrations must redact and minimize trace content.
- The score is a debugging aid, not an independently validated quality metric.

## Portfolio-ready evidence

1. Reproducible test commands + 15 unit tests.
2. Both positive and adversarial fixture traces.
3. Machine-readable evaluation reports.
4. CI that fails when expected negative scenarios accidentally pass.
5. Methodology and explicit limitations.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for all 15 project modules, implementation order, and acceptance criteria.

## License

MIT, see `LICENSE`.
