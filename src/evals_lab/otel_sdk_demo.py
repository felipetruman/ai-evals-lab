"""Real OpenTelemetry Python SDK instrumentation with deterministic local faults.

All tool effects are confined to an in-memory demonstration. This is a trace
reliability experiment, not a remote exporter or a proof of independent capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON
from opentelemetry.trace import Status, StatusCode

from .attestation import SCHEMA, canonical_hash, verify_attestation
from .demo import LocalRefundService
from .engine import grade_trajectory
from .otel import otlp_to_trajectory


class FaultInjectingExporter(SpanExporter):
    """Thread-safe in-memory exporter. Faults operate at the exporter boundary."""

    def __init__(self, fault: str = "none") -> None:
        if fault not in ("none", "drop_last", "exporter_failure"):
            raise ValueError("unsupported exporter fault")
        self.fault = fault
        self._lock = Lock()
        self.spans: list[Any] = []
        self.export_failures = 0
        self.export_calls = 0
        self.discarded = 0
        self._did_drop = False

    def export(self, spans: Any) -> SpanExportResult:
        with self._lock:
            self.export_calls += 1
            if self.fault == "exporter_failure":
                self.export_failures += 1
                return SpanExportResult.FAILURE
            incoming = list(spans)
            if self.fault == "drop_last" and not self._did_drop and incoming:
                incoming.pop()
                self.discarded += 1
                self._did_drop = True
            self.spans.extend(incoming)
            return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _otlp_attribute(key: str, value: Any) -> dict[str, Any]:
    """The demo records only whitelisted synthetic str/bool/int/float attributes."""
    if isinstance(value, bool):
        val = {"boolValue": value}
    elif isinstance(value, int):
        val = {"intValue": str(value)}
    elif isinstance(value, float):
        val = {"doubleValue": value}
    elif isinstance(value, str):
        val = {"stringValue": value}
    else:
        raise ValueError(f"unexpected span attribute type: {key}")
    return {"key": key, "value": val}


def _to_otlp(exported: list[Any]) -> dict[str, Any]:
    spans = []
    for span in exported:
        context = span.get_span_context()
        code = span.status.status_code
        status = ({"code": "STATUS_CODE_ERROR"} if code == StatusCode.ERROR else
                  {"code": "STATUS_CODE_OK"} if code == StatusCode.OK else
                  {"code": "STATUS_CODE_UNSET"})
        attrs = []
        # Never copy arbitrary baggage/events: the demo exports explicit attributes only.
        for key in ("gen_ai.operation.name", "gen_ai.tool.name", "evals.step_id",
                    "gen_ai.tool.call.arguments", "error.type"):
            if key in span.attributes:
                attrs.append(_otlp_attribute(key, span.attributes[key]))
        item = {
            "traceId": f"{context.trace_id:032x}",
            "spanId": f"{context.span_id:016x}",
            "name": span.name,
            "startTimeUnixNano": str(span.start_time),
            "endTimeUnixNano": str(span.end_time),
            "status": status,
            "attributes": attrs,
        }
        if span.parent is not None:
            item["parentSpanId"] = f"{span.parent.span_id:016x}"
        spans.append(item)
    return {"resourceSpans": [{"resource": {"attributes": [
        _otlp_attribute("service.name", "ai-evals-sdk-demo")
    ]}, "scopeSpans": [{"scope": {"name": "ai-evals-lab.sdk"}, "spans": spans}]}]}


@dataclass
class SdkRun:
    otlp: dict[str, Any]
    attestation: dict[str, Any]
    exporter_status: dict[str, Any]
    effects: dict[str, Any]


def execute_sdk_demo(fault: str = "none", skip_identity: bool = False) -> SdkRun:
    """Execute three real local tool functions under SDK-managed trace spans.

    The boundary ledger is populated *before* tool invocation. This makes a
    missing exporter span detectable from the local process's independent log.
    The ledger itself can still be forged or omitted by a compromised process.
    """
    if fault not in ("none", "drop_last", "exporter_failure", "sampler_off"):
        raise ValueError("unsupported fault")
    provider = TracerProvider(
        sampler=ALWAYS_OFF if fault == "sampler_off" else ALWAYS_ON,
        resource=Resource.create({"service.name": "ai-evals-sdk-demo"}),
    )
    exporter = FaultInjectingExporter(fault if fault in ("drop_last", "exporter_failure") else "none")
    provider.add_span_processor(BatchSpanProcessor(
        exporter, max_queue_size=128, max_export_batch_size=32,
        schedule_delay_millis=60_000, export_timeout_millis=5_000,
    ))
    tracer = provider.get_tracer("ai-evals-lab.sdk")  # private provider: never alters global OTel state
    service = LocalRefundService()
    attempts: list[str] = []
    trace_id = ""
    flush_ok = False

    def tool_call(step: str, name: str, args: dict[str, Any], fn: Callable[..., Any]) -> Any:
        with tracer.start_as_current_span(f"execute_tool {name}", record_exception=False) as span:
            context = span.get_span_context()
            attempts.append(f"{context.span_id:016x}")
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("evals.step_id", step)
            span.set_attribute("gen_ai.tool.call.arguments", json.dumps(args, sort_keys=True))
            try:
                outcome = fn(**args)
                span.set_status(Status(StatusCode.OK))
                return outcome
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise

    try:
        with tracer.start_as_current_span("agent.run", record_exception=False) as root:
            trace_id = f"{root.get_span_context().trace_id:032x}"
            attempts.append(f"{root.get_span_context().span_id:016x}")
            tool_call("fetch_order", "orders.get", {"order_id": "ORDER-001"}, service.get_order)
            if not skip_identity:
                tool_call("verify_identity", "identity.verify", {"customer_id": "CUSTOMER-001"}, service.verify_identity)
            try:
                tool_call("issue_refund", "refunds.create", {"order_id": "ORDER-001", "amount": 25.0}, service.issue_refund)
            except PermissionError:
                pass  # The deliberately skipped safety check prevents local side effects.
            root.set_status(Status(StatusCode.OK))
        flush_ok = provider.force_flush(timeout_millis=5_000)
    finally:
        provider.shutdown()  # processor joins its worker before we read exported spans

    otlp = _to_otlp(exporter.spans)
    observed = len(exporter.spans)
    # The reported number of missing spans is derived from independent attempts,
    # not merely from the exporter's self-reported discards.
    dropped = max(0, len(attempts) - observed)
    attestation = {
        "schema": SCHEMA,
        "trace_id": trace_id,
        "attempted_span_ids": attempts,
        "run_finalized": bool(flush_ok) and exporter.export_failures == 0,
        "sampling": "sampled" if fault == "sampler_off" else "none",
        "dropped_spans": dropped,
        "otlp_sha256": canonical_hash(otlp),
    }
    status = {"flush_completed": flush_ok, "export_calls": exporter.export_calls,
              "export_failures": exporter.export_failures, "exported_spans": observed,
              "recorded_attempts": len(attempts), "injected_discards": exporter.discarded}
    effects = {"refunded": len(service.refunds), "balance": service.orders["ORDER-001"]["balance"]}
    return SdkRun(otlp, attestation, status, effects)


def evaluate_sdk_run(run: SdkRun, policy: dict[str, Any]) -> dict[str, Any]:
    completeness = verify_attestation(run.otlp, run.attestation)
    grade = None
    if completeness.complete:
        grade = grade_trajectory(policy, otlp_to_trajectory(run.otlp)).to_dict()
    passed = completeness.complete and grade is not None and grade["passed"]
    return {"passed": bool(passed), "completeness": completeness.to_dict(), "grade": grade,
            "exporter_status": run.exporter_status, "effects": run.effects}


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenTelemetry SDK resilience demonstration")
    parser.add_argument("--policy", type=Path, default=Path("examples/policy.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/otel-sdk"))
    parser.add_argument("--fault", choices=("none", "drop_last", "exporter_failure", "sampler_off"), default="none")
    parser.add_argument("--skip-identity", action="store_true")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        run = execute_sdk_demo(fault=args.fault, skip_identity=args.skip_identity)
        report = evaluate_sdk_run(run, policy)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # OTLP includes arguments, albeit only synthetic ones; never upload real data.
        for name, doc in (("otlp.json", run.otlp), ("attestation.json", run.attestation),
                          ("evaluation.json", report)):
            (args.output_dir / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "complete": report["completeness"]["complete"],
                          "reasons": report["completeness"]["reasons"], "exporter": run.exporter_status,
                          "effects": run.effects}, indent=2))
        return 0 if report["passed"] else 2
    except (ValueError, TypeError, OSError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
