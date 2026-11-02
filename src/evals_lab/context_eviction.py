"""Offline, deterministic context-window eviction benchmark.

Only simulates typed key/value retrieval. Neither model tokenization nor natural-
language reasoning, learned summarization, or production memory are evaluated.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = "ai-evals-lab/context-eviction/v1"
STRATEGIES = ("recency", "priority", "structured_summary")
_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def proxy_tokens(text: str) -> int:
    """Deterministic proxy, NOT the token count of any particular LLM."""
    return len(_TOKEN.findall(text))


@dataclass(frozen=True)
class Record:
    item_id: str
    key: str | None
    value: str | None
    text: str
    priority: int
    position: int

    @property
    def is_fact(self) -> bool:
        return self.key is not None


def validate_case(case: dict[str, Any]) -> list[Record]:
    if not isinstance(case, dict) or case.get("schema") != SCHEMA:
        raise ValueError("invalid/missing scenario schema")
    ident = case.get("case_id")
    if not isinstance(ident, str) or not ident or len(ident) > 128:
        raise ValueError("invalid case_id")
    budget = case.get("budget_tokens")
    if type(budget) is not int or not 0 <= budget <= 100000:
        raise ValueError("budget_tokens must be integer in 0..100000")
    target = case.get("query_key")
    if not isinstance(target, str) or not target or len(target) > 128:
        raise ValueError("query_key must be a nonempty string")
    if "expected_value" not in case or (case["expected_value"] is not None and not isinstance(case["expected_value"], str)):
        raise ValueError("expected_value must be null or string")
    data = case.get("records")
    if not isinstance(data, list) or not data or len(data) > 1000:
        raise ValueError("records must be a nonempty list with at most 1000 entries")
    seen: set[str] = set()
    records: list[Record] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("each record must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 128 or item_id in seen:
            raise ValueError("record id must be unique, nonempty and <=128 chars")
        seen.add(item_id)
        kind = item.get("kind")
        if kind not in ("fact", "noise"):
            raise ValueError("kind must be fact or noise")
        content = item.get("text")
        if not isinstance(content, str) or not content.strip() or len(content) > 10000:
            raise ValueError("record text must be nonempty and <=10000 chars")
        prio = item.get("priority", 0)
        if type(prio) is not int or not 0 <= prio <= 10:
            raise ValueError("priority must be integer in 0..10")
        key = item.get("key")
        value = item.get("value")
        if kind == "fact":
            if not isinstance(key, str) or not key or len(key) > 128 or not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError("fact needs nonempty key and value")
            if key.casefold() not in content.casefold() or value.casefold() not in content.casefold():
                raise ValueError("fact key and value must occur in text")
        elif key is not None or value is not None:
            raise ValueError("noise must not have key/value")
        records.append(Record(item_id, key, value, content, prio, index))
    return records


def record_cost(record: Record, strategy: str) -> int:
    if strategy == "structured_summary" and record.is_fact:
        payload = f"{record.key} = {record.value}"
    else:
        payload = f"[{record.item_id}] {record.text}"
    return proxy_tokens(payload)


def select_records(records: list[Record], budget_tokens: int, strategy: str) -> tuple[list[Record], int]:
    """Select complete records within budget. Never edit values or split facts."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy}")
    if type(budget_tokens) is not int or budget_tokens < 0:
        raise ValueError("budget_tokens must be nonnegative integer")
    if strategy == "recency":
        ranked = list(reversed(records))
    else:
        ranked = sorted(records, key=lambda r: (r.priority, r.position), reverse=True)
    selected: list[Record] = []
    spent = 0
    for item in ranked:
        cost = record_cost(item, strategy)
        if spent + cost <= budget_tokens:
            selected.append(item)
            spent += cost
    selected.sort(key=lambda r: r.position)
    return selected, spent


def retrieve(records: list[Record], query_key: str) -> str | None:
    """Lookup of retained typed facts, newest retained value wins."""
    for record in reversed(records):
        if record.key == query_key:
            return record.value
    return None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    strategy: str
    budget_tokens: int
    selected_tokens: int
    original_tokens: int
    selected_ids: list[str]
    dropped_ids: list[str]
    correct: bool
    abstained: bool
    expected_answerable: bool
    error_kind: str | None


@dataclass(frozen=True)
class StrategySummary:
    strategy: str
    total: int
    correct: int
    accuracy: float
    answerable: int
    answerable_correct: int
    answerable_recall: float
    unanswerable: int
    correct_abstentions: int
    abstention_specificity: float
    missed: int
    stale_or_wrong: int
    token_utilization: float


@dataclass(frozen=True)
class Benchmark:
    schema: str
    cases_total: int
    results: list[CaseResult]
    summaries: list[StrategySummary]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_case(case: dict[str, Any], strategy: str) -> CaseResult:
    records = validate_case(case)
    selected, spent = select_records(records, case["budget_tokens"], strategy)
    expected = case["expected_value"]
    response = retrieve(selected, case["query_key"])
    correct = response == expected
    if correct:
        kind = None
    elif response is None:
        kind = "MISSED_FACT"
    else:
        kind = "STALE_OR_WRONG_FACT"
    selected_ids = [r.item_id for r in selected]
    ids = set(selected_ids)
    return CaseResult(case["case_id"], strategy, case["budget_tokens"], spent,
                      sum(record_cost(r, "recency") for r in records), selected_ids,
                      [r.item_id for r in records if r.item_id not in ids], correct,
                      response is None, expected is not None, kind)


def read_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for no, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"line {no}: empty JSONL entry")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {no}: invalid JSON") from exc
            validate_case(obj)
            if obj["case_id"] in seen:
                raise ValueError(f"line {no}: duplicate case_id")
            seen.add(obj["case_id"])
            cases.append(obj)
    if not cases:
        raise ValueError("at least one case required")
    return cases


def benchmark(cases: list[dict[str, Any]]) -> Benchmark:
    if not cases:
        raise ValueError("at least one case required")
    seen: set[str] = set()
    for case in cases:
        validate_case(case)
        if case["case_id"] in seen:
            raise ValueError("duplicate case_id")
        seen.add(case["case_id"])
    outcomes = [evaluate_case(case, strategy) for case in cases for strategy in STRATEGIES]
    summaries: list[StrategySummary] = []
    for strategy in STRATEGIES:
        rows = [r for r in outcomes if r.strategy == strategy]
        n = len(rows)
        answerable = [r for r in rows if r.expected_answerable]
        unanswerable = [r for r in rows if not r.expected_answerable]
        correct = sum(r.correct for r in rows)
        answerable_correct = sum(r.correct for r in answerable)
        correct_abstentions = sum(r.correct and r.abstained for r in unanswerable)
        total_budget = sum(r.budget_tokens for r in rows)
        utilization = sum(r.selected_tokens for r in rows) / total_budget if total_budget else 0.0
        summaries.append(StrategySummary(strategy, n, correct, round(correct / n, 4),
                     len(answerable), answerable_correct,
                     round(answerable_correct / len(answerable), 4) if answerable else 0.0,
                     len(unanswerable), correct_abstentions,
                     round(correct_abstentions / len(unanswerable), 4) if unanswerable else 0.0,
                     sum(r.error_kind == "MISSED_FACT" for r in rows),
                     sum(r.error_kind == "STALE_OR_WRONG_FACT" for r in rows),
                     round(utilization, 4)))
    return Benchmark(SCHEMA, len(cases), outcomes, summaries)
