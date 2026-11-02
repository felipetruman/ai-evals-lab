# Project 05: RAG Adversarial Harness (v0.9)

This offline deterministic harness checks whether **structured fact values** in a candidate RAG response are supported by supplied documents, with correct source citations and abstentions.

## Contract

One JSONL record per case:
```json
{"case_id":"example","scenario":"grounded_answer","expected_pass":true,"question":{"fact_key":"refund_days"},"context":[{"id":"source-a","facts":[{"key":"refund_days","value":"30"}]}],"response":{"status":"answered","value":"30","citations":["source-a"]}}
```

- If sources consistently supply a unique value: answer it and cite at least one relevant supporting document.
- If the sources have conflicting values: `status=abstained`, `value=null`, `citations=[]`, `reason=conflicting_evidence`.
- If no source supplies the fact: abstain with `reason=insufficient_evidence`.
- Unsupported value, absent/fictional/irrelevant citation, unnecessary abstention or wrong reason fail.
- The dataset label `expected_pass` is compared to the verdict to detect evaluation regressions, not used to compute the verdict.

## Reproduce

```bash
python -m pip install -e .
python -m unittest discover -s tests -p 'test_rag.py' -v
python -m evals_lab.rag_cli --dataset examples/rag-adversarial.jsonl --output reports/rag-adversarial.json
```

Expected for **14 synthetic cases**: 14/14 agreement, 5 successful, 9 expected failures; abstention required in 6 cases and correctly performed in 3. These are crafted fixtures, **not measured LLM quality**.

## Security and methodological limitations

This evaluator does not understand arbitrary natural language, assess semantic entailment, query a retriever, or prove that retrieval included every relevant document. It validates only explicit key/value ground truth in the supplied input. Source IDs and scenario labels should be pseudonymous; reports omit raw values. It never makes external requests. Tests must remain held-out and not be reused as training data. Future work: independently human-labeled free-text evidence, retrieval completeness, statistical uncertainty, and optional real-model adapters.
