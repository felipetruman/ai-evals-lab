"""Versionable labeled JSONL runner; reports exclude raw arguments."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .engine import grade_trajectory, validate_policy
from .otel import otlp_to_trajectory


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected_pass: bool
    actual_pass: bool
    matched: bool
    score: float
    required_coverage: float
    violation_codes: list[str]


@dataclass(frozen=True)
class BatchReport:
    workflow_id: str
    cases_total: int
    matched: int
    mismatched: int
    agreement: float
    actual_passed: int
    actual_failed: int
    cases: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_dataset(policy: dict[str, Any], dataset: Path) -> BatchReport:
    """Compare every evaluated trace to its label, refusing silently skipped fixtures."""
    validate_policy(policy)
    results: list[CaseResult] = []
    seen: set[str] = set()
    with dataset.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"line {line_number}: blank record")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number}: record must be an object")
            ident = row.get("case_id")
            if not isinstance(ident, str) or not ident.strip() or ident in seen:
                raise ValueError(f"line {line_number}: missing/duplicate case_id")
            seen.add(ident)
            expected = row.get("expected_pass")
            if type(expected) is not bool:
                raise ValueError(f"line {line_number}: expected_pass must be boolean")
            if ("trace" in row) == ("otlp" in row):
                raise ValueError(f"line {line_number}: specify exactly one of trace or otlp")
            try:
                trace = otlp_to_trajectory(row["otlp"]) if "otlp" in row else row["trace"]
                grade = grade_trajectory(policy, trace)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"line {line_number} ({ident}): {exc}") from exc
            results.append(CaseResult(ident, expected, grade.passed, expected == grade.passed,
                                      grade.score, grade.required_coverage,
                                      sorted({v.code for v in grade.violations})))
    if not results:
        raise ValueError("dataset must contain at least one case")
    matched = sum(case.matched for case in results)
    actual_passed = sum(case.actual_pass for case in results)
    return BatchReport(policy["workflow_id"], len(results), matched, len(results) - matched,
                       round(matched / len(results), 4), actual_passed, len(results) - actual_passed,
                       results)
