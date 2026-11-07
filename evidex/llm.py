"""Model clients (local Ollama or hosted Groq): one system prompt + one user message in, JSON out.

Agents get no tools, no internet and no database: only the text passed here.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    model: str

    def chat_json(self, system: str, user: str, schema: dict) -> dict: ...


class OllamaLLM:
    def __init__(self, model: str | None = None, host: str | None = None,
                 num_ctx: int = 8192, timeout: int = 300):
        self.model = model or os.environ.get("LLM_MODEL", "qwen3:8b")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,  # Ollama structured output: reply must match this JSON schema
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        req = urllib.request.Request(f"{self.host}/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.load(r)
        except urllib.error.URLError as e:
            raise LLMError(f"cannot reach Ollama at {self.host}: {e}") from e
        text = reply.get("message", {}).get("content", "")
        try:
            self.last_reply = json.loads(text)
            return self.last_reply
        except json.JSONDecodeError as e:
            raise LLMError(f"model did not return JSON: {text[:200]!r}") from e


def schema_problems(value, schema: dict, path: str = "reply") -> list[str]:
    """Small JSON-schema check (type, required, properties, items, enum): enough for our schemas.
    Ollama enforces the schema itself; hosted JSON mode does not, so the reply is checked here."""
    t = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool,
             "number": (int, float), "integer": int}
    if t in kinds and (not isinstance(value, kinds[t]) or (t in ("number", "integer") and isinstance(value, bool))):
        return [f"{path} should be {t}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path} must be one of {schema['enum']}"]
    out = []
    if t == "object":
        out += [f"{path} is missing '{k}'" for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += schema_problems(value[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            out += schema_problems(v, schema["items"], f"{path}[{i}]")
    return out


class GroqLLM:
    """Hosted model through Groq's OpenAI-compatible API (JSON mode; schema checked in code)."""
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: int = 120):
        self.model = model or os.environ.get("LLM_MODEL") or "qwen/qwen3.8-27b"
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            raise LLMError("GROQ_API_KEY is not set")

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(self.URL, json.dumps(body).encode(), {
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "evidex/1.0"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code == 429 or e.code >= 500:  # rate limit or server hiccup: wait and retry
                    wait = float(e.headers.get("retry-after") or 2 ** attempt)
                    time.sleep(min(wait, 20))
                    continue
                raise LLMError(f"Groq HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                if attempt == 3:
                    raise LLMError(f"cannot reach Groq: {e}") from e
                time.sleep(2 ** attempt)
        raise LLMError("Groq kept rate-limiting the request; try again in a minute")

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + "\n\nReply with one JSON object only, matching this "
                                                       "JSON schema:\n" + json.dumps(schema)},
                {"role": "user", "content": user}],
        }
        try:
            reply = self._post(body)
        except LLMError as e:
            if "reasoning_effort" not in str(e):
                raise
            body.pop("reasoning_effort")
            reply = self._post(body)
        text = reply["choices"][0]["message"].get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        try:
            out = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"model did not return JSON: {text[:200]!r}") from e
        problems = schema_problems(out, schema)
        if problems:
            raise LLMError("reply does not match the schema: " + "; ".join(problems[:5]))
        self.last_reply = out
        return out


def make_llm() -> "LLM":
    """LLM_PROVIDER=ollama (default, local) or groq (hosted)."""
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "groq":
        return GroqLLM()
    return OllamaLLM()


def call_logged(llm: LLM, system: str, user: str, schema: dict, *, step: str, run_id: str,
                validate=None, log_conn=None) -> dict:
    """Call the model, validate, retry once, and write the attempt to agent_call.

    validate(output) returns a list of problems; any problem triggers one retry with the
    problems appended to the prompt. After that, LLMError is raised.
    """
    attempts, out, err = 0, None, None
    t0 = time.monotonic()
    prompt = user
    while attempts < 2:
        attempts += 1
        try:
            out = llm.chat_json(system, prompt, schema)
            problems = validate(out) if validate else []
            if not problems:
                err = None
                break
            err = "; ".join(problems)
            prompt = (user + "\n\nYour previous answer had these problems; fix them and answer "
                      "again:\n- " + "\n- ".join(problems))
        except LLMError as e:
            err, out = str(e), None
    ms = int((time.monotonic() - t0) * 1000)
    if log_conn is not None:
        log_conn.execute(
            "insert into agent_call (run_id, step, model, input, output, error, attempts, ms) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, step, llm.model, json.dumps({"system": system, "user": user}),
             json.dumps(out) if out is not None else None, err, attempts, ms))
    if err:
        raise LLMError(f"{step}: {err}")
    return out
