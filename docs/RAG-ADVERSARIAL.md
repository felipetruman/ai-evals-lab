# Project 05: RAG Adversarial Harness (v0.9)

## Goal

Determine whether a candidate answer is supported by **the evidence supplied** to a retrieval-augmented generation (RAG) system. Explicitly test missing evidence, contradictory sources, irrelevant context, unsupported values and fake or missing citations. Evaluate refusal to guess (abstention) when evidence is absent or conflicting.

## Scope and precise limitations

This is an **offline deterministic reference evaluator for structured facts**. It does not yet send prompts to a language model or retrieve real-world passages. Documents contain `facts: [{key,value}]` with truth adjudicated for the fixture. The evaluator does **not** prove that an arbitrary natural-language sentence is entailed by a real document. It cannot detect evidence the retrieval step failed to return. An independent natural-language judge and retrieval recall tests will be separate extensions requiring calibration.

## Dataset contract

Each line of UTF-8 JSONL contains:

```json
{
  "case_id": "grounded-001",
  "scenario": "grounded_answer",
  "expected_pass": true,
  "question": {"fact_key": "refund_days"},
  "context": [
    {"id": "source-a", "facts": [{"key": "refund_days", "value": "30"}]}
  ],
  "response": {"status": "answered", "value": "30", "citations": ["source-a"]}
}
```

Valid abstentions have `status="abstained"`, `value=null`, `citations=[]`, and a `reason` of either `insufficient_evidence` (no document supports the key) or `conflicting_evidence` (documents disagree). For answerable facts, the evaluator requires an answer with the exact value, at least one relevant supporting source ID and no unsupported citations.

When multiple documents support *different values*, the harness requires abstention even if one particular document agrees with a proposed answer. A wrong `expected_pass` label is a fixture regression, not an evaluation success.

## Reproduce

```bash
python -m pip install -e .
python -m unittest discover -s tests -p 'test_rag.py' -v
python -m evals_lab.rag_cli --dataset examples/rag-adversarial.jsonl \
  --min-agreement 1.0 --output reports/rag-adversarial.json
```

Expected on the synthetic 14-case fixture: 5 successful, 9 intentionally rejected; 14/14 expected classifications reproduced; 6 cases require abstention, and 3 of those contain correct abstentions. These counts reflect **handcrafted cases, not a model performance benchmark**. All data is artificial.

## Failure taxonomy

- `MISSED_ABSTENTION_WITHOUT_EVIDENCE`: answering when evidence is absent or irrelevant.
- `MISSED_ABSTENTION_ON_CONFLICT`: answering despite contradictory documents.
- `UNSUPPORTED_ANSWER`: answer value does not match the only supported value.
- `MISSING_CITATION` / `NO_SUPPORTING_CITATION`: answer not grounded in a supporting source.
- `IRRELEVANT_CITATION` / `UNKNOWN_CITATION`: citation does not reference the relevant evidence.
- `CITATION_NOT_SUPPORTING`: cited document states a different value.
- `UNNECESSARY_ABSTENTION` / `INCORRECT_ABSTENTION_REASON`: unwarranted or incorrectly explained refusal.

## Operational and security boundaries

- Runs offline, with no paid model calls or network permissions.
- Fails on malformed datasets, duplicate source IDs and unsupported response types.
- JSON reports omit the raw source facts and answer values. Case identifiers and scenario labels should still be pseudonymous.
- The dataset must be independently curated. It must not be copied into training data and then reused to claim held-out performance (see Project 14).
- The evaluator is a validator for explicit fact-key/value test doubles, **not a complete natural-language RAG faithfulness evaluation**.
- Future work: pluggable answer adapters, human-labeled semantic entailment, retrieval coverage tests, and statistical uncertainty with Project 7.
