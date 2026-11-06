"""Minimal Ollama client: one system prompt + one user message in, JSON out.

Agents get no tools, no internet and no database: only the text passed here.
"""
from __future__ import annotations

import json
import os
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
