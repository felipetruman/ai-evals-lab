"""Offline, explainable cross-corpus overlap checks for training vs evaluation.

No raw text in results. Lexical scores are deterministic; supplied embeddings
are checked numerically but are never generated or downloaded by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = "ai-evals-lab/dataset-contamination/v1"
WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class CorpusItem:
    item_id: str
    normalized: str
    tokens: tuple[str, ...]
    embedding: tuple[float, ...] | None


@dataclass(frozen=True)
class Match:
    eval_id: str
    train_id: str
    severity: str
    signals: list[str]
    ngram_jaccard: float
    ngram_containment: float
    shared_ngrams: int
    cosine_similarity: float | None


@dataclass(frozen=True)
class ScanReport:
    schema: str
    train_count: int
    eval_count: int
    compared_pairs: int
    flagged_eval_count: int
    flagged_pair_count: int
    high_severity_pairs: int
    semantic_available: bool
    train_sha256: str
    eval_sha256: str
    matches: list[Match]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: str) -> str:
    """NFKC/casefold and normalized whitespace; do not claim paraphrase equivalence."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _fingerprint(items: list[CorpusItem]) -> str:
    canonical = [(x.item_id, x.normalized, list(x.embedding) if x.embedding else None)
                 for x in sorted(items, key=lambda x: x.item_id)]
    data = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def read_corpus(path: Path) -> list[CorpusItem]:
    """Read bounded JSONL with unique IDs, required text and optional numeric vectors."""
    output: list[CorpusItem] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for lineno, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"{path.name}:{lineno}: blank JSONL line")
            try:
                row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{lineno}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{lineno}: record must be an object")
            ident, value = row.get("id"), row.get("text")
            if not isinstance(ident, str) or not ident.strip() or ident in seen or len(ident) > 128:
                raise ValueError(f"{path.name}:{lineno}: missing, duplicate or oversized id")
            if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
                raise ValueError(f"{path.name}:{lineno}: missing or oversized text")
            seen.add(ident)
            normalized = normalize_text(value)
            if not WORD.search(normalized):
                raise ValueError(f"{path.name}:{lineno}: text must contain words")
            vector = row.get("embedding")
            embedding = None
            if vector is not None:
                if (not isinstance(vector, list) or not vector or len(vector) > 4096 or
                    any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)):
                    raise ValueError(f"{path.name}:{lineno}: invalid embedding")
                magnitude = math.sqrt(sum(float(v) ** 2 for v in vector))
                if not math.isfinite(magnitude) or magnitude == 0:
                    raise ValueError(f"{path.name}:{lineno}: invalid embedding norm")
                embedding = tuple(float(v) / magnitude for v in vector)
            output.append(CorpusItem(ident, normalized, tuple(WORD.findall(normalized)), embedding))
    if not output:
        raise ValueError(f"{path.name}: dataset must not be empty")
    return output


def _grams(item: CorpusItem, size: int) -> set[tuple[str, ...]]:
    return {item.tokens[i:i + size] for i in range(len(item.tokens) - size + 1)}


