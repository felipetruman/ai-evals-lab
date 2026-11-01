"""OTLP/HTTP delivery lab: strict *receiver-side* reconciliation.

Nothing here is a durable end-to-end attestation of a production system:
we compare a local attempt ledger with what a separately operated collector
persisted for one trace. Only synthetic arguments may be exported in the demo.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import re
import time
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from .otel_sdk_demo import execute_sdk_demo, evaluate_sdk_run


class AuditOTLPExporter(SpanExporter):
    """Wrap the official SDK HTTP exporter to audit final API result, not retries."""

    def __init__(self, endpoint: str, timeout: float = 2.0) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path != "/v1/traces":
            raise ValueError("endpoint must be an absolute HTTP(S) URL ending in /v1/traces")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("endpoint must not include credentials, query or fragment")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._exporter = OTLPSpanExporter(endpoint=endpoint, timeout=timeout)
        self._lock = Lock()
        self.calls = 0
        self.failures = 0
        self.successes = 0

    def export(self, spans: Any) -> SpanExportResult:
        try:
            result = self._exporter.export(spans)
        except Exception:
            with self._lock:
                self.calls += 1
                self.failures += 1
            return SpanExportResult.FAILURE
        with self._lock:
            self.calls += 1
            if result == SpanExportResult.SUCCESS:
                self.successes += 1
            else:
                self.failures += 1
        return result

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)


@dataclass(frozen=True)
class ReceiptReport:
    complete: bool
    trace_id: str
    expected_spans: int
    received_spans: int
    reasons: list[str]
    missing_span_ids: list[str] = field(default_factory=list)
    unexpected_span_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_id(value: Any, byte_count: int) -> str:
    """OTLP JSON encodes protobuf bytes as base64; hand-written fixtures may use hex."""
    if not isinstance(value, str):
        raise ValueError("invalid OTLP identifier")
    if re.fullmatch(r"[0-9a-fA-F]{%d}" % (byte_count * 2), value):
        return value.lower()
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 OTLP identifier") from exc
    if len(decoded) != byte_count:
        raise ValueError("invalid OTLP identifier length")
    return decoded.hex()


def reconcile_collector_file(attestation: dict[str, Any], collector_file: Path) -> ReceiptReport:
    """Reconcile one run against NDJSON from the OTel collector file exporter.

    Ignore other trace IDs; reject duplicated/foreign IDs inside this run.
    Empty file or unavailable file -> fail closed (not a successful delivery).
    """
    trace_id = attestation.get("trace_id")
    expected = attestation.get("attempted_span_ids")
    if not isinstance(trace_id, str) or len(trace_id) != 32 or not all(x in "0123456789abcdef" for x in trace_id):
        raise ValueError("invalid attestation trace_id")
    if not isinstance(expected, list) or not expected or len(set(expected)) != len(expected):
        raise ValueError("invalid attempted_span_ids")
    if any(not isinstance(s, str) or len(s) != 16 or any(x not in "0123456789abcdef" for x in s) for s in expected):
        raise ValueError("invalid span id in attempted_span_ids")
    seen: list[str] = []
    reasons: list[str] = []
    try:
        lines = collector_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ReceiptReport(False, trace_id, len(expected), 0, ["COLLECTOR_FILE_MISSING"], sorted(expected), [])
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
            resources = document["resourceSpans"]
            if not isinstance(resources, list):
                raise TypeError("not a list")
            for resource in resources:
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        try:
                            tid = _canonical_id(span.get("traceId"), 16)
                            sid = _canonical_id(span.get("spanId"), 8)
                        except ValueError as exc:
                            raise ValueError(f"invalid collector trace/span ID: {exc}") from exc
                        if tid == trace_id:
                            seen.append(sid)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"collector NDJSON line {index} invalid: {exc}") from exc
    received, wanted = set(seen), set(expected)
    missing = sorted(wanted - received)
    unexpected = sorted(received - wanted)
    if missing:
        reasons.append("COLLECTOR_SPANS_MISSING")
    if unexpected:
        reasons.append("UNEXPECTED_COLLECTOR_SPANS")
    if len(seen) != len(received):
        reasons.append("DUPLICATE_COLLECTOR_SPAN")
    if len(seen) != len(expected):
        reasons.append("SPAN_COUNT_MISMATCH")
    if not lines:
        reasons.append("EMPTY_COLLECTOR_FILE")
    return ReceiptReport(not reasons, trace_id, len(expected), len(seen), sorted(set(reasons)), missing, unexpected)


def run_remote(endpoint: str, policy: dict[str, Any], collector_file: Path | None = None,
               timeout: float = 2.0, skip_identity: bool = False) -> dict[str, Any]:
    """Execute a local SDK workflow and send spans through an official OTLP exporter.

    HTTP acknowledgement is not delivery confirmation: only a collector-side file
    receipt can establish observed storage. Without that receipt, fail closed.
    """
    exporter = AuditOTLPExporter(endpoint, timeout)
    run = execute_sdk_demo(skip_identity=skip_identity, additional_exporter=exporter)
    local = evaluate_sdk_run(run, policy)
    network = {"export_batches": exporter.calls, "successful_batches": exporter.successes,
               "failed_batches": exporter.failures}
    receipt = None
    if collector_file is not None:
        # Collector file exporter may flush asynchronously after OTLP HTTP 200.
        deadline = time.monotonic() + max(1.0, min(timeout * 3, 8.0))
        while True:
            receipt = reconcile_collector_file(run.attestation, collector_file).to_dict()
            if receipt["complete"] or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
    passed = bool(local["passed"] and network["successful_batches"] > 0
                  and network["failed_batches"] == 0 and receipt is not None and receipt["complete"])
    return {"passed": passed, "local": local, "network": network, "receipt": receipt,
            "attestation": run.attestation}


def main() -> int:
    parser = argparse.ArgumentParser(description="Real OTLP HTTP exporter with collector-side receipt check")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4318/v1/traces")
    parser.add_argument("--policy", type=Path, default=Path("examples/policy.json"))
    parser.add_argument("--collector-file", required=True, type=Path,
                        help="Collector file-exporter NDJSON path (local volume)")
    parser.add_argument("--output", type=Path, default=Path("reports/remote-evaluation.json"))
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--skip-identity", action="store_true")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        report = run_remote(args.endpoint, policy, args.collector_file, args.timeout, args.skip_identity)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Never write raw user arguments into the report; demo uses synthetic args only.
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "network": report["network"],
                          "receipt": report["receipt"], "local_passed": report["local"]["passed"]}, indent=2))
        return 0 if report["passed"] else 2
    except (ValueError, TypeError, OSError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
