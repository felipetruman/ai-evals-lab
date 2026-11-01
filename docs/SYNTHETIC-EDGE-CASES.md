# Project 12 — Synthetic Edge-Case Generator (v0.7)

## Purpose and boundaries

Produce diverse, reproducible adversarial **candidate** trajectories against a deterministic DAG policy without running any real agent tools. Evaluate the *grading rubric*, not the correctness of an external provider or a production refund workflow. Generated cases are **NOT** part of the golden dataset until a person explicitly reviews them.

## Deterministic generator (offline; default)

```bash
python -m pip install -e .
python -m evals_lab.synthetic_cli generate \
  --policy examples/policy.json --seed-trace examples/valid.json \
  --seed 42 --count 24 --output reports/synthetic-candidates.jsonl
```

- Stable seeded randomization; same policy, seed, and code yield identical results.
- Cases cover missing mandatory steps, dependency order, nonexistent API parameters, missing parameters, incorrect types, duplicate calls, failed tools, unexpected steps, invalid argument containers, and **type-valid argument variations**.
- SHA-256 fingerprints represent the canonical policy and full trajectory, while candidate IDs are deterministic and unique within the policy. SHA-256 is **not a cryptographic provenance attestation**; there is no external signature.
- Each candidate contains `review_status="pending"`, `mutation`, `generator`, and `expected_pass`. The label means *policy-engine acceptance*, **not semantic/task correctness or real-world boundary validity**. The grader is also used for consistency checks, so the label is not an independent oracle.
- The process never executes tool calls, processes production traces, or contacts an API.

## Optional model-assisted generation

An OpenAI-compatible `/v1/chat/completions` endpoint can generate **untrusted** candidate traces. The default is offline. The user must explicitly opt in with `--source llm --allow-network`. The only allowed URL schemes are HTTPS and localhost/loopback HTTP. Redirects are disabled. The key is read from `EVALS_LLM_API_KEY` (never put it in a command line argument).

```bash
# Example for a self-hosted, OpenAI-compatible endpoint (only if one is available)
python -m evals_lab.synthetic_cli generate \
  --source llm --allow-network --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --model YOUR_MODEL_NAME --policy examples/policy.json \
  --seed-trace examples/valid.json --count 8 \
  --output reports/model-candidates.jsonl
```

**Privacy:** This operation sends the entire policy and seed trace to the specified endpoint. The CLI **does not sanitize secrets or personal data**. Only use synthetic fixtures. No live endpoint/inference is exercised in the default CI; model protocol tests use simulated HTTP responses. Model-generated traces are not executed; generated labels are provisional and require human review.

## Human-reviewed promotion (separate output)

Inspect each candidate, verify that the intended error is meaningful and that fields are genuinely synthetic, and manually create a JSONL review file:

```json
{"candidate_id":"syn-REPLACE_WITH_REAL_ID","decision":"approve","reviewer":"YOUR_HANDLE","rationale":"Verified DAG dependency failure and no sensitive data"}
```

`decision` can be `approve` or `reject`. Unreviewed candidates are ignored. The test suite does not pretend to have human reviews: it simulates review decisions solely to verify the gate.

```bash
python -m evals_lab.synthetic_cli promote \
  --policy examples/policy.json \
  --candidates reports/synthetic-candidates.jsonl \
  --reviews reports/manual-reviews.jsonl \
  --output reports/curated-cases.jsonl

# Optionally evaluate the new curated file *without altering* the existing golden dataset
python -m evals_lab.batch_cli \
  --policy examples/policy.json --dataset reports/curated-cases.jsonl
```

All JSONL output paths use exclusive creation: no overwriting existing files, including golden fixtures. The promotion output has `case_id`, `expected_pass`, and `trace`, as required by the existing batch engine.

## Threat model and limitations

1. **Label leakage/circularity:** deterministic policy labels are obtained from the same grader that evaluates the data. These test consistency and basic invariants, not independently proven model quality. Human/domain validators must supply semantic ground truth.
2. **Out-of-distribution:** mutations cover **structural** edge cases and type boundaries. No claim of statistical OOD coverage, novelty relative to production, or future agent failures is made.
3. **Limited LLM protocol:** optional provider accepts OpenAI-compatible `chat/completions` non-streaming JSON content only; it is not a general vendor-neutral protocol for all models. No assertion of model quality is made.
4. **No automatic merge:** reviewed candidates live in a separate artifact until someone decides to modify and version the golden dataset. The CI does not change production evaluations.
5. **Review identity:** a reviewer field in a JSONL file is self-reported and is not an authenticated approval/signature.
6. **Privacy and abuse:** never use real production traces, customer identifiers or API keys in candidate generation. `reports/` is gitignored; external model endpoint explicitly sends the provided inputs.

## Reproduction gates

```bash
python -m unittest discover -s tests -v
python -m evals_lab.synthetic_cli generate \
  --policy examples/policy.json --seed-trace examples/valid.json \
  --seed 42 --count 24 --output reports/synthetic-candidates.jsonl
```

The CI verifies deterministic fingerprints, uniqueness, policy labels, a positive/negative mix and that all cases are pending review. It does not invoke real paid models or approve cases.
