# v0.4: OpenTelemetry SDK reliability experiment

## Scope

A real `opentelemetry-sdk` `TracerProvider` and `BatchSpanProcessor` record
three in-memory tool functions under a parent agent span. A local
`SpanExporter` converts whitelisted **synthetic** tool attributes into an
OTLP/JSON-compatible dictionary. The root span has no tool attributes.
The demonstration has no LLM, collector, network connection, payments or
production integrations. The SDK is not installed globally, nor is the
process-global TracerProvider mutated.

## Scenarios / expected results

| Fault | Exported / attempted spans | Evaluator | Why |
|---|---:|---|---|
| None | 4 / 4 | PASS | parent and all three tools exported |
| `drop_last` | 3 / 4 | FAIL | exporter silently discards a span; ledger disagreement |
| `exporter_failure` | 0 / 4 | FAIL | exporter returns `SpanExportResult.FAILURE` |
| `sampler_off` | 0 / 4 | FAIL | real `ALWAYS_OFF` sampler makes spans unrecorded |
| `--skip-identity` | 3 / 3 | FAIL | tool denies in-memory refund; trajectory safety checks fail |

Expected **exit status 0** for passing normal run, **2** for intentional
fault/quality failure and **1** for malformed inputs. `grade=null` is
intentional whenever the trace is incomplete: do not grade unseen operations
as successful.

## Method

1. The application-layer boundary logs all attempted SDK trace/span IDs.
2. The SDK records an `agent.run` parent and `execute_tool` child spans.
3. A batch span processor delivers the ended spans to the local exporter.
4. The tool attributes use OpenTelemetry GenAI conventions for tool name and
   operation, plus proprietary `evals.step_id` for DAG matching. Only synthetic
   JSON tool arguments are included; results, stack traces and arbitrary
   baggage are not copied.
5. The evaluator checks the locally recorded attempted span IDs against
   the export, exporter failure count, explicit sampling mode, finalization
   and canonical SHA-256. It grades tool behavior only if this check passes.

## Core caveats

- The ledger and exported spans originate in the **same process** and are
  not a trustworthy independently signed provenance or completeness proof.
- `force_flush()` reporting success is **not** exporter delivery success.
  Exporter results are checked separately.
- A malicious or uninstrumented tool can evade this evidence entirely.
- Clock-based ordering is not an independently verified causal ordering.
- `drop_last` simulates a silent in-memory export loss. Network faults,
  retries, queue pressure, collector downtime and remote delivery receipts
  are **not** tested. Further work must exercise the actual OTLP wire exporter.
- `sampler_off` is a deterministic 0% scenario, not a statistical study of
  probabilistic sampling (e.g. 5%). For strict completeness gates use a
  100%-recording policy in the controlled scope; sampling compromises proof.
- The OTLP/JSON dictionary is a restricted local conversion, not an
  authoritative wire-level implementation of the OTLP specification.
- All reported metrics are fixture counts from synthetic test flows, not
  accuracy, statistical confidence or benchmarks on production users.

## Reproduce

```bash
python -m pip install -e ".[otel]"
python -m unittest discover -s tests -v
python -m evals_lab.otel_sdk_demo --output-dir reports/sdk
python -m evals_lab.otel_sdk_demo --fault drop_last --output-dir reports/sdk-drop
python -m evals_lab.otel_sdk_demo --fault exporter_failure --output-dir reports/sdk-failure
python -m evals_lab.otel_sdk_demo --fault sampler_off --output-dir reports/sdk-unsampled
```

Sources:
- https://opentelemetry.io/docs/languages/python/exporters/
- https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.export.html
- https://opentelemetry.io/docs/specs/otel/trace/sdk/
