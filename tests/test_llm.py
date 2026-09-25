import json
from typing import Any

import pytest

from sfetl.ask import llm
from sfetl.ask.classify import classification_schema


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.ok = True

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def test_ollama_call_is_deterministic_schema_bound_and_without_thinking(monkeypatch) -> None:
    sent: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        sent.update(url=url, body=json)
        return FakeResponse({"message": {"content": '{"sql": "SELECT 1", "title": "t"}'}})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    schema = {
        "type": "object",
        "properties": {"sql": {"type": "string"}, "title": {"type": "string"}},
    }
    reply = llm.OllamaProvider(model="qwen3:4b", url="http://127.0.0.1:11434").complete_json(
        "system", "user", schema
    )
    assert sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["body"]["think"] is False
    assert sent["body"]["format"] == schema
    assert sent["body"]["options"]["temperature"] == 0
    assert reply.data == {"sql": "SELECT 1", "title": "t"}


def test_ollama_reply_is_validated_against_the_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        llm.requests,
        "post",
        lambda *a, **k: FakeResponse({"message": {"content": json.dumps({"sql": 3})}}),
    )
    schema = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
    with pytest.raises(llm.LLMError):
        llm.OllamaProvider(model="m", url="http://x").complete_json("s", "u", schema)


def test_gemini_schema_conversion() -> None:
    converted = llm.to_gemini_schema(classification_schema())
    intent = converted["properties"]["intent_id"]
    assert intent == {"type": "string", "nullable": True}
    alternative = converted["properties"]["alternative"]
    assert alternative["type"] == "object" and alternative["nullable"] is True
    assert "anyOf" not in json.dumps(converted)


def test_gemini_provider_reads_the_candidate_text(monkeypatch) -> None:
    sent: dict[str, Any] = {}

    def fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float):
        sent.update(url=url, headers=headers, body=json)
        text = '{"sql": "", "title": "no"}'
        return FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(model="gemini-flash-lite-latest", api_key="k")
    reply = provider.complete_json(
        "s", "u", {"type": "object", "properties": {"sql": {"type": "string"}}}
    )
    assert reply.data["sql"] == ""
    assert sent["body"]["generationConfig"]["temperature"] == 0
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["headers"]["x-goog-api-key"] == "k"


def test_provider_is_chosen_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SFETL_LLM_PROVIDER", "gemini")
    assert isinstance(llm.provider_from_env(), llm.GeminiProvider)
    monkeypatch.setenv("SFETL_LLM_PROVIDER", "ollama")
    assert isinstance(llm.provider_from_env(), llm.OllamaProvider)
    monkeypatch.setenv("SFETL_LLM_PROVIDER", "other")
    with pytest.raises(llm.LLMError):
        llm.provider_from_env()
