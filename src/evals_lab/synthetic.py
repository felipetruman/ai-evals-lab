"""Synthetic agent trajectory candidate generation and human review gates.

Never executes agent tools. 'expected_pass' is a DAG/schema outcome, not proof
that a mutated call would succeed in a real system.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import grade_trajectory, validate_policy

SCHEMA = "ai-evals-lab/synthetic-candidate/v1"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"line {line_no}: blank JSONL record")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_no}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError(f"line {line_no}: JSONL record must be object")
            rows.append(record)
    if not rows:
        raise ValueError("dataset must contain at least one record")
    return rows


def write_new_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Refuse to overwrite an existing dataset; write only validated candidate data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(canonical(row) + "\n" for row in rows)
    with path.open("x", encoding="utf-8") as out:
        out.write(text)


def _select(policy: dict[str, Any], trace: dict[str, Any]):
    nodes = validate_policy(policy)
    if not grade_trajectory(policy, trace).passed:
        raise ValueError("seed trace must pass policy evaluation")
    calls = trace["calls"]
    by_id = {c["step_id"]: i for i, c in enumerate(calls)}
    required_nodes = [n for n in nodes.values() if n.get("required", True) and n["id"] in by_id]
    parameters = [(by_id[n["id"]], name, spec) for n in nodes.values()
                  if n["id"] in by_id for name, spec in n.get("params", {}).items()
                  if name in calls[by_id[n["id"]]]["arguments"]]
    needed = [(by_id[n["id"]], name) for n in nodes.values() if n["id"] in by_id
              for name, spec in n.get("params", {}).items() if spec.get("required", False)]
    dependencies = [(by_id[n["id"]], by_id[d]) for n in nodes.values()
                    if n["id"] in by_id for d in n.get("depends_on", []) if d in by_id]
    return nodes, calls, required_nodes, parameters, needed, dependencies


def _wrong_type(kind: str) -> Any:
    return {"string": 42, "number": "NaN", "integer": "42", "boolean": 1,
            "object": [], "array": {}}[kind]


def _changed_value(kind: str, ordinal: int) -> Any:
    return {"string": f"SYNTHETIC-{ordinal:07d}", "number": float(ordinal + 1),
            "integer": ordinal + 1, "boolean": bool(ordinal % 2),
            "object": {"synthetic": ordinal}, "array": [ordinal]}[kind]


def _mutations(policy: dict[str, Any], valid_trace: dict[str, Any], iteration: int):
    nodes, calls, required, params, needed, dependencies = _select(policy, valid_trace)
    variants: list[tuple[str, bool, dict[str, Any]]] = []

    def add(kind: str, expected: bool, transform):
        clone = copy.deepcopy(valid_trace)
        transform(clone["calls"])
        variants.append((kind, expected, clone))

    if params:
        idx, name, spec = params[iteration % len(params)]
        add("valid_type_boundary", True,
            lambda cs: cs[idx]["arguments"].__setitem__(name, _changed_value(spec["type"], iteration + 101)))
        add("wrong_parameter_type", False,
            lambda cs: cs[idx]["arguments"].__setitem__(name, _wrong_type(spec["type"])))
    if needed:
        idx, name = needed[iteration % len(needed)]
        add("missing_required_parameter", False,
            lambda cs: cs[idx]["arguments"].pop(name))
    if required:
        idx = iteration % len(required)
        target = required[idx]["id"]
        add("missing_required_step", False,
            lambda cs: cs.__setitem__(slice(None), [c for c in cs if c["step_id"] != target]))
    if dependencies:
        target, dep = dependencies[iteration % len(dependencies)]
        def swap(cs):
            cs[target], cs[dep] = cs[dep], cs[target]
        add("dependency_order", False, swap)
    if calls:
        index = iteration % len(calls)
        add("unknown_api_parameter", False,
            lambda cs: cs[index]["arguments"].__setitem__(f"__unknown_{iteration:05d}", True))
        add("wrong_tool", False,
            lambda cs: cs[index].__setitem__("tool", f"invalid.synthetic.tool.{iteration}"))
        add("duplicate_call", False,
            lambda cs: cs.insert(index + 1, copy.deepcopy(cs[index])))
        add("invalid_argument_container", False,
            lambda cs: cs[index].__setitem__("arguments", ["bad"]))
        add("tool_execution_failure", False,
            lambda cs: cs[index].__setitem__("status", "error"))
    add("unknown_step", False,
        lambda cs: cs.append({"step_id": f"unknown_{iteration:05d}", "tool": "synthetic.unknown",
                              "arguments": {}}))
    return variants


def _make_candidate(policy: dict[str, Any], trace: dict[str, Any], kind: str,
                    source: str, expected: bool, seed: int, ordinal: int) -> dict[str, Any]:
    verdict = grade_trajectory(policy, trace).passed
    if verdict != expected:
        raise ValueError(f"oracle mismatch for mutation {kind}: expected={expected} got={verdict}")
    policy_hash = digest(policy)
    trace_hash = digest(trace)
    return {"schema": SCHEMA, "candidate_id": f"syn-{digest([policy_hash, trace_hash])[:20]}",
            "policy_sha256": policy_hash, "trace_sha256": trace_hash, "generator": source,
            "seed": seed, "ordinal": ordinal, "mutation": kind, "expected_pass": expected,
            "review_status": "pending", "trace": trace}


def generate_candidates(policy: dict[str, Any], valid_trace: dict[str, Any],
                        count: int = 24, seed: int = 42) -> list[dict[str, Any]]:
    if type(count) is not int or not 1 <= count <= 500:
        raise ValueError("count must be between 1 and 500")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    _select(policy, valid_trace)
    rng = random.Random(seed)
    result: list[dict[str, Any]] = []
    seen: set[str] = {digest(valid_trace)}
    iteration = 0
    while len(result) < count and iteration < count * 30:
        variants = _mutations(policy, valid_trace, iteration)
        rng.shuffle(variants)
        for kind, expected, trace in variants:
            trace_hash = digest(trace)
            if trace_hash in seen:
                continue
            candidate = _make_candidate(policy, trace, kind, "deterministic", expected, seed, iteration)
            seen.add(trace_hash)
            result.append(candidate)
            if len(result) >= count:
                break
        iteration += 1
    if len(result) != count:
        raise ValueError(f"only {len(result)} unique mutations could be produced for {count} requested")
    return result


def validate_candidates(policy: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    validate_policy(policy)
    seen_ids: set[str] = set()
    seen_traces: set[str] = set()
    for row in candidates:
        if not isinstance(row, dict) or row.get("schema") != SCHEMA:
            raise ValueError("invalid candidate schema")
        if row.get("review_status") != "pending":
            raise ValueError("candidate status must be pending")
        if row.get("policy_sha256") != digest(policy):
            raise ValueError("policy hash mismatch")
        trace = row.get("trace")
        if not isinstance(trace, dict) or digest(trace) != row.get("trace_sha256"):
            raise ValueError("trace hash mismatch")
        ident = row.get("candidate_id")
        if ident != f"syn-{digest([digest(policy), digest(trace)])[:20]}":
            raise ValueError("candidate_id mismatch")
        if ident in seen_ids or row["trace_sha256"] in seen_traces:
            raise ValueError("duplicate candidate or trajectory")
        seen_ids.add(ident)
        seen_traces.add(row["trace_sha256"])
        if type(row.get("expected_pass")) is not bool or grade_trajectory(policy, trace).passed != row["expected_pass"]:
            raise ValueError("candidate grading label mismatch")
        if row.get("generator") not in ("deterministic", "llm"):
            raise ValueError("unsupported generator")


def promote_reviewed(policy: dict[str, Any], candidates: list[dict[str, Any]],
                     reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return separate curated labels only for explicitly approved candidates."""
    validate_candidates(policy, candidates)
    by_id = {r["candidate_id"]: r for r in candidates}
    decisions: dict[str, dict[str, Any]] = {}
    for review in reviews:
        ident = review.get("candidate_id")
        if ident not in by_id or ident in decisions:
            raise ValueError("unknown or duplicate review candidate_id")
        if review.get("decision") not in ("approve", "reject"):
            raise ValueError("decision must be approve or reject")
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            raise ValueError("reviewer name required")
        if not isinstance(review.get("rationale"), str) or not review["rationale"].strip():
            raise ValueError("review rationale required")
        decisions[ident] = review
    approved = []
    for item in candidates:
        decision = decisions.get(item["candidate_id"])
        if decision and decision["decision"] == "approve":
            approved.append({"case_id": item["candidate_id"], "expected_pass": item["expected_pass"],
                             "trace": item["trace"]})
    if not approved:
        raise ValueError("no approved cases; nothing was promoted")
    return approved


def read_candidates(path: Path) -> list[dict[str, Any]]:
    return _records(path)
