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

Python >=3.11 required. For the v0.4 SDK integration, install the optional `otel` dependency group.

```bash
python -m pip install -e ".[otel]"
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
uv run --python 3.12 --extra otel python -m unittest discover -s tests -v
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
- Tool results and model-generated content are not evaluated semantically in v0.4.
- No PII is collected in the demo; real integrations must redact and minimize trace content.
- The score is a debugging aid, not an independently validated quality metric.

## Portfolio-ready evidence

1. Reproducible test commands + 66 automated tests (v0.4).
2. Both positive and adversarial fixture traces.
3. Machine-readable evaluation reports.
4. CI that fails when expected negative scenarios accidentally pass.
5. Methodology and explicit limitations.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for all 15 project modules, implementation order, and acceptance criteria.

## License

MIT, see `LICENSE`.

## Batch evaluations (labeled JSONL)

Run the four-case synthetic golden dataset, with zero external inference:

```bash
python -m evals_lab.batch_cli --policy examples/policy.json --dataset examples/golden.jsonl --min-agreement 1 --output reports/batch.json
# expected: cases_total=4, matched=4, mismatched=0, agreement=1.0, exit 0
```

Each JSONL record needs a unique `case_id`, `expected_pass` boolean and **exactly one** of `trace` (native tool calls) or `otlp` (one OTLP trace with tool spans). Comparison measures classification agreement against labels: it does not validate task success or produce a statistical confidence interval. A mislabeled or unreadable case is never ignored; input errors exit 1. A labeled classification mismatch makes the default CI gate exit 2.

**OTLP adapter:** import an offline JSON `resourceSpans` export. Each executed tool span needs standard `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, and opt-in `gen_ai.tool.call.arguments`, plus a custom `evals.step_id` to match the policy node. This custom attribute is **not** an OpenTelemetry semantic convention. Tool spans are sorted by `startTimeUnixNano`, tie-broken by `spanId`, but timestamps do not prove causal ordering. Incomplete recordings can be misclassified. The v0.3/v0.4 self-reported local ledger improves detection but does not independently prove completeness.

**Privacy:** OTel tool arguments can contain secrets or personal data. Do not copy raw production traces into this public repository. The batch report excludes argument payloads, but it contains case IDs and policy step IDs that should also be synthetic or redacted. All published examples are fabricated.

See [the batch methodology](docs/BATCH-EVALS.md).

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

## Official OpenTelemetry Python SDK: reliability tests (v0.4)

This optional demonstration uses a **real SDK TracerProvider**, actual
automatic context propagation, SDK-managed spans with `BatchSpanProcessor`,
and a local `SpanExporter`. It does **not** talk to an OTLP collector or
pretend to have end-to-end transport acknowledgments. The demo serializes
SDK `ReadableSpan` objects into a restricted OTLP/JSON-compatible document
using only a whitelist of synthetic attributes.

```bash
python -m pip install -e ".[otel]"

# 4 recorded spans: parent run + 3 real in-memory tool invocations
python -m evals_lab.otel_sdk_demo --output-dir reports/sdk

# Each case intentionally exits 2 (reliability gate fails closed):
python -m evals_lab.otel_sdk_demo --fault drop_last --output-dir reports/sdk-drop
python -m evals_lab.otel_sdk_demo --fault exporter_failure --output-dir reports/sdk-export
python -m evals_lab.otel_sdk_demo --fault sampler_off --output-dir reports/sdk-sample

# Actual tool-level safety check still blocks local effects
python -m evals_lab.otel_sdk_demo --skip-identity --output-dir reports/sdk-blocked
```

Fault semantics: `drop_last` silently drops a span while reporting exporter
success; `exporter_failure` returns SDK `SpanExportResult.FAILURE`; and
`sampler_off` uses the SDK `ALWAYS_OFF` sampler. These are deliberate,
reproducible local fault injections and do not test network transport retries,
collector outages or realistic partial sampling rates.

**Important:** `TracerProvider.force_flush()` returning `True` means the
processor completed flushing, not that the exporter acknowledged delivery.
The evaluation checks export failures, the independent in-process attempt
ledger, the explicit sampler mode and a SHA-256 export checksum. This
attestation is not a digital signature or trusted external audit.

[Read the v0.4 methodology and limitations](docs/OTEL-SDK-RELIABILITY.md).
