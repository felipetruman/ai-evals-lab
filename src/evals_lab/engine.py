"""Deterministic step-level evaluator for agent tool-call trajectories.

This is deliberately provider-agnostic: examples use synthetic data and make
no external API calls. It evaluates recorded tool calls rather than model prose.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class InvalidPolicy(ValueError):
    """Raised when a policy is internally inconsistent or unsafe to evaluate."""


@dataclass(frozen=True)
class Violation:
    code: str
    step: str
    message: str


@dataclass(frozen=True)
class StepResult:
    index: int
    step_id: str
    tool: str
    passed: bool
    violation_codes: list[str]


@dataclass(frozen=True)
class Grade:
    workflow_id: str
    passed: bool
    score: float
    required_coverage: float
    checks_total: int
    checks_failed: int
    steps: list[StepResult]
    violations: list[Violation]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TYPES = {
    "string": lambda x: isinstance(x, str),
    "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
    "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    "boolean": lambda x: isinstance(x, bool),
    "object": lambda x: isinstance(x, dict),
    "array": lambda x: isinstance(x, list),
}


def validate_policy(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return an indexed policy, rejecting cycles and invalid definitions."""
    if not isinstance(policy, dict) or not isinstance(policy.get("workflow_id"), str):
        raise InvalidPolicy("workflow_id must be a string")
    nodes = policy.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise InvalidPolicy("nodes must be a non-empty array")
    indexed: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise InvalidPolicy("each node must be an object")
        ident, tool = node.get("id"), node.get("tool")
        if not isinstance(ident, str) or not ident or not isinstance(tool, str) or not tool:
            raise InvalidPolicy("each node requires non-empty string id and tool")
        if ident in indexed:
            raise InvalidPolicy(f"duplicate node id: {ident}")
        if type(node.get("required", True)) is not bool:
            raise InvalidPolicy(f"required must be boolean on {ident}")
        deps = node.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise InvalidPolicy(f"depends_on must be a string array on {ident}")
        if len(deps) != len(set(deps)):
            raise InvalidPolicy(f"duplicate dependency on {ident}")
        params = node.get("params", {})
        if not isinstance(params, dict):
            raise InvalidPolicy(f"params must be object on {ident}")
        for key, spec in params.items():
            if not isinstance(key, str) or not isinstance(spec, dict) or spec.get("type") not in _TYPES:
                raise InvalidPolicy(f"invalid parameter definition: {ident}.{key}")
            if type(spec.get("required", False)) is not bool:
                raise InvalidPolicy(f"parameter required must be boolean: {ident}.{key}")
        indexed[ident] = node

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(ident: str) -> None:
        if ident in visiting:
            raise InvalidPolicy("policy DAG has a cycle")
        if ident in visited:
            return
        visiting.add(ident)
        for dep in indexed[ident].get("depends_on", []):
            if dep not in indexed:
                raise InvalidPolicy(f"unknown dependency: {ident} -> {dep}")
            visit(dep)
        visiting.remove(ident)
        visited.add(ident)

    for ident in indexed:
        visit(ident)
    return indexed


def grade_trajectory(policy: dict[str, Any], trajectory: dict[str, Any]) -> Grade:
    """Grade a recorded trajectory against a strict DAG and parameter schemas.

    Verdict is *fail-closed*: any violation fails the entire trajectory.
    Score is diagnostic (1 - failed_checks / total_checks), not a calibrated
    probability of success. Safety dependencies are mandatory for gated calls.
    """
    nodes = validate_policy(policy)
    if not isinstance(trajectory, dict) or not isinstance(trajectory.get("calls"), list):
        raise ValueError("trajectory.calls must be an array")

    violations: list[Violation] = []
    steps: list[StepResult] = []
    observed: set[str] = set()
    successful: set[str] = set()
    checks_total = 0
    checks_failed = 0

    def check(ok: bool, code: str, ident: str, message: str) -> None:
        nonlocal checks_total, checks_failed
        checks_total += 1
        if not ok:
            checks_failed += 1
            violations.append(Violation(code, ident, message))

    for index, call in enumerate(trajectory["calls"]):
        if not isinstance(call, dict):
            check(False, "INVALID_CALL", f"#{index}", "tool call must be an object")
            steps.append(StepResult(index, f"#{index}", "", False, ["INVALID_CALL"]))
            continue

        ident = call.get("step_id")
        tool = call.get("tool")
        args = call.get("arguments")
        label = ident if isinstance(ident, str) else f"#{index}"
        previous = len(violations)
        known = isinstance(ident, str) and ident in nodes
        check(known, "UNKNOWN_STEP", label, "step_id not present in policy")
        if not known:
            steps.append(StepResult(index, label, str(tool), False, [v.code for v in violations[previous:]]))
            continue

        node = nodes[ident]
        check(ident not in observed, "DUPLICATE_STEP", ident, "step called more than once")
        observed.add(ident)
        check(tool == node["tool"], "TOOL_MISMATCH", ident, f"expected {node['tool']!r}, got {tool!r}")
        check(isinstance(args, dict), "INVALID_ARGUMENTS", ident, "arguments must be an object")
        if "status" in call:
            check(call["status"] == "ok", "TOOL_EXECUTION_FAILED", ident, "tool execution did not succeed")

        for dep in node.get("depends_on", []):
            check(dep in successful, "DEPENDENCY_NOT_SATISFIED", ident, f"dependency {dep!r} not previously completed successfully")

        if isinstance(args, dict):
            specs = node.get("params", {})
            for name, spec in specs.items():
                if spec.get("required", False):
                    check(name in args, "MISSING_ARGUMENT", ident, f"required parameter: {name}")
            for name, value in args.items():
                check(name in specs, "UNKNOWN_ARGUMENT", ident, f"unknown API parameter: {name}")
                if name in specs:
                    expected = specs[name]["type"]
                    check(_TYPES[expected](value), "ARGUMENT_TYPE", ident, f"{name} must have type {expected}")

        codes = [v.code for v in violations[previous:]]
        passed = not codes
        if passed:
            successful.add(ident)
        steps.append(StepResult(index, ident, str(tool), passed, codes))

    required = [ident for ident, node in nodes.items() if node.get("required", True)]
    for ident in required:
        check(ident in successful, "REQUIRED_STEP_FAILED", ident, "required step missing or unsuccessful")

    coverage = (len(successful.intersection(required)) / len(required)) if required else 1.0
    score = 100.0 * (checks_total - checks_failed) / checks_total if checks_total else 100.0
    return Grade(policy["workflow_id"], not violations, round(score, 2), round(coverage, 4),
                 checks_total, checks_failed, steps, violations)
