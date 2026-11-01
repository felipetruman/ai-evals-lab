"""Evidence of in-process trace export consistency, not third-party provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from .otel import otlp_to_trajectory

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
SCHEMA = "ai-evals-lab/trace-attestation/v1"


def canonical_hash(document: dict[str, Any]) -> str:
    """Hash a canonical JSON rendering, independent of insignificant formatting."""
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spans(document: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        raise ValueError("invalid OTLP resourceSpans")
    result = []
    for resource in document["resourceSpans"]:
        if not isinstance(resource, dict) or not isinstance(resource.get("scopeSpans"), list):
            raise ValueError("invalid OTLP scopeSpans")
        for scope in resource["scopeSpans"]:
            if not isinstance(scope, dict) or not isinstance(scope.get("spans"), list):
                raise ValueError("invalid OTLP spans")
            result.extend(scope["spans"])
    return result


@dataclass(frozen=True)
class CompletenessReport:
    complete: bool
    trace_id: str
    expected_spans: int
    exported_spans: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_attestation(document: dict[str, Any], attestation: dict[str, Any]) -> CompletenessReport:
    """Verify ledger/export consistency; invalid metadata fails closed.

    Both manifest and spans are from the same untrusted producer. This is not a
    signature and cannot prove independently that *all* work was instrumented.
    """
    if not isinstance(attestation, dict) or attestation.get("schema") != SCHEMA:
        raise ValueError("unsupported/missing attestation schema")
    trace_id = attestation.get("trace_id")
    expected = attestation.get("attempted_span_ids")
    if not isinstance(trace_id, str) or not TRACE_ID.fullmatch(trace_id):
        raise ValueError("invalid attestation trace_id")
    if not isinstance(expected, list) or not expected or not all(
        isinstance(s, str) and SPAN_ID.fullmatch(s) for s in expected
    ) or len(set(expected)) != len(expected):
        raise ValueError("attempted_span_ids must be unique lowercase hex span IDs")
    if type(attestation.get("run_finalized")) is not bool:
        raise ValueError("run_finalized must be boolean")
    if type(attestation.get("dropped_spans")) is not int or attestation["dropped_spans"] < 0:
        raise ValueError("dropped_spans must be a nonnegative integer")
    if attestation.get("sampling") not in ("none", "unknown", "sampled"):
        raise ValueError("sampling must be none, unknown or sampled")
    checksum = attestation.get("otlp_sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid otlp_sha256")

    spans = _spans(document)
    observed: list[str] = []
    reasons: list[str] = []
    for span in spans:
        if not isinstance(span, dict):
            reasons.append("INVALID_SPAN")
            continue
        sid, tid = span.get("spanId"), span.get("traceId")
        if not isinstance(sid, str) or not SPAN_ID.fullmatch(sid):
            reasons.append("INVALID_SPAN_ID")
            continue
        if tid != trace_id:
            reasons.append("TRACE_ID_MISMATCH")
        observed.append(sid)
    if len(set(observed)) != len(observed):
        reasons.append("DUPLICATE_SPAN_ID")
    if set(observed) != set(expected) or len(observed) != len(expected):
        reasons.append("SPAN_LEDGER_MISMATCH")
    if canonical_hash(document) != checksum:
        reasons.append("EXPORT_HASH_MISMATCH")
    if not attestation["run_finalized"]:
        reasons.append("RUN_NOT_FINALIZED")
    if attestation["dropped_spans"]:
        reasons.append("DROPPED_SPANS")
    if attestation["sampling"] != "none":
        reasons.append("SAMPLING_NOT_DISABLED")
    try:
        otlp_to_trajectory(document)
    except (ValueError, TypeError):
        reasons.append("INVALID_TOOL_TRACE")
    return CompletenessReport(not reasons, trace_id, len(expected), len(observed), sorted(set(reasons)))