def _check_threshold(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError(f"{label} must be in (0, 1]")


def scan(
    train: list[CorpusItem], evaluation: list[CorpusItem], *,
    ngram_size: int = 3, min_shared_ngrams: int = 2,
    jaccard_threshold: float = 0.8, containment_threshold: float = 0.95,
    cosine_threshold: float = 0.97, max_pairs: int = 250_000,
) -> ScanReport:
    """Find potential leakage across corpora; flags require human adjudication.

    This is a quadratic reference scanner. Larger corpora need indexed retrieval.
    """
    if not train or not evaluation:
        raise ValueError("train and evaluation must be non-empty")
    if type(ngram_size) is not int or not 2 <= ngram_size <= 10:
        raise ValueError("ngram_size must be between 2 and 10")
    if type(min_shared_ngrams) is not int or not 1 <= min_shared_ngrams <= 100:
        raise ValueError("min_shared_ngrams must be between 1 and 100")
    for value, label in ((jaccard_threshold, "jaccard_threshold"),
                         (containment_threshold, "containment_threshold"),
                         (cosine_threshold, "cosine_threshold")):
        _check_threshold(value, label)
    if type(max_pairs) is not int or max_pairs < 1:
        raise ValueError("max_pairs must be positive")
    total = len(train) * len(evaluation)
    if total > max_pairs:
        raise ValueError(f"refusing {total} comparisons above max_pairs={max_pairs}; use a retrieval index")
    all_items = train + evaluation
    has_embeddings = [x.embedding is not None for x in all_items]
    if any(has_embeddings) and not all(has_embeddings):
        raise ValueError("partial embedding coverage would create false negatives")
    has_semantic = all(has_embeddings)
    if has_semantic and len({len(x.embedding) for x in all_items if x.embedding}) != 1:
        raise ValueError("embedding dimensions must be identical across corpora")

    train_grams = [_grams(t, ngram_size) for t in train]
    eval_grams = [_grams(e, ngram_size) for e in evaluation]
    matches: list[Match] = []
    for evaluation_item, egrams in zip(evaluation, eval_grams):
        for training_item, tgrams in zip(train, train_grams):
            exact = training_item.normalized == evaluation_item.normalized
            shared = len(tgrams & egrams)
            union = len(tgrams | egrams)
            jaccard = shared / union if union else 0.0
            containment = shared / min(len(tgrams), len(egrams)) if tgrams and egrams else 0.0
            lexical = shared >= min_shared_ngrams
            cosine = None
            if has_semantic:
                cosine = sum(a * b for a, b in zip(training_item.embedding, evaluation_item.embedding))
                cosine = max(-1.0, min(1.0, cosine))
            signals = []
            if exact:
                signals.append("EXACT_NORMALIZED")
            if lexical and jaccard >= jaccard_threshold:
                signals.append("NGRAM_JACCARD")
            if lexical and containment >= containment_threshold:
                signals.append("NGRAM_CONTAINMENT")
            if cosine is not None and cosine >= cosine_threshold:
                signals.append("EMBEDDING_COSINE")
            if signals:
                matches.append(Match(evaluation_item.item_id, training_item.item_id,
                                     "high" if any(s != "EMBEDDING_COSINE" for s in signals) else "review",
                                     signals, round(jaccard, 6), round(containment, 6), shared,
                                     round(cosine, 6) if cosine is not None else None))
    matches.sort(key=lambda m: (m.eval_id, m.train_id))
    return ScanReport(SCHEMA, len(train), len(evaluation), total,
                      len({m.eval_id for m in matches}), len(matches),
                      sum(m.severity == "high" for m in matches), has_semantic,
                      _fingerprint(train), _fingerprint(evaluation), matches)


@dataclass(frozen=True)
class LabelReview:
    reviewed_pairs: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    specificity: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def review_against_labels(
    report: ScanReport, train: list[CorpusItem], evaluation: list[CorpusItem], labels_path: Path
) -> LabelReview:
    """Assess a flagged-pair rule against independent, explicitly annotated pairs.

    This measures only the supplied adjudicated sample, not all unknown matches.
    """
    valid_train = {x.item_id for x in train}
    valid_eval = {x.item_id for x in evaluation}
    flagged = {(m.train_id, m.eval_id) for m in report.matches}
    seen: set[tuple[str, str]] = set()
    tp = fp = tn = fn = 0
    with labels_path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"review line {line_no}: blank record")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"review line {line_no}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"review line {line_no}: object expected")
            train_id, eval_id, contaminated = (row.get("train_id"), row.get("eval_id"), row.get("contaminated"))
            pair = (train_id, eval_id)
            if (not isinstance(train_id, str) or not isinstance(eval_id, str) or
                train_id not in valid_train or eval_id not in valid_eval or pair in seen):
                raise ValueError(f"review line {line_no}: unknown or duplicate pair")
            if type(contaminated) is not bool:
                raise ValueError(f"review line {line_no}: contaminated must be boolean")
            if not isinstance(row.get("reviewer"), str) or not row["reviewer"].strip():
                raise ValueError(f"review line {line_no}: reviewer required")
            seen.add(pair)
            predicted = pair in flagged
            if contaminated and predicted:
                tp += 1
            elif not contaminated and predicted:
                fp += 1
            elif not contaminated and not predicted:
                tn += 1
            else:
                fn += 1
    if not seen:
        raise ValueError("no labeled pairs")
    return LabelReview(len(seen), tp, fp, tn, fn,
                       round(tp / (tp + fp), 6) if tp + fp else None,
                       round(tp / (tp + fn), 6) if tp + fn else None,
                       round(tn / (tn + fp), 6) if tn + fp else None)
