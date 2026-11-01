# Project 07 — Statistical Significance Engine (v0.6)

## Purpose

Compare **two models on exactly the same independent cases**, without treating
repeated measurements as independent. A raw 2-point difference on 50 cases is
not reliable evidence by itself. This tool makes uncertainty and assumptions
explicit. It does **not** measure model performance by making LLM calls.

## Data contract (JSONL)

Each line contains exactly three fields:

```json
{"case_id":"independent-task-001","model_a_success":true,"model_b_success":false}
```

- `case_id` must be unique and identify a single independent evaluation unit.
- Both models must have completed the **same** case, using the same task and scoring rubric.
- Boolean success values should come from a previously validated evaluator.
- Missing, malformed, repeated and extraneous fields cause an input error; no cases are silently omitted.
- Do not use raw production records or secrets; run on sanitized input only.

## Statistical methodology

1. **Point estimate:** `delta_pp = 100 × (mean(success_B) - mean(success_A))`.
   This is a difference in **percentage points** (pp), not percentage relative improvement.
2. **Confidence interval:** paired percentile bootstrap on matched case IDs,
   with replacement, seeded pseudorandom generator, 5,000 resamples by default.
   Percentile endpoints are interpolated from the sorted bootstrap distribution.
3. **Hypothesis test:** exact two-sided **McNemar test**, conditioned on discordant
   A-only and B-only cases. Under the null, A-only vs. B-only is a fair binomial
   outcome. The p-value is calculated independently of bootstrap resampling.
4. **Decision:** `direction=B` for B advantage if exact p < alpha and delta > 0;
   `A` for the reverse; `inconclusive` otherwise. Lack of significance does **not**
   prove equivalence; equivalence needs a prespecified margin and separate test.
5. **Diagnostic warnings:** fewer than 30 cases, fewer than 10 discordant pairs,
   degenerate resampling or disagreement between bootstrap interval and exact test.

Results provide `ci_low_pp`, `ci_high_pp` rather than assuming symmetric error
bars (many bootstrap intervals are asymmetric). Do **not** claim a p-value
from the bootstrap percentile interval or infer statistical significance
simply from a descriptive accuracy difference.

References:
- [SciPy bootstrap: paired resampling and percentile CI](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)
- [SciPy exact binomial test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html)

## Reproduction

```bash
python -m pip install -e '.[collector]'
python -m unittest discover -s tests -v
python -m evals_lab.significance_cli \
  --dataset examples/synthetic-model-comparison.jsonl \
  --model-a 'Model A' --model-b 'Model B' \
  --resamples 5000 --seed 42 --confidence 0.95 \
  --output reports/significance.json
```

The synthetic dataset has **100 matched cases**: 60 both succeed, 25 only B
succeeds, 5 only A succeeds, 10 neither succeeds. Thus A = 65%, B = 85%,
and B − A = +20.0 pp. This is a *constructed demonstration*, not measured
accuracy of any commercial model. The exact two-sided McNemar p-value is
calculated from the 30 discordant cases; inspect the CLI for the result.

The CLI returns exit 0 if the analysis executed successfully (even if it is
**inconclusive**) and exit 1 for invalid input. A low p-value is not a CI/CD
regression gate by default; the dataset and threshold must be validated first.

## Limitations

- The independent resampling unit is the `case_id`. If cases are correlated
  (multiple attempts on one user, conversations from one session, repeated
  prompts or shared documents), bootstrap at the appropriate *cluster* level
  instead. This v0.6 implementation does not support clustered bootstrap.
- Inputs are binary success/failure. Scores, costs and latency require
  separate tests and appropriate uncertainty models.
- Percentile bootstrap can have poor coverage for small, discrete or
  highly skewed samples; the interval is exploratory, not a universal guarantee.
- P-values presume a prespecified single comparison; searching over prompts,
  thresholds, seeds and many models without correction inflates false positives.
- Effect size, costs, statistical uncertainty, representativeness and safety
  matter together; significance alone is not a routing decision.
- No power analysis, sequential analysis, confidence sequences, Bayesian
  methods, multiple testing correction, dataset stratification or equivalence
  margins are implemented yet.
