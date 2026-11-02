"""Optional OpenAI-compatible API generation; strictly opt-in, untrusted output.

Only send synthetic policies/fixtures. Not a privacy sanitizer. No real tools run.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .synthetic import _make_candidate, digest, validate_candidates
from .engine import grade_trajectory

MAX_RESPONSE = 1_000_000
MAX_PROMPT = 60_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("LLM endpoint redirects are disabled")


def generate_model_candidates(policy: dict[str, Any], valid_trace: dict[str, Any], *,
                              endpoint: str, model: str, api_key: str = "",
                              count: int = 8, seed: int = 42, opener=None) -> list[dict[str, Any]]:
    """Request candidate traces, never automatically approve or promote them."""
    if not 1 <= count <= 40:
        raise ValueError("LLM count must be 1..40")
    if not model or len(model) > 200:
        raise ValueError("model name required")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("endpoint must be HTTPS or local loopback HTTP")
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ValueError("endpoint must not embed credentials or fragments")
    if parsed.path != "/v1/chat/completions" or parsed.query:
        raise ValueError("endpoint path must be /v1/chat/completions, without query parameters")
    if not grade_trajectory(policy, valid_trace).passed:
        raise ValueError("seed trace must pass policy")
    prompt = ("Generate difficult SYNTHETIC tool-call trajectories. "
              "Reply with only a JSON object containing a cases array. "
              "Each case must have a category string and a trace object with calls array. "
              "Each call has step_id, tool, and arguments keys. "
              "Make varied examples: unexpected API parameters, missing safety steps, "
              "wrong order, unusual but type-valid inputs. No Markdown or secrets. "
              "This data will NOT execute tools. "
              f"Return 1..{count} cases.\nPolicy: {json.dumps(policy, sort_keys=True)}\n"
              f"Seed trace: {json.dumps(valid_trace, sort_keys=True)}")
    if len(prompt) > MAX_PROMPT:
        raise ValueError("synthetic prompt exceeds limit")
    body = json.dumps({"model": model, "temperature": 0.7,
                       "messages": [{"role": "user", "content": prompt}]}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    client = opener or urllib.request.build_opener(_NoRedirect()).open
    try:
        with client(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("LLM endpoint request failed") from exc
    if len(raw) > MAX_RESPONSE:
        raise ValueError("LLM response too large")
    try:
        message = json.loads(raw)["choices"][0]["message"]["content"]
        cases = json.loads(message)["cases"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM returned invalid JSON candidate schema") from exc
    if not isinstance(cases, list) or not 1 <= len(cases) <= count:
        raise ValueError("LLM case count outside requested range")
    output: list[dict[str, Any]] = []
    existing = {digest(valid_trace)}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("trace"), dict):
            raise ValueError("LLM case must include a trace object")
        category = case.get("category")
        if not isinstance(category, str) or not 1 <= len(category) <= 100:
            raise ValueError("LLM case category must be a short string")
        trace = case["trace"]
        if digest(trace) in existing:
            raise ValueError("LLM returned a duplicate or unchanged trace")
        existing.add(digest(trace))
        # Model labels are not trusted: policy engine supplies provisional label only.
        expected = grade_trajectory(policy, trace).passed
        output.append(_make_candidate(policy, trace, "model:" + category, "llm", expected, seed, index))
    validate_candidates(policy, output)
    return output
