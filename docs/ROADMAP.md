# 15-project AI Evals Engineering Roadmap

## Rules of evidence
Every project is considered **done** only after runnable implementation, automated tests, deterministic or well-characterized sample data, reproducible evaluation results and limitations written down. Do not claim a benchmark or improvement that has not actually been measured.

## Phase 1: Evaluation foundations
1. **Trajectory Grading Engine** — status: working prototype with labeled JSONL batch runner, OTLP/JSON tool span import, strict fail-closed DAG, per-step errors and negative fixtures. Added official Python OpenTelemetry SDK instrumentation, failure injection, sampling-off and export-ledger consistency tests in v0.4. Future: semantic checks, alternative valid paths, external provenance and independent completeness evidence.
7. **Statistical Significance Engine** — **implemented v0.6 prototype:** paired percentile bootstrap, exact McNemar test, seeded reproducibility, confidence intervals, samples/warnings; future: clustered sampling and multiple testing correction.
12. **Synthetic Edge-Case Generator** — **implemented v0.7 prototype:** deterministic structural mutations with seeded reproduction, optional opt-in OpenAI-compatible generation, per-case SHA-256 fingerprints, candidate review gate, separate curated JSONL; future: independently labeled semantic/OOD tests and measured coverage.
14. **Dataset Contamination Checker** — **implemented v0.8 prototype:** normalized exact matches, token n-gram overlap/containment, optional supplied-embedding cosine similarity, synthetic human-label precision/recall demo and CI gate. Future: independently labeled threshold calibration and scalable ANN indexing.

## Phase 2: Reliability and security
5. **RAG Adversarial Harness** — **implemented v0.9 structured-fact prototype:** contradictory evidence, no/irrelevant sources, citation validity and safe abstention; future: independently labeled free-text entailment, retrieval evaluation and model integrations.
8. **Agent Red-Team Fuzzer** — untrusted tool outputs, malformed calls, loop limits, no outbound destructive effects.
13. **Context Window Eviction Tester** — long-context retrieval and compression, answer retention, noise tolerance.
11. **Counterfactual Replay Debugger** — captured dependencies, deterministic stubs, substitution of one node, replay diff.

## Phase 3: Continuous evaluation
3. **Calibrated LLM-as-a-Judge** — labeled anchors, repeated trials, position and verbosity bias, agreement metrics.
4. **CI/CD Regression Gate** — versioned golden datasets, practical thresholds, rollbacks, failure evidence attached to PR.
2. **Shadow Routing Comparator** — strictly isolated side effects, 5% configurable sampled traffic, cost/quality comparison, privacy.
9. **Production Drift Monitor** — stratified sample, incident thresholds, alerts and labeling review.

## Phase 4: Optimization and publication
10. **Cost-Quality Pareto Dashboard** — per-tenant metrics, token/latency/cost accounting, frontier and tradeoffs.
6. **Automated DPO Flywheel** — consent/data retention, actual ranked preference pairs, offline gates before fine-tune promotion.
15. **Public Eval Methodology Teardown** — three evidence-backed public technical writeups and limitations.

## Public portfolio layout
- **One public flagship repository:** this lab, with 15 independent modules in time.
- **Three case studies:** voice agent reliability, RAG factuality/memory, observability/cost. Use synthetic fixtures or expressly publishable metrics; never mirror private repositories.
- **Five-minute demo:** reproduce PASS and FAIL locally, show JSON report, explain each failure.
- **Quantified results:** only measured counts, confidence intervals and run metadata.

## Next milestone
Project 5 RAG adversarial harness with independently labeled source-grounding benchmarks; no real user data by default.
