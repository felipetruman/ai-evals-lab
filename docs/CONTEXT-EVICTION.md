# Project 13: Context Window Eviction Tester (v0.11)

## Motivation

Long-running agents may lose critical facts when irrelevant recent messages consume their context budgets. This offline harness isolates **which structured records survive** a bounded context-selection policy, without confusing that with a live language model's reasoning accuracy.

## Reproduction

```bash
python -m pip install -e '.[collector]'
python -m unittest discover -s tests -p 'test_context_eviction.py' -v
python -m evals_lab.context_cli --dataset examples/context-eviction.jsonl --output reports/context-eviction.json
```

The 12 synthetic cases include early important facts followed by noise, a corrected/stale value, very long fact text, missing/unknown keys, insufficient budget and large-budget controls. The report contains 36 paired strategy results. The fixture contract is `ai-evals-lab/context-eviction/v1`.

## Strategies

1. `recency`: consider newest records first; admit entire records if they fit.
2. `priority`: rank by supplied importance (0..10), then recency; metadata is **supplied**, not predicted.
3. `structured_summary`: same priority ordering, but represent typed facts compactly as `key = value`; noise retains its full original text. No generative summarization is performed.

All policies select complete records, return selected items in source order, and refuse to exceed budget. Retrieval searches selected facts by exact key and prefers the latest retained matching fact. Expected answers exist **only in the evaluator**, not in the selector or retrieval function.

## Token accounting and metrics

The `proxy_tokens()` counter recognizes Unicode word chunks and punctuation via a regular expression. It is **not** tiktoken or any model's real tokenizer. `budget_tokens` counts retained record content only, **not** system prompts, query strings, tool schemas or other conversation overhead. A lower count does not prove a model would consume fewer billable tokens.

Metrics: strict exact-value accuracy, answerable recall, specificity of abstention on unanswerable queries, missed-answer count, stale/wrong-answer count, selected/dropped record IDs, and fraction of proxy-token budget filled. Empty denominators are reported as 0. Case labels and IDs are synthetic.

## Synthetic reference results (12 cases, no generalization)

| Policy | Correct | Accuracy | Answerable recall | Correct abstentions |
| --- | ---: | ---: | ---: | ---: |
| recency | 5/12 | 41.67% | 3/10 | 2/2 |
| priority | 5/12 | 41.67% | 3/10 | 2/2 |
| structured_summary | 10/12 | 83.33% | 8/10 | 2/2 |

These are property checks of the synthetic benchmark, **not** measured performance on production agents or evidence that structured summarization universally outperforms other policies.

## Safety / limits

- Only mock data and deterministic Python; no LLM, model API, network or actual context-window overflow.
- Structured fact extraction is pre-supplied. In reality, extraction can be inaccurate, paraphrases can be ambiguous and summary content can hallucinate or omit critical qualifiers.
- Temporal updates are resolved only among *retained* items. An older but high-priority record can cause a stale answer if a newer correction was evicted.
- No learned priority scoring, semantic retrieval, references across documents, long-conversation state or model-native tokenization is tested.
- The report omits fact values and text, but can include case/item IDs; remove identifying information before use on real data.
- No statistical significance or confidence intervals are reported for this 12-case hand-designed suite.

## Next research directions

Introduce tokenizer adapters with explicit model-specific accounting, sampled long-conversation traces in an authorized private environment, truly lossy summary policies and human-labeled semantic answer checks. Keep the offline core reproducible.
