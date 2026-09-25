"""Swappable LLM providers that return JSON constrained by a JSON schema.

The assistant asks the model for two small JSON objects only: a routing decision
(``{intent_id, params, alternative}``) and, when no intent fits, ``{sql, title}``. Every
provider is called with temperature 0 and the target JSON schema, and every reply is validated
against that schema again here (``jsonschema``) before anything uses it.

* ``OllamaProvider`` (default): a local model through Ollama's ``/api/chat`` with
  ``format=<schema>`` and ``think=false``. Default model ``qwen3:4b`` at 127.0.0.1:11434.
* ``GeminiProvider``: the cloud setup of the production system this project rebuilds
  (``gemini-flash-lite-latest``, ``responseMimeType=application/json`` + ``responseSchema``,
  temperature 0). Implemented and unit-tested against a recorded response shape; not exercised
  against the live API in this repository.
* ``ScriptedProvider``: returns queued answers; used by the offline tests.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import jsonschema
import requests

from sfetl import config


class LLMError(RuntimeError):
    """The provider failed or returned something that does not match the schema."""


@dataclass(frozen=True)
class LLMReply:
    data: dict[str, Any]
    raw: str
    seconds: float
    model: str


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply: ...


def _validated(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise LLMError(f"reply is not JSON: {raw[:200]!r}") from err
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as err:
        raise LLMError(f"reply does not match the schema: {err.message}") from err
    if not isinstance(data, dict):
        raise LLMError("reply is not a JSON object")
    return data


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout_s: float = 300.0,
        num_ctx: int = 8192,
    ) -> None:
        self.model = model or config.ollama_model()
        self.url = (url or config.ollama_url()).rstrip("/")
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply:
        started = time.perf_counter()
        try:
            response = requests.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "options": {"temperature": 0, "seed": 42, "num_ctx": self.num_ctx},
                    "keep_alive": "5m",
                },
                timeout=self.timeout_s,
            )
            response.raise_for_status()
        except requests.RequestException as err:
            raise LLMError(f"Ollama call failed: {err}") from err
        payload = response.json()
        raw = payload.get("message", {}).get("content", "")
        return LLMReply(
            data=_validated(raw, schema),
            raw=raw,
            seconds=time.perf_counter() - started,
            model=self.model,
        )


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema -> the OpenAPI subset Gemini's ``responseSchema`` accepts.

    ``type: [X, "null"]`` and ``anyOf: [{"type": "null"}, X]`` become ``X`` + ``nullable``;
    ``None`` is removed from enums; keys Gemini does not accept are dropped.
    """
    out: dict[str, Any] = {}
    nullable = False
    source = dict(schema)
    if "anyOf" in source:
        options = [o for o in source.pop("anyOf") if o.get("type") != "null"]
        nullable = len(options) < len(schema["anyOf"])
        if len(options) == 1:
            source = {**options[0], **source}
    kind = source.get("type")
    if isinstance(kind, list):
        types = [t for t in kind if t != "null"]
        nullable = nullable or len(types) < len(kind)
        kind = types[0] if types else "string"
    if kind:
        out["type"] = kind
    if "enum" in source:
        values = [v for v in source["enum"] if v is not None]
        nullable = nullable or len(values) < len(source["enum"])
        out["enum"] = values
    if "properties" in source:
        out["properties"] = {k: to_gemini_schema(v) for k, v in source["properties"].items()}
    if "items" in source:
        out["items"] = to_gemini_schema(source["items"])
    if "required" in source:
        out["required"] = list(source["required"])
    if "description" in source:
        out["description"] = source["description"]
    if nullable:
        out["nullable"] = True
    return out


class GeminiProvider:
    name = "gemini"

    def __init__(
        self, model: str | None = None, api_key: str | None = None, timeout_s: float = 60.0
    ) -> None:
        self.model = model or config.gemini_model()
        self.api_key = api_key if api_key is not None else config.gemini_api_key()
        self.timeout_s = timeout_s

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply:
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        started = time.perf_counter()
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
                ":generateContent",
                headers={"x-goog-api-key": self.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {
                        "temperature": 0,
                        "responseMimeType": "application/json",
                        "responseSchema": to_gemini_schema(schema),
                    },
                },
                timeout=self.timeout_s,
            )
            response.raise_for_status()
        except requests.RequestException as err:
            raise LLMError(f"Gemini call failed: {err}") from err
        payload = response.json()
        try:
            raw = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as err:
            raise LLMError(f"Gemini returned no content: {str(payload)[:200]}") from err
        return LLMReply(
            data=_validated(raw, schema),
            raw=raw,
            seconds=time.perf_counter() - started,
            model=self.model,
        )


class ScriptedProvider:
    """Replays prepared replies in order (tests). Records every prompt it receives."""

    name = "scripted"

    def __init__(self, replies: Iterable[dict[str, Any] | Exception], model: str = "scripted"):
        self.replies: deque[dict[str, Any] | Exception] = deque(replies)
        self.model = model
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMReply:
        self.calls.append((system, user))
        if not self.replies:
            raise LLMError("no scripted reply left")
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        raw = json.dumps(reply)
        return LLMReply(data=_validated(raw, schema), raw=raw, seconds=0.0, model=self.model)


def provider_from_env() -> LLMProvider:
    name = config.llm_provider()
    if name == "ollama":
        return OllamaProvider()
    if name == "gemini":
        return GeminiProvider()
    raise LLMError(f"unknown SFETL_LLM_PROVIDER={name!r} (use ollama or gemini)")
