# Project 08 — Agent Red-Team Fuzzer (v0.10)

## Scope

A deterministic, **offline tool-boundary simulator**, not a live LLM agent or an operating-system sandbox. Its only tool effects are an in-memory record and refund of imaginary funds. It performs no network requests, shell execution, external payments or production authorization.

Threat model references: [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) and [OWASP AI Agent Security](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html).

## Attack types and guardrails

| Family | Simulated adversarial action | Expected defense |
|---|---|---|
| Prompt injection | Fake system-role instructions embedded in tool output; attempts to trigger tool action | UNTRUSTED_INSTRUCTION |
| Malformed tool | Invalid type, argument, extra field or unknown tool | Explicit schema/type/allowlist validation |
| Repetition and unbounded loops | Repeated identical calls or exceeded action budget | REPEAT_LIMIT_EXCEEDED, STEP_BUDGET_EXCEEDED |
| Privilege bypass | Refund without accepted identity verification | IDENTITY_REQUIRED, no refund |
| Benign control | Lookup, verify identity, refund once | One in-memory refund, no block |

## Commands

```bash
python -m pip install -e .
python -m unittest discover -s tests -p test_redteam.py -v
python -m evals_lab.redteam_cli run --dataset examples/redteam-cases.jsonl --output reports/redteam-seeds.json
python -m evals_lab.redteam_cli generate --count 40 --seed 42 --output reports/redteam-generated.jsonl
python -m evals_lab.redteam_cli run --dataset reports/redteam-generated.jsonl --output reports/redteam-campaign.json
```

Results contain case IDs, attack types, violation codes, attempted/executed counts and synthetic effects, but **not raw tool arguments or injection payloads**. Never publish sensitive identifiers in case IDs. Exit codes are 0 for exact expected-match, 2 for deviation and 1 for invalid input.

## Security properties and limitations

- Payloads are always untrusted **data**; this test harness deliberately never interprets them as runtime instructions. Therefore a PASS does **not** show that an LLM model resisted prompt injection.
- `origin` is an authoritative **synthetic fixture label**, not a verified provenance signal. A real attacker who controls the entire case JSON could spoof this. Never use this test dispatcher as a production authorization layer.
- A fixed tool allowlist and explicit identity checks are enforced before the in-memory refund. Financial operations in real environments require trusted approval, tenant isolation and stronger authorization.
- Inputs, action budgets, repeated calls and invalid schemas are bounded and fail closed; these checks do not replace process isolation for running arbitrary agent code.
- Seed 42 and explicit labels make the campaign reproducible. The 40-case campaign cycles through **16 test patterns**, so it is not a 40-sample independent statistical benchmark. Cases do not run on paid LLMs.
- Follow-up: opt-in adapter for real-model calls with separate untrusted content provenance, mocked side effects and isolated sandbox tests. Do not connect commercial or production tool credentials to the fuzzer.
