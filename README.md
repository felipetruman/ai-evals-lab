# AI Evals Lab

[![CI](https://github.com/felipetruman/ai-evals-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/felipetruman/ai-evals-lab/actions/workflows/ci.yml)

A reproducible evaluation engineering portfolio: **15 modules, one coherent methodology.**

> Current status: **Project 01: Trajectory Grading Engine (working prototype with batch, OTLP/JSON adapter and instrumented demo).** Remaining 14 modules are planned, not implemented.

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

## Batch evaluation (labeled JSONL)

Run the synthetic golden dataset with zero external inference:

```bash
python -m evals_lab.batch_cli --policy examples/policy.json --dataset examples/golden.jsonl --min-agreement 1 --output reports/batch.json
# expected: cases_total=4, matched=4, agreement=1.0, exit 0
```

Each JSONL line needs unique `case_id`, boolean `expected_pass`, and exactly one of `trace` (native tool calls) or `otlp` (one OTLP trace). Reports contain only step-level outcomes, not raw tool arguments. A malformed fixture exits 1; an unexpected classification exits 2 by default. Agreement measures reproduction of **labels**, not model task success or statistical significance.

**OTLP adapter:** offline `resourceSpans` with `gen_ai.operation.name=execute_tool`, standard `gen_ai.tool.name`, opt-in `gen_ai.tool.call.arguments` and custom `evals.step_id`. The latter is project-specific, **not** an OpenTelemetry standard. Spans sort by `startTimeUnixNano` and `spanId`, which do not establish actual causality. Missing spans cannot be detected with certainty.

**Privacy:** OTLP tool arguments can contain secrets and personal data. Never publish unredacted production traces. The published fixtures are synthetic. Full methodology in [docs/BATCH-EVALS.md](docs/BATCH-EVALS.md).

## Instrumented local demo + trace consistency checks (v0.3)

The demo executes three **real in-memory Python tool functions** (order lookup,
identity verification and refund with an in-memory balance update), capturing
actual execution spans, explicit OK/ERROR status, and a per-run attestation.
It is **not** a production refund, LLM call or third-party API integration.

```bash
python -m evals_lab.demo --policy examples/policy.json --output-dir reports/demo
# exit 0, one in-memory refund, local export consistency OK, policy PASS
python -m evals_lab.demo --policy examples/policy.json --output-dir reports/blocked --skip-identity
# exit 2, ZERO refunds, policy FAIL, local export consistency still OK
```

Outputs: `otlp.json`, `attestation.json`, `evaluation.json` per execution.
The attestation checks expected span IDs from the in-process invocation ledger,
JSON SHA-256, finalization, zero reported dropped spans, and sampling disabled.
**This is a self-reported consistency check, not a cryptographic signature or
proof that uninstrumented production operations never happened.** See
[docs/TRACE-COMPLETENESS.md](docs/TRACE-COMPLETENESS.md).

Do not publish these files after switching the demo to real customers; tool
arguments can contain personal data and API secrets.
