"""Hosted model client: schema checks and reply parsing (no network)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidex import llm  # noqa: E402
from evidex.agents import steps  # noqa: E402


def test_schema_problems_on_our_planner_schema():
    good = {"title": "T", "not_covered": [], "sections": [{"heading": "H", "assertion_ids": ["a"]}]}
    assert llm.schema_problems(good, steps.PLANNER_SCHEMA) == []
    bad = {"title": "T", "sections": [{"heading": 3}]}
    probs = llm.schema_problems(bad, steps.PLANNER_SCHEMA)
    assert any("missing 'not_covered'" in p for p in probs)
    assert any("sections[0].heading should be string" in p for p in probs)


def _client(reply_text):
    c = llm.GroqLLM(api_key="test")
    c._post = lambda body: {"choices": [{"message": {"content": reply_text}}]}
    return c


def test_groq_reply_is_parsed_and_checked():
    schema = {"type": "object", "properties": {"label": {"type": "string", "enum": ["a", "b"]}},
              "required": ["label"]}
    assert _client('<think>hmm</think>{"label": "a"}').chat_json("s", "u", schema) == {"label": "a"}
    with pytest.raises(llm.LLMError, match="must be one of"):
        _client('{"label": "c"}').chat_json("s", "u", schema)
    with pytest.raises(llm.LLMError, match="did not return JSON"):
        _client("not json").chat_json("s", "u", schema)


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    m = llm.make_llm()
    assert isinstance(m, llm.GroqLLM) and m.model == "qwen/qwen3.8-27b"
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert isinstance(llm.make_llm(), llm.OllamaLLM)
