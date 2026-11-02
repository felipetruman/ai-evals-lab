# Project 14: Dataset Contamination Checker (v0.8)

## Scope

Find candidate overlap between training/fine-tuning data and held-out evaluation data. Corpus overlap is **not proof** that a particular model trained on an example or of when a leak happened.

## Data contract

Input: two UTF-8 JSONL files. Each line has a unique string id, a non-empty text and optionally a finite numeric embedding array. Embeddings must be supplied for **all** records or for **none**, and dimensionality must match.

Optional review-labels JSONL contains train_id, eval_id, boolean contaminated and non-empty reviewer. Metrics apply only to the explicitly adjudicated subset, not to all unlabeled data. Use pseudonymized identifiers.

## Detection

1. Exact Unicode NFKC + casefold + whitespace normalized text.
2. Word n-gram Jaccard and containment. Defaults: trigrams, at least two shared n-grams, Jaccard >= 0.80 or containment >= 0.95.
3. Cosine similarity on supplied vectors, default >= 0.97. **No embedding generation or model download in v0.8.** Generate both corpora with the same model, version and preprocessing.

Lexical matches are categorized as high; cosine-only signals need review. Both are candidate matches, not confirmed contamination. Boilerplate may cause false positives. Paraphrases, translations, obfuscation or short content may cause false negatives.

## How to reproduce

~~~bash
python -m pip install -e .
python -m unittest tests.test_contamination -v

python -m evals_lab.contamination_cli \
 --train examples/contamination-train.jsonl \
 --eval examples/contamination-eval-clean.jsonl \
 --output reports/contamination-clean.json
# exit 0

python -m evals_lab.contamination_cli \
 --train examples/contamination-train.jsonl \
 --eval examples/contamination-eval-leaked.jsonl \
 --output reports/contamination-leaked.json
# exit 2
~~~

Review 15 synthetically labeled pairs:

~~~bash
cat examples/contamination-eval-clean.jsonl examples/contamination-eval-leaked.jsonl > reports/combined-eval.jsonl
python -m evals_lab.contamination_cli \
 --train examples/contamination-train.jsonl \
 --eval reports/combined-eval.jsonl \
 --review-labels examples/contamination-reviewed-pairs.jsonl \
 --fail-on none --output reports/contamination-review.json
~~~

Synthetic demonstration: TP=3, FP=0, TN=12, FN=0. These crafted results do **not** establish empirical detection accuracy on real data.

## Safety and limits

- Runs offline without external inference; reports expose IDs, score metrics and corpus fingerprints, not raw text.
- Pairwise O(train × eval), with a conservative limit of 250,000 comparisons. Large datasets need indexed retrieval with measured recall.
- Rejects invalid/duplicate IDs, missing text, inconsistent vectors, NaN and oversized workloads.
- CI gate defaults to --fail-on any, with high and none choices. Configure thresholds using a *separate*, independently labeled calibration sample, not the held-out final test set.
- Cannot detect data exposure from model pretraining, third-party corpora, private logs or actual memorization that was not supplied for scanning.
