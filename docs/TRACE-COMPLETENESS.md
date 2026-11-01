# Trace completeness: local attestation, v0.3

## Threat model

An OTLP trace can be partially exported, sampled, dropped, truncated or silently lose a tool call. Without an independent record of expected operations, an evaluator cannot prove a missing span never occurred. A valid *trajectory grade* only evaluates **observed** calls.

## What is actually validated

`LocalRecorder` records a fresh span ID in an in-process invocation ledger **before** running each tool function. A `finally` block records an OTLP `execute_tool` span with explicit success/failure status. On finalization, the recorder creates an export and a separate JSON attestation containing:

- `schema`: versioned `ai-evals-lab/trace-attestation/v1` contract
- `trace_id`, `attempted_span_ids` from the invocation ledger
- `run_finalized`: whether the recorder finalized
- `dropped_spans`: locally reported exporter losses
- `sampling`: `none` in the local demo
- `otlp_sha256`: SHA-256 of sorted, compact UTF-8 JSON

The verifier ensures a single valid trace with matching span IDs, no duplicates, hash integrity, zero *reported* dropped spans, no sampling, and a finalized run. It rejects missing arguments, bad spans and explicit error statuses through the importer. A failed tool call still produces an `execute_tool` span with `status.code=STATUS_CODE_ERROR` and the grader emits `TOOL_EXECUTION_FAILED`.

**Interpretation:** `complete=true` means **internally consistent with this instrumented process' self-reported ledger**. It does not establish externally authenticated provenance, perfect instrumentation coverage, or absence of hidden tools, prompt injection or tampering of *both* manifest and trace. Two internally consistent lies can pass. The SHA-256 checksum is not a signature.

## Example

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m evals_lab.demo --policy examples/policy.json --output-dir reports/demo
python -m evals_lab.demo --policy examples/policy.json --output-dir reports/blocked --skip-identity
```

The second command must return exit status `2`: the demo deliberately skips identity verification, the service refuses to issue the refund, and the evaluator reports safety violations.

## Privacy and transport

The synthetic local demo records opt-in `gen_ai.tool.call.arguments` only with non-sensitive identifiers. OTLP conventions treat these arguments as potentially sensitive. Do not enable argument capture or upload spans from actual users without appropriate redaction, access controls, consent, retention limits and filtering. Never commit real user traces. This sample **does not** establish a production-grade telemetry collector or use the OpenTelemetry SDK. It produces a small compliant-shape OTLP/JSON fixture through a project-local recorder for testing.

## Remaining production gaps

1. Independent collector-side count and flush acknowledgments with spans across process boundaries.
2. Parent/child relationships and true ordering via causal links rather than timestamps alone.
3. Live transport sampling, retries, drop counters and shutdown/flush failure injection.
4. Authenticated manifest provenance, retention, redaction, and per-tenant authorization.

Standards reference: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md
