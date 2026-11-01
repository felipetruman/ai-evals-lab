"""Offline OTLP/JSON tool-span import. Requires custom evals.step_id and opt-in arguments."""

from __future__ import annotations

import json
import re
from typing import Any

_TRACE = re.compile(r"^[0-9a-fA-F]{32}$")
_SPAN = re.compile(r"^[0-9a-fA-F]{16}$")


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        raise ValueError("OTLP attribute value must be an AnyValue object")
    keys = [k for k in ("stringValue", "intValue", "boolValue", "doubleValue", "arrayValue", "kvlistValue") if k in value]
    if len(keys) != 1:
        raise ValueError("OTLP attribute must contain exactly one supported value type")
    key = keys[0]
    data = value[key]
    if key == "stringValue":
        if not isinstance(data, str):
            raise ValueError("stringValue must be a string")
        return data
    if key == "intValue":
        if isinstance(data, bool) or not isinstance(data, (str, int)):
            raise ValueError("intValue must be a decimal integer")
        try:
            return int(data)
        except ValueError as exc:
            raise ValueError("invalid intValue") from exc
    if key == "doubleValue":
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            raise ValueError("doubleValue must be numeric")
        return float(data)
    if key == "boolValue":
        if not isinstance(data, bool):
            raise ValueError("boolValue must be boolean")
        return data
    if key == "arrayValue":
        if not isinstance(data, dict) or not isinstance(data.get("values"), list):
            raise ValueError("arrayValue must contain values")
        return [_any_value(v) for v in data["values"]]
    if not isinstance(data, dict) or not isinstance(data.get("values"), list):
        raise ValueError("kvlistValue must contain values")
    result: dict[str, Any] = {}
    for pair in data["values"]:
        if not isinstance(pair, dict) or not isinstance(pair.get("key"), str) or pair["key"] in result:
            raise ValueError("invalid or duplicate kvlistValue key")
        result[pair["key"]] = _any_value(pair.get("value"))
    return result


def _attributes(values: Any) -> dict[str, Any]:
    if not isinstance(values, list):
        raise ValueError("span.attributes must be an array")
    result: dict[str, Any] = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or item["key"] in result:
            raise ValueError("span has invalid or duplicate attribute key")
        result[item["key"]] = _any_value(item.get("value"))
    return result


def otlp_to_trajectory(document: dict[str, Any]) -> dict[str, Any]:
    """Convert exactly one trace's execute_tool spans into a trajectory.

    Require explicit mapping instead of guessing node IDs from tool names.
    No outbound calls, full-trace recording or persistence of raw inputs.
    """
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        raise ValueError("OTLP document must have resourceSpans array")
    tool_calls: list[tuple[int, str, dict[str, Any]]] = []
    trace_ids: set[str] = set()
    span_ids: set[tuple[str, str]] = set()
    for resource in document["resourceSpans"]:
        if not isinstance(resource, dict) or not isinstance(resource.get("scopeSpans"), list):
            raise ValueError("each resourceSpan must contain scopeSpans")
        for scope in resource["scopeSpans"]:
            if not isinstance(scope, dict) or not isinstance(scope.get("spans"), list):
                raise ValueError("each scopeSpan must contain spans")
            for span in scope["spans"]:
                if not isinstance(span, dict):
                    raise ValueError("span must be an object")
                trace_id, span_id = span.get("traceId"), span.get("spanId")
                if not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id):
                    raise ValueError("invalid OTLP traceId")
                if not isinstance(span_id, str) or not _SPAN.fullmatch(span_id):
                    raise ValueError("invalid OTLP spanId")
                trace_ids.add(trace_id.lower())
                span_key = (trace_id.lower(), span_id.lower())
                if span_key in span_ids:
                    raise ValueError("duplicate spanId")
                span_ids.add(span_key)
                attrs = _attributes(span.get("attributes", []))
                if attrs.get("gen_ai.operation.name") != "execute_tool":
                    continue
                name = attrs.get("gen_ai.tool.name")
                step = attrs.get("evals.step_id")
                arguments = attrs.get("gen_ai.tool.call.arguments")
                if not isinstance(name, str) or not name or not isinstance(step, str) or not step:
                    raise ValueError("execute_tool span missing gen_ai.tool.name or evals.step_id")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise ValueError("tool call arguments are not valid JSON") from exc
                if not isinstance(arguments, dict):
                    raise ValueError("execute_tool span requires object arguments (opt-in recording)")
                start = span.get("startTimeUnixNano")
                if not isinstance(start, (int, str)) or isinstance(start, bool):
                    raise ValueError("tool span missing startTimeUnixNano")
                try:
                    timestamp = int(start)
                except ValueError as exc:
                    raise ValueError("invalid startTimeUnixNano") from exc
                if timestamp <= 0:
                    raise ValueError("startTimeUnixNano must be positive")
                call = {"step_id": step, "tool": name, "arguments": arguments}
                status = span.get("status")
                if status is not None:
                    if not isinstance(status, dict) or status.get("code") not in (
                        "STATUS_CODE_OK", "STATUS_CODE_ERROR", 1, 2,
                    ):
                        raise ValueError("tool span must have explicit OK or ERROR status")
                    call["status"] = "error" if status["code"] in ("STATUS_CODE_ERROR", 2) else "ok"
                tool_calls.append((timestamp, span_id.lower(), call))
    if len(trace_ids) != 1:
        raise ValueError("OTLP fixture must contain exactly one traceId")
    if not tool_calls:
        raise ValueError("OTLP fixture has no execute_tool spans")
    tool_calls.sort(key=lambda row: (row[0], row[1]))
    return {"calls": [row[2] for row in tool_calls]}
