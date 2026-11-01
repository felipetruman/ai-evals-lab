# Batch Evaluation Methodology (v0.2)

## Evaluation unit
One labeled trajectory for a single policy workflow. A strict DAG specifies required calls, expected tool names, parameter types and dependencies. Any unknown argument, missed required call or unsatisfied prerequisite fails the run.

## Golden JSONL dataset

- One JSON record per line, with unique `case_id`, boolean `expected_pass`, and exactly one of `trace` or `otlp`.
- `trace.calls` is an ordered native sequence; `otlp.resourceSpans` is an OTLP/JSON export with a single trace ID.
- Invalid JSON, duplicates, absent labels, invalid OTLP attributes and missing tool arguments are input errors (exit 1), not skipped cases.
- `agreement=matched/cases_total` measures concordance with expected case labels, **not** real-world task success.
- A mismatch returns exit 2 by default; set a different threshold only with documented validation.
- Reports expose case labels, verdicts, scores and violation codes but no raw tool arguments. No external model calls.

## OTLP contract

The adapter scans `resourceSpans[].scopeSpans[].spans[]`, selects `gen_ai.operation.name=execute_tool`, requires standard `gen_ai.tool.name` and opt-in `gen_ai.tool.call.arguments`, plus custom `evals.step_id` mapping to an explicit policy node. The custom attribute is **not** part of OpenTelemetry semantic conventions. Arguments may use `kvlistValue` or a JSON-encoded `stringValue`. `startTimeUnixNano` must be present. Ties sort by span ID. Mixed trace IDs and duplicate span IDs are rejected.

## Caveats and security

Tracing may be sampled or incomplete: the importer cannot prove an unobserved tool was never executed. Timestamp ordering does not prove causality. A tool name alone cannot identify which DAG node was executed because tools can recur. The dataset is synthetic and statistically unrepresentative. Do not commit production traces, API keys, emails or personally identifiable information. Collect opt-in arguments only under a deliberate retention/redaction policy.

## Reproduce

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m evals_lab.batch_cli --policy examples/policy.json --dataset examples/golden.jsonl --output reports/batch.json
```

Reference: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md
