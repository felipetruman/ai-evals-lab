"""Paired binary model comparisons with a reproducible bootstrap CI and exact test.

Units must be independent cases measured once by *both* models. No external APIs.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Pair:
    case_id: str
    model_a_success: bool
    model_b_success: bool


@dataclass(frozen=True)
class Comparison:
    method: str
    model_a: str
    model_b: str
    sample_size: int
    success_a: int
    success_b: int
    success_rate_a: float
    success_rate_b: float
    delta_pp: float
    confidence: float
    ci_low_pp: float
    ci_high_pp: float
    p_value: float
    test: str
    alpha: float
    statistically_significant: bool
    direction: str
    discordant_a_only: int
    discordant_b_only: int
    discordant_total: int
    bootstrap_resamples: int
    random_seed: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_pairs(dataset: Path) -> list[Pair]:
    """Load one paired independent case per JSONL row. Fail on malformed inputs."""
    pairs: list[Pair] = []
    seen: set[str] = set()
    with dataset.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"line {line_no}: empty JSONL record")
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_no}: invalid JSON") from exc
            if not isinstance(item, dict):
                raise ValueError(f"line {line_no}: record must be an object")
            case_id = item.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
                raise ValueError(f"line {line_no}: missing or repeated case_id")
            a, b = item.get("model_a_success"), item.get("model_b_success")
            if type(a) is not bool or type(b) is not bool:
                raise ValueError(f"line {line_no}: model outcomes must be booleans")
            if set(item) != {"case_id", "model_a_success", "model_b_success"}:
                raise ValueError(f"line {line_no}: unexpected/missing fields")
            seen.add(case_id)
            pairs.append(Pair(case_id, a, b))
    if not pairs:
        raise ValueError("dataset must contain at least one paired case")
    return pairs


def exact_mcnemar_p_value(a_only: int, b_only: int) -> float:
    """Exact two-sided McNemar: conditional binomial P(X<=min(b,c)), p=0.5.

    The exact test evaluates the symmetric null on discordant observations;
    concordant pairs do not contribute to its p-value.
    """
    if type(a_only) is not int or type(b_only) is not int or min(a_only, b_only) < 0:
        raise ValueError("discordant counts must be nonnegative integers")
    discordant = a_only + b_only
    if not discordant:
        return 1.0
    low = min(a_only, b_only)
    # Exact integer arithmetic prevents underflow in the binomial tail sum.
    tail_count = sum(math.comb(discordant, k) for k in range(low + 1))
    return min(1.0, 2.0 * (tail_count / (1 << discordant)))


def _percentile(values: list[float], quantile: float) -> float:
    if not 0 <= quantile <= 1 or not values:
        raise ValueError("invalid quantile or empty distribution")
    position = (len(values) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def compare_pairs(
    pairs: list[Pair], *, model_a: str = "Model A", model_b: str = "Model B",
    confidence: float = 0.95, alpha: float = 0.05,
    resamples: int = 5000, seed: int = 42,
) -> Comparison:
    """Return descriptive rates, paired percentile CI and an exact McNemar p-value.

    Bootstraps are draws of *paired case indices*, not independent outcomes.
    Assumes cases are independent/exchangeable and sampled appropriately;
    no correction for repeated seeds, multiple comparisons or clustering.
    """
    if not pairs or not isinstance(pairs, list):
        raise ValueError("at least one paired case required")
    if not isinstance(model_a, str) or not model_a.strip() or not isinstance(model_b, str) or not model_b.strip() or model_a == model_b:
        raise ValueError("model names must be distinct non-empty strings")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be strictly between 0 and 1")
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be strictly between 0 and 1")
    if type(resamples) is not int or not 100 <= resamples <= 100000:
        raise ValueError("resamples must be between 100 and 100000")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("seed must be an integer in [0, 2**64)")
    if any(not isinstance(p, Pair) or type(p.model_a_success) is not bool or type(p.model_b_success) is not bool for p in pairs):
        raise ValueError("each pair must have boolean outcomes")
    if len({p.case_id for p in pairs}) != len(pairs) or any(not isinstance(p.case_id, str) or not p.case_id.strip() for p in pairs):
        raise ValueError("each pair must have a distinct non-empty case_id")

    n = len(pairs)
    sa = sum(p.model_a_success for p in pairs)
    sb = sum(p.model_b_success for p in pairs)
    a_only = sum(p.model_a_success and not p.model_b_success for p in pairs)
    b_only = sum(p.model_b_success and not p.model_a_success for p in pairs)
    changes = [int(p.model_b_success) - int(p.model_a_success) for p in pairs]
    delta = (sb - sa) / n

    rng = random.Random(seed)
    boot = []
    for _ in range(resamples):
        boot.append(sum(changes[rng.randrange(n)] for _ in range(n)) / n)
    boot.sort()
    tail = (1.0 - confidence) / 2
    low = _percentile(boot, tail)
    high = _percentile(boot, 1 - tail)
    p = exact_mcnemar_p_value(a_only, b_only)
    significant = p < alpha
    direction = "B" if significant and delta > 0 else "A" if significant and delta < 0 else "inconclusive"
    warnings: list[str] = []
    if n < 30:
        warnings.append("SMALL_SAMPLE: fewer than 30 independent paired cases")
    if a_only + b_only < 10:
        warnings.append("FEW_DISCORDANT_PAIRS: exact p-value may be uninformative")
    if len(set(changes)) == 1:
        warnings.append("DEGENERATE_BOOTSTRAP: CI has zero empirical spread")
    if (low > 0 or high < 0) != significant:
        warnings.append("CI_TEST_DISAGREE: percentile bootstrap CI and exact test differ")
    warnings.append("ASSUMPTIONS: independent cases, paired measurements, no dataset leakage or multiple testing")
    return Comparison(
        "paired_bootstrap_percentile_and_exact_mcnemar", model_a, model_b, n,
        sa, sb, round(sa / n, 6), round(sb / n, 6), round(100 * delta, 4),
        float(confidence), round(low * 100, 4), round(high * 100, 4), round(p, 10),
        "mcnemar_exact_two_sided", float(alpha), significant, direction,
        a_only, b_only, a_only + b_only, resamples, seed, warnings,
    )
