"""Local refund workflow running real in-memory tool functions, with OTLP/JSON export.

No LLM, network, accounts, commercial refunds, or third-party APIs are used.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .attestation import SCHEMA, canonical_hash, verify_attestation
from .engine import grade_trajectory
from .otel import otlp_to_trajectory


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


@dataclass
class LocalRecorder:
    trace_id: str = field(default_factory=lambda: secrets.token_hex(16))
    attempts: list[str] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)
    finalized: bool = False

    def call(self, step: str, tool: str, arguments: dict[str, Any], fn: Callable[..., Any]) -> Any:
        """Capture attempt before execution and success/failure after invocation."""
        if self.finalized:
            raise RuntimeError("cannot record after finalization")
        span_id = secrets.token_hex(8)
        start = time.time_ns()
        self.attempts.append(span_id)
        code = "STATUS_CODE_OK"
        error_type = None
        try:
            return fn(**arguments)
        except Exception as exc:
            code = "STATUS_CODE_ERROR"
            error_type = type(exc).__name__
            raise
        finally:
            end = max(time.time_ns(), start + 1)
            attrs = [
                _attr("gen_ai.operation.name", "execute_tool"),
                _attr("gen_ai.tool.name", tool),
                _attr("evals.step_id", step),
                _attr("gen_ai.tool.call.arguments", json.dumps(arguments, sort_keys=True)),
            ]
            if error_type:
                attrs.append(_attr("error.type", error_type))
            self.spans.append({
                "traceId": self.trace_id,
                "spanId": span_id,
                "name": f"execute_tool {tool}",
                "kind": "SPAN_KIND_INTERNAL",
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "status": {"code": code},
                "attributes": attrs,
            })

    def finish(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.finalized:
            raise RuntimeError("already finalized")
        self.finalized = True
        document = {"resourceSpans": [{
            "resource": {"attributes": [_attr("service.name", "ai-evals-local-demo")]},
            "scopeSpans": [{"scope": {"name": "ai-evals-lab.demo"}, "spans": list(self.spans)}],
        }]}
        attestation = {
            "schema": SCHEMA,
            "trace_id": self.trace_id,
            "attempted_span_ids": list(self.attempts),
            "run_finalized": True,
            "sampling": "none",
            "dropped_spans": 0,
            "otlp_sha256": canonical_hash(document),
        }
        return document, attestation


class LocalRefundService:
    """Tools have real in-memory effects and enforce their own safety checks."""

    def __init__(self) -> None:
        self.orders = {"ORDER-001": {"customer_id": "CUSTOMER-001", "balance": 50.0}}
        self.verified: set[str] = set()
        self.refunds: list[dict[str, Any]] = []

    def get_order(self, order_id: str) -> dict[str, Any]:
        if order_id not in self.orders:
            raise LookupError("order missing")
        return self.orders[order_id]

    def verify_identity(self, customer_id: str) -> bool:
        if customer_id != "CUSTOMER-001":
            raise PermissionError("identity rejected")
        self.verified.add(customer_id)
        return True

    def issue_refund(self, order_id: str, amount: float) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["customer_id"] not in self.verified:
            raise PermissionError("identity verification required")
        if not 0 < amount <= order["balance"]:
            raise ValueError("refund amount out of range")
        order["balance"] -= amount
        record = {"order_id": order_id, "amount": amount}
        self.refunds.append(record)
        return record


def execute_demo(skip_identity: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run real local tool calls, with optional intentional safety violation."""
    service = LocalRefundService()
    recorder = LocalRecorder()
    recorder.call("fetch_order", "orders.get", {"order_id": "ORDER-001"}, service.get_order)
    if not skip_identity:
        recorder.call("verify_identity", "identity.verify", {"customer_id": "CUSTOMER-001"}, service.verify_identity)
    try:
        recorder.call("issue_refund", "refunds.create", {"order_id": "ORDER-001", "amount": 25.0}, service.issue_refund)
    except PermissionError:
        pass  # Intentional fail-closed demonstration: the local refund was NOT applied.
    document, attestation = recorder.finish()
    return document, attestation, {"refunded": len(service.refunds), "balance": service.orders["ORDER-001"]["balance"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute a local instrumented agent-like workflow")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/demo"))
    parser.add_argument("--policy", type=Path, default=Path("examples/policy.json"))
    parser.add_argument("--skip-identity", action="store_true", help="Demonstrate failed safety gate")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        otlp, attestation, effects = execute_demo(args.skip_identity)
        completeness = verify_attestation(otlp, attestation)
        grade = grade_trajectory(policy, otlp_to_trajectory(otlp))
        report = {"completeness": completeness.to_dict(), "grade": grade.to_dict(), "effects": effects}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, data in (("otlp.json", otlp), ("attestation.json", attestation), ("evaluation.json", report)):
            (args.output_dir / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"complete": completeness.complete, "passed": grade.passed, "effects": effects,
                          "violations": [v.code for v in grade.violations]}, indent=2))
        return 0 if completeness.complete and grade.passed else 2
    except (OSError, ValueError, TypeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
