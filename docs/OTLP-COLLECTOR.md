# v0.5: External OTLP Collector delivery evaluation

## Purpose
Demonstrate an actual OpenTelemetry Python **OTLP/HTTP protobuf exporter** sending synthetic spans to an external **OpenTelemetry Collector Contrib**. A receiver-side file exporter persists OTLP/JSON NDJSON; a reconciler compares persisted span IDs with an in-process attempt ledger.

This is a **testing environment**, not a production deployment. The spans carry only fictional order/customer identifiers and monetary amounts.

## Local setup (Docker + Python)

From repository root, create the writable demo directory and start the fixed-version Collector:

```bash
mkdir -p reports/collector
chmod 0777 reports/collector # DEMO ONLY: avoid on shared/multi-tenant machines
# Ports are mapped only to 127.0.0.1 on the host.
docker compose -f infra/otel/docker-compose.yml up -d
curl --fail http://127.0.0.1:13133/
```

Install optional Python dependencies and evaluate actual collector-side receipt:

```bash
uv sync --extra collector
uv run --extra collector evals-lab-collector \
  --endpoint http://127.0.0.1:4318/v1/traces \
  --policy examples/policy.json \
  --collector-file reports/collector/traces.json \
  --output reports/remote-evaluation.json
```

Expected: `passed=true`, `network.successful_batches=1`, `receipt.complete=true`, `receipt.received_spans=4`. Then stop the collector:

```bash
docker compose -f infra/otel/docker-compose.yml down
```

Do **not** publish `reports/collector/traces.json` from real workloads: OTLP traces can contain personal data, payloads or credentials. The file exporter is meant for synthetic CI cases only.

## Failure matrix

| Test | What is injected | Expected evidence |
| --- | --- | --- |
| Healthy | Official OTel SDK -> OTLP HTTP -> file receipt | 4 distinct span IDs confirmed |
| Transient outage | Local test receiver returns 503 once | Official exporter retries and completes |
| Permanent refusal | Local test receiver returns HTTP 400 | Failure, no retry |
| Network down | Connect to stopped local receiver | Fail closed; zero successful exports |
| Silent loss | Receiver writes 3 of 4 spans, replies 200 | Fail; `COLLECTOR_SPANS_MISSING` |
| False acknowledgement | Receiver replies 200 but writes nothing | Fail; `COLLECTOR_FILE_MISSING` |
| Duplicates/foreign IDs | Mutated persisted trace | Duplicate/unknown IDs detected |
| Safety gate skipped | Real in-memory tool throws PermissionError | Fail; 0 refunds |

The local HTTP receiver in unit tests is a *test double*. Only the GitHub Actions Collector integration and the Docker instructions above use the official Collector binary.

## Limitations

- A 200 HTTP response only confirms acceptance by the receiver, not durable storage or delivery to a final backend. The check uses Collector file output as **receiver-side evidence**, not an independent cryptographic attestation.
- The exporter implements retries per its Python SDK behavior. The demo does not guarantee exactly-once delivery or persist outbound queues across process termination.
- Under partial failure the Collector can buffer spans and drop them later; this is an eventual snapshot check with bounded polling. It does not prove continuous end-to-end capture.
- Host exposure is bound to localhost and the isolated demo has no external exporter. Never deploy the example unmodified in production. Use proper mTLS/auth, ACLs, redaction, retention, backpressure, metrics, and persistent queues where appropriate.
- Both the in-process attempt ledger and the collector volume may be controlled by the same adversary. A compromised process can omit uninstrumented operations.

## Grounding

- OTel Collector Docker: https://opentelemetry.io/docs/collector/install/docker/
- OTLP status and retry semantics: https://opentelemetry.io/docs/specs/otlp/
- Python OTLP exporter: https://opentelemetry.io/docs/languages/python/exporters/
- Collector file exporter: https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/fileexporter
