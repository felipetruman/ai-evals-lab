"""Deterministic, evidence-backed RAG response evaluation for structured facts.

This is an offline reference harness. It does not infer meaning from free text,
validate retrieval completeness, or invoke any model. Documents are deliberately
structured as explicit ground-truth facts to avoid unsupported NLP claims.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = "ai-evals-lab/rag-adversarial/v1"
MAX_RECORDS = 10_000


@dataclass(frozen=True)
class Verdict:
    passed: bool
    required_action: str
    required_reason: str | None
    violations: list[str]
    supporting_source_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    scenario: str
    expected_pass: bool
    actual_pass: bool
    matched: bool
    required_action: str
    violation_codes: list[str]


@dataclass(frozen=True)
class RagReport:
    schema: str
    cases_total: int
    matched: int
    mismatched: int
    agreement: float
    passed: int
    failed: int
    abstention_required: int
    correct_abstentions: int
    cases: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _label(value: Any, label: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string with length <= {limit}")
    return value


def _validate_case(row: dict[str, Any]) -> tuple[str, dict[str, dict[str, str]], dict[str, Any]]:
    if not isinstance(row, dict):
        raise ValueError("case must be an object")
    question = row.get("question")
    if not isinstance(question, dict):
        raise ValueError("question must be an object")
    key = _label(question.get("fact_key"), "question.fact_key")
    docs = row.get("context")
    if not isinstance(docs, list) or len(docs) > 1000:
        raise ValueError("context must be a list of at most 1000 documents")
    source_by_id: dict[str, dict[str, str]] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError("document must be an object")
        doc_id = _label(doc.get("id"), "document.id")
        if doc_id in source_by_id:
            raise ValueError("duplicate document.id")
        facts = doc.get("facts")
        if not isinstance(facts, list) or len(facts) > 1000:
            raise ValueError("document.facts must be a list of at most 1000 items")
        mapping: dict[str, str] = {}
        for fact in facts:
            if not isinstance(fact, dict):
                raise ValueError("fact must be an object")
            fact_key = _label(fact.get("key"), "fact.key")
            value = _label(fact.get("value"), "fact.value", 1000)
            if fact_key in mapping:
                raise ValueError("duplicate fact key in document")
            mapping[fact_key] = value
        source_by_id[doc_id] = mapping
    response = row.get("response")
    if not isinstance(response, dict) or response.get("status") not in ("answered", "abstained"):
        raise ValueError("response.status must be answered or abstained")
    citations = response.get("citations")
    if not isinstance(citations, list) or len(citations) > 1000 or not all(isinstance(c, str) and c for c in citations):
        raise ValueError("response.citations must be an array of nonempty source IDs")
    if response["status"] == "answered":
        _label(response.get("value"), "response.value", 1000)
        if response.get("reason") not in (None, ""):
            raise ValueError("answered response must not provide abstention reason")
    else:
        if response.get("value") is not None:
            raise ValueError("abstained response.value must be null")
        if response.get("reason") not in ("insufficient_evidence", "conflicting_evidence"):
            raise ValueError("abstained response.reason must be a recognized reason")
    return key, source_by_id, response


def evaluate_case(row: dict[str, Any]) -> Verdict:
    """Check a response against *all supplied* structured evidence.

    If the dataset lacks evidence or contains conflicting values, abstention is
    required. Citing an irrelevant or unsupporting document fails the answer.
    A missing document not included in context cannot be inferred.
    """
    key, sources, response = _validate_case(row)
    evidence = {doc_id: facts[key] for doc_id, facts in sources.items() if key in facts}
    distinct = set(evidence.values())
    required_action = "answered" if len(distinct) == 1 else "abstained"
    reason = "conflicting_evidence" if len(distinct) > 1 else (
        "insufficient_evidence" if not distinct else None
    )
    violations: list[str] = []
    cited = response["citations"]
    if len(set(cited)) != len(cited):
        violations.append("DUPLICATE_CITATION")
    for cid in cited:
        if cid not in sources:
            violations.append("UNKNOWN_CITATION")
        elif key not in sources[cid]:
            violations.append("IRRELEVANT_CITATION")
        elif response["status"] == "answered" and sources[cid][key] != response["value"]:
            violations.append("CITATION_NOT_SUPPORTING")

    if response["status"] == "answered":
        if required_action == "abstained":
            violations.append("MISSED_ABSTENTION_ON_CONFLICT" if reason == "conflicting_evidence"
                              else "MISSED_ABSTENTION_WITHOUT_EVIDENCE")
        else:
            required_value = next(iter(distinct))
            if response["value"] != required_value:
                violations.append("UNSUPPORTED_ANSWER")
            if not cited:
                violations.append("MISSING_CITATION")
            if not any(cid in evidence and evidence[cid] == response["value"] for cid in cited):
                violations.append("NO_SUPPORTING_CITATION")
    else:
        if required_action == "answered":
            violations.append("UNNECESSARY_ABSTENTION")
        if response["reason"] != reason:
            violations.append("INCORRECT_ABSTENTION_REASON")
        if cited:
            violations.append("CITATION_ON_ABSTENTION")

    return Verdict(not violations, required_action, reason, sorted(set(violations)), sorted(evidence))


def evaluate_jsonl(path: Path) -> RagReport:
    """Compare independent fixture labels with deterministic evidence verdicts."""
    rows: list[CaseResult] = []
    seen: set[str] = set()
    required_abstentions = correct_abstentions = 0
    with path.open(encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            if line_num > MAX_RECORDS:
                raise ValueError(f"dataset exceeds {MAX_RECORDS} rows")
            if not line.strip():
                raise ValueError(f"line {line_num}: blank JSONL record")
            try:
                row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
                cid = _label(row.get("case_id"), "case_id")
                scenario = _label(row.get("scenario"), "scenario")
                expected = row.get("expected_pass")
                if type(expected) is not bool:
                    raise ValueError("expected_pass must be boolean")
                if cid in seen:
                    raise ValueError("duplicate case_id")
                verdict = evaluate_case(row)
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
                raise ValueError(f"line {line_num}: {exc}") from exc
            seen.add(cid)
            if verdict.required_action == "abstained":
                required_abstentions += 1
                if verdict.passed and row["response"]["status"] == "abstained":
                    correct_abstentions += 1
            rows.append(CaseResult(cid, scenario, expected, verdict.passed, expected == verdict.passed,
                                   verdict.required_action, verdict.violations))
    if not rows:
        raise ValueError("dataset must contain at least one case")
    matched = sum(c.matched for c in rows)
    passed = sum(c.actual_pass for c in rows)
    return RagReport(SCHEMA, len(rows), matched, len(rows) - matched,
                     round(matched / len(rows), 6), passed, len(rows) - passed,
                     required_abstentions, correct_abstentions, rows)
